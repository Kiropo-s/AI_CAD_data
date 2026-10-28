"""Deterministic, proper similarity registration of occupied B-rep space.

Only measurement copies are transformed. No reflection, per-body registration,
healing, or fuzzy boolean tolerance is used.
"""
from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy, BRepBuilderAPI_Transform
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.gp import gp_Trsf

from json2scapi.evaluation.metrics import GeometryMetricsError, _sample_surface, bbox, solid_count

LENGTH = 100.0
DEFLECTION = 0.001 * LENGTH
REGISTRATION_SEED = 17


def solids(shape: Any) -> list[Any]:
    result = []
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        result.append(explorer.Current())
        explorer.Next()
    return result


def occupied_space(shape: Any) -> Any:
    if shape.IsNull() or not BRepCheck_Analyzer(shape).IsValid():
        raise GeometryMetricsError("invalid input topology")
    parts = [BRepBuilderAPI_Copy(part, True, False).Shape() for part in solids(shape)]
    if not parts:
        raise GeometryMetricsError("shape contains no solids")
    # Sorting eliminates incidental body enumeration differences.
    parts.sort(key=lambda item: tuple(bbox(item)))
    result = BRepBuilderAPI_Copy(parts[0], True, False).Shape()
    for part in parts[1:]:
        operation = BRepAlgoAPI_Fuse(result, part)
        operation.Build()
        if not operation.IsDone():
            raise GeometryMetricsError("occupied-space fusion failed")
        result = operation.Shape()
        if result.IsNull() or not BRepCheck_Analyzer(result).IsValid() or not solid_count(result):
            raise GeometryMetricsError("occupied-space fusion produced invalid topology")
    return result


def transform_shape(shape: Any, matrix: np.ndarray) -> Any:
    transform = gp_Trsf()
    transform.SetValues(*map(float, matrix[:3, :].ravel()))
    result = BRepBuilderAPI_Transform(shape, transform, True).Shape()
    if result.IsNull() or not BRepCheck_Analyzer(result).IsValid():
        raise GeometryMetricsError("similarity transform produced invalid topology")
    return result


def properties(shape: Any) -> tuple[float, np.ndarray, np.ndarray]:
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    volume = float(props.Mass())
    center = np.array(props.CentreOfMass().Coord(), dtype=float)
    tensor = props.MatrixOfInertia()
    inertia = np.array([[tensor.Value(i + 1, j + 1) for j in range(3)] for i in range(3)])
    if not (math.isfinite(volume) and volume > 0 and np.isfinite(center).all() and np.isfinite(inertia).all()):
        raise GeometryMetricsError("invalid volume, centroid or inertia")
    return volume, center, inertia


def normalize(shape: Any) -> tuple[Any, dict[str, Any], np.ndarray]:
    volume, center, inertia = properties(shape)
    length = math.sqrt(6 * float(np.trace(inertia)) / volume)
    if not math.isfinite(length) or length <= 0:
        raise GeometryMetricsError("invalid characteristic length")
    scale = LENGTH / length
    matrix = np.eye(4)
    matrix[:3, :3] *= scale
    matrix[:3, 3] = -scale * center
    normalized = transform_shape(shape, matrix)
    # Obtain principal axes in normalized coordinates to avoid large-coordinate cancellation.
    normalized_volume, _, tensor = properties(normalized)
    expected_volume = volume * scale ** 3
    scaling_error = abs(normalized_volume - expected_volume) / expected_volume
    if scaling_error > 1e-6:
        raise GeometryMetricsError("volume integration changed under a known similarity transform")
    eigenvalues, axes = np.linalg.eigh(tensor)
    if eigenvalues[0] <= 0:
        raise GeometryMetricsError("nonpositive principal inertia")
    if np.linalg.det(axes) < 0:
        axes[:, -1] *= -1
    degenerate = bool(np.min(np.diff(eigenvalues)) / np.max(eigenvalues) < 1e-4)
    return normalized, {
        "centroid_mm": center.tolist(), "characteristic_length_mm": length,
        "scale_to_normalized": scale, "target_length_mm": LENGTH,
        "matrix": matrix.tolist(), "inverse_matrix": np.linalg.inv(matrix).tolist(),
        "principal_moments": eigenvalues.tolist(), "principal_axes_degenerate": degenerate,
        "effective_source_deflection_mm": DEFLECTION / scale,
        "volume_scaling_consistency_relative": scaling_error,
    }, axes


