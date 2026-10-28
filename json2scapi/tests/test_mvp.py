"""Fast, kernel-free checks for the DeepCAD IR MVP contract."""

from __future__ import annotations

import ast
import copy
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import json2scapi.legacy.verify_deepcad as verify_deepcad  # noqa: E402
import json2scapi.adapters.deepcad as deepcad_to_ir  # noqa: E402
import json2scapi.codegen.api as ir2code  # noqa: E402
import json2scapi.runtime.output_layout as output_layout  # noqa: E402
import json2scapi.legacy.batch_verify as batch_verify  # noqa: E402
from json2scapi.adapters.deepcad import ConversionError, convert  # noqa: E402
from json2scapi.codegen.readable import StructuredEmitter as Emitter  # noqa: E402
from json2scapi.ir.validation import validate_ir  # noqa: E402


def _example(name: str) -> dict:
    return json.loads((ROOT / "json2scapi" / "examples" / name).read_text(encoding="utf-8"))


class DeepCADIRTests(unittest.TestCase):
    def test_default_cli_outputs_share_one_case_directory(self) -> None:
        source = ROOT / "datasets" / "DeepCAD" / "cad_json" / "0000" / "00000007.json"
        if not source.is_file():
            self.skipTest("DeepCAD sample is not installed")
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            output_layout, "CASES_DIR", Path(temporary)
        ):
            self.assertEqual(deepcad_to_ir.main(["--input", str(source)]), 0)
            ir_path = Path(temporary) / "00000007" / "model.ir.json"
            self.assertTrue(ir_path.is_file())
            self.assertEqual(ir2code.main(["--input", str(ir_path)]), 0)
            self.assertTrue((ir_path.parent / "model.readable.prepared.ir.json").is_file())
            self.assertTrue((ir_path.parent / "model.readable.ftc.py").is_file())
            self.assertFalse((Path(temporary) / "model").exists())

    def test_real_json_to_ir_to_python_source(self) -> None:
        path = ROOT / "datasets" / "DeepCAD" / "cad_json" / "0000" / "00000007.json"
        if not path.is_file():
            self.skipTest("DeepCAD sample is not installed")
        ir = convert(json.loads(path.read_text(encoding="utf-8")), str(path))
        self.assertEqual(ir["units"], "mm")
        self.assertEqual(ir["unsupported"], [])
        self.assertEqual(validate_ir(ir, schema=False), [])
        self.assertEqual(ir["results"], [ir["sequence"][-1]])
        ast.parse(Emitter(ir, path.stem).emit())

    def test_missing_initial_body_is_not_recovered_as_join(self) -> None:
        path = ROOT / "datasets" / "DeepCAD" / "cad_json" / "0000" / "00000073.json"
        if not path.is_file():
            self.skipTest("DeepCAD sample is not installed")
        with self.assertRaisesRegex(ConversionError, "join requires a prior body"):
            convert(json.loads(path.read_text(encoding="utf-8", errors="replace")), str(path))

    def test_ftc_emitter_requires_normalized_complete_ir(self) -> None:
        ir = _example("plate_with_hole.ir.json")
        ir["units"] = "m"
        with self.assertRaisesRegex(ValueError, "millimeter"):
            Emitter(ir, "unit_test")
        ir["units"] = "mm"
        ir["unsupported"] = [{"code": "missing", "message": "not mapped"}]
        with self.assertRaisesRegex(ValueError, "incomplete IR"):
            Emitter(ir, "unit_test")

    def test_outer_loop_is_selected_by_role_not_list_position(self) -> None:
        ir = _example("plate_with_hole.ir.json")
        ir["entities"]["profile_hole_p1"]["inputs"].reverse()
        source = Emitter(ir, "hole").emit()
        emitter = Emitter(ir, "hole")
        source = emitter.emit()
        metadata = emitter.metadata
        loops = metadata["profiles"]["make_profile_01"]["loops"]
        refs = {loop["ir_id"]: loop["edges"][0]["entity_id"] for loop in loops}
        self.assertIn(f"profile={refs['loop_hole_outer']!r}, inner_profiles={[refs['loop_hole_inner']]!r}", source)
        self.assertEqual(validate_ir(ir, schema=False), [])

    def test_profile_regions_must_cover_each_loop_once(self) -> None:
        ir = _example("plate_with_hole.ir.json")
        ir["entities"]["profile_hole_p1"]["params"]["regions"][0]["holes"] = []
        errors = validate_ir(ir, schema=False)
        self.assertTrue(any("each input loop must appear exactly once" in error for error in errors))
        with self.assertRaisesRegex(ValueError, "invalid IR"):
            ir2code.generate(ir, "bad_regions")

    def test_profile_region_roles_must_match_geometry(self) -> None:
        ir = _example("plate_with_hole.ir.json")
        ir["entities"]["loop_hole_inner"]["params"]["is_outer"] = True
        errors = validate_ir(ir, schema=False)
        self.assertTrue(any("disagrees with profile regions" in error for error in errors))
        ir["entities"]["loop_hole_inner"]["params"]["is_outer"] = False
        ir["entities"]["profile_hole_p1"]["params"]["regions"] = [
            {"outer": "loop_hole_inner", "holes": ["loop_hole_outer"]}
        ]
        errors = validate_ir(ir, schema=False)
        self.assertTrue(any("does not match loop containment" in error for error in errors))

    def test_boolean_errors_are_not_silenced(self) -> None:
        ir = _example("plate_with_hole.ir.json")
        cut = copy.deepcopy(ir["entities"]["extrude_hole"])
        cut["params"]["operation"] = "cut"
        ir["entities"]["extrude_cut"] = cut
        ir["sequence"].append("extrude_cut")
        ir["results"] = ["extrude_cut"]
        source = Emitter(ir, "cut").emit()
        self.assertIn("operation='cut'", source)
        self.assertIn("no body to modify", source)
        self.assertNotIn("except Exception", source)
        ast.parse(source)

    def test_non_new_feature_needs_prior_body(self) -> None:
        ir = _example("plate_with_hole.ir.json")
        ir["entities"]["extrude_hole"]["params"]["operation"] = "join"
        with self.assertRaisesRegex(ValueError, "must create a new body"):
            ir2code.generate(ir, "orphan_join")

    def test_sequence_must_include_all_features(self) -> None:
        ir = _example("plate_with_hole.ir.json")
        ir["sequence"].remove("extrude_hole")
        self.assertTrue(any("missing feature.extrude" in error for error in validate_ir(ir, schema=False)))

    def test_results_identify_final_body_set_state(self) -> None:
        ir = _example("plate_with_hole.ir.json")
        ir["results"] = ["profile_hole_p1"]
        self.assertTrue(any("final body-set state" in error for error in validate_ir(ir, schema=False)))


