"""CD, occupied-space IoU and volume acceptance with explicit measurement errors."""
from __future__ import annotations

import numpy as np

from step2code.verification.metrics import bbox, solid_count
from step2code.verification.policy import THRESHOLDS, decide, metric_check
from step2code.verification.conditioning import normalize, occupied_space, properties, transform_shape
from step2code.verification.measurements import _pose_metrics


def evaluate(reference, candidate, *, samples=8192, mode='world'):
    """Reuse v4 measurement reliability; world mode never aligns one input alone.

    A shared centering/scaling transform conditions the numerical calculation.
    It preserves relative source/candidate dimensions and placement exactly.
    Surface distances and volumes are converted back to source millimeters.
    """
    if samples < 256:
        raise ValueError('CD requires at least 256 samples; production default is 8192')
    if mode!='world':
        raise ValueError('STEP Plan requires fixed source world coordinates')
    report = {
        'evaluation_version': 'fixed-frame-shape-v1',
        'metric_implementation': 'step2code/shape-v1',
        'coordinate_policy': 'source world frame; one shared numerical conditioning transform',
        'units': {'length': 'mm', 'volume': 'mm^3', 'squared_cd': 'mm^2'},
        'thresholds': dict(THRESHOLDS), 'checks': {}, 'metric_errors': [],
        'warnings': [], 'diagnostics': [], 'coordinate_check': {'status': 'not_run'},
        'cd_sampling': {'points_per_shape': samples, 'seed': 0,
                        'protocol': 'area-weighted-triangle-surface',
                        'relative_mesh_deflection': 0.001,
                        'definition': 'CD2=mean(d(A,B)^2)+mean(d(B,A)^2); CD=mean of both one-way mean distances'},
    }
    try:
        first, second = occupied_space(reference), occupied_space(candidate)
        vr, vc = properties(first)[0], properties(second)[0]
        report['raw'] = {
            'reference': {'volume_mm3': vr, 'bbox_mm': bbox(first), 'solid_count': solid_count(first)},
            'candidate': {'volume_mm3': vc, 'bbox_mm': bbox(second), 'solid_count': solid_count(second)},
        }
        relative_volume = abs(vc-vr)/vr
        report['volume_relative_error'] = relative_volume
        report['checks']['volume_relative_error'] = metric_check('volume_relative_error', relative_volume)
        normalized_reference, conditioning, _ = normalize(first)
        normalized_candidate = transform_shape(second, np.array(conditioning['matrix']))
        scale = conditioning['scale_to_normalized']
        # Detect unreliable integration under the known shared transform.
        actual = properties(normalized_candidate)[0]
        if abs(actual-vc*scale**3)/(vc*scale**3) > 1e-6:
            raise ValueError('Candidate volume changed under shared numerical conditioning')
        measured = _pose_metrics(normalized_reference, normalized_candidate, samples, diagnostics=False)
        report['measurement'] = measured
        report['comparison_frame'] = conditioning
        report['checks'] = dict(measured['checks'])
        # Source-unit volume is independently measured, never inferred from CD.
        report['checks']['volume_relative_error'] = metric_check('volume_relative_error', relative_volume)
        report['metric_errors'].extend(measured['metric_errors'])
        report['warnings'].extend(measured['warnings'])
        report['diagnostics'].extend(measured['diagnostics'])
        report.update(reference_volume_mm3=vr, candidate_volume_mm3=vc,
                      volume_relative_error=relative_volume,
                      volume_iou=measured['volume_iou'],
                      chamfer_relative=measured['chamfer_relative'])
        for key, factor in [('chamfer_mean_mm', scale), ('chamfer_distance_mm2', scale**2),
                            ('symmetric_difference_volume_mm3', scale**3)]:
            value = measured.get(key)
            report[key] = value / factor if value is not None else None
        report['coordinate_check'] = {'status': 'resolved', 'method': 'fixed_source_frame',
                                   'candidate_to_reference': np.eye(4).tolist()}
    except Exception as exc:
        report['metric_errors'].append({'metric': 'shape_evaluation', 'required': True,
                                       'error': f'{type(exc).__name__}: {exc}'})
    report['decision'] = decide(report)
    return report

