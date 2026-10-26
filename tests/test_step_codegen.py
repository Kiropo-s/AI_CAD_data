"""STEP contract regressions: editable instances, placement, coverage and units."""
import ast
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt

from json2scapi.evaluation.metrics import bbox, volume
from json2scapi.runtime.worker import compound, export_step, returned_shapes
from step2code.codegen import StepEmitter
from step2code.pipeline import convert_case
from step2code.recognition import recognize, UnsupportedGeometry
from step2code.step_import import read_reference
from step2code.validation import verify_source_coverage
from test_step2scapi import cylinder, fixtures, transform


def program(shape):
    payload, evidence = recognize(shape, Path('source.step'))
    emitter = StepEmitter(payload, evidence)
    code = emitter.emit()
    namespace = {}
    exec(code, namespace)
    return namespace, emitter, code


class StepCodeTests(unittest.TestCase):
    def test_reuse_with_independent_sizes_placement_and_fresh_builds(self):
        ns, emitter, code = program(compound([cylinder(), cylinder(10, 7, (40,0,0)), cylinder(4,9,(80,0,0))]))
        self.assertEqual(emitter.metadata['structure']['template_count'], 1)
        self.assertLess(emitter.metadata['structure']['build_lines'], 12)
        baseline = returned_shapes(ns['build']())
        bindings = [item['bindings'] for item in emitter.metadata['instances']]
        first_radius = next(name for key,name in bindings[0].items() if 'radius' in key)
        second_radius = next(name for key,name in bindings[1].items() if 'radius' in key)
        self.assertNotEqual(first_radius, second_radius)
        ns[first_radius] *= 1.1
        ns[bindings[0]['distance']] *= 2
        ns[bindings[1]['origin']] = [v+d for v,d in zip(ns[bindings[1]['origin']], (3,-4,8))]
        changed = returned_shapes(ns['build']())
        self.assertAlmostEqual(volume(changed[0])/volume(baseline[0]), 2.42, places=7)
        for i in (1,2):
            self.assertAlmostEqual(volume(changed[i]),volume(baseline[i]),places=6)
        for before, after, shift in zip(bbox(baseline[1]),bbox(changed[1]),(3,-4,8,3,-4,8)):
            self.assertAlmostEqual(after-before, shift, places=7)
        self.assertEqual(bbox(baseline[2]),bbox(changed[2]))
        self.assertIsNot(baseline[0], changed[0])
        self.assertTrue(all(BRepCheck_Analyzer(s).IsValid() for s in changed))
        self.assertIn('for instance in _feature_instances_01():',code)

    def test_world_rotation_edit_uses_same_frame_for_profile_and_extrusion(self):
        ns, emitter, _ = program(BRepPrimAPI_MakeBox(30,20,5).Shape())
        original = returned_shapes(ns['build']())[0]
        bindings = emitter.metadata['instances'][0]['bindings']
        for key in ('origin','x_axis','y_axis'):
            x,y,z = ns[bindings[key]]
            ns[bindings[key]] = [-y,x,z]
        actual = returned_shapes(ns['build']())[0]
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCP.gp import gp_Ax1, gp_Dir, gp_Trsf
        import math
        matrix=gp_Trsf();matrix.SetRotation(gp_Ax1(gp_Pnt(0,0,0),gp_Dir(0,0,1)),math.pi/2)
        expected=BRepBuilderAPI_Transform(original,matrix,True).Shape()
        from json2scapi.evaluation.metrics import symmetric_difference_volume
        self.assertLess(symmetric_difference_volume(expected,actual)/volume(expected),1e-9)

    def test_invalid_frames_fail_without_silent_normalization(self):
        ns, emitter, _ = program(cylinder())
        key = emitter.metadata['instances'][0]['bindings']['x_axis']
        for value in ([2,0,0],[float('nan'),0,0],[1,0]):
            ns[key] = value
            with self.assertRaises(ValueError): ns['build']()

    def test_template_identity_keeps_profile_topology_and_fixed_coordinates(self):
        shape=compound([cylinder(),BRepPrimAPI_MakeBox(gp_Pnt(40,0,0),20,10,5).Shape(),
                        BRepPrimAPI_MakeBox(gp_Pnt(80,0,0),22,10,5).Shape()])
        _, emitter, _ = program(shape)
        self.assertEqual(emitter.metadata['structure']['template_count'],3)

    def test_metadata_and_codegen_are_deterministic_and_bound(self):
        shape=transform(fixtures()['two_holes'])
        _, a, code_a=program(shape);_, b, code_b=program(shape)
        self.assertEqual(code_a,code_b)
        self.assertEqual(a.metadata,b.metadata)
        tree=ast.parse(code_a)
        functions={n.name for n in tree.body if isinstance(n,ast.FunctionDef)}
        self.assertEqual(set(a.metadata['profiles']),{f for f in functions if f.startswith('make_profile_')})
        self.assertEqual([n.names[0].name for n in ast.walk(tree) if isinstance(n,ast.Import)],['simplecadapi'])
        for parameter in a.metadata['parameters'].values():
            for field in ('value','units','quantity','origin_kind','edit_policy','bindings','evidence_refs','expression'):
                self.assertIn(field,parameter)
            self.assertFalse(parameter['design_parameter_recovered'])
        selected=a.evidence['features'][0]['selected']
        self.assertEqual(selected['distance_method'],'parallel_cap_separation')
        self.assertEqual(selected['distance_origin_kind'],'measured')

    def test_coverage_catches_missing_and_swapped_bodies(self):
        reference=compound([cylinder(),cylinder(3,8,(40,0,0))])
        ns, emitter, _=program(reference)
        bodies=returned_shapes(ns['build']())
        coverage=emitter.evidence['source_coverage']
        self.assertEqual(verify_source_coverage(reference,bodies,coverage)['status'],'pass')
        self.assertEqual(verify_source_coverage(reference,bodies[:1],coverage)['status'],'fail')
        self.assertEqual(verify_source_coverage(reference,bodies[::-1],coverage)['status'],'fail')
        bad_faces=copy.deepcopy(coverage);bad_faces['mappings'][0]['source_face_indices'].pop()
        self.assertEqual(verify_source_coverage(reference,bodies,bad_faces)['status'],'fail')
        missing=copy.deepcopy(coverage);missing['mappings'].pop()
        self.assertEqual(verify_source_coverage(reference,bodies,missing)['status'],'fail')

    def test_coverage_failure_cannot_be_train_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'box.step';export_step([cylinder()],source)
            failure=dict(status='fail',decision=dict(status='rejected',reason='incomplete_source_coverage'))
            with patch('step2code.pipeline.verify_source_coverage',return_value=failure):
                report=convert_case(source,root/'case',samples=512)
            self.assertEqual(report['status'],'rejected')
            self.assertIs(report['geometry_validated'],False)
            self.assertEqual(report['parameter_edit_validation']['status'],'not_run')
            self.assertEqual(report['assembly_validation']['status'],'unsupported')

    def test_meter_source_imported_to_mm_exactly_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'meters.step'
            export_step([BRepPrimAPI_MakeBox(.03,.02,.005).Shape()],source)
            text=source.read_text();self.assertIn('SI_UNIT(.MILLI.,.METRE.)',text)
            source.write_text(text.replace('SI_UNIT(.MILLI.,.METRE.)','SI_UNIT($,.METRE.)'))
            shape, evidence=read_reference(source)
            self.assertIn('metre',evidence['source_length_units'])
            self.assertAlmostEqual(volume(shape),3000,places=5)
            report=convert_case(source,root/'case',samples=8192)
            self.assertEqual(report['status'],'train_ready',report.get('error'))
            self.assertIs(report['geometry_validated'],True)
            self.assertEqual(report['source_coverage_validation']['status'],'pass')

    def test_saved_program_is_standalone_in_isolated_python(self):
        import subprocess
        import sys
        _, emitter, code=program(cylinder())
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'model.scapi.py';path.write_text(code,encoding='utf-8')
            script = """import runpy, sys
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp
loaded=runpy.run_path(sys.argv[1]); ns=loaded['build'].__globals__
first=ns['build'](); before=GProp_GProps();BRepGProp.VolumeProperties_s(first.wrapped,before)
key=next(k for k in ns if k.endswith('_RADIUS_MM')); ns[key]*=1.1
second=ns['build'](); after=GProp_GProps();BRepGProp.VolumeProperties_s(second.wrapped,after)
assert abs(after.Mass()/before.Mass()-1.21)<1e-7
assert first is not second
"""
            result=subprocess.run([sys.executable,'-I','-c',script,str(path)],cwd=tmp,
                                  capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_edit_smoke_changes_actual_saved_program_globals(self):
        from step2code.tests.run_abc_regression import edit_smoke
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.step';export_step([cylinder()],source)
            report=convert_case(source,root/'case')
            self.assertEqual(report['status'],'train_ready')
            result=edit_smoke(root/'case')
            self.assertEqual(result['status'],'partial')
            self.assertTrue(result['all_recorded_checks_passed'])
            self.assertAlmostEqual(result['tests'][0]['actual_volume_ratios'][0],1.1,places=7)

    def test_loose_geometry_is_not_dropped(self):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
        edge=BRepBuilderAPI_MakeEdge(gp_Pnt(50,0,0),gp_Pnt(51,0,0)).Edge()
        with self.assertRaises(UnsupportedGeometry):
            recognize(compound([cylinder(),edge]),Path('mixed.step'))


if __name__=='__main__': unittest.main()
