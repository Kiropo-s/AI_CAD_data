"""Readable-only pipeline and signed adapter contracts."""
import ast
import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from json2scapi.adapters.deepcad import convert, ConversionError, SKETCH_PLANAR_TOL_MM
from json2scapi.ir.validation import validate_ir


def source(stem="00000007"):
    return json.loads((ROOT / "datasets/DeepCAD/mixed/cases" / (stem + ".json")).read_text(encoding="utf-8"))


class AdapterRulesTests(unittest.TestCase):
    def test_real_signed_and_planar_cases_validate_without_unsupported(self):
        ir = convert(source("00000718"), "00000718")
        self.assertFalse(ir["unsupported"])
        self.assertEqual(validate_ir(ir, schema=False), [])
        # Signed extents are now covered, but a later profile in this source
        # contains a 546.1 mm local z and must not be silently projected.
        data = source("00007714")
        with self.assertRaisesRegex(ConversionError, "nonplanar"):
            convert(data)
        prefix = {**data, "sequence": data["sequence"][:-1]}
        ir = convert(prefix, "signed_prefix")
        self.assertEqual(validate_ir(ir, schema=False), [])
        self.assertTrue(any(e.get("source", {}).get("extent_normalization", {}).get("extent_one_m", 0) < 0
                            for e in ir["entities"].values()))
        ir = convert(source("00000718"))
        corrections = [c for e in ir["entities"].values()
                       for c in e.get("source", {}).get("planarity_projection", [])]
        self.assertTrue(corrections)
        self.assertTrue(all(abs(c["z_mm"]) <= SKETCH_PLANAR_TOL_MM for c in corrections))

    def test_axis_rounding_is_normalized_without_flipping_orientation(self):
        data = source("00000718")
        ir = convert(data)
        normalized = [e for e in ir["entities"].values()
                      if "frame_normalization" in e.get("source", {})]
        self.assertTrue(normalized)
        for e in normalized:
            frame = e["params"]["frame"]
            for key in ("x_axis", "y_axis", "z_axis"):
                self.assertAlmostEqual(sum(v*v for v in frame[key]), 1.0, places=12)
        data = source()
        sketch = next(e for e in data["entities"].values() if e["type"] == "Sketch")
        sketch["transform"]["z_axis"]["z"] = -1
        with self.assertRaisesRegex(ConversionError, "right-handed"):
            convert(data)

    def test_signed_interval_combinations_and_zero_side(self):
        for one, two, expected_direction, extent, distance, distance2 in (
            (0.02, 0.01, 1, "two_sides", 20, 10),
            (-0.02, 0.01, -1, "one_side", 20, None),
            (0.02, -0.03, 1, "one_side", 30, None),
            (-0.02, -0.03, -1, "two_sides", 20, 30),
            (0.02, 0, 1, "one_side", 20, None),
            (0, 0.01, -1, "one_side", 10, None),
        ):
            data = source()
            e = next(e for e in data["entities"].values() if e["type"] == "ExtrudeFeature")
            e["extent_type"] = "TwoSidesFeatureExtentType"
            e["extent_one"]["distance"]["value"] = one
            e["extent_two"]["distance"]["value"] = two
            ir = convert(data)
            step = ir["entities"][ir["results"][0]]
            self.assertEqual(step["params"]["extent"], extent)
            self.assertAlmostEqual(step["params"]["distance"], distance)
            self.assertEqual(step["params"].get("distance2"), distance2)
            self.assertEqual(step["params"]["direction"], [0, 0, expected_direction])
            self.assertEqual(validate_ir(ir, schema=False), [])
            if distance2 is not None:
                broken = copy.deepcopy(ir)
                broken["entities"][ir["results"][0]]["source"]["extent_normalization"]["interval_mm"][0] += 1
                self.assertTrue(validate_ir(broken, schema=False))

    def test_nonplanar_input_and_signed_taper_are_not_silently_repaired(self):
        data = source()
        sketch = next(e for e in data["entities"].values() if e["type"] == "Sketch")
        curve = next(iter(sketch["profiles"].values()))["loops"][0]["profile_curves"][0]
        curve["center_point"]["z"] = 1e-4
        with self.assertRaisesRegex(ConversionError, "nonplanar"):
            convert(data)
        data = source()
        e = next(e for e in data["entities"].values() if e["type"] == "ExtrudeFeature")
        e["extent_type"] = "TwoSidesFeatureExtentType"
        e["extent_one"]["distance"]["value"] = -0.02
        e["extent_two"]["distance"]["value"] = 0.01
        e["extent_one"]["taper_angle"]["value"] = 0.1
        with self.assertRaisesRegex(ConversionError, "with taper"):
            convert(data)


@unittest.skipUnless(all(importlib.util.find_spec(m) for m in ("OCP", "scipy", "jsonschema")), "CAD dependencies missing")
class ReadableOnlyKernelTests(unittest.TestCase):
    def test_source_frame_restoration_preserves_negative_x_normal(self):
        import numpy as np
        from unittest.mock import patch
        from types import SimpleNamespace
        path = ROOT / "deepcad_official/local_adapter/json2step_ocp.py"
        spec = importlib.util.spec_from_file_location("frame_adapter_test", path)
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        data = source("00008376")
        original = np.cross
        def cross(a, b, *args, **kwargs):
            a, b = np.asarray(a), np.asarray(b)
            if a.shape[-1:] == (2,) and b.shape[-1:] == (2,):
                return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]
            return original(a, b, *args, **kwargs)
        diagnostics = {}
        with patch.object(np, "cross", cross):
            sequence = adapter.CADSequence.from_dict(data)
            adapter._restore_source_frames(sequence, data, diagnostics)
        np.testing.assert_allclose(sequence.seq[1].sketch_plane.normal, [-1, 0, 0])
        self.assertTrue(diagnostics["source_frame_repairs"])
        ir = convert(data)
        from json2scapi.codegen.api import generate
        from json2scapi.evaluation.metrics import compare_shapes
        from simplecadapi.kernel.ocp_export import make_compound
        namespace = {}
        exec(generate(ir, "source_frame"), namespace)
        namespace["build"]()
        result = compare_shapes(adapter.build_shape(data),
            make_compound([b.wrapped for b in namespace["BODIES"]]),
            volume_relative_tolerance=1e-3, bbox_relative_tolerance=1e-3,
            difference_relative_tolerance=1e-3, iou_min=0.999)
        self.assertTrue(result["passed"], result)

    def test_signed_circle_geometry_matches_analytic_volume(self):
        import math
        from json2scapi.codegen.api import generate
        for one, two in ((-0.02, 0.01), (0.02, -0.03), (-0.02, -0.03), (0, 0.01)):
            data = source()
            e = next(e for e in data["entities"].values() if e["type"] == "ExtrudeFeature")
            e["extent_type"] = "TwoSidesFeatureExtentType"
            e["extent_one"]["distance"]["value"] = one
            e["extent_two"]["distance"]["value"] = two
            namespace = {}
            exec(generate(convert(data), "signed_circle"), namespace)
            model = namespace["build"]()
            length = (max(0, one, -two) - min(0, one, -two)) * 1000
            expected = math.pi * 91.04851 ** 2 * length
            self.assertAlmostEqual(model.get_volume(), expected, delta=expected * 1e-8)


if __name__ == "__main__":
    unittest.main()
