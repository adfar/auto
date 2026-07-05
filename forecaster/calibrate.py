"""Stage D: deterministic calibration.

v1 is fixed extremization (counters the documented LLM hedge-toward-50%
bias) plus a tail clamp matching the prompt guidance. Replace with fitted
Platt scaling once >=50 harness forecasts have resolved; both p_raw and
p_calibrated are persisted so recalibration is always retroactive.
"""
EXTREMIZATION_ALPHA = 1.3
P_MIN, P_MAX = 0.02, 0.98


def calibrate(p_raw: float) -> float:
    a = EXTREMIZATION_ALPHA
    num = p_raw ** a
    p = num / (num + (1.0 - p_raw) ** a)
    return min(max(p, P_MIN), P_MAX)


def describe() -> dict:
    """Stored on each forecast_runs row so every forecast records how it was calibrated."""
    return {"method": "extremize", "alpha": EXTREMIZATION_ALPHA, "clamp": [P_MIN, P_MAX]}
