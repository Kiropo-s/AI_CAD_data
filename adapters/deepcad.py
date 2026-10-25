"""Convert a DeepCAD cad_json file into CAD IR v0.1.

The adapter follows the official cadlib semantics:
- only line/arc/circle + extrude;
- one ExtrudeFeature with N profiles becomes N extrude nodes;
- NewBody + N profiles becomes New then Join;
- all lengths are converted from meters to millimeters;
- sketch curves stay in 2D local coordinates, with a shared sketch.plane.

Usage:
    python deepcad_to_ir.py --input cad_json/0000/00000007.json

Default output:
    out/cases/<stem>/model.ir.json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from json2scapi.ir.regions import profile_regions

M_TO_MM = 1000.0
TOL = 1e-3  # mm; DeepCAD endpoints carry ~1e-5 mm rounding noise
SKETCH_PLANAR_TOL_MM = 1e-4  # local z noise only; larger nonplanarity is rejected

OPERATION_MAP = {
    "NewBodyFeatureOperation": "new",
    "JoinFeatureOperation": "join",
    "CutFeatureOperation": "cut",
    "IntersectFeatureOperation": "intersect",
}
EXTENT_MAP = {
    "OneSideFeatureExtentType": "one_side",
    "SymmetricFeatureExtentType": "symmetric",
    "TwoSidesFeatureExtentType": "two_sides",
}


class ConversionError(ValueError):
    pass


def _slug(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(value)).strip("_")
    return text or "item"


def _vec3(value: Mapping[str, Any]) -> list[float]:
    return [float(value["x"]), float(value["y"]), float(value["z"])]


def _vec2(value: Mapping[str, Any]) -> tuple[float, float]:
    return float(value["x"]), float(value["y"])


def _pt2_mm(value: Mapping[str, Any], field: str) -> list[float]:
    x, y = _vec2(value)
    z = float(value.get("z", 0.0))
    if not all(math.isfinite(v) for v in (x, y, z)):
        raise ConversionError(f"{field}: non-finite sketch point")
    if abs(z) * M_TO_MM > SKETCH_PLANAR_TOL_MM:
        raise ConversionError(f"{field}: nonplanar sketch point z={z * M_TO_MM} mm "
                              f"exceeds {SKETCH_PLANAR_TOL_MM} mm")
    return [x * M_TO_MM, y * M_TO_MM]


def _vec3_mm(value: Mapping[str, Any]) -> list[float]:
    return [item * M_TO_MM for item in _vec3(value)]


def _dist2(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _same2(a: Sequence[float], b: Sequence[float], tolerance: float = TOL) -> bool:
    return _dist2(a, b) <= tolerance


def _sub2(a: Sequence[float], b: Sequence[float]) -> list[float]:
    return [float(a[0]) - float(b[0]), float(a[1]) - float(b[1])]


def _cross2(a: Sequence[float], b: Sequence[float]) -> float:
    return float(a[0]) * float(b[1]) - float(a[1]) * float(b[0])


def _bbox_of_points(points: Iterable[Sequence[float]]) -> list[float]:
    values = list(points)
    if not values:
        return [0.0, 0.0, 0.0, 0.0]
    xs = [float(item[0]) for item in values]
    ys = [float(item[1]) for item in values]
    return [min(xs), min(ys), max(xs), max(ys)]


def _reverse_curve(curve: dict[str, Any]) -> dict[str, Any]:
    params = dict(curve["params"])
    params["start"], params["end"] = params["end"], params["start"]
    if curve["op"] == "sketch.arc" and "ccw" in params:
        params["ccw"] = not params["ccw"]
    return {**curve, "params": params}


def _arc_midpoint(params: Mapping[str, Any]) -> list[float]:
    center = params["_center_2d"]
    radius = float(params["_radius_m"])
    start_angle = float(params["_start_angle"])
    end_angle = float(params["_end_angle"])
    ref = params["_reference_vec"]
    mid_angle = (start_angle + end_angle) / 2.0
    cos_a, sin_a = math.cos(mid_angle), math.sin(mid_angle)
    mid = [
        center[0] + radius * (cos_a * ref[0] - sin_a * ref[1]),
        center[1] + radius * (sin_a * ref[0] + cos_a * ref[1]),
    ]
    return [mid[0] * M_TO_MM, mid[1] * M_TO_MM]


def _curve_points(curve: Mapping[str, Any]) -> list[list[float]]:
    params = curve["params"]
    if curve["op"] == "sketch.circle":
        center = params["center"]
        radius = float(params["radius"])
        return [
            [center[0] - radius, center[1]],
            [center[0], center[1] - radius],
            [center[0] + radius, center[1]],
            [center[0], center[1] + radius],
        ]
    points = [params["start"], params["end"]]
    if curve["op"] == "sketch.arc":
        points.append(params["mid"])
    return points


def _signed_area(curves: Sequence[Mapping[str, Any]]) -> float:
    points: list[list[float]] = []
    for curve in curves:
        points.extend(_curve_points(curve))
    if len(points) < 3:
        return 0.0
    area = 0.0
    for first, second in zip(points, points[1:] + points[:1]):
        area += float(first[0]) * float(second[1]) - float(second[0]) * float(first[1])
    return area / 2.0


def _order_loop(curves: list[dict[str, Any]], loop_path: str) -> list[dict[str, Any]]:
    """Find an ordered, closed, counter-clockwise traversal of the loop."""
    if not curves:
        raise ConversionError(f"{loop_path}: loop has no curves")
    if len(curves) == 1 and curves[0]["op"] == "sketch.circle":
        return curves
    if any(curve["op"] == "sketch.circle" for curve in curves):
        raise ConversionError(f"{loop_path}: a circle must be the only curve in a loop")

    # Try every start curve and both orientations. Loops are small (DeepCAD
    # limits them to 15 curves), so a depth-first search is cheap and avoids
    # relying on the JSON curve order (which is not guaranteed).
    best: list[dict[str, Any]] | None = None
    best_key: tuple[float, float] | None = None

    def search(chain: list[dict[str, Any]], used: set[int], end: Sequence[float]) -> None:
        nonlocal best, best_key
        if best is not None and len(chain) == len(curves):
            return
        if len(chain) == len(curves):
            if _same2(chain[-1]["params"]["end"], chain[0]["params"]["start"]):
                candidate = list(chain)
                area = _signed_area(candidate)
                if area < 0:
                    candidate = [_reverse_curve(item) for item in reversed(candidate)]
                key = (float(candidate[0]["params"]["start"][0]), float(candidate[0]["params"]["start"][1]))
                if best is None or key < best_key:  # type: ignore[operator]
                    best, best_key = candidate, key
            return
        for index, curve in enumerate(curves):
            if index in used:
                continue
            start = curve["params"]["start"]
            end_point = curve["params"]["end"]
            if _same2(start, end):
                search([*chain, curve], used | {index}, end_point)
            elif _same2(end_point, end):
                search([*chain, _reverse_curve(curve)], used | {index}, start)

    for index, curve in enumerate(curves):
        for candidate in (curve, _reverse_curve(curve)):
            search([candidate], {index}, candidate["params"]["end"])
    if best is None:
        points = [curve["params"]["start"] for curve in curves] + [curve["params"]["end"] for curve in curves]
        raise ConversionError(f"{loop_path}: cannot form a closed loop; endpoints={points}")
    # Preserve source coordinates; bounded shared preparation handles adjacency.
    return best


class DeepCADConverter:
    def __init__(self, payload: Mapping[str, Any], source_name: str):
        self.payload = payload
        self.source_name = source_name
        self.entities: Mapping[str, Mapping[str, Any]] = payload["entities"]
        self.sequence: Sequence[Mapping[str, Any]] = payload["sequence"]
        self.nodes: dict[str, dict[str, Any]] = {}
        self.order: list[str] = []
        self.plane_ids: dict[str, str] = {}
        self.profile_ids: dict[tuple[str, str], str] = {}
        self.curve_ids: dict[tuple[str, str], str] = {}
        self.curve_variants: dict[tuple[str, str, bool], str] = {}
        self.loop_ids: dict[tuple[str, str, int], str] = {}
        self.extrude_ids: list[str] = []
        self.unsupported: list[dict[str, Any]] = []

    def _add(self, node_id: str, node: dict[str, Any]) -> None:
        if node_id in self.nodes:
            return
        self.nodes[node_id] = node

    def ensure_plane(self, sketch_id: str) -> str:
        if sketch_id in self.plane_ids:
            return self.plane_ids[sketch_id]
        sketch = self.entities.get(sketch_id)
        if sketch is None or sketch.get("type") != "Sketch":
            raise ConversionError(f"missing Sketch entity {sketch_id!r}")
        transform = sketch.get("transform") or {}
        try:
            origin = [float(transform["origin"][axis]) * M_TO_MM for axis in "xyz"]
            x_axis = [float(transform["x_axis"][axis]) for axis in "xyz"]
            y_axis = [float(transform["y_axis"][axis]) for axis in "xyz"]
            z_axis = [float(transform["z_axis"][axis]) for axis in "xyz"]
        except (KeyError, TypeError) as error:
            raise ConversionError(f"Sketch {sketch_id!r}: invalid transform: {error}") from error
        raw_axes = [list(axis) for axis in (x_axis, y_axis, z_axis)]
        lengths = [math.sqrt(sum(v * v for v in axis)) for axis in raw_axes]
        if (not all(math.isfinite(v) for axis in raw_axes for v in axis)
                or any(abs(length - 1.0) > 1e-6 for length in lengths)):
            raise ConversionError(f"Sketch {sketch_id!r}: non-unit or non-finite source frame")
        x_axis, y_axis, z_axis = [[v / length for v in axis]
                                 for axis, length in zip(raw_axes, lengths)]
        cross = [x_axis[1] * y_axis[2] - x_axis[2] * y_axis[1],
                 x_axis[2] * y_axis[0] - x_axis[0] * y_axis[2],
                 x_axis[0] * y_axis[1] - x_axis[1] * y_axis[0]]
        if max(abs(a - b) for a, b in zip(cross, z_axis)) > 1e-9:
            raise ConversionError(f"Sketch {sketch_id!r}: axes are not orthogonal/right-handed")
        plane_source = {"format": "deepcad-1", "entity_id": sketch_id}
        if any(abs(length - 1.0) > 1e-12 for length in lengths):
            plane_source["frame_normalization"] = {
                "rule": "unit_axes_v1", "original_axis_lengths": lengths,
                "tolerance": 1e-6,
            }
        node_id = f"sketch_{_slug(sketch_id)}"
        self._add(node_id, {
            "type": "SketchPlane",
            "op": "sketch.plane",
            "inputs": [],
            "params": {
                "frame": {
                    "origin": origin,
                    "x_axis": x_axis,
                    "y_axis": y_axis,
                    "z_axis": z_axis,
                }
            },
            "source": plane_source,
        })
        self.plane_ids[sketch_id] = node_id
        if node_id not in self.order:
            self.order.append(node_id)
        return node_id

    def _convert_curve(self, sketch_id: str, curve: Mapping[str, Any]) -> dict[str, Any]:
        curve_type = curve.get("type")
        curve_key = str(curve.get("curve") or f"curve{len(self.curve_ids)}")
        node_id = f"curve_{_slug(sketch_id)}_{_slug(curve_key)}"
        if node_id in self.nodes:
            return self.nodes[node_id]
        if curve_type == "Line3D":
            node = {
                "type": "SketchLine",
                "op": "sketch.line",
                "inputs": [],
                "params": {
                    "start": _pt2_mm(curve["start_point"], f"{node_id}.start"),
                    "end": _pt2_mm(curve["end_point"], f"{node_id}.end"),
                },
            }
        elif curve_type == "Circle3D":
            radius = float(curve["radius"]) * M_TO_MM
            if radius <= 0:
                raise ConversionError(f"{node_id}: circle radius must be positive")
            node = {
                "type": "SketchCircle",
                "op": "sketch.circle",
                "inputs": [],
                "params": {
                    "center": _pt2_mm(curve["center_point"], f"{node_id}.center"),
                    "radius": radius,
                },
            }
        elif curve_type == "Arc3D":
            center = _vec2(curve["center_point"])
            radius_m = float(curve["radius"])
            if radius_m <= 0:
                raise ConversionError(f"{node_id}: arc radius must be positive")
            ref = _vec2(curve["reference_vector"])
            ref_norm = math.hypot(ref[0], ref[1])
            if ref_norm <= 1e-12:
                raise ConversionError(f"{node_id}: degenerate arc reference vector")
            ref = (ref[0] / ref_norm, ref[1] / ref_norm)
            start_angle = float(curve["start_angle"])
            end_angle = float(curve["end_angle"])
            template = {
                "_center_2d": center,
                "_radius_m": radius_m,
                "_start_angle": start_angle,
                "_end_angle": end_angle,
                "_reference_vec": ref,
            }
            mid = _arc_midpoint(template)
            start = _pt2_mm(curve["start_point"], f"{node_id}.start")
            end = _pt2_mm(curve["end_point"], f"{node_id}.end")
            center_mm = [center[0] * M_TO_MM, center[1] * M_TO_MM]
            ccw = _cross2(_sub2(mid, start), _sub2(end, start)) >= 0
            node = {
                "type": "SketchArc",
                "op": "sketch.arc",
                "inputs": [],
                "params": {
                    "start": start,
                    "mid": mid,
                    "end": end,
                    "center": center_mm,
                    "radius": radius_m * M_TO_MM,
                    "ccw": bool(ccw),
                },
            }
        else:
            raise ConversionError(f"{node_id}: unsupported curve type {curve_type!r}")
        node["source"] = {"format": "deepcad-1", "entity_id": sketch_id, "curve_id": curve_key}
        projections = [
            {"point": key, "z_mm": float(curve[key].get("z", 0.0)) * M_TO_MM,
             "tolerance_mm": SKETCH_PLANAR_TOL_MM}
            for key in ("start_point", "end_point", "center_point")
            if key in curve and float(curve[key].get("z", 0.0)) != 0.0
        ]
        if projections:
            node["source"]["planarity_projection"] = projections
        self._add(node_id, node)
        self.curve_ids[(sketch_id, curve_key)] = node_id
        self.curve_variants[(sketch_id, curve_key, False)] = node_id
        return node

    def _curve_variant(self, sketch_id: str, curve_key: str, reversed_flag: bool) -> str:
        key = (sketch_id, curve_key, reversed_flag)
        if key in self.curve_variants:
            return self.curve_variants[key]
        original_id = self.curve_ids[(sketch_id, curve_key)]
        original = self.nodes[original_id]
        if not reversed_flag:
            self.curve_variants[key] = original_id
            return original_id
        params = dict(original["params"])
        params["start"], params["end"] = params["end"], params["start"]
        if original["op"] == "sketch.arc" and "ccw" in params:
            params["ccw"] = not bool(params["ccw"])
        source = dict(original.get("source", {}))
        source["reversed"] = True
        node_id = original_id + "_rev"
        self._add(node_id, {**original, "params": params, "source": source})
        self.curve_variants[key] = node_id
        return node_id

    def ensure_profile(self, sketch_id: str, profile_id: str) -> str:
        key = (sketch_id, profile_id)
        if key in self.profile_ids:
            return self.profile_ids[key]
        sketch = self.entities.get(sketch_id)
        if sketch is None:
            raise ConversionError(f"missing Sketch entity {sketch_id!r}")
        profiles = sketch.get("profiles") or {}
        profile = profiles.get(profile_id)
        if profile is None:
            raise ConversionError(f"Sketch {sketch_id!r}: missing profile {profile_id!r}")
        plane_id = self.ensure_plane(sketch_id)
        loops = []
        for loop_index, loop in enumerate(profile.get("loops", [])):
            loop_path = f"{sketch_id}.{profile_id}.loop[{loop_index}]"
            curves = [self._convert_curve(sketch_id, item) for item in loop.get("profile_curves", [])]
            ordered = _order_loop(curves, loop_path)
            ordered_ids = []
            for position, item in enumerate(ordered):
                curve_key = str(item["source"]["curve_id"])
                original_id = self.curve_ids[(sketch_id, curve_key)]
                original = self.nodes[original_id]
                node_id = f"{original_id}_{_slug(profile_id)}_loop{loop_index}_{position}"
                self._add(node_id, {
                    **original,
                    "params": dict(item["params"]),
                    "source": {**original.get("source", {}), "ordered": True},
                })
                ordered_ids.append(node_id)
            loop_id = f"loop_{_slug(sketch_id)}_{_slug(profile_id)}_{loop_index}"
            self._add(loop_id, {
                "type": "SketchLoop",
                "op": "sketch.loop",
                "inputs": ordered_ids,
                "params": {"is_outer": bool(loop.get("is_outer", False)), "closed": True},
                "source": {"format": "deepcad-1", "entity_id": sketch_id, "profile_id": profile_id, "loop_index": loop_index},
            })
            loops.append((loop_id, ordered))
        if not loops:
            raise ConversionError(f"{sketch_id}.{profile_id}: profile has no loops")
        loops.sort(key=lambda item: (
            0 if bool(self.nodes[item[0]]["params"]["is_outer"]) else 1,
            _bbox_of_points([point for curve in item[1] for point in _curve_points(curve)]),
        ))
        loop_ids = [loop_id for loop_id, _ in loops]
        # Source is_outer is often true even for an enclosed hole. Keep the
        # original label as evidence but normalize IR roles from geometry.
        try:
            regions = profile_regions({"inputs": loop_ids}, self.nodes, TOL)
        except ValueError as error:
            raise ConversionError(f"{sketch_id}.{profile_id}: {error}") from error
        filled = {outer for outer, _ in regions}
        for loop_id in loop_ids:
            loop_node = self.nodes[loop_id]
            original = loop_node["params"]["is_outer"]
            loop_node["source"]["deepcad_is_outer"] = original
            loop_node["source"]["role_provenance"] = "geometry_nesting"
            loop_node["params"]["is_outer"] = loop_id in filled
        profile_node_id = f"profile_{_slug(sketch_id)}_{_slug(profile_id)}"
        self._add(profile_node_id, {
            "type": "SketchProfile",
            "op": "sketch.profile",
            "inputs": [loop_id for loop_id, _ in loops],
            "params": {
                "plane": plane_id,
                "closed": True,
                "regions": [{"outer": outer, "holes": holes} for outer, holes in regions],
            },
            "source": {"format": "deepcad-1", "entity_id": sketch_id, "profile_id": profile_id},
        })
        self.profile_ids[key] = profile_node_id
        return profile_node_id

    def _extrude_node(
        self,
        extrude_id: str,
        profile_node: str,
        operation: str,
        extent_type: str,
        extent_one_m: float,
        extent_two_m: float,
        taper_rad: float,
        normal: Sequence[float],
        index: int,
        total: int,
    ) -> str:
        extent = EXTENT_MAP.get(extent_type)
        if extent is None:
            raise ConversionError(f"{extrude_id}: unsupported extent_type {extent_type!r}")
        values = (extent_one_m, extent_two_m, taper_rad)
        if not all(math.isfinite(value) for value in values):
            raise ConversionError(f"{extrude_id}: non-finite extrusion parameter")
        raw_extent = extent
        direction = [float(item) for item in normal]
        distance2 = None
        interval = None
        if extent == "two_sides":
            lower = min(0.0, extent_one_m, -extent_two_m)
            upper = max(0.0, extent_one_m, -extent_two_m)
            interval = [lower * M_TO_MM, upper * M_TO_MM]
            if upper - lower <= 1e-15:
                raise ConversionError(f"{extrude_id}: zero extrusion distance")
            if extent_one_m <= 0 or extent_two_m <= 0:
                if abs(taper_rad) > 1e-12:
                    raise ConversionError(f"{extrude_id}: signed/zero two_sides with taper is unsupported")
            if lower < 0 < upper:
                # Keep positive canonical distances; negative source directions are swapped.
                sign = 1.0 if extent_one_m >= 0 else -1.0
                direction = [sign * value for value in direction]
                distance = (upper if sign > 0 else -lower) * M_TO_MM
                distance2 = (-lower if sign > 0 else upper) * M_TO_MM
            else:
                extent = "one_side"
                sign = 1.0 if upper > 0 else -1.0
                direction = [sign * value for value in direction]
                distance = max(upper, -lower) * M_TO_MM
        else:
            if abs(extent_one_m) <= 1e-15:
                raise ConversionError(f"{extrude_id}: zero extrusion distance")
            if extent == "one_side" and extent_one_m < 0:
                direction = [-value for value in direction]
            distance = abs(extent_one_m) * M_TO_MM
        params = {
            "distance": distance, "direction": direction, "extent": extent,
            "operation": operation, "start": "profile_plane", "taper": math.degrees(taper_rad),
        }
        if distance2 is not None:
            params["distance2"] = distance2
        source = {"format": "deepcad-1", "entity_id": extrude_id, "profile_index": index}
        if raw_extent == "two_sides":
            source["extent_normalization"] = {
                "rule": "signed_interval_v1", "extent_one_m": extent_one_m,
                "extent_two_m": extent_two_m, "interval_mm": interval,
            }
        node_id = f"extrude_{_slug(extrude_id)}" if total == 1 else f"extrude_{_slug(extrude_id)}_{index}"
        self._add(node_id, {
            "type": "ExtrudeFeature",
            "op": "feature.extrude",
            "inputs": [profile_node],
            "params": params,
            "source": source,
        })
        return node_id

    def add_extrude(self, extrude_id: str) -> None:
        entity = self.entities.get(extrude_id)
        if entity is None or entity.get("type") != "ExtrudeFeature":
            raise ConversionError(f"missing ExtrudeFeature entity {extrude_id!r}")
        start_extent = entity.get("start_extent") or {}
        if start_extent.get("type") != "ProfilePlaneStartDefinition":
            raise ConversionError(f"{extrude_id}: unsupported start_extent {start_extent.get('type')!r}")
        profiles = entity.get("profiles") or []
        if not profiles:
            # cadlib.Extrude.from_dict returns an empty list for these; skip
            # them instead of failing the whole model.
            self.unsupported.append({
                "code": "empty_profiles",
                "message": "ExtrudeFeature has no profiles and was skipped",
                "entity": extrude_id,
            })
            return
        operation = OPERATION_MAP.get(entity.get("operation"))
        if operation is None:
            raise ConversionError(f"{extrude_id}: unsupported operation {entity.get('operation')!r}")
        extent_type = entity.get("extent_type")
        if extent_type not in EXTENT_MAP:
            raise ConversionError(f"{extrude_id}: unsupported extent_type {extent_type!r}")
        extent_one = float(entity["extent_one"]["distance"]["value"])
        extent_two = float(entity.get("extent_two", {}).get("distance", {}).get("value", 0.0))
        taper_one = float(entity.get("extent_one", {}).get("taper_angle", {}).get("value", 0.0))
        taper_two = float(entity.get("extent_two", {}).get("taper_angle", {}).get("value", 0.0))
        if not all(math.isfinite(value) for value in (extent_one, extent_two, taper_one, taper_two)):
            raise ConversionError(f"{extrude_id}: non-finite extrusion parameter")
        if extent_type == "TwoSidesFeatureExtentType" and extent_two != 0 and abs(taper_two - taper_one) > 1e-12:
            raise ConversionError(f"{extrude_id}: two-sided extrusion with taper: differing side taper angles are not supported")
        if operation == "intersect" and len(profiles) > 1:
            raise ConversionError(f"{extrude_id}: multi-profile intersect needs a grouped tool")
        operations = [operation] + ["join"] * (len(profiles) - 1) if operation == "new" else [operation] * len(profiles)
        if not self.extrude_ids and operations[0] != "new":
            raise ConversionError(f"{extrude_id}: {operation} requires a prior body")
        for index, item in enumerate(profiles):
            profile_sketch = str(item["sketch"])
            profile_plane = self.ensure_plane(profile_sketch)
            normal = self.nodes[profile_plane]["params"]["frame"]["z_axis"]
            profile_node = self.ensure_profile(profile_sketch, str(item["profile"]))
            node_id = self._extrude_node(
                extrude_id, profile_node, operations[index], extent_type,
                extent_one, extent_two, taper_one, normal, index, len(profiles),
            )
            self.extrude_ids.append(node_id)
            if node_id not in self.order:
                self.order.append(node_id)

    def convert(self) -> dict[str, Any]:
        for item in self.sequence:
            if item.get("type") == "Sketch":
                self.ensure_plane(str(item["entity"]))
            elif item.get("type") == "ExtrudeFeature":
                self.add_extrude(str(item["entity"]))
        if not self.extrude_ids:
            raise ConversionError("no ExtrudeFeature produced a solid")
        bbox = self.payload.get("properties", {}).get("bounding_box")
        evidence = {}
        source = {
            "format": "deepcad-1",
            "file": self.source_name,
            "units": "m",
        }
        if bbox:
            minimum = bbox.get("min_point") or {}
            maximum = bbox.get("max_point") or {}
            bbox_m = [
                float(minimum.get("x", 0.0)), float(minimum.get("y", 0.0)), float(minimum.get("z", 0.0)),
                float(maximum.get("x", 0.0)), float(maximum.get("y", 0.0)), float(maximum.get("z", 0.0)),
            ]
            source["bounding_box_m"] = bbox_m
            evidence["bounding_box_mm"] = [item * M_TO_MM for item in bbox_m]
        return {
            "ir_version": "0.1",
            "kind": "part",
            "units": "mm",
            "tolerance": {"length": TOL, "angle_deg": 1e-6},
            "entities": self.nodes,
            "sequence": self.order,
            "results": [self.extrude_ids[-1]],
            "extensions": {"source": source},
            "unsupported": self.unsupported,
            "evidence": evidence,
        }


def convert(payload: Mapping[str, Any], source_name: str = "<deepcad>") -> dict[str, Any]:
    return DeepCADConverter(payload, source_name).convert()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="IR file (default: out/cases/<stem>/model.ir.json)")
    args = parser.parse_args(argv)
    from json2scapi.runtime.output_layout import case_dir
    output = args.out or case_dir(args.input.stem) / "model.ir.json"
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8", errors="replace"))
        ir = convert(payload, str(args.input))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"{args.input}: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(ir, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{args.input} -> {output} ({len(ir['entities'])} entities, {len(ir['unsupported'])} unsupported)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
