"""Snapshot every open Kalshi market into SQLite.

Usage: python3 scan.py
"""
import time
from datetime import datetime, timezone

import requests

import db

BASE = "https://api.elections.kalshi.com/trade-api/v2"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "kalshi-edge-paper/0.1"


def paged(path: str, key: str, params: dict):
    cursor = None
    while True:
        p = dict(params)
        if cursor:
            p["cursor"] = cursor
        r = SESSION.get(f"{BASE}{path}", params=p, timeout=30)
        r.raise_for_status()
        data = r.json()
        yield from data[key]
        cursor = data.get("cursor")
        if not cursor:
            return
        time.sleep(0.15)  # stay under the public rate limit


def dollars(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    now = datetime.now(timezone.utc).isoformat()

    print("Fetching open events...")
    event_meta = {}
    for e in paged("/events", "events", {"limit": 200, "status": "open"}):
        event_meta[e["event_ticker"]] = (e.get("series_ticker", ""), e.get("category", ""))
    print(f"  {len(event_meta)} open events")

    print("Fetching open markets...")
    conn = db.connect()
    n = 0
    skipped_mve = 0
    with conn:
        for m in paged("/markets", "markets", {"limit": 1000, "status": "open"}):
            if m.get("mve_collection_ticker"):
                skipped_mve += 1
                continue
            if m.get("market_type") != "binary":
                continue
            series, category = event_meta.get(m.get("event_ticker", ""), ("", ""))
            conn.execute(
                """INSERT OR REPLACE INTO markets
                   (ticker, event_ticker, series_ticker, category, title, yes_sub_title,
                    rules_primary, market_type, status, close_time, yes_bid, yes_ask,
                    last_price, volume, open_interest, liquidity, snapshot_ts)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    m["ticker"], m.get("event_ticker"), series, category,
                    m.get("title"), m.get("yes_sub_title"),
                    (m.get("rules_primary") or "")[:2000],
                    m.get("market_type"), m.get("status"), m.get("close_time"),
                    dollars(m.get("yes_bid_dollars")), dollars(m.get("yes_ask_dollars")),
                    dollars(m.get("last_price_dollars")),
                    dollars(m.get("volume_fp")), dollars(m.get("open_interest_fp")),
                    dollars(m.get("liquidity_dollars")), now,
                ),
            )
            n += 1
            if n % 5000 == 0:
                print(f"  ...{n} markets")
    print(f"Stored {n} binary markets ({skipped_mve} MVE/parlay skipped)")


if __name__ == "__main__":
    main()
