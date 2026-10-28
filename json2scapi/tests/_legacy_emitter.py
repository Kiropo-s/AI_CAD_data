"""Frozen v1 emitter, used only as a regression oracle; no production entry point."""
from typing import Any, Mapping
from json2scapi.codegen.api import SketchBuilder, _slug, _vec2, _vec3, fmt_literal, fmt_num, fmt_vec

class Emitter:
    def __init__(self, payload: Mapping[str, Any], stem: str):
        if payload.get("units") != "mm":
            raise ValueError("FTC emitter requires millimeter IR; normalize units in the adapter")
        if payload.get("unsupported"):
            raise ValueError("FTC emitter refuses incomplete IR with unsupported source features")
        self.payload = payload
        self.stem = stem
        self.entities: Mapping[str, Mapping[str, Any]] = payload["entities"]
        self.profiles = [key for key, entity in self.entities.items() if entity["op"] == "sketch.profile"]
        self.frames: dict[str, Mapping[str, Any]] = {}
        self.builders: dict[str, SketchBuilder] = {}
        self.lines: list[str] = []
        self.curve_owners: dict[str, str] = {}
        self.curve_entities: dict[str, str] = {}
        self.profile_faces: dict[str, list[str]] = {}
        self.body_vars: list[str] = []

    def _plane_var(self, plane_id: str) -> str:
        return "sketch_" + _slug(plane_id)

    def _emit_planes(self) -> None:
        for entity_id, entity in self.entities.items():
            if entity["op"] != "sketch.plane":
                continue
            self.frames[entity_id] = entity["params"]["frame"]
            self.lines.append(f"    # ---- feature: {entity_id} (build, sketch.plane) ----")
            self.lines.append("")

    def _curve_plane(self) -> dict[str, str]:
        owners: dict[str, str] = {}
        for profile_id in self.profiles:
            profile = self.entities[profile_id]
            plane_id = profile["params"]["plane"]
            for loop_id in profile["inputs"]:
                loop = self.entities[loop_id]
                for curve_id in loop["inputs"]:
                    previous = owners.get(curve_id)
                    if previous is not None and previous != plane_id:
                        raise ValueError(
                            f"curve {curve_id!r} is used by more than one sketch plane"
                        )
                    owners[curve_id] = plane_id
        return owners

    def _emit_curve(self, curve_id: str, builder: SketchBuilder) -> str:
        entity = self.entities[curve_id]
        op = entity["op"]
        params = entity["params"]
        if op == "sketch.line":
            entity_id = builder.line(
                _vec2(params["start"], f"{curve_id}.start"),
                _vec2(params["end"], f"{curve_id}.end"),
            )
        elif op == "sketch.arc":
            entity_id = builder.arc(
                _vec2(params["start"], f"{curve_id}.start"),
                _vec2(params["mid"], f"{curve_id}.mid"),
                _vec2(params["end"], f"{curve_id}.end"),
                _vec2(params["center"], f"{curve_id}.center") if "center" in params else None,
            )
        elif op == "sketch.circle":
            center = _vec2(params["center"], f"{curve_id}.center")
            radius = float(params["radius"])
            if radius <= 0:
                raise ValueError(f"{curve_id}: circle radius must be positive")
            entity_id = builder.circle(center, radius)
        else:
            raise ValueError(f"unsupported sketch curve op: {op}")
        return entity_id

    def _emit_profiles(self) -> None:
        self.curve_owners = self._curve_plane()
        for profile_id in self.profiles:
            profile = self.entities[profile_id]
            plane_id = profile["params"]["plane"]
            frame = self.frames[plane_id]
            variable = "sketch_" + _slug(profile_id)
            plane = {
                "origin": frame["origin"],
                "x_axis": frame["x_axis"],
                "y_axis": frame["y_axis"],
            }
            builder = SketchBuilder(plane_id, frame, variable)
            builder.lines.append(
                f"    {variable} = scad.make_sketch_rsketch("
                f"name={profile_id!r}, plane={fmt_literal(plane)})"
            )
            self.builders[profile_id] = builder
            local_curve_ids: dict[str, str] = {}
            regions = [(region["outer"], region["holes"])
                       for region in profile["params"]["regions"]]
            loop_refs: dict[str, str] = {}
            ordered_loops = [loop_id for outer, holes in regions for loop_id in (outer, *holes)]
            for loop_id in ordered_loops:
                loop = self.entities[loop_id]
                edge_ids: list[str] = []
                for curve_id in loop["inputs"]:
                    if curve_id not in local_curve_ids:
                        local_curve_ids[curve_id] = self._emit_curve(curve_id, builder)
                    edge_ids.append(local_curve_ids[curve_id])
                if not edge_ids:
                    raise ValueError(f"{loop_id}: loop has no curves")
                # Each profile has its own sketch document, so the first edge
                # of a loop is also the connected component's first entity.
                loop_refs[loop_id] = min(edge_ids, key=lambda item: int(item[1:]) if item[1:].isdigit() else 10**9)
            self.lines.extend(builder.lines)
            self.lines.append(f"    # ---- feature: {profile_id} (build, sketch.profile) ----")
            self.lines.append("    # Filled regions / holes inferred from geometry; DeepCAD is_outer may mark holes as outer.")
            faces = []
            for index, (outer_id, hole_ids) in enumerate(regions):
                face_var = "face_" + _slug(profile_id) + (f"_region{index}" if len(regions) > 1 else "")
                if len(loop_refs) == 1:
                    profile_ref = "0"
                else:
                    profile_ref = repr(loop_refs[outer_id])
                inner_arg = (f", inner_profiles={[loop_refs[hole] for hole in hole_ids]!r}"
                             if hole_ids else "")
                self.lines.append(
                    f"    {face_var} = scad.make_face_from_sketch_rface("
                    f"sketch={builder.variable}, profile={profile_ref}{inner_arg})"
                )
                faces.append(face_var)
            self.profile_faces[profile_id] = faces
            self.lines.append("")

    def _extrude_call(
        self,
        profile_var: str,
        direction: tuple[float, float, float],
        distance: float,
        taper: float,
        variable: str,
    ) -> str:
        if abs(taper) > 1e-12:
            return (
                f"    {variable} = scad.extrude_taper_rsolid("
                f"profile={profile_var}, vector={fmt_vec([value * distance for value in direction])}, "
                f"taper={fmt_num(taper)}, flip_inner=True)"
            )
        return (
            f"    {variable} = scad.extrude_rsolid("
            f"profile={profile_var}, direction={fmt_vec(direction)}, "
            f"distance={fmt_num(distance)})"
        )

    def _emit_extrude(self, entity_id: str, entity: Mapping[str, Any]) -> None:
        if len(entity["inputs"]) != 1:
            raise ValueError(f"{entity_id}: extrude needs exactly one profile")
        profile_id = entity["inputs"][0]
        if profile_id not in self.profile_faces:
            raise ValueError(f"{entity_id}: profile {profile_id!r} was not emitted")
        params = entity["params"]
        direction = _vec3(params["direction"], f"{entity_id}.direction")
        distance = float(params["distance"])
        taper = float(params.get("taper", 0.0))
        extent = params["extent"]
        operation = params["operation"]
        base_var = "solid_" + _slug(entity_id)
        self.lines.append(f"    # ---- feature: {entity_id} (build, feature.extrude) ----")
        if extent not in {"one_side", "symmetric", "two_sides"}:
            raise ValueError(f"{entity_id}: unsupported extent {extent!r}")
        tools = []
        faces = self.profile_faces[profile_id]
        for index, face in enumerate(faces):
            tool_var = base_var + (f"_region{index}" if len(faces) > 1 else "")
            if extent == "one_side":
                self.lines.append(self._extrude_call(face, direction, distance, taper, tool_var))
            else:
                second = distance if extent == "symmetric" else float(params["distance2"])
                first_var, second_var = tool_var + "_a", tool_var + "_b"
                self.lines.append(self._extrude_call(face, direction, distance, taper, first_var))
                self.lines.append(self._extrude_call(face, tuple(-value for value in direction), second, taper, second_var))
                self.lines.append(
                    f"    {tool_var} = scad.union_rsolid([{first_var}, {second_var}], tracking_policy=\"graph\")"
                )
            tools.append(tool_var)

        if operation != "new" and not self.body_vars:
            raise ValueError(f"{entity_id}: {operation} requires a prior body")
        if operation != "new":
            self.lines.append("    if not bodies:")
            self.lines.append(f"        raise ValueError({entity_id!r} + ': no body to modify')")
        if operation == "new":
            # NewBody starts a new body. Disjoint bodies stay separate so the
            # final result can be a multi-solid assembly.
            self.body_vars.extend(tools)
            self.lines.append(f"    bodies.extend([{', '.join(tools)}])")
        elif operation == "join":
            self.lines.append(
                f"    bodies = scad.boolean_rsolidlist(left=bodies, right=[{', '.join(tools)}], operation='union')"
            )
        elif operation in {"cut", "intersect"}:
            self.lines.append(
                f"    bodies = scad.boolean_rsolidlist(left=bodies, right=[{', '.join(tools)}], operation={operation!r})"
            )
        else:
            raise ValueError(f"{entity_id}: unsupported operation {operation!r}")
        self.lines.append("")

    def emit(self) -> str:
        results = self.payload["results"]
        if len(results) != 1:
            raise ValueError("FTC emitter requires one final body-set state")
        self._emit_planes()
        self._emit_profiles()
        extrude_ids = [
            entity_id for entity_id in self.payload.get("sequence", [])
            if entity_id in self.entities and self.entities[entity_id]["op"] == "feature.extrude"
        ]
        for entity_id in extrude_ids:
            self._emit_extrude(entity_id, self.entities[entity_id])
        if not extrude_ids or results[0] != extrude_ids[-1]:
            raise ValueError("IR result must be the final extrude in sequence")
        if not self.body_vars:
            raise ValueError("model produced no bodies")
        part_id = "json2scapi-" + _slug(self.stem)
        header = [
            f'"""FTC generated from CAD IR v0.1: {self.stem}."""',
            "",
            "import simplecadapi as scad",
            "",
            "BODIES = None",
            "",
            "",
            "def build():",
            "    bodies = []",
        ]
        footer = [
            "    if not bodies:",
            "        raise ValueError('model produced no solids')",
            "    global BODIES",
            "    BODIES = list(bodies)",
            "    if len(bodies) == 1:",
            "        return bodies[0]",
            f"    assembly = scad.make_assembly_rassembly({(part_id + '-assembly')!r})",
            "    for index, body in enumerate(bodies):",
            f"        part = scad.make_part_rpart(f'{part_id}-part-{{index}}', body)",
            "        assembly = scad.add_component_rassembly(",
            "            assembly, part, f'component-{index}',",
            "            scad.identity_placement_rplacement(),",
            "        )",
            "    return assembly",
            "",
        ]
        return "\n".join([*header, *self.lines, *footer])
