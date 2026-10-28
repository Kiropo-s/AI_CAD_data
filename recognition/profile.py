"""Exact planar profile extraction for the P2A recognition stage.

This module converts OCC boundary evidence into the same curve semantics used
by the plan kernel.  It never samples a curve into a polyline and it does not
create a plan by itself; ``emit_profile_nodes`` is the single explicit bridge
for the existing step-plan/1 profile nodes.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
from OCP.GeomAbs import GeomAbs_BSplineCurve, GeomAbs_Circle, GeomAbs_Ellipse, GeomAbs_Line
from OCP.TopAbs import TopAbs_EDGE, TopAbs_REVERSED, TopAbs_WIRE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

from step2code.plan.model import node
from .context import record_source


class ProfileError(ValueError):
    """A deterministic profile failure with a machine-readable reason code."""

    def __init__(self, message: str, reason_code: str = 'profile.curve_unsupported', details: dict[str, Any] | None = None):
        super().__init__(message)
        self.reason_code = reason_code
        self.details = details or {}


@dataclass(frozen=True)
class CurveEvidence:
    edge: Any
    kind: str
    operation: str
    params: dict[str, Any]
    source_edge_index: int | None


@dataclass(frozen=True)
class LoopEvidence:
    wire: Any
    kind: str
    orientation: str
    curves: tuple[CurveEvidence, ...]


@dataclass(frozen=True)
class ProfileEvidence:
    face: Any
    frame: dict[str, Any]
    loops: tuple[LoopEvidence, ...]
    outer_index: int
    inner_indices: tuple[int, ...]
    self_intersection_status: str


def _kind_name(value: Any) -> str:
    return str(value).split('.')[-1]


def _explore(shape: Any, kind: Any) -> list[Any]:
    explorer = TopExp_Explorer(shape, kind)
    result = []
    while explorer.More():
        result.append(explorer.Current())
        explorer.Next()
    return result


def _coordinates(point: Any, frame: dict[str, Any]) -> list[float]:
    offset = np.array(point.Coord(), dtype=float) - np.array(frame['origin'], dtype=float)
    return [float(offset @ np.array(frame['x_axis'], dtype=float)),
            float(offset @ np.array(frame['y_axis'], dtype=float))]


def _plane_residual(point: Any, frame: dict[str, Any]) -> float:
    offset = np.array(point.Coord(), dtype=float) - np.array(frame['origin'], dtype=float)
    return abs(float(offset @ np.array(frame['z_axis'], dtype=float)))


def _require_planar(value: float, tolerance: float, *, entity: str, details: dict[str, Any] | None = None) -> None:
    if not math.isfinite(value) or value > tolerance:
        raise ProfileError(f'Profile curve is not planar: residual={value}', 'profile.non_planar',
                           {'entity': entity, 'residual_mm': value, **(details or {})})


def _edge_parameter_range(adaptor: BRepAdaptor_Curve, edge: Any) -> tuple[float, float]:
    first, last = float(adaptor.FirstParameter()), float(adaptor.LastParameter())
    if not math.isfinite(first + last):
        raise ProfileError('Profile edge has an unbounded parameter range', 'profile.curve_unsupported',
                           {'reason': 'unbounded_parameter_range'})
    if edge.Orientation() == TopAbs_REVERSED:
        first, last = last, first
    return first, last


def _ellipse(adaptor: BRepAdaptor_Curve, frame: dict[str, Any], first: float, last: float,
             tolerance: float) -> dict[str, Any]:
    ellipse = adaptor.Ellipse()
    sweep = float(last - first)
    _require_planar(_plane_residual(ellipse.Location(), frame), tolerance, entity='ellipse.center')
    normal = np.array(ellipse.Axis().Direction().Coord(), dtype=float)
    z_axis = np.array(frame['z_axis'], dtype=float)
    if abs(abs(float(normal @ z_axis)) - 1.0) > tolerance:
        raise ProfileError('Ellipse plane is not parallel to profile plane', 'profile.non_planar',
                           {'curve_type': 'GeomAbs_Ellipse', 'normal_dot': float(normal @ z_axis)})
    if abs(abs(sweep) - 2 * math.pi) > 1e-7:
        raise ProfileError('Partial ellipse cannot be emitted by the current exact SCAPI contract',
                           'profile.curve_unsupported',
                           {'curve_type': 'GeomAbs_Ellipse', 'partial': True,
                            'source_parameter_range': [first, last],
                            'backend': 'full_ellipse_only'})
    x3 = np.array(ellipse.XAxis().Direction().Coord(), dtype=float)
    y3 = np.array(ellipse.YAxis().Direction().Coord(), dtype=float)
    if abs(float(x3 @ z_axis)) > tolerance or abs(float(y3 @ z_axis)) > tolerance:
        raise ProfileError('Ellipse axes are not planar in the profile frame', 'profile.non_planar',
                           {'curve_type': 'GeomAbs_Ellipse'})
    x2 = np.array([float(x3 @ frame['x_axis']), float(x3 @ frame['y_axis'])])
    y2 = np.array([float(y3 @ frame['x_axis']), float(y3 @ frame['y_axis'])])
    if abs(float(x2 @ y2)) > 1e-8 or abs(np.linalg.norm(x2) - 1.0) > 1e-8 or abs(np.linalg.norm(y2) - 1.0) > 1e-8:
        raise ProfileError('Ellipse axes are not orthonormal in the profile frame', 'profile.curve_unsupported',
                           {'curve_type': 'GeomAbs_Ellipse'})
    return {
        'center': _coordinates(ellipse.Location(), frame),
        'major_radius': float(ellipse.MajorRadius()),
        'minor_radius': float(ellipse.MinorRadius()),
        'major_axis_angle': float(math.atan2(x2[1], x2[0])),
        'winding': 'ccw' if sweep > 0 else 'cw',
        'provenance': 'bounded OCC ellipse; exact native curve, no fitting',
        'source_parameter_range': [first, last],
    }


def extract_curve(edge: Any, frame: dict[str, Any], tolerance: float = 1e-6,
                  max_spline_poles: int = 256, source_edge_index: int | None = None) -> CurveEvidence:
    """Extract one exact planar curve and its oriented source parameter range."""
    adaptor = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
    first, last = _edge_parameter_range(adaptor, edge)
    start_3d, end_3d = adaptor.Value(first), adaptor.Value(last)
    _require_planar(_plane_residual(start_3d, frame), tolerance, entity='curve.start')
    _require_planar(_plane_residual(end_3d, frame), tolerance, entity='curve.end')
    start, end = _coordinates(start_3d, frame), _coordinates(end_3d, frame)
    kind = adaptor.GetType()
    kind_name = _kind_name(kind)
    if kind == GeomAbs_Line:
        if math.dist(start, end) <= tolerance:
            raise ProfileError('Degenerate straight profile edge', 'profile.curve_unsupported',
                               {'curve_type': kind_name, 'reason': 'degenerate_line'})
        params = {'start': start, 'end': end, 'source_parameter_range': [first, last]}
        return CurveEvidence(edge, kind_name, 'sketch.line', params, source_edge_index)
    if kind == GeomAbs_Ellipse:
        params = _ellipse(adaptor, frame, first, last, tolerance)
        params.update({'start': start, 'end': end})
        return CurveEvidence(edge, kind_name, 'sketch.ellipse', params, source_edge_index)
    if kind == GeomAbs_BSplineCurve:
        if max_spline_poles < 1:
            raise ProfileError('B-spline profile support is disabled by the current limit', 'profile.curve_unsupported',
                               {'curve_type': kind_name, 'reason': 'spline_limit'})
        spline = adaptor.BSpline().Copy()
        spline.Segment(min(first, last), max(first, last), 1e-12)
        if first > last:
            spline.Reverse()
        if spline.NbPoles() > max_spline_poles or spline.Degree() > 25:
            raise ProfileError('Section B-spline exceeds supported pole count or degree', 'profile.curve_unsupported',
                               {'curve_type': kind_name, 'poles': spline.NbPoles(), 'degree': spline.Degree(),
                                'max_poles': max_spline_poles})
        poles = [spline.Pole(i) for i in range(1, spline.NbPoles() + 1)]
        residual = max((_plane_residual(point, frame) for point in poles), default=0.0)
        _require_planar(residual, tolerance, entity='bspline.poles', details={'curve_type': kind_name})
        params = {
            'control_points': [_coordinates(point, frame) for point in poles],
            'degree': spline.Degree(),
            'knots': [float(spline.Knot(i)) for i in range(1, spline.NbKnots() + 1)],
            'multiplicities': [spline.Multiplicity(i) for i in range(1, spline.NbKnots() + 1)],
            'weights': [float(spline.Weight(i)) for i in range(1, spline.NbPoles() + 1)],
            'periodic': spline.IsPeriodic(),
            'pole_plane_residual_mm': residual,
            'provenance': 'bounded kernel section B-spline; no converter sampling or fitting',
            'source_parameter_range': [first, last],
            'trim': [spline.FirstParameter(), spline.LastParameter()],
            # The extracted control points are already oriented with the edge.
            # Keep the plan flag false so replay does not reverse them twice.
            'reversed': False,
            'start': start, 'end': end,
        }
        return CurveEvidence(edge, kind_name, 'sketch.bspline', params, source_edge_index)
    if kind != GeomAbs_Circle:
        raise ProfileError(f'Unsupported profile curve: {kind_name}', 'profile.curve_unsupported',
                           {'curve_type': kind_name})
    circle = adaptor.Circle()
    _require_planar(_plane_residual(circle.Location(), frame), tolerance, entity='circle.center')
    normal = np.array(circle.Axis().Direction().Coord(), dtype=float)
    z_axis = np.array(frame['z_axis'], dtype=float)
    if abs(abs(float(normal @ z_axis)) - 1.0) > tolerance:
        raise ProfileError('Circle plane is not parallel to profile plane', 'profile.non_planar',
                           {'curve_type': kind_name, 'normal_dot': float(normal @ z_axis)})
    center = _coordinates(circle.Location(), frame)
    radius = float(circle.Radius())
    sweep = float(last - first)
    if abs(abs(sweep) - 2 * math.pi) <= 1e-7:
        return CurveEvidence(edge, kind_name, 'sketch.circle',
                             {'center': center, 'radius': radius, 'winding': 'ccw' if sweep > 0 else 'cw',
                              'source_parameter_range': [first, last], 'start': start, 'end': end}, source_edge_index)
    if math.dist(start, end) <= tolerance:
        raise ProfileError('Ambiguous closed circular edge', 'profile.curve_unsupported',
                           {'curve_type': kind_name, 'reason': 'closed_partial_arc'})
    middle = _coordinates(adaptor.Value((first + last) / 2), frame)
    ccw = float(normal @ z_axis) * (last - first) > 0
    sweep_angle = abs(last - first) * (1.0 if ccw else -1.0)
    return CurveEvidence(edge, kind_name, 'sketch.arc', {
        'start': start, 'mid': middle, 'end': end, 'center': center, 'radius': radius,
        'ccw': ccw, 'start_angle': math.atan2(start[1] - center[1], start[0] - center[0]),
        'sweep_angle': sweep_angle, 'source_parameter_range': [first, last],
    }, source_edge_index)


def _wire_edges(wire: Any, face: Any) -> list[Any]:
    explorer = BRepTools_WireExplorer(TopoDS.Wire_s(wire), TopoDS.Face_s(face))
    result = []
    while explorer.More():
        result.append(explorer.Current())
        explorer.Next()
    if not result:
        raise ProfileError('Profile loop contains no ordered edges', 'profile.open_loop', {})
    return result


def _ordered_loop(face: Any, wire: Any, kind: str, frame: dict[str, Any], tolerance: float,
                  max_spline_poles: int, source_edges: list[Any]) -> LoopEvidence:
    if not BRepCheck_Analyzer(TopoDS.Wire_s(wire)).IsValid():
        raise ProfileError('Profile loop failed B-rep validity checks', 'profile.self_intersection',
                           {'loop_kind': kind})
    edges = _wire_edges(wire, face)
    curves = []
    for edge in edges:
        source_index = next((index for index, original in enumerate(source_edges, 1) if edge.IsSame(original)), None)
        curves.append(extract_curve(edge, frame, tolerance, max_spline_poles, source_index))
    for previous, current in zip(curves, curves[1:]):
        if math.dist(previous.params.get('end', []), current.params.get('start', [])) > tolerance:
            raise ProfileError('Profile loop edges are not continuous', 'profile.open_loop',
                               {'loop_kind': kind, 'previous_edge': previous.source_edge_index,
                                'current_edge': current.source_edge_index})
    if math.dist(curves[-1].params.get('end', []), curves[0].params.get('start', [])) > tolerance:
        raise ProfileError('Profile loop is open', 'profile.open_loop',
                           {'loop_kind': kind, 'first_edge': curves[0].source_edge_index,
                            'last_edge': curves[-1].source_edge_index})
    return LoopEvidence(wire, kind, _kind_name(wire.Orientation()), tuple(curves))


def extract_profile(face: Any, frame: dict[str, Any], tolerance: float = 1e-6,
                    max_spline_poles: int = 256, source_edges: list[Any] | None = None) -> ProfileEvidence:
    """Extract an ordered, planar profile with explicit outer and inner loops."""
    face = TopoDS.Face_s(face)
    outer = BRepTools.OuterWire_s(face)
    if outer.IsNull():
        raise ProfileError('Planar face has no outer wire', 'profile.open_loop', {})
    source_edges = source_edges or _explore(face, TopAbs_EDGE)
    wires = [outer]
    for wire in _explore(face, TopAbs_WIRE):
        if not wire.IsSame(outer):
            wires.append(TopoDS.Wire_s(wire))
    loops = []
    for wire in wires:
        kind = 'outer' if wire.IsSame(outer) else 'inner'
        loops.append(_ordered_loop(face, wire, kind, frame, tolerance, max_spline_poles, source_edges))
    if not loops or loops[0].kind != 'outer':
        raise ProfileError('Profile has no outer loop', 'profile.open_loop', {})
    return ProfileEvidence(face, frame, tuple(loops), 0, tuple(range(1, len(loops))), 'pass')


def emit_profile_nodes(profile: ProfileEvidence, prefix: str, source_id: str) -> tuple[dict[str, Any], str, str]:
    """Lower an extracted profile to exact step-plan/1 curve and loop nodes."""
    nodes: dict[str, Any] = {}
    loop_ids = []
    for loop_index, loop in enumerate(profile.loops):
        loop_id = f'{prefix}_loop_{loop_index + 1:02d}'
        curve_ids = []
        for curve_index, curve in enumerate(loop.curves, 1):
            curve_id = f'{loop_id}_edge_{curve_index:03d}'
            params = curve.params
            if curve.operation == 'sketch.line':
                nodes[curve_id] = node('curve.line2', start=params['start'], end=params['end'])
            elif curve.operation == 'sketch.circle':
                nodes[curve_id] = node('curve.circle2', center=params['center'], radius=params['radius'], winding=params['winding'])
            elif curve.operation == 'sketch.arc':
                nodes[curve_id] = node('curve.arc2', center=params['center'], radius=params['radius'],
                                       start_angle=params['start_angle'], sweep_angle=params['sweep_angle'])
            elif curve.operation == 'sketch.ellipse':
                nodes[curve_id] = node('curve.ellipse2', center=params['center'], major_radius=params['major_radius'],
                                       minor_radius=params['minor_radius'], major_axis_angle=params['major_axis_angle'],
                                       winding=params['winding'])
            elif curve.operation == 'sketch.bspline':
                nodes[curve_id] = node('curve.bspline2', degree=params['degree'], poles=params['control_points'],
                                       knots=params['knots'], multiplicities=params['multiplicities'], weights=params['weights'],
                                       periodic=params['periodic'], trim=params['trim'], reversed=params['reversed'])
            else:
                raise ProfileError(f'Unsupported profile operation: {curve.operation}', 'profile.curve_unsupported', {})
            record_source(curve_id, {'entity_id': source_id, 'loop_index': loop_index,
                                     'edge_index': curve.source_edge_index, 'curve_evidence': params})
            curve_ids.append(curve_id)
        nodes[loop_id] = node('wire.loop2', dict(curves=curve_ids))
        loop_ids.append(loop_id)
    frame_id, profile_id = f'{prefix}_frame', f'{prefix}_profile'
    nodes[frame_id] = node('frame.rigid3', origin=profile.frame['origin'],
                           x_axis=profile.frame['x_axis'], y_axis=profile.frame['y_axis'])
    nodes[profile_id] = node('profile.face2', dict(
            outer=[loop_ids[profile.outer_index]],
            holes=[loop_ids[index] for index in profile.inner_indices],
        ))
    return nodes, frame_id, profile_id


