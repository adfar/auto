"""Generate an independent probability estimate for a market via headless
`claude -p` with web search. Returns JSON and stores it in the forecasts table.

Usage: python3 forecast.py [N]   # forecast top-N candidates
"""
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

import candidates
import db

MODEL = "claude-opus-4-8"
TIMEOUT_S = 420

PROMPT_TEMPLATE = """You are a careful superforecaster. Estimate the probability that the \
following prediction-market question resolves YES.

Question: {title}
{subtitle}Resolution rules (primary): {rules}
Market closes: {close_time}
Today's date: {today}

Instructions:
- Use web search to find current, relevant evidence (news, official data, base rates).
- Do NOT look up prediction-market prices (Kalshi, Polymarket, Metaculus etc.) — I need \
your independent estimate, not the market's.
- Start from a base rate, then adjust for current evidence. Consider how much time \
remains before the close date.
- Be honest about uncertainty. Avoid probabilities below 0.02 or above 0.98 unless the \
outcome is essentially determined.

- Before deciding, restate the exact resolution threshold and write out the key \
quantitative comparison (e.g. "threshold is >18; current pace implies ~15"). If the \
numbers land near the threshold, your probability should be near 0.5, not extreme. \
Double-check the arithmetic in that comparison before finalizing.

Respond with ONLY a JSON object, no other text:
{{"key_comparison": "<threshold vs your best estimate of the quantity, with numbers>", \
"probability_yes": <float 0-1>, "confidence": "<low|medium|high>", \
"rationale": "<2-4 sentences citing your key evidence>"}}"""


def run_claude(prompt: str) -> dict:
    cmd = [
        "claude", "-p", prompt,
        "--model", MODEL,
        "--allowedTools", "WebSearch,WebFetch",
        "--output-format", "json",
    ]
    # Run from a neutral cwd so the CLI doesn't load unrelated project context.
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S,
                          cwd=tempfile.gettempdir(), stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr[:500]}")
    wrapper = json.loads(proc.stdout)
    text = wrapper.get("result", "")
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"no JSON in model output: {text[:300]}")
    out = json.loads(m.group(0))
    p = float(out["probability_yes"])
    if not (0.0 <= p <= 1.0):
        raise ValueError(f"probability out of range: {p}")
    return out


def forecast_market(conn, row) -> int:
    sub = f"Detail: {row['yes_sub_title']}\n" if row["yes_sub_title"] else ""
    prompt = PROMPT_TEMPLATE.format(
        title=row["title"],
        subtitle=sub,
        rules=(row["rules_primary"] or "")[:1200],
        close_time=row["close_time"],
        today=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    )
    out = run_claude(prompt)
    mid = (row["yes_bid"] + row["yes_ask"]) / 2
    cur = conn.execute(
        """INSERT INTO forecasts
           (ticker, ts, p_model, p_market, yes_bid, yes_ask, confidence, rationale, model)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            row["ticker"], datetime.now(timezone.utc).isoformat(),
            out["probability_yes"], mid, row["yes_bid"], row["yes_ask"],
            out.get("confidence"),
            (f"[{out['key_comparison']}] " if out.get("key_comparison") else "")
            + (out.get("rationale") or ""),
            MODEL,
        ),
    )
    conn.commit()
    return cur.lastrowid


def main(n: int):
    conn = db.connect()
    cands = candidates.select(n)
    print(f"Forecasting {len(cands)} candidates with {MODEL}...")
    for row in cands:
        already = conn.execute(
            "SELECT 1 FROM forecasts WHERE ticker=? AND ts > datetime('now','-2 days')",
            (row["ticker"],),
        ).fetchone()
        if already:
            print(f"  skip (recent forecast exists): {row['ticker']}")
            continue
        try:
            fid = forecast_market(conn, row)
            f = conn.execute("SELECT * FROM forecasts WHERE id=?", (fid,)).fetchone()
            print(f"  {row['ticker']}: model={f['p_model']:.2f} market={f['p_market']:.2f} "
                  f"({f['confidence']}) — {row['title'][:60]}")
        except Exception as e:
            print(f"  FAILED {row['ticker']}: {e}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 5)
