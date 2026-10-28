"""Structure, ordering and provenance checks for standalone readable programs."""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from json2scapi.adapters.deepcad import convert
from _legacy_emitter import Emitter  # frozen oracle only; production is readable-only
from json2scapi.codegen.readable import CodePlan, StructuredEmitter
from json2scapi.ir.validation import validate_ir


def sample(stem):
    source = ROOT / "datasets/DeepCAD/cad_json/0000" / f"{stem}.json"
    return convert(json.loads(source.read_text(encoding="utf-8", errors="replace")), str(source))


class ReadableStructureTests(unittest.TestCase):
    def test_build_contains_only_explicit_feature_flow(self):
        ir = sample("00000069")
        emitter = StructuredEmitter(ir, "sample")
        source = emitter.emit()
        tree = ast.parse(source)
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        build = functions["build"]
        calls = [node.func.id for node in ast.walk(build) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id.startswith("apply_feature_")]
        self.assertEqual(calls, [feature.function for feature in emitter.plan.features])
        self.assertFalse(any(isinstance(node, ast.Attribute) and node.attr.startswith("add_")
                             for node in ast.walk(build)))
        self.assertIn("def _add_closed_polyline", source)
        self.assertNotIn("0 * arc_", source)
        self.assertEqual(source, StructuredEmitter(ir, "sample").emit())
        self.assertEqual(emitter.metadata["readable_version"], 3)
        for profile in emitter.plan.profiles.values():
            self.assertIn(profile.function, functions)
            self.assertFalse(functions[profile.function].args.defaults)
        self.assertEqual([node.module for node in tree.body if isinstance(node, ast.ImportFrom)], [])
        self.assertEqual([alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names],
                         ["simplecadapi"])

    def test_source_optional_and_noncontiguous_groups_stay_separate(self):
        ir = sample("00000069")
        steps = [key for key in ir["sequence"] if ir["entities"][key]["op"] == "feature.extrude"]
        for entity in ir["entities"].values():
            entity.pop("source", None)
        self.assertEqual(validate_ir(ir, schema=False), [])

        self.assertEqual(len(CodePlan(ir).features), len(steps))
        for index, step in enumerate(steps):
            ir["entities"][step]["source"] = {"format": "custom", "entity_id": "a" if index % 2 == 0 else "b"}
        self.assertEqual(len(CodePlan(ir).features), len(steps))

    def test_equal_unrelated_dimensions_are_not_aliased(self):
        ir = sample("00001705")
        steps = [key for key in ir["sequence"] if ir["entities"][key]["op"] == "feature.extrude"]
        ir["entities"][steps[1]]["params"]["distance"] = ir["entities"][steps[0]]["params"]["distance"]
        plan = CodePlan(ir)
        self.assertIsNot(plan.features[0].dimensions[steps[0], "distance"],
                         plan.features[1].dimensions[steps[1], "distance"])
        circles = [node for node in ir["entities"].values() if node["op"] == "sketch.circle"]
        for node in circles:
            node["params"]["radius"] = 5
        radii = [p for p in CodePlan(ir).parameters if p.kind == "circle_radius"]
        self.assertEqual(len(radii), 2)
        self.assertNotEqual(radii[0].name, radii[1].name)

    def test_provenance_fields_are_validated_but_not_required(self):
        ir = sample("00000123")
        step = next(key for key in ir["sequence"] if ir["entities"][key]["op"] == "feature.extrude")
        ir["entities"][step]["source"]["profile_index"] = True
        self.assertTrue(any("profile_index" in error for error in validate_ir(ir, schema=False)))
        ir["entities"][step]["source"]["profile_index"] = -1
        self.assertTrue(any("profile_index" in error for error in validate_ir(ir, schema=False)))
        steps = [key for key in ir["sequence"] if ir["entities"][key]["op"] == "feature.extrude"]
        for key in steps:
            ir["entities"][key]["source"]["profile_index"] = 0
        self.assertTrue(any("must increase" in error for error in validate_ir(ir, schema=False)))
        for entity in ir["entities"].values():
            entity.pop("source", None)
        self.assertEqual(validate_ir(ir, schema=False), [])


        ir = sample("00000069")
        steps = [key for key in ir["sequence"] if ir["entities"][key]["op"] == "feature.extrude"]
        ir["entities"][steps[0]]["source"]["profile_index"] = 2
        ir["entities"][steps[1]]["source"].pop("profile_index")
        ir["entities"][steps[2]]["source"]["profile_index"] = 1
        self.assertTrue(any("must increase" in error for error in validate_ir(ir, schema=False)))

    def test_conflicting_arc_auxiliary_is_rejected(self):
        ir = sample("00000061")
        for entity in ir["entities"].values():
            if entity["op"] == "sketch.arc":
                entity["params"]["radius"] += 0.01
        with self.assertRaisesRegex(ValueError, "auxiliary radius conflicts"):
            StructuredEmitter(ir, "arc")

    def test_functions_preserve_extrusion_and_boolean_trace(self):
        # Compare the actual executed operations, not just generated text.
        for stem in ("00000069", "00000123", "00001705"):
            ir = sample(stem)
            for extent in ("one_side", "symmetric", "two_sides"):
                candidate = copy.deepcopy(ir)
                for node in candidate["entities"].values():
                    if node["op"] == "feature.extrude":
                        node["params"]["extent"] = extent
                        if extent == "two_sides":
                            node["params"]["distance2"] = 1.25
                later_steps = [key for key in candidate["sequence"]
                               if candidate["entities"][key]["op"] == "feature.extrude"][1:]
                for index, key in enumerate(later_steps):
                    candidate["entities"][key]["params"]["operation"] = ("cut", "intersect", "new", "join")[index % 4]
                traces = []
                for code in (Emitter(candidate, stem).emit(), StructuredEmitter(candidate, stem).emit()):
                    fake = ModuleType("simplecadapi")
                    trace = []

                    def unchanged(*args, **kwargs):
                        return kwargs.get("sketch", args[0] if args else object())

                    fake.make_sketch_rsketch = lambda **kwargs: object()
                    for name in ("add_point_rsketch", "add_line_rsketch", "add_circle_rsketch", "add_arc_rsketch"):
                        setattr(fake, name, unchanged)
                    fake.make_face_from_sketch_rface = lambda **kwargs: object()

                    def extrude(**kwargs):
                        trace.append(("extrude", kwargs["direction"], kwargs["distance"]))
                        return object()

                    def boolean(*, left, right, operation):
                        trace.append((operation, len(left), len(right)))
                        return [object()]

                    def union(tools, **kwargs):
                        trace.append(("tool_union", len(tools)))
                        return object()

                    fake.extrude_rsolid = extrude
                    fake.boolean_rsolidlist = boolean
                    fake.union_rsolid = union
                    fake.make_assembly_rassembly = lambda *args: object()
                    fake.make_part_rpart = lambda *args: object()
                    fake.add_component_rassembly = lambda *args: object()
                    fake.identity_placement_rplacement = lambda: None
                    with patch.dict(sys.modules, simplecadapi=fake):
                        namespace = {}
                        exec(compile(code, "<generated>", "exec"), namespace)
                        namespace["build"]()
                    traces.append(trace)
                self.assertEqual(traces[0], traces[1], (stem, extent))


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ("OCP", "scipy")), "CAD dependencies missing")
class ReadableGeometryTests(unittest.TestCase):
    def test_build_reads_edited_top_level_distance_at_call_time(self):
        from OCP.BRepCheck import BRepCheck_Analyzer

        namespace = {}
        exec(StructuredEmitter(sample("00000007"), "edited").emit(), namespace)
        before = namespace["build"]().get_volume()
        namespace["FEATURE_01_DISTANCE_MM"] += 1
        model = namespace["build"]()
        self.assertTrue(BRepCheck_Analyzer(model.wrapped).IsValid())
        self.assertGreater(model.get_volume(), before)

    def test_region_holes_and_two_sided_geometry(self):
        from json2scapi.evaluation.metrics import compare_shapes
        # Sample 123 exercises multiple profiles; synthetic fixtures cover regions/holes.
        fixtures = [sample("00000123")]
        islands = sample("00000007")
        curve = next(node for node in islands["entities"].values() if node["op"] == "sketch.circle")
        profile = next(node for node in islands["entities"].values() if node["op"] == "sketch.profile")
        islands["entities"]["island_circle"] = {
            **copy.deepcopy(curve), "source": {},
            "params": {"center": [300, 0], "radius": 10},
        }
        islands["entities"]["island_loop"] = {
            "type": "SketchLoop", "op": "sketch.loop", "inputs": ["island_circle"],
            "params": {"closed": True, "is_outer": True},
        }
        profile["inputs"].append("island_loop")
        profile["params"]["regions"].append({"outer": "island_loop", "holes": []})
        self.assertEqual(validate_ir(islands, schema=False), [])
        fixtures.append(islands)
        for name in ("plate_with_hole.ir.json", "line_arc_plate.ir.json"):
            fixtures.append(json.loads((ROOT / "json2scapi/examples" / name).read_text(encoding="utf-8")))
        for ir in fixtures:
            for extent in ("one_side", "symmetric", "two_sides"):
                candidate = copy.deepcopy(ir)
                for node in candidate["entities"].values():
                    if node["op"] == "feature.extrude":
                        node["params"]["extent"] = extent
                        if extent == "two_sides":
                            node["params"]["distance2"] = 1.25
                shapes = []
                for code in (Emitter(candidate, "fixture").emit(), StructuredEmitter(candidate, "fixture").emit()):
                    namespace = {}
                    exec(compile(code, "<generated>", "exec"), namespace)
                    namespace["build"]()
                    shapes.append(namespace["BODIES"])
                # BODIES avoid assembly wrappers for multi-solid results.
                from simplecadapi.kernel.ocp_export import make_compound
                result = compare_shapes(make_compound([body.wrapped for body in shapes[0]]),
                                        make_compound([body.wrapped for body in shapes[1]]), cd_samples=8192)
                self.assertTrue(result["passed"], result)


if __name__ == "__main__":
    unittest.main()
