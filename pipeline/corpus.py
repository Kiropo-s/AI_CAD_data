"""Build readable-only FTC examples with independent DeepCAD references.

Only cases with a valid readable FTC build and a passing independently
reconstructed STEP are exported to train_ready.jsonl. All other cases retain
their artifacts and diagnostic reports for inspection.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from json2scapi.adapters.deepcad import convert
from json2scapi.codegen.api import generate_readable_with_metadata
from json2scapi.runtime.output_layout import corpus_dir
from json2scapi.runtime.worker import run_worker as _run_worker
from json2scapi.ir.validation import validate_ir
from json2scapi.evaluation.policy import EVALUATION_VERSION, CD_PROTOCOL

from json2scapi.runtime.paths import PACKAGE_DIR, REFERENCE_SCRIPT


def _save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _source_bbox(payload: dict[str, Any]) -> list[float] | None:
    box = payload.get("properties", {}).get("bounding_box")
    if not box:
        return None
    return [1000.0 * float(box[key][axis])
            for key in ("min_point", "max_point") for axis in "xyz"]


def _bbox_relative_error(actual: list[float], expected: list[float]) -> float:
    spans = [max(expected[index + 3] - expected[index], 1.0) for index in range(3)]
    return max(abs(actual[index] - expected[index]) / spans[index % 3] for index in range(6))


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:d}:{seconds:02d}"


def _progress(index: int, total: int, stem: str, status: str, stage: str,
              started: float, width: int = 28) -> None:
    elapsed = time.monotonic() - started
    eta = elapsed / index * (total - index) if index else 0.0
    percent = (index / total * 100.0) if total else 100.0
    detail = (
        f"{index}/{total} {percent:5.1f}% "
        f"elapsed={_format_duration(elapsed)} eta={_format_duration(eta)} "
        f"{stem}: {status} ({stage})"
    )
    if sys.stdout.isatty():
        filled = int(width * index / total) if total else width
        bar = "#" * filled + "-" * (width - filled)
        print(f"\r[{bar}] {detail}", end="", flush=True)
        if index == total:
            print()
    else:
        print(f"[{detail}]", flush=True)


def _append_result(path: Path, result: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        handle.flush()


def _write_analysis_csv(path: Path, results: list[dict[str, Any]]) -> None:
    fields = [
        "stem", "status", "stage", "reference_evaluation", "reasons", "source", "warnings", "diagnostics", "metric_errors",
        "readable_version", "lines", "function_count", "build_lines",
        "max_function_lines", "feature_count", "profile_count", "polyline_helper",
        "volume_iou", "volume_relative_error", "area_relative_error",
        "bbox_max_relative_error", "symmetric_difference_relative",
        "chamfer_relative", "source_bbox_relative_error",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in results:
            metrics = item.get("metrics") or {}
            comparison = metrics.get("reference_vs_readable") or {}
            structure = item.get("code_structure") or {}
            writer.writerow({
                "stem": item.get("stem"),
                "status": item.get("status"),
                "stage": item.get("stage"),
                "reference_evaluation": item.get("reference_evaluation"),
                "reasons": " | ".join(item.get("reasons") or []),
                "source": (item.get("paths") or {}).get("source"),
                "warnings": " | ".join(item.get("warnings", [])),
                "diagnostics": " | ".join(item.get("diagnostics", [])),
                "metric_errors": json.dumps(item.get("metric_errors", []), ensure_ascii=False),
                "readable_version": item.get("readable_version"),
                "lines": structure.get("lines"),
                "function_count": structure.get("function_count"),
                "build_lines": structure.get("build_lines"),
                "max_function_lines": structure.get("max_function_lines"),
                "feature_count": structure.get("feature_count"),
                "profile_count": structure.get("profile_count"),
                "polyline_helper": structure.get("polyline_helper"),
                "volume_iou": comparison.get("volume_iou"),
                "volume_relative_error": comparison.get("volume_relative_error"),
                "area_relative_error": comparison.get("area_relative_error"),
                "bbox_max_relative_error": comparison.get("bbox_max_relative_error"),
                "symmetric_difference_relative": comparison.get("symmetric_difference_relative"),
                "chamfer_relative": comparison.get("chamfer_relative"),
                "source_bbox_relative_error": metrics.get("source_bbox_relative_error"),
            })


def _run_case(source: Path, out_dir: Path, *, cd_samples: int = 8192) -> dict[str, Any]:
    from json2scapi.evaluation.similarity import evaluate_shapes
    from json2scapi.evaluation.metrics import read_step, solid_count

    if cd_samples < 8192:
        raise ValueError("training acceptance requires at least 8192 Chamfer samples")
    source, out_dir = source.resolve(), out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"source": str(source)}
    report: dict[str, Any] = {"stem": source.stem, "status": "rejected", "stage": "adapter",
                              "reference_evaluation": "not_attempted", "reasons": [], "paths": paths,
                              "metrics": {}, "warnings": [], "diagnostics": [], "metric_errors": [], "checks": {},
                              "evaluation_version": EVALUATION_VERSION, "equivalence_mode": "similarity_shape",
                              "code_style": "readable", "pipeline_version": "readable-only-v2"}

    try:
        payload = json.loads(source.read_text(encoding="utf-8", errors="replace"))
        ir = convert(payload, str(source))
        if ir.get("unsupported"):
            report["stage"] = "incomplete_ir"
            raise ValueError(f"incomplete IR: {ir['unsupported']}")
        report["checks"]["adapter"] = "pass"
        report["stage"] = "validate_ir"
        from json2scapi.ir.validation import prepare_ir
        prepared_ir, preparation = prepare_ir(ir)
        report["checks"]["validate_ir"] = "pass"
        ir_path = out_dir / "fidelity.ir.json"
        _save_json(ir_path, ir)
        paths["ir"] = str(ir_path)
        prepared_path = out_dir / "prepared.ir.json"
        _save_json(prepared_path, prepared_ir)
        paths["prepared_ir"] = str(prepared_path)
        report["preparation_version"] = preparation["version"]
        report["stage"] = "emitter"
        readable, symbols = generate_readable_with_metadata(ir, source.stem)
        report["readable_version"] = symbols["readable_version"]
        report["code_structure"] = symbols["structure"]
        readable_path = out_dir / "model.readable.ftc.py"
        readable_path.write_text(readable, encoding="utf-8")
        symbols_path = out_dir / "symbols.json"
        _save_json(symbols_path, symbols)
        paths.update(readable_code=str(readable_path),
                     symbols=str(symbols_path))
        report["stage"] = "build"
        step = out_dir / "readable.step"
        result = _run_worker(readable_path, out_dir / "readable.result.json",
                             out_dir / ".work" / "readable", export_step=step, quiet=True, cd_samples=cd_samples)
        report["build"] = result
        report["checks"].update(result.get("checks", {}))
        if result.get("status") in {"process_error", "roundtrip_measurement_error"}:
            report.update(status="provisional", stage="candidate_step", reference_evaluation="not_attempted")
            report["reasons"].append(result.get("error", result["status"]))
            return report
        if result.get("status") != "ok":
            report["stage"] = "candidate_step" if result.get("is_valid") else "build"
            raise ValueError(result.get("error", "invalid candidate"))
        if (result.get("is_valid") is not True or result.get("solid_count", 0) < 1
                or result.get("body_count", 0) < 1):
            raise ValueError("readable build contains no valid bodies")
        paths["readable_step"] = str(step)
    except Exception as error:
        report["reasons"].append(f"{type(error).__name__}: {error}")
        return report

    report["stage"] = "reference"
    reference_step = out_dir / "reference.step"
    reference_report = out_dir / "reference.json"
    try:
        completed = subprocess.run(
            [sys.executable, str(REFERENCE_SCRIPT), "--input", str(source),
             "--step", str(reference_step), "--report", str(reference_report)],
            cwd=REFERENCE_SCRIPT.parents[1], capture_output=True, text=True, check=False, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        report.update(status="provisional", reference_evaluation="reference_error")
        report["reasons"].append(f"reference process: {type(error).__name__}: {error}")
        return report
    if not reference_report.is_file():
        report.update(status="provisional", reference_evaluation="reference_error")
        report["reasons"].append(f"reference produced no report (exit {completed.returncode}): {completed.stderr[-500:]}")
        return report
    try:
        reference = json.loads(reference_report.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        report.update(status="provisional", reference_evaluation="reference_error")
        report["reasons"].append(f"invalid reference report: {error}")
        return report
    report["reference"] = reference
    paths["reference_report"] = str(reference_report)
    if reference.get("status") != "ok" or completed.returncode != 0:
        evaluation = (reference.get("status") if completed.returncode == 0 or
                      reference.get("status") != "ok" else "reference_error")
        report.update(status="provisional", reference_evaluation=evaluation or "reference_error")
        report["reasons"].append(reference.get("reason") or f"reference exit {completed.returncode}")
        return report
    paths["reference_step"] = str(reference_step)
    report["stage"] = "independent_comparison"
    try:
        reference_shape = read_step(reference_step)
        if solid_count(reference_shape) < 1:
            raise ValueError("reference STEP contains no solids")
        report["checks"]["reference_step"] = "pass"
    except Exception as error:
        report.update(status="provisional", stage="reference", reference_evaluation="reference_error")
        report["reasons"].append(f"reference STEP: {type(error).__name__}: {error}")
        return report
    try:
        candidate_shape = read_step(step)
    except Exception as error:
        report.update(status="rejected", stage="candidate_step", reference_evaluation="not_attempted")
        report["reasons"].append(f"candidate STEP: {type(error).__name__}: {error}")
        return report
    try:
        comparison = evaluate_shapes(reference_shape, candidate_shape, cd_samples=cd_samples)
        report["metrics"]["reference_vs_readable"] = comparison
        report["checks"].update(comparison["checks"])
        report["warnings"].extend(comparison["warnings"])
        report["diagnostics"].extend(comparison["diagnostics"])
        report["metric_errors"].extend(comparison["metric_errors"])
        decision = comparison["decision"]
        report.update(status=decision["status"], reference_evaluation=decision["reason"])
        if decision["status"] != "train_ready":
            report["reasons"].append(decision["reason"])
        # Source metadata is raw-world evidence; compare against the raw
        # reference bbox, never the normalized/aligned one.
        try:
            source_bbox = _source_bbox(payload)
            if source_bbox is not None:
                reference_bbox = comparison["raw"]["reference"]["bbox_mm"]
                error = _bbox_relative_error(reference_bbox, source_bbox)
                report["metrics"]["source_bbox_relative_error"] = error
                if error > 1e-3:
                    report["diagnostics"].append("source_bbox_mismatch")
        except Exception as error:
            report["metric_errors"].append({"metric": "source_bbox", "required": False,
                                            "error": f"{type(error).__name__}: {error}"})
    except Exception as error:
        report.update(status="provisional", reference_evaluation="measurement_error")
        report["reasons"].append(f"reference comparison: {type(error).__name__}: {error}")
    return report


def run_case(source: Path, out_dir: Path, *, cd_samples: int = 8192) -> dict[str, Any]:
    started = time.monotonic()
    result = _run_case(source, out_dir, cd_samples=cd_samples)
    result["elapsed_seconds"] = round(time.monotonic() - started, 6)
    result["rejection_reasons"] = list(result["reasons"]) if result["status"] == "rejected" else []
    return result


def _run_case_isolated(source: Path, out_dir: Path, *, cd_samples: int) -> dict[str, Any]:
    report_path = out_dir / "report.json"
    command = [
        sys.executable, str(PACKAGE_DIR / "build_corpus.py"),
        "--case-input", str(source.resolve()),
        "--case-out", str(out_dir),
        "--cd-samples", str(cd_samples),
    ]
    try:
        completed = subprocess.run(
            command, cwd=PACKAGE_DIR,
            capture_output=True, text=True, check=False, timeout=180,
        )
    except subprocess.TimeoutExpired:
        result = {"stem": source.stem, "status": "provisional", "stage": "process",
                  "reference_evaluation": "process_error", "reasons": ["case timeout after 180s"],
                  "paths": {"source": str(source)}, "metrics": {}, "warnings": [], "diagnostics": [], "metric_errors": [],
                  "evaluation_version": EVALUATION_VERSION, "equivalence_mode": "similarity_shape"}
        _append_result(out_dir.parent / "progress.jsonl", result)
        return result
    if report_path.is_file():
        return json.loads(report_path.read_text(encoding="utf-8", errors="replace"))
    detail = (completed.stderr or completed.stdout or "").strip()[-800:]
    return {
        "stem": source.stem,
        "status": "provisional",
        "stage": "process",
        "reference_evaluation": "process_error",
        "reasons": [f"case process exited {completed.returncode}: {detail}"],
        "paths": {"source": str(source)},
        "metrics": {},
        "code_style": "readable",
        "pipeline_version": "readable-only-v2",
        "artifacts": "unavailable",
    }


def run_corpus(sources: list[Path], out_dir: Path, *, cd_samples: int = 8192,
               log_only: bool = False, isolate: bool = False) -> dict[str, Any]:
    if cd_samples < 8192:
        raise ValueError("training acceptance requires at least 8192 Chamfer samples")
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if len({source.stem for source in sources}) != len(sources):
        raise ValueError("case IDs must be unique within a corpus")
    if ((out_dir / "manifest.json").exists() or (out_dir / "progress.jsonl").exists()
            or any((out_dir / source.stem).exists() for source in sources)):
        raise FileExistsError(f"corpus output already exists; choose a new run name: {out_dir}")
    progress_path = out_dir / "progress.jsonl"
    if not isolate:
        progress_path.write_text("", encoding="utf-8")
    total = len(sources)
    started = time.monotonic()
    if total:
        print(f"corpus: {total} cases -> {out_dir}", flush=True)
    results = []
    for index, source in enumerate(sources, 1):
        case_dir = out_dir / source.stem
        case_dir.mkdir(parents=True, exist_ok=True)
        if isolate:
            result = _run_case_isolated(source, case_dir, cd_samples=cd_samples)
        else:
            result = run_case(source, case_dir, cd_samples=cd_samples)
        if log_only:
            source_path = (result.get("paths") or {}).get("source")
            result["paths"] = {"source": source_path} if source_path else {}
            result["artifacts"] = "discarded"
            shutil.rmtree(case_dir, ignore_errors=True)
        else:
            _save_json(case_dir / "report.json", result)
        if not isolate:
            _append_result(progress_path, result)
        results.append(result)
        _progress(index, total, source.stem, result["status"], result["stage"], started)
    manifest = {"pipeline_version": "readable-only-v2", "code_style": "readable",
                "evaluation_version": EVALUATION_VERSION, "equivalence_mode": "similarity_shape",
                "cd_protocol": CD_PROTOCOL,
                "artifact_mode": "logs-only" if log_only else "full",
                "total": len(results), "counts": dict(Counter(item["status"] for item in results)),
                "results": results}
    _save_json(out_dir / "manifest.json", manifest)
    _write_analysis_csv(out_dir / "errors.csv", results)
    if log_only:
        with (out_dir / "train_ready_index.jsonl").open("w", encoding="utf-8") as handle:
            for item in results:
                if item["status"] == "train_ready":
                    handle.write(json.dumps({
                        "stem": item["stem"],
                        "source": (item.get("paths") or {}).get("source"),
                        "readable_version": item.get("readable_version"),
                        "evaluation_version": EVALUATION_VERSION, "equivalence_mode": "similarity_shape",
                        "warnings": item.get("warnings", []),
                        "diagnostics": item.get("diagnostics", []),
                        "code_structure": item.get("code_structure"),
                    }, ensure_ascii=False) + "\n")
        print(f"log-only: artifacts discarded; analysis={out_dir / 'errors.csv'}", flush=True)
    else:
        with (out_dir / "train_ready.jsonl").open("w", encoding="utf-8") as handle:
            for item in results:
                if item["status"] == "train_ready":
                    code = Path(item["paths"]["readable_code"]).read_text(encoding="utf-8")
                    handle.write(json.dumps({"stem": item["stem"], "source": item["paths"]["source"],
                                             "code": code,
                                             "evaluation_version": EVALUATION_VERSION,
                                             "equivalence_mode": "similarity_shape", "cd_protocol": CD_PROTOCOL,
                                             "warnings": item.get("warnings", []),
                                             "diagnostics": item.get("diagnostics", []),
                                             "symbols": item["paths"]["symbols"],
                                             "readable_version": item.get("readable_version"), "code_style": "readable",
                                             "report": str(out_dir / item["stem"] / "report.json")},
                                            ensure_ascii=False) + "\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--input", type=Path)
    group.add_argument("--cad-dir", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--name")
    parser.add_argument("--cd-samples", type=int, default=8192)
    parser.add_argument("--log-only", action="store_true",
                        help="discard per-case source/STEP artifacts; keep manifest, errors.csv and train_ready index")
    parser.add_argument("--isolate", action="store_true",
                        help="run each case in a child process so a native CAD crash cannot stop the batch")
    parser.add_argument("--case-input", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--case-out", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.case_input is not None:
        if args.case_out is None:
            parser.error("--case-input requires --case-out")
        args.case_out.mkdir(parents=True, exist_ok=True)
        result = run_case(args.case_input, args.case_out, cd_samples=args.cd_samples)
        _save_json(args.case_out / "report.json", result)
        _append_result(args.case_out.parent / "progress.jsonl", result)
        print(json.dumps({"stem": result["stem"], "status": result["status"]}, ensure_ascii=False))
        return 0
    if args.input is None and args.cad_dir is None:
        parser.error("one of --input or --cad-dir is required")
    sources = [args.input] if args.input is not None else sorted(args.cad_dir.glob("*.json"))
    if args.limit is not None:
        sources = sources[:args.limit]
    label = args.name or (args.input.stem if args.input else args.cad_dir.name + f"-first{args.limit}" if args.limit else args.cad_dir.name + "-all")
    manifest = run_corpus(sources, corpus_dir(label, args.out), cd_samples=args.cd_samples,
                          log_only=args.log_only, isolate=args.isolate)
    print(json.dumps({"total": manifest["total"], "counts": manifest["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