class VerificationLabelsTests(unittest.TestCase):
    def test_batch_preserves_artifacts_and_failure_stage(self) -> None:
        source = ROOT / "datasets" / "DeepCAD" / "cad_json" / "0000" / "00000007.json"
        if not source.is_file():
            self.skipTest("DeepCAD sample is not installed")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = root / "inputs"
            inputs.mkdir()
            (inputs / source.name).write_bytes(source.read_bytes())
            with patch.object(batch_verify, "generate", side_effect=ValueError("deliberate emit error")):
                summary = batch_verify.run_batch(inputs, root / "batch", limit=None)
            entry = summary["results"][0]
            self.assertEqual(entry["status"], "emitter_error")
            self.assertEqual(entry["stage"], "emitter")
            self.assertTrue(Path(entry["paths"]["ir"]).is_file())
            with (root / "batch" / "batch_results.csv").open(encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["stage"], "emitter")
            self.assertEqual(row["ir"], entry["paths"]["ir"])

    def test_self_consistency_is_not_independent_acceptance(self) -> None:
        source = ROOT / "datasets" / "DeepCAD" / "cad_json" / "0000" / "00000007.json"
        if not source.is_file():
            self.skipTest("DeepCAD sample is not installed")
        metrics = {
            "status": "ok", "is_valid": True, "body_count": 1, "solid_count": 1,
            "volume": 661499.1269, "area": 66617.2418,
            "bbox": [-91.04851, -91.04851, 0, 91.04851, 91.04851, 25.4],
            "reference_status": "ok", "reference_volume": 661499.1269,
            "symmetric_difference_volume": 0.0,
        }
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            with patch.object(verify_deepcad, "generate", return_value="def build(): pass\n"), patch.object(
                verify_deepcad, "_run_worker", return_value=metrics
            ):
                report = verify_deepcad.verify(source, output / "candidate.ftc.py", output)
                self.assertEqual(report["quality"], "self_consistent")
                self.assertEqual(report["reference_kind"], "generated_from_same_ir")
                self.assertTrue((output / "gt.ir.json").is_file())
                self.assertTrue((output / "verify.json").is_file())
                independent = verify_deepcad.verify(
                    source, output / "candidate.ftc.py", output / "independent",
                    reference_step=output / "official.step",
                )
                self.assertEqual(independent["quality"], "accepted")
                self.assertEqual(independent["reference_kind"], "independent_step")

    def test_independent_boolean_failure_cannot_be_accepted(self) -> None:
        source = ROOT / "datasets" / "DeepCAD" / "cad_json" / "0000" / "00000007.json"
        if not source.is_file():
            self.skipTest("DeepCAD sample is not installed")
        metrics = {
            "status": "ok", "is_valid": True, "body_count": 1, "solid_count": 1,
            "volume": 661499.1269, "area": 66617.2418,
            "bbox": [-91.04851, -91.04851, 0, 91.04851, 91.04851, 25.4],
            "reference_status": "ok", "reference_volume": 661499.1269,
            "symmetric_difference_volume": 0.0,
        }
        candidate = {**metrics, "reference_status": "error",
                     "reference_error": "symmetric difference failed"}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            with patch.object(verify_deepcad, "generate", return_value="def build(): pass\n"), patch.object(
                verify_deepcad, "_run_worker", side_effect=[metrics, metrics, candidate]
            ):
                report = verify_deepcad.verify(
                    source, output / "candidate.ftc.py", output,
                    reference_step=output / "official.step",
                )
                self.assertEqual(report["quality"], "rejected")
                self.assertIn("reference_error", report["reasons"])


if __name__ == "__main__":
    unittest.main()
