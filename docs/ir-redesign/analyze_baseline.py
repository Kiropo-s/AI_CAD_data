"""Audit a retained STEP run without executing recognition or generated code.

Writes an immutable evidence snapshot and a compact case table beside this script.
Run from the repository root using the existing simplecad Python environment.
"""
from __future__ import annotations
from pathlib import Path
from collections import Counter
import csv, hashlib, json, math, statistics

ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
RUN=ROOT/'step2code/out/abc-first1000-v4-20261026-180058'

def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def counted(values):return dict(sorted(Counter(values).items(),key=lambda x:(-x[1],str(x[0]))))
def pct(values,q):
    if not values:return None
    a=sorted(values);p=(len(a)-1)*q;lo=math.floor(p);hi=math.ceil(p)
    return a[lo]+(a[hi]-a[lo])*(p-lo)

summary_path=RUN/'summary.json';summary=read(summary_path)
start_hash=digest(summary_path)
rows=[];methods=Counter();accepted_methods=Counter();families=Counter();formats=Counter();submissions=[];fieldcounts=Counter();special={};manifest=[];plan_problems=[]
for case in summary['cases']:
    report_path=Path(case['report']);r=read(report_path);name=Path(case['input']).name;ident=name[:8]
    f=r.get('recognition_failure',{});features=r.get('recognition',{}).get('features',[])
    method_list=[x['method'] for x in features]
    methods.update(method_list)
    if case['status']=='train_ready':
        accepted_methods.update(method_list)
        families['+'.join(sorted(set(method_list)))]+=1
    files={}
    for field in ('ir','prepared_ir','code','step','symbols'):
        entry=r.get('artifacts',{}).get(field)
        if entry:
            p=report_path.parent/entry
            files[field]={'path':str(p),'exists':p.exists(),'sha256':digest(p) if p.exists() else None,'bytes':p.stat().st_size if p.exists() else None}
            fieldcounts[f"{case['status']}:{field}"]+=1
    plan_info=None
    if 'ir' in files:
        plan=read(files['ir']['path']);fmt=plan.get('format') or f"cad-ir-{plan.get('ir_version')}";formats[fmt]+=1
        recipe_nodes=[];all_recipe_count=0;root_nodes=[]
        roots=[x['operation_id'] for x in features]
        for k,node in plan['entities'].items():
            if node['op']=='feature.csg':
                all_recipe_count+=1
                if k in roots:
                    recipe=node['params']['recipe'];root_nodes.extend(recipe['nodes'].values())
        plan_info={'format':fmt,'entity_count':len(plan['entities']),'operation_roots':roots,'results':plan['results'],
                   'csg_feature_count':all_recipe_count,'root_recipe_node_count':len(root_nodes),
                   'root_recipe_boolean_count':sum(len(n['inputs'])-1 for n in root_nodes if n['kind'] in ('union','cut','intersect')),
                   'root_recipe_node_kinds':counted(n['kind'] for n in root_nodes)}
        submissions.append(ident)
    reason=case['reason']
    csg=f.get('csg',{})
    if reason=='unsupported_geometry':
        if csg.get('reason')=='CSG fallback currently requires bounded analytic boundary surfaces':cat='csg_capability_or_face_limit_gate'
        elif csg:cat='csg_search_incomplete'
        else:cat='outside_recognition_domain'
    else:cat=reason
    elapsed=r.get('elapsed_seconds')
    source_path=Path(case['input']);actual_hash=digest(source_path)
    row={'id':ident,'input':str(source_path),'input_sha256':r.get('input_sha256'),'current_input_sha256':actual_hash,
         'source_hash_matches':actual_hash==r.get('input_sha256'),'document_token':name.split('_')[1],
         'status':case['status'],'reason':reason,'analysis_category':cat,'report':str(report_path),
         'report_sha256':digest(report_path),'summary_status_matches_report':case['status']==r['status'],
         'stage':r['stage'],'elapsed_seconds':elapsed,'error':r.get('error'),
         'configuration':r.get('configuration'),'runtime_versions':r.get('runtime_versions'),
         'implementation_fingerprint_available':bool(r.get('implementation_sha256')),
         'methods':method_list,'recognized_body_count_complete_report':len(features),
         'recognized_body_count_before_failure':f.get('recognized_solid_count',0),
         'failure_solid_index':f.get('solid_index'),'coverage_status':f.get('coverage_status'),
         'source_solid_count_complete_report':r.get('recognition',{}).get('solid_count'),
         'parameter_edit_status':r.get('parameter_edit_validation',{}).get('status'),
         'history_recovered':r.get('history_recovered'),'assembly_status':r.get('assembly_validation',{}).get('status'),
         'structure':r.get('structure'),'plan':plan_info,'files':files,
         'csg_diagnostic_reason':csg.get('reason'),'csg_partition':csg.get('partition'),
         'decisions':{key:r.get(key,{}).get('decision') for key in ('source_coverage_validation','roundtrip','geometry')},
         'geometry_metrics':{key:r.get('geometry',{}).get(key) for key in ('chamfer_relative','volume_iou','volume_relative_error')},
         'roundtrip_raw':r.get('roundtrip',{}).get('raw'),'geometry_raw':r.get('geometry',{}).get('raw'),
         'metric_errors':{key:r.get(key,{}).get('metric_errors') for key in ('roundtrip','geometry')}}
    rows.append(row)
    if case['status']=='rejected' or reason not in ('accepted','unsupported_geometry'):
        special[ident]={'report':str(report_path),'error':r.get('error'),'stage':r.get('stage'),'traceback':r.get('traceback'),
                       'recognition_failure':f,'decisions':row['decisions'],'roundtrip_raw':row['roundtrip_raw'],'geometry_raw':row['geometry_raw'],
                       'metrics':{phase:{k:r.get(phase,{}).get(k) for k in ('volume_relative_error','chamfer_relative','volume_iou','checks','metric_errors')} for phase in ('roundtrip','geometry')}}
    manifest.append({'id':ident,'input':str(source_path),'input_sha256':actual_hash,'report':str(report_path),'report_sha256':row['report_sha256'],'status':row['status'],'artifacts':files})
