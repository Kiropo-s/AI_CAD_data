"""Shared source-frame numerical conditioning and reliable occupied-space union."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.spatial import cKDTree
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy, BRepBuilderAPI_Transform
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.gp import gp_Trsf

from step2code.verification.metrics import GeometryMetricsError, _sample_surface, bbox, solid_count, volume

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
    for index,part in enumerate(parts[1:],1):
        before,added=volume(result),volume(part)
        operation = BRepAlgoAPI_Fuse(result, part)
        operation.Build()
        if not operation.IsDone():
            raise GeometryMetricsError("occupied-space fusion failed")
        result = operation.Shape()
        if result.IsNull() or not BRepCheck_Analyzer(result).IsValid() or not solid_count(result):
            raise GeometryMetricsError("occupied-space fusion produced invalid topology")
        after=volume(result);lower=max(before,added);upper=before+added
        tolerance=max(1e-9,upper*1e-6)
        if not math.isfinite(after) or after<lower-tolerance or after>upper+tolerance:
            raise GeometryMetricsError(f"occupied-space fusion violates volume bounds at part {index}: "
                f"before={before:.17g}, added={added:.17g}, after={after:.17g}, "
                f"expected=[{lower:.17g}, {upper:.17g}]")
    return result


def transform_shape(shape: Any, matrix: np.ndarray) -> Any:
    transform = gp_Trsf()
    transform.SetValues(*map(float, matrix[:3, :].ravel()))
    result = BRepBuilderAPI_Transform(shape, transform, True).Shape()
    if result.IsNull() or not BRepCheck_Analyzer(result).IsValid():
        raise GeometryMetricsError("shared conditioning transform produced invalid topology")
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
        raise GeometryMetricsError("volume integration changed under a known shared conditioning transform")
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



def distances(a, b):
    return cKDTree(b).query(a)[0], cKDTree(a).query(b)[0]
