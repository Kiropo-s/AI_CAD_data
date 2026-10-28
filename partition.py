"""Verified planar partition fallback for direct STEP reconstruction.

The partitioner uses source planar faces only to choose finite slabs. Each slab is
intersected with the source solid in OCC, then reconstructed independently by the
same direct prism/hybrid recognizers. The source shape is used for measurement and
sectioning only; it is never emitted as an operand.
"""
from __future__ import annotations

from dataclasses import dataclass
import itertools
import math
import time

import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Fuse
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.GeomAbs import GeomAbs_Plane
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.TopTools import TopTools_ListOfShape

from json2scapi.evaluation.metrics import area, bbox, solid_count, symmetric_difference_volume, volume
from step2code_direct.geometry import (UnsupportedGeometry, explore, recognize_prism,
                                       source_solids)
from step2code_direct.hybrid import _boolean_builder, _valid_single, recognize_hybrid
from OCP.BRepAdaptor import BRepAdaptor_Surface


@dataclass(frozen=True)
class PartitionPiece:
    """A reconstructed native piece and the exact shape it represents."""
    result: object
    shape: object
    method: str


@dataclass(frozen=True)
class PartitionResult:
    """A verified union of independently reconstructed native pieces."""
    parts: tuple
    shape: object
    evidence: dict


def _canonical_axis(axis):
    axis = np.asarray(axis, dtype=float)
    norm = float(np.linalg.norm(axis))
    if not math.isfinite(norm) or norm <= 1e-12:
        raise ValueError('invalid planar normal')
    axis = axis / norm
    pivot = int(np.argmax(np.abs(axis)))
    if axis[pivot] < 0:
        axis = -axis
    return axis


def _projection_limits(shape, axis):
    limits = np.asarray(bbox(shape), dtype=float)
    corners = np.array(list(itertools.product(*[(limits[i], limits[i + 3]) for i in range(3)])))
    values = corners @ axis
    return float(values.min()), float(values.max())


def _plane_groups(solid, tolerance):
    """Return unique interior plane levels grouped by canonical normal."""
    groups = []
    for face_index, raw_face in enumerate(explore(solid, TopAbs_FACE), 1):
        face = TopoDS.Face_s(raw_face)
        surface = BRepAdaptor_Surface(face)
        if surface.GetType() != GeomAbs_Plane:
            continue
        plane = surface.Plane()
        axis = _canonical_axis(plane.Axis().Direction().Coord())
        group = next((item for item in groups
                      if float(np.linalg.norm(np.cross(axis, item['axis']))) <= 1e-8), None)
        if group is None:
            group = {'axis': axis, 'levels': [], 'faces': []}
            groups.append(group)
        position = float(np.dot(np.asarray(plane.Location().Coord(), dtype=float), axis))
        if not any(abs(position - old) <= tolerance for old in group['levels']):
            group['levels'].append(position)
        group['faces'].append(face_index)
    return groups


def _candidate_score(groups):
    """Rank splits by useful separation while penalizing combinatorial growth."""
    parts = math.prod(max(1, len(group[1]) - 1) for group in groups)
    internal_levels = sum(max(0, len(group[1]) - 2) for group in groups)
    axis_bonus = 0.15 * len(groups)
    # More internal levels can expose stepped features, but the part count is
    # the dominant cost because every cell invokes OCC and a recognizer.
    return float(axis_bonus + internal_levels / max(1.0, parts) - 0.025 * parts)


def _candidate_axes(solid, options):
    tolerance = options.length_tolerance
    candidates = []
    for group in _plane_groups(solid, tolerance):
        axis = group['axis']
        low, high = _projection_limits(solid, axis)
        levels = sorted(level for level in group['levels']
                        if low + tolerance < level < high - tolerance)
        if not levels:
            continue
        intervals = [low - tolerance] + levels + [high + tolerance]
        count = len(intervals) - 1
        if count > options.max_partition_parts:
            continue
        candidates.append((axis, tuple(intervals), tuple(group['faces'])))
    candidates.sort(key=lambda item: (-_candidate_score((item,)), len(item[1]), tuple(np.round(item[0], 12))))
    # One axis is the normal case. Pairing independent plane families handles
    # orthogonal stepped parts while remaining bounded by the part budget.
    selected = candidates[:options.max_partition_axes]
    if options.max_partition_axes >= 2:
        for first, second in itertools.combinations(candidates, 2):
            if abs(float(np.dot(first[0], second[0]))) >= 1.0 - 1e-8:
                continue
            if (len(first[1]) - 1) * (len(second[1]) - 1) > options.max_partition_parts:
                continue
            selected.append((first, second))
    # Normalize the representation: a candidate is a tuple of axis groups.
    normalized = []
    for item in selected:
        if len(item) == 3 and isinstance(item[0], np.ndarray):
            groups = (item,)
        else:
            groups = item
        signature = tuple((tuple(np.round(group[0], 10)), tuple(np.round(group[1], 10)))
                          for group in groups)
        score = _candidate_score(groups)
        if score < options.partition_score_min:
            continue
        if not any(signature == old[0] for old in normalized):
            normalized.append((signature, groups, score))
    normalized.sort(key=lambda item: (-item[2], math.prod(max(1, len(group[1]) - 1) for group in item[1]), item[0]))
    return [groups for _, groups, _ in normalized]


