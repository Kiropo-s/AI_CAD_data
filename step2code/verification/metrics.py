"""Reusable B-rep/STEP geometry metrics for evaluation.

This module is intentionally independent of the IR adapter. It accepts OCP
shapes or STEP files and reports volume, area, bbox, solid count, volume IoU,
and Chamfer Distance. CD uses area-weighted surface sampling (8192 points by
default); lower sample counts are noticeably noisier.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from OCP.BRep import BRep_Tool
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.Bnd import Bnd_Box
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS


class GeometryMetricsError(ValueError):
    """Raised when a B-rep measurement cannot be computed reliably."""


def read_step(path: Path) -> Any:
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone or reader.TransferRoots() < 1:
        raise GeometryMetricsError(f"cannot read STEP: {path}")
    shape = reader.OneShape()
    if shape.IsNull() or not BRepCheck_Analyzer(shape).IsValid():
        raise GeometryMetricsError(f"invalid STEP geometry: {path}")
    return shape


def solid_count(shape: Any) -> int:
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    count = 0
    while explorer.More():
        count += 1
        explorer.Next()
    return count


def volume(shape: Any) -> float:
    if solid_count(shape) < 1:
        raise GeometryMetricsError("shape contains no solids")
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return float(props.Mass())


def area(shape: Any) -> float:
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, props)
    return float(props.Mass())


def bbox(shape: Any) -> list[float]:
    box = Bnd_Box()
    box.SetGap(0.0)
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    return [float(value) for value in box.Get()]


def boolean_volume(shape: Any, volume_function=None) -> float:
    """A valid boolean result may contain no solids: its volume is zero."""
    if shape.IsNull() or not BRepCheck_Analyzer(shape).IsValid():
        raise GeometryMetricsError("invalid boolean topology")
    if solid_count(shape) == 0:
        return 0.0
    value = (volume_function or volume)(shape)
    if not math.isfinite(value) or value < 0:
        raise GeometryMetricsError("nonfinite or negative boolean volume")
    return value


def _intersection_volume(first: Any, second: Any, volume_function=None) -> float:
    operation = BRepAlgoAPI_Common(first, second)
    operation.Build()
    shape = operation.Shape()
    if not operation.IsDone() or shape.IsNull() or not BRepCheck_Analyzer(shape).IsValid():
        raise GeometryMetricsError("B-rep intersection failed or produced invalid topology")
    return boolean_volume(shape, volume_function)


def volume_iou(first: Any, second: Any, *, volume_function=None) -> float:
    measure = volume_function or volume
    first_volume, second_volume = measure(first), measure(second)
    if first_volume <= 0 or second_volume <= 0:
        raise GeometryMetricsError("IoU requires nonempty solids")
    intersection = _intersection_volume(first, second, volume_function)
    union = first_volume + second_volume - intersection
    if union <= 0 or intersection > min(first_volume, second_volume) + max(1e-9, min(first_volume, second_volume) * 1e-8):
        raise GeometryMetricsError(
            "inconsistent B-rep intersection volume: "
            f"reference={first_volume:.17g}, candidate={second_volume:.17g}, "
            f"intersection={intersection:.17g}, "
            f"overshoot_relative={(intersection - min(first_volume, second_volume)) / min(first_volume, second_volume):.17g}"
        )
    return intersection / union


def symmetric_difference_volume(first: Any, second: Any, *, volume_function=None) -> float:
    total = 0.0
    for left, right in ((first, second), (second, first)):
        operation = BRepAlgoAPI_Cut(left, right)
        operation.Build()
        if not operation.IsDone() or not BRepCheck_Analyzer(operation.Shape()).IsValid():
            raise GeometryMetricsError("B-rep symmetric difference failed or produced invalid topology")
        total += boolean_volume(operation.Shape(), volume_function)
    return total


def _surface_triangles(shape: Any, linear_deflection: float) -> np.ndarray:
    # Never reuse or mutate the caller's cached triangulation.
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    from OCP.BRepTools import BRepTools
    shape = BRepBuilderAPI_Copy(shape, True, False).Shape()
    BRepTools.Clean_s(shape)
    mesher = BRepMesh_IncrementalMesh(shape, linear_deflection, False, 0.2, False)
    mesher.Perform()
    if not mesher.IsDone():
        raise GeometryMetricsError("surface tessellation failed")
    triangles = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        location = TopLoc_Location()
        mesh = BRep_Tool.Triangulation_s(face, location, 0)
        if mesh is None or mesh.NbTriangles() == 0:
            raise GeometryMetricsError("face has no surface triangles")
        transform = location.Transformation()
        nodes = np.array(
            [tuple(mesh.Node(index).Transformed(transform).Coord())
             for index in range(1, mesh.NbNodes() + 1)],
            dtype=np.float64,
        )
        for index in range(1, mesh.NbTriangles() + 1):
            a, b, c = mesh.Triangle(index).Get()
            triangles.append(nodes[[a - 1, b - 1, c - 1]])
        explorer.Next()
    if not triangles:
        raise GeometryMetricsError("solid has no surface triangles")
    # STEP may enumerate identical faces/triangles differently.
    ordered = [np.asarray(sorted(triangle, key=lambda point: tuple(point))) for triangle in triangles]
    ordered.sort(key=lambda triangle: tuple(triangle.ravel()))
    return np.stack(ordered)


def _sample_surface(shape: Any, count: int, seed: int, linear_deflection: float) -> np.ndarray:
    triangles = _surface_triangles(shape, linear_deflection)
    areas = 0.5 * np.linalg.norm(
        np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1
    )
    total_area = float(areas.sum())
    if not math.isfinite(total_area) or total_area <= 0:
        raise GeometryMetricsError("invalid surface mesh area")
    rng = np.random.default_rng(seed)
    selected = triangles[rng.choice(len(triangles), size=count, p=areas / total_area)]
    barycentric = rng.random((count, 2))
    fold = barycentric.sum(axis=1) > 1
    barycentric[fold] = 1 - barycentric[fold]
    return (selected[:, 0] + barycentric[:, :1] * (selected[:, 1] - selected[:, 0])
            + barycentric[:, 1:] * (selected[:, 2] - selected[:, 0]))


def chamfer_distance(first: Any, second: Any, *, count: int = 8192, seed: int = 0,
                     linear_deflection: float = 0.1) -> tuple[float, float]:
    """Return symmetric mean squared CD (mm^2) and mean distance (mm)."""
    if count < 1 or not math.isfinite(linear_deflection) or linear_deflection <= 0:
        raise GeometryMetricsError("invalid Chamfer sampling parameters")
    points_a = _sample_surface(first, count, seed, linear_deflection)
    points_b = _sample_surface(second, count, seed, linear_deflection)
    a_to_b = cKDTree(points_b).query(points_a)[0]
    b_to_a = cKDTree(points_a).query(points_b)[0]
    return (float(np.mean(a_to_b ** 2) + np.mean(b_to_a ** 2)),
            float((np.mean(a_to_b) + np.mean(b_to_a)) / 2))


def _relative_error(actual: float, expected: float) -> float:
    if abs(expected) < 1e-15:
        return abs(actual - expected)
    return abs(actual - expected) / abs(expected)


def _bbox_error(actual: list[float], expected: list[float]) -> float:
    spans = [max(expected[index + 3] - expected[index], 1.0) for index in range(3)]
    return max(abs(actual[index] - expected[index]) / spans[index % 3] for index in range(6))

