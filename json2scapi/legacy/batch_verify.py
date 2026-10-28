"""Batch-convert DeepCAD JSON files and verify the generated FTC models.

Usage:
    python batch_verify.py --cad-dir F:\\AI_CAD\\datasets\\DeepCAD\\cad_json\\0000 --limit 10

Default output:
    out/batches/0000-first10/
        batch_summary.json
        batch_results.csv
        00000007/ ...

Success statuses:
    accepted          independent STEP comparison passed
    self_consistent   generated baseline and candidate agree

Use --require-independent to count only `accepted` as success.
Use --cd-iou to add volume IoU and Chamfer Distance from STEP files.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from json2scapi.adapters.deepcad import convert  # noqa: E402
from json2scapi.codegen.api import generate  # noqa: E402
from json2scapi.legacy.verify_deepcad import verify  # noqa: E402
from json2scapi.runtime.output_layout import batch_dir  # noqa: E402


SUCCESS_STATUSES = frozenset({"accepted", "self_consistent"})
CSV_FIELDS = (
    "stem", "status", "stage", "success", "reference_kind", "reasons", "warnings",
    "unsupported_count", "volume_error", "area_error", "bbox_error",
    "source_bbox_error", "symmetric_difference_error", "volume_iou",
    "chamfer_distance_mm2", "chamfer_relative", "cd_iou_passed", "ir", "ftc", "verify",
)


def _is_success(status: str, require_independent: bool) -> bool:
    if require_independent:
        return status == "accepted"
    return status in SUCCESS_STATUSES


def _reference_step(reference_dir: Path | None, stem: str) -> Path | None:
    if reference_dir is None:
        return None
    for name in (f"{stem}.step", f"{stem}.reference.step"):
        candidate = reference_dir / name
        if candidate.is_file():
            return candidate
    return None


def _write_csv(path: Path, results: Sequence[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for entry in results:
            comparison = entry.get("comparison") or {}
            paths = entry.get("paths") or {}
            writer.writerow({
                "stem": entry["stem"],
                "status": entry["status"],
                "stage": entry.get("stage", ""),
                "success": str(entry["success"]).lower(),
                "reference_kind": entry.get("reference_kind", ""),
                "reasons": " | ".join(entry.get("reasons", [])),
                "warnings": " | ".join(entry.get("warnings", [])),
                "unsupported_count": entry.get("unsupported_count", 0),
                "volume_error": comparison.get("volume_error"),
                "area_error": comparison.get("area_error"),
                "bbox_error": comparison.get("bbox_error"),
                "source_bbox_error": comparison.get("source_bbox_error"),
                "symmetric_difference_error": comparison.get("symmetric_difference_error"),
                "volume_iou": comparison.get("volume_iou"),
                "chamfer_distance_mm2": comparison.get("chamfer_distance_mm2"),
                "chamfer_relative": comparison.get("chamfer_relative"),
                "cd_iou_passed": comparison.get("cd_iou_passed"),
                "ir": paths.get("ir", ""),
                "ftc": paths.get("ftc", ""),
                "verify": paths.get("verify", ""),
            })


def run_batch(
    cad_dir: Path,
    out_dir: Path | None = None,
    *,
    limit: int | None = 10,
    pattern: str = "*.json",
    name: str | None = None,
    reference_step_dir: Path | None = None,
    require_independent: bool = False,
    compute_cd_iou: bool = False,
    cd_samples: int = 8192,
    cd_seed: int = 0,
    cd_deflection_mm: float = 0.1,
    cd_relative_tolerance: float = 0.01,
    cd_iou_min: float = 0.999999,
) -> dict[str, Any]:
    sources = sorted(cad_dir.glob(pattern))
    if limit is not None:
        sources = sources[:limit]
    if out_dir is None:
        range_name = f"first{limit}" if limit is not None else "all"
        batch_name = name or f"{cad_dir.name}-{range_name}"
        out_dir = batch_dir(batch_name)
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    results: list[dict[str, Any]] = []

    for index, source in enumerate(sources, start=1):
        stem = source.stem
        sample_dir = out_dir / stem
        sample_dir.mkdir(parents=True, exist_ok=True)
        reference_step = _reference_step(reference_step_dir, stem)
        entry: dict[str, Any] = {
            "stem": stem,
            "source": str(source),
            "reference_step": str(reference_step) if reference_step else None,
        }
        stage = "input"
        paths: dict[str, str] = {}
        unsupported_count = 0
        try:
            payload = json.loads(source.read_text(encoding="utf-8", errors="replace"))
            stage = "adapter"
            ir = convert(payload, str(source))
            unsupported_count = len(ir.get("unsupported", []))
            ir_path = sample_dir / "model.ir.json"
            ir_path.write_text(json.dumps(ir, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            paths["ir"] = str(ir_path)
            stage = "emitter"
            ftc_path = sample_dir / "model.ftc.py"
            ftc_path.write_text(generate(ir, stem), encoding="utf-8")
            paths["ftc"] = str(ftc_path)
            stage = "verification"
            report = verify(
                source, ftc_path, sample_dir, stem=stem,
                reference_step=reference_step,
                compute_cd_iou=compute_cd_iou,
                cd_samples=cd_samples,
                cd_seed=cd_seed,
                cd_deflection_mm=cd_deflection_mm,
                cd_relative_tolerance=cd_relative_tolerance,
                cd_iou_min=cd_iou_min,
            )
            entry.update(
                status=report["quality"],
                stage="complete",
                reference_kind=report.get("reference_kind", ""),
                reasons=report.get("reasons", []),
                warnings=report.get("warnings", []),
                unsupported_count=unsupported_count,
                comparison=report.get("comparison", {}),
                paths={**paths, "verify": str(sample_dir / "verify.json")},
            )
        except Exception as error:  # noqa: BLE001 - batch must keep going
            entry.update(
                status=f"{stage}_error",
                stage=stage,
                reference_kind="",
                reasons=[f"{type(error).__name__}: {error}"],
                warnings=[],
                unsupported_count=unsupported_count,
                comparison={},
                paths=paths,
            )
        entry["success"] = _is_success(entry["status"], require_independent)
        results.append(entry)
        outcome = "OK" if entry["success"] else "FAIL"
        print(f"[{index}/{len(sources)}] {stem}: {entry['status']} {outcome}")
        for reason in entry.get("reasons", []):
            print(f"  - {reason}")
        for warning in entry.get("warnings", []):
            print(f"  ! {warning}")

    counts = Counter(entry["status"] for entry in results)
    failures = [
        {"stem": entry["stem"], "status": entry["status"], "reasons": entry.get("reasons", [])}
        for entry in results if not entry["success"]
    ]
    success_count = sum(1 for entry in results if entry["success"])
    failure_count = len(results) - success_count
    cd_iou_evaluated = sum(
        1 for entry in results
        if (entry.get("comparison") or {}).get("cd_iou_passed") is not None
    )
    cd_iou_passed = sum(
        1 for entry in results
        if (entry.get("comparison") or {}).get("cd_iou_passed") is True
    )
    summary = {
        "cad_dir": str(cad_dir),
        "out_dir": str(out_dir),
        "batch": out_dir.name,
        "total": len(results),
        "success_count": success_count,
        "failure_count": failure_count,
        "success_rate": (success_count / len(results)) if results else 0.0,
        "require_independent": require_independent,
        "cd_iou_enabled": compute_cd_iou,
        "cd_iou_evaluated": cd_iou_evaluated,
        "cd_iou_passed": cd_iou_passed,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "counts": dict(sorted(counts.items())),
        "status_counts": dict(sorted(counts.items())),
        "failures": failures,
        "results": results,
    }
    (out_dir / "batch_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_csv(out_dir / "batch_results.csv", results)

    print("")
    print("=== MVP batch summary ===")
    print(
        f"total={len(results)} success={success_count} failure={failure_count} "
        f"rate={summary['success_rate']:.1%} elapsed={summary['elapsed_seconds']}s"
    )
    if compute_cd_iou:
        print(f"CD/IoU: evaluated={cd_iou_evaluated} passed={cd_iou_passed}")
    print("by status: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    if failures:
        print("failures:")
        for failure in failures:
            print(f"  - {failure['stem']}: {failure['status']}")
            for reason in failure["reasons"]:
                print(f"    {reason}")
    print(f"summary: {out_dir / 'batch_summary.json'}")
    print(f"csv:     {out_dir / 'batch_results.csv'}")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cad-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path,
                        help="batch directory (default: out/batches/<dataset>-firstN)")
    parser.add_argument("--name", help="batch directory name when --out is omitted")
    parser.add_argument("--limit", type=int, default=10,
                        help="number of samples for the MVP run (default: 10)")
    parser.add_argument("--all", action="store_true", help="run all samples instead of --limit")
    parser.add_argument("--pattern", default="*.json")
    parser.add_argument("--reference-step-dir", type=Path,
                        help="directory containing <stem>.step independent references")
    parser.add_argument("--require-independent", action="store_true",
                        help="count only accepted independent comparisons as success")
    parser.add_argument("--cd-iou", action="store_true",
                        help="also compute volume IoU and Chamfer Distance")
    parser.add_argument("--cd-samples", type=int, default=8192)
    parser.add_argument("--cd-seed", type=int, default=0)
    parser.add_argument("--cd-deflection-mm", type=float, default=0.1)
    parser.add_argument("--cd-relative-tolerance", type=float, default=0.01)
    parser.add_argument("--cd-iou-min", type=float, default=0.999999)
    args = parser.parse_args(argv)
    if not args.all and args.limit < 1:
        parser.error("--limit must be at least 1")
    summary = run_batch(
        args.cad_dir,
        args.out,
        limit=None if args.all else args.limit,
        pattern=args.pattern,
        name=args.name,
        reference_step_dir=args.reference_step_dir,
        require_independent=args.require_independent,
        compute_cd_iou=args.cd_iou,
        cd_samples=args.cd_samples,
        cd_seed=args.cd_seed,
        cd_deflection_mm=args.cd_deflection_mm,
        cd_relative_tolerance=args.cd_relative_tolerance,
        cd_iou_min=args.cd_iou_min,
    )
    return 0 if summary["failure_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
