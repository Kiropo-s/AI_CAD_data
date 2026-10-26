"""Regression evidence for explicit native extrusion/primitive CSG reconstruction."""
import ast
from dataclasses import replace
import json
import math
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut,BRepAlgoAPI_Fuse,BRepAlgoAPI_Common
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox,BRepPrimAPI_MakeCylinder,BRepPrimAPI_MakeCone,BRepPrimAPI_MakeSphere,BRepPrimAPI_MakeTorus
from OCP.gp import gp_Ax2,gp_Dir,gp_Pnt
from json2scapi.runtime.worker import export_step,returned_shapes,compound
from json2scapi.evaluation.metrics import volume,bbox,symmetric_difference_volume
from step2code_direct.geometry import Options,UnsupportedGeometry,source_solids
from step2code_direct.hybrid import recognize_hybrid
from step2code_direct.hybrid_codegen import add_hybrid
from step2code_direct.emitter import DirectEmitter
from step2code_direct.pipeline import convert_case
from test_direct import cylinder,transform


def hybrid_fixtures():
    base=BRepPrimAPI_MakeBox(20,20,5).Shape()
    boss=cylinder(3,5,(10,10,5))
    bossed=BRepAlgoAPI_Fuse(base,boss).Shape()
    block=BRepPrimAPI_MakeBox(20,20,8).Shape()
    hole=cylinder(2,8,(10,10,0))
    sink=BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(10,10,6),gp_Dir(0,0,1)),2,4,2).Shape()
    countersink=BRepAlgoAPI_Cut(BRepAlgoAPI_Cut(block,hole).Shape(),sink).Shape()
    blind=BRepAlgoAPI_Cut(block,cylinder(2,4,(10,10,4))).Shape()
    tilted_tool=BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(7,10,-3),gp_Dir(0.3,0,1)),2,16).Shape()
    tilted=BRepAlgoAPI_Cut(block,tilted_tool).Shape()
    sphere=BRepPrimAPI_MakeSphere(5).Shape()
    half=BRepAlgoAPI_Common(sphere,BRepPrimAPI_MakeBox(gp_Pnt(0,-6,-6),6,12,12).Shape()).Shape()
    torus=BRepPrimAPI_MakeTorus(gp_Ax2(gp_Pnt(0,0,0),gp_Dir(0,0,1)),10,2,-math.pi,math.pi,math.pi/2).Shape()
    elbow=BRepAlgoAPI_Fuse(BRepAlgoAPI_Fuse(torus,BRepPrimAPI_MakeSphere(gp_Pnt(10,0,0),2).Shape()).Shape(),
                          BRepPrimAPI_MakeSphere(gp_Pnt(0,10,0),2).Shape()).Shape()
    return {'sphere':sphere,'cone':BRepPrimAPI_MakeCone(8,4,10).Shape(),'boss_union':bossed,
            'countersink_cut':countersink,'blind_hole_cut':blind,'tilted_hole_cut':tilted,
            'hemisphere_intersect':half,'full_torus':BRepPrimAPI_MakeTorus(10,2).Shape(),
            'rounded_elbow':elbow,'oblique_boss':transform(bossed),'oblique_countersink':transform(countersink),
            'mixed_source_bodies':compound([BRepPrimAPI_MakeBox(5,6,7).Shape(),BRepPrimAPI_MakeSphere(gp_Pnt(30,0,0),4).Shape()])}


def emit_hybrid(shape,options=None):
    options=options or Options()
    emitter=DirectEmitter()
    results=[]
    for index,solid in enumerate(source_solids(shape,options),1):
        result=recognize_hybrid(solid,index,options)
        add_hybrid(emitter,result,index)
        results.append(result)
    return emitter.emit(),results


