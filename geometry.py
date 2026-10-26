"""Verified native end-cap selection. No entities/sequence/IR conversion."""
from dataclasses import dataclass
import math

import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepLib import BRepLib
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Line, GeomAbs_Circle
from OCP.TopAbs import TopAbs_SOLID, TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX, TopAbs_WIRE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Vec
from json2scapi.evaluation.metrics import area, bbox, volume, symmetric_difference_volume


class UnsupportedGeometry(ValueError):
    def __init__(self, message, diagnostics=None, native_candidates=None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}
        self.native_candidates = native_candidates or []


@dataclass(frozen=True)
class Options:
    length_tolerance: float = 1e-6
    reconstruction_relative_tolerance: float = 1e-7
    max_candidates: int = 64
    max_solids: int = 64
    enable_hybrid: bool = True
    max_hybrid_atoms: int = 48
    max_boolean_steps: int = 8
    max_boolean_trials: int = 256
    hybrid_beam_width: int = 3
    hybrid_timeout_seconds: float = 20.0
    hybrid_search: str = 'lookahead'

    def __post_init__(self):
        if self.hybrid_search not in ('lookahead', 'guided', 'legacy'):
            raise ValueError('hybrid_search must be lookahead, guided or legacy')
        for name in ('length_tolerance', 'reconstruction_relative_tolerance', 'hybrid_timeout_seconds'):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be positive and finite')
        if min(self.max_candidates, self.max_solids, self.max_hybrid_atoms, self.max_boolean_steps,
               self.max_boolean_trials, self.hybrid_beam_width) < 1:
            raise ValueError('recognition limits must be positive')


@dataclass
class NativePrism:
    """Ephemeral kernel objects, passed straight to the source emitter.

    This is not an operation graph, portable CAD document or serialized plan.
    """
    face: object
    origin: list
    x_axis: list
    y_axis: list
    distance: float
    evidence: dict


def explore(shape, kind):
    iterator = TopExp_Explorer(shape, kind)
    while iterator.More():
        yield iterator.Current()
        iterator.Next()


def source_solids(shape, options):
    solids = [TopoDS.Solid_s(s) for s in explore(shape, TopAbs_SOLID)]
    if not solids:
        raise UnsupportedGeometry('No closed solids; surface-only STEP is unsupported')
    if len(solids) > options.max_solids:
        raise UnsupportedGeometry(f'Solid count {len(solids)} exceeds {options.max_solids}')
    for kind, avoid, label in ((TopAbs_FACE, TopAbs_SOLID, 'faces'),
                                (TopAbs_EDGE, TopAbs_FACE, 'edges'),
                                (TopAbs_VERTEX, TopAbs_EDGE, 'vertices')):
        if TopExp_Explorer(shape, kind, avoid).More():
            raise UnsupportedGeometry(f'Loose {label} outside solids are unsupported')
    return solids


def face_wires(face):
    outer = BRepTools.OuterWire_s(face)
    if outer.IsNull():
        raise UnsupportedGeometry('End cap has no outer wire')
    return [outer] + [TopoDS.Wire_s(w) for w in explore(face, TopAbs_WIRE) if not w.IsSame(outer)]


def wire_edges(wire, face):
    walker = BRepTools_WireExplorer(wire, face)
    edges = []
    while walker.More():
        edges.append(TopoDS.Edge_s(walker.Current()))
        walker.Next()
    if not edges:
        raise UnsupportedGeometry('Empty end-cap wire')
    return edges


def check_profile(face, tolerance):
    for wire in face_wires(face):
        for edge in wire_edges(wire, face):
            curve = BRepAdaptor_Curve(edge)
            first, last = curve.FirstParameter(), curve.LastParameter()
            if not math.isfinite(first + last):
                raise UnsupportedGeometry('Unbounded profile curve')
            if curve.GetType() not in (GeomAbs_Line, GeomAbs_Circle):
                raise UnsupportedGeometry(f'Unsupported profile curve: {curve.GetType()}')
            if curve.GetType() == GeomAbs_Line and curve.Value(first).Distance(curve.Value(last)) <= tolerance:
                raise UnsupportedGeometry('Degenerate profile line')
            if curve.GetType() == GeomAbs_Circle:
                complete = abs(abs(last-first)-2*math.pi) <= 1e-7
                if not complete and curve.Value(first).Distance(curve.Value(last)) <= tolerance:
                    raise UnsupportedGeometry('Ambiguous circular edge')


