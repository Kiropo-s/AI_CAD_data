"""Integration checks for package resources and command compatibility."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from json2scapi.codegen.api import generate_readable_with_metadata
from json2scapi.runtime.paths import PACKAGE_DIR, WORKSPACE_DIR, SCHEMA_PATH, OUT_DIR, REFERENCE_SCRIPT


class ModuleLayoutTests(unittest.TestCase):
    def test_resources_still_point_to_project_locations(self):
        self.assertEqual(PACKAGE_DIR, ROOT / "json2scapi")
        self.assertEqual(WORKSPACE_DIR, ROOT)
        self.assertEqual(OUT_DIR, PACKAGE_DIR / "out")
        self.assertEqual(SCHEMA_PATH, PACKAGE_DIR / "schema" / "ir-0.1.schema.json")
        self.assertTrue(SCHEMA_PATH.is_file())
        self.assertEqual(REFERENCE_SCRIPT, ROOT / "deepcad_official" / "local_adapter" / "json2step_ocp.py")

    def test_compatibility_entries_share_module_state(self):
        entries = {
            "deepcad_to_ir": "adapters.deepcad", "validate_ir": "ir.validation",
            "ir2code": "codegen.api", "build_corpus": "pipeline.corpus",
            "run_shape_regression": "pipeline.regression", "local_worker": "runtime.worker",
            "batch_verify": "legacy.batch_verify", "compare_minimal": "legacy.compare_minimal",
            "deepcad_render": "legacy.deepcad_render", "verify_deepcad": "legacy.verify_deepcad",
        }
        for entry, implementation in entries.items():
            with self.subTest(entry=entry):
                self.assertIs(importlib.import_module("json2scapi." + entry),
                              importlib.import_module("json2scapi." + implementation))

    def test_script_and_module_generation_agree_from_different_working_directories(self):
        source = PACKAGE_DIR / "examples" / "line_arc_plate.ir.json"
        payload = json.loads(source.read_text(encoding="utf-8"))
        expected_code, expected_symbols = generate_readable_with_metadata(payload, "layout_check")
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            commands = [
                ([sys.executable, str(PACKAGE_DIR / "ir2code.py")], folder),
                ([sys.executable, "-m", "json2scapi.codegen.api"], ROOT),
            ]
            for index, (command, cwd) in enumerate(commands):
                output = folder / str(index) / "model.readable.ftc.py"
                result = subprocess.run(command + ["--input", str(source), "--out", str(output),
                                        "--stem", "layout_check"], cwd=cwd, capture_output=True,
                                        text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertEqual(output.read_text(encoding="utf-8"), expected_code)
                symbols = json.loads(output.with_name("model.readable.symbols.json").read_text(encoding="utf-8"))
                self.assertEqual(symbols, expected_symbols)
                self.assertTrue(output.with_name("model.readable.prepared.ir.json").is_file())

    def test_codegen_import_does_not_eagerly_load_cad_kernel(self):
        code = (
            "import json2scapi.adapters.deepcad, json2scapi.codegen.api, json2scapi.ir.validation; "
            "import sys; assert not any(n == 'OCP' or n.startswith('OCP.') or "
            "n == 'simplecadapi' or n.startswith('simplecadapi.') for n in sys.modules)"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