def proper_axis_rotations(first_axes: np.ndarray, second_axes: np.ndarray):
    for permutation in itertools.permutations(range(3)):
        for signs in itertools.product((-1, 1), repeat=3):
            mapping = np.eye(3)[:, permutation] @ np.diag(signs)
            if np.linalg.det(mapping) > 0:
                yield first_axes @ mapping @ second_axes.T


def distances(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return cKDTree(b).query(a)[0], cKDTree(a).query(b)[0]


def score(a: np.ndarray, b: np.ndarray) -> float:
    ab, ba = distances(a, b)
    return float((ab.mean() + ba.mean()) / 2)


def kabsch(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    # Both complete bodies were centered at the volume centroid. Keep that center
    # fixed instead of introducing a sampling-dependent translation.
    u, _, vt = np.linalg.svd(source.T @ target)
    correction = np.eye(3)
    correction[-1, -1] = np.sign(np.linalg.det(vt.T @ u.T))
    return vt.T @ correction @ u.T


def planar_frames(shape: Any) -> list[np.ndarray]:
    """Analytic seeds for repeated inertia eigenvalues (e.g. a rotated cube).

    This only proposes proper rotations; the B-rep hard gates still decide.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopoDS import TopoDS
    normals = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        surface = BRepAdaptor_Surface(TopoDS.Face_s(explorer.Current()))
        if surface.GetType() == GeomAbs_Plane:
            n = np.array(surface.Plane().Axis().Direction().Coord())
            if n[np.argmax(np.abs(n))] < 0:
                n = -n
            if not any(abs(np.dot(n, other)) > 1 - 1e-8 for other in normals):
                normals.append(n)
        explorer.Next()
    normals.sort(key=lambda n: tuple(np.round(n, 8)))
    frames = []
    for x, other in itertools.permutations(normals[:12], 2):
        y = other - np.dot(x, other) * x
        if np.linalg.norm(y) < 1e-6:
            continue
        y /= np.linalg.norm(y)
        for sx, sy in itertools.product((-1, 1), repeat=2):
            a, b = sx * x, sy * y
            frames.append(np.column_stack((a, b, np.cross(a, b))))
    return frames


def candidates(first: Any, second: Any, axes_a: np.ndarray, axes_b: np.ndarray) -> list[dict[str, Any]]:
    a = _sample_surface(first, 2048, REGISTRATION_SEED, DEFLECTION)
    b = _sample_surface(second, 2048, REGISTRATION_SEED, DEFLECTION)
    tree_a = cKDTree(a)
    initial = [np.eye(3), *proper_axis_rotations(axes_a, axes_b),
               *Rotation.random(128, random_state=REGISTRATION_SEED).as_matrix()]
    frames_a, frames_b = planar_frames(first), planar_frames(second)
    if frames_a and frames_b:
        initial.extend(frames_a[0] @ frame.T for frame in frames_b)
    ranked = sorted(enumerate(initial), key=lambda item: (score(a, b @ item[1].T), item[0]))[:8]
    result = []
    for index, rotation in ranked:
        result.append({"rotation": rotation, "score": score(a, b @ rotation.T), "initial": index, "refined": False})
        current = rotation.copy()
        for _ in range(100):
            moved = b @ current.T
            # Symmetric correspondences; no reflection or scale is estimated.
            _, forward = tree_a.query(moved)
            _, backward = cKDTree(moved).query(a)
            source = np.vstack((moved, moved[backward]))
            target = np.vstack((a[forward], a))
            update = kabsch(source, target)
            current = update @ current
            if np.linalg.norm(update - np.eye(3)) < 1e-10:
                break
        result.append({"rotation": current, "score": score(a, b @ current.T), "initial": index, "refined": True})
    result.sort(key=lambda item: (item["score"], item["initial"], item["refined"]))
    unique = []
    for item in result:
        if not any(np.linalg.norm(item["rotation"] - other["rotation"]) < 1e-8 for other in unique):
            unique.append(item)
    for item in unique:
        item["initial_count"] = len(initial)
    return unique
