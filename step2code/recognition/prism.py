"""Recognize exact analytic straight extrusions; never embed a source STEP."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepLib import BRepLib
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Circle
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID, TopAbs_REVERSED, TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Vec

from step2code.verification.metrics import area, bbox, volume, symmetric_difference_volume
from step2code.plan.model import node
from .context import call, capture_sources
from .profile import ProfileError, extract_profile, emit_profile_nodes


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


def _profile(face, frame, prefix, source_id, tolerance, source_edges, max_spline_poles=0):
    try:
        evidence = extract_profile(face, frame, tolerance, max_spline_poles, source_edges)
        return emit_profile_nodes(evidence, prefix, source_id)
    except ProfileError as exc:
        raise UnsupportedGeometry(str(exc), {'reason_code': exc.reason_code, **exc.details}) from exc


def _profile(face, frame, prefix, source_id, tolerance, source_edges, max_spline_poles=0):
    try:
        evidence = extract_profile(face, frame, tolerance, max_spline_poles, source_edges)
        return emit_profile_nodes(evidence, prefix, source_id)
    except ProfileError as exc:
        raise UnsupportedGeometry(str(exc), {'reason_code': exc.reason_code, **exc.details}) from exc


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
            nodes, frame_id, profile_id = _profile(
                face, frame, prefix, source_id, options.length_tolerance, source_edges,
                options.max_spline_poles)
            prism = call('make_prism', BRepPrimAPI_MakePrism,face, gp_Vec(*(z_axis*distance)), True).Shape()
            if prism.IsNull():
                raise UnsupportedGeometry('Prism construction failed')
            prism = TopoDS.Solid_s(prism)
            if not BRepLib.OrientClosedSolid_s(prism):
                raise UnsupportedGeometry('Cannot orient closed prism')
            bbox_error = float(np.max(np.abs(np.array(bbox(prism))-source_bbox)))
            if bbox_error > max(options.length_tolerance*10, scale*1e-7):
                raise UnsupportedGeometry('Cap prism does not match the source bounding box')
            difference = call('source_difference',symmetric_difference_volume,solid,prism) / source_volume
            if not math.isfinite(difference) or difference > options.reconstruction_relative_tolerance:
                raise UnsupportedGeometry(f'Cap prism differs from source: relative difference={difference}')
            feature_id = prefix+'_extrude'
            local_id=feature_id+'_local'
            nodes[local_id]=node('solid.extrude',dict(profile=[profile_id]),distance=distance)
            nodes[feature_id]=node('shape.transform',dict(shape=[local_id],frame=[frame_id]))
            record.update(status='recognized', distance_mm=distance,
                          symmetric_difference_relative=difference)
            attempts.append(record)
            return nodes, [feature_id], {
                'solid_index': number, 'method': 'analytic_cap_prism', 'node_sources':capture_sources(nodes),
                'surface_types': surface_types, 'selected': record, 'attempts': attempts,
                'candidate_selection_rule': 'descending area, plane origin, plane normal, imported face index',
                'history_recovered': False,
                'coordinate_frame': {'source_plane_origin': cap_origin.tolist(),
                                     'selected_frame': frame, 'anchor_rule': 'outer_edge_circle_center_or_first_vertex',
                                     'application': 'sketch_plane_once', 'adjustments': []},
                'profile_nodes': [key for key,entity in nodes.items() if entity['op'].startswith('curve.')],
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


