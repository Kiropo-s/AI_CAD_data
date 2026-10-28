"""Build a 3D model from a DeepCAD cad_json file and export STEP/STL.

Pipeline:
    cad_json -> deepcad_to_ir -> ir2code -> execute -> STEP/STL

Usage:
    python deepcad_render.py --input cad_json/0000/00000007.json

Default output:
    out/cases/<stem>/
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from json2scapi.adapters.deepcad import convert
from json2scapi.codegen.api import generate
from json2scapi.runtime.output_layout import case_dir
from json2scapi.runtime.paths import WORKSPACE_DIR


def _worker_path() -> Path:
    return WORKSPACE_DIR / "research" / "build123dftc" / "b3ftc" / "_validate_worker.py"


def _run_worker(ftc: Path, output: Path, staging: Path, step: Path) -> dict[str, Any]:
    worker = _worker_path()
    if not worker.is_file():
        raise FileNotFoundError(f"validation worker not found: {worker}")
    command = [
        sys.executable, str(worker),
        "--ftc", str(ftc),
        "--output", str(output),
        "--staging-dir", str(staging),
        "--export-step", str(step),
    ]
    subprocess.run(command, check=True, cwd=ftc.parent)
    return json.loads(output.read_text(encoding="utf-8", errors="replace"))


def _step_to_stl(step: Path, stl: Path, deflection: float = 0.1) -> None:
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader
    from OCP.StlAPI import StlAPI_Writer

    reader = STEPControl_Reader()
    if reader.ReadFile(str(step)) != IFSelect_RetDone:
        raise ValueError(f"could not read STEP: {step}")
    reader.TransferRoots()
    shape = reader.OneShape()
    if shape.IsNull():
        raise ValueError(f"STEP contains no shape: {step}")
    BRepMesh_IncrementalMesh(shape, deflection, False, 0.5, True)
    writer = StlAPI_Writer()
    writer.ASCIIMode = False
    if not writer.Write(shape, str(stl)):
        raise ValueError(f"could not write STL: {stl}")


def render(source: Path, out_dir: Path | None = None, stem: str | None = None, *, stl: bool = True) -> dict[str, Any]:
    stem = stem or source.stem
    out_dir = case_dir(stem, out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    staging = out_dir / ".work" / "render"
    staging.mkdir(parents=True, exist_ok=True)

    payload = json.loads(source.read_text(encoding="utf-8", errors="replace"))
    ir = convert(payload, str(source))
    ir_path = out_dir / "model.ir.json"
    ir_path.write_text(json.dumps(ir, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    source_code = generate(ir, stem)
    ftc_path = out_dir / "model.ftc.py"
    ftc_path.write_text(source_code, encoding="utf-8")

    step_path = out_dir / "model.step"
    result_path = out_dir / "result.json"
    result = _run_worker(ftc_path, result_path, staging, step_path)
    if result.get("status") != "ok":
        raise RuntimeError(f"generated model failed: {result}")

    stl_path = out_dir / "model.stl"
    if stl:
        _step_to_stl(step_path, stl_path)

    report = {
        "stem": stem,
        "source": str(source),
        "ir": str(ir_path),
        "ftc": str(ftc_path),
        "step": str(step_path),
        "stl": str(stl_path) if stl else None,
        "metrics": result,
    }
    (out_dir / "render.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="case directory (default: out/cases/<stem>)")
    parser.add_argument("--stem")
    parser.add_argument("--no-stl", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = render(args.input, args.out, args.stem, stl=not args.no_stl)
    except Exception as error:
        print(f"{args.input}: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    metrics = report["metrics"]
    print(
        f"{report['stem']}: volume={metrics.get('volume')} "
        f"bbox={metrics.get('bbox')} valid={metrics.get('is_valid')}"
    )
    print(f"STEP: {report['step']}")
    if report.get("stl"):
        print(f"STL:  {report['stl']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
