"""Read-only, deterministic B-rep graph extraction.

The graph is a source-evidence object.  It never creates plan nodes and it is
safe to serialize as a sidecar for later recognition stages.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math
from typing import Any, Iterable

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
from OCP.GProp import GProp_GProps
from OCP.GeomAbs import (
    GeomAbs_BSplineCurve, GeomAbs_BSplineSurface, GeomAbs_BezierCurve,
    GeomAbs_BezierSurface, GeomAbs_Circle, GeomAbs_Cone, GeomAbs_Cylinder,
    GeomAbs_Ellipse, GeomAbs_Line, GeomAbs_Plane, GeomAbs_Sphere, GeomAbs_Torus,
)
from OCP.TopAbs import (
    TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_SHELL, TopAbs_SOLID,
    TopAbs_VERTEX, TopAbs_WIRE,
)
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS

from step2code.kernel.signatures import explore, shape_signature
from step2code.verification.metrics import area, bbox, volume


GRAPH_FORMAT = 'step-brep-graph'
GRAPH_VERSION = 1


def _kind_name(value: Any) -> str:
    return str(value).split('.')[-1]


def _safe_float(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _point(point: Any) -> list[float] | None:
    try:
        return [float(v) for v in point.Coord()]
    except Exception:
        return None


def _direction(direction: Any) -> list[float] | None:
    try:
        return [float(v) for v in direction.Coord()]
    except Exception:
        return None


def _axis(axis: Any) -> dict[str, Any]:
    return {
        'origin': _point(axis.Location()),
        'direction': _direction(axis.Direction()),
    }


def _safe_bbox(shape: Any) -> list[float] | None:
    try:
        return [float(v) for v in bbox(shape)]
    except Exception:
        return None


def _center(shape: Any, surface: bool = False) -> list[float] | None:
    try:
        props = GProp_GProps()
        if surface:
            BRepGProp.SurfaceProperties_s(shape, props)
        else:
            BRepGProp.VolumeProperties_s(shape, props)
        return _point(props.CentreOfMass())
    except Exception:
        return None


def _unique(items: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    index = TopTools_IndexedMapOfShape()
    for item in items:
        if not index.Contains(item):
            index.Add(item)
            result.append(item)
    return result


def _shape_map(items: list[Any]) -> TopTools_IndexedMapOfShape:
    index = TopTools_IndexedMapOfShape()
    for item in items:
        index.Add(item)
    return index


def _explore(shape: Any, kind: Any) -> list[Any]:
    return list(explore(shape, kind))


def _orientation(shape: Any) -> str:
    return _kind_name(shape.Orientation())


def _index_by_same(items: list[Any], item: Any, index_map: TopTools_IndexedMapOfShape | None = None) -> int | None:
    if item is None:
        return None
    if index_map is not None:
        index = int(index_map.FindIndex(item))
        return index or None
    for index, original in enumerate(items, 1):
        if item.IsSame(original):
            return index
    return None


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, float):
        return _safe_float(value)
    return value


def _surface_parameters(adaptor: BRepAdaptor_Surface, kind: Any, limit: int) -> dict[str, Any]:
    params: dict[str, Any] = {
        'u_domain': [_safe_float(adaptor.FirstUParameter()), _safe_float(adaptor.LastUParameter())],
        'v_domain': [_safe_float(adaptor.FirstVParameter()), _safe_float(adaptor.LastVParameter())],
        'u_periodic': bool(adaptor.IsUPeriodic()),
        'v_periodic': bool(adaptor.IsVPeriodic()),
    }
    try:
        if kind == GeomAbs_Plane:
            plane = adaptor.Plane()
            params.update({'axis': _axis(plane.Axis()), 'x_axis': _axis(plane.XAxis()),
                           'y_axis': _axis(plane.YAxis()), 'coefficients': [_safe_float(v) for v in plane.Coefficients()]})
        elif kind == GeomAbs_Cylinder:
            cylinder = adaptor.Cylinder()
            params.update({'axis': _axis(cylinder.Axis()), 'radius': _safe_float(cylinder.Radius())})
        elif kind == GeomAbs_Cone:
            cone = adaptor.Cone()
            params.update({'axis': _axis(cone.Axis()), 'semi_angle': _safe_float(cone.SemiAngle()),
                           'ref_radius': _safe_float(cone.RefRadius())})
        elif kind == GeomAbs_Sphere:
            sphere = adaptor.Sphere()
            params.update({'position': _axis(sphere.Position()), 'radius': _safe_float(sphere.Radius())})
        elif kind == GeomAbs_Torus:
            torus = adaptor.Torus()
            params.update({'axis': _axis(torus.Axis()), 'major_radius': _safe_float(torus.MajorRadius()),
                           'minor_radius': _safe_float(torus.MinorRadius())})
        elif kind in (GeomAbs_BSplineSurface, GeomAbs_BezierSurface):
            if kind == GeomAbs_BSplineSurface:
                surface = adaptor.BSpline()
                params.update({'u_degree': int(surface.UDegree()), 'v_degree': int(surface.VDegree()),
                               'u_poles': int(surface.NbUPoles()), 'v_poles': int(surface.NbVPoles()),
                               'u_knots': int(surface.NbUKnots()), 'v_knots': int(surface.NbVKnots()),
                               'u_periodic': bool(surface.IsUPeriodic()), 'v_periodic': bool(surface.IsVPeriodic()),
                               'rational': bool(surface.IsURational() or surface.IsVRational())})
                pole_count = int(surface.NbUPoles()) * int(surface.NbVPoles())
                if pole_count <= limit:
                    params['poles'] = [[_point(surface.Pole(u, v)) for v in range(1, surface.NbVPoles() + 1)]
                                       for u in range(1, surface.NbUPoles() + 1)]
                    params['weights'] = [[_safe_float(surface.Weight(u, v)) for v in range(1, surface.NbVPoles() + 1)]
                                         for u in range(1, surface.NbUPoles() + 1)]
                    params['u_knots_values'] = [_safe_float(surface.UKnot(i)) for i in range(1, surface.NbUKnots() + 1)]
                    params['v_knots_values'] = [_safe_float(surface.VKnot(i)) for i in range(1, surface.NbVKnots() + 1)]
                    params['u_multiplicities'] = [int(surface.UMultiplicity(i)) for i in range(1, surface.NbUKnots() + 1)]
                    params['v_multiplicities'] = [int(surface.VMultiplicity(i)) for i in range(1, surface.NbVKnots() + 1)]
                else:
                    params['parameter_status'] = 'truncated'
                    params['parameter_limit'] = limit
            else:
                surface = adaptor.Bezier()
                params.update({'u_degree': int(surface.UDegree()), 'v_degree': int(surface.VDegree()),
                               'u_poles': int(surface.NbUPoles()), 'v_poles': int(surface.NbVPoles())})
                pole_count = int(surface.NbUPoles()) * int(surface.NbVPoles())
                if pole_count <= limit:
                    params['poles'] = [[_point(surface.Pole(u, v)) for v in range(1, surface.NbVPoles() + 1)]
                                       for u in range(1, surface.NbUPoles() + 1)]
                else:
                    params['parameter_status'] = 'truncated'
                    params['parameter_limit'] = limit
    except Exception as exc:
        params['parameter_status'] = 'unavailable'
        params['parameter_error'] = type(exc).__name__
    params.setdefault('parameter_status', 'complete')
    return _jsonable(params)


def _curve_parameters(adaptor: BRepAdaptor_Curve, kind: Any, limit: int) -> dict[str, Any]:
    first, last = adaptor.FirstParameter(), adaptor.LastParameter()
    params: dict[str, Any] = {
        'parameter_range': [_safe_float(first), _safe_float(last)],
        'closed': bool(adaptor.IsClosed()),
        'periodic': bool(adaptor.IsPeriodic()),
    }
    try:
        if kind == GeomAbs_Line:
            line = adaptor.Line()
            params.update({'axis': _axis(line)})
        elif kind == GeomAbs_Circle:
            circle = adaptor.Circle()
            params.update({'axis': _axis(circle.Axis()), 'radius': _safe_float(circle.Radius())})
        elif kind == GeomAbs_Ellipse:
            ellipse = adaptor.Ellipse()
            params.update({'axis': _axis(ellipse.Axis()), 'major_radius': _safe_float(ellipse.MajorRadius()),
                           'minor_radius': _safe_float(ellipse.MinorRadius())})
        elif kind == GeomAbs_BSplineCurve:
            curve = adaptor.BSpline()
            params.update({'degree': int(curve.Degree()), 'poles': int(curve.NbPoles()),
                           'knots': int(curve.NbKnots()), 'periodic': bool(curve.IsPeriodic()),
                           'rational': bool(curve.IsRational())})
            if curve.NbPoles() <= limit:
                params.update({'poles_xyz': [_point(curve.Pole(i)) for i in range(1, curve.NbPoles() + 1)],
                               'weights': [_safe_float(curve.Weight(i)) for i in range(1, curve.NbPoles() + 1)],
                               'knots_values': [_safe_float(curve.Knot(i)) for i in range(1, curve.NbKnots() + 1)],
                               'multiplicities': [int(curve.Multiplicity(i)) for i in range(1, curve.NbKnots() + 1)]})
            else:
                params['parameter_status'] = 'truncated'
                params['parameter_limit'] = limit
        elif kind == GeomAbs_BezierCurve:
            curve = adaptor.Bezier()
            params.update({'degree': int(curve.Degree()), 'poles': int(curve.NbPoles()),
                           'rational': bool(curve.IsRational())})
            if curve.NbPoles() <= limit:
                params.update({'poles_xyz': [_point(curve.Pole(i)) for i in range(1, curve.NbPoles() + 1)],
                               'weights': [_safe_float(curve.Weight(i)) for i in range(1, curve.NbPoles() + 1)]})
            else:
                params['parameter_status'] = 'truncated'
                params['parameter_limit'] = limit
    except Exception as exc:
        params['parameter_status'] = 'unavailable'
        params['parameter_error'] = type(exc).__name__
    params.setdefault('parameter_status', 'complete')
    return _jsonable(params)


def _edge_vertices(edge: Any) -> tuple[Any, Any]:
    try:
        return TopExp.FirstVertex_s(TopoDS.Edge_s(edge), True), TopExp.LastVertex_s(TopoDS.Edge_s(edge), True)
    except Exception:
        vertices = [TopoDS.Vertex_s(item) for item in _explore(edge, TopAbs_VERTEX)]
        return (vertices + [None, None])[:2]


def _wire_edges(wire: Any, face: Any) -> tuple[list[Any], str | None]:
    result: list[Any] = []
    try:
        explorer = BRepTools_WireExplorer(TopoDS.Wire_s(wire), TopoDS.Face_s(face))
        while explorer.More():
            result.append(explorer.Current())
            explorer.Next()
        if result:
            return result, None
    except Exception as exc:
        error = type(exc).__name__
    else:
        error = 'empty_wire_explorer'
    fallback = _explore(wire, TopAbs_EDGE)
    return fallback, error


def _shape_status(shape: Any, *, detailed: bool = True) -> dict[str, Any]:
    if shape is None or shape.IsNull():
        return {'valid': False, 'reason': 'null_shape'}
    if not detailed:
        return {'valid': None, 'reason': 'large_shape_signature_skipped'}
    try:
        signature = shape_signature(shape)
        return {'valid': bool(signature.get('valid')), 'signature': signature}
    except Exception as exc:
        return {'valid': None, 'reason': type(exc).__name__}


class BrepGraph:
    """Immutable-by-convention source B-rep graph with stable local IDs."""

    def __init__(self, data: dict[str, Any]):
        clean = deepcopy(_jsonable(data))
        clean.pop('graph_hash', None)
        self._data = clean

    @classmethod
    def from_shape(cls, shape: Any, *, max_poles: int = 256,
                   max_detailed_solids: int = 64, max_detailed_faces: int = 2000,
                   max_detailed_edges: int = 8000) -> 'BrepGraph':
        return cls(_build_graph(shape, max_poles=max_poles,
                                 max_detailed_solids=max_detailed_solids,
                                 max_detailed_faces=max_detailed_faces,
                                 max_detailed_edges=max_detailed_edges))

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, verify_hash: bool = True) -> 'BrepGraph':
        graph = cls(data)
        supplied = data.get('graph_hash')
        if verify_hash and supplied and supplied != graph.graph_hash:
            raise ValueError('B-rep graph hash mismatch')
        return graph

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        data = deepcopy(self._data)
        if include_hash:
            data['graph_hash'] = self.graph_hash
        return data

    @property
    def graph_hash(self) -> str:
        payload = json.dumps(self._data, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()

    def dumps(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(',', ':'), ensure_ascii=True)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __repr__(self) -> str:
        return f"BrepGraph(hash={self.graph_hash[:12]}, entities={self._data.get('entity_counts', {})})"

    def relation_index(self):
        """Build a read-only P1B relation index for this graph."""
        from step2code.kernel.brep_relations import BrepRelationIndex
        return BrepRelationIndex.from_graph(self)


def _build_graph(shape: Any, *, max_poles: int, max_detailed_solids: int,
                 max_detailed_faces: int, max_detailed_edges: int) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    if shape is None or shape.IsNull():
        status = _shape_status(shape)
        return {'format': GRAPH_FORMAT, 'version': GRAPH_VERSION, 'entity_counts': {},
                'entities': {}, 'relations': {}, 'status': status, 'warnings': [{'reason': 'null_shape'}],
                'limits': {'max_poles': max_poles, 'max_detailed_solids': max_detailed_solids,
                            'max_detailed_faces': max_detailed_faces, 'max_detailed_edges': max_detailed_edges}}

    solids = _unique(_explore(shape, TopAbs_SOLID))
    shells = _unique(_explore(shape, TopAbs_SHELL))
    faces = _unique(_explore(shape, TopAbs_FACE))
    edges = _unique(_explore(shape, TopAbs_EDGE))
    vertices = _unique(_explore(shape, TopAbs_VERTEX))
    detailed = (len(solids) <= max_detailed_solids and len(faces) <= max_detailed_faces
                and len(edges) <= max_detailed_edges)
    status = _shape_status(shape, detailed=detailed)
    shell_map = _shape_map(shells)
    face_map = _shape_map(faces)
    edge_map = _shape_map(edges)
    vertex_map = _shape_map(vertices)
    shell_solid: dict[int, int] = {}
    face_shell: dict[int, int] = {}
    face_solid: dict[int, int] = {}
    for si, solid in enumerate(solids, 1):
        for shell in _unique(_explore(solid, TopAbs_SHELL)):
            shell_id = _index_by_same(shells, shell, shell_map)
            if shell_id is not None:
                shell_solid[shell_id] = si
                for face in _unique(_explore(shell, TopAbs_FACE)):
                    face_id = _index_by_same(faces, face, face_map)
                    if face_id is not None:
                        face_shell[face_id] = shell_id
                        face_solid[face_id] = si

    surfaces: list[dict[str, Any]] = []
    curves: list[dict[str, Any]] = []
    surface_ids: dict[int, str] = {}
    curve_ids: dict[int, str] = {}
    for index, face in enumerate(faces, 1):
        surface_id = f'surface_{index:06d}'
        surface_ids[index] = surface_id
        try:
            adaptor = BRepAdaptor_Surface(TopoDS.Face_s(face))
            kind = _kind_name(adaptor.GetType())
            parameters = (_surface_parameters(adaptor, adaptor.GetType(), max_poles) if detailed
                          else {'parameter_status': 'skipped_large_source'})
        except Exception as exc:
            kind, parameters = 'unknown', {'parameter_status': 'unavailable', 'parameter_error': type(exc).__name__}
            warnings.append({'entity_id': f'face_{index:06d}', 'reason': 'surface_adaptor_failed', 'error': type(exc).__name__})
        surfaces.append({'id': surface_id, 'type': kind, 'parameters': parameters})
    for index, edge in enumerate(edges, 1):
        curve_id = f'curve_{index:06d}'
        curve_ids[index] = curve_id
        try:
            adaptor = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
            kind = _kind_name(adaptor.GetType())
            parameters = (_curve_parameters(adaptor, adaptor.GetType(), max_poles) if detailed
                          else {'parameter_status': 'skipped_large_source'})
        except Exception as exc:
            kind, parameters = 'unknown', {'parameter_status': 'unavailable', 'parameter_error': type(exc).__name__}
            warnings.append({'entity_id': f'edge_{index:06d}', 'reason': 'curve_adaptor_failed', 'error': type(exc).__name__})
        curves.append({'id': curve_id, 'type': kind, 'parameters': parameters})

    vertex_records = []
    for index, vertex in enumerate(vertices, 1):
        vertex_records.append({'id': f'vertex_{index:06d}', 'point': _point(BRep_Tool.Pnt_s(TopoDS.Vertex_s(vertex))),
                               'tolerance': _safe_float(BRep_Tool.Tolerance_s(TopoDS.Vertex_s(vertex)))})

    edge_face_ids: dict[int, list[str]] = defaultdict(list)
    edge_occurrences: list[dict[str, Any]] = []
    loops: list[dict[str, Any]] = []
    face_records: list[dict[str, Any]] = []
    occurrence_index = 0
    loop_index = 0
    for face_index, face in enumerate(faces, 1):
        face_id = f'face_{face_index:06d}'
        outer = None
        try:
            outer = BRepTools.OuterWire_s(TopoDS.Face_s(face))
        except Exception as exc:
            warnings.append({'entity_id': face_id, 'reason': 'outer_wire_unavailable', 'error': type(exc).__name__})
        wires = _unique(_explore(face, TopAbs_WIRE))
        if outer is not None and not outer.IsNull() and not any(outer.IsSame(item) for item in wires):
            wires.insert(0, outer)
        face_loop_ids: list[str] = []
        for wire in wires:
            loop_index += 1
            loop_id = f'loop_{loop_index:06d}'
            kind = 'outer' if outer is not None and not outer.IsNull() and wire.IsSame(outer) else 'inner'
            wire_edges, wire_error = _wire_edges(wire, face)
            if wire_error:
                warnings.append({'entity_id': loop_id, 'reason': 'wire_order_fallback', 'detail': wire_error})
            occurrence_ids: list[str] = []
            for edge in wire_edges:
                edge_index = _index_by_same(edges, edge, edge_map)
                if edge_index is None:
                    edge_index = len(edges) + 1
                    edges.append(edge)
                    warnings.append({'entity_id': face_id, 'reason': 'edge_discovered_after_indexing'})
                occurrence_index += 1
                occurrence_id = f'edge_occurrence_{occurrence_index:06d}'
                first, last = _edge_vertices(edge)
                first_index = _index_by_same(vertices, first, vertex_map) if first is not None and not first.IsNull() else None
                last_index = _index_by_same(vertices, last, vertex_map) if last is not None and not last.IsNull() else None
                edge_id = f'edge_{edge_index:06d}'
                occurrence_ids.append(occurrence_id)
                edge_occurrences.append({'id': occurrence_id, 'edge_id': edge_id, 'loop_id': loop_id,
                                         'face_id': face_id, 'orientation': _orientation(edge),
                                         'start_vertex_id': f'vertex_{first_index:06d}' if first_index else None,
                                         'end_vertex_id': f'vertex_{last_index:06d}' if last_index else None})
                edge_face_ids[edge_index].append(face_id)
            loops.append({'id': loop_id, 'face_id': face_id, 'kind': kind,
                          'orientation': _orientation(wire), 'edge_occurrence_ids': occurrence_ids})
            face_loop_ids.append(loop_id)
        try:
            adaptor = BRepAdaptor_Surface(TopoDS.Face_s(face))
            surface_type = _kind_name(adaptor.GetType())
        except Exception:
            surface_type = 'unknown'
        face_records.append({'id': face_id, 'solid_id': f'solid_{face_solid[face_index]:06d}' if face_index in face_solid else None,
                             'shell_id': f'shell_{face_shell[face_index]:06d}' if face_index in face_shell else None,
                             'surface_id': surface_ids.get(face_index), 'surface_type': surface_type,
                             'area_mm2': _safe_float(area(face)) if detailed else None,
                             'bbox_mm': _safe_bbox(face) if detailed else None,
                             'center': _center(face, surface=True) if detailed else None, 'orientation': _orientation(face),
                             'uv_domain': surfaces[face_index - 1]['parameters'].get('u_domain', []) + surfaces[face_index - 1]['parameters'].get('v_domain', []),
                             'outer_loop_id': next((item['id'] for item in loops if item['id'] in face_loop_ids and item['kind'] == 'outer'), None),
                             'inner_loop_ids': [item['id'] for item in loops if item['id'] in face_loop_ids and item['kind'] == 'inner'],
                             'loop_ids': face_loop_ids})

    edge_records = []
    for index, edge in enumerate(edges, 1):
        first, last = _edge_vertices(edge)
        first_index = _index_by_same(vertices, first, vertex_map) if first is not None and not first.IsNull() else None
        last_index = _index_by_same(vertices, last, vertex_map) if last is not None and not last.IsNull() else None
        try:
            adaptor = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
            curve_type = _kind_name(adaptor.GetType())
            first_parameter, last_parameter = adaptor.FirstParameter(), adaptor.LastParameter()
        except Exception:
            curve_type, first_parameter, last_parameter = 'unknown', None, None
        adjacent = sorted(set(edge_face_ids.get(index, [])))
        edge_records.append({'id': f'edge_{index:06d}', 'curve_id': curve_ids.get(index),
                             'curve_type': curve_type,
                             'length_mm': _safe_float(_edge_length(edge)) if detailed else None,
                             'first_parameter': _safe_float(first_parameter), 'last_parameter': _safe_float(last_parameter),
                             'orientation': _orientation(edge),
                             'start_vertex_id': f'vertex_{first_index:06d}' if first_index else None,
                             'end_vertex_id': f'vertex_{last_index:06d}' if last_index else None,
                             'adjacent_face_ids': adjacent,
                             'left_face_id': adjacent[0] if len(adjacent) >= 1 else None,
                             'right_face_id': adjacent[1] if len(adjacent) >= 2 else None,
                             'non_manifold': len(adjacent) > 2})

    shell_records = []
    for index, shell in enumerate(shells, 1):
        shell_faces = [f'face_{face_index:06d}' for face_index in range(1, len(faces) + 1) if face_shell.get(face_index) == index]
        shell_records.append({'id': f'shell_{index:06d}', 'solid_id': f'solid_{shell_solid[index]:06d}' if index in shell_solid else None,
                              'face_ids': shell_faces})
    solid_records = []
    for index, solid in enumerate(solids, 1):
        solid_shells = [f'shell_{shell_index:06d}' for shell_index, value in enumerate(shells, 1) if shell_solid.get(shell_index) == index]
        solid_faces = [f'face_{face_index:06d}' for face_index, value in enumerate(faces, 1) if face_solid.get(face_index) == index]
        solid_records.append({'id': f'solid_{index:06d}', 'shell_ids': solid_shells, 'face_ids': solid_faces,
                              'volume_mm3': _safe_float(volume(solid)) if detailed else None,
                              'bbox_mm': _safe_bbox(solid) if detailed else None,
                              'center': _center(solid) if detailed else None, 'orientation': _orientation(solid)})

    adjacency = []
    for edge in edge_records:
        faces_for_edge = edge['adjacent_face_ids']
        for left_index, left in enumerate(faces_for_edge):
            for right in faces_for_edge[left_index + 1:]:
                adjacency.append({'left_face_id': left, 'right_face_id': right, 'edge_id': edge['id'], 'relation': 'true'})
    return {
        'format': GRAPH_FORMAT,
        'version': GRAPH_VERSION,
        'status': status,
        'detail_status': 'complete' if detailed else 'summary_only',
        'limits': {'max_poles': max_poles, 'max_detailed_solids': max_detailed_solids,
                   'max_detailed_faces': max_detailed_faces, 'max_detailed_edges': max_detailed_edges},
        'entity_counts': {'solid_count': len(solid_records), 'shell_count': len(shell_records),
                          'face_count': len(face_records), 'loop_count': len(loops),
                          'edge_occurrence_count': len(edge_occurrences), 'edge_count': len(edge_records),
                          'vertex_count': len(vertex_records), 'surface_count': len(surfaces), 'curve_count': len(curves)},
        'entities': {'solids': solid_records, 'shells': shell_records, 'faces': face_records,
                     'loops': loops, 'edge_occurrences': edge_occurrences, 'edges': edge_records,
                     'vertices': vertex_records, 'surfaces': surfaces, 'curves': curves},
        'relations': {'face_adjacency': adjacency},
        'warnings': warnings,
    }


def _edge_length(edge: Any) -> float | None:
    try:
        props = GProp_GProps()
        BRepGProp.LinearProperties_s(TopoDS.Edge_s(edge), props)
        return _safe_float(props.Mass())
    except Exception:
        return None


def graph_from_shape(shape: Any, *, max_poles: int = 256,
                     max_detailed_solids: int = 64, max_detailed_faces: int = 2000,
                     max_detailed_edges: int = 8000) -> BrepGraph:
    """Build a deterministic graph from an OCC shape."""
    return BrepGraph.from_shape(shape, max_poles=max_poles,
                                max_detailed_solids=max_detailed_solids,
                                max_detailed_faces=max_detailed_faces,
                                max_detailed_edges=max_detailed_edges)


def graph_hash(data: dict[str, Any]) -> str:
    """Return the canonical hash of a serialized graph without its hash field."""
    payload = deepcopy(data)
    payload.pop('graph_hash', None)
    encoded = json.dumps(_jsonable(payload), sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()







