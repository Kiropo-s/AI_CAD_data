"""Versioned similarity-invariant evaluation; the legacy metric API is unchanged."""
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

from json2scapi.evaluation.metrics import (GeometryMetricsError, _bbox_error, _sample_surface,
                              area, bbox, read_step, solid_count,
                              symmetric_difference_volume as _difference, volume_iou as _iou)
from json2scapi.evaluation.policy import (CD_PROTOCOL, EVALUATION_VERSION, THRESHOLDS,
                               CHECK_DIRECTIONS, decide, metric_check)
from json2scapi.evaluation.registration import (DEFLECTION, LENGTH, candidates, distances,
                                     normalize, occupied_space, properties,
                                     solids, transform_shape)


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


def evaluate_shapes(reference: Any, candidate: Any, *, cd_samples: int = 8192,
                    diagnostics: bool = True) -> dict:
    if cd_samples < 8192:
        raise ValueError("shape acceptance requires at least 8192 samples per shape")
    report = {"evaluation_version": EVALUATION_VERSION, "equivalence_mode": "similarity_shape",
              "measurement_units": "normalized_mm",
              "volume_method": "BRepGProp.VolumeProperties; conditional Cut/Common reliability checks",
              "inertia_degeneracy_relative_threshold": 1e-4,
              "thresholds": dict(THRESHOLDS), "threshold_directions": dict(CHECK_DIRECTIONS),
              "metric_errors": [], "warnings": [], "diagnostics": [], "checks": {},
              "registration": {"status": "not_run"},
              "chamfer_sampling": {"protocol": CD_PROTOCOL, "points_per_shape": cd_samples,
                  "seed": 0, "registration_seed": 17, "linear_deflection_mm": DEFLECTION,
                  "normalization_length_mm": LENGTH, "angular_deflection_rad": .2},
              "versions": {"OCP": OCP.__version__, "numpy": np.__version__, "scipy": scipy.__version__}}
    report.update({key: None for key in METRIC_KEYS})
    try:
        report["raw"] = {"reference": _raw(reference), "candidate": _raw(candidate)}
        report["reference_solid_count"] = solid_count(reference)
        report["candidate_solid_count"] = solid_count(candidate)
        if report["reference_solid_count"] != report["candidate_solid_count"]:
            report["diagnostics"].append("solid_count_mismatch")
        first, second = occupied_space(reference), occupied_space(candidate)
        report["occupied_solid_counts"] = {"reference": solid_count(first), "candidate": solid_count(second)}
        first, info_a, axes_a = normalize(first)
        second, info_b, axes_b = normalize(second)
        report["normalization"] = {"reference": info_a, "candidate": info_b}
        if DEFLECTION < 10 * Precision.Confusion_s():
            raise GeometryMetricsError("normalization mesh resolution below kernel precision")
        ambiguous = info_a["principal_axes_degenerate"] or info_b["principal_axes_degenerate"]
        poses = candidates(first, second, axes_a, axes_b)
        report["registration"] = {"status": "searching", "principal_axes_degenerate": ambiguous,
                                  "initial_count": poses[0]["initial_count"], "retained_initial_count": 8, "attempts": []}
        attempts = []
        for pose in poses:
            matrix = np.eye(4)
            matrix[:3, :3] = pose["rotation"]
            transformed = transform_shape(second, matrix)
            measured = _pose_metrics(first, transformed, cd_samples)
            passed = all(measured["checks"].get(name) == "pass" for name in THRESHOLDS)
            report["registration"]["attempts"].append({"initial": pose["initial"], "refined": pose["refined"],
                "registration_score_mm": pose["score"], "checks": measured["checks"],
                "volume_iou": measured["volume_iou"],
                "volume_relative_error": measured["volume_relative_error"],
                "chamfer_relative": measured["chamfer_relative"],
                "symmetric_difference_relative": measured["symmetric_difference_relative"],
                "metric_errors": measured["metric_errors"]})
            attempts.append((measured, matrix, transformed, pose))
            if passed or measured["checks"].get("volume_relative_error") == "fail":
                break
        # Try every retained pose for acceptance. If none passes, report the
        # best registration evidence, including its required measurement errors.
        selected = min(attempts, key=lambda item: (
            not all(item[0]["checks"].get(k) == "pass" for k in THRESHOLDS),
            item[3]["score"]))
        measured, rotation, transformed, pose = selected
        if diagnostics:
            measured = _pose_metrics(first, transformed, cd_samples, diagnostics=True)
        report["metric_errors"].extend(measured.pop("metric_errors"))
        report["warnings"].extend(measured.pop("warnings"))
        report["diagnostics"].extend(measured.pop("diagnostics"))
        report.update(measured)
        hard_pass = all(report["checks"].get(k) == "pass" for k in THRESHOLDS)
        # Registration cannot manufacture a mismatch from unresolved symmetry.
        report["registration"].update(status="resolved" if hard_pass or not ambiguous else "unresolved",
            selected_initial=pose["initial"], selected_refined=pose["refined"],
            rotation_matrix=rotation[:3, :3].tolist(), determinant=float(np.linalg.det(rotation[:3, :3])))
        na, nb = np.array(info_a["matrix"]), np.array(info_b["matrix"])
        original_transform = np.linalg.inv(na) @ rotation @ nb
        report["transform"] = {"candidate_to_reference": original_transform.tolist(),
            "reference_to_candidate": np.linalg.inv(original_transform).tolist(),
            "scale": info_b["scale_to_normalized"] / info_a["scale_to_normalized"],
            "translation_mm": original_transform[:3, 3].tolist()}
    except Exception as exc:
        report["metric_errors"].append(_error("shape_evaluation", exc, True))
    report["decision"] = decide(report)
    return report


def evaluate_step_files(reference_step: Path, candidate_step: Path, **kwargs) -> dict:
    result = evaluate_shapes(read_step(reference_step), read_step(candidate_step), **kwargs)
    result.update(reference_step=str(reference_step), candidate_step=str(candidate_step))
    return result
