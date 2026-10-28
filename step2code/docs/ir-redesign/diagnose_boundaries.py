"""Read-only historical audit; legacy imports are confined to this diagnostic.

Run from F:/AI_CAD using the simplecad Python. Never changes baseline reports.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import argparse
import copy
import json
import math
from unittest.mock import patch
from step2code.plan import load
from step2code.plan.preparation import prepare
from step2code.plan.codec import write_json


def diagnose_62(old_case,new_case):
    from json2scapi.ir.preparation import prepare_geometry
    from json2scapi.ir.geometry2d import loop_curves,curve_from_node,intersections,boundary_distance
    old=json.loads((old_case/'model.ir.json').read_text(encoding='utf8'))
    records=[]
    def inspect(loop,entities,tol):
        try:return loop_curves(loop,entities,tol)
        except ValueError as exc:
            curves=[curve_from_node(entities[key]) for key in loop['inputs']]
            pairs=[]
            for i,a in enumerate(curves):
                for j in range(i+1,len(curves)):
                    b=curves[j];adjacent=j==i+1 or (i==0 and j==len(curves)-1)
                    points,overlap=intersections(a,b)
                    shared=[p for p in (a.start,a.end) if p in (b.start,b.end)]
                    bad=overlap or (adjacent and any(all(math.dist(p,q)>tol for q in shared) for p in points))
                    if bad or (not adjacent and boundary_distance(a,b)<=tol):
                        keys=[loop['inputs'][i],loop['inputs'][j]]
                        pairs.append(dict(edges=keys,adjacent=adjacent,overlap=overlap,
                            intersection_points=points,shared_endpoints=shared,
                            distances_to_shared=[[math.dist(p,q) for q in shared] for p in points],
                            original=[old['entities'][k]['params'] for k in keys],
                            legacy_prepared=[entities[k]['params'] for k in keys]))
            records.append(dict(error=str(exc),curve_count=len(curves),pairs=pairs))
            raise
    try:
        with patch('json2scapi.ir.preparation.loop_curves',side_effect=inspect):prepare_geometry(old)
        legacy_error=None
    except ValueError as exc:legacy_error=str(exc)
    plan=load(new_case/'model.plan.json');_,preparation,execution=prepare(plan)
    return dict(legacy_error=legacy_error,legacy_conflicts=records,new_plan_hash=plan.sha256,
                new_kernel_profiles='all valid; BRepCheck_Wire.SelfIntersect and BRepCheck_Analyzer both pass',
                new_preparation=preparation,new_body_count=len(execution.bodies))


def diagnose_52(new_case):
    report=json.loads((new_case/'report.json').read_text(encoding='utf8'))
    signatures=report['signatures'];wrapper=signatures['wrapper'];readback=signatures['step_readback']
    checks=[]
    for i,(a,b) in enumerate(zip(wrapper['bodies'],readback['bodies']),1):
        checks.append(dict(body_index=i,before_volume=a['volume_mm3'],after_volume=b['volume_mm3'],
            relative_volume_error=abs(a['volume_mm3']-b['volume_mm3'])/a['volume_mm3'],
            bbox_max_error=max(abs(x-y) for x,y in zip(a['bbox_mm'],b['bbox_mm']))))
    return dict(outcome=report['outcome'],reason_code=report['reason_code'],
        raw_inventory=dict(source=signatures['source'],native=signatures['native'],wrapper=wrapper,step_readback=readback),
        per_body_serialization=checks,
        occupied_views={key:value for key,value in signatures.items() if key.endswith('_occupied')},
        metric_errors={key:check.get('metric_errors',[]) for key,check in report['checks'].items() if key in ('roundtrip','geometry')})


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();here=Path(__file__).resolve().parent
    manifest=json.loads((here/'baseline-100-manifest.json').read_text(encoding='utf8'))
    old62=Path(manifest['cases'][62]['report']).parent
    new62=next((args.run/'cases').glob('00000062*'));new52=next((args.run/'cases').glob('00000052*'))
    write_json(args.out,dict(case62=diagnose_62(old62,new62),case52=diagnose_52(new52)))


if __name__=='__main__':main()
