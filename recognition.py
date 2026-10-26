"""Recognize exact analytic straight extrusions; never embed a source STEP."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepLib import BRepLib
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Line, GeomAbs_Circle, GeomAbs_BSplineCurve
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID, TopAbs_WIRE, TopAbs_REVERSED, TopAbs_EDGE, TopAbs_VERTEX
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Vec

from json2scapi.evaluation.metrics import area, bbox, volume, symmetric_difference_volume


class UnsupportedGeometry(ValueError):
    def __init__(self, message: str, diagnostics: dict | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


@dataclass(frozen=True)
class RecognitionOptions:
    length_tolerance: float = 1e-6
    reconstruction_relative_tolerance: float = 1e-7
    max_candidates: int = 64
    max_solids: int = 64
    max_revolve_candidates: int = 8
    max_section_faces: int = 16
    max_section_edges: int = 256
    max_spline_poles: int = 256
    max_csg_candidates: int = 96
    max_csg_steps: int = 32
    max_csg_faces: int = 80
    max_csg_seconds: float = 15.0
    max_csg_parts: int = 24
    max_csg_depth: int = 3

    def __post_init__(self):
        for name in ('length_tolerance', 'reconstruction_relative_tolerance', 'max_csg_seconds'):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be positive and finite')
        if min(self.max_candidates, self.max_solids, self.max_revolve_candidates, self.max_section_faces, self.max_section_edges, self.max_spline_poles, self.max_csg_candidates, self.max_csg_steps, self.max_csg_faces, self.max_csg_parts) < 1:
            raise ValueError('Recognition limits must be positive')
        if self.max_csg_depth<0 or self.max_csg_depth>3:
            raise ValueError('CSG partition depth must be between 0 and 3')


def explore(shape, kind):
    explorer = TopExp_Explorer(shape, kind)
    while explorer.More():
        yield explorer.Current()
        explorer.Next()


def _coordinates(point, frame):
    offset = np.array(point.Coord()) - frame['origin']
    return [float(offset @ frame['x_axis']), float(offset @ frame['y_axis'])]


def _curve(edge, frame, tolerance, max_spline_poles=0):
    adaptor = BRepAdaptor_Curve(edge)
    first, last = adaptor.FirstParameter(), adaptor.LastParameter()
    if not math.isfinite(first + last):
        raise UnsupportedGeometry('Unbounded edge parameter range')
    if edge.Orientation() == TopAbs_REVERSED:
        first, last = last, first
    start = _coordinates(adaptor.Value(first), frame)
    end = _coordinates(adaptor.Value(last), frame)
    kind = adaptor.GetType()
    if kind == GeomAbs_Line:
        if math.dist(start, end) <= tolerance:
            raise UnsupportedGeometry('Degenerate straight profile edge')
        return 'SketchLine', 'sketch.line', {'start': start, 'end': end}
    if kind == GeomAbs_BSplineCurve and max_spline_poles:
        spline=adaptor.BSpline().Copy()
        spline.Segment(min(first,last),max(first,last),1e-12)
        if first>last:spline.Reverse()
        if spline.NbPoles()>max_spline_poles or spline.Degree()>25:
            raise UnsupportedGeometry('Section B-spline exceeds supported pole count or degree')
        poles=[spline.Pole(i) for i in range(1,spline.NbPoles()+1)]
        residual=max(abs(float((np.array(p.Coord())-frame['origin'])@frame['z_axis'])) for p in poles)
        if residual>tolerance:
            raise UnsupportedGeometry(f'Section spline poles are not planar: {residual}')
        return 'SketchBSpline','sketch.bspline',dict(
            control_points=[_coordinates(p,frame) for p in poles],degree=spline.Degree(),
            knots=[float(spline.Knot(i)) for i in range(1,spline.NbKnots()+1)],
            multiplicities=[spline.Multiplicity(i) for i in range(1,spline.NbKnots()+1)],
            weights=[float(spline.Weight(i)) for i in range(1,spline.NbPoles()+1)],
            periodic=spline.IsPeriodic(),pole_plane_residual_mm=residual,
            provenance='bounded kernel section B-spline; no converter sampling or fitting',
            source_parameter_range=[float(first),float(last)])
    if kind != GeomAbs_Circle:
        raise UnsupportedGeometry(f'Unsupported profile curve: {kind}')
    circle = adaptor.Circle()
    center = _coordinates(circle.Location(), frame)
    radius = float(circle.Radius())
    if abs(abs(last-first) - 2*math.pi) <= 1e-7:
        return 'SketchCircle', 'sketch.circle', {'center': center, 'radius': radius}
    if math.dist(start, end) <= tolerance:
        raise UnsupportedGeometry('Ambiguous closed circular edge')
    middle = _coordinates(adaptor.Value((first+last)/2), frame)
    direction = np.array(circle.Axis().Direction().Coord())
    ccw = float(direction @ frame['z_axis']) * (last-first) > 0
    return 'SketchArc', 'sketch.arc', {
        'start': start, 'mid': middle, 'end': end,
        'center': center, 'radius': radius, 'ccw': ccw,
    }


def _profile(face, frame, prefix, source_id, tolerance, source_edges, max_spline_poles=0):
    outer = BRepTools.OuterWire_s(face)
    if outer.IsNull():
        raise UnsupportedGeometry('Planar face has no outer wire')
    wires = [outer] + [TopoDS.Wire_s(w) for w in explore(face, TopAbs_WIRE)
                       if not w.IsSame(outer)]
    entities, loops = {}, []
    for index, wire in enumerate(wires):
        loop_id = f'{prefix}_loop_{index+1:02d}'
        curves = []
        explorer = BRepTools_WireExplorer(wire, face)
        while explorer.More():
            curve_id = f'{loop_id}_edge_{len(curves)+1:03d}'
            kind, operation, params = _curve(explorer.Current(), frame, tolerance, max_spline_poles)
            entities[curve_id] = {
                'type': kind, 'op': operation, 'inputs': [], 'params': params,
                'source': {'format': 'step', 'entity_id': source_id,
                           'profile_id': prefix, 'curve_id': curve_id,
                           'loop_index': index, 'ordered': True,
                           'source_edge_index': next(i for i, edge in enumerate(source_edges, 1)
                                                     if edge.IsSame(explorer.Current()))},
            }
            curves.append(curve_id)
            explorer.Next()
        if not curves:
            raise UnsupportedGeometry('Empty profile wire')
        entities[loop_id] = {'type': 'SketchLoop', 'op': 'sketch.loop',
                             'inputs': curves, 'params': {'closed': True, 'is_outer': index == 0}}
        loops.append(loop_id)
    plane_id, profile_id = prefix+'_plane', prefix+'_profile'
    entities[plane_id] = {'type': 'SketchPlane', 'op': 'sketch.plane', 'inputs': [],
                          'params': {'frame': frame}}
    entities[profile_id] = {
        'type': 'SketchProfile', 'op': 'sketch.profile', 'inputs': loops,
        'params': {'plane': plane_id, 'closed': True,
                   'regions': [{'outer': loops[0], 'holes': loops[1:]}]},
        'source': {'format': 'step', 'entity_id': source_id, 'profile_id': prefix},
    }
    return entities, plane_id, profile_id


def _recognize_solid(solid, number, options):
    source_volume = volume(solid)
    if not math.isfinite(source_volume) or source_volume <= 0:
        raise UnsupportedGeometry('Nonpositive source solid volume')
    source_bbox = np.array(bbox(solid))
    scale = float(np.linalg.norm(source_bbox[3:]-source_bbox[:3]))
    planar, surface_types = [], {}
    for index, item in enumerate(explore(solid, TopAbs_FACE), 1):
        face = TopoDS.Face_s(item)
        surface = BRepAdaptor_Surface(face)
        kind = str(surface.GetType()).split('.')[-1]
        surface_types[kind] = surface_types.get(kind, 0) + 1
        if surface.GetType() == GeomAbs_Plane:
            planar.append((area(face), index, face, surface.Plane()))
    source_edges = []
    for edge in explore(solid, TopAbs_EDGE):
        if not any(edge.IsSame(item) for item in source_edges):
            source_edges.append(edge)
    attempts = []
    # Stable ordering; prefer a large end cap, but verify every selected cap.
    planar.sort(key=lambda item: (-item[0], tuple(item[3].Location().Coord()),
                                  tuple(item[3].Axis().Direction().Coord()), item[1]))
    for face_area, face_index, face, plane in planar[:options.max_candidates]:
        record = {'face_index': face_index, 'face_area_mm2': face_area}
        try:
            if face_area <= options.length_tolerance**2:
                raise UnsupportedGeometry('Degenerate planar face')
            outward = np.array(plane.Axis().Direction().Coord())
            if face.Orientation() == TopAbs_REVERSED:
                outward = -outward
            z_axis = -outward
            x_axis = np.array(plane.XAxis().Direction().Coord())
            y_axis = np.cross(z_axis, x_axis)
            # Measure cap separation independently of volume whenever available.
            cap_origin = np.array(plane.Location().Coord())
            caps = []
            for _, other_index, _, other_plane in planar:
                normal = np.array(other_plane.Axis().Direction().Coord())
                separation = float((np.array(other_plane.Location().Coord())-cap_origin) @ z_axis)
                if abs(abs(float(normal @ z_axis))-1) <= 1e-10 and separation > options.length_tolerance:
                    caps.append((separation, other_index))
            if caps:
                distance, other_index = max(caps)
                record.update(distance_origin_kind='measured', distance_method='parallel_cap_separation',
                              opposite_face_index=other_index)
            else:
                distance = source_volume / face_area
                record.update(distance_origin_kind='derived', distance_method='volume_over_cap_area',
                              distance_expression='source_volume_mm3 / face_area_mm2')
            # A cap anchor separates local profile geometry from world translation.
            outer = BRepTools.OuterWire_s(face)
            wire = BRepTools_WireExplorer(outer, face)
            curve = BRepAdaptor_Curve(wire.Current())
            origin = np.array(curve.Circle().Location().Coord()) if curve.GetType() == GeomAbs_Circle else np.array(curve.Value(curve.FirstParameter()).Coord())
            frame = {key: value.tolist() for key, value in {
                'origin': origin, 'x_axis': x_axis,
                'y_axis': y_axis, 'z_axis': z_axis}.items()}
            prefix = f'solid_{number:02d}'
            source_id = f'{prefix}.face_{face_index:04d}.inferred_prism'
            entities, plane_id, profile_id = _profile(
                face, frame, prefix, source_id, options.length_tolerance, source_edges)
            prism = BRepPrimAPI_MakePrism(face, gp_Vec(*(z_axis*distance)), True).Shape()
            if prism.IsNull():
                raise UnsupportedGeometry('Prism construction failed')
            prism = TopoDS.Solid_s(prism)
            if not BRepLib.OrientClosedSolid_s(prism):
                raise UnsupportedGeometry('Cannot orient closed prism')
            bbox_error = float(np.max(np.abs(np.array(bbox(prism))-source_bbox)))
            if bbox_error > max(options.length_tolerance*10, scale*1e-7):
                raise UnsupportedGeometry('Cap prism does not match the source bounding box')
            difference = symmetric_difference_volume(solid, prism) / source_volume
            if not math.isfinite(difference) or difference > options.reconstruction_relative_tolerance:
                raise UnsupportedGeometry(f'Cap prism differs from source: relative difference={difference}')
            feature_id = prefix+'_extrude'
            entities[feature_id] = {
                'type': 'ExtrudeFeature', 'op': 'feature.extrude', 'inputs': [profile_id],
                'params': {'distance': distance, 'direction': frame['z_axis'],
                           'extent': 'one_side', 'operation': 'new',
                           'start': 'profile_plane', 'taper': 0.0},
                'source': {'format': 'step', 'entity_id': source_id, 'profile_index': 0},
            }
            record.update(status='recognized', distance_mm=distance,
                          symmetric_difference_relative=difference)
            attempts.append(record)
            return entities, [plane_id, feature_id], {
                'solid_index': number, 'method': 'analytic_cap_prism',
                'surface_types': surface_types, 'selected': record, 'attempts': attempts,
                'candidate_selection_rule': 'descending area, plane origin, plane normal, imported face index',
                'history_recovered': False,
                'coordinate_frame': {'source_plane_origin': cap_origin.tolist(),
                                     'selected_frame': frame, 'anchor_rule': 'outer_edge_circle_center_or_first_vertex',
                                     'application': 'sketch_plane_once', 'adjustments': []},
                'profile_edges': {key: entity['source'] for key, entity in entities.items()
                                  if entity['op'].startswith('sketch.') and 'source' in entity},
                'source_face_indices': list(range(1, sum(surface_types.values())+1)),
                'source_topology': {'face_count': sum(surface_types.values()),
                    'edge_count': len(source_edges),
                    'faces': [{'face_index': i, 'edge_indices': sorted({
                        j for edge in explore(f, TopAbs_EDGE)
                        for j, original in enumerate(source_edges, 1) if edge.IsSame(original)})}
                        for i, f in enumerate(explore(solid, TopAbs_FACE), 1)]},
            }
        except Exception as exc:
            record.update(status='not_recognized', reason=f'{type(exc).__name__}: {exc}')
            attempts.append(record)
    raise UnsupportedGeometry(
        f'Solid {number} is not a supported straight extrusion with line/circle/arc profiles',
        {'solid_index': number, 'surface_types': surface_types,
         'planar_faces': len(planar), 'candidate_limit': options.max_candidates,
         'attempts': attempts})


def recognize(shape: Any, source: Path, options: RecognitionOptions | None = None):
    options = options or RecognitionOptions()
    solids = [TopoDS.Solid_s(item) for item in explore(shape, TopAbs_SOLID)]
    if not solids:
        raise UnsupportedGeometry('STEP contains no closed solids; surfaces are unsupported')
    if len(solids) > options.max_solids:
        raise UnsupportedGeometry(f'Solid count {len(solids)} exceeds limit {options.max_solids}')
    # Loose geometry cannot be silently discarded when reporting full coverage.
    for kind, avoid, label in ((TopAbs_FACE, TopAbs_SOLID, 'faces'),
                               (TopAbs_EDGE, TopAbs_FACE, 'edges'),
                               (TopAbs_VERTEX, TopAbs_EDGE, 'vertices')):
        if TopExp_Explorer(shape, kind, avoid).More():
            raise UnsupportedGeometry(f'STEP contains loose {label} outside the supported solid domain')
    entities, sequence, recognized = {}, [], []
    for index, solid in enumerate(solids, 1):
        try:
            local, steps, evidence = _recognize_solid(solid, index, options)
        except UnsupportedGeometry as prism_failure:
            from step2code.revolution import recognize_revolution
            try:
                local, steps, evidence = recognize_revolution(solid, index, options)
                evidence['prior_prism_attempt'] = prism_failure.diagnostics
            except UnsupportedGeometry as revolution_failure:
                from step2code.csg import recognize_csg
                try:
                    local, steps, evidence = recognize_csg(solid,index,options)
                    evidence['prior_prism_attempt'] = prism_failure.diagnostics
                    evidence['prior_revolution_attempt'] = revolution_failure.diagnostics
                except UnsupportedGeometry as csg_failure:
                    raise UnsupportedGeometry(
                        f'Solid {index}: no verified prism, full revolution, or bounded CSG reconstruction',
                        {'solid_index': index, 'recognized_solid_count':len(recognized),
                         'coverage_status':'partial' if recognized else 'none',
                         'recognized_solids':[{'solid_index':item['solid_index'],'method':item['method'],
                                               'operation_id':item['operation_id']} for item in recognized],
                         'prism': prism_failure.diagnostics,
                         'revolution': revolution_failure.diagnostics,
                         'csg': csg_failure.diagnostics}) from csg_failure
        evidence['operation_id'] = steps[-1]
        entities.update(local)
        sequence.extend(steps)
        recognized.append(evidence)
    payload = {
        'ir_version': '0.1', 'kind': 'part', 'units': 'mm',
        'tolerance': {'length': options.length_tolerance, 'angle_deg': 1e-6},
        'entities': entities, 'sequence': sequence, 'results': [sequence[-1]],
        'extensions': {'source': {'format': 'step', 'file': str(source)},
                       'step2scapi': {'recognizer': 'analytic-prism-revolution-csg-v4',
                                      'history_recovered': False}},
        'unsupported': [],
    }
    coverage = {'status': 'complete', 'source_solid_count': len(solids),
                'scope': 'all imported solids; loose faces/edges/vertices rejected',
                'mappings': [{'source_solid_index': index, 'operations': [recognized[index-1]['operation_id']],
                              'output_body_indices': [index-1],
                              'source_face_indices': recognized[index-1]['source_face_indices']}
                             for index in range(1, len(solids)+1)]}
    if any(entity['op'] in ('feature.revolve','feature.csg') for entity in entities.values()):
        payload.pop('ir_version')
        payload['format'] = 'step-reconstruction-plan-v1'
        payload['plan_version'] = '1'
    payload['extensions']['step2scapi']['source_coverage'] = coverage
    return payload, {'recognizer': 'analytic-prism-revolution-csg-v4', 'source_coverage': coverage, 'solid_count': len(solids),
                     'features': recognized, 'history_recovered': False}
