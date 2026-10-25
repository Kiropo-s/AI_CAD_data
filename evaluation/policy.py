"""Shape acceptance policy, independent of CAD and metric implementations."""
from __future__ import annotations

EVALUATION_VERSION = "similarity-shape-v4"
CD_PROTOCOL = "similarity-surface-v3"
THRESHOLDS = {"volume_relative_error": 1e-3, "volume_iou": 0.999,
              "chamfer_relative": 0.01}
CHECK_DIRECTIONS = {"volume_relative_error": "max", "volume_iou": "min", "chamfer_relative": "max"}


def metric_check(name: str, value: float | None) -> str:
    """Apply explicit threshold direction; IoU has a lower bound."""
    import math
    if value is None or not math.isfinite(value):
        return "error"
    if name == "volume_iou" and not 0 <= value <= 1:
        return "error"
    accepted = value >= THRESHOLDS[name] if CHECK_DIRECTIONS[name] == "min" else value <= THRESHOLDS[name]
    return "pass" if accepted else "fail"


def decide(metrics: dict) -> dict:
    """Map evidence to a status; no optional diagnostic is a hidden hard gate."""
    errors = metrics.get("metric_errors", [])
    checks = metrics.get("checks", {})
    failed = [name for name in THRESHOLDS if checks.get(name) == "fail"]
    # Normalized volume is rotation invariant, so an independently confirmed
    # volume failure remains conclusive even when another measurement fails.
    if "volume_relative_error" in failed:
        return {"status": "rejected", "reason": "geometry_mismatch", "failed_checks": failed}
    if any(error.get("required") for error in errors):
        return {"status": "provisional", "reason": "measurement_error", "failed_checks": failed}
    if metrics.get("registration", {}).get("status") != "resolved":
        return {"status": "provisional", "reason": "registration_unresolved", "failed_checks": failed}
    if failed:
        return {"status": "rejected", "reason": "geometry_mismatch", "failed_checks": failed}
    if all(checks.get(name) == "pass" for name in THRESHOLDS):
        return {"status": "train_ready", "reason": "accepted", "failed_checks": []}
    return {"status": "provisional", "reason": "measurement_error", "failed_checks": failed}
