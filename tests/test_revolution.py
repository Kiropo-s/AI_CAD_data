"""Real kernel regressions for verified full rotations and mixed operation plans."""
import ast
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse, BRepAlgoAPI_Cut
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCone, BRepPrimAPI_MakeSphere, BRepPrimAPI_MakeTorus
from OCP.gp import gp_Pnt
from json2scapi.evaluation.metrics import volume, bbox, symmetric_difference_volume
from json2scapi.runtime.worker import export_step, compound, returned_shapes
from step2code.composite_codegen import CompositeEmitter
from step2code.pipeline import convert_case
from step2code.recognition import recognize, RecognitionOptions, UnsupportedGeometry
from step2code.validation import verify_source_coverage
from test_step2scapi import cylinder, transform, unsupported_freeform


def examples():
    stepped=BRepAlgoAPI_Fuse(cylinder(10,4),cylinder(5,5,(0,0,4))).Shape()
    shell=BRepAlgoAPI_Cut(BRepPrimAPI_MakeSphere(10).Shape(),BRepPrimAPI_MakeSphere(6).Shape()).Shape()
    return dict(sphere=BRepPrimAPI_MakeSphere(10).Shape(),cone=BRepPrimAPI_MakeCone(10,0,8).Shape(),
        frustum=BRepPrimAPI_MakeCone(10,5,8).Shape(),torus=BRepPrimAPI_MakeTorus(20,5).Shape(),
        stepped_shaft=stepped,hollow_sphere=shell,oblique_frustum=transform(BRepPrimAPI_MakeCone(10,5,8).Shape()))


