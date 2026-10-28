"""Kernel-backed metric checks; skipped by the lightweight Python runtime."""

from __future__ import annotations

import importlib.util
import copy
import json
import math
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ("OCP", "numpy", "scipy")),
                     "CAD metric dependencies are not installed")
class GeometryMetricsTests(unittest.TestCase):
    def test_concentric_source_outer_flags_still_create_a_hole(self) -> None:
        from json2scapi.codegen.api import generate

        source = Path(__file__).resolve().parents[1] / "examples/deepcad_00000007.ir.json"
        ir = json.loads(source.read_text(encoding="utf-8"))
        original_profile_id = next(k for k, e in ir["entities"].items() if e["op"] == "sketch.profile")
        original_curve_id = next(k for k, e in ir["entities"].items() if e["op"] == "sketch.circle")
        inner_circle = copy.deepcopy(ir["entities"][original_curve_id])
        inner_circle["params"]["radius"] = 40.0
        ir["entities"]["inner_circle"] = inner_circle
        ir["entities"]["inner_loop"] = {
            "type": "SketchLoop", "op": "sketch.loop", "inputs": ["inner_circle"],
            "params": {"is_outer": False, "closed": True},
            "source": {"deepcad_is_outer": True, "role_provenance": "geometry_nesting"},
        }
        ir["entities"][original_profile_id]["inputs"].append("inner_loop")
        ir["entities"][original_profile_id]["params"]["regions"][0]["holes"].append("inner_loop")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.ftc.py"
            path.write_text(generate(ir, "concentric_ring"), encoding="utf-8")
            model = runpy.run_path(str(path))["build"]()
        expected = math.pi * (91.04851 ** 2 - 40.0 ** 2) * 25.4
        self.assertAlmostEqual(model.get_volume(), expected, delta=expected * 1e-8)

    def test_identical_and_different_cylinders(self) -> None:
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from json2scapi.legacy.compare_minimal import chamfer_distance, volume_iou

        first = BRepPrimAPI_MakeCylinder(10.0, 5.0).Shape()
        different = BRepPrimAPI_MakeCylinder(11.0, 5.0).Shape()
        self.assertAlmostEqual(volume_iou(first, first), 1.0, places=10)
        self.assertLess(volume_iou(first, different), 0.85)
        same_cd, same_mean = chamfer_distance(first, first, count=2048)
        other_cd, other_mean = chamfer_distance(first, different, count=2048)
        self.assertAlmostEqual(same_cd, 0.0, places=10)
        self.assertAlmostEqual(same_mean, 0.0, places=10)
        self.assertGreater(other_cd, same_cd)
        self.assertGreater(other_mean, same_mean)

    def test_report_rejects_modified_generated_model(self) -> None:
        import json2scapi.legacy.compare_minimal as compare_minimal
        from json2scapi.codegen.api import generate

        source = Path(__file__).resolve().parents[2] / "datasets/DeepCAD/cad_json/0000/00000007.json"
        if not source.is_file():
            self.skipTest("DeepCAD sample is not installed")

        def changed_radius(ir, stem):
            code = generate(ir, stem)
            self.assertIn("FEATURE_01_CIRCLE_01_RADIUS_MM = 91.04851", code)
            return code.replace("FEATURE_01_CIRCLE_01_RADIUS_MM = 91.04851", "FEATURE_01_CIRCLE_01_RADIUS_MM = 92.04851", 1)

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            compare_minimal, "generate", side_effect=changed_radius
        ):
            report = compare_minimal.compare(source, Path(temporary), sample_count=2048)
        self.assertFalse(report["passed"])
        self.assertLess(report["volume_iou"], report["thresholds"]["volume_iou_min"])
        self.assertGreater(report["chamfer_distance_mm2"], 0)


if __name__ == "__main__":
    unittest.main()
