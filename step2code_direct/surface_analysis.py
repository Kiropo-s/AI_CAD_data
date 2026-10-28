"""Surface and feature diagnostics used by the direct STEP route.

The analysis is deliberately descriptive: it never fabricates a replacement
shape and it never authorizes code emission.  It gives the recognizers a
stable vocabulary for route selection and gives reports a useful failure class.
"""
from __future__ import annotations

from collections import Counter
import math

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import (
    GeomAbs_BSplineSurface, GeomAbs_BezierSurface, GeomAbs_Cone,
    GeomAbs_Cylinder, GeomAbs_Plane, GeomAbs_Sphere, GeomAbs_SurfaceOfExtrusion,
    GeomAbs_SurfaceOfRevolution, GeomAbs_Torus,
)
from OCP.TopAbs import TopAbs_FACE
from OCP.TopoDS import TopoDS

from step2code_direct.geometry import explore


def _surface_label(kind):
    return str(kind).split('.')[-1].removeprefix('GeomAbs_')


def _is_freeform(kind):
    return kind in (GeomAbs_BSplineSurface, GeomAbs_BezierSurface,
                    GeomAbs_SurfaceOfExtrusion)


def analyze_solid(solid, tolerance=1e-6):
    """Return deterministic surface/feature complexity evidence for one solid."""
    counts = Counter()
    face_records = []
    revolution_faces = []
    analytic_faces = 0
    freeform_faces = 0
    planar_faces = 0
    for face_index, raw_face in enumerate(explore(solid, TopAbs_FACE), 1):
        face = TopoDS.Face_s(raw_face)
        surface = BRepAdaptor_Surface(face)
        kind = surface.GetType()
        label = _surface_label(kind)
        counts[label] += 1
        if kind in (GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone,
                    GeomAbs_Sphere, GeomAbs_Torus, GeomAbs_SurfaceOfRevolution):
            analytic_faces += 1
        if _is_freeform(kind):
            freeform_faces += 1
        if kind == GeomAbs_Plane:
            planar_faces += 1
        if kind == GeomAbs_SurfaceOfRevolution:
            revolution_faces.append(face_index)
        face_records.append(dict(index=face_index, kind=label,
                                 orientation=str(face.Orientation()).split('.')[-1]))

    total = sum(counts.values())
    freeform_ratio = freeform_faces / max(1, total)
    # Penalize surface families that are currently expensive to search.  This
    # is a route hint only; final acceptance still uses exact OCC validation.
    complexity = (total + 2 * counts.get('BSplineSurface', 0)
                  + 3 * counts.get('SurfaceOfRevolution', 0)
                  + 2 * counts.get('Torus', 0))
    if freeform_faces and freeform_faces >= max(8, total // 3):
        recoverability = 'freeform_dominant'
    elif revolution_faces:
        recoverability = 'revolution_feature'
    elif freeform_faces:
        recoverability = 'mixed_analytic_freeform'
    elif total <= 16:
        recoverability = 'analytic_compact'
    else:
        recoverability = 'analytic_complex'

    if recoverability == 'freeform_dominant':
        route_hint = 'freeform_gate'
    elif revolution_faces:
        route_hint = 'revolution_then_csg'
    elif complexity >= 36:
        route_hint = 'feature_grouped_csg'
    else:
        route_hint = 'analytic_csg'
    return dict(
        surface_types=dict(sorted(counts.items())),
        face_count=total,
        planar_face_count=planar_faces,
        analytic_face_count=analytic_faces,
        freeform_face_count=freeform_faces,
        freeform_face_ratio=freeform_ratio,
        revolution_face_indices=revolution_faces,
        complexity_score=complexity,
        recoverability=recoverability,
        route_hint=route_hint,
        face_records=face_records,
        tolerance=tolerance,
    )


def failure_category(analysis, diagnostics=None):
    """Map a failed recognition attempt to an actionable category."""
    diagnostics = diagnostics or {}
    if analysis.get('recoverability') == 'freeform_dominant':
        return 'freeform_surface'
    if analysis.get('revolution_face_indices'):
        return 'rotation_feature'
    hybrid = diagnostics.get('hybrid') or diagnostics.get('recognition') or {}
    if hybrid.get('stop_reason') in ('time_budget', 'boolean_trial_budget',
                                     'no_monotone_solution_within_depth_and_operands'):
        return 'hybrid_search'
    partition = diagnostics.get('partition') or {}
    if partition:
        return 'partition_candidate'
    return 'unsupported_topology'
