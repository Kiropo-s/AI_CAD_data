"""Bounded, source-verified CSG over analytic tools and measured cap prisms."""
from __future__ import annotations
import math
import time
import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepLib import BRepLib
from OCP.BRepPrimAPI import (BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder,
                            BRepPrimAPI_MakeCone, BRepPrimAPI_MakeSphere,
                            BRepPrimAPI_MakeTorus, BRepPrimAPI_MakePrism)
from OCP.BRepTools import BRepTools
from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere, GeomAbs_Torus
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_SOLID, TopAbs_REVERSED
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir, gp_Vec
from step2code.verification.metrics import bbox, volume, area, boolean_volume, symmetric_difference_volume
from step2code.plan.model import node as plan_node
from step2code.plan.graph import rewrite_ids, reachable
from .context import native_call, csg_deadline, namespace_sources, capture_sources, restore_sources


def boolean(a,b,operation):
    with native_call('boolean.'+operation):
        builder={'cut':BRepAlgoAPI_Cut,'intersect':BRepAlgoAPI_Common,'union':BRepAlgoAPI_Fuse}[operation](a,b)
        builder.Build()
        if not builder.IsDone():raise ValueError('CSG boolean failed')
        return builder.Shape()


def _valid(shape):
    from step2code.recognition.prism import explore
    return (not shape.IsNull() and BRepCheck_Analyzer(shape).IsValid() and
            any(explore(shape,TopAbs_SOLID)) and math.isfinite(volume(shape)) and volume(shape)>0)


def _frame(origin,axis):
    seed=np.eye(3)[int(np.argmin(np.abs(axis)))];x=seed-axis*float(seed@axis);x/=np.linalg.norm(x)
    return dict(origin=np.array(origin).tolist(),x_axis=x.tolist(),y_axis=np.cross(axis,x).tolist(),z_axis=np.array(axis).tolist())


def _box(origin,x,y,z,dimensions):
    node=dict(kind='box',origin=np.array(origin).tolist(),x_axis=np.array(x).tolist(),
              y_axis=np.array(y).tolist(),axis=np.array(z).tolist(),dimensions=list(map(float,dimensions)))
    shape=BRepPrimAPI_MakeBox(gp_Ax2(gp_Pnt(*origin),gp_Dir(*z),gp_Dir(*x)),*dimensions).Shape()
    return node,shape


