"""Required CD/IoU/volume measurements with independent reliability checks."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import scipy
import OCP
from scipy.spatial import cKDTree
from OCP.Precision import Precision
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer

from step2code.verification.metrics import (GeometryMetricsError, _bbox_error, _sample_surface,
                              area, bbox, read_step, solid_count,
                              symmetric_difference_volume as _difference, volume_iou as _iou)
from step2code.verification.policy import (CD_PROTOCOL, EVALUATION_VERSION, THRESHOLDS,
                               CHECK_DIRECTIONS, decide, metric_check)
from step2code.verification.conditioning import DEFLECTION, LENGTH, distances, properties, solids


def volume(shape):
    return properties(shape)[0]


def symmetric_difference_volume(first, second):
    return _difference(first, second)


def volume_iou(first, second):
    return _iou(first, second)


METRIC_KEYS = ("volume_relative_error", "symmetric_difference_relative", "chamfer_relative",
               "symmetric_difference_volume_mm3", "chamfer_mean_mm", "chamfer_distance_mm2",
               "area_relative_error", "bbox_max_relative_error", "volume_iou")


def _error(name: str, exc: Exception, required: bool) -> dict:
    return {"metric": name, "required": required, "error": f"{type(exc).__name__}: {exc}"}


def _capture(report: dict, name: str, function, *, required=False):
    try:
        value = function()
        if isinstance(value, (float, int)) and not math.isfinite(value):
            raise GeometryMetricsError("nonfinite metric")
        report[name] = value
        return value
    except Exception as exc:
        report[name] = None
        report["metric_errors"].append(_error(name, exc, required))
        return None


def _raw(shape: Any) -> dict:
    # Raw metrics are diagnostics; failed area integration must not prevent
    # shape evaluation. Body count is supplied separately by the build worker.
    result = {"solid_count": solid_count(shape), "metric_errors": []}
    for name, fn in (("volume_mm3", volume), ("area_mm2", area), ("bbox_mm", bbox)):
        _capture(result, name, lambda fn=fn: fn(shape))
    return result


def _local_diagnostics(first: Any, second: Any, a: np.ndarray, b: np.ndarray) -> dict:
    result = {"points_per_face": 32, "points_per_solid": 128, "relative_p95_max": 0.0,
              "directions": []}
    for label, shape, target in (("reference_to_candidate", first, b), ("candidate_to_reference", second, a)):
        tree = cKDTree(target)
        groups = []
        explorer = TopExp_Explorer(shape, TopAbs_FACE)
        while explorer.More():
            groups.append(("face", explorer.Current(), 32))
            explorer.Next()
        groups.extend(("solid", part, 128) for part in solids(shape))
        entries = []
        for index, (kind, part, count) in enumerate(groups):
            points = _sample_surface(part, count, 1000 + index, DEFLECTION)
            p95 = float(np.percentile(tree.query(points)[0], 95)) / LENGTH
            entries.append({"kind": kind, "index": index, "relative_p95": p95})
            result["relative_p95_max"] = max(result["relative_p95_max"], p95)
        result["directions"].append({"direction": label, "groups": entries})
    return result


def _shared_interior_witness(first: Any, second: Any) -> list[float] | None:
    """Cheap positive certificate against a claimed empty intersection.

    Failure to find a point proves nothing. This never estimates a volume or
    substitutes for any hard metric, and is bounded at 129 point pairs.
    """
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN
    from OCP.gp import gp_Pnt
    a, b = bbox(first), bbox(second)
    low = np.maximum(a[:3], b[:3])
    high = np.minimum(a[3:], b[3:])
    if np.any(high <= low):
        return None
    groups = [[BRepClass3d_SolidClassifier(part) for part in solids(shape)] for shape in (first, second)]
    rng = np.random.default_rng(31)
    points = np.vstack(((low + high) / 2, rng.uniform(low, high, size=(128, 3))))
    for point in points:
        location = gp_Pnt(*map(float, point))
        inside = []
        for classifiers in groups:
            found = False
            for classifier in classifiers:
                classifier.Perform(location, Precision.Confusion_s())
                if classifier.State() == TopAbs_IN:
                    found = True
                    break
            inside.append(found)
        if all(inside):
            return point.tolist()
    return None


def _pose_metrics(reference: Any, candidate: Any, count: int, *, diagnostics=False) -> dict:
    result = {key: None for key in METRIC_KEYS}
    result.update(checks={}, metric_errors=[], warnings=[], diagnostics=[])
    vr, vc = volume(reference), volume(candidate)
    result.update(reference_volume_mm3=vr, candidate_volume_mm3=vc,
                  reference_bbox_mm=bbox(reference), candidate_bbox_mm=bbox(candidate))
    result["volume_relative_error"] = abs(vc - vr) / vr
    iou = _capture(result, "volume_iou", lambda: volume_iou(reference, candidate), required=True)
    # Common may overshoot a coincident input volume by floating roundoff.
    # Keep the raw IoU; only a separately verified Cut can qualify that value.
    iou_roundoff = iou is not None and 1 < iou <= 1 + 1e-8
    if iou is not None and (iou < 0 or iou > 1 + 1e-8):
        result["metric_errors"].append(_error("volume_iou",
            GeometryMetricsError("IoU outside numerical physical bounds; raw result retained"), True))
    try:
        a = _sample_surface(reference, count, 0, DEFLECTION)
        b = _sample_surface(candidate, count, 0, DEFLECTION)
        ab, ba = distances(a, b)
        result["chamfer_distance_mm2"] = float(np.mean(ab ** 2) + np.mean(ba ** 2))
        result["chamfer_mean_mm"] = float((ab.mean() + ba.mean()) / 2)
        result["chamfer_relative"] = result["chamfer_mean_mm"] / LENGTH
        if diagnostics:
            _capture(result, "local_surface", lambda: _local_diagnostics(reference, candidate, a, b))
    except Exception as exc:
        result["metric_errors"].append(_error("chamfer_relative", exc, True))
    result["checks"] = {name: metric_check(name, result[name]) for name in THRESHOLDS}

    # Cut is a conditional reliability check on a failing Common measurement,
    # never a fourth acceptance metric. An in-range passing IoU needs no Cut.
    if result["checks"]["volume_iou"] == "fail" or iou_roundoff:
        difference = _capture(result, "symmetric_difference_volume_mm3",
            lambda: symmetric_difference_volume(reference, candidate), required=True)
        invalid = None
        if difference is None:
            invalid = "failed IoU could not be independently verified by Cut"
        else:
            result["symmetric_difference_relative"] = difference / vr
            slack = max(1e-9, (vr + vc) * 1e-8)
            implied = (vr + vc) * (1 - iou) / (1 + iou)
            if difference > vr + vc + slack or difference + slack < abs(vr - vc):
                invalid = "difference is inconsistent with input volumes"
            elif abs(difference - implied) > max(1e-8, (vr + vc) * 1e-7):
                invalid = "Cut and Common disagree on occupied-space difference"
            elif iou <= 1e-12 and difference >= (vr + vc) * (1 - 1e-8):
                try:
                    witness = _shared_interior_witness(reference, candidate)
                    if witness is not None:
                        result["shared_interior_witness_normalized_mm"] = witness
                        invalid = "claimed disjoint shapes have a shared interior point"
                except Exception as exc:
                    invalid = f"disjointness reliability check failed: {type(exc).__name__}: {exc}"
        if invalid:
            result["checks"]["volume_iou"] = "error"
            result["metric_errors"].append(_error("boolean_consistency", GeometryMetricsError(invalid), True))
        elif iou_roundoff:
            # The physical interval [raw-eps, 1] lies wholly above the IoU
            # threshold, and independent Cut agrees. Never clamp the raw value.
            result["checks"]["volume_iou"] = "pass"
            result["diagnostics"].append("volume_iou_roundoff_verified")
            result["volume_iou_roundoff_tolerance"] = 1e-8

    if diagnostics:
        ar = _capture(result, "reference_area_mm2", lambda: area(reference))
        ac = _capture(result, "candidate_area_mm2", lambda: area(candidate))
        if ar is not None and ac is not None and ar > 0:
            result["area_relative_error"] = abs(ac - ar) / ar
        result["bbox_max_relative_error"] = _bbox_error(bbox(candidate), bbox(reference))
        for name in ("area_relative_error", "bbox_max_relative_error"):
            if result[name] is not None and result[name] > 1e-3:
                result["diagnostics"].append(name + "_mismatch")
        if (result.get("local_surface") or {}).get("relative_p95_max", 0) > .01:
            result["warnings"].append("local_surface_deviation")
    return result

