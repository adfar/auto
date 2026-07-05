# kalshi-edge

Paper-trading pipeline that tests whether an LLM forecaster has any edge over
Kalshi market prices — before risking real money.

## Pipeline

```
scan.py        # snapshot all open Kalshi markets (public API, no key) -> SQLite
candidates.py  # hard filters: liquidity, spread, horizon, category -> top-N
forecast.py    # adapter into forecaster/ (multi-agent harness) -> independent P(YES)
trade.py       # paper trade only when EV > fees + spread + 5c margin; 1/4-Kelly
settle.py      # check real resolutions; realize P&L; Brier: model vs market
```

The forecaster is a multi-agent harness (`forecaster/`, design in
`docs/forecaster-design.md`): an analyst decomposes the question into
subquestions, 3 independent researchers investigate in parallel with web
search, a supervisor reconciles their reports (with targeted follow-up on
the crux of any disagreement), and a deterministic calibration step
extremizes the result. Every stage is persisted to `forecast_runs` /
`agent_traces` so misses can be diagnosed per-stage after settlement.

Run daily-ish:

```sh
python3 scan.py && python3 forecast.py 10 && python3 trade.py && python3 settle.py
```

## The gate

Do **not** trade real money unless, after ~50+ resolved forecasts,
`settle.py` shows the model's Brier score beating the market price's Brier
score on the same questions AND realized paper P&L is positive after fees.

## Design choices

- **Fee model**: Kalshi taker fee ≈ `0.07 * p * (1-p)` per contract, charged
  on entry; positions held to settlement.
- **Excluded**: sports game markets (efficient, huge count), crypto/index
  price ladders (random walks), one-sided books, mids outside 3–97c.
- **Anchoring guard**: the forecaster prompt forbids looking up prediction-
  market prices so `p_model` is independent of `p_market`.
- **Forecaster**: `claude -p --model claude-opus-4-8` with WebSearch — uses
  the local Claude Code login, no API key needed.

State lives in `kalshi_edge.db` (tables: markets, forecasts, trades,
settlements).
