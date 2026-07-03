"""Settle paper trades against real Kalshi resolutions and score calibration.

- Fetches each open-trade / forecast ticker from the API; records results.
- Realizes P&L on settled trades.
- Prints Brier scores: model vs market price on the same questions.

Usage: python3 settle.py
"""
import time

import requests

import db

BASE = "https://api.elections.kalshi.com/trade-api/v2"


def fetch_result(ticker: str) -> str | None:
    r = requests.get(f"{BASE}/markets/{ticker}", timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    m = r.json()["market"]
    if m.get("status") in ("settled", "finalized") and m.get("result") in ("yes", "no"):
        return m["result"]
    return None


def main():
    conn = db.connect()
    tickers = {r["ticker"] for r in conn.execute(
        """SELECT ticker FROM forecasts
           WHERE ticker NOT IN (SELECT ticker FROM settlements)"""
    )}
    print(f"Checking {len(tickers)} unsettled ticker(s)...")
    for t in sorted(tickers):
        res = fetch_result(t)
        time.sleep(0.15)
        if res is None:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO settlements (ticker, result, settled_ts) "
            "VALUES (?,?,datetime('now'))", (t, res))
        conn.commit()
        print(f"  settled {t}: {res.upper()}")

    # Realize P&L on open trades with settlements
    open_trades = conn.execute(
        """SELECT t.*, s.result AS res FROM trades t
           JOIN settlements s ON s.ticker = t.ticker
           WHERE t.status = 'open'"""
    ).fetchall()
    for t in open_trades:
        won = t["side"] == t["res"]
        payout = t["contracts"] * (1.0 if won else 0.0)
        pnl = payout - t["entry_price"] * t["contracts"] - t["fee"]
        conn.execute("UPDATE trades SET status='settled', result=?, pnl=? WHERE id=?",
                     (t["res"], pnl, t["id"]))
        conn.commit()
        print(f"  P&L {t['ticker']}: {t['side'].upper()} x{t['contracts']} -> "
              f"{'WON' if won else 'LOST'} {pnl:+.2f}")

    # Scoreboard
    rows = conn.execute(
        """SELECT f.p_model, f.p_market, s.result FROM forecasts f
           JOIN settlements s ON s.ticker = f.ticker"""
    ).fetchall()
    if rows:
        bm = sum((r["p_model"] - (1 if r["result"] == "yes" else 0)) ** 2 for r in rows) / len(rows)
        bk = sum((r["p_market"] - (1 if r["result"] == "yes" else 0)) ** 2 for r in rows) / len(rows)
        print(f"\nCalibration over {len(rows)} resolved forecast(s):")
        print(f"  Brier (model):  {bm:.4f}")
        print(f"  Brier (market): {bk:.4f}   {'MODEL BEATS MARKET' if bm < bk else 'market wins'}")
    total = conn.execute(
        "SELECT COUNT(*) c, COALESCE(SUM(pnl),0) p FROM trades WHERE status='settled'"
    ).fetchone()
    if total["c"]:
        print(f"\nRealized paper P&L: ${total['p']:+.2f} over {total['c']} settled trade(s)")


if __name__ == "__main__":
    main()
