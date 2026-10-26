"""Readable, standalone SCAPI emission for verified STEP CSG recipes."""
from __future__ import annotations
import math
import numpy as np
from json2scapi.codegen.sketch_builder import fmt_literal


HELPERS = '''def _csg_vector(x_axis, y_axis, vector):
    z_axis = (x_axis[1]*y_axis[2]-x_axis[2]*y_axis[1],
              x_axis[2]*y_axis[0]-x_axis[0]*y_axis[2],
              x_axis[0]*y_axis[1]-x_axis[1]*y_axis[0])
    return tuple(vector[0]*x_axis[i] + vector[1]*y_axis[i] + vector[2]*z_axis[i] for i in range(3))


def _csg_point(origin, x_axis, y_axis, point, scale):
    vector = _csg_vector(x_axis, y_axis, point)
    return tuple(origin[i] + scale*vector[i] for i in range(3))'''


def _oriented_curves(entities,loop_id,outer):
    """Orient loop inputs CCW; SCAPI reverses inner wires when making faces."""
    import copy
    curves=[copy.deepcopy(entities[k]) for k in entities[loop_id]['inputs']]
    signed=0.
    for curve in curves:
        p=curve['params']
        if curve['op']=='sketch.circle':continue
        a=p['start'];b=p['end'];signed+=a[0]*b[1]-a[1]*b[0]
        if curve['op']=='sketch.arc':
            c=p['center'];t0=math.atan2(a[1]-c[1],a[0]-c[0]);t1=math.atan2(b[1]-c[1],b[0]-c[0])
            theta=(t1-t0)%(2*math.pi) if p['ccw'] else -((t0-t1)%(2*math.pi))
            signed+=p['radius']**2*(theta-math.sin(theta))
    reverse=(signed<0) if outer else (signed>0)
    if reverse:
        curves.reverse()
        for curve in curves:
            p=curve['params']
            if curve['op']!='sketch.circle':
                p['start'],p['end']=p['end'],p['start']
                if 'ccw' in p:p['ccw']=not p['ccw']
    return curves


