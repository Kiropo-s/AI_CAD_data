"""Compose verified prism and full-revolution programs without changing CAD IR."""
from __future__ import annotations
import ast
import copy

from step2code.codegen import StepEmitter, CODE_STRUCTURE_SPEC, _HELPERS
from json2scapi.codegen.sketch_builder import fmt_literal


class _Rename(ast.NodeTransformer):
    def __init__(self, names): self.names=names
    def visit_Name(self,node):
        node.id=self.names.get(node.id,node.id);return node
    def visit_FunctionDef(self,node):
        node.name=self.names.get(node.name,node.name);self.generic_visit(node);return node


def _mapped(value,names):
    if isinstance(value,str):return names.get(value,value)
    if isinstance(value,list):return [_mapped(v,names) for v in value]
    if isinstance(value,dict):return {names.get(k,k):_mapped(v,names) for k,v in value.items()}
    return value


def _prism_payload(payload,step):
    entities=payload['entities'];needed=set()
    def collect(key):
        if key in needed:return
        needed.add(key);node=entities[key]
        for ref in node['inputs']:collect(ref)
        if node['op']=='sketch.profile':collect(node['params']['plane'])
    collect(step)
    return dict(ir_version='0.1',kind='part',units='mm',tolerance=payload['tolerance'],
                entities={key:copy.deepcopy(node) for key,node in entities.items() if key in needed},
                sequence=[key for key in payload['sequence'] if key in needed],results=[step],
                extensions=copy.deepcopy(payload['extensions']),unsupported=[])