class RevolutionTests(unittest.TestCase):
    def test_end_to_end_analytic_revolutions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name,shape in examples().items():
                with self.subTest(name=name):
                    source=root/(name+'.step');export_step([shape],source)
                    report=convert_case(source,root/name)
                    self.assertEqual(report['status'],'train_ready',json.dumps(report,ensure_ascii=False)[-5000:])
                    self.assertEqual(report['recognition']['features'][0]['method'],'verified_meridian_revolution')
                    self.assertEqual(report['source_coverage_validation']['status'],'pass')
                    self.assertIs(report['geometry_validated'],True)
                    plan=json.loads((root/name/'model.ir.json').read_text())
                    self.assertNotIn('ir_version',plan)
                    self.assertEqual(plan['format'],'step-reconstruction-plan-v1')
                    symbols=json.loads((root/name/'model.symbols.json').read_text())
                    self.assertEqual(symbols['structure']['operation_counts']['full_revolution'],1)
                    self.assertEqual(symbols['emitter'],'step-composite-emitter-v1')
                    self.assertFalse(report['history_recovered'])

    def test_mixed_operations_cover_bodies_in_both_orders(self):
        box=BRepPrimAPI_MakeBox(gp_Pnt(40,0,0),20,15,5).Shape()
        sphere=BRepPrimAPI_MakeSphere(10).Shape()
        for source_shapes in ([box,sphere],[sphere,box]):
            with self.subTest(first_box=source_shapes[0] is box):
                shape=compound(source_shapes);payload,evidence=recognize(shape,Path('mixed.step'))
                emitter=CompositeEmitter(payload,evidence);code=emitter.emit();namespace={};exec(code,namespace)
                shapes=returned_shapes(namespace['build']())
                self.assertEqual(verify_source_coverage(shape,shapes,evidence['source_coverage'])['status'],'pass')
                self.assertEqual(set(emitter.metadata['placements']),{'BODY_01','BODY_02'})
                for i,instance in enumerate(emitter.metadata['instances'],1):
                    self.assertEqual(instance['source_solid_index'],i)
                    self.assertEqual(instance['output_body_index'],i-1)
                    for key,name in instance['bindings'].items():self.assertIn(name,namespace)
                    if i==2:self.assertTrue(instance['bindings']['origin'].startswith('BODY_02'))
                self.assertEqual(len(shapes),2)
                self.assertEqual(emitter.metadata['structure']['operation_counts'],{'new_prism':1,'full_revolution':1})

    def test_scale_and_placement_edits_are_independent(self):
        shape=compound([transform(BRepPrimAPI_MakeCone(10,5,8).Shape()),
                        BRepPrimAPI_MakeBox(gp_Pnt(80,0,0),20,15,5).Shape()])
        payload,evidence=recognize(shape,Path('source.step'));emitter=CompositeEmitter(payload,evidence)
        code=emitter.emit();namespace={};exec(code,namespace)
        before=returned_shapes(namespace['build']());bindings=emitter.metadata['instances'][0]['bindings']
        namespace[bindings['profile_scale']]=1.1
        after=returned_shapes(namespace['build']())
        self.assertAlmostEqual(volume(after[0])/volume(before[0]),1.1**3,places=7)
        self.assertEqual(bbox(before[1]),bbox(after[1]))
        self.assertIsNot(before[0],after[0])
        namespace[bindings['profile_scale']]=1
        origin=namespace[bindings['origin']]
        namespace[bindings['origin']]=[v+d for v,d in zip(origin,(3,-4,8))]
        shifted=returned_shapes(namespace['build']())
        for a,b,d in zip(bbox(before[0]),bbox(shifted[0]),(3,-4,8,3,-4,8)):
            self.assertAlmostEqual(b-a,d,places=6)
        self.assertEqual(bbox(before[1]),bbox(shifted[1]))
        namespace[bindings['profile_scale']]=0
        with self.assertRaises(ValueError):namespace['build']()

    def test_wrong_axis_or_asymmetric_geometry_does_not_pass(self):
        shape=BRepAlgoAPI_Fuse(BRepPrimAPI_MakeBox(20,15,5).Shape(),
                              BRepPrimAPI_MakeSphere(gp_Pnt(20,15,5),3).Shape()).Shape()
        from step2code.revolution import recognize_revolution
        with self.assertRaises(UnsupportedGeometry) as captured:recognize_revolution(shape,1,RecognitionOptions())
        self.assertEqual(captured.exception.diagnostics['method'],'verified_meridian_revolution')

    def test_section_limits_are_enforced(self):
        shape=BRepPrimAPI_MakeSphere(10).Shape()
        with self.assertRaises(UnsupportedGeometry):
            from step2code.revolution import recognize_revolution
            recognize_revolution(shape,1,RecognitionOptions(max_section_edges=1))
        for option in ({'max_revolve_candidates':0},{'max_section_faces':0},{'max_section_edges':0}):
            with self.assertRaises(ValueError):RecognitionOptions(**option)

    def test_section_provenance_is_not_a_source_edge_claim(self):
        payload,evidence=recognize(BRepPrimAPI_MakeCone(10,5,8).Shape(),Path('source.step'))
        for node in payload['entities'].values():
            if node['op'].startswith('sketch.') and 'source' in node:
                self.assertTrue(node['source']['derived_section'])
                self.assertNotIn('source_edge_index',node['source'])
        self.assertLessEqual(evidence['features'][0]['selected']['symmetric_difference_relative'],1e-7)

    def test_kernel_rational_spline_section_replayed_without_fitting(self):
        import simplecadapi as scad
        spline=scad.make_spline_redge(control_points=[(0,0,0),(12,0,0),(9,0,8),(0,0,10)],
            degree=3,knots=[0,1],multiplicities=[4,4],weights=[1,1.2,.8,1])
        axis_edge=scad.make_line_redge(start=(0,0,10),end=(0,0,0))
        wire=scad.make_wire_from_edges_rwire(edges=[spline,axis_edge])
        face=scad.make_face_from_wire_rface(wire=wire,normal=(0,-1,0))
        shape=scad.revolve_rsolid(profile=face,axis=(0,0,1),origin=(0,0,0),angle=360).wrapped
        payload,evidence=recognize(shape,Path('spline.step'))
        curves=[node for node in payload['entities'].values() if node['op']=='sketch.bspline']
        self.assertTrue(curves)
        with self.assertRaises(UnsupportedGeometry):
            recognize(shape,Path('limited.step'),RecognitionOptions(max_spline_poles=3))
        for curve in curves:
            params=curve['params']
            self.assertEqual(len(params['control_points']),len(params['weights']))
            self.assertLessEqual(params['pole_plane_residual_mm'],1e-6)
            self.assertIn('no converter sampling or fitting',params['provenance'])
        emitter=CompositeEmitter(payload,evidence);code=emitter.emit();namespace={};exec(code,namespace)
        actual=returned_shapes(namespace['build']())[0]
        self.assertLessEqual(symmetric_difference_volume(shape,actual)/volume(shape),1e-7)
        self.assertIn('scad.make_spline_redge(',code)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'spline.step';export_step([shape],source)
            report=convert_case(source,root/'case')
            self.assertEqual(report['status'],'train_ready',report.get('decision'))

    def test_partial_recognition_never_exports_complete_case(self):
        unsupported=unsupported_freeform()
        shape=compound([BRepPrimAPI_MakeSphere(10).Shape(),unsupported])
        with self.assertRaises(UnsupportedGeometry) as captured:recognize(shape,Path('partial.step'))
        self.assertEqual(captured.exception.diagnostics['recognized_solid_count'],1)
        self.assertEqual(captured.exception.diagnostics['coverage_status'],'partial')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'partial.step';export_step([shape],source)
            report=convert_case(source,root/'case')
            self.assertEqual(report['status'],'provisional')
            self.assertIsNone(report['geometry_validated'])
            self.assertFalse((root/'case'/'model.scapi.py').exists())

    def test_reversed_source_is_not_accepted_by_negative_volume(self):
        shape=BRepPrimAPI_MakeSphere(10).Shape().Reversed()
        self.assertLess(volume(shape),0)
        with self.assertRaises(UnsupportedGeometry):recognize(shape,Path('reversed.step'))

    def test_unsupported_plan_semantics_are_not_silently_emitted(self):
        import copy
        payload,evidence=recognize(BRepPrimAPI_MakeSphere(10).Shape(),Path('sphere.step'))
        step=evidence['features'][0]['operation_id']
        for field,value in (('operation','cut'),('angle_deg',180),('profile_scale',2)):
            plan=copy.deepcopy(payload);plan['entities'][step]['params'][field]=value
            with self.assertRaises(ValueError):CompositeEmitter(plan,evidence)

    def test_saved_rotation_program_is_isolated_and_deterministic(self):
        shape=transform(BRepPrimAPI_MakeTorus(20,5).Shape())
        first,first_evidence=recognize(shape,Path('source.step'))
        second,second_evidence=recognize(shape,Path('source.step'))
        code=CompositeEmitter(first,first_evidence).emit()
        self.assertEqual(code,CompositeEmitter(second,second_evidence).emit())
        imports=[n for n in ast.walk(ast.parse(code)) if isinstance(n,(ast.Import,ast.ImportFrom))]
        self.assertEqual(len(imports),1);self.assertEqual(imports[0].names[0].name,'simplecadapi')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'model.py';path.write_text(code,encoding='utf-8')
            result=subprocess.run([sys.executable,'-I','-c',
                'import runpy,sys; ns=runpy.run_path(sys.argv[1]); a=ns["build"](); b=ns["build"](); assert a is not b; assert not a.wrapped.IsNull()',str(path)],
                capture_output=True,text=True,cwd=tmp,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__':unittest.main()