def _orthonormal_frame(axis):
    axis = np.asarray(axis, dtype=float)
    seed = np.eye(3)[int(np.argmin(np.abs(axis)))]
    x_axis = seed - axis * float(np.dot(seed, axis))
    x_axis /= np.linalg.norm(x_axis)
    y_axis = np.cross(axis, x_axis)
    y_axis /= np.linalg.norm(y_axis)
    return x_axis, y_axis


def _slab_box(shape, axis_levels, overlap=0.0):
    """Construct a generous oriented box for one interval along each axis."""
    bounds = np.asarray(bbox(shape), dtype=float)
    diagonal = float(np.linalg.norm(bounds[3:] - bounds[:3]))
    if not math.isfinite(diagonal) or diagonal <= 0:
        raise ValueError('source solid has no finite extent')
    axis, low, high = axis_levels
    low -= overlap
    high += overlap
    x_axis, y_axis = _orthonormal_frame(axis)
    center = (bounds[:3] + bounds[3:]) / 2.0
    origin = center + axis * (low - float(np.dot(center, axis))) - diagonal * (x_axis + y_axis)
    frame = gp_Ax2(gp_Pnt(*origin.tolist()), gp_Dir(*axis.tolist()), gp_Dir(*x_axis.tolist()))
    maker = BRepPrimAPI_MakeBox(frame, 2.0 * diagonal, 2.0 * diagonal, high - low)
    maker.Build()
    if not maker.IsDone():
        raise ValueError('partition slab construction failed')
    return maker.Shape()


def _section_solids(source, slab):
    operation = _boolean_builder(BRepAlgoAPI_Common, source, slab)
    if not operation.IsDone():
        raise ValueError('partition section common failed')
    section = operation.Shape()
    return [TopoDS.Solid_s(item) for item in explore(section, TopAbs_SOLID)
            if not TopoDS.Solid_s(item).IsNull() and solid_count(TopoDS.Solid_s(item)) == 1]


def _union(first, second):
    operation = _boolean_builder(BRepAlgoAPI_Fuse, first, second)
    if not operation.IsDone():
        raise ValueError('partition union failed')
    operation.SimplifyResult(True, True)
    result = operation.Shape()
    if result.IsNull() or solid_count(result) < 1 or not BRepCheck_Analyzer(result).IsValid():
        raise ValueError('partition union did not produce valid geometry')
    # A deterministic pairwise order may temporarily leave disconnected
    # components. They are allowed during accumulation; the final source
    # coverage check still requires one solid.
    return result


def _bbox_may_touch(first, second, tolerance):
    a = np.asarray(bbox(first), dtype=float)
    b = np.asarray(bbox(second), dtype=float)
    return all(a[i] <= b[i + 3] + tolerance and b[i] <= a[i + 3] + tolerance
               for i in range(3))


def _contact_order(pieces, tolerance):
    """Order pieces so each SCAPI union adds a face-contacting component."""
    count = len(pieces)
    if count < 2:
        return list(range(count))
    graph = [set() for _ in pieces]
    for left in range(count):
        for right in range(left + 1, count):
            if not _bbox_may_touch(pieces[left].shape, pieces[right].shape, tolerance):
                continue
            try:
                common = _boolean_builder(BRepAlgoAPI_Common, pieces[left].shape, pieces[right].shape)
                if not common.IsDone():
                    continue
                # A positive common area means a finite shared face. Edge-only
                # contacts are deliberately excluded because SCAPI rejects
                # them as a valid single-solid union.
                if (volume(common.Shape()) > max(tolerance ** 3, 1e-12)
                        or area(common.Shape()) > max(tolerance * tolerance, 1e-12)):
                    graph[left].add(right)
                    graph[right].add(left)
            except Exception:
                continue
    start = max(range(count), key=lambda item: (len(graph[item]), -item))
    order, seen = [], {start}
    frontier = [start]
    while frontier:
        current = frontier.pop(0)
        order.append(current)
        candidates = sorted(graph[current] - seen, key=lambda item: (-len(graph[item]), item))
        seen.update(candidates)
        frontier.extend(candidates)
    # Preserve a complete result only when the contact graph is connected.
    # Otherwise the caller can report the disconnected geometry explicitly.
    if len(order) != count:
        return list(range(count))
    return order


