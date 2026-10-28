"""Bounded planar decomposition into independently proved native features."""
from __future__ import annotations
import copy
import time
from dataclasses import replace
import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Plane
from OCP.TopAbs import TopAbs_FACE,TopAbs_SOLID,TopAbs_EDGE
from OCP.TopoDS import TopoDS
from step2code.verification.metrics import bbox,volume,symmetric_difference_volume
from step2code.plan.graph import rewrite_ids
from step2code.plan.model import node
from step2code.plan.passes import flatten_unions
from .context import namespace_sources,capture_sources,restore_sources


def recognize_partition(solid,number,options,initial_failure,started):
    from step2code.recognition.prism import explore,UnsupportedGeometry,_recognize_solid
    from step2code.recognition.revolution import recognize_revolution
    from step2code.recognition.csg import _recognize_csg,_box,boolean,_native_operand
    deadline=started+options.max_csg_seconds;attempts=[];counter=0
    def remaining():return deadline-time.monotonic()
    def groups(shape,excluded):
        bounds=np.array(bbox(shape));corners=np.array([[x,y,z] for x in (bounds[0],bounds[3]) for y in (bounds[1],bounds[4]) for z in (bounds[2],bounds[5])]);found=[]
        for i,f in enumerate(explore(shape,TopAbs_FACE),1):
            a=BRepAdaptor_Surface(TopoDS.Face_s(f))
            if a.GetType()!=GeomAbs_Plane:continue
            axis=np.array(a.Plane().Axis().Direction().Coord())
            if axis[int(np.argmax(np.abs(axis)))]<0:axis=-axis
            if any(np.linalg.norm(np.cross(axis,old))<1e-8 for old in excluded):continue
            group=next((g for g in found if np.linalg.norm(np.cross(axis,g[0]))<1e-8),None)
            if group is None:group=[axis,[]];found.append(group)
            position=float(np.array(a.Plane().Location().Coord())@axis)
            if not any(abs(position-v)<options.length_tolerance for v in group[1]):group[1].append(position)
        result=[]
        for axis,positions in found:
            projected=corners@axis;lo=float(min(projected));hi=float(max(projected))
            positions=sorted(v for v in positions if lo+options.length_tolerance<v<hi-options.length_tolerance)
            if positions:result.append((axis,[lo-options.length_tolerance]+positions+[hi+options.length_tolerance]))
        return sorted(result,key=lambda g:-len(g[1]))[:2]
    def solve(shape,depth,excluded,root=False):
        nonlocal counter
        if remaining()<=0:return None
        if not root:
            counter+=1
            if counter>options.max_csg_parts:return None
            local_options=replace(options,max_csg_seconds=max(.001,remaining()))
            for method in (_recognize_solid,recognize_revolution,_recognize_csg):
                if remaining()<=0:return None
                try:
                    local,steps,ev=method(shape,number,local_options)
                    return [(local,steps,ev,shape)]
                except UnsupportedGeometry:pass
        if depth>=options.max_csg_depth:return None
        for axis,positions in groups(shape,excluded):
            if len(positions)-1>options.max_csg_parts-counter:continue
            bounds=np.array(bbox(shape));center=(bounds[3:]+bounds[:3])/2;diagonal=float(np.linalg.norm(bounds[3:]-bounds[:3]))
            seed=np.eye(3)[int(np.argmin(np.abs(axis)))];x=seed-axis*float(seed@axis);x/=np.linalg.norm(x);y=np.cross(axis,x)
            result=[];ok=True
            attempts.append(dict(depth=depth,axis=axis.tolist(),positions_mm=positions))
            for low,high in zip(positions,positions[1:]):
                if remaining()<=0:ok=False;break
                anchor=center+axis*(low-float(center@axis))-diagonal*(x+y)
                _,tool=_box(anchor,x,y,axis,(2*diagonal,2*diagonal,high-low))
                section=boolean(shape,tool,'intersect')
                for part in explore(section,TopAbs_SOLID):
                    pieces=solve(TopoDS.Solid_s(part),depth+1,excluded+[axis])
                    if pieces is None:ok=False;break
                    result.extend(pieces)
                if not ok:break
            if ok and result:
                joined=result[0][3]
                for piece in result[1:]:joined=boolean(joined,piece[3],'union')
                if symmetric_difference_volume(shape,joined)/volume(shape)<=options.reconstruction_relative_tolerance:
                    return result
        return None
    pieces=solve(solid,0,[],True)
    if not pieces:
        failure=copy.deepcopy(initial_failure)
        failure.update(partition=dict(status='not_recognized',attempted_parts=counter,attempts=attempts,
                       elapsed_seconds=time.monotonic()-started,reason='bounded native-feature partition incomplete'))
        raise UnsupportedGeometry('Neither direct CSG nor bounded native-feature partition reconstructed the source',failure)
    nodes={};roots=[];part_evidence=[]
    for i,(local,steps,ev,shape) in enumerate(pieces,1):
        prefix=f'solid_{number:02d}_partition_{i:03d}'
        restore_sources(ev)
        names={key:prefix+'_'+key for key in local}
        nodes.update(rewrite_ids(local,names));roots.append(names[steps[-1]])
        namespace_sources(names,prefix)
        part_evidence.append(dict(part_index=i,method=ev['method'],source_check=ev['selected']))
    root=f'solid_{number:02d}_partition_union'
    if len(roots)>1:nodes[root]=node('boolean.union',dict(operands=roots))
    else:root=roots[0]
    nodes=flatten_unions(nodes,[root])
    boolean_count=sum(len(n['inputs']['operands'])-1 if n['op']=='boolean.union' else 1 for n in nodes.values() if n['op'].startswith('boolean.'))
    if boolean_count>options.max_csg_steps:
        raise UnsupportedGeometry('Partition CSG exceeds total Boolean operation limit',dict(solid_index=number,boolean_count=boolean_count,limit=options.max_csg_steps))
    joined=pieces[0][3]
    for piece in pieces[1:]:joined=boolean(joined,piece[3],'union')
    difference=symmetric_difference_volume(solid,joined)/volume(solid)
    source_faces=list(explore(solid,TopAbs_FACE));source_edges=[]
    for edge in explore(solid,TopAbs_EDGE):
        if not any(edge.IsSame(e) for e in source_edges):source_edges.append(edge)
    bounds=np.array(bbox(solid));frame=dict(origin=((bounds[:3]+bounds[3:])/2).tolist(),x_axis=[1.,0.,0.],y_axis=[0.,1.,0.],z_axis=[0.,0.,1.])
    selected=dict(status='recognized',strategy='verified_planar_partition_union',symmetric_difference_relative=difference,
                  part_count=len(pieces),node_count=len(nodes),boolean_count=boolean_count)
    evidence=dict(solid_index=number,method='verified_partition_csg',node_sources=capture_sources(nodes),selected=selected,
        attempts=dict(prior_direct_csg=initial_failure,partition_planes=attempts,attempted_parts=counter,parts=part_evidence),
        coordinate_frame=dict(selected_frame=frame,application='all derived native operands transformed together exactly once',adjustments=[]),
        source_face_indices=list(range(1,len(source_faces)+1)),source_topology=dict(face_count=len(source_faces),edge_count=len(source_edges),faces=[dict(face_index=i,edge_indices=sorted({j for edge in explore(face,TopAbs_EDGE) for j,original in enumerate(source_edges,1) if edge.IsSame(original)})) for i,face in enumerate(source_faces,1)]),
        history_recovered=False,approximation='planar source partition; verified cap/meridian geometry replay; no sampled fitting')
    return nodes,[root],evidence
