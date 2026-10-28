"""Audit completed paired runs and save compact, reviewable evidence."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import statistics

from step2code_direct.cli import ROOT
from step2code_direct.pipeline import write_json


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args(argv)
    summaries={m:json.loads((args.run/m/'summary.json').read_text(encoding='utf-8'))
               for m in ('lookahead','csg-first')}
    if any(s['run_status']!='finished' for s in summaries.values()):
        raise ValueError('Both arms must finish before the audit')
    runs={}
    for mode,summary in summaries.items():
        rows=[]
        for case in summary['cases']:
            report=json.loads(Path(case['report']).read_text(encoding='utf-8'))
            h=report.get('hybrid_search',{})
            rows.append(dict(id=Path(case['input']).stem[:8],input=case['input'],
                input_sha256=case['input_sha256'],status=case['status'],reason=case['reason'],
                worker_wall_seconds=case['worker_wall_seconds'],report=case['report'],
                stop_reason=h.get('stop_reason'),best_symmetric_difference_relative=h.get('best_symmetric_difference_relative'),
                boolean_trials=h.get('boolean_trials'),exact_score_calls=h.get('exact_score_calls'),
                last_search_route=h.get('solution_route'),csg_phases=h.get('csg_phases'),
                source_solid_count=report.get('recognition',{}).get('source_solid_count'),
                reconstructed_solid_count=len(report.get('recognition',{}).get('features',[])),
                chamfer_relative=case.get('chamfer_relative'),volume_iou=case.get('volume_iou'),
                volume_relative_error=case.get('volume_relative_error'),
                implementation=report.get('implementation_sha256'),runtime=report.get('runtime_versions'),
                source_matches_disk=hashlib.sha256(Path(case['input']).read_bytes()).hexdigest()==case['input_sha256'],
                feature_routes=[f.get('solution_route') for f in report.get('recognition',{}).get('features',[]) if f.get('solution_route')]))
        runs[mode]=rows
    keys=lambda rows:[(Path(r['input']).name,r['input_sha256']) for r in rows]
    configurations={m:deepcopy(s['configuration']) for m,s in summaries.items()}
    for conf in configurations.values():
        conf['recognition'].pop('hybrid_search')
    expected=summaries['lookahead']['implementation_sha256']
    checks=dict(all_inputs_match_disk=all(r['source_matches_disk'] for rows in runs.values() for r in rows),
        same_input_order_and_hashes=keys(runs['lookahead'])==keys(runs['csg-first']),
        same_configuration_except_search=configurations['lookahead']==configurations['csg-first'],
        same_worker_hard_timeout=summaries['lookahead']['timeout_seconds']==summaries['csg-first']['timeout_seconds'],
        same_source_fingerprints=all(r['implementation']==expected for rows in runs.values() for r in rows),
        same_runtime=len({json.dumps(r['runtime'],sort_keys=True) for rows in runs.values() for r in rows})==1,
        source_still_matches_run=all((ROOT/name).is_file() and hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest
                                    for name,digest in expected.items()),
        no_worker_crashes=all(r['reason']!='worker_crash' for rows in runs.values() for r in rows))
    aggregates={}
    for mode,rows in runs.items():
        times=[r['worker_wall_seconds'] for r in rows]
        groups={}
        for name,ids in [('first20',set(range(20))),('residual_targets',{29,71,78,80}),('import_regression',{83,99})]:
            subset=[r for r in rows if int(r['id']) in ids]
            groups[name]=dict(count=len(subset),statuses=dict(Counter(r['status'] for r in subset)),
                              accepted_ids=[r['id'] for r in subset if r['status']=='train_ready'])
        aggregates[mode]=dict(count=len(rows),statuses=dict(Counter(r['status'] for r in rows)),
            reasons=dict(Counter(r['reason'] for r in rows)),stop_reasons=dict(Counter(r['stop_reason'] for r in rows if r['stop_reason'])),
            accepted_ids=[r['id'] for r in rows if r['status']=='train_ready'],groups=groups,
            worker_sum_seconds=sum(times),worker_median_seconds=statistics.median(times))
    before={r['id'] for r in runs['lookahead'] if r['status']=='train_ready'}
    after={r['id'] for r in runs['csg-first'] if r['status']=='train_ready'}
    for rows in runs.values():
        for row in rows:
            row.pop('implementation');row.pop('runtime')
    payload=dict(version='0.5.0',baseline_snapshot='d740a3f',selection=summaries['lookahead']['selection'],
        checks=checks,all_checks_pass=all(checks.values()),aggregates=aggregates,
        newly_accepted=sorted(after-before),lost_acceptances=sorted(before-after),
        source_fingerprints=expected,cases=runs,
        limits=['26 development and targeted inputs, not the first 100 or a random sample.',
                'Single native calls can exceed soft deadlines; the hard worker cap still applies.',
                'Elapsed times are descriptive, not a controlled speedup claim.',
                'Compatibility modules come from committed source, not historical uncommitted 100-case files.'])
    write_json(args.out,payload)
    print(json.dumps(dict(checks=checks,aggregates=aggregates,newly_accepted=payload['newly_accepted'],
                          lost_acceptances=payload['lost_acceptances']),ensure_ascii=False,indent=2))
    return 0 if payload['all_checks_pass'] else 1


if __name__=='__main__':
    raise SystemExit(main())
