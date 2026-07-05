# Forecasting Harness — Design Doc

Status: draft · 2026-07-04
Scope: replaces the single-shot forecaster (`forecast.py`) with a dedicated,
multi-agent forecasting harness. The bet-search infrastructure
(`scan.py`, `candidates.py`, `trade.py`, `settle.py`) is out of scope and
stays as-is.

---

## 1. Problem

The current forecaster is one `claude -p` call per market: one agent, one
pass, no decomposition, no ensemble, no calibration, and no record of *how*
a probability was produced. The systems that demonstrably beat this shape
(Preseen — 6th on Kalshi all-time; FutureSearch; Bridgewater's AIA
Forecaster) all share the same architecture: parallel research agents,
question decomposition, a synthesis step, a calibration layer, and a
resolution-driven feedback loop. Published numbers back the individual
pieces:

- Decomposing into 3–5 subquestions improved FutureSearch's Brier from
  0.141 → 0.132.
- LLMs systematically hedge toward 50%; extremization (Platt scaling)
  improves accuracy (AIA Forecaster).
- Research depth correlates with accuracy ("returns improve with more web
  pages visited").
- Liquid markets still beat the best standalone AI (market Brier 0.111 vs
  AIA 0.126) — the edge is in less-liquid, harder questions, which our
  candidate filters already target.
- A market+AI blend (~67/33) beats either alone on hard questions.

We want those pieces, sized for a personal paper-trading pipeline rather
than a research lab.

## 2. Goals

1. **Lower Brier score** than the current single-shot forecaster on the
   same questions.
2. **Diagnosable misses** — when `settle.py` scores a loss, we can see
   which stage (research, synthesis, calibration) was wrong.
3. **Clean boundary** — the harness takes a question and returns a
   calibrated probability. It never sees market prices, order books, or
   anything Kalshi-specific beyond the question text and resolution rules.
4. **Version-comparable** — every forecast records the harness version that
   produced it, so `settle.py` can score versions against each other.
5. **Offline evaluation** — a pastcasting mode that scores harness changes
   against already-resolved questions in a day instead of waiting weeks.

## 3. Non-goals

- Live/real-money trading (unchanged gate: 50+ resolutions, model Brier <
  market Brier, positive paper P&L).
- Market-price blending inside the harness. The anchoring guard stays
  absolute; any 67/33-style blend happens downstream in `trade.py`, where
  the market price is already visible. (Future work, section 13.)
- Numeric / distribution forecasts. Binary YES/NO only in v1 — that's all
  Kalshi markets need.
- Question *generation* (FutureSearch generates questions from news seeds;
  our questions come from the Kalshi scan).

## 4. Architecture overview

```
                       forecaster/  (new package)
              ┌──────────────────────────────────────────┐
QuestionSpec ─┤  A. Analyst      decompose + plan        │
              │        │                                 │
              │  B. Researchers  K independent agents    │
              │        │         (parallel, web search)  │
              │  C. Supervisor   reconcile, follow up    │
              │        │                                 │
              │  D. Calibrate    deterministic Python    │
              └────────┼─────────────────────────────────┘
                       ▼
                 ForecastResult (p_raw, p_calibrated, rationale,
                                 confidence, full audit trail)
```

Stages A–C are LLM agent calls. Stage D is pure Python. Every stage's
input, output, sources, and cost are persisted (section 8).

### Interface

```python
@dataclass(frozen=True)
class QuestionSpec:
    question_id: str        # opaque; caller supplies (we use the ticker)
    title: str
    detail: str | None      # yes_sub_title
    resolution_rules: str
    close_time: str         # ISO 8601
    category: str | None
    as_of: str | None = None  # pastcasting: pretend today is this date

@dataclass(frozen=True)
class ForecastResult:
    run_id: int             # FK into forecast_runs
    p_raw: float            # supervisor output, pre-calibration
    p_calibrated: float     # what trade.py should consume
    confidence: str         # low | medium | high
    rationale: str
    key_comparison: str     # threshold vs estimate, kept from current prompt
    harness_version: str

def forecast(spec: QuestionSpec) -> ForecastResult: ...
```

Note what is *absent* from `QuestionSpec`: bid, ask, mid, volume. The
anchoring guard is enforced structurally — the harness cannot leak market
prices into prompts because it never receives them. The prompt-level
"do not look up prediction-market prices" instruction stays as a second
layer.

## 5. Pipeline stages

### Stage A — Analyst (1 agent call)

Input: the QuestionSpec. Output (JSON):

- `question_type`: event-occurrence | quantity-threshold | deadline-race |
  election/appointment | other. Drives which base-rate framing the
  researchers are told to use.
- `resolution_summary`: the analyst restates the exact resolution
  criterion and threshold in its own words. This is the current prompt's
  "restate the threshold" discipline, promoted to its own stage — a
  misread resolution rule poisons everything downstream, so we isolate it
  where it's cheap to audit.
- `subquestions`: 3–5 researchable subquestions (the FutureSearch
  technique). E.g. for "Will CPI YoY exceed 3.2% in July?" →
  current CPI trajectory, recent MoM prints, energy base effects,
  consensus forecasts, release date vs market close.
- `research_plan`: 2–3 sentences on where primary sources live
  (government data, official schedules, scientific registries —
  Preseen's "primary sources only" discipline).

No web access at this stage; it's pure question analysis. Cheap and fast.

### Stage B — Researchers (K parallel agent calls, default K=3)

Each researcher independently receives the QuestionSpec + Stage A output
and must:

1. Research each subquestion with web search, preferring primary sources.
2. Report per-subquestion findings with source URLs.
3. State a base rate and the adjustments made to it.
4. Output `probability_yes`, `confidence`, `key_comparison`, `rationale`,
   and `sources` (JSON).

Independence rules: researchers never see each other's output, and they
run with the same prompt — the diversity comes from sampling and search
paths, not from assigned personas (personas add prompt-maintenance cost
without published evidence of benefit; revisit if ensemble spread is too
small).

Prompt inherits the guards from the current `forecast.py` prompt verbatim:
no prediction-market prices, honesty about uncertainty, explicit threshold
arithmetic, probabilities near 0.5 when the numbers land near the
threshold.

### Stage C — Supervisor (1 agent call, web access enabled)

Input: QuestionSpec + Stage A output + all K researcher reports
(anonymized as Researcher 1..K). The supervisor must:

1. Identify where the researchers disagree and *why* (different sources?
   different base rates? one misread the resolution rule?).
2. If the disagreement hinges on a checkable fact, do targeted follow-up
   research on that crux only.
3. Output the final `probability_yes` (= `p_raw`), `confidence`,
   `rationale`, `key_comparison`, and a `crux` field describing what the
   forecast most depends on.

This is the AIA Forecaster's supervisor pattern; it beats naive
mean/median pooling because it can resolve *why* the ensemble disagrees
rather than averaging over an error.

Fallback: if the supervisor call fails after retries, use the median of
researcher probabilities and mark the run `supervisor_fallback=1`.

### Stage D — Calibration (deterministic)

```python
def calibrate(p_raw: float) -> float:
    a = EXTREMIZATION_ALPHA          # v1: fixed, e.g. 1.3
    p = p_raw**a / (p_raw**a + (1 - p_raw)**a)
    return min(max(p, 0.02), 0.98)  # tail clamp, matches prompt guidance
```

- v1 ships with a fixed extremization exponent `α` (start ~1.2–1.4; tune
  on the pastcast set). This directly counters the documented
  hedge-toward-50% bias.
- Once we have ≥50 resolved harness forecasts, fit logistic regression on
  `logit(p_raw)` (Platt scaling) and replace the fixed exponent. The
  calibration function's parameters and version are stored per-run, and
  both `p_raw` and `p_calibrated` are persisted, so recalibrating
  retroactively is always possible.

## 6. Execution layer

Stages A–C run as headless agent calls. Two backends behind one small
`run_agent(prompt, tools, timeout) -> dict` function:

1. **`claude -p` subprocess (v1 default).** Same mechanism as today:
   `--allowedTools WebSearch,WebFetch --output-format json`, neutral cwd,
   stdin devnull. Zero new auth — keeps using the Claude Code login, which
   is the project's existing constraint and cost model. Parallelism via
   `concurrent.futures.ThreadPoolExecutor` (subprocesses, so threads are
   fine).
2. **Anthropic Python SDK (optional, later).** `anthropic` package,
   `model="claude-opus-4-8"`, `thinking={"type": "adaptive"}`, server-side
   `web_search_20260209` / `web_fetch_20260209` tools, and structured
   outputs (`output_config.format` with a JSON schema) instead of
   regex-extracting JSON from text. Strictly better ergonomics (schema
   guarantees kill the whole parse-failure class; usage/token accounting
   comes back on the response) but requires API billing. An `ant auth
   login` OAuth profile can also power a bare `Anthropic()` client if we
   want SDK ergonomics without managing an API key.

Model: `claude-opus-4-8` for all three stages in v1 (matches current
`forecast.py`). Stage A is a candidate for a cheaper model later, but
don't optimize cost before the harness is proven.

JSON handling (subprocess backend): keep the current
`re.search(r"\{.*\}", ...)` extraction, add one retry with an appended
"Respond with ONLY the JSON object" nudge on parse failure, and validate
required keys + probability range before accepting.

## 7. Package layout

```
forecaster/
  __init__.py       # forecast(spec) -> ForecastResult, public surface
  spec.py           # QuestionSpec, ForecastResult dataclasses
  analyst.py        # Stage A prompt + parsing
  researcher.py     # Stage B prompt + parsing
  supervisor.py     # Stage C prompt + parsing
  calibrate.py      # Stage D + (later) fitted parameters
  runner.py         # run_agent(): claude -p backend, retries, timeouts
  store.py          # forecast_runs / agent_traces persistence
  version.py        # HARNESS_VERSION string
prompts/
  analyst.txt
  researcher.txt
  supervisor.txt
pastcast.py         # offline eval (section 10)
```

Prompts live in files, not string literals, so a prompt diff is a visible
diff and bumping `HARNESS_VERSION` on prompt changes is an obvious ritual.

## 8. Data model

Two new tables (added to `db.py` SCHEMA; SQLite, additive-only so existing
data is untouched):

```sql
CREATE TABLE IF NOT EXISTS forecast_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id TEXT NOT NULL,        -- ticker
    ts TEXT NOT NULL,
    harness_version TEXT NOT NULL,
    p_raw REAL,
    p_calibrated REAL,
    calibration TEXT,                 -- json: {"method":"extremize","alpha":1.3}
    confidence TEXT,
    rationale TEXT,
    crux TEXT,
    question_type TEXT,               -- from Stage A
    n_researchers INTEGER,
    researcher_spread REAL,           -- max - min of researcher probs
    supervisor_fallback INTEGER DEFAULT 0,
    status TEXT NOT NULL,             -- ok / failed
    duration_s REAL,
    as_of TEXT                        -- null for live; set for pastcasts
);

CREATE TABLE IF NOT EXISTS agent_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES forecast_runs(id),
    stage TEXT NOT NULL,              -- analyst / researcher / supervisor
    agent_index INTEGER,              -- 0..K-1 for researchers
    output_json TEXT,                 -- full parsed output
    sources TEXT,                     -- json array of URLs
    error TEXT,
    duration_s REAL
);
```

The existing `forecasts` table stays the trading pipeline's contract:
`forecast.py` inserts `p_calibrated` as `p_model` exactly as before, plus
a new nullable `run_id` column referencing `forecast_runs`. `trade.py`
and `settle.py` need **zero changes**.

`researcher_spread` is stored because it's a free, useful signal: high
spread means the question is genuinely uncertain or under-researched —
worth examining as a trade filter later (e.g. require spread < 0.25 to
trade).

## 9. Integration

`forecast.py` shrinks to an adapter:

```python
spec = QuestionSpec(question_id=row["ticker"], title=row["title"], ...)
result = forecaster.forecast(spec)
# insert into forecasts: p_model=result.p_calibrated, p_market=mid, run_id=...
```

The mid-price capture (`p_market`) stays in `forecast.py`, outside the
harness boundary. `run.sh` is unchanged.

## 10. Pastcasting (offline eval)

`pastcast.py` builds an eval set from our own history: every ticker in
`settlements` joined to `markets` (question text, rules, close time) and
`forecasts` (the market price at forecast time, as the benchmark).

For each question, run the harness with `as_of` set to the original
forecast date. The prompts receive "Today's date: {as_of}" plus an
explicit instruction to ignore information published after that date.

**Known limitation, stated honestly:** web search cannot be truly
time-boxed; leakage of post-date information is possible and makes
pastcast Briers optimistic. Pastcasting is therefore used for *relative*
comparisons (harness v2 vs v3 on the same set, calibration fitting,
prompt A/Bs), never as an absolute claim of edge. The live gate remains
the only gate.

Output: Brier per harness version vs the frozen market-price Brier on the
same questions, plus a calibration curve (predicted vs realized in
buckets).

## 11. Failure handling

| Failure | Behavior |
|---|---|
| Stage A fails (2 retries) | Abort question, `status=failed`. No forecast beats a bad forecast. |
| A researcher fails | Continue if ≥2 researchers succeeded; record the error in `agent_traces`. |
| <2 researchers succeed | Abort question. |
| Supervisor fails (2 retries) | Median of researcher probabilities, `supervisor_fallback=1`. |
| JSON parse failure | One retry with a JSON-only nudge, then treat as stage failure. |
| Per-agent timeout | 420 s (current value), counts as that agent's failure. |
| Whole-question budget | 25 min wall clock; abort beyond it. |

Failures never write to `forecasts`, so the trading pipeline only ever
sees completed runs.

## 12. Cost & latency budget

Per question: 1 analyst + 3 researchers + 1 supervisor = 5 agent calls
(vs 1 today). Researchers run in parallel, so wall clock ≈ analyst +
slowest researcher + supervisor ≈ 3 sequential agent calls ≈ 10–15 min
per question at current timeouts. A daily N=10 run ≈ 50 agent calls,
2–2.5 h sequential — fine for a nightly cron, and questions themselves
can be parallelized (2 at a time) if it isn't.

Knobs, in order of preference when cutting cost: K researchers (3→2),
per-agent timeout, N candidates. Don't cut the supervisor — it's the
highest-leverage stage.

## 13. Learning loop & future work

The Preseen pattern — "when a question resolves, score what produced it" —
becomes possible because of the audit trail:

- **Per-stage postmortems**: for each resolved miss (|p − outcome| > 0.5),
  is the error already present in the researcher median (research
  failure), introduced by the supervisor (synthesis failure), or
  amplified by calibration?
- **Segment Briers**: by `question_type`, `category`, `confidence`,
  `researcher_spread`, `harness_version`. This tells us which question
  types to stop trading and which prompt to fix.

Deliberately deferred:

1. **Market blend in `trade.py`** (post-forecast 67/33-style pooling) —
   only after the harness Brier is measured, so the blend doesn't mask a
   weak forecaster.
2. **Fitted Platt scaling** — at ≥50 resolutions.
3. **SDK backend with structured outputs** — when parse failures or cost
   accounting justify it.
4. **Spread-based trade filter** — evaluate `researcher_spread` as a
   predictor of miss size first.

## 14. Milestones

| # | Deliverable | Done when |
|---|---|---|
| M1 | Package skeleton, `run_agent`, Stage B+D only (K=3 ensemble, median pool, fixed extremization), new tables, `forecast.py` adapter | Nightly run produces forecasts end-to-end via the harness |
| M2 | Stage A (analyst/decomposition) + Stage C (supervisor) wired in | Full pipeline live; `supervisor_fallback` rate < 10% |
| M3 | `pastcast.py` + eval set from settlements | Harness vs single-shot Brier comparison on ≥30 resolved questions |
| M4 | Tune α, prompt iterations driven by pastcast + per-stage postmortems | Harness beats single-shot forecaster on pastcast set |
| M5 | Fitted calibration at 50+ live resolutions | Calibration curve within ±0.05 per bucket |

## 15. Open questions

1. **K=3 vs K=5** — start at 3; raise only if `researcher_spread` shows
   the ensemble is too noisy at 3. (FutureSearch uses ~6 agents/question;
   they also share agents across related questions, which we skip.)
2. **Persona diversity for researchers** — skipped in v1 (see 5B);
   revisit if all researchers converge on identical sources.
3. **Subscription rate limits** — 50 parallel-ish `claude -p` calls per
   night may hit Claude Code usage limits; if so, drop N before dropping K.
