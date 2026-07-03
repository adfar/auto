"""Select tradeable candidate markets from the latest scan.

Hard filters first (liquidity, spread, horizon, category), then rank.
Usage: python3 candidates.py [N]
"""
import sys
from datetime import datetime, timedelta, timezone

import db

# Categories where an LLM plausibly has synthesis edge and questions are
# researchable. Sports game markets are efficient + huge in number; crypto/
# equity price ladders are random walks at short horizons.
EXCLUDED_CATEGORIES = {"Sports", "Financials", "Crypto", "Economics"}
EXCLUDED_SERIES_PREFIXES = ("KXBTC", "KXETH", "KXNASDAQ", "KXINX", "KXUSDT")

MIN_PRICE = 0.03      # skip near-certain markets: no room / bad tail calibration
MAX_PRICE = 0.97
MAX_SPREAD = 0.10
MIN_OPEN_INTEREST = 25
MIN_HORIZON_HOURS = 12
MAX_HORIZON_DAYS = 45
PER_EVENT_CAP = 1     # avoid forecasting 20 strikes of the same event


def select(limit: int = 20) -> list:
    conn = db.connect()
    now = datetime.now(timezone.utc)
    lo = (now + timedelta(hours=MIN_HORIZON_HOURS)).isoformat()
    hi = (now + timedelta(days=MAX_HORIZON_DAYS)).isoformat()

    rows = conn.execute(
        """SELECT * FROM markets
           WHERE status IN ('active', 'open')
             AND yes_bid IS NOT NULL AND yes_ask IS NOT NULL""",
    ).fetchall()

    out = []
    seen_events = {}
    for r in rows:
        if r["category"] in EXCLUDED_CATEGORIES:
            continue
        if any(r["ticker"].startswith(p) for p in EXCLUDED_SERIES_PREFIXES):
            continue
        bid, ask = r["yes_bid"] or 0.0, r["yes_ask"] or 1.0
        if bid <= 0 or ask >= 1:
            continue  # one-sided book
        mid = (bid + ask) / 2
        if not (MIN_PRICE <= mid <= MAX_PRICE):
            continue
        if ask - bid > MAX_SPREAD:
            continue
        if (r["open_interest"] or 0) < MIN_OPEN_INTEREST:
            continue
        ct = r["close_time"] or ""
        if not (lo <= ct <= hi):
            continue
        if seen_events.get(r["event_ticker"], 0) >= PER_EVENT_CAP:
            continue
        seen_events[r["event_ticker"]] = seen_events.get(r["event_ticker"], 0) + 1
        out.append(r)

    # Nearest resolution first: fastest calibration feedback per LLM call.
    out.sort(key=lambda r: r["close_time"])
    return out[:limit]


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    for r in select(n):
        mid = (r["yes_bid"] + r["yes_ask"]) / 2
        print(f"{r['ticker']:<45} {mid:5.2f}  spr={r['yes_ask']-r['yes_bid']:.2f} "
              f"oi={int(r['open_interest'] or 0):<6} {r['category']:<12} "
              f"closes {r['close_time'][:10]}  {r['title'][:60]}")
