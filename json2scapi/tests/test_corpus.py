"""Readable-only output and reference acceptance regressions."""

from __future__ import annotations

import importlib.util
import json
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from json2scapi.adapters.deepcad import convert  # noqa: E402


def _ir(stem: str):
    path = ROOT / "datasets" / "DeepCAD" / "cad_json" / "0000" / f"{stem}.json"
    return convert(json.loads(path.read_text(encoding="utf-8", errors="replace")), str(path))


@unittest.skipUnless(importlib.util.find_spec("jsonschema"), "schema validator not installed")
class ReadableSourceTests(unittest.TestCase):
    def test_export_contains_only_train_ready_and_current_provenance(self):
        import json2scapi.pipeline.corpus as build_corpus

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            code = root / "readable.py"
            symbols = root / "symbols.json"
            code.write_text("def build(): pass\n", encoding="utf-8")
            symbols.write_text("{}\n", encoding="utf-8")

            def fake_case(source, out_dir, **kwargs):
                return {"stem": source.stem,
                        "status": "train_ready" if source.stem == "ready" else "provisional",
                        "stage": "independent_comparison", "reference_evaluation": "accepted",
                        "paths": {"source": str(source), "readable_code": str(code),
                                  "symbols": str(symbols)}}

            with patch.object(build_corpus, "run_case", side_effect=fake_case):
                manifest = build_corpus.run_corpus(
                    [root / "ready.json", root / "pending.json"], root / "corpus"
                )
            self.assertEqual(manifest["counts"], {"train_ready": 1, "provisional": 1})
            exported = [json.loads(line) for line in
                        (root / "corpus" / "train_ready.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["stem"] for row in exported], ["ready"])
            self.assertEqual(exported[0]["source"], str(root / "ready.json"))
            self.assertEqual(exported[0]["report"], str(root / "corpus" / "ready" / "report.json"))

    def test_log_only_discards_case_artifacts_and_writes_analysis(self):
        import json2scapi.pipeline.corpus as build_corpus

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def fake_case(source, out_dir, **kwargs):
                out_dir.mkdir(parents=True, exist_ok=True)
                code = out_dir / "model.readable.ftc.py"
                code.write_text("def build(): pass\n", encoding="utf-8")
                return {
                    "stem": source.stem,
                    "status": "train_ready" if source.stem == "ready" else "rejected",
                    "stage": "independent_comparison",
                    "reference_evaluation": "accepted" if source.stem == "ready" else "mismatch",
                    "reasons": [] if source.stem == "ready" else ["independent_geometry_mismatch"],
                    "paths": {"source": str(source), "readable_code": str(code)},
                    "readable_version": 2,
                    "code_structure": {"lines": 1},
                }

            with patch.object(build_corpus, "run_case", side_effect=fake_case):
                manifest = build_corpus.run_corpus(
                    [root / "ready.json", root / "bad.json"], root / "corpus", log_only=True
                )

            self.assertEqual(manifest["artifact_mode"], "logs-only")
            self.assertFalse((root / "corpus" / "ready").exists())
            self.assertFalse((root / "corpus" / "bad").exists())
            self.assertFalse((root / "corpus" / "train_ready.jsonl").exists())
            self.assertTrue((root / "corpus" / "train_ready_index.jsonl").is_file())
            self.assertTrue((root / "corpus" / "errors.csv").is_file())
            self.assertEqual(manifest["results"][0]["paths"], {"source": str(root / "ready.json")})
            index = (root / "corpus" / "train_ready_index.jsonl").read_text(encoding="utf-8")
            self.assertIn('"stem": "ready"', index)
            self.assertNotIn('"stem": "bad"', index)

    def test_only_readable_is_available_and_deterministic(self):
        from json2scapi.codegen.api import generate, generate_readable_with_metadata, main

        ir = _ir("00000123")
        self.assertEqual(generate(ir, "00000123"), generate(ir, "00000123", style="readable"))
        with self.assertRaisesRegex(ValueError, "unsupported emitter style"):
            generate(ir, "00000123", style="faithful")
        first, metadata = generate_readable_with_metadata(ir, "00000123")
        self.assertEqual((first, metadata), generate_readable_with_metadata(ir, "00000123"))
        self.assertIn("FEATURE_01_CIRCLE_01_RADIUS_MM", first)
        self.assertIn("FEATURE_01_DISTANCE_MM", first)
        self.assertIn("# Step 02: join one_side", first)
        self.assertNotIn("FEATURE_02_DISTANCE_MM", first)
        self.assertIn("apply_feature_01", metadata["symbols"])
        self.assertEqual(metadata["readable_version"], 3)
        with tempfile.TemporaryDirectory() as temporary:
            ir_path = Path(temporary) / "model.ir.json"
            output = Path(temporary) / "model.readable.ftc.py"
            ir_path.write_text(json.dumps(ir), encoding="utf-8")
            self.assertEqual(main(["--input", str(ir_path), "--style", "readable",
                                   "--stem", "00000123", "--out", str(output)]), 0)
            self.assertEqual(output.read_text(encoding="utf-8"), first)
            self.assertEqual(json.loads((Path(temporary) / "model.readable.symbols.json").read_text()), metadata)


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ("OCP", "jsonschema", "scipy")),
                     "CAD acceptance dependencies not installed")
