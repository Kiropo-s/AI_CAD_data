"""Exact planar curve replay and directed-loop checks, independent of SCAPI."""
import math
from dataclasses import dataclass
from scipy.integrate import quad
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
from OCP.Geom import Geom_BSplineCurve, Geom_Ellipse
from OCP.TColgp import TColgp_Array1OfPnt
from OCP.TColStd import TColStd_Array1OfReal, TColStd_Array1OfInteger
from OCP.gp import gp_Pnt, gp_Vec, gp_Ax2, gp_Dir, gp_Circ, gp_Elips
from OCP.TopoDS import TopoDS


@dataclass
class Curve:
    edges: tuple
    start: tuple
    end: tuple
    signed_area: float


def spline(a):
    poles=TColgp_Array1OfPnt(1,len(a['poles']))
    knots=TColStd_Array1OfReal(1,len(a['knots']))
    mult=TColStd_Array1OfInteger(1,len(a['multiplicities']))
    weights=TColStd_Array1OfReal(1,len(a['weights']))
    for i,p in enumerate(a['poles'],1):poles.SetValue(i,gp_Pnt(*p,0))
    for i,v in enumerate(a['knots'],1):knots.SetValue(i,v)
    for i,v in enumerate(a['multiplicities'],1):mult.SetValue(i,v)
    for i,v in enumerate(a['weights'],1):weights.SetValue(i,v)
    result=Geom_BSplineCurve(poles,weights,knots,mult,a['degree'],a['periodic'])
    # Verify true kernel domain, not just outer knot range.
    if not result.FirstParameter()<=a['trim'][0]<a['trim'][1]<=result.LastParameter():
        raise ValueError('B-spline trim outside active kernel domain')
    result.Segment(*a['trim'],1e-12)
    if a['reversed']:result.Reverse()
    return result


def spline_args(curve):
    return dict(poles=[list(curve.Pole(i).Coord()[:2]) for i in range(1,curve.NbPoles()+1)],
                degree=curve.Degree(), knots=[curve.Knot(i) for i in range(1,curve.NbKnots()+1)],
                multiplicities=[curve.Multiplicity(i) for i in range(1,curve.NbKnots()+1)],
                weights=[curve.Weight(i) for i in range(1,curve.NbPoles()+1)], periodic=curve.IsPeriodic())


def make_curve(op,a):
    if op=='curve.line2':
        start,end=tuple(a['start']),tuple(a['end'])
        edge=BRepBuilderAPI_MakeEdge(gp_Pnt(*start,0),gp_Pnt(*end,0)).Edge()
        return Curve((edge,),start,end,(start[0]*end[1]-end[0]*start[1])/2)
    if op=='curve.ellipse2':
        cx, cy = a['center']; angle = a['major_axis_angle']
        axis = gp_Ax2(gp_Pnt(cx, cy, 0), gp_Dir(0, 0, 1),
                      gp_Dir(math.cos(angle), math.sin(angle), 0))
        ellipse = Geom_Ellipse(gp_Elips(axis, a['major_radius'], a['minor_radius']))
        edge = BRepBuilderAPI_MakeEdge(ellipse).Edge()
        if a['winding']=='cw': edge = TopoDS.Edge_s(edge.Reversed())
        start = (cx + a['major_radius']*math.cos(angle), cy + a['major_radius']*math.sin(angle))
        return Curve((edge,), start, start, math.copysign(math.pi*a['major_radius']*a['minor_radius'],
                                                           1 if a['winding']=='ccw' else -1))
    if op=='curve.bspline2':
        c=spline(a);lo,hi=c.FirstParameter(),c.LastParameter()
        def integral(t):
            p,v=gp_Pnt(),gp_Vec();c.D1(t,p,v)
            return (p.X()*v.Y()-p.Y()*v.X())/2
        signed=quad(integral,lo,hi,epsabs=1e-10,limit=300)[0]
        return Curve((BRepBuilderAPI_MakeEdge(c).Edge(),),tuple(c.Value(lo).Coord()[:2]),
                     tuple(c.Value(hi).Coord()[:2]),signed)
    cx,cy=a['center'];r=a['radius']
    start=a.get('start_angle',0.)
    sweep=a.get('sweep_angle',(2*math.pi if a.get('winding')=='ccw' else -2*math.pi))
    def point(t):return (cx+r*math.cos(t),cy+r*math.sin(t))
    circle=gp_Circ(gp_Ax2(gp_Pnt(cx,cy,0),gp_Dir(0,0,1)),r)
    boundaries=[start,start+sweep] if op=='curve.arc2' else [start,start+sweep/2,start+sweep]
    edges=[]
    for lo,hi in zip(boundaries,boundaries[1:]):
        edge=BRepBuilderAPI_MakeEdge(circle,min(lo,hi),max(lo,hi)).Edge()
        if hi<lo:edge=TopoDS.Edge_s(edge.Reversed())
        edges.append(edge)
    end=start+sweep
    signed=(r*r*sweep+cx*r*(math.sin(end)-math.sin(start))-cy*r*(math.cos(end)-math.cos(start)))/2
    return Curve(tuple(edges),point(start),point(end),signed)
