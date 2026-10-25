"""Similarity-shape acceptance and evidence-policy regressions."""
from __future__ import annotations

import copy
import importlib.util
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from json2scapi.evaluation.policy import THRESHOLDS, decide, metric_check


class PolicyTests(unittest.TestCase):
    def test_optional_failure_does_not_block_training(self):
        report = {"checks": dict.fromkeys(THRESHOLDS, "pass"), "registration": {"status": "resolved"},
                  "metric_errors": [{"metric": "local_surface", "required": False}]}
        self.assertEqual(decide(report)["status"], "train_ready")
        report["metric_errors"][0]["required"] = True
        self.assertEqual(decide(report)["status"], "provisional")

    def test_three_hard_metrics_and_threshold_directions(self):
        self.assertEqual(set(THRESHOLDS), {"volume_iou", "volume_relative_error", "chamfer_relative"})
        for name, threshold in THRESHOLDS.items():
            self.assertEqual(metric_check(name, threshold), "pass")
            self.assertEqual(metric_check(name, None), "error")
        self.assertEqual(metric_check("volume_iou", .9989), "fail")
        self.assertEqual(metric_check("volume_iou", 1.01), "error")
        self.assertEqual(metric_check("volume_iou", -.01), "error")
        self.assertEqual(metric_check("volume_iou", float("nan")), "error")
        self.assertEqual(metric_check("volume_relative_error", .0011), "fail")
        self.assertEqual(metric_check("chamfer_relative", .0101), "fail")

    def test_confirmed_failure_has_priority(self):
        report = {"checks": {"volume_relative_error": "fail"}, "registration": {"status": "unresolved"},
                  "metric_errors": [{"required": True}]}
        self.assertEqual(decide(report)["status"], "rejected")
        report["checks"] = {"volume_iou": "fail"}
        self.assertEqual(decide(report)["reason"], "measurement_error")
        report["registration"]["status"] = "resolved"
        self.assertEqual(decide(report)["reason"], "measurement_error")
        report["metric_errors"] = []
        self.assertEqual(decide(report)["status"], "rejected")
        report["registration"]["status"] = "unresolved"
        self.assertEqual(decide(report)["reason"], "registration_unresolved")


