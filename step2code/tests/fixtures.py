"""Geometry behavior fixtures retained from baseline 097478e."""
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut,BRepAlgoAPI_Fuse,BRepAlgoAPI_Common
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge,BRepBuilderAPI_MakeFace,BRepBuilderAPI_MakeWire,BRepBuilderAPI_Transform,BRepBuilderAPI_MakePolygon
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox,BRepPrimAPI_MakeCylinder,BRepPrimAPI_MakeCone,BRepPrimAPI_MakePrism,BRepPrimAPI_MakeSphere,BRepPrimAPI_MakeTorus
from OCP.GC import GC_MakeArcOfCircle
from OCP.gp import gp_Ax1,gp_Ax2,gp_Dir,gp_Pnt,gp_Trsf,gp_Vec
from step2code.kernel.runtime import compound
def cylinder(radius=10, height=5, origin=(0,0,0)):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*origin),gp_Dir(0,0,1)),radius,height).Shape()


def transform(shape):
    matrix=gp_Trsf()
    matrix.SetRotation(gp_Ax1(gp_Pnt(0,0,0),gp_Dir(1,2,3)),0.73)
    matrix.SetTranslationPart(gp_Vec(37,-21,59))
    return BRepBuilderAPI_Transform(shape,matrix,True).Shape()


def arc_prism(major=False):
    if major:
        start, middle, end = gp_Pnt(5,0,0), gp_Pnt(-5,0,0), gp_Pnt(0,5,0)
        wire=BRepBuilderAPI_MakeWire()
        wire.Add(BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(start,middle,end).Value()).Edge())
        wire.Add(BRepBuilderAPI_MakeEdge(end,start).Edge())
    else:
        a,b,m,c,d = gp_Pnt(0,0,0),gp_Pnt(20,0,0),gp_Pnt(25,5,0),gp_Pnt(20,10,0),gp_Pnt(0,10,0)
        wire=BRepBuilderAPI_MakeWire()
        for edge in [BRepBuilderAPI_MakeEdge(a,b).Edge(),
                     BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(b,m,c).Value()).Edge(),
                     BRepBuilderAPI_MakeEdge(c,d).Edge(),BRepBuilderAPI_MakeEdge(d,a).Edge()]:
            wire.Add(edge)
    return BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(wire.Wire()).Face(),gp_Vec(0,0,3)).Shape()


def fixtures():
    box=BRepPrimAPI_MakeBox(30,20,5).Shape()
    annulus=BRepAlgoAPI_Cut(cylinder(),cylinder(4)).Shape()
    hole=BRepAlgoAPI_Cut(box,cylinder(2,5,(7,8,0))).Shape()
    holes=BRepAlgoAPI_Cut(hole,cylinder(3,5,(20,12,0))).Shape()
    disjoint=compound([box,cylinder(4,8,(50,0,0))])
    overlapping=compound([box,BRepPrimAPI_MakeBox(gp_Pnt(25,0,0),30,20,5).Shape()])
    return {'box':box, 'cylinder':cylinder(), 'annulus':annulus,
            'box_with_hole':hole,'two_holes':holes, 'oblique_box':transform(box),
            'oblique_annulus':transform(annulus),'line_arc':arc_prism(),
            'major_arc':arc_prism(True),'disjoint':disjoint,'overlapping_bodies':overlapping,
            'offset_cylinder':cylinder(7,4,(600,-200,51))}



def unsupported_freeform():
    """An anisotropically transformed sphere is outside analytic CSG support."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_GTransform
    from OCP.gp import gp_GTrsf,gp_Mat
    transform=gp_GTrsf();transform.SetVectorialPart(gp_Mat(1.,0.,0.,0.,1.3,0.,0.,0.,.8))
    return BRepBuilderAPI_GTransform(BRepPrimAPI_MakeSphere(8).Shape(),transform,True).Shape()


def examples():
    stepped=BRepAlgoAPI_Fuse(cylinder(10,4),cylinder(5,5,(0,0,4))).Shape()
    shell=BRepAlgoAPI_Cut(BRepPrimAPI_MakeSphere(10).Shape(),BRepPrimAPI_MakeSphere(6).Shape()).Shape()
    return dict(sphere=BRepPrimAPI_MakeSphere(10).Shape(),cone=BRepPrimAPI_MakeCone(10,0,8).Shape(),
        frustum=BRepPrimAPI_MakeCone(10,5,8).Shape(),torus=BRepPrimAPI_MakeTorus(20,5).Shape(),
        stepped_shaft=stepped,hollow_sphere=shell,oblique_frustum=transform(BRepPrimAPI_MakeCone(10,5,8).Shape()))



def hybrid_examples():
    box=BRepPrimAPI_MakeBox(20,15,8).Shape()
    vertical=BRepAlgoAPI_Cut(box,cylinder(2,8,(5,4,0))).Shape()
    cross=BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0,11,4),gp_Dir(1,0,0)),1.5,13).Shape()
    crossed=BRepAlgoAPI_Cut(vertical,cross).Shape()
    blind=BRepAlgoAPI_Cut(box,cylinder(2,4,(6,7,4))).Shape()
    boss=BRepAlgoAPI_Fuse(box,cylinder(3,5,(14,7,8))).Shape()
    polygon=BRepBuilderAPI_MakePolygon()
    for p in ((-3,-2,0),(3,-2,0),(4,0,0),(3,2,0),(-3,2,0),(-4,0,0)):polygon.Add(gp_Pnt(*p))
    polygon.Close();prism=BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(polygon.Wire()).Face(),gp_Vec(0,0,3)).Shape()
    cone=BRepPrimAPI_MakeCone(5,3,3).Shape()
    tapered=BRepAlgoAPI_Common(prism,cone).Shape()
    tapered=BRepAlgoAPI_Cut(tapered,cylinder(.7,3)).Shape()
    return dict(crossed_blind_holes=crossed,blind_hole=blind,localized_boss=boss,tapered_polygon=tapered)