def _recognize_csg(solid,number,options):
    from step2code.recognition.prism import explore,_profile,UnsupportedGeometry
    start=time.monotonic();faces=[TopoDS.Face_s(f) for f in explore(solid,TopAbs_FACE)]
    allowed=(GeomAbs_Plane,GeomAbs_Cylinder,GeomAbs_Cone,GeomAbs_Sphere,GeomAbs_Torus)
    diagnostics=dict(solid_index=number,method='verified_bounded_csg',attempts=[],
                     limits=dict(candidates=options.max_csg_candidates,steps=options.max_csg_steps,
                                 faces=options.max_csg_faces,seconds=options.max_csg_seconds))
    unsupported=sorted({str(BRepAdaptor_Surface(f).GetType()) for f in faces if BRepAdaptor_Surface(f).GetType() not in allowed})
    diagnostics['gate']=dict(face_count=len(faces),face_limit=options.max_csg_faces,unsupported_surface_types=unsupported)
    if unsupported or len(faces)>options.max_csg_faces:
        diagnostics['reason_code']='capability.surface_type' if unsupported else 'limit.source_faces'
        diagnostics['reason']='CSG fallback currently requires bounded analytic boundary surfaces'
        raise UnsupportedGeometry(diagnostics['reason'],diagnostics)
    deadline=csg_deadline(options.max_csg_seconds)
    source_volume=volume(solid)
    if not math.isfinite(source_volume) or source_volume<=0:raise UnsupportedGeometry('Nonpositive CSG source volume',diagnostics)
    bounds=np.array(bbox(solid));diag=float(np.linalg.norm(bounds[3:]-bounds[:3]));center=(bounds[3:]+bounds[:3])/2
    corners=np.array([[x,y,z] for x in (bounds[0],bounds[3]) for y in (bounds[1],bounds[4]) for z in (bounds[2],bounds[5])])
    margin=max(options.length_tolerance*10,diag*1e-5)
    budget=options.reconstruction_relative_tolerance
    tools=[];stocks=[];inner=[];counter=0;unique=set()
    source_edges=[]
    for e in explore(solid,TopAbs_EDGE):
        if not any(e.IsSame(old) for old in source_edges):source_edges.append(e)
    def expired():return time.monotonic()>deadline
    def add(node,shape,operation,source_face=None,local=None):
        nonlocal counter
        if expired() or counter>=options.max_csg_candidates:return
        if operation=='stock' and len(stocks)>=max(1,min(20,options.max_csg_candidates//2)):return
        if not _valid(shape):return
        # Exact serialized parameters only: no geometric tolerance merging.
        import json
        signature=json.dumps(dict(node=node,operation=operation),sort_keys=True)
        if signature in unique:return
        unique.add(signature);counter+=1
        node.update(source_face_index=source_face)
        item=dict(node=node,shape=shape,operation=operation,local=local or {},id=f'tool_{counter:03d}')
        if operation=='stock':stocks.append(item)
        else:tools.append(item)
    node,shape=_box(bounds[:3]-margin,(1,0,0),(0,1,0),(0,0,1),bounds[3:]-bounds[:3]+2*margin)
    add(node,shape,'stock')
    # Large source cap profiles are a compact alternative to a box plus planar cuts.
    planar=[(area(f),i,f,BRepAdaptor_Surface(f).Plane()) for i,f in enumerate(faces,1) if BRepAdaptor_Surface(f).GetType()==GeomAbs_Plane]
    planar.sort(key=lambda p:(-p[0],p[1]))
    for _,index,face,plane in planar[:min(12,options.max_candidates)]:
        if expired():break
        outward=np.array(plane.Axis().Direction().Coord())*(-1 if face.Orientation()==TopAbs_REVERSED else 1)
        axis=-outward;origin=np.array(plane.Location().Coord());frame=_frame(origin,axis)
        projections=(corners-origin)@axis
        separations=sorted(set(float((np.array(p.Location().Coord())-origin)@axis) for _,_,_,p in planar
                               if abs(abs(float(np.array(p.Axis().Direction().Coord())@axis))-1)<1e-9 and
                               float((np.array(p.Location().Coord())-origin)@axis)>options.length_tolerance))
        distances=list(dict.fromkeys(([max(separations)] if separations else [float(max(projections))])+separations[:3]))
        for outer_only in (False,True):
            try:
                cap=BRepBuilderAPI_MakeFace(BRepTools.OuterWire_s(face),True).Face() if outer_only else face
                prefix=f'solid_{number:02d}_csg_cap_{index:03d}_{int(outer_only)}'
                local,plane_id,profile_id=_profile(cap,frame,prefix,f'solid_{number:02d}.face_{index:04d}.csg_cap',options.length_tolerance,source_edges)
            except Exception:continue
            for distance in distances:
                try:
                    prism=BRepPrimAPI_MakePrism(cap,gp_Vec(*(axis*distance)),True).Shape()
                    if len(list(explore(prism,TopAbs_SOLID)))!=1:continue
                    prism=TopoDS.Solid_s(next(explore(prism,TopAbs_SOLID)));BRepLib.OrientClosedSolid_s(prism)
                    add(dict(kind='prism',profile_id=profile_id,frame=frame,distance=distance,
                             outer_only=outer_only),prism,'stock',index,local)
                except Exception:continue
    # Analytic surface tools, global spans and measured finite spans for blind cuts.
    for index,face in enumerate(faces,1):
        if expired() or counter>=options.max_csg_candidates:break
        a=BRepAdaptor_Surface(face);kind=a.GetType();reverse=face.Orientation()==TopAbs_REVERSED
        try:
            if kind==GeomAbs_Plane:
                plane=a.Plane();axis=np.array(plane.Axis().Direction().Coord())*(-1 if reverse else 1)
                frame=_frame(plane.Location().Coord(),axis);x=np.array(frame['x_axis']);y=np.array(frame['y_axis'])
                offset=center-np.array(frame['origin']);anchor=center-axis*float(offset@axis)-2*diag*(x+y)
                node,shape=_box(anchor,x,y,axis,(4*diag,4*diag,4*diag))
                add(node,shape,'cut',index)
            elif kind in (GeomAbs_Cylinder,GeomAbs_Cone):
                geometry=a.Cylinder() if kind==GeomAbs_Cylinder else a.Cone()
                axis=np.array(geometry.Axis().Direction().Coord());origin=np.array(geometry.Location().Coord())
                projected=(corners-origin)@axis;lo=float(min(projected))-margin;hi=float(max(projected))+margin
                fbox=np.array(bbox(face));fcorners=np.array([[x,y,z] for x in (fbox[0],fbox[3]) for y in (fbox[1],fbox[4]) for z in (fbox[2],fbox[5])])
                fp=(fcorners-origin)@axis
                spans=[(lo,hi)]
                if reverse:spans.append((float(min(fp)),float(max(fp))))
                for low,high in spans:
                    if kind==GeomAbs_Cylinder:
                        radius=geometry.Radius();bottom=origin+axis*low;height=high-low
                        shape=BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*bottom),gp_Dir(*axis)),radius,height).Shape()
                        node=dict(kind='cylinder',origin=bottom.tolist(),axis=axis.tolist(),radius=radius,height=height)
                    else:
                        slope=math.tan(geometry.SemiAngle());radius=geometry.RefRadius();apex=-radius/slope
                        if slope>0:low=max(low,apex)
                        else:high=min(high,apex)
                        if high-low<=options.length_tolerance:continue
                        r0=max(0.,radius+slope*low);r1=max(0.,radius+slope*high);bottom=origin+axis*low
                        shape=BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(*bottom),gp_Dir(*axis)),r0,r1,high-low).Shape()
                        node=dict(kind='cone',origin=bottom.tolist(),axis=axis.tolist(),bottom_radius=r0,top_radius=r1,height=high-low)
                    add(node,shape,'cut' if reverse else 'intersect',index)
            elif kind==GeomAbs_Sphere:
                sphere=a.Sphere();origin=sphere.Location().Coord();radius=sphere.Radius()
                add(dict(kind='sphere',origin=list(origin),radius=radius),BRepPrimAPI_MakeSphere(gp_Pnt(*origin),radius).Shape(),'cut' if reverse else 'intersect',index)
            elif kind==GeomAbs_Torus:
                torus=a.Torus();origin=torus.Location().Coord();axis=torus.Axis().Direction().Coord()
                add(dict(kind='torus',origin=list(origin),axis=list(axis),major_radius=torus.MajorRadius(),minor_radius=torus.MinorRadius()),
                    BRepPrimAPI_MakeTorus(gp_Ax2(gp_Pnt(*origin),gp_Dir(*axis)),torus.MajorRadius(),torus.MinorRadius()).Shape(),'cut' if reverse else 'intersect',index)
        except Exception as exc:diagnostics['attempts'].append(dict(face_index=index,status='tool_error',reason=str(exc)))
    diagnostics.update(candidate_count=counter,stock_count=len(stocks),tool_count=len(tools))
    def missing(shape):return boolean_volume(boolean(solid,shape,'cut'))/source_volume
    def extra(shape):return boolean_volume(boolean(shape,solid,'cut'))/source_volume
    # Prefer the smallest enclosing measured prism. The box always remains an option.
    enclosing=[]
    for stock in sorted(stocks,key=lambda t:(volume(t['shape']),t['id'])):
        if expired():break
        try:
            miss=missing(stock['shape'])
            diagnostics['attempts'].append(dict(stock_id=stock['id'],missing_relative=miss))
            if miss<=budget/10:enclosing.append(stock);break
            if extra(stock['shape'])<=budget/10:inner.append(stock)
        except Exception:continue
    selected=[];candidate=None;root_id=None;strategy=None
    if enclosing:
        stock=enclosing[0];candidate=stock['shape'];selected=[stock];root_id=stock['id'];strategy='enclosing_stock_monotone_cuts'
        step_count=0
        for tool in tools:
            if expired() or step_count>=options.max_csg_steps:break
            try:
                result=boolean(candidate,tool['shape'],tool['operation'])
                if not _valid(result) or volume(candidate)-volume(result)<=source_volume*1e-10:continue
                # Cumulative missing volume, not just the current step's loss.
                miss=missing(result)
                if miss>budget/10:continue
                key=f'op_{len(selected):03d}'
                item=dict(id=key,node=dict(kind=tool['operation'],inputs=[root_id,tool['id']]))
                selected.extend([tool,item]);root_id=key;candidate=result;step_count+=1
                if abs(volume(candidate)-source_volume)/source_volume<=budget:break
            except Exception:continue
    primary=(candidate,list(selected),root_id,strategy)
    # Additive cap coverage handles localized bosses and nonconvex prism unions.
    if candidate is None or abs(volume(candidate)-source_volume)/source_volume>budget:
        if inner:
            candidate=inner[0]['shape'];selected=[inner[0]];root_id=inner[0]['id'];strategy='source_contained_prism_union'
            for tool in inner[1:options.max_csg_steps+1]:
                if expired():break
                try:
                    result=boolean(candidate,tool['shape'],'union')
                    if not _valid(result) or volume(result)-volume(candidate)<=source_volume*1e-10:continue
                    key=f'op_{len(selected):03d}'
                    selected.extend([tool,dict(id=key,node=dict(kind='union',inputs=[root_id,tool['id']]))]);root_id=key;candidate=result
                except Exception:continue
    if primary[0] is not None and (candidate is None or abs(volume(primary[0])-source_volume)<abs(volume(candidate)-source_volume)):
        candidate,selected,root_id,strategy=primary
    # Residual solids must themselves be expressible by verified native features.
    # Their BREP is never embedded; only measured curves and operation parameters survive.
    if candidate is not None and not expired() and abs(volume(candidate)-source_volume)/source_volume>budget:
        for operation,remaining in (('cut',boolean(candidate,solid,'cut')),('union',boolean(solid,candidate,'cut'))):
            residuals=list(explore(remaining,TopAbs_SOLID))
            if len(residuals)>min(8,options.max_csg_steps):continue
            additions=[];success=True
            for ri,part in enumerate(residuals,1):
                if expired():success=False;break
                found=None
                from step2code.recognition.prism import _recognize_solid
                from step2code.recognition.revolution import recognize_revolution
                for method in (_recognize_solid,recognize_revolution):
                    try:
                        local,steps,ev=method(TopoDS.Solid_s(part),number,options)
                        found=_native_operand(local,steps,ev,f'solid_{number:02d}_residual_{operation}_{ri:02d}')
                        break
                    except UnsupportedGeometry:pass
                if found is None:success=False;break
                additions.append(found)
            if success:
                for ri,(node,local) in enumerate(additions,1):
                    key=f'residual_{operation}_{ri:02d}';opid=f'join_{operation}_{ri:02d}'
                    selected.extend([dict(id=key,node=node,local=local),dict(id=opid,node=dict(kind=operation,inputs=[root_id,key]))])
                    root_id=opid
                if additions:
                    candidate=boolean(candidate,remaining,operation)
                    strategy+='; verified native residual '+operation
    diagnostics.update(elapsed_seconds=time.monotonic()-start,strategy=strategy,
                       final_volume_relative_error=abs(volume(candidate)-source_volume)/source_volume if candidate is not None else None)

    if candidate is None or not _valid(candidate) or len(list(explore(candidate,TopAbs_SOLID)))!=1 or abs(volume(candidate)-source_volume)/source_volume>budget:
        diagnostics['reason']='Bounded CSG candidates did not reconstruct the complete source solid'
        raise UnsupportedGeometry(diagnostics['reason'],diagnostics)
    difference=symmetric_difference_volume(solid,candidate)/source_volume
    if not math.isfinite(difference) or difference>budget:
        diagnostics['symmetric_difference_relative']=difference
        raise UnsupportedGeometry('CSG source symmetric difference failed',diagnostics)
    nodes={};definitions={}
    for item in selected:
        definitions[item['id']]=dict(item['node'])
        nodes.update(item.get('local',{}))
    nodes,root_id=materialize_candidates(nodes,definitions,root_id,f'solid_{number:02d}_csg')
    boolean_count=sum(len(n['inputs'].get('operands',[]))-1 if n['op']=='boolean.union' else 1
                      for n in nodes.values() if n['op'].startswith('boolean.'))
    if boolean_count>options.max_csg_steps:
        raise UnsupportedGeometry('CSG graph exceeds total Boolean limit',dict(solid_index=number,boolean_count=boolean_count,limit=options.max_csg_steps))
    feature_id=root_id
    frame=dict(origin=center.tolist(),x_axis=[1.,0.,0.],y_axis=[0.,1.,0.],z_axis=[0.,0.,1.])
    selected_check=dict(status='recognized',strategy=strategy,symmetric_difference_relative=difference,
                        node_count=len(nodes),boolean_count=boolean_count)
    evidence=dict(solid_index=number,method='verified_bounded_csg',node_sources=capture_sources(nodes),selected=selected_check,attempts=diagnostics,
        coordinate_frame=dict(selected_frame=frame,application='one shared rigid frame and uniform scale for all CSG operands',adjustments=[]),
        source_face_indices=list(range(1,len(faces)+1)),
        source_topology=dict(face_count=len(faces),edge_count=len(source_edges),faces=[dict(face_index=i,edge_indices=sorted({j for edge in explore(face,TopAbs_EDGE) for j,original in enumerate(source_edges,1) if edge.IsSame(original)})) for i,face in enumerate(faces,1)]),
        history_recovered=False,approximation='analytic primitives and source cap curves; no sampled fitting',
        source_coverage_role='all source faces validated by complete reconstructed-solid difference; tool face indices are inference evidence')
    return nodes,[feature_id],evidence