def add_csg(emitter,index,step,evidence):
    feature=emitter.payload['entities'][step];params=feature['params'];frame=evidence['coordinate_frame']['selected_frame']
    if params.get('operation')!='new' or params.get('profile_scale')!=1 or params.get('frame')!=frame:
        raise ValueError('Only verified new CSG with matching measured frame and scale 1 is supported')
    if frame['x_axis']!=[1.,0.,0.] or frame['y_axis']!=[0.,1.,0.]:
        raise ValueError('Measured CSG recipe must start in its recorded world-aligned local frame')
    nodes=params['recipe']['nodes'];root=params['recipe']['root'];order=[];active=set();visited=set()
    def walk(key):
        if key in active:raise ValueError('Cyclic CSG recipe')
        if key in visited:return
        if key not in nodes:raise ValueError('Missing CSG node')
        active.add(key);node=nodes[key];kind=node['kind']
        if kind in ('cut','intersect','union'):
            if (kind=='union' and len(node.get('inputs',[]))<2) or (kind!='union' and len(node.get('inputs',[]))!=2):
                raise ValueError('CSG cut/intersect require two operands; union requires at least two')
            for child in node['inputs']:walk(child)
        elif kind not in ('box','prism','cylinder','cone','sphere','torus','revolution'):
            raise ValueError('Unsupported CSG leaf '+kind)
        active.remove(key);visited.add(key);order.append(key)
    walk(root)
    if visited!=set(nodes):raise ValueError('Unreachable CSG nodes are not accepted')
    feature_name=f'apply_feature_{index:02d}'
    bindings={key:f'BODY_{index:02d}_{key.upper()}'+('_MM' if key=='origin' else '') for key in ('origin','x_axis','y_axis')}
    bindings['profile_scale']=f'FEATURE_{index:02d}_PROFILE_SCALE'
    for key,name in bindings.items():
        value=1. if key=='profile_scale' else frame[key]
        emitter.constants.append(f'{name} = {fmt_literal(value)}')
        item=dict(value=value,units='mm' if key=='origin' else '1',quantity='uniform_scale' if key=='profile_scale' else ('position' if key=='origin' else 'direction'),
                  origin_kind='inferred',edit_policy='independent',expression=None,argument=key,
                  bindings=[dict(ir_id=step,path='params.profile_scale' if key=='profile_scale' else 'params.frame.'+key)],
                  constraints='positive finite scale and finite orthonormal placement',design_parameter_recovered=False)
        emitter.metadata['parameters'][name]=item;emitter.metadata['symbols'][name]=item
    center=np.array(frame['origin']);entities=emitter.payload['entities'];tool_names={};profile_names=[]
    def point(world):
        return f'_csg_point(origin, x_axis, y_axis, {fmt_literal((np.array(world)-center).tolist())}, profile_scale)'
    def vector(v):return f'_csg_vector(x_axis, y_axis, {fmt_literal(v)})'
    arguments='origin=origin, x_axis=x_axis, y_axis=y_axis, profile_scale=profile_scale'
    for ordinal,key in enumerate(order,1):
        node=nodes[key];kind=node['kind']
        if kind in ('cut','intersect','union'):continue
        name=f'make_tool_{index:02d}_{ordinal:03d}';tool_names[key]=name
        lines=[f'def {name}(*, origin, x_axis, y_axis, profile_scale):',f'    """Build measured {kind} operand; source face {node.get("source_face_index")}."""']
        if kind=='revolution':
            import ast
            import copy
            from step2code.composite_codegen import CompositeEmitter, _Rename
            original=copy.deepcopy(entities[node['feature_id']]);ev=copy.deepcopy(node['evidence']);ev['operation_id']=node['feature_id']
            payload=copy.deepcopy(emitter.payload)
            native=CompositeEmitter.__new__(CompositeEmitter)
            native.payload=payload;native.evidence=dict(features=[ev]);native.constants=[];native.profiles=[];native.features=[];native.build_calls=[]
            native.metadata={k:{} for k in ('parameters','symbols','profiles','source_profiles','placements','templates','feature_groups')}
            native.metadata.update(instances=[],reconstruction_operations=[],preparation={'operations':{}})
            native._revolution(1,node['feature_id'],ev)
            names={function.name:f'{function.name}_body_{index:02d}_tool_{ordinal:03d}'
                   for block in native.profiles+native.features for function in ast.parse(block).body if isinstance(function,ast.FunctionDef)}
            for block in native.profiles+native.features:
                tree=ast.fix_missing_locations(_Rename(names).visit(ast.parse(block)))
                emitter.profiles.append(ast.unparse(tree))
            for pname,meta in native.metadata['profiles'].items():
                emitter.metadata['profiles'][names[pname]]=meta
                emitter.metadata['symbols'][names[pname]]=dict(kind='profile_function',ir_ids=[step])
                profile_names.append(names[pname])
            nf=ev['coordinate_frame']['selected_frame']
            lines += ['    parts = []',f'    {names["apply_feature_01"]}(parts, origin={point(nf["origin"])}, x_axis={vector(nf["x_axis"])}, y_axis={vector(nf["y_axis"])}, profile_scale=profile_scale)',
                      '    return parts[0]']
        elif kind in ('cylinder','cone','sphere'):

            if kind=='sphere':call=f'scad.make_sphere_rsolid(radius={fmt_literal(node["radius"])}*profile_scale, center={point(node["origin"])})'
            else:
                sizes=(f'radius={fmt_literal(node["radius"])}*profile_scale' if kind=='cylinder' else
                       f'bottom_radius={fmt_literal(node["bottom_radius"])}*profile_scale, top_radius={fmt_literal(node["top_radius"])}*profile_scale')
                call=f'scad.make_{kind}_rsolid({sizes}, height={fmt_literal(node["height"])}*profile_scale, bottom_face_center={point(node["origin"])}, axis={vector(node["axis"])})'
            lines.append('    return '+call)
        else:
            profile_name=f'make_profile_{index:02d}_tool_{ordinal:03d}';profile_names.append(profile_name)
            plines=[f'def {profile_name}(*, origin, x_axis, y_axis, profile_scale):','    """Build explicit local curves for one measured CSG operand."""']
            wires=[]
            if kind=='prism':
                profile=entities[node['profile_id']];localframe=node['frame'];base=np.array(localframe['origin']);x=np.array(localframe['x_axis']);y=np.array(localframe['y_axis']);normal=localframe['z_axis']
                def profile_point(p):return point(base+p[0]*x+p[1]*y)
                for li,loop_id in enumerate(profile['inputs'],1):
                    edges=[]
                    for ei,curve in enumerate(_oriented_curves(entities,loop_id,True),1):
                        p=curve['params'];edge=f'edge_{li:02d}_{ei:03d}'
                        if curve['op']=='sketch.line':call=f'scad.make_line_redge(start={profile_point(p["start"])}, end={profile_point(p["end"])})'
                        elif curve['op']=='sketch.arc':call=f'scad.make_three_point_arc_redge(start={profile_point(p["start"])}, middle={profile_point(p["mid"])}, end={profile_point(p["end"])})'
                        elif curve['op']=='sketch.circle':
                            cx,cy=p['center'];r=p['radius'];pts=[(cx+r,cy),(cx,cy+r),(cx-r,cy),(cx,cy-r)]
                            for half,(a,b,c) in enumerate(((0,1,2),(2,3,0)),1):
                                arc=edge+f'_half_{half}';edges.append(arc)
                                plines.append(f'    {arc} = scad.make_three_point_arc_redge(start={profile_point(pts[a])}, middle={profile_point(pts[b])}, end={profile_point(pts[c])})')
                            continue
                        else:raise ValueError('Unsupported CSG prism curve')
                        plines.append(f'    {edge} = {call}');edges.append(edge)
                    wire=f'wire_{li:02d}';wires.append(wire)
                    plines.append(f'    {wire} = scad.make_wire_from_edges_rwire(edges=[{", ".join(edges)}])')
            else:
                base=np.array(node['origin'])
                if kind=='box':
                    x=np.array(node['x_axis'])*node['dimensions'][0];y=np.array(node['y_axis'])*node['dimensions'][1]
                    pts=[base,base+x,base+x+y,base+y];normal=node['axis']
                    for i in range(4):plines.append(f'    edge_{i+1} = scad.make_line_redge(start={point(pts[i])}, end={point(pts[(i+1)%4])})')
                    plines.append('    wire_01 = scad.make_wire_from_edges_rwire(edges=[edge_1, edge_2, edge_3, edge_4])')
                else:
                    axis=np.array(node['axis']);seed=np.eye(3)[int(np.argmin(np.abs(axis)))];radial=seed-axis*float(seed@axis);radial/=np.linalg.norm(radial)
                    c=base+radial*node['major_radius'];r=node['minor_radius'];pts=[c+radial*r,c+axis*r,c-radial*r,c-axis*r];normal=np.cross(radial,axis).tolist()
                    for h,(a,b,c) in enumerate(((0,1,2),(2,3,0)),1):plines.append(f'    edge_{h} = scad.make_three_point_arc_redge(start={point(pts[a])}, middle={point(pts[b])}, end={point(pts[c])})')
                    plines.append('    wire_01 = scad.make_wire_from_edges_rwire(edges=[edge_1, edge_2])')
                wires=['wire_01']
            plines.append(f'    return scad.make_face_from_wires_rface(outer_wire={wires[0]}, inner_wires=[{", ".join(wires[1:])}], normal={vector(normal)})')
            emitter.profiles.append('\n'.join(plines))
            emitter.metadata['profiles'][profile_name]=dict(ir_ids=[node['profile_id']] if kind=='prism' else [],returns='face',csg_operand=key,edit_policy='fixed_local_geometry')
            emitter.metadata['symbols'][profile_name]=dict(kind='profile_function',ir_ids=[step])
            lines.append(f'    face = {profile_name}({arguments})')
            if kind=='torus':lines.append(f'    return scad.revolve_rsolid(profile=face, axis={vector(node["axis"])}, origin={point(node["origin"])}, angle=360)')
            else:
                direction=node['frame']['z_axis'] if kind=='prism' else node['axis'];distance=node['distance'] if kind=='prism' else node['dimensions'][2]
                lines.append(f'    return scad.extrude_rsolid(profile=face, direction={vector(direction)}, distance={fmt_literal(distance)}*profile_scale)')
        emitter.profiles.append('\n'.join(lines));emitter.metadata['symbols'][name]=dict(kind='tool_function',ir_ids=[step],csg_node=key)
    lines=[f'def {feature_name}(bodies, *, origin, x_axis, y_axis, profile_scale):','    """Execute a verified CSG recipe with fresh independent operands."""',
           '    _validate_frame(origin, x_axis, y_axis)',"    if not (0 < profile_scale < float('inf')):","        raise ValueError('profile_scale must be positive and finite')"]
    names={key:f'operand_{i:03d}' for i,key in enumerate(order,1)}
    for key in order:
        node=nodes[key];kind=node['kind']
        if kind in ('cut','intersect','union'):
            operands=', '.join(names[c] for c in node['inputs'])
            extra=', skip_non_intersecting=False, tracking_policy="graph"' if kind=='cut' else (', tracking_policy="graph"' if kind=='union' else '')
            lines.append(f'    {names[key]} = scad.{kind}_rsolid({operands}{extra})')
        else:lines.append(f'    {names[key]} = {tool_names[key]}({arguments})')
    lines += [f'    bodies.append({names[root]})','    return bodies']
    emitter.features.append('\n'.join(lines))
    emitter.metadata['symbols'][feature_name]=dict(kind='feature_function',ir_ids=[step])
    emitter.metadata['feature_groups'][feature_name]=[step]
    emitter.metadata['templates'][feature_name]=dict(profile_functions=profile_names,reuse_rule='one verified CSG recipe; no inferred original feature history')
    emitter.metadata['instances'].append(dict(source_solid_index=index,output_body_index=index-1,operation=step,template=feature_name,bindings=bindings,shared_design_parameters=False))
    emitter.metadata['placements'][f'BODY_{index:02d}']=dict(local_to_world=frame,application='all operands transformed together exactly once')
    emitter.metadata['preparation']['operations'][step]=dict(protocol='verified-bounded-csg-v1',adjustments=[],approximation='analytic tools and original cap curves; no sampled fitting',source_check=evidence['selected'])
    emitter.metadata['reconstruction_operations'].append(dict(id=step,operation='bounded_csg',inputs=feature['inputs'],depends_on=[],source_solid_indices=[index],output_body_indices=[index-1],recipe=params['recipe'],history_recovered=False))
    emitter.build_calls.append(f'    bodies = {feature_name}(bodies, '+', '.join(f'{k}={v}' for k,v in bindings.items())+')')
