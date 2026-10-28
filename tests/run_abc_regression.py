"""Compare a new-format run against the immutable, content-bound 100-case baseline.

This audit reads legacy reports only as historical evidence. It is never imported
by conversion, plan loading, native execution or compilation.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from step2code.batch.identity import file_hash,implementation_identity
from step2code.plan.codec import write_json


def compare(run,baseline):
    old=json.loads(baseline.read_text(encoding='utf8'));manifest=json.loads((run/'manifest.json').read_text(encoding='utf8'))
    selected={Path(item['path']).stem[:8]:item for item in manifest['inputs']}
    rows=[];changed=[];regressions=[];source_errors=[];baseline_errors=[]
    for item in old['cases']:
        case_id=item['id'];identity=selected.get(case_id)
        if not identity or identity['sha256']!=item['input_sha256'] or file_hash(item['input'])!=item['input_sha256']:
            source_errors.append(case_id)
        if file_hash(item['report'])!=item['report_sha256']:baseline_errors.append(case_id)
        report_path=run/'cases'/Path(item['input']).stem/'report.json'
        if not report_path.exists():
            source_errors.append(case_id+':missing_report');continue
        report=json.loads(report_path.read_text(encoding='utf8'))
        row=dict(id=case_id,previous=item['status'],outcome=report['outcome'],reason_code=report['reason_code'],
            source_sha256=item['input_sha256'],report_sha256=file_hash(report_path),
            plan_hash=report.get('plan_hash'),elapsed_seconds=report['elapsed_seconds'])
        if report['outcome']=='accepted':
            row['bodies']=report['recognition']['source_solid_count']
            row['methods']=dict(Counter(f['method'] for f in report['recognition']['features']))
            row['eligibility']=report['eligibility']
        rows.append(row)
        if item['status']=='train_ready' and report['outcome']!='accepted':regressions.append(row)
        if (item['status']=='train_ready')!=(report['outcome']=='accepted'):changed.append(row)
    accepted=[row for row in rows if row['outcome']=='accepted']
    reasons=dict(Counter(row['reason_code'] for row in rows))
    current=implementation_identity()
    return dict(schema='step-plan-refactor-regression-1',run=str(run.resolve()),
        run_state=manifest['run_state'],completed_count=manifest['completed_count'],selected_count=manifest['selected_count'],
        requested_count=manifest['requested_count'],configuration=manifest['configuration'],
        implementation_fingerprint=manifest['implementation']['fingerprint'],current_code_matches=current['fingerprint']==manifest['implementation']['fingerprint'],
        previous_accepted=28,accepted_count=len(accepted),accepted_bodies=sum(row.get('bodies',0) for row in accepted),
        counts=dict(Counter(row['outcome'] for row in rows)),reason_counts=reasons,
        source_identity_errors=source_errors,baseline_report_hash_errors=baseline_errors,
        all_previous_successes_retained=not regressions,regressions=regressions,acceptance_changes=changed,cases=rows)


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True)
    p.add_argument('--baseline',type=Path,default=Path(__file__).resolve().parents[1]/'docs/ir-redesign/baseline-100-manifest.json')
    p.add_argument('--out',type=Path,required=True);p.add_argument('--execute',action='store_true')
    args=p.parse_args()
    if args.execute:
        baseline=json.loads(args.baseline.read_text(encoding='utf8'))
        source=Path(baseline['cases'][0]['input']).parent
        subprocess.run([sys.executable,'-m','step2code','--input',str(source),'--limit','100','--out',str(args.run),
                        '--samples','8192','--timeout','120'],check=True)
    result=compare(args.run,args.baseline);write_json(args.out,result)
    print(json.dumps({key:value for key,value in result.items() if key not in ('cases','configuration')},ensure_ascii=False,indent=2))
    return 0 if (result['run_state']=='completed' and result['completed_count']==100
                 and not result['source_identity_errors'] and not result['baseline_report_hash_errors']
                 and result['all_previous_successes_retained'] and result['current_code_matches']) else 1


if __name__=='__main__':raise SystemExit(main())