@unittest.skipUnless(importlib.util.find_spec("OCP"), "CAD runtime required")
class SimilarityTests(unittest.TestCase):
    @staticmethod
    def box(x=4, y=7, z=11, origin=None):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Pnt
        return (BRepPrimAPI_MakeBox(x, y, z) if origin is None else
                BRepPrimAPI_MakeBox(gp_Pnt(*origin), x, y, z)).Shape()

    @staticmethod
    def moved(shape, scale=1, rotation=(.4, -.7, 1.1), translation=(23, -9, 44)):
        import numpy as np
        from scipy.spatial.transform import Rotation
        from json2scapi.evaluation.registration import transform_shape
        matrix = np.eye(4)
        matrix[:3, :3] = scale * Rotation.from_rotvec(rotation).as_matrix()
        matrix[:3, 3] = translation
        return transform_shape(shape, matrix)

    def evaluate(self, first, second, **kwargs):
        from json2scapi.evaluation.similarity import evaluate_shapes
        return evaluate_shapes(first, second, diagnostics=False, **kwargs)

    def test_similarity_transforms_and_determinism(self):
        shape = self.box()
        results = []
        for scale in (.01, 10, 1000):
            result = self.evaluate(shape, self.moved(shape, scale))
            self.assertEqual(result["decision"]["status"], "train_ready", result)
            self.assertAlmostEqual(result["transform"]["scale"], 1 / scale, places=6)
            self.assertAlmostEqual(result["registration"]["determinant"], 1, places=10)
            results.append(result)
        self.assertEqual(results[0], self.evaluate(shape, self.moved(shape, .01)))

    def test_symmetric_shapes(self):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        for shape in (self.box(5, 5, 5), self.box(5, 5, 5.00001), BRepPrimAPI_MakeCylinder(3, 9).Shape()):
            result = self.evaluate(shape, self.moved(shape))
            self.assertEqual(result["decision"]["status"], "train_ready", result)

    def test_nonuniform_scale_and_missing_feature_not_accepted(self):
        for changed in (self.box(5, 7, 11), self.box(4, 7, 9)):
            result = self.evaluate(self.box(), changed)
            self.assertEqual(result["decision"]["status"], "rejected", result)

    def test_compound_union_and_relative_placement(self):
        from json2scapi.runtime.worker import compound
        from json2scapi.evaluation.registration import occupied_space
        raw = compound([self.box(), self.box(origin=(2, 0, 0))])
        fused = occupied_space(raw)
        result = self.evaluate(fused, raw)
        self.assertEqual(result["decision"]["status"], "train_ready", result)
        self.assertIn("solid_count_mismatch", result["diagnostics"])
        self.assertNotIn("solid_count_mismatch", result["warnings"])
        separated = compound([self.box(), self.box(2, 3, 4, (14, 1, 0))])
        moved = self.moved(separated, 10)
        self.assertEqual(self.evaluate(separated, moved)["decision"]["status"], "train_ready")
        changed = compound([self.box(), self.box(2, 3, 4, (19, 1, 0))])
        self.assertNotEqual(self.evaluate(separated, changed)["decision"]["status"], "train_ready")

    def test_hole_location_and_wall_thickness_changes(self):
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
        def drilled(center, radius):
            block = self.box(20, 31, 9)
            tool = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*center, -1), gp_Dir(0,0,1)), radius, 11).Shape()
            cut = BRepAlgoAPI_Cut(block, tool)
            cut.Build()
            return cut.Shape()
        original = drilled((6, 8), 3)
        for changed in (drilled((10, 14), 3), drilled((6, 8), 5)):
            report = self.evaluate(original, changed)
            self.assertNotEqual(report["decision"]["status"], "train_ready", report)

    def test_small_missing_component_produces_local_warning(self):
        from json2scapi.runtime.worker import compound
        from json2scapi.evaluation.similarity import evaluate_shapes
        # Its volume is below the global difference budget; the local probe must
        # still expose the omitted remote component. This documents the limit of
        # global averages rather than inventing a new hard gate.
        reference = compound([self.box(20, 31, 9), self.box(.05, .05, .05, (40, 45, 30))])
        report = evaluate_shapes(reference, self.box(20, 31, 9))
        self.assertIn("local_surface_deviation", report["warnings"], report)
        self.assertGreater(report["local_surface"]["relative_p95_max"], .01)

    def test_degenerate_registration_failure_stays_unresolved(self):
        import numpy as np
        from json2scapi.evaluation.similarity import evaluate_shapes
        with patch("json2scapi.evaluation.similarity.candidates", return_value=[{
            "rotation": np.eye(3), "score": 10., "initial": 0, "refined": False, "initial_count": 1
        }]):
            report = evaluate_shapes(self.box(5,5,5), self.moved(self.box(5,5,5)), diagnostics=False)
        self.assertEqual(report["decision"]["reason"], "registration_unresolved", report)

    def test_chiral_mirror_is_not_accepted(self):
        import numpy as np
        from OCP.gp import gp_Trsf, gp_Ax2, gp_Pnt, gp_Dir
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        from json2scapi.runtime.worker import compound
        # Distinct sizes identify the four noncoplanar components uniquely.
        shape = compound([self.box(1, 2, 3, (0, 0, 0)), self.box(2, 3, 4, (8, 1, 0)),
                          self.box(3, 4, 5, (1, 12, 0)), self.box(4, 5, 6, (2, 3, 17))])
        mirror = gp_Trsf()
        mirror.SetMirror(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(1, 0, 0)))
        reflected = BRepBuilderAPI_Transform(shape, mirror, True).Shape()
        result = self.evaluate(shape, reflected)
        self.assertNotEqual(result["decision"]["status"], "train_ready", result)

    def test_empty_boolean_results_and_required_iou_error(self):
        from json2scapi.evaluation.metrics import volume_iou, symmetric_difference_volume
        from json2scapi.evaluation.similarity import evaluate_shapes
        shape = self.box()
        self.assertEqual(volume_iou(shape, self.box(origin=(100, 0, 0))), 0)
        self.assertAlmostEqual(symmetric_difference_volume(shape, shape), 0)
        with patch("json2scapi.evaluation.similarity.volume_iou", side_effect=ValueError("injected Common failure")):
            report = self.evaluate(shape, shape)
        self.assertEqual(report["decision"]["status"], "provisional", report)
        self.assertEqual(report["checks"]["volume_iou"], "error")
        self.assertTrue(any(e["metric"] == "volume_iou" and e["required"] for e in report["metric_errors"]))
        self.assertIsNotNone(report["chamfer_relative"])
        self.assertIsNotNone(report["volume_relative_error"])
        # Successful primary metrics must not invoke Cut, even with diagnostics.
        with patch("json2scapi.evaluation.similarity.symmetric_difference_volume", side_effect=ValueError("Cut")) as cut:
            report = evaluate_shapes(shape, shape)
        cut.assert_not_called()
        self.assertEqual(report["decision"]["status"], "train_ready", report)
        self.assertIsNone(report["symmetric_difference_relative"])
        with patch("json2scapi.evaluation.similarity.volume_iou", return_value=.5), patch(
                "json2scapi.evaluation.similarity.symmetric_difference_volume", side_effect=ValueError("Cut")):
            report = self.evaluate(shape, shape)
        self.assertEqual(report["decision"]["status"], "provisional", report)
        self.assertEqual(report["checks"]["volume_iou"], "error")

    def test_iou_roundoff_requires_independent_verification(self):
        from json2scapi.evaluation.similarity import _pose_metrics
        from json2scapi.evaluation.registration import normalize
        shape, _, _ = normalize(self.box())
        raw = 1 + 1e-10
        with patch("json2scapi.evaluation.similarity.volume_iou", return_value=raw):
            measured = _pose_metrics(shape, shape, 8192)
        self.assertEqual(measured["volume_iou"], raw)
        self.assertEqual(measured["checks"]["volume_iou"], "pass")
        self.assertIn("volume_iou_roundoff_verified", measured["diagnostics"])
        with patch("json2scapi.evaluation.similarity.volume_iou", return_value=raw), patch(
                "json2scapi.evaluation.similarity.symmetric_difference_volume", side_effect=ValueError("Cut")):
            measured = _pose_metrics(shape, shape, 8192)
        self.assertEqual(measured["checks"]["volume_iou"], "error")
        with patch("json2scapi.evaluation.similarity.volume_iou", return_value=1.01):
            measured = _pose_metrics(shape, shape, 8192)
        self.assertEqual(measured["checks"]["volume_iou"], "error")

    def test_optional_local_diagnostic_failure_does_not_block(self):
        from json2scapi.evaluation.similarity import evaluate_shapes
        with patch("json2scapi.evaluation.similarity._local_diagnostics", side_effect=ValueError("local diagnostic")):
            report = evaluate_shapes(self.box(), self.box())
        self.assertEqual(report["decision"]["status"], "train_ready", report)
        self.assertTrue(any(e["metric"] == "local_surface" and not e["required"] for e in report["metric_errors"]))

    def test_contradictory_boolean_measurements_stay_provisional(self):
        from json2scapi.evaluation.similarity import evaluate_shapes
        from json2scapi.evaluation.metrics import volume
        shape = self.box()
        # Coincident shapes cannot have the full input volume as a difference.
        # Common certifies this contradiction; no alternate wrong pose can turn
        # it into a conclusive rejection.
        with patch("json2scapi.evaluation.similarity.symmetric_difference_volume", return_value=1000.0), patch(
                "json2scapi.evaluation.similarity.volume_iou", return_value=.5):
            report = evaluate_shapes(shape, shape, diagnostics=False)
        self.assertEqual(report["decision"]["status"], "provisional", report)
        self.assertTrue(any(e["metric"] == "boolean_consistency" for e in report["metric_errors"]))
        # Both booleans can be mutually consistent and still be wrong. An
        # independent interior-point certificate must catch a false disjointness.
        from json2scapi.evaluation.registration import normalize
        normalized, _, _ = normalize(shape)
        total = 2 * volume(normalized)
        with patch("json2scapi.evaluation.similarity.symmetric_difference_volume", return_value=total), patch(
                "json2scapi.evaluation.similarity.volume_iou", return_value=0.0):
            report = evaluate_shapes(shape, shape, diagnostics=False)
        self.assertEqual(report["decision"]["status"], "provisional", report)
        self.assertIsNotNone(report.get("shared_interior_witness_normalized_mm"))

    def test_step_roundtrip_and_error_attribution(self):
        from json2scapi.runtime.worker import run
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code = root / "model.py"
            import json
            from json2scapi.codegen.api import generate
            ir = json.loads((Path(__file__).resolve().parents[1] / "examples/line_arc_plate.ir.json").read_text(encoding="utf-8"))
            code.write_text(generate(ir, "roundtrip"), encoding="utf-8")
            good = run(code, root / "model.step")
            self.assertEqual(good["status"], "ok", good)
            with patch("json2scapi.runtime.worker.export_step", side_effect=OSError("export")):
                self.assertEqual(run(code, root / "bad.step")["status"], "candidate_step_invalid")
            with patch("json2scapi.evaluation.metrics.read_step", side_effect=ValueError("invalid readback")):
                self.assertEqual(run(code, root / "bad.step")["status"], "candidate_step_invalid")
            fake = {"decision": {"status": "provisional", "reason": "measurement_error"}}
            with patch("json2scapi.evaluation.similarity.evaluate_shapes", return_value=fake):
                self.assertEqual(run(code, root / "bad.step")["status"], "roundtrip_measurement_error")
            fake = {"decision": {"status": "rejected", "reason": "geometry_mismatch"}}
            with patch("json2scapi.evaluation.similarity.evaluate_shapes", return_value=fake):
                self.assertEqual(run(code, root / "bad.step")["status"], "candidate_step_mismatch")


if __name__ == "__main__":
    unittest.main()
