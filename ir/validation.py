"""Validate CAD IR v0.1 documents.

Usage:
    python validate_ir.py path/to/model.ir.json

The JSON Schema checks structure. This module additionally checks semantics:
references, DAG cycles, sequence consistency, frames, loop closure, profiles,
and extrude parameters.
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from json2scapi.ir.regions import profile_regions

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - schema validation is optional
    Draft202012Validator = None  # type: ignore[assignment]

from json2scapi.runtime.paths import SCHEMA_PATH
ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")
SKETCH_CURVES = {"sketch.line", "sketch.arc", "sketch.circle"}


def _finite(value: Any) -> bool:
    return not isinstance(value, bool) and math.isfinite(float(value))


def _walk_numbers(value: Any, path: str = "$") -> Iterable[str]:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if not _finite(value):
            yield f"{path}: non-finite number"
        return
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_numbers(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_numbers(item, f"{path}[{index}]")


def _sub(a: list[float], b: list[float]) -> list[float]:
    return [float(x) - float(y) for x, y in zip(a, b)]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(float(x) * float(y) for x, y in zip(a, b))


def _norm(a: list[float]) -> float:
    return math.sqrt(_dot(a, a))


def _close(a: list[float], b: list[float], tolerance: float) -> bool:
    return _norm(_sub(a, b)) <= tolerance


def _schema_errors(payload: dict[str, Any]) -> list[str]:
    if Draft202012Validator is None:
        return ["jsonschema is not installed; skipped JSON Schema validation"]
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    return [
        f"{'/'.join(str(p) for p in error.absolute_path) or '$'}: {error.message}"
        for error in sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path))
    ]


def _graph(payload: dict[str, Any], errors: list[str]) -> dict[str, list[str]]:
    entities = payload.get("entities", {})
    graph: dict[str, list[str]] = {}
    for entity_id, entity in entities.items():
        if not ID_PATTERN.match(entity_id):
            errors.append(f"entities.{entity_id}: invalid id")
        inputs = list(entity.get("inputs", []))
        for ref in inputs:
            if ref not in entities:
                errors.append(f"entities.{entity_id}.inputs: missing reference {ref!r}")
        if entity.get("op") == "sketch.profile":
            plane = entity.get("params", {}).get("plane")
            if not isinstance(plane, str):
                errors.append(f"entities.{entity_id}.params.plane: expected string")
            else:
                if plane not in entities:
                    errors.append(f"entities.{entity_id}.params.plane: missing reference {plane!r}")
                elif entities[plane].get("op") != "sketch.plane":
                    errors.append(
                        f"entities.{entity_id}.params.plane: {plane!r} is not a sketch.plane"
                    )
                inputs.append(plane)
        graph[entity_id] = list(dict.fromkeys(inputs))
    return graph


def _topological_order(graph: dict[str, list[str]], errors: list[str]) -> list[str]:
    state: dict[str, int] = {}
    order: list[str] = []

    def visit(node: str, stack: list[str]) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            cycle = " -> ".join([*stack, node])
            errors.append(f"entities: dependency cycle: {cycle}")
            return
        state[node] = 1
        stack.append(node)
        for ref in graph.get(node, []):
            if ref in graph:
                visit(ref, stack)
        stack.pop()
        state[node] = 2
        order.append(node)

    for node in graph:
        visit(node, [])
    return order


def _sequence_errors(
    payload: dict[str, Any], graph: dict[str, list[str]], errors: list[str]
) -> None:
    entities = payload.get("entities", {})
    sequence = payload.get("sequence", [])
    for ref in sequence:
        if ref not in entities:
            errors.append(f"sequence: missing reference {ref!r}")
    for entity_id, entity in entities.items():
        if entity.get("op") == "feature.extrude" and entity_id not in sequence:
            errors.append(f"sequence: missing feature.extrude {entity_id!r}")
    first_extrude = next((ref for ref in sequence if ref in entities and
                          entities[ref].get("op") == "feature.extrude"), None)
    if first_extrude and entities[first_extrude].get("params", {}).get("operation") != "new":
        errors.append(f"sequence: first extrude {first_extrude!r} must create a new body")
    index = {ref: position for position, ref in enumerate(sequence)}
    for node, refs in graph.items():
        if node not in index:
            continue
        for ref in refs:
            if ref in index and index[ref] > index[node]:
                errors.append(
                    f"sequence: {ref!r} must appear before {node!r} (dependency order)"
                )


def _frame_errors(entity_id: str, frame: Any, errors: list[str]) -> None:
    if not isinstance(frame, dict):
        errors.append(f"entities.{entity_id}.params.frame: expected object")
        return
    axes: dict[str, list[float]] = {}
    for name in ("x_axis", "y_axis", "z_axis"):
        value = frame.get(name)
        if not isinstance(value, list) or len(value) != 3:
            errors.append(f"entities.{entity_id}.params.frame.{name}: expected 3 numbers")
            continue
        if not all(_finite(item) for item in value):
            errors.append(f"entities.{entity_id}.params.frame.{name}: non-finite value")
            continue
        axes[name] = [float(item) for item in value]
        if abs(_norm(axes[name]) - 1.0) > 1e-9:
            errors.append(f"entities.{entity_id}.params.frame.{name}: not a unit vector")
    if len(axes) == 3:
        for first, second in (("x_axis", "y_axis"), ("x_axis", "z_axis"), ("y_axis", "z_axis")):
            if abs(_dot(axes[first], axes[second])) > 1e-9:
                errors.append(
                    f"entities.{entity_id}.params.frame: {first} and {second} are not orthogonal"
                )
        cross = [
            axes["x_axis"][1] * axes["y_axis"][2] - axes["x_axis"][2] * axes["y_axis"][1],
            axes["x_axis"][2] * axes["y_axis"][0] - axes["x_axis"][0] * axes["y_axis"][2],
            axes["x_axis"][0] * axes["y_axis"][1] - axes["x_axis"][1] * axes["y_axis"][0],
        ]
        if not _close(cross, axes["z_axis"], 1e-9):
            errors.append(f"entities.{entity_id}.params.frame: not a right-handed frame")
    origin = frame.get("origin")
    if not isinstance(origin, list) or len(origin) != 3:
        errors.append(f"entities.{entity_id}.params.frame.origin: expected 3 numbers")


def _loop_errors(
    entity_id: str,
    entity: dict[str, Any],
    entities: dict[str, Any],
    tolerance: float,
    errors: list[str],
) -> None:
    edge_ids = entity.get("inputs", [])
    if not edge_ids:
        errors.append(f"entities.{entity_id}.inputs: loop has no edges")
        return
    edges = []
    for ref in edge_ids:
        edge = entities.get(ref)
        if edge is None:
            continue
        if edge.get("op") not in SKETCH_CURVES:
            errors.append(
                f"entities.{entity_id}.inputs: {ref!r} is not a sketch curve"
            )
            continue
        edges.append((ref, edge))
    if len(edges) != len(edge_ids):
        return
    circles = [item for item in edges if item[1].get("op") == "sketch.circle"]
    if circles:
        if len(edges) != 1:
            errors.append(
                f"entities.{entity_id}: a circle must be the only edge in its loop"
            )
        return
    for current, following in zip(edges, edges[1:] + edges[:1]):
        current_end = current[1].get("params", {}).get("end")
        following_start = following[1].get("params", {}).get("start")
        if not isinstance(current_end, list) or not isinstance(following_start, list):
            errors.append(f"entities.{entity_id}: line/arc loop edges need start and end")
            continue
        if not _close(current_end, following_start, tolerance):
            errors.append(
                f"entities.{entity_id}: gap between {current[0]!r}.end and {following[0]!r}.start"
            )


def _profile_errors(
    entity_id: str,
    entity: dict[str, Any],
    entities: dict[str, Any],
    tolerance: float,
    errors: list[str],
    *, geometry: bool = True,
) -> None:
    loops = entity.get("inputs", [])
    if not loops:
        errors.append(f"entities.{entity_id}.inputs: profile has no loops")
        return
    has_outer = False
    for ref in loops:
        loop = entities.get(ref)
        if loop is None:
            continue
        if loop.get("op") != "sketch.loop":
            errors.append(f"entities.{entity_id}.inputs: {ref!r} is not a sketch.loop")
            continue
        if loop.get("params", {}).get("is_outer") is True:
            has_outer = True
    if not has_outer:
        errors.append(f"entities.{entity_id}: profile needs at least one outer loop")
    regions = entity.get("params", {}).get("regions")
    if not isinstance(regions, list) or not regions:
        errors.append(f"entities.{entity_id}.params.regions: expected nonempty region list")
        return
    grouped: list[str] = []
    provided: dict[str, set[str]] = {}
    for index, region in enumerate(regions):
        if not isinstance(region, dict) or not isinstance(region.get("outer"), str) or not isinstance(region.get("holes"), list):
            errors.append(f"entities.{entity_id}.params.regions[{index}]: expected outer and holes")
            return
        outer, holes = region["outer"], region["holes"]
        if any(not isinstance(hole, str) for hole in holes) or len(set(holes)) != len(holes):
            errors.append(f"entities.{entity_id}.params.regions[{index}].holes: invalid or duplicate loop")
            return
        if outer in provided:
            errors.append(f"entities.{entity_id}.params.regions: duplicate outer {outer!r}")
        provided[outer] = set(holes)
        grouped.extend([outer, *holes])
    if len(grouped) != len(loops) or set(grouped) != set(loops):
        errors.append(f"entities.{entity_id}.params.regions: each input loop must appear exactly once")
    if any(ref not in entities or entities[ref].get("op") != "sketch.loop" for ref in loops):
        return
    if not geometry:
        return
    try:
        expected_regions = profile_regions(entity, entities, tolerance, prepared=True)
    except (ValueError, KeyError, TypeError) as error:
        errors.append(f"entities.{entity_id}.params.regions: invalid geometry: {error}")
        return
    expected = {outer: set(holes) for outer, holes in expected_regions}
    if provided != expected:
        errors.append(f"entities.{entity_id}.params.regions: does not match loop containment")
    for loop_id in loops:
        if entities[loop_id]["params"].get("is_outer") is not (loop_id in expected):
            errors.append(f"entities.{loop_id}.params.is_outer: disagrees with profile regions")


def _extrude_errors(
    entity_id: str,
    entity: dict[str, Any],
    entities: dict[str, Any],
    errors: list[str],
) -> None:
    inputs = entity.get("inputs", [])
    if len(inputs) != 1:
        errors.append(f"entities.{entity_id}.inputs: extrude needs exactly one profile")
    else:
        profile = entities.get(inputs[0])
        if profile is None or profile.get("op") != "sketch.profile":
            errors.append(
                f"entities.{entity_id}.inputs: {inputs[0]!r} is not a sketch.profile"
            )
    params = entity.get("params", {})
    distance = params.get("distance")
    if not isinstance(distance, (int, float)) or isinstance(distance, bool) or distance <= 0:
        errors.append(f"entities.{entity_id}.params.distance: must be positive")
    direction = params.get("direction")
    if not isinstance(direction, list) or len(direction) != 3:
        errors.append(f"entities.{entity_id}.params.direction: expected 3 numbers")
    elif abs(_norm(direction) - 1.0) > 1e-9:
        errors.append(f"entities.{entity_id}.params.direction: not a unit vector")
    extent = params.get("extent")
    if extent == "two_sides":
        distance2 = params.get("distance2")
        if not isinstance(distance2, (int, float)) or isinstance(distance2, bool) or distance2 <= 0:
            errors.append(
                f"entities.{entity_id}.params.distance2: required and positive for two_sides"
            )


def _source_errors(payload: dict[str, Any], errors: list[str]) -> None:
    """Validate declared provenance without requiring it for geometry execution."""
    entities = payload.get("entities", {})
    for entity_id, entity in entities.items():
        if "source" not in entity:
            continue
        source = entity["source"]
        path = f"entities.{entity_id}.source"
        if not isinstance(source, dict):
            errors.append(f"{path}: expected object")
            continue
        for key in ("format", "entity_id", "profile_id", "curve_id"):
            if key in source and (not isinstance(source[key], str) or not source[key]):
                errors.append(f"{path}.{key}: expected nonempty string")
        for key in ("profile_index", "loop_index"):
            if key in source and (not isinstance(source[key], int) or
                                  isinstance(source[key], bool) or source[key] < 0):
                errors.append(f"{path}.{key}: expected zero-based nonnegative integer")
        for key in ("ordered", "reversed"):
            if key in source and not isinstance(source[key], bool):
                errors.append(f"{path}.{key}: expected boolean")
        projections = source.get("planarity_projection", [])
        if not isinstance(projections, list):
            errors.append(f"{path}.planarity_projection: expected array")
        else:
            for correction in projections:
                if (not isinstance(correction, dict)
                        or correction.get("point") not in {"start_point", "end_point", "center_point"}
                        or not isinstance(correction.get("z_mm"), (int, float))
                        or not isinstance(correction.get("tolerance_mm"), (int, float))):
                    errors.append(f"{path}.planarity_projection: invalid correction")
                    continue
                residual, tolerance = correction["z_mm"], correction["tolerance_mm"]
                if not (0 < tolerance <= float(payload.get("tolerance", {}).get("length", 1e-3))
                        and abs(residual) <= tolerance):
                    errors.append(f"{path}.planarity_projection: residual outside declared tolerance")
        normalization = source.get("extent_normalization")
        if normalization is not None:
            if not isinstance(normalization, dict) or normalization.get("rule") != "signed_interval_v1":
                errors.append(f"{path}.extent_normalization: invalid normalization rule")
            else:
                one, two = normalization.get("extent_one_m"), normalization.get("extent_two_m")
                bounds = normalization.get("interval_mm")
                if (not isinstance(one, (int, float)) or not isinstance(two, (int, float))
                        or not isinstance(bounds, list) or len(bounds) != 2
                        or not all(isinstance(v, (int, float)) for v in bounds)):
                    errors.append(f"{path}.extent_normalization: invalid signed interval")
                elif not all(math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-9)
                             for actual, expected in zip(bounds, [min(0, one, -two) * 1000,
                                                                  max(0, one, -two) * 1000])):
                    errors.append(f"{path}.extent_normalization: inconsistent signed interval")
    previous_key = None
    previous_index = None
    for step in payload.get("sequence", []):
        entity = entities.get(step, {})
        if entity.get("op") != "feature.extrude":
            continue
        source = entity.get("source", {})
        if not isinstance(source, dict):
            previous_key, previous_index = None, None
            continue
        key = (source.get("format"), source.get("entity_id"))
        index = source.get("profile_index")
        complete = all(isinstance(item, str) and item for item in key)
        if not complete or key != previous_key:
            previous_index = None
        if isinstance(index, int) and not isinstance(index, bool):
            if complete and previous_index is not None and index <= previous_index:
                errors.append(f"entities.{step}.source.profile_index: must increase within a contiguous source feature")
            previous_index = index
        previous_key = key


def _validate_ir(payload: dict[str, Any], *, schema: bool = True, geometry: bool = True) -> list[str]:
    errors: list[str] = []
    if schema:
        errors.extend(_schema_errors(payload))
        if errors:
            return errors
    entities = payload.get("entities")
    if not isinstance(entities, dict):
        return errors
    for error in _walk_numbers(payload):
        errors.append(error)
    graph = _graph(payload, errors)
    _topological_order(graph, errors)
    _sequence_errors(payload, graph, errors)
    _source_errors(payload, errors)
    for ref in payload.get("results", []):
        if ref not in entities:
            errors.append(f"results: missing reference {ref!r}")
    extrude_steps = [ref for ref in payload.get("sequence", [])
                     if ref in entities and entities[ref].get("op") == "feature.extrude"]
    if extrude_steps and payload.get("results") != [extrude_steps[-1]]:
        errors.append("results: v0.1 requires the last extrude step (final body-set state)")
    tolerance = payload.get("tolerance", {}).get("length", 1e-6)
    if not isinstance(tolerance, (int, float)) or isinstance(tolerance, bool) or tolerance <= 0:
        tolerance = 1e-6
    for entity_id, entity in entities.items():
        op = entity.get("op")
        if op == "sketch.plane":
            _frame_errors(entity_id, entity.get("params", {}).get("frame"), errors)
        elif op == "sketch.loop":
            _loop_errors(entity_id, entity, entities, float(tolerance), errors)
        elif op == "sketch.profile":
            _profile_errors(entity_id, entity, entities, float(tolerance), errors, geometry=geometry)
        elif op == "feature.extrude":
            _extrude_errors(entity_id, entity, entities, errors)
    return errors


def prepare_ir(payload: dict[str, Any], *, schema: bool = True):
    """Return (prepared IR, adjustment metadata), without mutating the input."""
    from json2scapi.ir.preparation import prepare_geometry
    errors = _validate_ir(payload, schema=schema, geometry=False)
    if errors:
        raise ValueError('invalid IR:\n' + '\n'.join(errors))
    try:
        prepared, metadata = prepare_geometry(payload)
    except (ValueError, KeyError, TypeError, OverflowError) as error:
        raise ValueError(f'invalid IR geometry: {error}') from error
    errors = _validate_ir(prepared, schema=False, geometry=True)
    if errors:
        raise ValueError('invalid prepared IR:\n' + '\n'.join(errors))
    return prepared, metadata


def validate_ir(payload: dict[str, Any], *, schema: bool = True) -> list[str]:
    try:
        prepare_ir(payload, schema=schema)
    except (ValueError, KeyError, TypeError, OverflowError) as error:
        return str(error).splitlines()
    return []


def main(argv: list[str] | None = None) -> int:
    paths = [Path(item) for item in (argv if argv is not None else sys.argv[1:])]
    if not paths:
        print("usage: python validate_ir.py path/to/model.ir.json", file=sys.stderr)
        return 2
    failed = 0
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(f"{path}: {type(error).__name__}: {error}")
            failed += 1
            continue
        errors = validate_ir(payload)
        if errors:
            failed += 1
            print(f"{path}: {len(errors)} error(s)")
            for error in errors:
                print(f"  - {error}")
        else:
            print(f"{path}: ok")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