def recognize_prism(solid, index, options):
    source_volume = volume(solid)
    if not math.isfinite(source_volume) or source_volume <= 0:
        raise UnsupportedGeometry('Nonpositive source solid volume')
    source_box = np.array(bbox(solid))
    scale = float(np.linalg.norm(source_box[3:]-source_box[:3]))
    planar, types = [], {}
    for number, value in enumerate(explore(solid, TopAbs_FACE), 1):
        face = TopoDS.Face_s(value)
        surface = BRepAdaptor_Surface(face)
        kind = str(surface.GetType()).split('.')[-1]
        types[kind] = types.get(kind, 0)+1
        if surface.GetType() == GeomAbs_Plane:
            planar.append((area(face), number, face, surface.Plane()))
    planar.sort(key=lambda item: (-item[0], tuple(item[3].Location().Coord()),
                                  tuple(item[3].Axis().Direction().Coord()), item[1]))
    attempts, near_prisms = [], []
    for cap_area, cap_index, face, plane in planar[:options.max_candidates]:
        record = {'face_index': cap_index, 'face_area_mm2': cap_area}
        try:
            if cap_area <= options.length_tolerance**2:
                raise UnsupportedGeometry('Degenerate planar cap')
            check_profile(face, options.length_tolerance)
            outward = np.array(plane.Axis().Direction().Coord())
            direction = outward if face.Orientation() == TopAbs_REVERSED else -outward
            x_axis = np.array(plane.XAxis().Direction().Coord())
            y_axis = np.cross(direction, x_axis)
            cap_origin = np.array(plane.Location().Coord())
            opposite = []
            for _, other_index, _, other in planar:
                normal = np.array(other.Axis().Direction().Coord())
                separation = float((np.array(other.Location().Coord())-cap_origin) @ direction)
                if abs(abs(float(normal @ direction))-1) <= 1e-10 and separation > options.length_tolerance:
                    opposite.append((separation, other_index))
            if opposite:
                distance, opposite_index = max(opposite)
                record.update(distance_origin_kind='measured', distance_method='parallel_cap_separation',
                              opposite_face_index=opposite_index)
            else:
                distance = source_volume/cap_area
                record.update(distance_origin_kind='derived', distance_method='volume_over_cap_area')
            maker = BRepPrimAPI_MakePrism(face, gp_Vec(*(direction*distance).tolist()))
            if not maker.IsDone():
                raise UnsupportedGeometry('Prism kernel construction failed')
            prism = TopoDS.Solid_s(maker.Shape())
            if not BRepLib.OrientClosedSolid_s(prism) or not BRepCheck_Analyzer(prism).IsValid():
                raise UnsupportedGeometry('Invalid reconstructed prism')
            box_error = float(np.max(np.abs(np.array(bbox(prism))-source_box)))
            if box_error > max(options.length_tolerance*10, scale*1e-8):
                raise UnsupportedGeometry('Prism bounding box differs from source')
            difference = symmetric_difference_volume(solid, prism)/source_volume
            first = BRepAdaptor_Curve(wire_edges(face_wires(face)[0], face)[0])
            anchor = first.Circle().Location() if first.GetType() == GeomAbs_Circle else first.Value(first.FirstParameter())
            if (options.hybrid_search == 'lookahead' and options.enable_hybrid
                    and math.isfinite(difference) and difference <= 0.25):
                evidence = dict(record, method='retained_near_prism', source_solid_index=index,
                                distance_mm=distance, symmetric_difference_relative=difference)
                near_prisms.append(NativePrism(face, list(anchor.Coord()), x_axis.tolist(),
                                              y_axis.tolist(), distance, evidence))
                near_prisms.sort(key=lambda p: p.evidence['symmetric_difference_relative'])
                del near_prisms[3:]
            if not math.isfinite(difference) or difference > options.reconstruction_relative_tolerance:
                raise UnsupportedGeometry(f'Prism symmetric difference: {difference}')
            record.update(status='recognized', source_solid_index=index, method='analytic_end_cap_prism',
                          distance_mm=distance, symmetric_difference_relative=difference,
                          source_face_count=sum(types.values()), surface_types=types,
                          prior_attempts=attempts)
            return NativePrism(face, list(anchor.Coord()), x_axis.tolist(), y_axis.tolist(), distance, record)
        except Exception as exc:
            record.update(status='not_recognized', error=f'{type(exc).__name__}: {exc}')
            attempts.append(record)
    raise UnsupportedGeometry(f'Solid {index} is not a supported analytic straight extrusion',
        {'source_solid_index': index, 'surface_types': types, 'planar_faces': len(planar),
         'candidate_limit': options.max_candidates, 'attempts': attempts}, native_candidates=near_prisms)
