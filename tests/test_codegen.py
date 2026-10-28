import ast
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from step2code.plan import Plan,PlanError
from step2code.codegen import compile_plan
from step2code.kernel.execute import execute
from step2code.kernel.runtime import returned_shapes,export_step
from step2code.kernel.import_step import read_reference
from step2code.verification.metrics import volume,symmetric_difference_volume,bbox
from step2code.recognition import recognize
from fixtures import fixtures,examples,hybrid_examples
from helpers import generated


class CompilerTests(unittest.TestCase):
    def test_code_is_deterministic_bound_and_standalone(self):
        plan,_=recognize(fixtures()['oblique_annulus'],Path('absent.step'))
        a=compile_plan(plan);b=compile_plan(plan)
        self.assertEqual(a,b);self.assertEqual(a.symbols['plan_hash'],plan.sha256)
        imports=[n for n in ast.walk(ast.parse(a.code)) if isinstance(n,(ast.Import,ast.ImportFrom))]
        self.assertEqual({x.name for n in imports for x in n.names},{'math','simplecadapi'})
        self.assertNotIn('json2scapi',a.code);self.assertNotIn('OCP',a.code)
        self.assertNotIn('transform_rsolid',a.code)
        self.assertTrue(a.symbols['source_map'])

    def test_body_scale_edit_native_and_generated(self):
        for source in (fixtures()['annulus'],examples()['oblique_frustum'],hybrid_examples()['localized_boss']):
            with self.subTest():
                plan,_=recognize(source,Path('unused.step'));ns,bodies,compiled=generated(plan)
                key='body_001_factor';ns[compiled.symbols['parameters'][key]['symbol']]=1.2
                changed=returned_shapes(ns['build']())[0]
                native=execute(plan.with_parameters({key:1.2})).bodies[0]
                self.assertAlmostEqual(volume(changed)/volume(bodies[0]),1.2**3,places=6)
                self.assertLessEqual(symmetric_difference_volume(native,changed)/volume(native),1e-7)

    def test_rotation_translation_edits_apply_once(self):
        plan,_=recognize(fixtures()['oblique_box'],Path('unused.step'));ns,_,compiled=generated(plan)
        edits=dict(body_001_origin=[5,7,-2],body_001_x_axis=[0,1,0],body_001_y_axis=[-1,0,0])
        for k,v in edits.items():ns[compiled.symbols['parameters'][k]['symbol']]=v
        native=execute(plan.with_parameters(edits)).bodies[0];actual=returned_shapes(ns['build']())[0]
        self.assertLessEqual(symmetric_difference_volume(native,actual)/volume(native),1e-7)

    def test_instances_are_independent_fresh_and_parameter_isolated(self):
        plan,_=recognize(fixtures()['disjoint'],Path('unused.step'));ns,original,c=generated(plan)
        ns[c.symbols['parameters']['body_001_factor']['symbol']]=1.2
        changed=returned_shapes(ns['build']())
        self.assertAlmostEqual(volume(changed[0])/volume(original[0]),1.2**3,places=6)
        self.assertAlmostEqual(volume(changed[1]),volume(original[1]),places=7)
        self.assertFalse(changed[0].IsSame(original[0]))
        again=returned_shapes(ns['build']());self.assertFalse(again[1].IsSame(changed[1]))

    def test_invalid_frame_and_scale_edits_rejected(self):
        plan,_=recognize(fixtures()['box'],Path('unused.step'));ns,_,c=generated(plan)
        for k,v in [('body_001_factor',0),('body_001_factor',math.nan),('body_001_origin',[math.inf,0,0]),
                    ('body_001_x_axis',[2,0,0]),('body_001_y_axis',[1,0,0])]:
            original=ns[c.symbols['parameters'][k]['symbol']];ns[c.symbols['parameters'][k]['symbol']]=v
            with self.assertRaises(ValueError):ns['build']()
            ns[c.symbols['parameters'][k]['symbol']]=original

    def test_saved_code_runs_with_no_repo_source_or_sidecar(self):
        plan,_=recognize(examples()['torus'],Path('nonexistent.step'));c=compile_plan(plan)
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'model.py';file.write_text(c.code,encoding='utf8')
            result=subprocess.run([sys.executable,'-I','-c',
                'import runpy; m=runpy.run_path('+repr(str(file))+'); assert m["build"]() is not None'],
                cwd=tmp,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout)

    def test_plan_replays_after_source_evidence_removed(self):
        from step2code.plan import dump,load
        plan,_=recognize(hybrid_examples()['tapered_polygon'],Path('unused.step'))
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'model.plan.json';dump(plan,p)
            native=execute(load(p)).bodies[0];ns,bodies,_=generated(load(p))
            self.assertLessEqual(symmetric_difference_volume(native,bodies[0])/volume(native),1e-7)

    def test_loose_faces_are_not_dropped(self):
        from step2code.kernel.runtime import compound
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.gp import gp_Pln,gp_Pnt,gp_Dir
        from step2code.recognition import UnsupportedGeometry
        face=BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(0,0,20),gp_Dir(0,0,1)),-2,2,-2,2).Face()
        with self.assertRaises(UnsupportedGeometry) as e:recognize(compound([fixtures()['box'],face]),Path('loose.step'))
        self.assertEqual(e.exception.diagnostics['reason_code'],'domain.loose_faces')

    def test_primitive_parameter_edits_validate_types_and_ranges(self):
        from helpers import box_document
        from step2code.plan import node
        d=box_document();d['nodes']['box']=node('solid.sphere',radius=2)
        d['parameters']['radius']=dict(type='number',unit='mm',value=2.,editability='independent')
        d['nodes']['box']['args']['radius']=dict(param='radius')
        p=Plan.from_dict(d);ns,_,c=generated(p);name=c.symbols['parameters']['radius']['symbol']
        for invalid in (True,0,-1,math.nan,math.inf):
            ns[name]=invalid
            with self.assertRaises(ValueError):ns['build']()
        ns[name]=3.;self.assertAlmostEqual(volume(returned_shapes(ns['build']())[0]),4*math.pi*9,places=6)

    def test_equal_radii_cone_has_cylinder_semantics(self):
        from helpers import box_document
        from step2code.plan import node
        d=box_document();d['nodes']['box']=node('solid.cone',radius_bottom=2,radius_top=2,height=4)
        plan=Plan.from_dict(d);ns,bodies,_=generated(plan)
        self.assertAlmostEqual(volume(bodies[0]),16*math.pi,places=6)
