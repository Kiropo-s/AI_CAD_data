"""Compare a one-circle DeepCAD JSON with locally generated FTC geometry.

This is an OCP port of the minimal circle/prism path in the official
DeepCAD cadlib/visualize.py. It does not import the project's IR adapter for
the reference shape and does not call cadlib.normalize().
"""

from __future__ import annotations

import argparse
import json
import math
import runpy
from pathlib import Path

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.Bnd import Bnd_Box
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from json2scapi.adapters.deepcad import convert
from json2scapi.evaluation.metrics import chamfer_distance, volume_iou
from json2scapi.codegen.api import generate
from json2scapi.runtime.output_layout import case_dir


def _xyz(value: dict) -> tuple[float, float, float]:
    return (float(value["x"]), float(value["y"]), float(value["z"]))


def reference_shape(payload: dict):
    """Read the raw DeepCAD fields independently; reject other model types."""
    sequence = payload["sequence"]
    if [item["type"] for item in sequence] != ["Sketch", "ExtrudeFeature"]:
        raise ValueError("minimal reference supports one Sketch and one ExtrudeFeature")
    sketch = payload["entities"][sequence[0]["entity"]]
    feature = payload["entities"][sequence[1]["entity"]]
    if (feature["operation"] != "NewBodyFeatureOperation" or
            feature["extent_type"] != "OneSideFeatureExtentType" or
            feature["start_extent"]["type"] != "ProfilePlaneStartDefinition" or
            len(feature["profiles"]) != 1):
        raise ValueError("minimal reference supports one-sided NewBody only")
    profile_ref = feature["profiles"][0]
    if profile_ref["sketch"] != sequence[0]["entity"]:
        raise ValueError("extrusion references a different sketch")
    loops = sketch["profiles"][profile_ref["profile"]]["loops"]
    if len(loops) != 1 or not loops[0]["is_outer"] or len(loops[0]["profile_curves"]) != 1:
        raise ValueError("minimal reference supports one outer circle only")
    circle = loops[0]["profile_curves"][0]
    if circle["type"] != "Circle3D":
        raise ValueError("minimal reference supports Circle3D only")
    if any(abs(value) > 1e-12 for value in (
        float(feature["extent_one"]["taper_angle"]["value"]),
        float(feature["extent_two"]["taper_angle"]["value"]),
    )):
        raise ValueError("minimal reference does not support taper")
    transform = sketch["transform"]
    origin = _xyz(transform["origin"])
    x_axis = _xyz(transform["x_axis"])
    y_axis = _xyz(transform["y_axis"])
    z_axis = _xyz(transform["z_axis"])
    local = _xyz(circle["center_point"])
    if abs(local[2]) > 1e-9:
        raise ValueError("circle is not on the sketch plane")
    center = tuple(1000 * (origin[i] + local[0] * x_axis[i] + local[1] * y_axis[i]) for i in range(3))
    distance = 1000 * float(feature["extent_one"]["distance"]["value"])
    radius = 1000 * float(circle["radius"])
    if distance == 0 or radius <= 0:
        raise ValueError("invalid circle or extrusion distance")
    direction = z_axis if distance > 0 else tuple(-value for value in z_axis)
    axis = gp_Ax2(gp_Pnt(*center), gp_Dir(*direction), gp_Dir(*x_axis))
    shape = BRepPrimAPI_MakeCylinder(axis, radius, abs(distance)).Shape()
    if not BRepCheck_Analyzer(shape).IsValid():
        raise ValueError("reference B-rep is invalid")
    return shape


def _volume(shape) -> float:
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return float(props.Mass())


def _bbox(shape) -> list[float]:
    box = Bnd_Box()
    box.SetGap(0.0)
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    return list(box.Get())


def _difference(first, second) -> float:
    operation = BRepAlgoAPI_Cut(first, second)
    operation.Build()
    if not operation.IsDone() or not BRepCheck_Analyzer(operation.Shape()).IsValid():
        raise ValueError("B-rep difference failed or is invalid")
    return abs(_volume(operation.Shape()))


def _read_step(path: Path):
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone or reader.TransferRoots() < 1:
        raise ValueError(f"cannot read STEP: {path}")
    shape = reader.OneShape()
    if shape.IsNull() or not BRepCheck_Analyzer(shape).IsValid():
        raise ValueError(f"invalid STEP geometry: {path}")
    return shape


