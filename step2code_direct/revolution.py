"""Recovery of bounded solids from OCC SurfaceOfRevolution faces."""
from __future__ import annotations

import math
import time

import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
from OCP.GeomAbs import GeomAbs_BSplineCurve, GeomAbs_SurfaceOfRevolution
from OCP.TopoDS import TopoDS
from OCP.TopAbs import TopAbs_FACE
from OCP.gp import gp_Pnt

from step2code_direct.geometry import UnsupportedGeometry, explore
from json2scapi.evaluation.metrics import bbox, volume, solid_count


def _array(point):
    return np.asarray(point.Coord(), dtype=float)


def _project_to_axis(point, axis):
    location = _array(axis.Location())
    direction = np.asarray(axis.Direction().Coord(), dtype=float)
    value = np.dot(_array(point) - location, direction)
    return gp_Pnt(*(location + value * direction).tolist())


def _array_list(array):
    if hasattr(array, 'Coord'):
        array = array.Coord()
    return [float(value) for value in np.asarray(array, dtype=float)]


def _bspline_parameters(curve):
    if curve.GetType() != GeomAbs_BSplineCurve:
        raise UnsupportedGeometry(f'Unsupported revolution basis curve: {curve.GetType()}')
    bspline = curve.BSpline()
    poles = [_array_list(bspline.Pole(i)) for i in range(1, bspline.NbPoles() + 1)]
    knots_array = bspline.Knots()
    mult_array = bspline.Multiplicities()
    knots = [float(knots_array.Value(i)) for i in range(1, knots_array.Length() + 1)]
    multiplicities = [int(mult_array.Value(i)) for i in range(1, mult_array.Length() + 1)]
    weights = None
    if bspline.IsRational():
        weights_array = bspline.Weights()
        weights = [float(weights_array.Value(i)) for i in range(1, weights_array.Length() + 1)]
    return dict(control_points=poles, degree=int(bspline.Degree()), knots=knots,
                multiplicities=multiplicities, weights=weights)


def _make_profile_face(adaptor, tolerance):
    curve = adaptor.BasisCurve()
    params = _bspline_parameters(curve)
    first, last = float(curve.FirstParameter()), float(curve.LastParameter())
    if not math.isfinite(first + last) or last - first <= tolerance:
        raise UnsupportedGeometry('Degenerate revolution basis curve')
    start, end = curve.Value(first), curve.Value(last)
    axis = adaptor.AxeOfRevolution()
    start_axis, end_axis = _project_to_axis(start, axis), _project_to_axis(end, axis)
    radial_start = float(_array(start).dot(_array(start) - _array(start_axis)))
    radial_end = float(_array(end).dot(_array(end) - _array(end_axis)))
    # Use distances instead of the signed dot product; the profile must leave
    # the axis somewhere, otherwise revolving it cannot form a positive solid.
    radial_start = float(np.linalg.norm(_array(start) - _array(start_axis)))
    radial_end = float(np.linalg.norm(_array(end) - _array(end_axis)))
    samples = [curve.Value(first + (last - first) * i / 8.0) for i in range(9)]
    max_radius = max(float(np.linalg.norm(_array(p) - _array(_project_to_axis(p, axis)))) for p in samples)
    if max_radius <= tolerance or radial_start <= tolerance and radial_end <= tolerance:
        raise UnsupportedGeometry('Revolution profile is on the axis')

    edge = BRepBuilderAPI_MakeEdge(curve.Curve(), first, last).Edge()
    edge_start = BRepBuilderAPI_MakeEdge(end, end_axis).Edge()
    axis_edge = BRepBuilderAPI_MakeEdge(end_axis, start_axis).Edge()
    edge_end = BRepBuilderAPI_MakeEdge(start_axis, start).Edge()
    wire_builder = BRepBuilderAPI_MakeWire()
    for item in (edge, edge_start, axis_edge, edge_end):
        wire_builder.Add(item)
    if not wire_builder.IsDone():
        raise UnsupportedGeometry('Revolution profile wire construction failed')
    face = BRepBuilderAPI_MakeFace(wire_builder.Wire()).Face()
    if face.IsNull():
        raise UnsupportedGeometry('Revolution profile face construction failed')
    return face, params, start, end, start_axis, end_axis, axis


def _native_revolution(adaptor, tolerance):
    face, params, start, end, start_axis, end_axis, axis = _make_profile_face(adaptor, tolerance)
    first_u, last_u = float(adaptor.FirstUParameter()), float(adaptor.LastUParameter())
    sweep = last_u - first_u
    if not math.isfinite(sweep) or sweep <= tolerance:
        raise UnsupportedGeometry('Invalid revolution angular interval')
    sweep = min(2.0 * math.pi, sweep)
    maker = BRepPrimAPI_MakeRevol(face, axis, sweep, True)
    if not maker.IsDone():
        raise UnsupportedGeometry('OCC revolution construction failed')
    shape = TopoDS.Solid_s(maker.Shape())
    if shape.IsNull() or solid_count(shape) != 1 or volume(shape) <= tolerance ** 3:
        raise UnsupportedGeometry('Revolution did not produce one positive solid')
    if not BRepCheck_Analyzer(shape).IsValid():
        raise UnsupportedGeometry('Invalid OCC revolution topology')
    axis_origin = _array_list(axis.Location())
    axis_direction = _array_list(axis.Direction())
    params.update(axis=axis_direction, origin=axis_origin,
                  angle_radians=sweep, start_point=_array_list(start),
                  end_point=_array_list(end), start_axis_point=_array_list(start_axis),
                  end_axis_point=_array_list(end_axis))
    params['parameter_origin'] = 'occ_surface_of_revolution_basis_bspline'
    return shape, params


def generate_revolution_atoms(solid, index, options, deadline, Atom):
    """Return exact revolution atoms and diagnostics for a source solid."""
    atoms, errors, seen = [], [], set()
    for face_index, raw_face in enumerate(explore(solid, TopAbs_FACE), 1):
        if time.monotonic() >= deadline:
            break
        face = TopoDS.Face_s(raw_face)
        adaptor = BRepAdaptor_Surface(face)
        if adaptor.GetType() != GeomAbs_SurfaceOfRevolution:
            continue
        try:
            shape, params = _native_revolution(adaptor, options.length_tolerance)
            signature = (params['degree'], tuple(round(v, 8) for point in params['control_points'] for v in point),
                         tuple(round(v, 8) for v in params['axis']), round(params['angle_radians'], 8))
            if signature in seen:
                continue
            seen.add(signature)
            role = 'cut' if str(face.Orientation()).endswith('REVERSED') else 'union'
            evidence = dict(source_face_index=face_index, source_solid_index=index,
                            method='analytic_surface_of_revolution',
                            parameter_origin=params['parameter_origin'],
                            feature_role='subtractive' if role == 'cut' else 'additive')
            atoms.append(Atom('revolution', shape, params, evidence, role))
        except Exception as exc:
            if len(errors) < 24:
                errors.append(dict(face_index=face_index,
                                   error=f'{type(exc).__name__}: {exc}'))
    return atoms, errors
