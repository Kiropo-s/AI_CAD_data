"""Bounded real ABC regression, including finite per-case edit smoke evidence.

Run from the repository root with the existing CAD Python:
  python -m step2code.tests.run_abc_regression --input datasets/abc/step/abc_0000 --out step2code/out/abc-regression
"""
from __future__ import annotations

import argparse
import json
import math
import runpy
import subprocess
import sys
from collections import Counter
from pathlib import Path

from step2code.pipeline import write_json


def edit_smoke(case):
    """Test a distance and an origin from one instance; never claim full edit coverage."""
    from OCP.BRepCheck import BRepCheck_Analyzer
    from json2scapi.evaluation.metrics import bbox, volume
    from json2scapi.runtime.worker import returned_shapes
    symbols=json.loads((case/'model.symbols.json').read_text(encoding='utf-8'))
    loaded=runpy.run_path(str(case/'model.scapi.py'))
    # runpy returns a copy; mutate the globals actually used by build().
    namespace=loaded['build'].__globals__
    baseline=returned_shapes(namespace['build']())
    instance=symbols['instances'][0]
    bindings=instance['bindings']; body_index=instance['output_body_index']
    results=[]
    dimension = 'distance' if 'distance' in bindings else 'profile_scale'
    for argument in (dimension,'origin'):
        name=bindings[argument];original=namespace[name]
        edited=original*1.1 if argument in ('distance','profile_scale') else [v+d for v,d in zip(original,(3.,-4.,8.))]
        namespace[name]=edited
        try:
            bodies=returned_shapes(namespace['build']())
            valid=(len(bodies)==len(baseline) and all(BRepCheck_Analyzer(s).IsValid() and
                    math.isfinite(volume(s)) and volume(s)>0 for s in bodies))
            ratios=[volume(after)/volume(before) for before,after in zip(baseline,bodies)]
            expected=[(1.1 if argument=='distance' else 1.1**3)
                      if argument in ('distance','profile_scale') and i==body_index else 1.
                      for i in range(len(baseline))]
            volume_ok=len(ratios)==len(expected) and all(math.isclose(a,b,rel_tol=1e-7,abs_tol=1e-7)
                                                                       for a,b in zip(ratios,expected))
            other_bodies_unchanged=all(all(math.isclose(x,y,rel_tol=1e-10,abs_tol=1e-6)
                for x,y in zip(bbox(before),bbox(after)))
                for i,(before,after) in enumerate(zip(baseline,bodies)) if i!=body_index)
            placement_ok=True
            if argument=='origin' and len(bodies)==len(baseline):
                displacement=[a-b for a,b in zip(bbox(bodies[body_index]),bbox(baseline[body_index]))]
                placement_ok=all(math.isclose(a,b,abs_tol=1e-6) for a,b in zip(displacement,(3,-4,8,3,-4,8)))
            results.append(dict(parameter=name, original_value=original, test_value=edited,
                actual_volume_ratios=ratios, expected_volume_ratios=expected,
                topology_valid=valid, other_bodies_unchanged=other_bodies_unchanged,
                placement_matches=placement_ok,
                status='pass' if valid and volume_ok and placement_ok and other_bodies_unchanged else 'fail'))
        except Exception as exc:
            results.append(dict(parameter=name,original_value=original,test_value=edited,
                                status='fail',error=f'{type(exc).__name__}: {exc}'))
        finally:
            namespace[name]=original
    passed=all(r['status']=='pass' for r in results)
    result=dict(status='partial' if passed else 'fail', tested_parameter_count=len(results),
                public_parameter_count=len(symbols['parameters']),
                scope=f'first instance {dimension} x1.1 and origin +(3,-4,8) mm; other parameters not tested',
                all_recorded_checks_passed=passed, tests=results)
    report=json.loads((case/'report.json').read_text(encoding='utf-8'))
    report['parameter_edit_validation']=result
    write_json(case/'report.json',report)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--limit',type=int,default=20)
    parser.add_argument('--timeout',type=float,default=120)
    parser.add_argument('--samples',type=int,default=8192)
    parser.add_argument('--baseline',type=Path)
    parser.add_argument('--_edit-worker',type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if args._edit_worker:
        return 0 if edit_smoke(args._edit_worker)['all_recorded_checks_passed'] else 1
    if args.input is None or args.out is None:
        parser.error('--input and --out are required')
    from step2code.cli import main as convert
    convert(['--input',str(args.input),'--out',str(args.out),'--limit',str(args.limit),
             '--samples',str(args.samples),'--timeout',str(args.timeout)])
    root=args.out.resolve()
    summary=json.loads((root/'summary.json').read_text(encoding='utf-8'))
    baseline={}
    if args.baseline:
        previous=json.loads(args.baseline.read_text(encoding='utf-8'))
        baseline={Path(row['input']).name:row for row in previous['cases']}
    regressions=[]; cases=[]
    for row in summary['cases']:
        case=Path(row['report']).parent
        report=json.loads((case/'report.json').read_text(encoding='utf-8'))
        name=Path(row['input']).name
        prior=baseline.get(name)
        comparison='not_compared'
        if prior:
            old_report=json.loads(Path(prior['report']).read_text(encoding='utf-8'))
            if old_report.get('input_sha256') != report.get('input_sha256'):
                comparison='source_hash_mismatch';regressions.append(name+': input hash differs')
            elif prior['status']=='train_ready' and row['status']!='train_ready':
                comparison='regression';regressions.append(name+': lost train_ready')
            else:
                comparison='same_status' if prior['status']==row['status'] else 'changed_status'
        if row['status']=='rejected': regressions.append(name+': rejected')
        if row.get('reason')=='worker_error': regressions.append(name+': worker error or timeout')
        edit_status='not_run'
        if row['status']=='train_ready':
            with (case/'edit-worker.log').open('w',encoding='utf-8') as log:
                try:
                    child=subprocess.run([sys.executable,'-m','step2code.tests.run_abc_regression',
                        '--_edit-worker',str(case)],timeout=args.timeout,
                        stdout=log,stderr=subprocess.STDOUT,cwd=Path(__file__).resolve().parents[2])
                    if child.returncode:
                        regressions.append(name+': edit worker failed')
                except (OSError,subprocess.TimeoutExpired) as exc:
                    regressions.append(name+': edit worker '+str(exc))
            report=json.loads((case/'report.json').read_text(encoding='utf-8'))
            edit_status=report['parameter_edit_validation']['status']
            if edit_status not in ('partial','pass'):
                regressions.append(name+': no successful edit evidence')
            row['parameter_edit_status']=edit_status
        cases.append(dict(input=row['input'],status=row['status'],baseline_status=prior['status'] if prior else None,
                          baseline_comparison=comparison,parameter_edit_status=edit_status,
                          geometry_validated=report.get('geometry_validated'),structure=report.get('structure'),
                          report=row['report']))
    if not any(row['status']=='train_ready' for row in summary['cases']):
        regressions.append('No supported real case completed')
    write_json(root/'summary.json',summary)
    # Keep both summaries consistent after adding independently measured edit evidence.
    import csv
    with (root/'summary.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(summary['cases'][0]));writer.writeheader();writer.writerows(summary['cases'])
    result=dict(status='pass' if not regressions else 'fail',input_directory=str(args.input.resolve()),
                sample_selection='first N STEP files sorted by filename; no recursive search',
                samples=args.samples,validation_mode='world',source_count=len(cases),
                counts=dict(Counter(c['status'] for c in cases)),baseline=str(args.baseline) if args.baseline else None,
                regressions=regressions,cases=cases)
    write_json(root/'regression.json',result)
    print('Regression:',result['status'],result['counts'],flush=True)
    return 0 if result['status']=='pass' else 1


if __name__=='__main__': raise SystemExit(main())
