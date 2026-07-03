"""Turn fresh forecasts into paper trades when the edge clears fees + spread + margin.

Fee model: Kalshi taker fee ≈ 0.07 * price * (1 - price) per contract, rounded
up to the cent per fill. We charge it on entry only (hold to settlement).

Sizing: fractional Kelly (quarter), capped at MAX_POSITION_FRAC of bankroll.

Usage: python3 trade.py
"""
import math
from datetime import datetime, timezone

import db

BANKROLL = 1000.00        # paper dollars
KELLY_FRACTION = 0.25
MAX_POSITION_FRAC = 0.05  # max 5% of bankroll per trade
EDGE_MARGIN = 0.05        # required EV per contract beyond fees, in dollars


def taker_fee(price: float, contracts: int) -> float:
    return math.ceil(7 * price * (1 - price) * contracts) / 100


def kelly_contracts(p_win: float, price: float) -> int:
    """Contracts to buy at `price` with win prob `p_win`, quarter-Kelly."""
    if price <= 0 or price >= 1:
        return 0
    b = (1 - price) / price          # net odds per dollar staked
    f = (p_win * b - (1 - p_win)) / b
    f = max(0.0, f) * KELLY_FRACTION
    stake = min(f, MAX_POSITION_FRAC) * BANKROLL
    return int(stake // price)


def evaluate(f) -> dict | None:
    """Return a trade dict for forecast row `f`, or None if no edge."""
    p, bid, ask = f["p_model"], f["yes_bid"], f["yes_ask"]
    # Buy YES at ask: EV/contract = p - ask - fee
    fee_yes = taker_fee(ask, 1)
    ev_yes = p - ask - fee_yes
    # Buy NO at (1 - bid): EV/contract = (1-p) - (1-bid) - fee = bid - p - fee
    no_price = 1 - bid
    fee_no = taker_fee(no_price, 1)
    ev_no = bid - p - fee_no

    if ev_yes >= EDGE_MARGIN and ev_yes >= ev_no:
        side, price, ev, p_win = "yes", ask, ev_yes, p
    elif ev_no >= EDGE_MARGIN:
        side, price, ev, p_win = "no", no_price, ev_no, 1 - p
    else:
        return None

    n = kelly_contracts(p_win, price)
    if n < 1:
        return None
    return {"side": side, "entry_price": price, "contracts": n,
            "fee": taker_fee(price, n), "edge": ev}


def main():
    conn = db.connect()
    fresh = conn.execute(
        """SELECT f.* FROM forecasts f
           WHERE f.ts > datetime('now', '-1 day')
             AND NOT EXISTS (SELECT 1 FROM trades t
                             WHERE t.ticker = f.ticker AND t.status = 'open')"""
    ).fetchall()

    n_trades = 0
    for f in fresh:
        t = evaluate(f)
        tag = f"{f['ticker']:<45} model={f['p_model']:.2f} mkt={f['p_market']:.2f}"
        if t is None:
            print(f"  pass  {tag}")
            continue
        conn.execute(
            """INSERT INTO trades (forecast_id, ticker, ts, side, entry_price,
                                   contracts, fee, p_model, edge)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (f["id"], f["ticker"], datetime.now(timezone.utc).isoformat(),
             t["side"], t["entry_price"], t["contracts"], t["fee"],
             f["p_model"], t["edge"]),
        )
        conn.commit()
        n_trades += 1
        cost = t["entry_price"] * t["contracts"] + t["fee"]
        print(f"  TRADE {tag}  -> {t['side'].upper()} x{t['contracts']} @ "
              f"{t['entry_price']:.2f} (cost ${cost:.2f}, fee ${t['fee']:.2f}, "
              f"edge {t['edge']:+.2f}/contract)")
    print(f"{n_trades} paper trade(s) opened from {len(fresh)} fresh forecast(s)")


if __name__ == "__main__":
    main()
