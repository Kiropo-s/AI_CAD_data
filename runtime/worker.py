"""Isolated execution and STEP round-trip validation for json2scapi.

Evaluates the actual return value, not globals claiming a particular body set.
"""
from __future__ import annotations

import argparse
import json
import math
import runpy
import subprocess
import sys
from .paths import PACKAGE_DIR
import traceback
from pathlib import Path


def returned_shapes(value, placement=None):
    from simplecadapi import Solid, Part, Assembly, PartBuildResult, AssemblyBuildResult
    from simplecadapi.product.placement import identity_placement
    from json2scapi.evaluation.registration import transform_shape
    import numpy as np
    if isinstance(value, (PartBuildResult, AssemblyBuildResult)):
        value = value.value
    if isinstance(value, Part):
        value = value.body
    if isinstance(value, Assembly):
        parent = placement or identity_placement()
        return [shape for component in value.components
                for shape in returned_shapes(component.item, parent.compose(component.placement))]
    if isinstance(value, Solid):
        if placement is None or placement == identity_placement():
            return [value.wrapped]
        matrix = np.eye(4)
        matrix[:3, :3] = np.array([placement.x_axis, placement.y_axis, placement.z_axis]).T
        matrix[:3, 3] = placement.origin
        return [transform_shape(value.wrapped, matrix)]
    raise ValueError("build() must return an actual Solid, Part or Assembly")


def compound(shapes):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound
    builder = BRep_Builder()
    result = TopoDS_Compound()
    builder.MakeCompound(result)
    for shape in shapes:
        builder.Add(result, shape)
    return result


def export_step(shapes, path):
    from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    Interface_Static.SetIVal_s("write.surfacecurve.mode", 1)
    Interface_Static.SetIVal_s("write.precision.mode", 0)
    writer = STEPControl_Writer()
    for shape in shapes:
        if writer.Transfer(shape, STEPControl_AsIs) != IFSelect_RetDone:
            raise ValueError("STEP transfer failed")
    if writer.Write(str(path)) != IFSelect_RetDone:
        raise ValueError("STEP write failed")


def run(ftc: Path, step: Path, *, cd_samples=8192) -> dict:
    from OCP.BRepCheck import BRepCheck_Analyzer
    from json2scapi.evaluation.metrics import read_step, solid_count, volume
    from json2scapi.evaluation.similarity import evaluate_shapes
    result = {"status": "build_error", "is_valid": False, "checks": {}, "warnings": []}
    try:
        namespace = runpy.run_path(str(ftc))
        build = namespace.get("build")
        if not callable(build):
            raise ValueError("module must define callable build()")
        shapes = returned_shapes(build())
        if not shapes or any(s.IsNull() or solid_count(s) < 1 or not BRepCheck_Analyzer(s).IsValid() for s in shapes):
            raise ValueError("build returned invalid or empty solid geometry")
        shape = compound(shapes)
        measured_volume = volume(shape)
        if not math.isfinite(measured_volume) or measured_volume <= 0:
            raise ValueError("build returned nonpositive volume")
        result.update(status="ok", is_valid=True, body_count=len(shapes), solid_count=solid_count(shape),
                      volume=measured_volume, checks={"build": "pass"})
    except Exception as exc:
        result.update(error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc(limit=5))
        return result
    try:
        export_step(shapes, step)
        result["checks"]["export"] = "pass"
    except Exception as exc:
        result.update(status="candidate_step_invalid", error=f"{type(exc).__name__}: {exc}")
        result["checks"]["export"] = "fail"
        return result
    try:
        restored = read_step(step)
        if solid_count(restored) < 1:
            raise ValueError("candidate STEP contains no solid")
        result["checks"]["roundtrip_valid"] = "pass"
    except Exception as exc:
        result.update(status="candidate_step_invalid", error=f"{type(exc).__name__}: {exc}")
        result["checks"]["roundtrip_valid"] = "fail"
        return result
    comparison = evaluate_shapes(shape, restored, cd_samples=cd_samples, diagnostics=False)
    result["roundtrip"] = comparison
    decision = comparison["decision"]
    if decision["status"] == "rejected":
        result.update(status="candidate_step_mismatch", error="STEP round-trip changed shape")
        result["checks"]["roundtrip_shape"] = "fail"
    elif decision["status"] == "provisional":
        result.update(status="roundtrip_measurement_error", error=decision["reason"])
        result["checks"]["roundtrip_shape"] = "error"
    else:
        result["checks"]["roundtrip_shape"] = "pass"
    return result


def run_worker(ftc: Path, output: Path, staging: Path, *, export_step: Path,
               quiet=False, cd_samples=8192) -> dict:
    staging.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run([sys.executable, str(PACKAGE_DIR / "local_worker.py"),
            "--ftc", str(ftc), "--step", str(export_step), "--output", str(output),
            "--cd-samples", str(cd_samples)], cwd=staging, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return {"status": "process_error", "is_valid": False, "error": "candidate worker timeout after 120s"}
    if not output.is_file():
        return {"status": "process_error", "is_valid": False,
                "error": f"worker exited {completed.returncode}: {completed.stderr[-1000:]}"}
    result = json.loads(output.read_text(encoding="utf-8"))
    if completed.returncode != 0:
        result.update(status="process_error", error=f"worker exit {completed.returncode}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ftc", type=Path, required=True)
    parser.add_argument("--step", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cd-samples", type=int, default=8192)
    args = parser.parse_args()
    result = run(args.ftc, args.step, cd_samples=args.cd_samples)
    args.output.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
