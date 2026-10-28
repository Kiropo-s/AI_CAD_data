"""Shape acceptance policy, independent of CAD and metric implementations."""
from __future__ import annotations

EVALUATION_VERSION = "shape-world-1"
CD_PROTOCOL = "area-weighted-triangle-surface-1"
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
    # Each reliable failed check is conclusive even if a different measurement failed.
    if failed:
        return {"outcome": "rejected", "reason_code": "geometry.mismatch", "failed_checks": failed}
    if any(error.get("required") for error in errors):
        return {"outcome": "incomplete", "reason_code": "measurement.unreliable", "failed_checks": failed}
    if metrics.get("coordinate_check", {}).get("status") != "resolved":
        return {"outcome": "incomplete", "reason_code": "measurement.frame_unresolved", "failed_checks": failed}
    if all(checks.get(name) == "pass" for name in THRESHOLDS):
        return {"outcome": "accepted", "reason_code": "geometry.verified", "failed_checks": []}
    return {"outcome": "incomplete", "reason_code": "measurement.unreliable", "failed_checks": failed}
