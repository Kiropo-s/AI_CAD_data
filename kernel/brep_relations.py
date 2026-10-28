"""Read-only B-rep incidence and geometric relation index.

The index is derived from :mod:`kernel.brep_graph` and never creates plan
nodes.  Topological relations are materialized once; geometric relations are
computed lazily for the requested pair or face.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from typing import Any, Iterable


FORMAT = 'step-brep-relation-index'
VERSION = 1
_RELATION_ALIASES = {
    'face_edge': 'face-edge', 'edge_vertex': 'edge-vertex',
    'face_face': 'face-face', 'shared_edge': 'face-face',
    'same_normal': 'same-normal',
}


def _canonical_relation(value: str) -> str:
    key = str(value).strip().lower().replace('_', '-')
    return _RELATION_ALIASES.get(key.replace('-', '_'), key)


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _vec(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    values = tuple(_safe_float(item) for item in value)
    return values if all(item is not None for item in values) else None


def _sub(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(a - b for a, b in zip(left, right))


def _dot(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _cross(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float, float]:
    return (left[1] * right[2] - left[2] * right[1],
            left[2] * right[0] - left[0] * right[2],
            left[0] * right[1] - left[1] * right[0])


def _norm(value: tuple[float, float, float]) -> float:
    return math.sqrt(_dot(value, value))


def _unit(value: tuple[float, float, float] | None) -> tuple[float, float, float] | None:
    if value is None:
        return None
    length = _norm(value)
    return tuple(item / length for item in value) if length > 0 else None


def _angle_between(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    cosine = max(-1.0, min(1.0, _dot(left, right)))
    return math.acos(cosine)


def _status_record(relation: str, left: str, right: str, status: str,
                   tolerance: float, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {'relation': relation, 'left': left, 'right': right, 'status': status,
            'tolerance': tolerance, 'evidence': evidence or {}}


def _axis_from_surface(face: dict[str, Any], surfaces: dict[str, dict[str, Any]]) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    surface = surfaces.get(face.get('surface_id'))
    if not surface:
        return None
    kind = surface.get('type')
    if kind not in {'GeomAbs_Plane', 'GeomAbs_Cylinder', 'GeomAbs_Cone', 'GeomAbs_Torus'}:
        return None
    axis = (surface.get('parameters') or {}).get('axis')
    if not isinstance(axis, dict):
        return None
    origin = _vec(axis.get('origin'))
    direction = _unit(_vec(axis.get('direction')))
    if origin is None or direction is None:
        return None
    if kind == 'GeomAbs_Plane' and face.get('orientation') == 'TopAbs_REVERSED':
        direction = tuple(-item for item in direction)
    return origin, direction


def _surface_type(face: dict[str, Any], surfaces: dict[str, dict[str, Any]]) -> str | None:
    surface = surfaces.get(face.get('surface_id'))
    return surface.get('type') if surface else face.get('surface_type')


class BrepRelationIndex:
    """Queryable, serializable relations derived from a source B-rep graph."""

    def __init__(self, data: dict[str, Any], *, graph_hash: str | None = None):
        self._data = deepcopy(data)
        self._graph_hash = graph_hash or self._data.get('graph_hash')
        self._entities = self._data.get('entities') or self._data.get('source_entities') or {}
        self._by_id = {
            item.get('id'): item
            for group in self._entities.values() if isinstance(group, list)
            for item in group if isinstance(item, dict) and item.get('id')
        }
        self._surfaces = {item['id']: item for item in self._entities.get('surfaces', [])}
        self._faces = {item['id']: item for item in self._entities.get('faces', [])}
        self._edges = {item['id']: item for item in self._entities.get('edges', [])}
        self._occurrences = {item['id']: item for item in self._entities.get('edge_occurrences', [])}
        self._loops = {item['id']: item for item in self._entities.get('loops', [])}
        self._build_incidence()

    @classmethod
    def from_graph(cls, graph: Any) -> 'BrepRelationIndex':
        data = graph.to_dict(include_hash=False) if hasattr(graph, 'to_dict') else deepcopy(graph)
        return cls(data, graph_hash=getattr(graph, 'graph_hash', None))

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, verify_hash: bool = True) -> 'BrepRelationIndex':
        supplied = data.get('index_hash')
        if verify_hash and supplied:
            raw = deepcopy(data)
            raw.pop('index_hash', None)
            encoded = json.dumps(raw, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
            expected = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
            if supplied != expected:
                raise ValueError('B-rep relation index hash mismatch')
        index = cls(data, graph_hash=data.get('graph_hash'))
        if verify_hash and supplied and supplied != index.index_hash:
            raise ValueError('B-rep relation index normalization mismatch')
        return index

    def _build_incidence(self) -> None:
        self.face_to_loops = {face_id: list(face.get('loop_ids', []))
                              for face_id, face in self._faces.items()}
        self.loop_to_face = {loop_id: loop.get('face_id') for loop_id, loop in self._loops.items()}
        self.loop_to_occurrences = {loop_id: list(loop.get('edge_occurrence_ids', []))
                                    for loop_id, loop in self._loops.items()}
        self.occurrence_to_edge = {occurrence_id: item.get('edge_id')
                                   for occurrence_id, item in self._occurrences.items()}
        self.occurrence_to_face = {occurrence_id: item.get('face_id')
                                   for occurrence_id, item in self._occurrences.items()}
        self.edge_to_occurrences: dict[str, list[str]] = {edge_id: [] for edge_id in self._edges}
        self.edge_to_faces: dict[str, list[str]] = {edge_id: [] for edge_id in self._edges}
        self.vertex_to_edges: dict[str, list[str]] = {}
        for occurrence_id, occurrence in self._occurrences.items():
            edge_id = occurrence.get('edge_id')
            face_id = occurrence.get('face_id')
            if edge_id in self.edge_to_occurrences:
                self.edge_to_occurrences[edge_id].append(occurrence_id)
                if face_id and face_id not in self.edge_to_faces[edge_id]:
                    self.edge_to_faces[edge_id].append(face_id)
        for edge_id, edge in self._edges.items():
            for vertex_id in (edge.get('start_vertex_id'), edge.get('end_vertex_id')):
                if vertex_id:
                    self.vertex_to_edges.setdefault(vertex_id, []).append(edge_id)
        self.face_to_adjacent_faces: dict[str, list[str]] = {face_id: [] for face_id in self._faces}
        for record in self._data.get('relations', {}).get('face_adjacency', []):
            left, right = record.get('left_face_id'), record.get('right_face_id')
            if left in self.face_to_adjacent_faces and right in self.face_to_adjacent_faces:
                if right not in self.face_to_adjacent_faces[left]:
                    self.face_to_adjacent_faces[left].append(right)
                if left not in self.face_to_adjacent_faces[right]:
                    self.face_to_adjacent_faces[right].append(left)
        serialized_adjacency = self._data.get('incidence', {}).get('face_to_adjacent_faces', {})
        if serialized_adjacency:
            self.face_to_adjacent_faces = {
                face_id: sorted(neighbors)
                for face_id, neighbors in serialized_adjacency.items()
                if face_id in self.face_to_adjacent_faces
            }
        for mapping in (self.face_to_loops, self.loop_to_occurrences, self.edge_to_occurrences,
                        self.edge_to_faces, self.vertex_to_edges, self.face_to_adjacent_faces):
            for key in mapping:
                if isinstance(mapping[key], list):
                    mapping[key] = sorted(mapping[key])

    def _incidence(self, relation: str, left: str, right: str) -> dict[str, Any] | None:
        if relation == 'face-edge':
            occurrences = [item for item in self._occurrences.values()
                            if item.get('face_id') == left and item.get('edge_id') == right]
            if occurrences:
                return _status_record(relation, left, right, 'true', 0.0,
                                      {'edge_occurrence_ids': sorted(item['id'] for item in occurrences)})
            return _status_record(relation, left, right, 'false', 0.0, {})
        if relation == 'edge-vertex':
            edge = self._edges.get(left)
            if edge and right in {edge.get('start_vertex_id'), edge.get('end_vertex_id')}:
                return _status_record(relation, left, right, 'true', 0.0,
                                      {'endpoint_role': 'start' if right == edge.get('start_vertex_id') else 'end'})
            return _status_record(relation, left, right, 'false', 0.0, {})
        if relation == 'face-face':
            if right in self.face_to_adjacent_faces.get(left, []):
                shared = sorted({edge_id for edge_id, faces in self.edge_to_faces.items()
                                 if left in faces and right in faces})
                return _status_record(relation, left, right, 'true', 0.0, {'shared_edge_ids': shared})
            return _status_record(relation, left, right, 'false', 0.0, {})
        if relation == 'containment':
            if self._contains(left, right):
                return _status_record(relation, left, right, 'true', 0.0, {'source': 'topology'})
            return _status_record(relation, left, right, 'false', 0.0, {})
        return None

    def _contains(self, left: str, right: str) -> bool:
        if right in self.face_to_loops.get(left, []):
            return True
        if right in self.loop_to_occurrences.get(left, []):
            return True
        if right in self.edge_to_occurrences.get(left, []):
            return True
        if right in self.edge_to_faces.get(left, []):
            return True
        if right in self.vertex_to_edges.get(left, []):
            return True
        for solid in self._entities.get('solids', []):
            if solid.get('id') == left and right in solid.get('shell_ids', []) + solid.get('face_ids', []):
                return True
        for shell in self._entities.get('shells', []):
            if shell.get('id') == left and right in shell.get('face_ids', []):
                return True
        return False

    def _geometric(self, relation: str, left: str, right: str,
                   linear_tolerance: float, angular_tolerance: float) -> dict[str, Any]:
        if left == right:
            return _status_record(relation, left, right, 'false', linear_tolerance,
                                  {'reason': 'same_entity'})
        first, second = self._faces.get(left), self._faces.get(right)
        if first is None or second is None:
            return _status_record(relation, left, right, 'unknown', linear_tolerance,
                                  {'reason': 'face_not_found'})
        first_type, second_type = _surface_type(first, self._surfaces), _surface_type(second, self._surfaces)
        first_axis, second_axis = _axis_from_surface(first, self._surfaces), _axis_from_surface(second, self._surfaces)
        plane_pair = first_type == second_type == 'GeomAbs_Plane'
        axis_pair = first_type in {'GeomAbs_Plane', 'GeomAbs_Cylinder', 'GeomAbs_Cone', 'GeomAbs_Torus'} and second_type in {'GeomAbs_Plane', 'GeomAbs_Cylinder', 'GeomAbs_Cone', 'GeomAbs_Torus'}
        if first_axis is None or second_axis is None or not axis_pair:
            return _status_record(relation, left, right, 'unknown', linear_tolerance,
                                  {'reason': 'unsupported_surface_pair', 'left_surface': first_type, 'right_surface': second_type})
        p1, d1 = first_axis
        p2, d2 = second_axis
        cross_norm = _norm(_cross(d1, d2))
        angle = min(_angle_between(d1, d2), _angle_between(d1, tuple(-item for item in d2)))
        parallel = cross_norm <= angular_tolerance
        perpendicular = abs(_dot(d1, d2)) <= angular_tolerance
        evidence = {'left_surface': first_type, 'right_surface': second_type,
                    'axis_angle_rad': angle, 'axis_cross_norm': cross_norm}
        if relation == 'parallel':
            return _status_record(relation, left, right, 'true' if parallel else 'false', angular_tolerance, evidence)
        if relation == 'perpendicular':
            return _status_record(relation, left, right, 'true' if perpendicular else 'false', angular_tolerance, evidence)
        if relation == 'same-normal':
            if not plane_pair:
                return _status_record(relation, left, right, 'unknown', angular_tolerance,
                                      {**evidence, 'reason': 'same_normal_requires_planes'})
            signed_dot = _dot(d1, d2)
            evidence['signed_normal_dot'] = signed_dot
            return _status_record(relation, left, right, 'true' if signed_dot >= 1.0 - angular_tolerance else 'false', angular_tolerance, evidence)
        if relation == 'coplanar':
            if not plane_pair:
                return _status_record(relation, left, right, 'unknown', linear_tolerance,
                                      {**evidence, 'reason': 'coplanar_requires_planes'})
            distance = abs(_dot(_sub(p2, p1), d1))
            evidence['plane_distance'] = distance
            evidence['parallel'] = parallel
            return _status_record(relation, left, right, 'true' if parallel and distance <= linear_tolerance else 'false', linear_tolerance, evidence)
        if relation == 'coaxial':
            if first_type not in {'GeomAbs_Cylinder', 'GeomAbs_Cone', 'GeomAbs_Torus'} or second_type not in {'GeomAbs_Cylinder', 'GeomAbs_Cone', 'GeomAbs_Torus'}:
                return _status_record(relation, left, right, 'unknown', linear_tolerance,
                                      {**evidence, 'reason': 'coaxial_requires_axis_surfaces'})
            distance = _norm(_cross(_sub(p2, p1), d1)) if parallel else None
            evidence['axis_distance'] = distance
            return _status_record(relation, left, right, 'true' if parallel and distance is not None and distance <= linear_tolerance else 'false', linear_tolerance, evidence)
        return _status_record(relation, left, right, 'unknown', linear_tolerance,
                              {'reason': 'unsupported_relation'})

    def relation(self, relation: str, left: str, right: str, *,
                 linear_tolerance: float = 1e-6, angular_tolerance: float = 1e-8) -> dict[str, Any]:
        canonical = _canonical_relation(relation)
        incidence = self._incidence(canonical, left, right)
        if incidence is not None:
            return incidence
        if canonical in {'parallel', 'coplanar', 'coaxial', 'perpendicular', 'same-normal'}:
            return self._geometric(canonical, left, right, linear_tolerance, angular_tolerance)
        return _status_record(canonical, left, right, 'unknown', linear_tolerance,
                              {'reason': 'unsupported_relation'})

    def find(self, relation: str, left: str, *, status: str = 'true', max_results: int | None = None,
             linear_tolerance: float = 1e-6, angular_tolerance: float = 1e-8) -> list[dict[str, Any]]:
        canonical = _canonical_relation(relation)
        if canonical == 'face-face':
            candidates = self.face_to_adjacent_faces.get(left, [])
        elif canonical == 'face-edge':
            candidates = sorted(self._edges)
        elif canonical == 'edge-vertex':
            candidates = sorted(self._by_id)
        elif canonical == 'containment':
            candidates = sorted(self._by_id)
        else:
            candidates = sorted(self._faces)
        result = []
        for right in candidates:
            record = self.relation(canonical, left, right, linear_tolerance=linear_tolerance,
                                    angular_tolerance=angular_tolerance)
            if record['status'] == status:
                result.append(record)
                if max_results is not None and len(result) >= max_results:
                    break
        return result

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        data = {
            'format': FORMAT, 'version': VERSION, 'graph_hash': self._graph_hash,
            'entities': {
                key: deepcopy(self._entities.get(key, []))
                for key in ('solids', 'shells', 'faces', 'loops', 'edge_occurrences',
                            'edges', 'vertices', 'surfaces', 'curves')
            },
            'incidence': {
                'face_to_loops': self.face_to_loops,
                'loop_to_occurrences': self.loop_to_occurrences,
                'occurrence_to_edge': self.occurrence_to_edge,
                'occurrence_to_face': self.occurrence_to_face,
                'edge_to_occurrences': self.edge_to_occurrences,
                'edge_to_faces': self.edge_to_faces,
                'vertex_to_edges': self.vertex_to_edges,
                'face_to_adjacent_faces': self.face_to_adjacent_faces,
            },
        }
        if include_hash:
            data['index_hash'] = self.index_hash
        return data

    @property
    def index_hash(self) -> str:
        payload = self.to_dict(include_hash=False)
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
        return hashlib.sha256(encoded.encode('utf-8')).hexdigest()

    def dumps(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(',', ':'), ensure_ascii=True)

    @property
    def incidence(self) -> dict[str, Any]:
        return deepcopy(self.to_dict(include_hash=False)['incidence'])


def relation_index(graph: Any) -> BrepRelationIndex:
    return BrepRelationIndex.from_graph(graph)
