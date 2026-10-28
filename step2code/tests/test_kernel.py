"""Exact OCP/SCAPI cross-backend geometry and topology regressions."""
import math
from pathlib import Path
import unittest
from OCP.BRepCheck import BRepCheck_Analyzer
from step2code.plan import Plan, PlanError, empty_document, node
from step2code.plan.preparation import prepare
from step2code.kernel.execute import execute
from step2code.verification.metrics import volume,bbox,symmetric_difference_volume
from step2code.recognition import recognize, RecognitionOptions, UnsupportedGeometry
from helpers import box_document,generated
from fixtures import fixtures,examples,hybrid_examples,unsupported_freeform


def profile_doc(curves):
    d=empty_document();d['nodes'].update(curves)
    d['nodes']['loop']=node('wire.loop2',dict(curves=list(curves)))
    d['nodes']['profile']=node('profile.face2',dict(outer=['loop'],holes=[]))
    d['nodes']['prism']=node('solid.extrude',dict(profile=['profile']),distance=2.)
    d['outputs']=[dict(id='body',node='prism',expected_kind='solid')]
    return d


class KernelTests(unittest.TestCase):
    def equivalent(self,plan,source=None):
        native=execute(plan).bodies;ns,bodies,compiled=generated(plan)
        for a,b in zip(native,bodies):
            self.assertTrue(BRepCheck_Analyzer(b).IsValid())
            self.assertLessEqual(abs(volume(a)-volume(b))/volume(a),1e-7)
            self.assertLessEqual(symmetric_difference_volume(a,b)/volume(a),1e-7)
            self.assertLessEqual(max(abs(x-y) for x,y in zip(bbox(a),bbox(b))),1e-5)
        if source is not None:
            self.assertLessEqual(symmetric_difference_volume(source,bodies[0])/volume(source),1e-7)
        return ns,bodies,compiled

    def test_all_primitive_dimensions(self):
        for op,args in [('solid.box',dict(width=2,depth=3,height=4)),('solid.cylinder',dict(radius=3,height=7)),
            ('solid.cone',dict(radius_bottom=3,radius_top=1,height=7)),('solid.sphere',dict(radius=3)),
            ('solid.torus',dict(major_radius=8,minor_radius=2))]:
            with self.subTest(op=op):
                d=box_document();d['nodes']['box']=node(op,**args);self.equivalent(Plan.from_dict(d))

    def test_prism_behavior_fixtures(self):
        for name,source in fixtures().items():
            with self.subTest(name=name):
                plan,_=recognize(source,Path(name+'.step'))
                ns,bodies,_=self.equivalent(plan)
                from OCP.TopAbs import TopAbs_SOLID
                from step2code.kernel.signatures import explore
                for a,b in zip(explore(source,TopAbs_SOLID),bodies):
                    self.assertLessEqual(symmetric_difference_volume(a,b)/volume(a),1e-7)

    def test_prism_recognizes_exact_b_spline_profile(self):
        from OCP.Geom import Geom_BSplineCurve
        from OCP.TColgp import TColgp_Array1OfPnt
        from OCP.TColStd import TColStd_Array1OfReal, TColStd_Array1OfInteger
        from OCP.gp import gp_Pnt, gp_Vec
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire, BRepBuilderAPI_MakeFace
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism

        poles = TColgp_Array1OfPnt(1, 4)
        for index, point in enumerate(((0, 0, 0), (6, 0, 0), (8, 10, 0), (0, 10, 0)), 1):
            poles.SetValue(index, gp_Pnt(*point))
        knots = TColStd_Array1OfReal(1, 2)
        knots.SetValue(1, 0.0); knots.SetValue(2, 1.0)
        multiplicities = TColStd_Array1OfInteger(1, 2)
        multiplicities.SetValue(1, 4); multiplicities.SetValue(2, 4)
        spline = Geom_BSplineCurve(poles, knots, multiplicities, 3, False)
        wire = BRepBuilderAPI_MakeWire()
        wire.Add(BRepBuilderAPI_MakeEdge(spline).Edge())
        wire.Add(BRepBuilderAPI_MakeEdge(gp_Pnt(0, 10, 0), gp_Pnt(0, 0, 0)).Edge())
        source = BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(wire.Wire()).Face(), gp_Vec(0, 0, 4)).Shape()

        plan, evidence = recognize(source, Path('spline_prism.step'))
        self.assertEqual(evidence['features'][0]['method'], 'analytic_cap_prism')
        self.assertIn('curve.bspline2', {entity['op'] for entity in plan.data['nodes'].values()})
        self.equivalent(plan, source)

    def test_prism_recognizes_exact_ellipse_profile(self):
        import simplecadapi as scad
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
        from OCP.gp import gp_Vec

        edge = scad.make_ellipse_redge(center=(0, 0, 0), major_radius=5, minor_radius=3,
                                       major_direction=(1, 0, 0))
        edge = edge.wrapped if hasattr(edge, 'wrapped') else edge
        wire = BRepBuilderAPI_MakeWire(); wire.Add(edge)
        source = BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(wire.Wire()).Face(), gp_Vec(0, 0, 4)).Shape()

        plan, evidence = recognize(source, Path('ellipse_prism.step'))
        self.assertEqual(evidence['features'][0]['method'], 'analytic_cap_prism')
        self.assertIn('curve.ellipse2', {entity['op'] for entity in plan.data['nodes'].values()})
        self.equivalent(plan, source)

    def test_revolution_behavior_fixtures(self):
        for name,source in examples().items():
            with self.subTest(name=name):
                plan,evidence=recognize(source,Path(name+'.step'))
                self.assertIn('solid.revolve',{n['op'] for n in plan.data['nodes'].values()})
                self.equivalent(plan,source)

    def test_csg_behavior_fixtures(self):
        for name,source in hybrid_examples().items():
            with self.subTest(name=name):
                plan,_=recognize(source,Path(name+'.step'));self.equivalent(plan,source)

    def test_major_arc_both_directions(self):
        for sweep in (1.5*math.pi,-1.5*math.pi):
            end=(5*math.cos(sweep),5*math.sin(sweep))
            d=profile_doc(dict(arc=node('curve.arc2',center=[0,0],radius=5,start_angle=0,sweep_angle=sweep),
                line=node('curve.line2',start=list(end),end=[5,0])))
            self.equivalent(Plan.from_dict(d))

    def test_open_loop_rejected(self):
        d=profile_doc(dict(line=node('curve.line2',start=[0,0],end=[1,0])))
        with self.assertRaises(PlanError) as e:prepare(Plan.from_dict(d))
        self.assertEqual(e.exception.code,'preparation.loop_gap')

    def test_self_intersection_is_not_disabled(self):
        points=[[0,0],[4,3],[0,4],[3,0]]
        curves={f'edge{i}':node('curve.line2',start=p,end=points[(i+1)%4]) for i,p in enumerate(points)}
        with self.assertRaises(PlanError) as e:prepare(Plan.from_dict(profile_doc(curves)))
        self.assertIn(e.exception.code,('preparation.topology_conflict','preparation.degenerate_loop'))

    def test_rational_spline_replay(self):
        d=profile_doc(dict(spline=node('curve.bspline2',degree=3,poles=[[0,0],[12,0],[9,8],[0,10]],
            knots=[0,1],multiplicities=[4,4],weights=[1,1.2,.8,1],periodic=False,trim=[0,1],reversed=False),
            line=node('curve.line2',start=[0,10],end=[0,0])))
        self.equivalent(Plan.from_dict(d))

    def test_spline_invalid_domain_rejected(self):
        d=profile_doc(dict(spline=node('curve.bspline2',degree=2,poles=[[0,0],[4,6],[8,0]],
            knots=[0,1],multiplicities=[3,3],weights=[1,1,1],periodic=False,trim=[-1,1],reversed=False)))
        with self.assertRaises(PlanError):Plan.from_dict(d)

    def test_nary_union_lowering_and_final_contract(self):
        from step2code.plan.passes import flatten_unions
        d=box_document();d['nodes']['sphere']=node('solid.sphere',radius=3)
        d['nodes']['union']=node('boolean.union',dict(operands=['box','sphere']))
        d['outputs'][0]['node']='union';self.equivalent(Plan.from_dict(d))
        d['nodes']['out']=node('boolean.union',dict(operands=['box','union']));d['outputs'][0]['node']='out'
        normalized=flatten_unions(d['nodes'],['out'])
        self.assertNotIn('union',normalized)
        self.assertEqual(normalized['out']['inputs']['operands'],['box','box','sphere'])

    def test_disconnected_intermediate_capability_rejected(self):
        d=box_document();d['nodes']['frame']=node('frame.rigid3',origin=[20,0,0],x_axis=[1,0,0],y_axis=[0,1,0])
        d['nodes']['other']=node('shape.transform',dict(shape=['box'],frame=['frame']))
        d['nodes']['pair']=node('boolean.union',dict(operands=['box','other']))
        d['nodes']['cut']=node('boolean.cut',dict(target=['pair'],tool=['other']));d['outputs'][0]['node']='cut'
        p=Plan.from_dict(d);self.assertEqual(len(execute(p).bodies),1)
        with self.assertRaises(PlanError) as e:generated(p)
        self.assertEqual(e.exception.code,'backend.unsupported_intermediate')

    def test_limits_and_freeform_domain(self):
        with self.assertRaises(UnsupportedGeometry):recognize(unsupported_freeform(),Path('free.step'))
        with self.assertRaises(UnsupportedGeometry):recognize(fixtures()['disjoint'],Path('many.step'),RecognitionOptions(max_solids=1))

    def test_mixed_prism_revolve_csg_outputs(self):
        from step2code.kernel.runtime import compound
        for source in (compound([fixtures()['box'],examples()['sphere']]),
                       compound([examples()['sphere'],hybrid_examples()['blind_hole']])):
            plan,evidence=recognize(source,Path('mixed.step'))
            self.assertEqual(len(plan.data['outputs']),2);self.equivalent(plan)

    def test_kernel_revolution_spline_section_extraction(self):
        import simplecadapi as scad
        edge=scad.make_spline_redge(control_points=[(0,0,0),(12,0,0),(9,0,8),(0,0,10)],degree=3,
            knots=[0,1],multiplicities=[4,4],weights=[1,1.2,.8,1])
        axis=scad.make_line_redge(start=(0,0,10),end=(0,0,0))
        face=scad.make_face_from_wire_rface(wire=scad.make_wire_from_edges_rwire(edges=[edge,axis]),normal=(0,-1,0))
        source=scad.revolve_rsolid(profile=face,axis=(0,0,1),origin=(0,0,0),angle=360).wrapped
        plan,_=recognize(source,Path('spline.step'));self.equivalent(plan,source)
        self.assertIn('curve.bspline2',{n['op'] for n in plan.data['nodes'].values()})

    def test_csg_gate_separates_surface_capability_from_face_limit(self):
        from step2code.recognition.csg import _recognize_csg
        for source,opts,reason in [(hybrid_examples()['blind_hole'],RecognitionOptions(max_csg_faces=1),'limit.source_faces'),
                (unsupported_freeform(),RecognitionOptions(),'capability.surface_type')]:
            with self.assertRaises(UnsupportedGeometry) as e:_recognize_csg(source,1,opts)
            self.assertEqual(e.exception.diagnostics['reason_code'],reason)
            self.assertIn('unsupported_surface_types',e.exception.diagnostics['gate'])

    def test_csg_deadline_is_not_reset_by_nested_attempts(self):
        from step2code.recognition.context import context,csg_deadline
        token=context.set(dict(csg_deadline=None))
        try:
            original=csg_deadline(1)
            self.assertEqual(original,csg_deadline(999))
        finally:context.reset(token)

    def test_trimmed_and_reversed_rational_spline_is_exact(self):
        from step2code.kernel.curves import spline
        for reverse in (False,True):
            args=dict(degree=2,poles=[[0,0],[4,6],[8,0]],knots=[0,1],multiplicities=[3,3],
                      weights=[1,2,1],periodic=False,trim=[.2,.8],reversed=reverse)
            c=spline(args);first=list(c.Value(c.FirstParameter()).Coord()[:2]);last=list(c.Value(c.LastParameter()).Coord()[:2])
            d=profile_doc(dict(spline=node('curve.bspline2',**args),closing=node('curve.line2',start=last,end=first)))
            self.equivalent(Plan.from_dict(d))

    def test_hole_outside_outer_profile_is_rejected(self):
        d=profile_doc(dict(circle=node('curve.circle2',center=[0,0],radius=2,winding='ccw')))
        d['nodes']['hole_curve']=node('curve.circle2',center=[10,0],radius=1,winding='ccw')
        d['nodes']['hole_loop']=node('wire.loop2',dict(curves=['hole_curve']))
        d['nodes']['profile']['inputs']['holes']=['hole_loop']
        with self.assertRaises(PlanError) as e:prepare(Plan.from_dict(d))
        self.assertEqual(e.exception.code,'preparation.topology_conflict')

    def test_native_empty_intermediate_has_zero_signature_and_can_feed_union(self):
        d=box_document()
        d['nodes']['frame']=node('frame.rigid3',origin=[20,0,0],x_axis=[1,0,0],y_axis=[0,1,0])
        d['nodes']['other']=node('shape.transform',dict(shape=['box'],frame=['frame']))
        d['nodes']['empty']=node('boolean.intersect',dict(operands=['box','other']))
        d['nodes']['out']=node('boolean.union',dict(operands=['empty','box']));d['outputs'][0]['node']='out'
        p=Plan.from_dict(d);execution=execute(p)
        self.assertEqual(execution.signatures['empty']['solid_count'],0)
        self.assertEqual(execution.signatures['empty']['volume_mm3'],0)
        self.assertAlmostEqual(volume(execution.bodies[0]),24,places=7)
        with self.assertRaises(PlanError) as e:generated(p)
        self.assertEqual(e.exception.code,'backend.unsupported_intermediate')
