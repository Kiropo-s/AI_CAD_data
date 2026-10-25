"""Structured, standalone SCAPI programs. This plan is internal, not a CAD IR."""
from __future__ import annotations

import ast
import math
from dataclasses import dataclass, field
from typing import Any, Mapping

from .sketch_builder import SketchBuilder, _slug, fmt_literal, fmt_num, fmt_vec


@dataclass
class Parameter:
    name: str
    argument: str
    value: float
    kind: str
    paths: list[dict[str, str]] = field(default_factory=list)


@dataclass
class Profile:
    ir_id: str
    number: int
    owner: int
    parameters: list[Parameter] = field(default_factory=list)
    curves: dict[str, Parameter] = field(default_factory=dict)
    point_expressions: dict[tuple[float, float], tuple[str, str]] = field(default_factory=dict)

    @property
    def function(self) -> str:
        return f"make_profile_{self.number:02d}"


@dataclass
class Feature:
    number: int
    steps: list[str]
    parameters: list[Parameter] = field(default_factory=list)
    dimensions: dict[tuple[str, str], Parameter] = field(default_factory=dict)

    @property
    def function(self) -> str:
        return f"apply_feature_{self.number:02d}"


def source_key(entity: Mapping[str, Any]) -> tuple[str, str] | None:
    source = entity.get("source", {})
    if source.get("format") and source.get("entity_id"):
        return source["format"], source["entity_id"]
    return None


def _point_key(point) -> tuple[float, float]:
    return tuple(float(value) for value in point)


class CodePlan:
    """Resolve dependencies, contiguous source groups and proven dimension aliases."""

    def __init__(self, payload: Mapping[str, Any], original_entities=None):
        self.original_entities = original_entities or payload["entities"]
        self.entities = payload["entities"]
        steps = [key for key in payload["sequence"]
                 if self.entities[key]["op"] == "feature.extrude"]
        if not steps or payload["results"] != [steps[-1]]:
            raise ValueError("readable emitter requires the final extrude body-set state")
        self.features: list[Feature] = []
        self.profiles: dict[str, Profile] = {}
        self.parameters: list[Parameter] = []
        self._curve_parameters: dict[tuple, Parameter] = {}
        self._curve_numbers = {"circle": 0, "arc": 0}
        previous = None
        for step in steps:
            key = source_key(self.entities[step])
            if not self.features or key is None or key != previous:
                self.features.append(Feature(len(self.features) + 1, []))
            self.features[-1].steps.append(step)
            previous = key
        for feature in self.features:
            for step in feature.steps:
                profile_id = self.entities[step]["inputs"][0]
                if profile_id not in self.profiles:
                    profile = Profile(profile_id, len(self.profiles) + 1, feature.number)
                    self.profiles[profile_id] = profile
                    self._plan_profile(profile)
                for parameter in self.profiles[profile_id].parameters:
                    self._append_parameter(feature.parameters, parameter)
            self._plan_dimensions(feature)

    @staticmethod
    def _append_parameter(parameters: list[Parameter], parameter: Parameter) -> None:
        if all(item is not parameter for item in parameters):
            parameters.append(parameter)

    def _plan_dimensions(self, feature: Feature) -> None:
        aliases: dict[tuple[str, float], Parameter] = {}
        for index, step in enumerate(feature.steps, 1):
            params = self.entities[step]["params"]
            fields = [("distance", "DISTANCE_MM", "distance")]
            if params["extent"] == "two_sides":
                fields.append(("distance2", "REVERSE_DISTANCE_MM", "reverse_distance"))
            if abs(float(params.get("taper", 0))) > 1e-12:
                fields.append(("taper", "TAPER_DEG", "taper"))
            for key, suffix, argument in fields:
                value = float(params[key])
                alias = (key, value)
                parameter = aliases.get(alias)
                if parameter is None:
                    extra = f"_STEP_{index:02d}" if any(k == key for k, _ in aliases) else ""
                    parameter = Parameter(f"FEATURE_{feature.number:02d}{extra}_{suffix}",
                                          argument + (f"_step_{index:02d}" if extra else ""),
                                          value, key)
                    self.parameters.append(parameter)
                    feature.parameters.append(parameter)
                    aliases[alias] = parameter
                parameter.paths.append({"ir_id": step, "path": f"params.{key}"})
                feature.dimensions[step, key] = parameter

    def _plan_profile(self, profile: Profile) -> None:
        node = self.entities[profile.ir_id]
        curve_ids = list(dict.fromkeys(curve for loop in node["inputs"]
                                       for curve in self.entities[loop]["inputs"]))
        arc_uses: dict[tuple, int] = {}
        for curve_id in curve_ids:
            curve = self.entities[curve_id]
            if curve["op"] == "sketch.arc":
                for end in ("start", "end"):
                    point = _point_key(curve["params"][end])
                    arc_uses[point] = arc_uses.get(point, 0) + 1
        for curve_id in curve_ids:
            curve = self.entities[curve_id]
            kind = curve["op"].removeprefix("sketch.")
            if kind not in {"circle", "arc"}:
                continue
            params = curve["params"]
            if kind == "arc":
                if "radius" not in self.original_entities[curve_id]["params"]:
                    continue
                if "center" not in params or "radius" not in params:
                    continue
                center, radius = params["center"], float(params["radius"])
                if radius <= 0 or any(arc_uses[_point_key(params[end])] > 1 for end in ("start", "end")):
                    continue
                if any(not math.isclose(math.dist(params[end], center), radius,
                                        rel_tol=1e-9, abs_tol=1e-9)
                       for end in ("start", "mid", "end")):
                    continue
            source = curve.get("source", {})
            origin = source_key(curve)
            key = (origin, source["curve_id"], kind, float(params["radius"])) if (
                origin is not None and source.get("curve_id")) else (curve_id, kind)
            parameter = self._curve_parameters.get(key)
            if parameter is None:
                self._curve_numbers[kind] += 1
                number = self._curve_numbers[kind]
                parameter = Parameter(f"FEATURE_{profile.owner:02d}_{kind.upper()}_{number:02d}_RADIUS_MM",
                                      f"{kind}_{number:02d}_radius", float(params["radius"]),
                                      f"{kind}_radius")
                self.parameters.append(parameter)
                self._curve_parameters[key] = parameter
            path = {"ir_id": curve_id, "path": "params.radius"}
            if path not in parameter.paths:
                parameter.paths.append(path)
            profile.curves[curve_id] = parameter
            self._append_parameter(profile.parameters, parameter)
            if kind == "arc":
                center, radius = params["center"], float(params["radius"])
                for end in ("start", "end"):
                    expressions = []
                    for value, origin_value in zip(params[end], center):
                        factor = (float(value) - float(origin_value)) / radius
                        delta = f"({parameter.argument} - {fmt_num(radius)})"
                        if abs(factor) < 1e-15:
                            expression = fmt_num(value)
                        elif factor == 1:
                            expression = f"{fmt_num(value)} + {delta}"
                        elif factor == -1:
                            expression = f"{fmt_num(value)} - {delta}"
                        else:
                            expression = f"{fmt_num(value)} + {fmt_num(factor)} * {delta}"
                        expressions.append(expression)
                    profile.point_expressions[_point_key(params[end])] = tuple(expressions)


