"""Direct source emission from a typed DAG. No AST renaming or plan interpreter."""
import ast
import copy
import hashlib
import json
import math
from dataclasses import dataclass
from step2code.plan import Plan, PlanError
from step2code.plan.graph import topological_order, reachable
from step2code.plan.model import arguments
from step2code.plan.registry import OPS
from step2code.kernel.curves import spline, spline_args
from .templates import HELPERS

CODE_STRUCTURE_SPEC='step-readable-2'
COMPILER_VERSION='scapi-plan-1'


@dataclass
class Compilation:
    code: str
    symbols: dict


class Compiler:
    def __init__(self,plan,execution):
        self.plan=plan;self.doc=plan.data;self.nodes=self.doc['nodes'];self.execution=execution
        self.lines=[];self.source_map={};self.functions=[];self.profile_names={};self.templates={}
        self.parameters={key:f'P{i:03d}_{key.upper()}' for i,key in enumerate(sorted(self.doc['parameters']),1)}
        self.check_capabilities()

    def check_capabilities(self):
        for key,n in self.nodes.items():
            if OPS[n['op']].result=='Region3':
                count=self.execution.signatures[key]['solid_count']
                if count!=1:
                    raise PlanError('backend.unsupported_intermediate','/nodes/'+key,
                        'SCAPI Solid requires exactly one intermediate solid',dict(solid_count=count))
            # Exact spline segmentation is compile-time lowering; editable spline definitions require a backend trim API.
            if n['op']=='curve.bspline2' and any('param' in b and self.doc['parameters'][b['param']]['editability']=='independent'
                                               for b in n['args'].values()):
                raise PlanError('backend.editable_spline','/nodes/'+key,'SCAPI does not expose exact runtime spline trim')
            if n['op'].startswith('curve.') and any('param' in b and self.doc['parameters'][b['param']]['editability']=='independent'
                                                   for b in n['args'].values()):
                raise PlanError('backend.editable_profile','/nodes/'+key,
                    'Editable profile coordinates require topology validation at runtime; use body scale in this release')

    def arg(self,key,name):
        b=self.nodes[key]['args'][name]
        value=self.parameters[b['param']] if 'param' in b else repr(b['literal'])
        sig=OPS[self.nodes[key]['op']].args[name]
        if sig.type=='number':
            guard={'positive':'_positive','nonnegative':'_nonnegative'}.get(sig.rule,'_finite_number')
            return f'{guard}({value}, {key+"."+name!r})'
        if sig.type=='point3':return f'_point3({value}, {key+"."+name!r})'
        return value

    def add_function(self,name,lines,mapping=None):
        first=len(self.lines)+1
        self.lines.extend(lines+['',''])
        self.functions.append(dict(name=name,line=first,end=first+len(lines)-1))
        for key,(start,end) in (mapping or {}).items():
            self.source_map.setdefault(key,[]).append(dict(function=name,line=first+start,end=first+end))

    def profile(self,key):
        p=self.nodes[key];loops=p['inputs']['outer']+p['inputs']['holes']
        identity=[]
        for loop in loops:
            identity.append([self.nodes[c] for c in self.nodes[loop]['inputs']['curves']])
        fingerprint=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        if fingerprint in self.templates:
            name=self.templates[fingerprint];self.profile_names[key]=name
            self.source_map.setdefault(key,[]).append(dict(function=name,reused=True))
            return name
        name=f'make_profile_{len(self.templates)+1:03d}'
        self.templates[fingerprint]=name;self.profile_names[key]=name
        lines=[f'def {name}(*, meridian=False):','    """Build exact local profile curves and its explicit holes."""']
        wire_names=[];mapping={}
        for li,loop in enumerate(loops,1):
            curves=list(self.nodes[loop]['inputs']['curves'])
            reverse=self.execution.nodes[loop].signed_area<0
            if reverse:curves.reverse()
            edge_names=[]
            for ei,curve in enumerate(curves,1):
                n=self.nodes[curve];op=n['op'];a=arguments(self.doc,n)
                edge=f'edge_{li:02d}_{ei:03d}';start=len(lines)
                if op=='curve.line2':
                    first,last=(a['end'],a['start']) if reverse else (a['start'],a['end'])
                    lines.append(f'    {edge} = scad.make_line_redge(start=_point({first!r}, meridian), end=_point({last!r}, meridian))')
                    edge_names.append(edge)
                elif op in ('curve.arc2','curve.circle2'):
                    angle=a.get('start_angle',0.);sweep=a.get('sweep_angle',2*math.pi*(1 if a.get('winding')=='ccw' else -1))
                    if reverse:angle+=sweep;sweep=-sweep
                    spans=[(angle,sweep)] if op=='curve.arc2' else [(angle,sweep/2),(angle+sweep/2,sweep/2)]
                    for hi,(t,theta) in enumerate(spans,1):
                        en=edge if len(spans)==1 else edge+f'_half_{hi}'
                        points=[f'_arc_point({a["center"]!r}, {a["radius"]!r}, {v!r}, meridian)' for v in (t,t+theta/2,t+theta)]
                        lines.extend([f'    {en} = scad.make_three_point_arc_redge(',f'        start={points[0]},',f'        middle={points[1]},',f'        end={points[2]},','    )'])
                        edge_names.append(en)
                elif op=='curve.ellipse2':
                    angle=a['major_axis_angle']
                    normal=('((0, 1, 0) if meridian else (0, 0, -1))'
                            if a['winding']=='cw' else
                            '((0, -1, 0) if meridian else (0, 0, 1))')
                    major_direction=(f'(math.cos({angle}), 0, math.sin({angle})) if meridian else '
                                     f'(math.cos({angle}), math.sin({angle}), 0)')
                    lines.append(f'    {edge} = scad.make_ellipse_redge(')
                    lines.extend([f'        center=_point({a["center"]!r}, meridian),',
                        f'        major_radius={a["major_radius"]!r}, minor_radius={a["minor_radius"]!r},',
                        f'        normal=({normal}), major_direction={major_direction},', '    )'])
                    edge_names.append(edge)
                elif op=='curve.bspline2':
                    c=spline(a)
                    if reverse:c.Reverse()
                    v=spline_args(c)
                    lines.extend([f'    {edge} = scad.make_spline_redge(',
                        f'        control_points=[_point(p, meridian) for p in {v["poles"]!r}],',
                        f'        degree={v["degree"]!r}, knots={v["knots"]!r},',
                        f'        multiplicities={v["multiplicities"]!r}, weights={v["weights"]!r},',
                        f'        periodic={v["periodic"]!r},','    )'])
                    edge_names.append(edge)
                mapping[curve]=(start,len(lines)-1)
            wire=f'wire_{li:02d}';wire_names.append(wire);start=len(lines)
            lines.append(f'    {wire} = scad.make_wire_from_edges_rwire(edges=[{", ".join(edge_names)}])')
            mapping[loop]=(start,len(lines)-1)
        start=len(lines)
        lines.append(f'    return scad.make_face_from_wires_rface(outer_wire={wire_names[0]}, inner_wires=[{", ".join(wire_names[1:])}], normal=(0, -1, 0) if meridian else (0, 0, 1))')
        mapping[key]=(start,start);self.add_function(name,lines,mapping)
        return name

    def body(self,index,output):
        roots=reachable(self.nodes,[output['node']]);order=[k for k in topological_order(self.nodes) if k in roots]
        names={key:f'operand_{i:03d}' for i,key in enumerate((k for k in order if OPS[self.nodes[k]['op']].result=='Region3'),1)}
        fn=f'make_body_{index:03d}';lines=[f'def {fn}():','    """Reconstruct one independent output in world coordinates."""'];mapping={}
        for key in order:
            n=self.nodes[key];op=n['op'];inp=n['inputs'];a=lambda name:self.arg(key,name)
            if OPS[op].result!='Region3':continue
            value=names[key];start=len(lines)
            if op=='solid.extrude':call=f'scad.extrude_rsolid(profile={self.profile_names[inp["profile"][0]]}(), direction=(0, 0, 1), distance=_positive({a("distance")}, "distance"))'
            elif op=='solid.revolve':call=f'scad.revolve_rsolid(profile={self.profile_names[inp["profile"][0]]}(meridian=True), axis=(0, 0, 1), origin=(0, 0, 0), angle=math.degrees(_full_turn({a("angle")})))'
            elif op=='solid.box':call=f'scad.make_box_rsolid(width={a("width")}, height={a("depth")}, depth={a("height")}, bottom_face_center=(0, 0, 0))'
            elif op=='solid.cylinder':call=f'scad.make_cylinder_rsolid(radius={a("radius")}, height={a("height")}, bottom_face_center=(0, 0, 0), axis=(0, 0, 1))'
            elif op=='solid.cone':call=f'_cone({a("radius_bottom")}, {a("radius_top")}, {a("height")})'
            elif op=='solid.sphere':call=f'scad.make_sphere_rsolid(radius={a("radius")}, center=(0, 0, 0))'
            elif op=='solid.torus':
                # SCAPI 2.2 has no torus solid primitive; exact circular meridian lowering.
                r=value+'_minor_radius';R=value+'_major_radius';circle=value+'_circle';face=value+'_profile'
                lines.append(f'    {R}, {r} = _torus_sizes({a("major_radius")}, {a("minor_radius")})')
                lines.extend([f'    {circle} = scad.make_circle_rwire(center=({R}, 0, 0), radius={r}, normal=(0, -1, 0))',
                    f'    {face} = scad.make_face_from_wire_rface(wire={circle}, normal=(0, -1, 0))'])
                call=f'scad.revolve_rsolid(profile={face}, axis=(0, 0, 1), origin=(0, 0, 0), angle=360)'
            elif op=='boolean.union':call='scad.union_rsolid(['+', '.join(names[r] for r in inp['operands'])+'], tracking_policy="graph")'
            elif op=='boolean.intersect':call='scad.intersect_rsolid('+', '.join(names[r] for r in inp['operands'])+')'
            elif op=='boolean.cut':call=f'scad.cut_rsolid({names[inp["target"][0]]}, {names[inp["tool"][0]]}, skip_non_intersecting=False, tracking_policy="graph")'
            elif op=='shape.transform':
                frame=inp['frame'][0];m=', '.join(self.arg(frame,n) for n in ('origin','x_axis','y_axis'))
                call=f'_place({names[inp["shape"][0]]}, {m})'
                mapping[frame]=(start,start)
            elif op=='shape.uniform_scale':
                lines.append(f'    scale = _positive({a("factor")}, "scale")')
                call=f'scad.scale_rsolid({names[inp["shape"][0]]}, factors=(scale, scale, scale), center={a("center")})'
            else:raise AssertionError(op)
            lines.append(f'    {value} = {call}');mapping[key]=(start,len(lines)-1)
        lines.append(f'    return {names[output["node"]]}')
        self.add_function(fn,lines,mapping);return fn

    def emit(self):
        self.lines=['"""STEP Plan 1 reconstruction; measured geometry, inferred operations, no source history."""',
            'import math','import simplecadapi as scad','','# Independent parameters; dimensions use mm, angles use radians.']
        for key,name in self.parameters.items():
            p=self.doc['parameters'][key]
            self.lines.append(f'{name} = {p["value"]!r}  # {p["unit"]}; {p["editability"]}')
        self.lines+=['','BODIES = None','','']
        for key in topological_order(self.nodes):
            if self.nodes[key]['op']=='profile.face2':self.profile(key)
        functions=[self.body(i,o) for i,o in enumerate(self.doc['outputs'],1)]
        build=['def build():','    """Build fresh independent bodies, then wrap them at identity placement."""','    global BODIES',
               '    BODIES = [']+[f'        {name}(),' for name in functions]+['    ]','    return _as_model(BODIES)']
        self.add_function('build',build)
        self.lines.extend([HELPERS]);code='\n'.join(self.lines)+'\n';ast.parse(code)
        return Compilation(code,dict(format='step-symbols',version=1,compiler=COMPILER_VERSION,
            plan_hash=self.plan.sha256,code_sha256=hashlib.sha256(code.encode()).hexdigest(),
            parameters={key:dict(symbol=name,**self.doc['parameters'][key]) for key,name in self.parameters.items()},
            source_map=self.source_map,outputs=self.doc['outputs'],functions=self.functions,
            structure=dict(lines=len(code.splitlines()),profile_count=len(self.templates),body_count=len(functions),
                           build_lines=len(build),max_function_lines=max(f['end']-f['line']+1 for f in self.functions)),
            code_structure_spec=CODE_STRUCTURE_SPEC,history_recovered=False))
