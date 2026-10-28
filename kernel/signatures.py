"""Auditable signatures at each geometry/serialization boundary."""
import math
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID, TopAbs_FACE, TopAbs_EDGE
from step2code.verification.metrics import volume, bbox


def explore(shape,kind):
    e=TopExp_Explorer(shape,kind)
    while e.More():
        yield e.Current()
        e.Next()


def shape_signature(shape):
    solids=list(explore(shape,TopAbs_SOLID))
    v=volume(shape) if solids else 0.0
    return dict(valid=not shape.IsNull() and BRepCheck_Analyzer(shape).IsValid(),
        solid_count=len(solids),face_count=sum(1 for _ in explore(shape,TopAbs_FACE)),
        edge_occurrences=sum(1 for _ in explore(shape,TopAbs_EDGE)),
        volume_mm3=v if math.isfinite(v) else None, bbox_mm=bbox(shape) if solids else None,
        bodies=[dict(volume_mm3=volume(s),bbox_mm=bbox(s),valid=BRepCheck_Analyzer(s).IsValid()) for s in solids])
