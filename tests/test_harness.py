"""Harness pipeline tests with runner.run_agent stubbed (no LLM calls).

Covers the happy path, supervisor fallback, researcher-failure abort,
calibration math, DB migration, and the audit trail.
"""
import json

import pytest

import forecaster
from forecaster import calibrate, runner

ANALYST_OUT = {
    "question_type": "quantity-threshold",
    "resolution_summary": "Resolves YES if the July CPI YoY print exceeds 3.2%.",
    "subquestions": ["Current CPI trajectory?", "Recent MoM prints?",
                     "Release date vs close?"],
    "research_plan": "Check BLS releases and the official schedule.",
}
RESEARCHER_OUTS = [
    {"findings": [{"subquestion": "s", "finding": "f",
                   "sources": ["https://bls.gov/a"]}],
     "base_rate": "b", "key_comparison": "3.2 vs 3.0", "probability_yes": p,
     "confidence": "medium", "rationale": "r"}
    for p in (0.30, 0.40, 0.35)
]
SUPERVISOR_OUT = {
    "disagreements": "minor", "crux": "June MoM print",
    "key_comparison": "3.2 vs ~3.0", "probability_yes": 0.34,
    "confidence": "medium",
    "rationale": "Researchers agree the pace is below threshold.",
}


def fake_runner(supervisor_fails=False, researchers_fail=0):
    state = {"researchers": 0}

    def fake(prompt, tools=("WebSearch", "WebFetch"), timeout=420):
        if "question analyst" in prompt:
            return dict(ANALYST_OUT)
        if "supervising forecaster" in prompt:
            if supervisor_fails:
                raise runner.AgentError("boom")
            return dict(SUPERVISOR_OUT)
        i = state["researchers"]
        state["researchers"] += 1
        if i < researchers_fail:
            raise runner.AgentError("researcher down")
        return dict(RESEARCHER_OUTS[i % 3])
    return fake


def spec():
    return forecaster.QuestionSpec(
        question_id="TEST-1", title="Will CPI YoY exceed 3.2% in July?",
        detail=None,
        resolution_rules="Resolves YES if BLS July CPI YoY > 3.2%.",
        close_time="2026-08-15T00:00:00Z")


def test_calibration_math():
    a = calibrate.EXTREMIZATION_ALPHA
    assert calibrate.calibrate(0.34) == pytest.approx(
        0.34**a / (0.34**a + 0.66**a))
    assert calibrate.calibrate(0.34) < 0.34          # pushed away from 0.5
    assert calibrate.calibrate(0.72) > 0.72
    assert calibrate.calibrate(0.5) == pytest.approx(0.5)
    assert calibrate.calibrate(0.001) == calibrate.P_MIN
    assert calibrate.calibrate(0.999) == calibrate.P_MAX


def test_forecasts_run_id_migration(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(forecasts)")}
    assert "run_id" in cols


def test_happy_path(conn, monkeypatch):
    monkeypatch.setattr(runner, "run_agent", fake_runner())
    res = forecaster.forecast(spec(), conn=conn)

    assert res.p_raw == pytest.approx(0.34)
    assert res.p_calibrated == pytest.approx(calibrate.calibrate(0.34))

    run = conn.execute("SELECT * FROM forecast_runs WHERE id=?",
                       (res.run_id,)).fetchone()
    assert run["status"] == "ok"
    assert run["supervisor_fallback"] == 0
    assert run["researcher_spread"] == pytest.approx(0.10)
    assert run["question_type"] == "quantity-threshold"
    assert json.loads(run["calibration"])["method"] == "extremize"

    traces = conn.execute(
        "SELECT stage, sources FROM agent_traces WHERE run_id=? ORDER BY id",
        (res.run_id,)).fetchall()
    assert [t["stage"] for t in traces] == [
        "analyst", "researcher", "researcher", "researcher", "supervisor"]
    assert json.loads(traces[1]["sources"]) == ["https://bls.gov/a"]


def test_supervisor_fallback_uses_researcher_median(conn, monkeypatch):
    monkeypatch.setattr(runner, "run_agent", fake_runner(supervisor_fails=True))
    res = forecaster.forecast(spec(), conn=conn)

    assert res.p_raw == pytest.approx(0.35)  # median of 0.30/0.40/0.35
    run = conn.execute("SELECT * FROM forecast_runs WHERE id=?",
                       (res.run_id,)).fetchone()
    assert run["status"] == "ok"
    assert run["supervisor_fallback"] == 1
    n_errs = conn.execute(
        """SELECT COUNT(*) c FROM agent_traces
           WHERE run_id=? AND stage='supervisor' AND error IS NOT NULL""",
        (res.run_id,)).fetchone()["c"]
    assert n_errs == forecaster.STAGE_ATTEMPTS


def test_too_few_researchers_aborts(conn, monkeypatch):
    monkeypatch.setattr(runner, "run_agent", fake_runner(researchers_fail=2))
    with pytest.raises(forecaster.ForecastFailed):
        forecaster.forecast(spec(), conn=conn)
    run = conn.execute(
        "SELECT * FROM forecast_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert run["status"] == "failed"


def test_one_researcher_down_is_tolerated(conn, monkeypatch):
    monkeypatch.setattr(runner, "run_agent", fake_runner(researchers_fail=1))
    res = forecaster.forecast(spec(), conn=conn)
    run = conn.execute("SELECT * FROM forecast_runs WHERE id=?",
                       (res.run_id,)).fetchone()
    assert run["status"] == "ok"


def test_bad_probability_rejected(conn, monkeypatch):
    bad = fake_runner()

    def with_bad_supervisor(prompt, tools=("WebSearch", "WebFetch"), timeout=420):
        out = bad(prompt, tools, timeout)
        if "supervising forecaster" in prompt:
            out["probability_yes"] = 1.7
        return out

    monkeypatch.setattr(runner, "run_agent", with_bad_supervisor)
    res = forecaster.forecast(spec(), conn=conn)
    # invalid supervisor output -> retries exhausted -> median fallback
    assert res.p_raw == pytest.approx(0.35)