def compare(source: Path, out_dir: Path | None = None, *, relative_tolerance: float = 1e-6,
            sample_count: int = 8192, sample_seed: int = 0,
            mesh_deflection_mm: float = 0.1, cd_relative_tolerance: float = 0.01) -> dict:
    from simplecadapi.kernel.ocp_export import export_step_shapes

    out_dir = case_dir(source.stem, out_dir)
    payload = json.loads(source.read_text(encoding="utf-8"))
    reference = reference_shape(payload)
    ir = convert(payload, str(source))
    code = generate(ir, source.stem)
    out_dir.mkdir(parents=True, exist_ok=True)
    candidate_path = out_dir / "model.ftc.py"
    candidate_path.write_text(code, encoding="utf-8")
    candidate = runpy.run_path(str(candidate_path))["build"]()
    candidate_shape = candidate.wrapped
    if not BRepCheck_Analyzer(candidate_shape).IsValid():
        raise ValueError("candidate B-rep is invalid")
    reference_step = out_dir / "reference.step"
    candidate_step = out_dir / "candidate.step"
    export_step_shapes([reference], str(reference_step))
    export_step_shapes([candidate_shape], str(candidate_step))
    reference = _read_step(reference_step)
    candidate_shape = _read_step(candidate_step)

    ref_volume = _volume(reference)
    candidate_volume = _volume(candidate_shape)
    reference_bbox = _bbox(reference)
    candidate_bbox = _bbox(candidate_shape)
    missing = _difference(reference, candidate_shape)
    excess = _difference(candidate_shape, reference)
    iou = volume_iou(reference, candidate_shape)
    cd_mm2, cd_mean_mm = chamfer_distance(
        reference, candidate_shape, count=sample_count, seed=sample_seed,
        linear_deflection=mesh_deflection_mm,
    )
    volume_error = abs(candidate_volume - ref_volume) / ref_volume
    bbox_error_mm = max(abs(a - b) for a, b in zip(reference_bbox, candidate_bbox))
    difference_error = (missing + excess) / ref_volume
    diagonal_mm = math.dist(reference_bbox[:3], reference_bbox[3:])
    if diagonal_mm <= 0:
        raise ValueError("reference bounding box has zero diagonal")
    cd_relative = cd_mean_mm / diagonal_mm
    result = {
        "reference": "raw_json_circle_prism_ocp_port_of_official_cadlib",
        "official_script_executed": False,
        "comparison_stage": "step_roundtrip",
        "source": str(source),
        "candidate_ftc": str(candidate_path),
        "reference_step": str(reference_step),
        "candidate_step": str(candidate_step),
        "reference_volume_mm3": ref_volume,
        "candidate_volume_mm3": candidate_volume,
        "volume_relative_error": volume_error,
        "reference_bbox_mm": reference_bbox,
        "candidate_bbox_mm": candidate_bbox,
        "bbox_max_error_mm": bbox_error_mm,
        "missing_volume_mm3": missing,
        "excess_volume_mm3": excess,
        "symmetric_difference_relative": difference_error,
        "volume_iou": iou,
        "chamfer_distance_mm2": cd_mm2,
        "chamfer_mean_mm": cd_mean_mm,
        "chamfer_relative": cd_relative,
        "chamfer_sampling": {"points_per_shape": sample_count, "seed": sample_seed,
                             "linear_deflection_mm": mesh_deflection_mm},
        "thresholds": {"volume_relative_error": relative_tolerance,
                       "bbox_max_error_mm": 1e-5,
                       "symmetric_difference_relative": relative_tolerance,
                       "volume_iou_min": 1 - relative_tolerance,
                       "chamfer_relative_max": cd_relative_tolerance},
        "passed": all(math.isfinite(value) for value in (volume_error, bbox_error_mm, difference_error,
                                                         iou, cd_mm2, cd_relative))
        and volume_error <= relative_tolerance and bbox_error_mm <= 1e-5
        and difference_error <= relative_tolerance and iou >= 1 - relative_tolerance
        and cd_relative <= cd_relative_tolerance,
    }
    (out_dir / "compare.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="case directory (default: out/cases/<stem>)")
    parser.add_argument("--samples", type=int, default=8192,
                        help="area-weighted surface points per STEP model")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--mesh-deflection-mm", type=float, default=0.1)
    parser.add_argument("--cd-relative-tolerance", type=float, default=0.01)
    args = parser.parse_args()
    result = compare(args.input, args.out, sample_count=args.samples, sample_seed=args.seed,
                     mesh_deflection_mm=args.mesh_deflection_mm,
                     cd_relative_tolerance=args.cd_relative_tolerance)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
