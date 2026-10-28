"""Source-solid selection, typed plan composition and independent per-body checkpoints."""
from pathlib import Path
import numpy as np
from OCP.TopAbs import TopAbs_SOLID, TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from step2code.plan import Plan, empty_document, node
from step2code.plan.graph import reachable
from step2code.plan.passes import flatten_unions
from step2code.verification.metrics import bbox
from .prism import RecognitionOptions, UnsupportedGeometry, explore, _recognize_solid
from .revolution import recognize_revolution
from .csg import recognize_csg
from .context import context, native_call


def expose_body(nodes,root,index,solid):
    """Explicit independent whole-body size and placement controls, no design-history claim."""
    doc=empty_document();prefix=f'body_{index:03d}'
    scale_id=prefix+'_scale';frame_id=prefix+'_placement';out_id=prefix+'_world'
    bounds=np.array(bbox(solid));center=((bounds[:3]+bounds[3:])/2).tolist()
    nodes[scale_id]=node('shape.uniform_scale',dict(shape=[root]),factor=1.,center=center)
    nodes[frame_id]=node('frame.rigid3',origin=[0.,0.,0.],x_axis=[1.,0.,0.],y_axis=[0.,1.,0.])
    nodes[out_id]=node('shape.transform',dict(shape=[scale_id],frame=[frame_id]))
    for key,names in ((scale_id,['factor']),(frame_id,['origin','x_axis','y_axis'])):
        for name in names:
            pid=prefix+'_'+name
            spec=dict(type='number' if name=='factor' else ('point3' if name=='origin' else 'direction3'),
                unit='mm' if name=='origin' else '1',value=nodes[key]['args'][name]['literal'],editability='independent')
            doc['parameters'][pid]=spec;nodes[key]['args'][name]=dict(param=pid)
    doc['nodes']=flatten_unions(nodes,[out_id])
    doc['outputs']=[dict(id=prefix,node=out_id,expected_kind='solid')]
    return Plan.from_dict(doc)


def recognize(shape, source: Path, options=None, *, events=None, checkpoint=None, restored=None):
    options=options or RecognitionOptions();restored=restored or {}
    solids=[TopoDS.Solid_s(s) for s in explore(shape,TopAbs_SOLID)]
    if not solids:raise UnsupportedGeometry('STEP contains no closed solids',dict(reason_code='domain.no_solids'))
    if len(solids)>options.max_solids:
        raise UnsupportedGeometry('Source solid count exceeds limit',dict(reason_code='limit.source_bodies',actual=len(solids),limit=options.max_solids))
    for kind,avoid,label in ((TopAbs_FACE,TopAbs_SOLID,'faces'),(TopAbs_EDGE,TopAbs_FACE,'edges'),(TopAbs_VERTEX,TopAbs_EDGE,'vertices')):
        if TopExp_Explorer(shape,kind,avoid).More():
            raise UnsupportedGeometry('Loose source '+label,dict(reason_code='domain.loose_'+label))
    current=dict(sources={},events=events,body_id=None,csg_deadline=None)
    token=context.set(current);doc=empty_document();features=[];body_checks=[]
    try:
        for i,solid in enumerate(solids,1):
            current.update(body_id=f'body_{i:03d}',csg_deadline=None)
            if events:events(event='start',phase='recognition.body',source_body_id=current['body_id'])
            if i in restored:
                plan,ev=restored[i]
            else:
                failures={};result=None
                for method,name in ((_recognize_solid,'prism'),(recognize_revolution,'revolution'),(recognize_csg,'csg')):
                    try:
                        with native_call('strategy.'+name):
                            result=method(solid,i,options)
                        break
                    except UnsupportedGeometry as exc:
                        failures[name]=exc.diagnostics
                if result is None:
                    csg=failures.get('csg',{})
                    details=dict(solid_index=i,recognized_solid_count=len(features),
                        recognized_solids=[dict(solid_index=f['solid_index'],method=f['method']) for f in features],
                        coverage_status='partial' if features else 'none',**failures,
                        reason_code=csg.get('reason_code','search.no_verified_candidate'))
                    raise UnsupportedGeometry(f'No verified reconstruction for body {i}',details)
                nodes,roots,ev=result
                plan=expose_body(nodes,roots[-1],i,solid)
                ev['prior_attempts']=failures
            # Every checkpoint is the actual new plan replay, independently source-verified.
            from step2code.plan.preparation import prepare
            from step2code.verification.metrics import symmetric_difference_volume,volume
            _,prep,replay=prepare(plan,events=events)
            error=symmetric_difference_volume(solid,replay.bodies[0])/volume(solid)
            volume_error=abs(volume(solid)-volume(replay.bodies[0]))/volume(solid)
            if not np.isfinite(error) or error>options.reconstruction_relative_tolerance or volume_error>options.reconstruction_relative_tolerance:
                from step2code.plan import PlanError
                raise PlanError('recognition.replay_mismatch','/outputs/0','Typed-plan replay differs from source',dict(relative_difference=error,volume_relative_error=volume_error,body_index=i))
            ev['replay_check']=dict(symmetric_difference_relative=error,plan_hash=plan.sha256)
            ev['node_sources']=ev.get('node_sources',{key:value for key,value in current['sources'].items() if key in plan.data['nodes']})
            if checkpoint:checkpoint(i,plan,ev,prep)
            part=plan.data;doc['nodes'].update(part['nodes']);doc['parameters'].update(part['parameters']);doc['outputs'].extend(part['outputs'])
            features.append(ev);body_checks.append(ev['replay_check'])
            if events:events(event='end',phase='recognition.body',source_body_id=current['body_id'],plan_hash=plan.sha256)
        plan=Plan.from_dict(doc)
        sources={key:value for feature in features for key,value in feature.get('node_sources',{}).items() if key in doc['nodes']}
        return plan,dict(recognizer='exact-analytic-dag-1',features=features,source_solid_count=len(solids),
            output_map=[dict(body_id=o['id'],source_solid_index=i,output_node=o['node'],source_face_indices=features[i-1]['source_face_indices'])
                        for i,o in enumerate(doc['outputs'],1)],
            node_sources=sources,history_recovered=False)
    finally:
        context.reset(token)
