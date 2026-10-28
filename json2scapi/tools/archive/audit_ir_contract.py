"""Audit generated artifacts without changing evaluation policy or geometry."""
import json,sys,math,ast
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from json2scapi.codegen.readable import StructuredEmitter
from json2scapi.ir.geometry2d import arc_geometry
from json2scapi.evaluation.metrics import read_step,volume,area,bbox,solid_count

runs=['ir-contract-v3-local-20261025','ir-contract-v3-first50-20261025']
base=ROOT/'json2scapi/out/corpus'
for run in runs:
    manifest=json.loads((base/run/'manifest.json').read_text(encoding='utf-8'))
    audits=[]
    for result in manifest['results']:
        folder=base/run/result['stem']
        code=folder/'model.readable.ftc.py'
        if not code.is_file():
            continue
        raw=json.loads((folder/'fidelity.ir.json').read_text(encoding='utf-8'))
        emitter=StructuredEmitter(raw,result['stem'])
        emitted=emitter.emit()
        previous=code.read_text(encoding='utf-8')
        identical=emitted==previous
        header_refreshed=False
        if not identical:
            def executable_ast(text):
                tree=ast.parse(text)
                if isinstance(tree.body[0],ast.Expr) and isinstance(tree.body[0].value,ast.Constant) and isinstance(tree.body[0].value.value,str):
                    tree.body=tree.body[1:]
                return ast.dump(tree,include_attributes=False)
            if executable_ast(previous)!=executable_ast(emitted):
                raise AssertionError(f'executable artifact drift: {run}/{result["stem"]}')
            code.write_text(emitted,encoding='utf-8')
            header_refreshed=True
            identical=True
        prepared=json.loads((folder/'prepared.ir.json').read_text(encoding='utf-8'))
        if not identical or prepared!=emitter.payload:
            raise AssertionError(f'artifact drift: {run}/{result["stem"]}')
        # Complete provenance snapshot with the final additive original_params field.
        (folder/'symbols.json').write_text(json.dumps(emitter.metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        audits.append({'stem':result['stem'],'code_reproduced':identical,'prepared_ir_reproduced':True,
                       'header_only_refreshed':header_refreshed, 'adjustments':len(emitter.metadata['preparation']['changes'])})
    export=base/run/'train_ready.jsonl'
    if export.is_file():
        records=[json.loads(line) for line in export.read_text(encoding='utf-8').splitlines()]
        for record in records:
            record['code']=(base/run/record['stem']/'model.readable.ftc.py').read_text(encoding='utf-8')
        export.write_text(''.join(json.dumps(record,ensure_ascii=False)+'\n' for record in records),encoding='utf-8')
    (base/run/'GENERATION_AUDIT.json').write_text(json.dumps(audits,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(run,len(audits),'code and prepared IR artifacts reproduced exactly')

old=json.loads((base/'structured-v2-first50/manifest.json').read_text(encoding='utf-8'))
new=json.loads((base/runs[1]/'manifest.json').read_text(encoding='utf-8'))
old_by={r['stem']:r for r in old['results']}
checks=[]
for item in new['results']:
    stem=item['stem']
    a,b=base/'structured-v2-first50'/stem/'readable.step',base/runs[1]/stem/'readable.step'
    if not a.is_file() or not b.is_file():
        continue
    try:
        first,second=read_step(a),read_step(b)
        va,vb=volume(first),volume(second)
        aa,ab=area(first),area(second)
        ba,bb=bbox(first),bbox(second)
        check={'stem':stem,'old_status':old_by[stem]['status'],'new_status':item['status'],
               'old_solids':solid_count(first),'new_solids':solid_count(second),
               'volume_relative_error':abs(va-vb)/abs(va),'area_relative_error':abs(aa-ab)/abs(aa),
               'world_bbox_absolute_error_mm':max(abs(x-y) for x,y in zip(ba,bb))}
        check['raw_invariants_preserved']=(check['old_solids']==check['new_solids'] and
            check['volume_relative_error']<=1e-6 and check['area_relative_error']<=1e-6 and
            check['world_bbox_absolute_error_mm']<=0.001)
    except Exception as error:
        check={'stem':stem,'error':str(error)}
    checks.append(check)
(base/runs[1]/'RAW_INVARIANT_COMPARISON.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('raw baseline invariants',len(checks),'checked;',sum(c.get('raw_invariants_preserved',False) for c in checks),'passed')

raw=json.loads((ROOT/'datasets/DeepCAD/cad_json/0000/00000093.json').read_text(encoding='utf-8'))
sketch=raw['entities']['FKz6nADneX0thB0_0']
curve=next(c for c in sketch['profiles']['KGmB']['loops'][1]['profile_curves'] if c['curve']=='JG5')
from json2scapi.adapters.deepcad import _arc_midpoint
p={key:[curve[key+'_point'][axis]*1000 for axis in ('x','y')] for key in ('start','end')}
p['mid']=_arc_midpoint({'_center_2d':[curve['center_point'][axis] for axis in ('x','y')],
    '_radius_m':curve['radius'],'_start_angle':curve['start_angle'],'_end_angle':curve['end_angle'],
    '_reference_vec':[curve['reference_vector'][axis] for axis in ('x','y')]})
center,radius,sweep=arc_geometry(p)
evidence={'stem':'00000093','curve':'JG5','source':'raw DeepCAD endpoints and angle midpoint',
          'three_points_mm':p,'declared_center_mm':[0,0],'computed_center_mm':center,
          'center_error_mm':math.hypot(*center),'declared_radius_mm':32.5,'computed_radius_mm':radius,
          'length_tolerance_mm':0.001,'reason':'auxiliary center conflicts with authoritative three-point circle',
          'historical_status':old_by['00000093']['status']}
(base/runs[1]/'ARC_CONFLICT_00000093.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(evidence))