class CompositeEmitter:
    """Use the existing STEP prism emitter and measured kernel meridian profiles."""
    def __init__(self,payload,evidence):
        if payload.get('units')!='mm' or payload.get('unsupported'):
            raise ValueError('Composite emitter requires complete millimeter reconstruction plans')
        self.payload=copy.deepcopy(payload);self.evidence=evidence
        self.metadata=dict(style='readable',readable_version=None,shared_readable_version=3,
            code_structure_spec=CODE_STRUCTURE_SPEC,emitter='step-composite-emitter-v1',
            input_format=payload.get('format','cad-ir-0.1'),
            symbols={},parameters={},profiles={},source_profiles={},placements={},templates={},
            instances=[],feature_groups={},reconstruction_operations=[],
            source_coverage=copy.deepcopy(evidence['source_coverage']),history_recovered=False,
            preparation={'scope':'per-operation; shared v3 preparation for prisms; measured kernel meridian sections for revolutions',
                         'operations':{}})
        self.constants=[];self.profiles=[];self.features=[];self.build_calls=[];self.polyline=None
        for index,feature_evidence in enumerate(evidence['features'],1):
            step=feature_evidence['operation_id'];node=payload['entities'][step]
            if node['op']=='feature.extrude': self._prism(index,step,feature_evidence)
            elif node['op']=='feature.revolve': self._revolution(index,step,feature_evidence)
            elif node['op']=='feature.csg':
                from step2code.csg_codegen import add_csg
                add_csg(self,index,step,feature_evidence)
            else:raise ValueError('Unsupported reconstruction operation: '+node['op'])

    def _prism(self,index,step,evidence):
        emitter=StepEmitter(_prism_payload(self.payload,step),
            dict(features=[evidence],source_coverage=self.evidence['source_coverage']))
        code=emitter.emit();tree=ast.parse(code)
        names={name:name.replace('FEATURE_01_',f'FEATURE_{index:02d}_').replace('BODY_01_',f'BODY_{index:02d}_')
               for name in emitter.metadata['parameters']}
        names['BODY_01']=f'BODY_{index:02d}'
        names.update(make_profile_01=f'make_profile_{index:02d}',apply_feature_01=f'apply_feature_{index:02d}')
        tree=ast.fix_missing_locations(_Rename(names).visit(tree))
        for node in tree.body:
            if isinstance(node,ast.Assign) and not any(isinstance(t,ast.Name) and t.id=='BODIES' for t in node.targets):
                self.constants.append(ast.unparse(node))
            if isinstance(node,ast.FunctionDef):
                if node.name.startswith('make_profile_'):self.profiles.append(ast.unparse(node))
                elif node.name.startswith('apply_feature_'):self.features.append(ast.unparse(node))
                elif node.name=='_add_closed_polyline':self.polyline=ast.unparse(node)
        meta=_mapped(emitter.metadata,names)
        for key in ('symbols','parameters','profiles','source_profiles','placements','templates','feature_groups'):
            self.metadata[key].update(meta[key])
        # Preparation remains attached to the actual operation, not a fake revolve IR.
        self.metadata['preparation']['operations'][step]=meta['preparation']
        for key,node in emitter.payload['entities'].items():self.payload['entities'][key]=node
        instance=meta['instances'][0]
        instance.update(source_solid_index=index,output_body_index=index-1)
        self.metadata['instances'].append(instance)
        operation=meta['reconstruction_operations'][0]
        operation.update(source_solid_indices=[index],output_body_indices=[index-1])
        self.metadata['reconstruction_operations'].append(operation)
        self.build_calls.append(f'    bodies = apply_feature_{index:02d}(bodies, '+
                                ', '.join(f'{k}={v}' for k,v in instance['bindings'].items())+')')

    def _revolution(self,index,step,evidence):
        entities=self.payload['entities'];feature=entities[step]
        params=feature['params']
        if params.get('operation')!='new' or params.get('angle_deg')!=360 or params.get('profile_scale')!=1:
            raise ValueError('Only a new full revolution with source profile scale 1 is supported')
        frame=evidence['coordinate_frame']['selected_frame']
        if params.get('section_frame')!=frame:
            raise ValueError('Reconstruction plan and measured section frames differ')
        feature_name=f'apply_feature_{index:02d}'
        bindings={key:f'BODY_{index:02d}_{key.upper()}'+('_MM' if key=='origin' else '')
                  for key in ('origin','x_axis','y_axis')}
        bindings['profile_scale']=f'FEATURE_{index:02d}_PROFILE_SCALE'
        for key,name in bindings.items():
            value=1. if key=='profile_scale' else frame[key]
            self.constants.append(f'{name} = {fmt_literal(value)}')
            item=dict(value=value,units='mm' if key=='origin' else '1',
                quantity='uniform_scale' if key=='profile_scale' else ('position' if key=='origin' else 'direction'),
                origin_kind='inferred',edit_policy='independent',expression=None,
                constraints='positive finite scale; finite origin; paired axes orthonormal',
                argument=key,bindings=[dict(ir_id=step,path='params.profile_scale' if key=='profile_scale' else 'params.section_frame.'+key)],
                evidence_refs=[f'recognition.features[{index-1}].coordinate_frame',f'recognition.features[{index-1}].selected'],
                design_parameter_recovered=False)
            self.metadata['parameters'][name]=item;self.metadata['symbols'][name]=item
        profile_names=[]
        for section,profile_id in enumerate(feature['inputs'],1):
            name=f'make_profile_{index:02d}'+(f'_section_{section:02d}' if section>1 else '')
            profile_names.append(name)
            lines=[f'def {name}(*, origin, x_axis, y_axis, profile_scale):',
                   '    """Replay the measured kernel meridian; scale about the selected axis origin."""',
                   '    normal = _validate_frame(origin, x_axis, y_axis)']
            profile=entities[profile_id];wire_names=[]
            for loop_index,loop_id in enumerate(profile['inputs'],1):
                edges=[]
                for edge_index,curve_id in enumerate(entities[loop_id]['inputs'],1):
                    curve=entities[curve_id];params=curve['params'];edge=f'edge_{loop_index:02d}_{edge_index:03d}'
                    def point(key):return f'_section_point(origin, x_axis, y_axis, {fmt_literal(params[key])}, profile_scale)'
                    if curve['op']=='sketch.line':
                        call=f'scad.make_line_redge(start={point("start")}, end={point("end")})'
                    elif curve['op']=='sketch.arc':
                        call=f'scad.make_three_point_arc_redge(start={point("start")}, middle={point("mid")}, end={point("end")})'
                    elif curve['op']=='sketch.bspline':
                        lines += [f'    {edge} = scad.make_spline_redge(', '        control_points=[']
                        lines += [f'            _section_point(origin, x_axis, y_axis, {fmt_literal(p)}, profile_scale),'
                                  for p in params['control_points']]
                        lines += ['        ],',f'        degree={params["degree"]},',
                                  f'        knots={fmt_literal(params["knots"])},',
                                  f'        multiplicities={fmt_literal(params["multiplicities"])},',
                                  f'        weights={fmt_literal(params["weights"])},',
                                  f'        periodic={params["periodic"]!r},', '    )']
                        edges.append(edge)
                        continue
                    elif curve['op']=='sketch.circle':
                        # Two exact semicircles avoid a closed-edge seam ambiguity
                        # in full toroidal revolutions and STEP round trips.
                        cx,cy=params['center'];radius=params['radius']
                        points=((cx+radius,cy),(cx,cy+radius),(cx-radius,cy),(cx,cy-radius))
                        def circle_point(i):
                            return f'_section_point(origin, x_axis, y_axis, {fmt_literal(points[i])}, profile_scale)'
                        for half,(a,b,c) in enumerate(((0,1,2),(2,3,0)),1):
                            arc=f'{edge}_half_{half}'
                            lines.append(f'    {arc} = scad.make_three_point_arc_redge(start={circle_point(a)}, middle={circle_point(b)}, end={circle_point(c)})')
                            edges.append(arc)
                        continue
                    else:raise ValueError('Unsupported meridian curve: '+curve['op'])
                    lines.append(f'    {edge} = {call}');edges.append(edge)
                wire=f'wire_{loop_index:02d}';wire_names.append(wire)
                lines.append(f'    {wire} = scad.make_wire_from_edges_rwire(edges=[{", ".join(edges)}])')
            lines.append(f'    face = scad.make_face_from_wires_rface(outer_wire={wire_names[0]}, inner_wires=[{", ".join(wire_names[1:])}], normal=normal)')
            lines.append('    return (face,)')
            self.profiles.append('\n'.join(lines))
            self.metadata['profiles'][name]=dict(ir_ids=[profile_id],derived_section=True,returns='ordered_face_tuple',
                curves=[{'ir_id':curve_id,'params':entities[curve_id]['params'],'edit_policy':'fixed'}
                        for loop_id in profile['inputs'] for curve_id in entities[loop_id]['inputs']])
            self.metadata['symbols'][name]=dict(kind='profile_function',ir_ids=[profile_id])
        lines=[f'def {feature_name}(bodies, *, origin, x_axis, y_axis, profile_scale):',
               '    """Revolve explicit meridian faces through 360 degrees and unite their solids."""',
               '    _validate_frame(origin, x_axis, y_axis)',
               "    if not (0 < profile_scale < float('inf')):",
               "        raise ValueError('profile_scale must be positive and finite')",'    tools = []']
        for name in profile_names:
            lines.append(f'    faces = {name}(origin=origin, x_axis=x_axis, y_axis=y_axis, profile_scale=profile_scale)')
            lines += ['    for face in faces:',
                      '        tools.append(scad.revolve_rsolid(profile=face, axis=y_axis, origin=origin, angle=360))']
        lines+=['    body = tools[0] if len(tools) == 1 else scad.union_rsolid(tools, tracking_policy="graph")',
                '    bodies.append(body)','    return bodies']
        self.features.append('\n'.join(lines))
        self.metadata['symbols'][feature_name]=dict(kind='feature_function',ir_ids=[step])
        self.metadata['feature_groups'][feature_name]=[step]
        self.metadata['templates'][feature_name]=dict(profile_functions=profile_names,reuse_rule='one explicit verified reconstruction; no inferred design relationships')
        self.metadata['instances'].append(dict(source_solid_index=index,output_body_index=index-1,operation=step,
                                               template=feature_name,bindings=bindings,shared_design_parameters=False))
        self.metadata['placements'][f'BODY_{index:02d}']=dict(local_to_world=frame,
            application='meridian points once; revolution axis follows y_axis',evidence_refs=[f'recognition.features[{index-1}].coordinate_frame'])
        self.metadata['reconstruction_operations'].append(dict(id=step,operation='full_revolution',inputs=feature['inputs'],
            depends_on=[],source_solid_indices=[index],output_body_indices=[index-1],
            section_union=feature['params']['section_union'],history_recovered=False))
        self.metadata['preparation']['operations'][step]=dict(protocol='verified-kernel-meridian-v1',adjustments=[],
            approximation='no converter fitting; kernel B-spline section replay; closed circles emitted as two exact semicircles',section_origin='boolean intersection with bounded radial half-plane',
            source_check=evidence['selected'])
        self.build_calls.append(f'    bodies = {feature_name}(bodies, '+', '.join(f'{k}={v}' for k,v in bindings.items())+')')

    def emit(self):
        blocks=['"""STEP analytic reconstruction (step-readable-v1); inferred operations, no source history."""',
                'import simplecadapi as scad','\n'.join(self.constants),'BODIES = None',*self.profiles,*self.features,
                '\n'.join(['def build():','    """Rebuild all verified source solids in world coordinates."""',
                    '    bodies = []',*self.build_calls,'    global BODIES','    BODIES = list(bodies)','    return _as_model(bodies)'])]
        if self.polyline:blocks.append(self.polyline)
        blocks += [_HELPERS,'''def _section_point(origin, x_axis, y_axis, point, scale):
    """Transform one explicit local meridian point into the selected world frame."""
    x, y = point
    return tuple(origin[i] + scale * (x*x_axis[i] + y*y_axis[i]) for i in range(3))''']
        if any(n['op']=='feature.csg' for n in self.payload['entities'].values()):
            from step2code.csg_codegen import HELPERS
            blocks.append(HELPERS)
        code='\n\n\n'.join(blocks)+'\n';tree=ast.parse(code)
        functions=[n for n in tree.body if isinstance(n,ast.FunctionDef)]
        self.metadata['structure']=dict(lines=len(code.splitlines()),function_count=len(functions),
            build_lines=next(n.end_lineno-n.lineno+1 for n in functions if n.name=='build'),
            max_function_lines=max(n.end_lineno-n.lineno+1 for n in functions),
            feature_count=len(self.metadata['instances']),profile_count=len(self.metadata['profiles']),
            template_count=len(self.metadata['templates']),instance_count=len(self.metadata['instances']),
            independent_parameter_count=len(self.metadata['parameters']),polyline_helper=bool(self.polyline),
            operation_counts={op:sum(r['operation']==op for r in self.metadata['reconstruction_operations'])
                              for op in ('new_prism','full_revolution','bounded_csg')
                              if op!='bounded_csg' or any(r['operation']=='bounded_csg' for r in self.metadata['reconstruction_operations'])})
        return code
