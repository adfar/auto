# kalshi-edge — project instructions for Claude

Paper-trading pipeline testing whether an LLM forecasting harness beats
Kalshi market prices. Architecture: README.md and docs/forecaster-design.md.

## Autonomy policy

Work autonomously through the full loop without asking: write code, run
tests, commit, and push to origin/main. Specifically:

- Commit and push completed, tested work without asking for permission.
  Small, focused commits with descriptive messages.
- Run the stubbed test suite freely (it makes no LLM calls and touches no
  real data).
- Live verification (real `claude -p` calls) is allowed up to ~5 questions
  per session without asking; it spends Claude usage and writes real rows.
- Ask first only for: deleting/rewriting rows in kalshi_edge.db, changing
  the trading gate or fee/Kelly logic in trade.py, force-pushing, or
  anything involving real money.

Definition of done for a change: stubbed tests pass AND (if the change
affects live behavior) one live forecast run verifies end-to-end, its
audit trail inspected via the queries below.

## Environment & commands

Always use the project venv: `.venv/bin/python` (never bare `python3` —
system pythons lack deps). Setup if missing:
`python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`

- Tests: `.venv/bin/pytest tests/ -q` (fast, no LLM calls)
- Full daily cycle: `./run.sh [N]`
- One live harness forecast: `.venv/bin/python scan.py && .venv/bin/python forecast.py 1`
  (scan takes several minutes; run in background)
- Scoreboard / Brier: `.venv/bin/python settle.py`
- Offline eval: `.venv/bin/python pastcast.py [N]` re-forecasts resolved
  questions with `as_of` set; `--score` prints per-version Briers. Counts
  toward the live-call budget. Leakage caveat: relative comparisons only.

## Nightly automation

launchd job `com.kalshi-edge.nightly` (~/Library/LaunchAgents/) runs
`scripts/nightly.sh` at 06:30 local: full cycle -> logs/nightly.log ->
best-effort commit+push of kalshi_edge.db. Check health with
`tail logs/nightly.log` and `launchctl print gui/$(id -u)/com.kalshi-edge.nightly`.
If the nightly pushed DB state, `git pull --rebase` before starting work.

## Inspecting a forecast run

```sql
SELECT * FROM forecast_runs ORDER BY id DESC LIMIT 5;
SELECT stage, agent_index, error, duration_s FROM agent_traces WHERE run_id=?;
```

`agent_traces.output_json` holds each stage's full output; `sources` the
researcher URLs.

## Invariants — do not break

- **Anchoring guard**: nothing market-price-shaped may enter `forecaster/`
  or its prompts. QuestionSpec deliberately has no price fields; keep it
  that way. p_market is captured only in forecast.py.
- **Trading gate**: no real money before 50+ resolved forecasts with model
  Brier < market Brier AND positive paper P&L (README).
- **Versioning**: any change to prompts/, pipeline structure, or
  calibration must bump HARNESS_VERSION in forecaster/version.py —
  settlement scoring segments by it.
- **trade.py/settle.py contract**: forecasts.p_model stays the calibrated
  probability; don't change that table's semantics.
- kalshi_edge.db is tracked in git on purpose (state travels with the
  repo). Don't gitignore it; commit it when rows changed as part of a run.

## Style

- Match the existing code: small modules, docstring at top, stdlib-only
  (requests is the only third-party runtime dep).
- Tests are stubbed at runner.run_agent — never make LLM calls in tests.
