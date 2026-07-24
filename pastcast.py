"""Pastcasting: re-forecast already-resolved questions with the current
harness and score harness versions against the market price frozen at the
original forecast time.

Web search cannot be truly time-boxed, so post-date leakage is possible and
pastcast Briers are optimistic. Use for RELATIVE comparisons (harness
version A vs B, calibration fits, prompt A/Bs) — never as an absolute claim
of edge. The live gate in README.md remains the only gate.

Pastcast runs are stored in forecast_runs with as_of set; they never write
to the forecasts table, so trade.py cannot see them.

Usage:
  pastcast.py [N]      run the current harness on up to N resolved
                       questions it hasn't pastcast yet, then score
  pastcast.py --score  print the scoreboard only
"""
import sys

import db
import forecaster


def eval_set(conn) -> list:
    """Resolved questions joined to their earliest live forecast, which
    supplies the as_of date and the frozen market-price benchmark."""
    return conn.execute(
        """SELECT s.ticker, s.result, m.title, m.yes_sub_title,
                  m.rules_primary, m.close_time, m.category,
                  f.ts AS forecast_ts, f.p_market
           FROM settlements s
           JOIN markets m ON m.ticker = s.ticker
           JOIN forecasts f ON f.id = (SELECT id FROM forecasts
                                       WHERE ticker = s.ticker
                                       ORDER BY ts LIMIT 1)
           ORDER BY s.settled_ts""").fetchall()


def pending(conn, rows) -> list:
    """Eval-set rows not yet pastcast at the current harness version."""
    done = {r["question_id"] for r in conn.execute(
        """SELECT DISTINCT question_id FROM forecast_runs
           WHERE as_of IS NOT NULL AND harness_version = ?""",
        (forecaster.HARNESS_VERSION,))}
    return [r for r in rows if r["ticker"] not in done]


def run_one(conn, row) -> forecaster.ForecastResult:
    spec = forecaster.QuestionSpec(
        question_id=row["ticker"],
        title=row["title"],
        detail=row["yes_sub_title"],
        resolution_rules=(row["rules_primary"] or "")[:1500],
        close_time=row["close_time"],
        category=row["category"],
        as_of=row["forecast_ts"][:10],
    )
    return forecaster.forecast(spec, conn=conn)


def scoreboard(conn) -> list[dict]:
    """Per harness version: Brier of pastcast forecasts vs the frozen market
    Brier on the same questions. Latest run wins per (version, question)."""
    rows = conn.execute(
        """SELECT r.harness_version, r.question_id, r.p_calibrated,
                  s.result, f.p_market
           FROM forecast_runs r
           JOIN settlements s ON s.ticker = r.question_id
           JOIN forecasts f ON f.id = (SELECT id FROM forecasts
                                       WHERE ticker = r.question_id
                                       ORDER BY ts LIMIT 1)
           WHERE r.as_of IS NOT NULL AND r.status = 'ok'
           ORDER BY r.id""").fetchall()
    by_version: dict[str, dict[str, tuple]] = {}
    for r in rows:
        by_version.setdefault(r["harness_version"], {})[r["question_id"]] = (
            r["p_calibrated"], r["p_market"],
            1.0 if r["result"] == "yes" else 0.0)
    out = []
    for version in sorted(by_version):
        triples = by_version[version].values()
        n = len(triples)
        out.append({
            "version": version,
            "n": n,
            "brier_model": sum((p - y) ** 2 for p, _, y in triples) / n,
            "brier_market": sum((pm - y) ** 2 for _, pm, y in triples) / n,
        })
    return out


def print_scoreboard(conn):
    board = scoreboard(conn)
    if not board:
        print("No scored pastcasts yet.")
        return
    print(f"\n{'version':<12} {'n':>4} {'Brier(harness)':>15} {'Brier(market)':>14}")
    for row in board:
        tag = "  <-- harness wins" if row["brier_model"] < row["brier_market"] else ""
        print(f"{row['version']:<12} {row['n']:>4} {row['brier_model']:>15.4f} "
              f"{row['brier_market']:>14.4f}{tag}")
    print("(leakage caveat: pastcast Briers are optimistic; compare versions, "
          "don't claim edge)")


def main(argv):
    conn = db.connect()
    if "--score" in argv:
        print_scoreboard(conn)
        return
    n = int(argv[0]) if argv else 5
    todo = pending(conn, eval_set(conn))[:n]
    print(f"Pastcasting {len(todo)} question(s) with harness "
          f"{forecaster.HARNESS_VERSION}...")
    for row in todo:
        try:
            res = run_one(conn, row)
            print(f"  {row['ticker']}: raw={res.p_raw:.2f} -> "
                  f"{res.p_calibrated:.2f} vs market={row['p_market']:.2f} "
                  f"resolved {row['result'].upper()}")
        except forecaster.ForecastFailed as e:
            print(f"  FAILED {row['ticker']}: {e}")
    print_scoreboard(conn)


if __name__ == "__main__":
    main(sys.argv[1:])