class CorpusGeometryTests(unittest.TestCase):
    def test_only_independently_accepted_code_is_exported(self):
        import json2scapi.pipeline.corpus as build_corpus

        source = ROOT / "datasets" / "DeepCAD" / "cad_json" / "0000" / "00000007.json"
        with tempfile.TemporaryDirectory() as temporary:
            manifest = build_corpus.run_corpus([source], Path(temporary))
            self.assertEqual(manifest["counts"], {"train_ready": 1})
            lines = (Path(temporary) / "train_ready.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            exported = json.loads(lines[0])
            self.assertIn("FEATURE_01_CIRCLE_01_RADIUS_MM", exported["code"])
            self.assertTrue(Path(exported["symbols"]).is_file())
            self.assertEqual(manifest["results"][0]["reference_evaluation"], "accepted")
            self.assertEqual(manifest["code_style"], "readable")
            case = Path(temporary) / source.stem
            self.assertFalse((case / "model.faithful.ftc.py").exists())
            self.assertFalse((case / "faithful.step").exists())
            self.assertNotIn("faithful_vs_readable", manifest["results"][0]["metrics"])
            with self.assertRaises(FileExistsError):
                build_corpus.run_corpus([source], Path(temporary))

    def test_missing_reference_process_stays_provisional(self):
        import json2scapi.pipeline.corpus as build_corpus

        source = ROOT / "datasets" / "DeepCAD" / "cad_json" / "0000" / "00000007.json"
        original_run = build_corpus.subprocess.run

        def fail_reference(command, *args, **kwargs):
            if str(build_corpus.REFERENCE_SCRIPT) in command:
                raise FileNotFoundError("missing adapter")
            return original_run(command, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            build_corpus.subprocess, "run", side_effect=fail_reference
        ):
            manifest = build_corpus.run_corpus([source], Path(temporary))
            self.assertEqual(manifest["counts"], {"provisional": 1})
            self.assertEqual(manifest["results"][0]["reference_evaluation"], "reference_error")
            self.assertEqual((Path(temporary) / "train_ready.jsonl").read_text(encoding="utf-8"), "")

    def test_editing_circle_and_arc_dimensions_changes_model(self):
        from json2scapi.codegen.api import generate

        for stem, parameter in (("00000007", "FEATURE_01_CIRCLE_01_RADIUS_MM"),
                                ("00000061", "FEATURE_01_ARC_01_RADIUS_MM")):
            code = generate(_ir(stem), stem, style="readable")
            with tempfile.TemporaryDirectory() as temporary:
                original = Path(temporary) / "original.py"
                edited = Path(temporary) / "edited.py"
                original.write_text(code, encoding="utf-8")
                line = next(line for line in code.splitlines() if line.startswith(parameter + " = "))
                value = float(line.split("=", 1)[1].strip())
                edited.write_text(code.replace(line, f"{parameter} = {value + 0.1}", 1), encoding="utf-8")
                before = runpy.run_path(str(original))["build"]().get_volume()
                after = runpy.run_path(str(edited))["build"]().get_volume()
            self.assertNotAlmostEqual(before, after, delta=1e-5)

    def test_bad_readable_code_never_enters_training_export(self):
        import json2scapi.pipeline.corpus as build_corpus
        from json2scapi.codegen.api import generate_readable_with_metadata

        source = ROOT / "datasets" / "DeepCAD" / "cad_json" / "0000" / "00000007.json"

        def corrupt(ir, stem):
            code, metadata = generate_readable_with_metadata(ir, stem)
            return code.replace("FEATURE_01_CIRCLE_01_RADIUS_MM = 91.04851",
                                "FEATURE_01_CIRCLE_01_RADIUS_MM = 92.04851", 1), metadata

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            build_corpus, "generate_readable_with_metadata", side_effect=corrupt
        ):
            result = build_corpus.run_corpus([source], Path(temporary))
            self.assertEqual(result["counts"], {"rejected": 1})
            self.assertEqual(result["results"][0]["stage"], "independent_comparison")
            self.assertEqual((Path(temporary) / "train_ready.jsonl").read_text(encoding="utf-8"), "")

    def test_official_reference_rejects_nonzero_taper(self):
        from importlib.util import module_from_spec, spec_from_file_location

        path = ROOT / "deepcad_official" / "local_adapter" / "json2step_ocp.py"
        spec = spec_from_file_location("deepcad_ocp_test", path)
        assert spec is not None and spec.loader is not None
        adapter = module_from_spec(spec)
        spec.loader.exec_module(adapter)
        source = ROOT / "datasets" / "DeepCAD" / "cad_json" / "0000" / "00000007.json"
        data = json.loads(source.read_text(encoding="utf-8"))
        feature_id = next(item["entity"] for item in data["sequence"] if item["type"] == "ExtrudeFeature")
        data["entities"][feature_id]["extent_one"]["taper_angle"]["value"] = 0.1
        with tempfile.TemporaryDirectory() as temporary:
            source_path = Path(temporary) / "taper.json"
            source_path.write_text(json.dumps(data), encoding="utf-8")
            report = adapter.export(source_path, Path(temporary) / "reference.step",
                                    Path(temporary) / "reference.json")
            self.assertEqual(report["status"], "reference_unsupported")
            self.assertFalse((Path(temporary) / "reference.step").exists())

    def test_official_reference_handles_arc_and_rejects_bad_step(self):
        from importlib.util import module_from_spec, spec_from_file_location

        path = ROOT / "deepcad_official" / "local_adapter" / "json2step_ocp.py"
        spec = spec_from_file_location("deepcad_ocp_roundtrip_test", path)
        assert spec is not None and spec.loader is not None
        adapter = module_from_spec(spec)
        spec.loader.exec_module(adapter)
        with tempfile.TemporaryDirectory() as temporary:
            for stem, expected in (("00000061", "ok"), ("00000062", "reference_error")):
                source = ROOT / "datasets" / "DeepCAD" / "cad_json" / "0000" / f"{stem}.json"
                report = adapter.export(source, Path(temporary) / f"{stem}.step",
                                        Path(temporary) / f"{stem}.json")
                self.assertEqual(report["status"], expected)


if __name__ == "__main__":
    unittest.main()