def _native_operand(local,roots,evidence,prefix):
    """Namespace a derived DAG using declared references only."""
    restore_sources(evidence)
    names={key:prefix+'_'+key for key in local}
    renamed=rewrite_ids(local,names)
    namespace_sources(names,prefix)
    return dict(kind='native',root=names[roots[-1]]),renamed


def recognize_csg(solid,number,options):
    from step2code.recognition import UnsupportedGeometry
    started=time.monotonic()
    try:return _recognize_csg(solid,number,options)
    except UnsupportedGeometry as failure:
        if failure.diagnostics.get('reason')=='CSG fallback currently requires bounded analytic boundary surfaces':raise
        if options.max_csg_depth==0:raise
        from step2code.recognition.partition import recognize_partition
        return recognize_partition(solid,number,options,failure.diagnostics,started)



def materialize_candidates(nodes, definitions, root, prefix):
    """Lower selected search candidates directly into the single typed DAG.

    Candidate descriptors are ephemeral search state, never a serialized subgraph.
    """
    import copy
    nodes=copy.deepcopy(nodes);done={}
    def visit(key):
        if key in done:return done[key]
        d=definitions[key];kind=d['kind'];name=prefix+'_'+key
        if kind in ('union','cut','intersect'):
            refs=[visit(k) for k in d['inputs']]
            inputs=dict(target=[refs[0]],tool=[refs[1]]) if kind=='cut' else dict(operands=refs)
            nodes[name]=plan_node('boolean.'+kind,inputs)
        elif kind=='native':
            name=d['root']
        else:
            local_id=name+'_local';frame_id=name+'_frame'
            if kind=='prism':
                nodes[local_id]=plan_node('solid.extrude',dict(profile=[d['profile_id']]),distance=d['distance'])
                f=d['frame']
            else:
                f=_frame(d['origin'],np.array(d.get('axis',[0,0,1]),dtype=float))
                if kind=='box':
                    w,dep,h=d['dimensions']
                    x=np.array(d['x_axis']);y=np.array(d['y_axis'])
                    f=dict(origin=(np.array(d['origin'])+x*w/2+y*dep/2).tolist(),x_axis=x.tolist(),y_axis=y.tolist())
                    nodes[local_id]=plan_node('solid.box',width=w,depth=dep,height=h)
                elif kind=='cylinder':nodes[local_id]=plan_node('solid.cylinder',radius=d['radius'],height=d['height'])
                elif kind=='cone':nodes[local_id]=plan_node('solid.cone',radius_bottom=d['bottom_radius'],radius_top=d['top_radius'],height=d['height'])
                elif kind=='sphere':nodes[local_id]=plan_node('solid.sphere',radius=d['radius'])
                elif kind=='torus':nodes[local_id]=plan_node('solid.torus',major_radius=d['major_radius'],minor_radius=d['minor_radius'])
                else:raise ValueError('Unknown candidate: '+kind)
            nodes[frame_id]=plan_node('frame.rigid3',origin=f['origin'],x_axis=f['x_axis'],y_axis=f['y_axis'])
            nodes[name]=plan_node('shape.transform',dict(shape=[local_id],frame=[frame_id]))
        done[key]=name;return name
    root=visit(root)
    # Explicit associative-union lowering, shared by every strategy.
    from step2code.plan.passes import flatten_unions
    nodes=flatten_unions(nodes,[root])
    return nodes,root
