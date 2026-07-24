"""Pastcast eval-set selection, dedupe, and Brier scoring — all on seeded
rows, no LLM calls."""
import pytest

import forecaster
import pastcast
from forecaster import runner
from tests.test_harness import fake_runner


def seed_resolved_question(conn, ticker, result, p_market, forecast_ts="2026-06-01T00:00:00"):
    conn.execute(
        """INSERT INTO markets (ticker, title, yes_sub_title, rules_primary,
                                close_time, category, status)
           VALUES (?, 'Q ' || ?, NULL, 'rules', '2026-06-20T00:00:00Z',
                   'Politics', 'settled')""", (ticker, ticker))
    conn.execute(
        """INSERT INTO forecasts (ticker, ts, p_model, p_market)
           VALUES (?, ?, 0.5, ?)""", (ticker, forecast_ts, p_market))
    conn.execute(
        "INSERT INTO settlements (ticker, result, settled_ts) VALUES (?, ?, ?)",
        (ticker, result, "2026-06-21T00:00:00"))
    conn.commit()


def test_eval_set_requires_original_forecast(conn):
    seed_resolved_question(conn, "T-A", "yes", 0.60)
    # resolved but never forecast live -> excluded (no as_of, no benchmark)
    conn.execute("INSERT INTO markets (ticker, title, status) VALUES ('T-B', 'B', 'settled')")
    conn.execute("INSERT INTO settlements (ticker, result, settled_ts) VALUES ('T-B','no','2026-06-21')")
    conn.commit()
    rows = pastcast.eval_set(conn)
    assert [r["ticker"] for r in rows] == ["T-A"]
    assert rows[0]["forecast_ts"].startswith("2026-06-01")


def test_pending_skips_already_pastcast_at_current_version(conn, monkeypatch):
    seed_resolved_question(conn, "T-A", "yes", 0.60)
    seed_resolved_question(conn, "T-B", "no", 0.30)
    rows = pastcast.eval_set(conn)
    assert len(pastcast.pending(conn, rows)) == 2

    monkeypatch.setattr(runner, "run_agent", fake_runner())
    res = pastcast.run_one(conn, rows[0])
    left = pastcast.pending(conn, pastcast.eval_set(conn))
    assert [r["ticker"] for r in left] == ["T-B"]

    # the pastcast run is tagged with as_of and never touches forecasts
    run = conn.execute("SELECT * FROM forecast_runs WHERE id=?", (res.run_id,)).fetchone()
    assert run["as_of"] == "2026-06-01"
    assert conn.execute("SELECT COUNT(*) c FROM forecasts").fetchone()["c"] == 2  # seeds only


def test_run_one_passes_as_of_into_prompts(conn, monkeypatch):
    seed_resolved_question(conn, "T-A", "yes", 0.60)
    seen = {}
    base = fake_runner()

    def spy(prompt, tools=("WebSearch", "WebFetch"), timeout=420):
        if "question analyst" in prompt:
            seen["analyst_prompt"] = prompt
        return base(prompt, tools, timeout)

    monkeypatch.setattr(runner, "run_agent", spy)
    pastcast.run_one(conn, pastcast.eval_set(conn)[0])
    assert "Today's date: 2026-06-01" in seen["analyst_prompt"]
    assert "Ignore any information published after" in seen["analyst_prompt"]


def test_scoreboard_brier_math(conn, monkeypatch):
    seed_resolved_question(conn, "T-A", "yes", 0.60)
    seed_resolved_question(conn, "T-B", "no", 0.30)
    monkeypatch.setattr(runner, "run_agent", fake_runner())
    for row in pastcast.eval_set(conn):
        pastcast.run_one(conn, row)

    board = pastcast.scoreboard(conn)
    assert len(board) == 1
    entry = board[0]
    assert entry["version"] == forecaster.HARNESS_VERSION
    assert entry["n"] == 2
    p = forecaster.calibrate.calibrate(0.34)  # stub supervisor always says 0.34
    assert entry["brier_model"] == pytest.approx(((p - 1) ** 2 + (p - 0) ** 2) / 2)
    assert entry["brier_market"] == pytest.approx(((0.60 - 1) ** 2 + (0.30 - 0) ** 2) / 2)


def test_scoreboard_latest_run_wins_and_ignores_failures(conn, monkeypatch):
    seed_resolved_question(conn, "T-A", "yes", 0.60)
    row = pastcast.eval_set(conn)[0]
    monkeypatch.setattr(runner, "run_agent", fake_runner())
    pastcast.run_one(conn, row)
    pastcast.run_one(conn, row)  # rerun same version: latest should win
    monkeypatch.setattr(runner, "run_agent", fake_runner(researchers_fail=3))
    with pytest.raises(forecaster.ForecastFailed):
        pastcast.run_one(conn, row)  # failed run must not enter the board

    board = pastcast.scoreboard(conn)
    assert board[0]["n"] == 1