def _recognize_piece(piece, index, options, deadline):
    if time.monotonic() >= deadline:
        raise UnsupportedGeometry('partition time budget exhausted')
    try:
        return PartitionPiece(recognize_prism(piece, index, options), piece, 'prism')
    except UnsupportedGeometry as prism_failure:
        if not options.enable_hybrid:
            raise
        result = recognize_hybrid(piece, index, options,
                                  seed_prisms=prism_failure.native_candidates)
        return PartitionPiece(result, piece, 'hybrid')


def recognize_partition(solid, index, options, initial_failure=None):
    """Find a bounded planar decomposition whose emitted pieces cover the source exactly."""
    started = time.monotonic()
    deadline = started + options.partition_timeout_seconds
    attempts = []
    candidates = _candidate_axes(solid, options)
    diagnostics = dict(status='not_recognized', source_solid_index=index,
                       strategy='verified_planar_partition_union', candidate_count=len(candidates),
                       attempts=attempts, part_limit=options.max_partition_parts,
                       axis_limit=options.max_partition_axes,
                       time_budget_seconds=options.partition_timeout_seconds)
    source_volume = volume(solid)
    if not math.isfinite(source_volume) or source_volume <= 0:
        raise UnsupportedGeometry('partition source has nonpositive volume', diagnostics)
    for candidate_index, groups in enumerate(candidates, 1):
        if time.monotonic() >= deadline:
            break
        intervals = [tuple(zip(group[1][:-1], group[1][1:])) for group in groups]
        expected_count = math.prod(len(item) for item in intervals)
        attempt = dict(candidate_index=candidate_index,
                       axes=[group[0].tolist() for group in groups],
                       interval_counts=[len(item) for item in intervals],
                       expected_parts=expected_count,
                       candidate_score=_candidate_score(groups))
        attempts.append(attempt)
        pieces = []
        valid = True
        try:
            for cell in itertools.product(*intervals):
                if time.monotonic() >= deadline:
                    valid = False
                    break
                section_shape = solid
                cell_shapes = []
                for group, bounds_pair in zip(groups, cell):
                    cell_shapes.append(_slab_box(
                        solid, (group[0], bounds_pair[0], bounds_pair[1]),
                        overlap=options.partition_overlap_mm))
                # Intersecting the source with each slab is equivalent to one
                # common with the product cell, but keeps kernel calls simple.
                for slab in cell_shapes:
                    common = _boolean_builder(BRepAlgoAPI_Common, section_shape, slab)
                    if not common.IsDone():
                        raise ValueError('partition cell common failed')
                    section_shape = common.Shape()
                for part in explore(section_shape, TopAbs_SOLID):
                    native = TopoDS.Solid_s(part)
                    if native.IsNull() or volume(native) <= options.length_tolerance ** 3:
                        continue
                    pieces.append(_recognize_piece(native, index, options, deadline))
            if not valid or not pieces:
                attempt['status'] = 'incomplete'
                continue
            joined = pieces[0].shape
            for piece in pieces[1:]:
                joined = _union(joined, piece.shape)
            if solid_count(joined) != 1:
                attempt.update(status='disconnected_union', actual_parts=len(pieces),
                               union_solid_count=solid_count(joined))
                continue
            difference = symmetric_difference_volume(solid, joined) / source_volume
            attempt.update(status='verified' if difference <= options.reconstruction_relative_tolerance
                           else 'geometry_mismatch', symmetric_difference_relative=difference,
                           actual_parts=len(pieces))
            if difference > options.reconstruction_relative_tolerance:
                continue
            order = _contact_order(pieces, options.length_tolerance)
            ordered_pieces = tuple(pieces[item] for item in order)
            diagnostics.update(status='recognized', selected_candidate=candidate_index,
                               part_count=len(pieces), piece_order=order,
                               symmetric_difference_relative=difference,
                               elapsed_seconds=time.monotonic() - started)
            return PartitionResult(ordered_pieces, joined, diagnostics)
        except (UnsupportedGeometry, ValueError) as exc:
            attempt.update(status='failed', error=f'{type(exc).__name__}: {exc}',
                           actual_parts=len(pieces))
            continue
    diagnostics.update(reason='no_verified_partition_within_budget',
                       elapsed_seconds=time.monotonic() - started)
    if initial_failure:
        diagnostics['prior_direct_failure'] = initial_failure
    raise UnsupportedGeometry('Planar partition did not produce a verified reconstruction', diagnostics)
