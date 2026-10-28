"""Build the P0.5 recognition case matrix from a completed regression audit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    audit = json.loads(args.audit.read_text(encoding='utf-8'))
    cases = []
    for item in audit.get('cases', []):
        cases.append({
            key: item[key]
            for key in (
                'id', 'previous', 'outcome', 'reason_code', 'source_sha256',
                'report_sha256', 'plan_hash', 'elapsed_seconds',
            )
            if key in item
        })
    result = {
        'format': 'step-recognition-case-matrix',
        'version': 1,
        'source_audit': str(args.audit),
        'run': audit.get('run'),
        'implementation_fingerprint': audit.get('implementation_fingerprint'),
        'configuration': audit.get('configuration'),
        'selected_count': audit.get('selected_count'),
        'completed_count': audit.get('completed_count'),
        'outcome_counts': audit.get('counts'),
        'reason_counts': audit.get('reason_counts'),
        'accepted_bodies': audit.get('accepted_bodies'),
        'regressions': audit.get('regressions', []),
        'all_previous_successes_retained': audit.get('all_previous_successes_retained'),
        'cases': cases,
        'sidecar_only': True,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
