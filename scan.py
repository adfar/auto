"""Snapshot open Kalshi markets in target categories into SQLite.

Strategy: build a series -> category map from /series (one request per
category we care about), then paginate /markets bounded to the candidate
horizon, keeping only markets whose series is in an allowed category.
Commits per page so progress is visible and restarts are cheap.

Usage: python3 -u scan.py
"""
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

import db

BASE = "https://api.elections.kalshi.com/trade-api/v2"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "kalshi-edge-paper/0.2"

CATEGORIES = [
    "Politics", "World", "Climate and Weather", "Science and Technology",
    "Entertainment", "Companies", "Health", "Economics",
]
MIN_HORIZON_HOURS = 6     # scan slightly wider than the candidate filter
MAX_HORIZON_DAYS = 45


def get(path: str, params: dict, retries: int = 4) -> dict:
    for attempt in range(retries):
        try:
            r = SESSION.get(f"{BASE}{path}", params=params, timeout=60)
            if r.status_code == 429:
                wait = 2 ** (attempt + 1)
                print(f"  rate limited, sleeping {wait}s", flush=True)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise
            print(f"  retry after error: {e}", flush=True)
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def series_category_map() -> dict:
    m = {}
    for cat in CATEGORIES:
        data = get("/series", {"category": cat})
        for s in data.get("series") or []:
            m[s["ticker"]] = cat
        print(f"  {cat}: {len(data.get('series') or [])} series", flush=True)
        time.sleep(0.2)
    return m


def dollars(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    now = datetime.now(timezone.utc)
    min_ts = int((now + timedelta(hours=MIN_HORIZON_HOURS)).timestamp())
    max_ts = int((now + timedelta(days=MAX_HORIZON_DAYS)).timestamp())
    snapshot_ts = now.isoformat()

    print("Building series -> category map...", flush=True)
    cat_map = series_category_map()
    print(f"{len(cat_map)} series in allowed categories", flush=True)

    conn = db.connect()
    cursor = None
    kept = seen = page = 0
    while True:
        params = {"limit": 1000, "status": "open",
                  "min_close_ts": min_ts, "max_close_ts": max_ts}
        if cursor:
            params["cursor"] = cursor
        data = get("/markets", params)
        page += 1
        batch = []
        for m in data["markets"]:
            seen += 1
            if m.get("mve_collection_ticker") or m.get("market_type") != "binary":
                continue
            series = m["ticker"].split("-")[0]
            cat = cat_map.get(series)
            if cat is None:
                continue
            batch.append((
                m["ticker"], m.get("event_ticker"), series, cat,
                m.get("title"), m.get("yes_sub_title"),
                (m.get("rules_primary") or "")[:2000],
                m.get("market_type"), m.get("status"), m.get("close_time"),
                dollars(m.get("yes_bid_dollars")), dollars(m.get("yes_ask_dollars")),
                dollars(m.get("last_price_dollars")),
                dollars(m.get("volume_fp")), dollars(m.get("open_interest_fp")),
                dollars(m.get("liquidity_dollars")), snapshot_ts,
            ))
        with conn:
            conn.executemany(
                """INSERT OR REPLACE INTO markets
                   (ticker, event_ticker, series_ticker, category, title, yes_sub_title,
                    rules_primary, market_type, status, close_time, yes_bid, yes_ask,
                    last_price, volume, open_interest, liquidity, snapshot_ts)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", batch)
        kept += len(batch)
        print(f"page {page}: seen {seen}, kept {kept}", flush=True)
        cursor = data.get("cursor")
        if not cursor:
            break
        time.sleep(0.25)

    # Drop rows from previous snapshots (markets now closed or out of window)
    with conn:
        n = conn.execute("DELETE FROM markets WHERE snapshot_ts != ?",
                         (snapshot_ts,)).rowcount
    print(f"Done: {kept} markets stored ({n} stale rows removed)", flush=True)


if __name__ == "__main__":
    main()
