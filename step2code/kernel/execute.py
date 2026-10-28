"""Typed DAG interpreter using OCP only. No source STEP or sidecar is consulted."""
from dataclasses import dataclass
import math
import numpy as np
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire, BRepBuilderAPI_MakeFace, BRepBuilderAPI_Transform
from OCP.BRepPrimAPI import (BRepPrimAPI_MakePrism, BRepPrimAPI_MakeRevol, BRepPrimAPI_MakeBox,
    BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeCone, BRepPrimAPI_MakeSphere, BRepPrimAPI_MakeTorus)
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse, BRepAlgoAPI_Cut, BRepAlgoAPI_Common
from OCP.BRepCheck import BRepCheck_Analyzer, BRepCheck_Wire, BRepCheck_NoError
from OCP.BRepLib import BRepLib
from OCP.TopoDS import TopoDS, TopoDS_Edge
from OCP.gp import gp_Vec, gp_Pnt, gp_Dir, gp_Ax1, gp_Trsf
from step2code.plan.model import Plan, PlanError, arguments
from step2code.plan.graph import topological_order
from step2code.plan.registry import OPS
from step2code.verification.metrics import solid_count, volume
from .curves import make_curve


@dataclass
class Loop:
    curves: tuple
    wire: object
    signed_area: float


@dataclass
class Execution:
    bodies: list
    nodes: dict
    signatures: dict


def rigid_matrix(a):
    result=np.eye(4)
    result[:3,0]=a['x_axis'];result[:3,1]=a['y_axis']
    result[:3,2]=np.cross(a['x_axis'],a['y_axis']);result[:3,3]=a['origin']
    return result


def transform(shape,matrix):
    t=gp_Trsf();t.SetValues(*[float(v) for v in np.array(matrix)[:3,:].reshape(-1)])
    return BRepBuilderAPI_Transform(shape,t,True).Shape()


def oriented_wire(loop,positive):
    return loop.wire if (loop.signed_area>0)==positive else TopoDS.Wire_s(loop.wire.Reversed())


def signature(shape):
    from .signatures import shape_signature
    return shape_signature(shape)


