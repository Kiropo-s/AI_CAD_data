"""Regression tests for DeepCAD loop nesting and FTC emission."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from json2scapi.codegen.readable import StructuredEmitter as Emitter  # noqa: E402
from json2scapi.adapters.deepcad import convert  # noqa: E402
from json2scapi.ir.regions import profile_regions  # noqa: E402


def circles(*specs):
    entities = {}
    loop_ids = []
    for i, (x, y, radius, flag) in enumerate(specs):
        curve_id, loop_id = f"c{i}", f"l{i}"
        entities[curve_id] = {"op": "sketch.circle", "params": {"center": [x, y], "radius": radius}}
        entities[loop_id] = {"op": "sketch.loop", "inputs": [curve_id],
                             "params": {"is_outer": flag}}
        loop_ids.append(loop_id)
    return {"inputs": loop_ids}, entities


class ProfileRegionTests(unittest.TestCase):
    def test_hole_mislabeled_as_outer(self):
        profile, entities = circles((0, 0, 10, True), (0, 0, 5, True))
        self.assertEqual(profile_regions(profile, entities), [("l0", ["l1"])])

    def test_disjoint_islands(self):
        profile, entities = circles((-10, 0, 3, True), (10, 0, 3, True))
        self.assertEqual(profile_regions(profile, entities), [("l0", []), ("l1", [])])

    def test_island_inside_hole(self):
        profile, entities = circles((0, 0, 10, True), (0, 0, 6, True), (0, 0, 2, True))
        self.assertEqual(profile_regions(profile, entities), [("l0", ["l1"]), ("l2", [])])

    def test_intersecting_contours_fail(self):
        profile, entities = circles((0, 0, 10, True), (6, 0, 8, True))
        with self.assertRaisesRegex(ValueError, "overlap"):
            profile_regions(profile, entities)

    def test_real_deepcad_ring_has_hole(self):
        path = ROOT / "datasets" / "DeepCAD" / "cad_json" / "0000" / "00000123.json"
        if not path.is_file():
            self.skipTest("DeepCAD sample not present")
        ir = convert(json.loads(path.read_text(encoding="utf-8")), str(path))
        profile_id = next(key for key, entity in ir["entities"].items()
                          if entity["op"] == "sketch.profile" and len(entity["inputs"]) == 2)
        loop_roles = [(ir["entities"][ref]["params"]["is_outer"],
                       ir["entities"][ref]["source"]["deepcad_is_outer"])
                      for ref in ir["entities"][profile_id]["inputs"]]
        self.assertEqual(loop_roles, [(True, True), (False, True)])
        regions = profile_regions(ir["entities"][profile_id], ir["entities"], ir["tolerance"]["length"])
        self.assertEqual(len(regions), 1)
        self.assertEqual(len(regions[0][1]), 1)
        source = Emitter(ir, "00000123").emit()
        self.assertIn("inner_profiles=", source)


if __name__ == "__main__":
    unittest.main()
