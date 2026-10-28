"""Check an FTC candidate against a generated baseline or independent STEP.

Generated baseline (self-consistency only):
    cad_json -> deepcad_to_ir -> ir2code -> execute -> STEP

Candidate path:
    supplied .ftc.py -> execute -> STEP/boolean difference against baseline

Usage:
    python verify_deepcad.py \\
        --deepcad-json cad_json/0000/00000007.json \\
        --candidate-ftc out/cases/00000007/model.ftc.py

Default output:
    out/cases/<stem>/  (gt.*, candidate.result.json, verify.json)

Optional:
    --reference-step official.step   # required for independent acceptance
    --cd-iou                         # compute volume IoU and Chamfer Distance
    --cd-samples 8192                # CD sampling; low values are noisy
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from json2scapi.adapters.deepcad import convert
from json2scapi.codegen.api import generate
from json2scapi.runtime.output_layout import case_dir
from json2scapi.runtime.paths import WORKSPACE_DIR

VOLUME_RTOL = 1e-3
BBOX_RTOL = 1e-3
DIFFERENCE_RTOL = 1e-3


def _worker_path() -> Path:
    return WORKSPACE_DIR / "research" / "build123dftc" / "b3ftc" / "_validate_worker.py"


def _run_worker(
    ftc: Path,
    output: Path,
    staging: Path,
    *,
    export_step: Path | None = None,
    reference_step: Path | None = None,
    quiet: bool = False,
) -> dict[str, Any]:
    worker = _worker_path()
    if not worker.is_file():
        raise FileNotFoundError(f"validation worker not found: {worker}")
    staging.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, str(worker),
        "--ftc", str(ftc),
        "--output", str(output),
        "--staging-dir", str(staging),
    ]
    if export_step is not None:
        command.extend(["--export-step", str(export_step)])
    if reference_step is not None:
        command.extend(["--reference-step", str(reference_step)])
    completed = subprocess.run(
        command, check=False, cwd=ftc.parent,
        stdout=subprocess.DEVNULL if quiet else None,
        stderr=subprocess.DEVNULL if quiet else None,
    )
    if not output.is_file():
        raise RuntimeError(f"worker produced no result for {ftc} (exit {completed.returncode})")
    result = json.loads(output.read_text(encoding="utf-8", errors="replace"))
    if completed.returncode != 0:
        raise RuntimeError(
            f"worker failed for {ftc} (exit {completed.returncode}; "
            f"status={result.get('status')}; error={result.get('error')})"
        )
    return result


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _bbox(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 6
        and all(_finite(item) for item in value)
        and all(value[index] <= value[index + 3] for index in range(3))
    )


def _relative_error(actual: float, expected: float) -> float:
    if abs(expected) < 1e-15:
        return abs(actual - expected)
    return abs(actual - expected) / abs(expected)


def _bbox_error(actual: Sequence[float], expected: Sequence[float]) -> float:
    spans = [max(float(expected[index + 3]) - float(expected[index]), 1.0) for index in range(3)]
    return max(abs(float(actual[index]) - float(expected[index])) / spans[index % 3] for index in range(6))


def _source_bbox(payload: Mapping[str, Any]) -> list[float] | None:
    bbox = payload.get("properties", {}).get("bounding_box")
    if not bbox:
        return None
    minimum = bbox.get("min_point") or {}
    maximum = bbox.get("max_point") or {}
    values = [
        float(minimum.get("x", 0.0)), float(minimum.get("y", 0.0)), float(minimum.get("z", 0.0)),
        float(maximum.get("x", 0.0)), float(maximum.get("y", 0.0)), float(maximum.get("z", 0.0)),
    ]
    return [value * 1000.0 for value in values]


def verify(
    source: Path,
    candidate_ftc: Path,
    out_dir: Path | None = None,
    *,
    stem: str | None = None,
    reference_step: Path | None = None,
    compute_cd_iou: bool = False,
    cd_samples: int = 8192,
    cd_seed: int = 0,
    cd_deflection_mm: float = 0.1,
    cd_relative_tolerance: float = 0.01,
    cd_iou_min: float = 0.999999,
) -> dict[str, Any]:
    stem = stem or source.stem
    source = source.resolve()
    candidate_ftc = candidate_ftc.resolve()
    reference_step = reference_step.resolve() if reference_step is not None else None
    out_dir = case_dir(stem, out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = json.loads(source.read_text(encoding="utf-8", errors="replace"))
    source_bbox = _source_bbox(payload)

    ir = convert(payload, str(source))
    ir_path = out_dir / "gt.ir.json"
    ir_path.write_text(json.dumps(ir, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    gt_ftc = out_dir / "gt.ftc.py"
    gt_ftc.write_text(generate(ir, f"{stem}_gt"), encoding="utf-8")

    gt_step = out_dir / "gt.step"
    gt_result = _run_worker(
        gt_ftc, out_dir / "gt.result.json",
        out_dir / ".work" / "gt", export_step=gt_step,
    )
    if reference_step is not None:
        # Check the generated GT against the independent reference too.
        gt_reference = _run_worker(
            gt_ftc, out_dir / "gt.reference.json",
            out_dir / ".work" / "gt_reference", reference_step=reference_step,
        )
    else:
        gt_reference = None

    comparison_step = reference_step or gt_step
    candidate_step = out_dir / "candidate.step" if compute_cd_iou else None
    candidate_result = _run_worker(
        candidate_ftc, out_dir / "candidate.result.json",
        out_dir / ".work" / "candidate",
        export_step=candidate_step, reference_step=comparison_step,
    )

    cd_iou = None
    cd_iou_error = None
    if compute_cd_iou and comparison_step is not None and candidate_step is not None:
        try:
            from json2scapi.evaluation.metrics import compare_step_files

            cd_iou = compare_step_files(
                comparison_step,
                candidate_step,
                volume_relative_tolerance=VOLUME_RTOL,
                bbox_relative_tolerance=BBOX_RTOL,
                iou_min=cd_iou_min,
                cd_relative_max=cd_relative_tolerance,
                cd_samples=cd_samples,
                cd_seed=cd_seed,
                cd_deflection_mm=cd_deflection_mm,
            )
        except Exception as error:  # noqa: BLE001 - report, do not abort the whole case
            cd_iou_error = f"{type(error).__name__}: {error}"

    reasons: list[str] = []
    warnings: list[str] = []
    if compute_cd_iou and reference_step is not None and cd_samples < 4096:
        warnings.append(
            f"cd_sampling_low: {cd_samples} points can cause false chamfer_mismatch"
        )
    unsupported = list(ir.get("unsupported", []))
    if unsupported:
        reasons.append("unsupported_source_features")
    if gt_result.get("status") != "ok":
        reasons.append("gt_build_failed")
    if gt_result.get("is_valid") is not True:
        reasons.append("gt_invalid")
    if candidate_result.get("status") != "ok":
        reasons.append("candidate_build_failed")
    if candidate_result.get("is_valid") is not True:
        reasons.append("candidate_invalid")
    if not isinstance(candidate_result.get("body_count"), int) or candidate_result.get("body_count", 0) < 1:
        reasons.append("candidate_empty")

    gt_volume = gt_result.get("volume")
    candidate_volume = candidate_result.get("volume")
    gt_bbox = gt_result.get("bbox")
    candidate_bbox = candidate_result.get("bbox")
    gt_area = gt_result.get("area")
    candidate_area = candidate_result.get("area")
    volume_error = area_error = bbox_error = None
    if _finite(gt_volume) and _finite(candidate_volume):
        volume_error = _relative_error(float(candidate_volume), float(gt_volume))
        if volume_error > VOLUME_RTOL:
            reasons.append("volume_mismatch")
    else:
        reasons.append("missing_volume")
    if _finite(gt_area) and _finite(candidate_area):
        area_error = _relative_error(float(candidate_area), float(gt_area))
        if area_error > VOLUME_RTOL:
            reasons.append("area_mismatch")
    if _bbox(gt_bbox) and _bbox(candidate_bbox):
        bbox_error = _bbox_error(candidate_bbox, gt_bbox)
        if bbox_error > BBOX_RTOL:
            reasons.append("bbox_mismatch")
    else:
        reasons.append("missing_bbox")
    if gt_result.get("solid_count") != candidate_result.get("solid_count"):
        reasons.append("solid_count_mismatch")

    if compute_cd_iou:
        if cd_iou_error is not None:
            if reference_step is not None:
                reasons.append("cd_iou_error")
            else:
                warnings.append(f"cd_iou_unavailable: {cd_iou_error}")
        elif cd_iou is not None:
            if not cd_iou.get("passed"):
                if cd_iou.get("volume_iou", 1.0) < cd_iou_min:
                    reasons.append("volume_iou_mismatch")
                if cd_iou.get("chamfer_relative", 0.0) > cd_relative_tolerance:
                    reasons.append("chamfer_mismatch")
                if cd_iou.get("volume_relative_error", 0.0) > VOLUME_RTOL:
                    reasons.append("cd_iou_volume_mismatch")
                if cd_iou.get("area_relative_error", 0.0) > VOLUME_RTOL:
                    reasons.append("cd_iou_area_mismatch")
                if cd_iou.get("bbox_max_relative_error", 0.0) > BBOX_RTOL:
                    reasons.append("cd_iou_bbox_mismatch")
                if cd_iou.get("reference_solid_count") != cd_iou.get("candidate_solid_count"):
                    reasons.append("cd_iou_solid_count_mismatch")
                if not reasons:
                    reasons.append("cd_iou_mismatch")

    source_bbox_error = None
    if source_bbox is not None and _bbox(gt_bbox):
        source_bbox_error = _bbox_error(gt_bbox, source_bbox)
        if source_bbox_error > BBOX_RTOL:
            if unsupported:
                warnings.append("source_bbox_incomplete: unsupported features affect source bbox")
            else:
                reasons.append("gt_vs_source_bbox_mismatch")

    difference_error = None
    if comparison_step is not None:
        if candidate_result.get("reference_status") != "ok":
            reference_error = str(candidate_result.get("reference_error", ""))
            boolean_unavailable = any(
                marker in reference_error for marker in (
                    "contains no valid solids",
                    "symmetric difference produced invalid topology",
                    "symmetric difference failed",
                )
            )
            metrics_match = not any(
                reason in reasons for reason in (
                    "volume_mismatch", "area_mismatch", "bbox_mismatch", "solid_count_mismatch",
                )
            )
            if reference_step is None and boolean_unavailable and metrics_match:
                warnings.append("reference_boolean_unavailable: metrics match but STEP Boolean check failed")
            else:
                reasons.append("reference_error")
        else:
            difference = candidate_result.get("symmetric_difference_volume")
            reference_volume = candidate_result.get("reference_volume")
            if _finite(difference) and _finite(reference_volume) and float(reference_volume) > 0:
                difference_error = abs(float(difference)) / float(reference_volume)
                if difference_error > DIFFERENCE_RTOL:
                    reasons.append("symmetric_difference_mismatch")
            else:
                reasons.append("missing_reference_metrics")

    if gt_reference is not None:
        if gt_reference.get("reference_status") != "ok":
            reasons.append("gt_reference_error")
        else:
            gt_diff = gt_reference.get("symmetric_difference_volume")
            gt_ref_volume = gt_reference.get("reference_volume")
            if not (_finite(gt_diff) and _finite(gt_ref_volume) and float(gt_ref_volume) > 0):
                reasons.append("gt_missing_reference_metrics")
            elif abs(float(gt_diff)) / float(gt_ref_volume) > DIFFERENCE_RTOL:
                reasons.append("gt_reference_mismatch")

    report = {
        "stem": stem,
        "quality": "rejected" if reasons else ("accepted" if reference_step is not None else "self_consistent"),
        "reference_kind": "independent_step" if reference_step is not None else "generated_from_same_ir",
        "reasons": reasons,
        "warnings": warnings,
        "unsupported": unsupported,
        "source": str(source),
        "candidate_ftc": str(candidate_ftc),
        "paths": {
            "gt_ir": str(ir_path),
            "gt_ftc": str(gt_ftc),
            "gt_step": str(gt_step),
            "candidate_step": str(candidate_step) if candidate_step else None,
            "reference_step": str(reference_step) if reference_step else None,
        },
        "source_bbox_mm": source_bbox,
        "gt": gt_result,
        "candidate": candidate_result,
        "gt_reference": gt_reference,
        "cd_iou": cd_iou,
        "cd_iou_error": cd_iou_error,
        "comparison": {
            "volume_error": volume_error,
            "area_error": area_error,
            "bbox_error": bbox_error,
            "source_bbox_error": source_bbox_error,
            "symmetric_difference_error": difference_error,
            "volume_iou": cd_iou.get("volume_iou") if cd_iou else None,
            "chamfer_distance_mm2": cd_iou.get("chamfer_distance_mm2") if cd_iou else None,
            "chamfer_relative": cd_iou.get("chamfer_relative") if cd_iou else None,
            "cd_iou_passed": cd_iou.get("passed") if cd_iou else None,
        },
    }
    (out_dir / "verify.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deepcad-json", type=Path, required=True)
    parser.add_argument("--candidate-ftc", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="case directory (default: out/cases/<stem>)")
    parser.add_argument("--stem")
    parser.add_argument("--reference-step", type=Path)
    parser.add_argument("--cd-iou", action="store_true",
                        help="also compute volume IoU and Chamfer Distance from STEP files")
    parser.add_argument("--cd-samples", type=int, default=8192)
    parser.add_argument("--cd-seed", type=int, default=0)
    parser.add_argument("--cd-deflection-mm", type=float, default=0.1)
    parser.add_argument("--cd-relative-tolerance", type=float, default=0.01)
    parser.add_argument("--cd-iou-min", type=float, default=0.999999)
    args = parser.parse_args(argv)
    try:
        report = verify(
            args.deepcad_json, args.candidate_ftc, args.out,
            stem=args.stem, reference_step=args.reference_step,
            compute_cd_iou=args.cd_iou,
            cd_samples=args.cd_samples,
            cd_seed=args.cd_seed,
            cd_deflection_mm=args.cd_deflection_mm,
            cd_relative_tolerance=args.cd_relative_tolerance,
            cd_iou_min=args.cd_iou_min,
        )
    except Exception as error:
        print(f"{args.deepcad_json}: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(f"{report['stem']}: {report['quality']}")
    if report["reasons"]:
        print("reasons: " + ", ".join(report["reasons"]))
    print(json.dumps(report["comparison"], indent=2))
    return 0 if report["quality"] in {"accepted", "self_consistent"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
