"""Forecast top-N candidate markets via the multi-agent harness in
forecaster/ (docs/forecaster-design.md) and store results in the forecasts
table for trade.py.

The market mid-price is captured here, outside the harness boundary — the
harness never sees prices (anchoring guard).

Usage: python3 forecast.py [N]   # forecast top-N candidates
"""
import sys
from datetime import datetime, timezone

import candidates
import db
import forecaster


def forecast_market(conn, row) -> int:
    spec = forecaster.QuestionSpec(
        question_id=row["ticker"],
        title=row["title"],
        detail=row["yes_sub_title"],
        resolution_rules=(row["rules_primary"] or "")[:1500],
        close_time=row["close_time"],
        category=row["category"],
    )
    res = forecaster.forecast(spec, conn=conn)
    mid = (row["yes_bid"] + row["yes_ask"]) / 2
    rationale = (f"[{res.key_comparison}] " if res.key_comparison else "") + res.rationale
    cur = conn.execute(
        """INSERT INTO forecasts
           (ticker, ts, p_model, p_market, yes_bid, yes_ask, confidence,
            rationale, model, run_id)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            row["ticker"], datetime.now(timezone.utc).isoformat(),
            res.p_calibrated, mid, row["yes_bid"], row["yes_ask"],
            res.confidence, rationale,
            f"harness/{res.harness_version}", res.run_id,
        ),
    )
    conn.commit()
    return cur.lastrowid


def main(n: int):
    conn = db.connect()
    cands = candidates.select(n)
    print(f"Forecasting {len(cands)} candidates with harness "
          f"{forecaster.HARNESS_VERSION} ({forecaster.N_RESEARCHERS} researchers)...")
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
            r = conn.execute("SELECT * FROM forecast_runs WHERE id=?",
                             (f["run_id"],)).fetchone()
            print(f"  {row['ticker']}: raw={r['p_raw']:.2f} -> model={f['p_model']:.2f} "
                  f"market={f['p_market']:.2f} spread={r['researcher_spread']:.2f} "
                  f"({f['confidence']}) — {row['title'][:60]}")
        except forecaster.ForecastFailed as e:
            print(f"  FAILED {row['ticker']}: {e}")
        except Exception as e:
            print(f"  ERROR {row['ticker']}: {e}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 5)
