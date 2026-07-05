"""Multi-agent forecasting harness (docs/forecaster-design.md).

Public surface:

    result = forecaster.forecast(QuestionSpec(...))

Pipeline: analyst (decompose) -> K parallel researchers (web) ->
supervisor (reconcile, web) -> deterministic calibration. Every stage is
persisted to forecast_runs / agent_traces.
"""
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

import db

from . import analyst, calibrate, researcher, store, supervisor
from .runner import AgentError, DEFAULT_TIMEOUT_S
from .spec import ForecastResult, QuestionSpec
from .version import HARNESS_VERSION

__all__ = ["forecast", "ForecastFailed", "ForecastResult", "QuestionSpec",
           "HARNESS_VERSION"]

N_RESEARCHERS = 3
MIN_RESEARCHERS = 2
STAGE_ATTEMPTS = 3        # analyst / supervisor: 1 try + 2 retries
BUDGET_S = 25 * 60        # whole-question wall-clock budget


class ForecastFailed(RuntimeError):
    """The harness could not produce a forecast; nothing was written to `forecasts`."""


def forecast(spec: QuestionSpec, conn=None) -> ForecastResult:
    conn = conn or db.connect()
    run_id = store.start_run(conn, spec, HARNESS_VERSION, N_RESEARCHERS)
    t0 = time.monotonic()
    deadline = t0 + BUDGET_S
    try:
        analysis = _run_stage(
            conn, run_id, "analyst",
            lambda to: analyst.run(spec, timeout=to), deadline)

        reports = _run_researchers(conn, run_id, spec, analysis, deadline)
        probs = [r["probability_yes"] for r in reports]
        spread = max(probs) - min(probs)

        fallback = 0
        try:
            final = _run_stage(
                conn, run_id, "supervisor",
                lambda to: supervisor.run(spec, analysis, reports, timeout=to),
                deadline)
        except ForecastFailed:
            fallback = 1
            final = {
                "probability_yes": statistics.median(probs),
                "confidence": "low",
                "key_comparison": "",
                "crux": None,
                "rationale": ("Supervisor stage failed; probability is the "
                              "median of the researcher ensemble."),
            }

        p_raw = float(final["probability_yes"])
        p_cal = calibrate.calibrate(p_raw)
        store.finish_run(
            conn, run_id, "ok",
            p_raw=p_raw, p_calibrated=p_cal, calibration=calibrate.describe(),
            confidence=final.get("confidence") or "low",
            rationale=final.get("rationale") or "",
            crux=final.get("crux"),
            question_type=analysis["question_type"],
            researcher_spread=spread,
            supervisor_fallback=fallback,
            duration_s=time.monotonic() - t0,
        )
        return ForecastResult(
            run_id=run_id, p_raw=p_raw, p_calibrated=p_cal,
            confidence=final.get("confidence") or "low",
            rationale=final.get("rationale") or "",
            key_comparison=final.get("key_comparison") or "",
            harness_version=HARNESS_VERSION,
        )
    except ForecastFailed:
        store.finish_run(conn, run_id, "failed", duration_s=time.monotonic() - t0)
        raise


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left < 60:
        raise ForecastFailed("question wall-clock budget exhausted")
    return min(DEFAULT_TIMEOUT_S, left)


def _run_stage(conn, run_id, stage, fn, deadline):
    """Run an analyst/supervisor stage with retries, tracing every attempt."""
    last_err = None
    for _ in range(STAGE_ATTEMPTS):
        timeout = _remaining(deadline)
        t = time.monotonic()
        try:
            out = fn(timeout)
        except AgentError as e:
            last_err = str(e)
            store.add_trace(conn, run_id, stage, error=last_err,
                            duration_s=time.monotonic() - t)
            continue
        store.add_trace(conn, run_id, stage, output=out,
                        duration_s=time.monotonic() - t)
        return out
    raise ForecastFailed(f"{stage} failed after {STAGE_ATTEMPTS} attempts: {last_err}")


def _run_researchers(conn, run_id, spec, analysis, deadline):
    timeout = _remaining(deadline)

    def one(i):
        t = time.monotonic()
        try:
            out = researcher.run(spec, analysis, timeout=timeout)
            return i, out, None, time.monotonic() - t
        except AgentError as e:
            return i, None, str(e), time.monotonic() - t

    with ThreadPoolExecutor(max_workers=N_RESEARCHERS) as pool:
        results = list(pool.map(one, range(N_RESEARCHERS)))

    reports = []
    for i, out, err, dur in results:
        store.add_trace(conn, run_id, "researcher", agent_index=i, output=out,
                        sources=researcher.sources(out) if out else None,
                        error=err, duration_s=dur)
        if out is not None:
            reports.append(out)
    if len(reports) < MIN_RESEARCHERS:
        raise ForecastFailed(
            f"only {len(reports)}/{N_RESEARCHERS} researchers succeeded "
            f"(need {MIN_RESEARCHERS})")
    return reports