def execute(plan: Plan, *, events=None, capture_signatures=True, connection_tolerance=1e-6):
    from step2code.plan.validation import validate
    doc=plan.data;validate(doc);values={};signatures={}
    def emit(event,key,**extra):
        if events:events(event=event,phase='native.execute',node_id=key,**extra)
    for key in topological_order(doc['nodes']):
        n=doc['nodes'][key];op=n['op'];a=arguments(doc,n)
        inputs={role:[values[ref] for ref in refs] for role,refs in n['inputs'].items()}
        emit('start',key,operation=op)
        try:
            if op.startswith('curve.'):
                value=make_curve(op,a)
            elif op=='wire.loop2':
                curves=inputs['curves'];maker=BRepBuilderAPI_MakeWire()
                gaps=[math.dist(first.end,second.start) for first,second in zip(curves,curves[1:]+curves[:1])]
                if max(gaps)>connection_tolerance:
                    raise PlanError('preparation.loop_gap','/nodes/'+key, 'Loop endpoints disagree',dict(gaps_mm=gaps))
                for curve in curves:
                    for edge in curve.edges:maker.Add(edge)
                if not maker.IsDone() or not maker.Wire().Closed():
                    raise PlanError('preparation.topology_conflict','/nodes/'+key,'Kernel wire is not closed')
                value=Loop(tuple(curves),maker.Wire(),sum(c.signed_area for c in curves))
                if abs(value.signed_area)<=1e-14:
                    raise PlanError('preparation.degenerate_loop','/nodes/'+key,'Zero signed loop area')
            elif op=='profile.face2':
                outer=inputs['outer'][0];holes=inputs['holes']
                maker=BRepBuilderAPI_MakeFace(oriented_wire(outer,True),True)
                for hole in holes:maker.Add(oriented_wire(hole,False))
                if not maker.IsDone():raise ValueError('Face construction failed')
                value=maker.Face()
                for role,loops in (('outer',[outer]),('holes',holes)):
                    for i,loop in enumerate(loops):
                        e1,e2=TopoDS_Edge(),TopoDS_Edge()
                        status=BRepCheck_Wire(loop.wire).SelfIntersect(value,e1,e2)
                        if status!=BRepCheck_NoError:
                            raise PlanError('preparation.topology_conflict','/nodes/'+key+'/inputs/'+role+'/'+str(i),
                                'Kernel wire self-intersection check failed',dict(kernel_status=str(status),
                                edge_pair=[next((j for j,c in enumerate(loop.curves) if any(e.IsSame(x) for x in c.edges)),None)
                                           if not e.IsNull() else None for e in (e1,e2)]))
                if not BRepCheck_Analyzer(value).IsValid():
                    raise PlanError('preparation.topology_conflict','/nodes/'+key,'Invalid kernel profile face')
            elif op=='frame.rigid3':value=rigid_matrix(a)
            elif op=='solid.extrude':value=BRepPrimAPI_MakePrism(inputs['profile'][0],gp_Vec(0,0,a['distance']),True).Shape()
            elif op=='solid.revolve':
                # Profile XY=(u,v) becomes (u,0,v); full sweep around local +Z.
                m=[[1,0,0,0],[0,0,-1,0],[0,1,0,0],[0,0,0,1]]
                face=transform(inputs['profile'][0],m)
                value=BRepPrimAPI_MakeRevol(face,gp_Ax1(gp_Pnt(0,0,0),gp_Dir(0,0,1)),a['angle'],True).Shape()
            elif op=='solid.box':value=BRepPrimAPI_MakeBox(gp_Pnt(-a['width']/2,-a['depth']/2,0),a['width'],a['depth'],a['height']).Shape()
            elif op=='solid.cylinder':value=BRepPrimAPI_MakeCylinder(a['radius'],a['height']).Shape()
            elif op=='solid.cone':
                if abs(a['radius_bottom']-a['radius_top'])<=1e-14:
                    value=BRepPrimAPI_MakeCylinder(a['radius_bottom'],a['height']).Shape()
                else:value=BRepPrimAPI_MakeCone(a['radius_bottom'],a['radius_top'],a['height']).Shape()
            elif op=='solid.sphere':value=BRepPrimAPI_MakeSphere(a['radius']).Shape()
            elif op=='solid.torus':value=BRepPrimAPI_MakeTorus(a['major_radius'],a['minor_radius']).Shape()
            elif op.startswith('boolean.'):
                operands=inputs.get('operands',inputs.get('target',[])+inputs.get('tool',[]))
                value=operands[0]
                constructor={'boolean.union':BRepAlgoAPI_Fuse,'boolean.cut':BRepAlgoAPI_Cut,
                             'boolean.intersect':BRepAlgoAPI_Common}[op]
                for operand in operands[1:]:
                    builder=constructor(value,operand);builder.Build()
                    if not builder.IsDone():raise ValueError('Boolean operation failed')
                    value=builder.Shape()
            elif op=='shape.transform':value=transform(inputs['shape'][0],inputs['frame'][0])
            elif op=='shape.uniform_scale':
                t=gp_Trsf();t.SetScale(gp_Pnt(*a['center']),a['factor'])
                value=BRepBuilderAPI_Transform(inputs['shape'][0],t,True).Shape()
            else:raise AssertionError(op)
            if OPS[op].result=='Region3':
                if value.IsNull() or not BRepCheck_Analyzer(value).IsValid():raise ValueError('Invalid native region')
                if solid_count(value)==1:
                    from OCP.TopExp import TopExp_Explorer
                    from OCP.TopAbs import TopAbs_SOLID
                    # Orient fresh region solids; no source topology repair.
                    explorer=TopExp_Explorer(value,TopAbs_SOLID)
                    BRepLib.OrientClosedSolid_s(TopoDS.Solid_s(explorer.Current()))
                if capture_signatures:signatures[key]=signature(value)
            values[key]=value;emit('end',key,operation=op)
        except Exception as exc:
            emit('fail',key,reason_code=getattr(exc,'code','native.operation_failure'),error=str(exc))
            if isinstance(exc,PlanError):raise
            raise PlanError('native.operation_failure','/nodes/'+key,str(exc)) from exc
    bodies=[values[o['node']] for o in doc['outputs']]
    for index,body in enumerate(bodies):
        if solid_count(body)!=1 or not math.isfinite(volume(body)) or volume(body)<=0:
            raise PlanError('native.output_contract','/outputs/'+str(index),'Expected one valid positive-volume solid')
    return Execution(bodies,values,signatures)
