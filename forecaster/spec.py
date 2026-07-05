"""Public dataclasses for the forecasting harness.

QuestionSpec deliberately excludes market prices (bid/ask/mid/volume): the
anchoring guard is structural — the harness never receives them.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class QuestionSpec:
    question_id: str        # opaque; the caller supplies (we use the ticker)
    title: str
    detail: str | None
    resolution_rules: str
    close_time: str         # ISO 8601
    category: str | None = None
    as_of: str | None = None  # pastcasting: pretend today is this date (YYYY-MM-DD)


@dataclass(frozen=True)
class ForecastResult:
    run_id: int
    p_raw: float            # supervisor output, pre-calibration
    p_calibrated: float     # what trade.py should consume
    confidence: str         # low | medium | high
    rationale: str
    key_comparison: str
    harness_version: str