assert digest(summary_path)==start_hash,'Source summary changed during audit; rerun against a stable snapshot'
assert len(rows)==100 and len({r['id'] for r in rows})==100
assert all(r['source_hash_matches'] and r['summary_status_matches_report'] for r in rows)
accepted=[r for r in rows if r['status']=='train_ready'];durations=[r['elapsed_seconds'] for r in rows if r['elapsed_seconds'] is not None]
configs={json.dumps(r['configuration'],sort_keys=True) for r in rows};runtimes={json.dumps(r['runtime_versions'],sort_keys=True) for r in rows}
result={'date':'2026-10-26','source_summary':str(summary_path),'source_summary_sha256':start_hash,
        'source_run_name':RUN.name,'recorded_case_count':len(rows),'id_range':[rows[0]['id'],rows[-1]['id']],
        'run_log_requested_count':1000,'run_completion_evidence':'100 rows retained; log ends [100/1000]; no final Completed line or summary.csv; no claim that 1000 completed',
        'status_counts':counted(r['status'] for r in rows),'reason_counts':counted(r['reason'] for r in rows),
        'analysis_categories':counted(r['analysis_category'] for r in rows),
        'document_token_group_count':len({r['document_token'] for r in rows}),
        'largest_document_groups':Counter(r['document_token'] for r in rows).most_common(6),
        'accepted_ids':[r['id'] for r in accepted],
        'accepted_families':dict(families),'all_completed_recognition_body_methods':dict(methods),
        'accepted_body_methods':dict(accepted_methods),'accepted_body_count':sum(accepted_methods.values()),
        'plan_format_counts_all_written':dict(formats),'artifact_counts':dict(fieldcounts),
        'partial_recognition_case_ids':[r['id'] for r in rows if r['recognized_body_count_before_failure']>0],
        'partial_recognition_known_body_count':sum(r['recognized_body_count_before_failure'] for r in rows),
        'parameter_edit_counts':counted(r['parameter_edit_status'] for r in rows),
        'elapsed_seconds':{'sum':sum(durations),'median':statistics.median(durations),'p95':pct(durations,.95),'max':max(durations)},
        'unique_configuration_count':len(configs),'unique_runtime_count':len(runtimes),
        'recorded_implementation_fingerprint_count':sum(r['implementation_fingerprint_available'] for r in rows),
        'structure_accepted':{k:{'min':min(r['structure'][k] for r in accepted),'median':statistics.median(r['structure'][k] for r in accepted),'max':max(r['structure'][k] for r in accepted)} for k in ('lines','function_count','max_function_lines','independent_parameter_count')},
        'special_cases':special,'cases':rows,
        'limits':['Ordered prefix, 35 filename document-token groups; grouping is inferred from filename and is not a recovered source assembly tree.',
                  'No converter reruns. Source hashes and retained report/artifact hashes checked, but historical code was not fingerprinted in these reports.',
                  'CSG gate combines unsupported surface types and face-count limits; logs cannot separate them reliably.',
                  'Worker timeouts have only last checkpoint; cannot infer exact active native call.',
                  '00000052 observations do not isolate exporter, assembly wrapping, import, occupied-space union, or measurement implementation.',
                  '00000062 preparation rejection does not prove a false positive; ordered curve/arc representation needs a reproducer.',
                  'All 100 parameter_edit_validation statuses are not_run; geometry acceptance is not parameter-design acceptance.']}
(OUT/'baseline-100-summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
(OUT/'baseline-100-manifest.json').write_text(json.dumps({'schema':'step-refactor-baseline-1','summary_sha256':start_hash,'cases':manifest},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
(OUT/'baseline-100-original-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
fields=['id','status','reason','analysis_category','document_token','elapsed_seconds','source_hash_matches','recognized_body_count_complete_report','recognized_body_count_before_failure','failure_solid_index','parameter_edit_status','error','report']
with (OUT/'baseline-100-cases.csv').open('w',encoding='utf-8-sig',newline='') as stream:
    writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
print(json.dumps({k:v for k,v in result.items() if k not in ('cases','special_cases','limits')},ensure_ascii=False,indent=2))
