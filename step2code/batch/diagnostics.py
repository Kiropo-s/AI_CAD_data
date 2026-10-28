"""Stable, sidecar-only diagnostics for STEP recognition runs.

This module deliberately does not create plan nodes or participate in acceptance.
It summarizes imported topology and normalizes recognizer evidence so later
recognition stages can be compared without parsing human error strings.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from copy import deepcopy
from typing import Any

from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.TopAbs import (
    TopAbs_EDGE,
    TopAbs_FACE,
    TopAbs_SOLID,
    TopAbs_VERTEX,
)
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from step2code.kernel.signatures import explore, shape_signature
from step2code.verification.metrics import area, bbox, volume


def _unique_subshapes(shape, kind):
    """Return subshapes once, using OCC identity rather than Python identity."""
    result = []
    for item in explore(shape, kind):
        if not any(item.IsSame(previous) for previous in result):
            result.append(item)
    return result


def _kind_name(value):
    return str(value).split('.')[-1]


def _safe_float(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value == value and abs(value) != float('inf') else None


def _edge_index(edges, edge):
    for index, original in enumerate(edges, 1):
        if edge.IsSame(original):
            return index
    edges.append(edge)
    return len(edges)


def _face_record(face, face_index, edges):
    edge_indices = []
    explorer = TopExp_Explorer(face, TopAbs_EDGE)
    while explorer.More():
        edge_indices.append(_edge_index(edges, explorer.Current()))
        explorer.Next()
    surface = BRepAdaptor_Surface(TopoDS.Face_s(face))
    return {
        'face_index': face_index,
        'surface_type': _kind_name(surface.GetType()),
        'area_mm2': _safe_float(area(face)),
        'bbox_mm': list(map(float, bbox(face))),
        'edge_indices': sorted(set(edge_indices)),
        'orientation': str(face.Orientation()).split('.')[-1],
    }


def _edge_length(edge):
    props = GProp_GProps()
    BRepGProp.LinearProperties_s(TopoDS.Edge_s(edge), props)
    return float(props.Mass())


def _edge_histogram(edges):
    histogram = Counter()
    records = []
    for index, edge in enumerate(edges, 1):
        curve = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
        kind = _kind_name(curve.GetType())
        histogram[kind] += 1
        records.append({
            'edge_index': index,
            'curve_type': kind,
            'length_mm': _safe_float(_edge_length(edge)),
            'first_parameter': _safe_float(curve.FirstParameter()),
            'last_parameter': _safe_float(curve.LastParameter()),
        })
    return dict(sorted(histogram.items())), records


def _face_adjacency(faces, face_records):
    by_edge = defaultdict(list)
    for record in face_records:
        for edge_index in record['edge_indices']:
            by_edge[edge_index].append(record['face_index'])
    pairs = Counter()
    non_manifold = []
    for edge_index, face_indices in sorted(by_edge.items()):
        unique = sorted(set(face_indices))
        if len(unique) > 2:
            non_manifold.append({'edge_index': edge_index, 'face_indices': unique})
        for left_index, left in enumerate(unique):
            for right in unique[left_index + 1:]:
                pairs[f'{left}:{right}'] += 1
    return {
        'shared_edge_pair_count': len(pairs),
        'shared_edge_pairs': [
            {
                'left_face_index': int(key.split(':')[0]),
                'right_face_index': int(key.split(':')[1]),
                'shared_edge_count': count,
            }
            for key, count in sorted(pairs.items())
        ],
        'non_manifold_edges': non_manifold,
    }


def topology_summary(shape):
    """Build a deterministic topology summary for a source shape.

    This is intentionally a summary, not the P1 BrepGraph. It is safe to
    persist during P0.5 and contains enough evidence to locate the common
    surface/curve/adjacency blockers without changing recognition behavior.
    """
    solids = [TopoDS.Solid_s(item) for item in explore(shape, TopAbs_SOLID)]
    faces = _unique_subshapes(shape, TopAbs_FACE)
    edges = _unique_subshapes(shape, TopAbs_EDGE)
    vertices = _unique_subshapes(shape, TopAbs_VERTEX)
    face_records = [_face_record(face, index, edges) for index, face in enumerate(faces, 1)]
    surface_histogram = Counter(record['surface_type'] for record in face_records)
    curve_histogram, edge_records = _edge_histogram(edges)
    body_records = []
    for index, solid in enumerate(solids, 1):
        body_faces = _unique_subshapes(solid, TopAbs_FACE)
        body_edges = _unique_subshapes(solid, TopAbs_EDGE)
        body_vertices = _unique_subshapes(solid, TopAbs_VERTEX)
        body_records.append({
            'solid_index': index,
            'face_count': len(body_faces),
            'edge_count': len(body_edges),
            'vertex_count': len(body_vertices),
            'volume_mm3': _safe_float(volume(solid)),
            'bbox_mm': list(map(float, bbox(solid))),
        })
    return {
        'format': 'step-source-topology',
        'version': 1,
        'shape_signature': shape_signature(shape),
        'entity_counts': {
            'solid_count': len(solids),
            'face_count': len(faces),
            'edge_count': len(edges),
            'vertex_count': len(vertices),
        },
        'surface_type_histogram': dict(sorted(surface_histogram.items())),
        'curve_type_histogram': curve_histogram,
        'bodies': body_records,
        'faces': face_records,
        'edges': edge_records,
        'face_adjacency': _face_adjacency(faces, face_records),
        'p1_graph_status': 'summary_only',
    }


def _attempts_from_failure(failure: dict[str, Any]) -> list[dict[str, Any]]:
    attempts = []
    for strategy_id in ('prism', 'revolution', 'csg', 'partition'):
        value = failure.get(strategy_id)
        if not isinstance(value, dict):
            continue
        item = {
            'strategy_id': strategy_id,
            'status': 'failed',
            'reason_code': value.get('reason_code') or value.get('failure_reason') or failure.get('reason_code'),
            'source_body_id': value.get('solid_index'),
        }
        if 'attempts' in value:
            item['attempt_count'] = len(value['attempts']) if isinstance(value['attempts'], list) else None
        if 'elapsed_seconds' in value:
            item['elapsed_seconds'] = value['elapsed_seconds']
        item['evidence'] = deepcopy(value)
        attempts.append(item)
    return attempts


def recognition_diagnostics(report: dict[str, Any]) -> dict[str, Any]:
    """Normalize report recognition evidence into a sidecar diagnostics record."""
    provenance = report.get('recognition') or {}
    failure = report.get('recognition_failure') or {}
    features = provenance.get('features') if isinstance(provenance, dict) else []
    if not isinstance(features, list):
        features = []
    recognizer_attempts = _attempts_from_failure(failure) if isinstance(failure, dict) else []
    return {
        'format': 'step-recognition-diagnostics',
        'version': 1,
        'case_id': report.get('input', '').rsplit('\\', 1)[-1].rsplit('/', 1)[-1].rsplit('.', 1)[0],
        'source_sha256': (report.get('identity') or {}).get('source_sha256'),
        'implementation_fingerprint': (report.get('implementation') or {}).get('fingerprint'),
        'outcome': report.get('outcome'),
        'stage': report.get('stage'),
        'reason_code': report.get('reason_code'),
        'elapsed_seconds': report.get('elapsed_seconds'),
        'source_body_count': provenance.get('source_solid_count'),
        'features': deepcopy(features),
        'recognizer_attempts': recognizer_attempts,
        'last_failure': {
            'reason_code': report.get('reason_code'),
            'stage': report.get('stage'),
            'error': report.get('error'),
        },
        'failure_evidence': deepcopy(failure),
        'sidecar_only': True,
    }






def write_case_sidecars(source, output, report):
    """Write P0.5 sidecars outside the recognition worker deadline.

    The parent batch process calls this after the worker has exited. This keeps
    diagnostics from changing native recognition timing or consuming the case
    hard timeout. The report is returned with sidecar artifact references; the
    caller owns the final report write.
    """
    from pathlib import Path
    from step2code.batch.identity import file_hash
    from step2code.kernel.import_step import read_reference
    from step2code.plan.codec import write_json

    source = Path(source)
    output = Path(output)
    artifacts = report.setdefault('artifacts', {})
    if artifacts.get('source') and (output / artifacts['source']).is_file():
        try:
            source_record = json.loads((output / artifacts['source']).read_text(encoding='utf-8'))
            source_topology = source_record.get('topology', {})
            counts = source_topology.get('solid_count', 0), source_topology.get('face_count', 0)
            config = (report.get('identity') or {}).get('configuration', {})
            recognition_config = config.get('recognition', {}) if isinstance(config, dict) else {}
            max_solids = int(recognition_config.get('max_solids', 64))
            max_detailed_faces = max(5000, int(recognition_config.get('max_csg_faces', 80)) * 100)
            if counts[0] > max_solids or counts[1] > max_detailed_faces:
                topology = {
                    'format': 'step-source-topology',
                    'version': 1,
                    'shape_signature': source_topology,
                    'entity_counts': {
                        'solid_count': source_topology.get('solid_count'),
                        'face_count': source_topology.get('face_count'),
                        'edge_count': source_topology.get('edge_occurrences'),
                    },
                    'surface_type_histogram': None,
                    'curve_type_histogram': None,
                    'bodies': None,
                    'faces': None,
                    'edges': None,
                    'face_adjacency': None,
                    'p1_graph_status': 'summary_only',
                    'detail_status': 'skipped_large_source',
                    'detail_limits': {
                        'max_solids': max_solids,
                        'max_faces': max_detailed_faces,
                    },
                }
            else:
                diagnostic_shape, _ = read_reference(source)
                topology = topology_summary(diagnostic_shape)
            write_json(output / 'source-topology.json', {
                'source_sha256': (report.get('identity') or {}).get('source_sha256'),
                'topology': topology,
            })
            artifacts['source_topology'] = 'source-topology.json'
        except Exception as diagnostics_error:
            report.setdefault('diagnostics_errors', []).append({
                'stage': report.get('stage'),
                'artifact': 'source-topology.json',
                'error': str(diagnostics_error),
            })
    try:
        write_json(output / 'recognition-diagnostics.json', recognition_diagnostics(report))
        artifacts['recognition_diagnostics'] = 'recognition-diagnostics.json'
    except Exception as diagnostics_error:
        report.setdefault('diagnostics_errors', []).append({
            'stage': report.get('stage'),
            'artifact': 'recognition-diagnostics.json',
            'error': str(diagnostics_error),
        })
    report['artifact_hashes'] = {
        name: file_hash(output / relative)
        for name, relative in artifacts.items()
        if (output / relative).is_file()
    }
    return report