class HybridTests(unittest.TestCase):
    def test_real_hybrid_build_export_metrics_and_explicit_operations(self):
        required={'boss_union':'union','countersink_cut':'cut','hemisphere_intersect':'intersect'}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name,shape in hybrid_fixtures().items():
                with self.subTest(name=name):
                    source=root/(name+'.step')
                    export_step([shape],source)
                    result=convert_case(source,root/name)
                    self.assertEqual(result['status'],'train_ready',json.dumps(result,ensure_ascii=False)[-6500:])
                    self.assertFalse(list((root/name).glob('*.ir.json')))
                    if name in required:
                        operations=[op['operation'] for feature in result['recognition']['features'] for op in feature.get('selected_steps',[])]
                        self.assertIn(required[name],operations)
                        text=(root/name/'model.scapi.py').read_text(encoding='utf-8')
                        self.assertIn('scad.'+{'union':'union_rsolid','cut':'cut_rsolid','intersect':'intersect_rsolid'}[required[name]],text)

    def test_hybrid_codegen_is_deterministic_and_has_no_runtime_interpreter(self):
        shape=hybrid_fixtures()['countersink_cut']
        code,_=emit_hybrid(shape)
        self.assertEqual(code,emit_hybrid(shape)[0])
        tree=ast.parse(code)
        imports=[n for n in ast.walk(tree) if isinstance(n,(ast.Import,ast.ImportFrom))]
        self.assertEqual(len(imports),1)
        self.assertEqual(imports[0].names[0].name,'simplecadapi')
        for forbidden in ('exec(', 'eval(', 'read_step', 'base64', 'entities[', 'sequence['):
            self.assertNotIn(forbidden,code)
        namespace={};exec(code,namespace)
        first=returned_shapes(namespace['build']())
        second=returned_shapes(namespace['build']())
        self.assertIsNot(first[0],second[0])
        self.assertLess(symmetric_difference_volume(first[0],second[0])/volume(first[0]),1e-9)

    def test_hybrid_body_translation_and_rotation_are_rigid(self):
        code,_=emit_hybrid(hybrid_fixtures()['boss_union'])
        namespace={};exec(code,namespace)
        initial=returned_shapes(namespace['build']())[0]
        namespace['BODY_01_ORIGIN_MM']=(3,-4,8)
        shifted=returned_shapes(namespace['build']())[0]
        self.assertAlmostEqual(volume(initial),volume(shifted),places=6)
        for i,delta in enumerate((3,-4,8,3,-4,8)):
            self.assertAlmostEqual(bbox(shifted)[i]-bbox(initial)[i],delta,places=6)
        namespace['BODY_01_X_AXIS']=(0,1,0)
        namespace['BODY_01_Y_AXIS']=(-1,0,0)
        rotated=returned_shapes(namespace['build']())[0]
        self.assertAlmostEqual(volume(initial),volume(rotated),places=6)
        namespace['BODY_01_X_AXIS']=(2,0,0)
        with self.assertRaises(ValueError):
            namespace['build']()

    def test_arbitrary_hybrid_frame_rotations_match_native_rigid_transform(self):
        from OCP.gp import gp_Trsf,gp_Ax1,gp_Vec
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        import numpy as np
        code,_=emit_hybrid(hybrid_fixtures()['boss_union'])
        for axis,angle in (((1,2,3),0.73),((0,0,1),math.pi),((1,0,0),math.pi),((1,2,0),math.pi-1e-5)):
            with self.subTest(axis=axis,angle=angle):
                pose=gp_Trsf()
                pose.SetRotation(gp_Ax1(gp_Pnt(0,0,0),gp_Dir(*axis)),angle)
                pose.SetTranslationPart(gp_Vec(3,-4,8))
                namespace={};exec(code,namespace)
                initial=returned_shapes(namespace['build']())[0]
                namespace['BODY_01_ORIGIN_MM']=(3,-4,8)
                namespace['BODY_01_X_AXIS']=tuple(pose.Value(i,1) for i in (1,2,3))
                namespace['BODY_01_Y_AXIS']=tuple(pose.Value(i,2) for i in (1,2,3))
                actual=returned_shapes(namespace['build']())[0]
                expected=BRepBuilderAPI_Transform(initial,pose,True).Shape()
                self.assertLess(symmetric_difference_volume(expected,actual)/volume(expected),1e-7)
                self.assertAlmostEqual(volume(expected),volume(actual),places=6)

    def test_search_keeps_exterior_seed_for_hollow_conical_body(self):
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
        lower=cylinder(8,2)
        outer=BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(0,0,2),gp_Dir(0,0,1)),8,10,20).Shape()
        inner=BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(0,0,2),gp_Dir(0,0,1)),7,9,20).Shape()
        source=BRepAlgoAPI_Fuse(lower,BRepAlgoAPI_Cut(outer,inner).Shape()).Shape()
        code,results=emit_hybrid(source)
        namespace={};exec(code,namespace)
        actual=returned_shapes(namespace['build']())[0]
        self.assertLess(symmetric_difference_volume(source,actual)/volume(source),1e-7)
        operations=[op for op,_ in results[0].steps]
        self.assertIn('cut',operations)
        self.assertIn('union',operations)

    def test_hybrid_rejects_unsupported_bezier_surface_without_faking_output(self):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge,BRepBuilderAPI_MakeWire,BRepBuilderAPI_MakeFace
        from OCP.Geom import Geom_BezierCurve
        from OCP.TColgp import TColgp_Array1OfPnt
        from OCP.gp import gp_Vec
        poles=TColgp_Array1OfPnt(1,4)
        for i,p in enumerate(((0,0,0),(4,-6,0),(16,-6,0),(20,0,0)),1):
            poles.SetValue(i,gp_Pnt(*p))
        wire=BRepBuilderAPI_MakeWire()
        wire.Add(BRepBuilderAPI_MakeEdge(Geom_BezierCurve(poles)).Edge())
        wire.Add(BRepBuilderAPI_MakeEdge(gp_Pnt(20,0,0),gp_Pnt(0,0,0)).Edge())
        shape=BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(wire.Wire()).Face(),gp_Vec(0,0,5)).Shape()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'bezier.step';export_step([shape],source)
            r=convert_case(source,root/'case',options=Options(max_boolean_trials=16))
            self.assertEqual(r['status'],'provisional')
            self.assertEqual(r['decision']['reason'],'unsupported_geometry')
            self.assertFalse((root/'case'/'model.scapi.py').exists())

    def test_primitive_parameters_are_read_at_build_time(self):
        code,_=emit_hybrid(BRepPrimAPI_MakeSphere(5).Shape())
        namespace={};exec(code,namespace)
        original=volume(returned_shapes(namespace['build']())[0])
        key=next(k for k in namespace if k.endswith('_RADIUS_MM'))
        namespace[key]*=1.1
        changed=volume(returned_shapes(namespace['build']())[0])
        self.assertAlmostEqual(changed/original,1.331,places=7)

    def test_isolated_hybrid_code_runs_without_repository(self):
        code,_=emit_hybrid(hybrid_fixtures()['countersink_cut'])
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'model.scapi.py';source.write_text(code,encoding='utf-8')
            script="import runpy,sys; ns=runpy.run_path(sys.argv[1]); assert ns['build']() is not ns['build'](); print('hybrid-standalone-ok')"
            result=subprocess.run([sys.executable,'-I','-X','utf8','-c',script,str(source)],capture_output=True,text=True,cwd=tmp,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('hybrid-standalone-ok',result.stdout)

    def test_hybrid_disable_keeps_legacy_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'sphere.step'
            export_step([BRepPrimAPI_MakeSphere(5).Shape()],source)
            old=convert_case(source,root/'off',options=Options(enable_hybrid=False))
            new=convert_case(source,root/'on')
            self.assertEqual(old['status'],'provisional')
            self.assertEqual(new['status'],'train_ready')

    def test_budgets_exhaustion_never_claims_acceptance(self):
        shape=hybrid_fixtures()['rounded_elbow']
        solid=source_solids(shape,Options())[0]
        for options in (Options(max_boolean_trials=1),Options(hybrid_timeout_seconds=1e-8)):
            with self.subTest(options=options):
                with self.assertRaises(UnsupportedGeometry) as caught:
                    recognize_hybrid(solid,1,options)
                evidence=caught.exception.diagnostics
                self.assertEqual(evidence['status'],'not_recognized')
                self.assertIn(evidence['stop_reason'],('time_budget','boolean_trial_budget'))
        with self.assertRaises(ValueError):
            Options(max_boolean_steps=0)

    def test_wrong_output_and_extra_bodies_still_fail_coverage(self):
        from step2code_direct.pipeline import verify_bodies
        shape=hybrid_fixtures()['boss_union']
        solid=source_solids(shape,Options())[0]
        wrong=cylinder()
        self.assertEqual(verify_bodies([solid],[wrong],1e-7)['decision']['status'],'rejected')
        self.assertEqual(verify_bodies([solid],[solid,wrong],1e-7)['decision']['status'],'rejected')


if __name__=='__main__':
    unittest.main()
