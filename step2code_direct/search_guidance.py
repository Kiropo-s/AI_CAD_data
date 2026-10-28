"""Deterministic occupancy hints for ranking only; never an acceptance test."""
from dataclasses import dataclass
import time

import numpy as np
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.TopAbs import TopAbs_IN, TopAbs_ON, TopAbs_OUT
from OCP.gp import gp_Pnt
from json2scapi.evaluation.metrics import bbox


class GuidanceUnavailable(ValueError):
    pass


def _halton(count):
    result = np.zeros((count, 3), dtype=float)
    for dimension, base in enumerate((2, 3, 5)):
        indices = np.arange(1, count + 1)
        factor = 1.0 / base
        while np.any(indices):
            result[:, dimension] += factor * (indices % base)
            indices //= base
            factor /= base
    return result


def _membership(shape, points, tolerance, deadline):
    bounds = np.asarray(bbox(shape), dtype=float)
    if not np.all(np.isfinite(bounds)):
        raise GuidanceUnavailable('Nonfinite sampling bounds')
    inside_box = np.all((points >= bounds[:3] - tolerance) &
                        (points <= bounds[3:] + tolerance), axis=1)
    result = np.zeros(len(points), dtype=bool)
    classifier = BRepClass3d_SolidClassifier(shape)
    for ordinal, index in enumerate(np.flatnonzero(inside_box)):
        if ordinal % 16 == 0 and time.monotonic() >= deadline:
            raise GuidanceUnavailable('Sampling time budget exhausted')
        classifier.Perform(gp_Pnt(*points[index]), tolerance)
        state = classifier.State()
        if state not in (TopAbs_IN, TopAbs_ON, TopAbs_OUT):
            raise GuidanceUnavailable('Point classifier returned an unknown state')
        result[index] = state in (TopAbs_IN, TopAbs_ON)
    return result


@dataclass
class Guidance:
    reference: object
    atoms: dict

    def difference(self, mask):
        denominator = max(1, int(np.count_nonzero(self.reference)))
        missing = np.count_nonzero(self.reference & ~mask) / denominator
        extra = np.count_nonzero(~self.reference & mask) / denominator
        return float(missing), float(extra)

    def combine(self, current, atom_index, operation):
        operand = self.atoms.get(atom_index)
        if current is None or operand is None:
            return None
        if operation == 'union':
            return current | operand
        if operation == 'cut':
            return current & ~operand
        return current & operand


def build_guidance(reference, atoms, tolerance, deadline, diagnostics):
    """Use two fixed point clouds to cover both reference and exterior material.

    This mixture is deliberately a ranking heuristic, not an unbiased volume
    estimate. Thin or unsampled features must still pass exact difference tests.
    Partial atom masks are usable; unranked candidates remain eligible.
    """
    reference_bounds = np.asarray(bbox(reference), dtype=float)
    atom_bounds = np.asarray([bbox(atom.shape) for atom in atoms], dtype=float)
    if not np.all(np.isfinite(reference_bounds)) or not np.all(np.isfinite(atom_bounds)):
        raise GuidanceUnavailable('Nonfinite sampling bounds')
    lower = np.minimum(reference_bounds[:3], atom_bounds[:, :3].min(axis=0))
    upper = np.maximum(reference_bounds[3:], atom_bounds[:, 3:].max(axis=0))
    local = reference_bounds[:3] + _halton(512) * (reference_bounds[3:] - reference_bounds[:3])
    exterior = lower + _halton(256) * (upper - lower)
    points = np.vstack((local, exterior))
    diagnostics['sampling_points'] = len(points)
    reference_mask = _membership(reference, points, tolerance, deadline)
    if not reference_mask.any():
        raise GuidanceUnavailable('No sampled interior points in reference')
    guidance = Guidance(reference_mask, {})
    for index, atom in enumerate(atoms):
        if time.monotonic() >= deadline:
            break
        try:
            guidance.atoms[index] = _membership(atom.shape, points, tolerance, deadline)
        except Exception as exc:
            if len(diagnostics['sampling_errors']) < 8:
                diagnostics['sampling_errors'].append({'atom_index': index, 'error': str(exc)})
    diagnostics['sampled_atoms'] = len(guidance.atoms)
    return guidance