class StructuredEmitter:
    def __init__(self, payload: Mapping[str, Any], stem: str):
        if payload.get("units") != "mm":
            raise ValueError("readable emitter requires millimeter IR")
        if payload.get("unsupported"):
            raise ValueError("readable emitter refuses incomplete IR with unsupported source features")
        from json2scapi.ir.validation import prepare_ir
        prepared, preparation = prepare_ir(dict(payload))
        self.original_entities = payload["entities"]
        self.payload, self.stem = prepared, stem
        self.plan = CodePlan(prepared, payload["entities"])
        self.entities = prepared["entities"]
        self.uses_polyline = False
        self.metadata: dict[str, Any] = {
            "style": "readable", "readable_version": 3, "preparation": preparation, "symbols": {},
            "feature_groups": {}, "profiles": {},
        }

    @staticmethod
    def _signature(name: str, parameters: list[Parameter], *, bodies: bool = False) -> list[str]:
        first = "bodies" if bodies else ""
        if not parameters:
            return [f"def {name}({first}):"]
        return [f"def {name}({first + ', ' if first else ''}*,",
                *[f"    {p.argument}," for p in parameters], "):"]

    def _polyline(self, builder: SketchBuilder, curves: list[str]) -> list[str] | None:
        nodes = [self.entities[key] for key in curves]
        if len(nodes) < 3 or any(node["op"] != "sketch.line" for node in nodes):
            return None
        points = [tuple(node["params"]["start"]) for node in nodes]
        if any(tuple(node["params"]["end"]) != points[(i + 1) % len(points)]
               for i, node in enumerate(nodes)):
            return None
        keys = [_point_key(point) for point in points]
        if len(set(keys)) != len(keys) or any(key in builder.points for key in keys):
            return None
        point_start, edge_start = builder.point_count + 1, builder.entity_count + 1
        builder.lines.extend([
            "    sketch = _add_closed_polyline(", "        sketch, vertices=[",
            *[f"            {fmt_vec(point)}," for point in points],
            f"        ], point_start={point_start}, edge_start={edge_start},", "    )",
        ])
        edges = []
        for index, key in enumerate(keys):
            builder.points[key] = f"p{point_start + index}"
            edges.append(f"e{edge_start + index}")
        builder.point_count += len(keys)
        builder.entity_count += len(keys)
        self.uses_polyline = True
        return edges

    def _profile(self, profile: Profile) -> list[str]:
        node = self.entities[profile.ir_id]
        frame = self.entities[node["params"]["plane"]]["params"]["frame"]
        plane = {key: frame[key] for key in ("origin", "x_axis", "y_axis")}
        builder = SketchBuilder(node["params"]["plane"], frame, "sketch")
        builder.point_expressions = profile.point_expressions
        builder.lines.append("    sketch = scad.make_sketch_rsketch(")
        builder.lines.extend([f"        name='sketch_{profile.number:02d}',", "        plane={",
                              *[f"            {key!r}: {fmt_literal(value)}," for key, value in plane.items()],
                              "        },", "    )"])
        refs = {}
        loop_metadata = []
        for loop_id in node["inputs"]:
            builder.points = {}  # No point identity is shared across loops.
            curves = self.entities[loop_id]["inputs"]
            edges = self._polyline(builder, curves)
            if edges is None:
                edges = []
                for curve_id in curves:
                    curve = self.entities[curve_id]
                    params = curve["params"]
                    op = curve["op"]
                    if op == "sketch.line":
                        edge = builder.line(tuple(params["start"]), tuple(params["end"]))
                    elif op == "sketch.arc":
                        edge = builder.arc(tuple(params["start"]), tuple(params["mid"]),
                                           tuple(params["end"]), tuple(params["center"]) if "center" in params else None)
                    elif op == "sketch.circle":
                        edge = builder.circle(tuple(params["center"]), float(params["radius"]),
                                              profile.curves[curve_id].argument)
                    else:
                        raise ValueError(f"unsupported curve: {op}")
                    edges.append(edge)
            refs[loop_id] = edges[0]
            loop_metadata.append({"ir_id": loop_id, "edges": [
                {"entity_id": edge, "ir_id": curve_id,
                 "source": self.entities[curve_id].get("source", {}),
                 "params": self.entities[curve_id]["params"],
                 "original_params": self.original_entities[curve_id]["params"]}
                for edge, curve_id in zip(edges, curves)
            ]})
        faces = []
        lines = self._signature(profile.function, profile.parameters)
        lines.append(f'    """Build profile {profile.number:02d} in its original sketch plane."""')
        lines.extend(builder.lines)
        for index, region in enumerate(node["params"]["regions"], 1):
            face = f"face_{index:02d}"
            outer = "0" if len(refs) == 1 else repr(refs[region["outer"]])
            holes = f", inner_profiles={[refs[hole] for hole in region['holes']]!r}" if region["holes"] else ""
            lines.append(f"    {face} = scad.make_face_from_sketch_rface(sketch=sketch, profile={outer}{holes})")
            faces.append(face)
        lines.append(f"    return ({', '.join(faces)},)")
        self.metadata["profiles"][profile.function] = {
            "ir_id": profile.ir_id, "source": node.get("source", {}), "loops": loop_metadata,
            "points": [{"point_id": point_id, "coordinates": list(point)}
                       for point, point_id in builder.points.items()],
        }
        self.metadata["symbols"][profile.function] = {"kind": "profile_function", "ir_id": profile.ir_id,
                                                     "source": node.get("source", {})}
        return lines

    def _extrude(self, feature: Feature, step: str, face: str, tool: str, *, reverse=False) -> str:
        params = self.entities[step]["params"]
        direction = tuple(float(v) * (-1 if reverse else 1) for v in params["direction"])
        key = "distance2" if reverse and params["extent"] == "two_sides" else "distance"
        distance = feature.dimensions[step, key].argument
        if (step, "taper") in feature.dimensions:
            taper = feature.dimensions[step, "taper"].argument
            return (f"        {tool} = scad.extrude_taper_rsolid(profile={face}, "
                    f"vector=tuple(v * {distance} for v in {fmt_vec(direction)}), taper={taper}, flip_inner=True)")
        return (f"        {tool} = scad.extrude_rsolid(profile={face}, "
                f"direction={fmt_vec(direction)}, distance={distance})")

    def _feature(self, feature: Feature) -> list[str]:
        lines = self._signature(feature.function, feature.parameters, bodies=True)
        operations = ", ".join(self.entities[step]["params"]["operation"] for step in feature.steps)
        lines.append(f'    """Apply feature {feature.number:02d}: {operations}."""')
        for index, step in enumerate(feature.steps, 1):
            profile = self.plan.profiles[self.entities[step]["inputs"][0]]
            params = self.entities[step]["params"]
            lines.extend([f"    # Step {index:02d}: {params['operation']} {params['extent']}",
                          f"    faces = {profile.function}(",
                          *[f"        {p.argument}={p.argument}," for p in profile.parameters], "    )",
                          "    tools = []", "    for face in faces:"])
            if params["extent"] == "one_side":
                lines.append(self._extrude(feature, step, "face", "tool"))
            else:
                lines.extend([self._extrude(feature, step, "face", "forward"),
                              self._extrude(feature, step, "face", "reverse", reverse=True),
                              '        tool = scad.union_rsolid([forward, reverse], tracking_policy="graph")'])
            lines.append("        tools.append(tool)")
            operation = params["operation"]
            if operation == "new":
                lines.append("    bodies.extend(tools)")
            else:
                lines.extend(["    if not bodies:",
                              f"        raise ValueError('feature {feature.number:02d}, step {index:02d}: no body to modify')",
                              "    bodies = scad.boolean_rsolidlist(",
                              f"        left=bodies, right=tools, operation={'union' if operation == 'join' else operation!r},",
                              "    )"])
        lines.append("    return bodies")
        self.metadata["feature_groups"][feature.function] = feature.steps
        self.metadata["symbols"][feature.function] = {
            "kind": "feature_function", "ir_ids": feature.steps,
            "source": self.entities[feature.steps[0]].get("source", {}),
        }
        return lines

    def emit(self) -> str:
        profiles = [self._profile(profile) for profile in self.plan.profiles.values()]
        features = [self._feature(feature) for feature in self.plan.features]
        lines = [f'"""Readable SCAPI v3 generated from CAD IR v0.1: {_slug(self.stem)}."""',
                 "", "import simplecadapi as scad", ""]
        for feature in self.plan.features:
            owned = [p for p in self.plan.parameters if p.name.startswith(f"FEATURE_{feature.number:02d}_")]
            if owned:
                lines.append(f"# Feature {feature.number:02d} dimensions (mm; taper in degrees).")
                lines.extend(f"{p.name} = {fmt_num(p.value)}" for p in owned)
                lines.append("")
        lines.extend(["BODIES = None", "", ""])
        for block in profiles + features:
            lines.extend([*block, "", ""])
        lines.extend(["def build():", '    """Build the model in source feature order."""', "    bodies = []"])
        for feature in self.plan.features:
            lines.extend([f"    bodies = {feature.function}(", "        bodies,",
                          *[f"        {p.argument}={p.name}," for p in feature.parameters], "    )"])
        lines.extend(["    global BODIES", "    BODIES = list(bodies)", "    return _as_model(bodies)", "", ""])
        if self.uses_polyline:
            lines.extend(POLYLINE_HELPER.splitlines() + ["", ""])
        part_id = "json2scapi-" + _slug(self.stem)
        lines.extend([
            "def _as_model(bodies):", '    """Return one solid or an assembly of the final body set."""',
            "    if not bodies:", "        raise ValueError('model produced no solids')",
            "    if len(bodies) == 1:", "        return bodies[0]",
            f"    assembly = scad.make_assembly_rassembly({(part_id + '-assembly')!r})",
            "    for index, body in enumerate(bodies):",
            f"        part = scad.make_part_rpart(f'{part_id}-part-{{index}}', body)",
            "        assembly = scad.add_component_rassembly(",
            "            assembly, part, f'component-{index}', scad.identity_placement_rplacement(),",
            "        )", "    return assembly", "",
        ])
        for p in self.plan.parameters:
            self.metadata["symbols"][p.name] = {
                "kind": p.kind, "value": p.value, "units": "deg" if p.kind == "taper" else "mm",
                "argument": p.argument, "bindings": [
                    {**path, "source": self.entities[path["ir_id"]].get("source", {})} for path in p.paths
                ],
            }
        source = "\n".join(lines)
        tree = ast.parse(source)
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
        self.metadata["structure"] = {
            "lines": len(source.splitlines()), "function_count": len(functions),
            "build_lines": next(node.end_lineno - node.lineno + 1 for node in functions if node.name == "build"),
            "max_function_lines": max(node.end_lineno - node.lineno + 1 for node in functions),
            "feature_count": len(self.plan.features), "profile_count": len(self.plan.profiles),
            "polyline_helper": self.uses_polyline,
        }
        return source


POLYLINE_HELPER = '''def _add_closed_polyline(sketch, vertices, *, point_start, edge_start):
    """Add an ordered closed line loop without changing its coordinates."""
    for index, (x, y) in enumerate(vertices):
        sketch = scad.add_point_rsketch(
            sketch=sketch, point_id=f'p{point_start + index}', x=x, y=y,
        )
    for index in range(len(vertices)):
        sketch = scad.add_line_rsketch(
            sketch=sketch, entity_id=f'e{edge_start + index}',
            start=f'p{point_start + index}',
            end=f'p{point_start + (index + 1) % len(vertices)}',
        )
    return sketch'''
