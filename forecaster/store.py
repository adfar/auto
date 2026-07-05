"""Persistence for forecast_runs and agent_traces (the audit trail)."""
import json
from datetime import datetime, timezone

from .spec import QuestionSpec

_FINISH_FIELDS = {
    "p_raw", "p_calibrated", "calibration", "confidence", "rationale", "crux",
    "question_type", "researcher_spread", "supervisor_fallback", "duration_s",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def start_run(conn, spec: QuestionSpec, harness_version: str, n_researchers: int) -> int:
    cur = conn.execute(
        """INSERT INTO forecast_runs
           (question_id, ts, harness_version, n_researchers, status, as_of)
           VALUES (?,?,?,?, 'running', ?)""",
        (spec.question_id, _now(), harness_version, n_researchers, spec.as_of),
    )
    conn.commit()
    return cur.lastrowid


def add_trace(conn, run_id: int, stage: str, agent_index: int | None = None,
              output: dict | None = None, sources: list[str] | None = None,
              error: str | None = None, duration_s: float | None = None) -> None:
    conn.execute(
        """INSERT INTO agent_traces
           (run_id, stage, agent_index, output_json, sources, error, duration_s)
           VALUES (?,?,?,?,?,?,?)""",
        (run_id, stage, agent_index,
         json.dumps(output) if output is not None else None,
         json.dumps(sources) if sources is not None else None,
         error, duration_s),
    )
    conn.commit()


def finish_run(conn, run_id: int, status: str, **fields) -> None:
    unknown = set(fields) - _FINISH_FIELDS
    if unknown:
        raise ValueError(f"unknown forecast_runs fields: {unknown}")
    if "calibration" in fields and isinstance(fields["calibration"], dict):
        fields["calibration"] = json.dumps(fields["calibration"])
    cols = ["status"] + list(fields)
    vals = [status] + list(fields.values())
    conn.execute(
        f"UPDATE forecast_runs SET {', '.join(c + '=?' for c in cols)} WHERE id=?",
        (*vals, run_id),
    )
    conn.commit()
