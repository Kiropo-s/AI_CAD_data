"""Primitive-first search behavior, auxiliary sketches and exact rejection."""
import ast
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

import numpy as np
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from json2scapi.evaluation.metrics import volume, symmetric_difference_volume
from json2scapi.runtime.worker import returned_shapes
from step2code_direct.csg_first import propose_csg_programs, residual_operands
from step2code_direct.geometry import Options, UnsupportedGeometry
from step2code_direct.hybrid import Atom, _combine, recognize_hybrid
from step2code_direct.search_guidance import Guidance
from test_hybrid import emit_hybrid
from test_lookahead import rounded_boss


class CSGFirstTests(unittest.TestCase):
    def test_primitive_solution_precedes_exact_extrusion_shortcut(self):
        base = BRepPrimAPI_MakeBox(10,10,2).Shape()
        boss = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(5,5,2),gp_Dir(0,0,1)),1,3).Shape()
        source = _combine(base,boss,'union')
        atoms = [Atom('box',base,{},{}), Atom('cylinder',boss,{},{}),
                 Atom('extrusion',source,{}, {})]
        with patch('step2code_direct.hybrid_search.generate_atoms',return_value=atoms):
            result = recognize_hybrid(source,1,Options(hybrid_search='csg-first'))
        self.assertEqual(result.evidence['solution_route'],'csg_primitive_only')
        self.assertEqual(result.base.kind,'box')
        self.assertTrue(all(a.kind!='extrusion' for _,a in result.steps))
        self.assertLess(symmetric_difference_volume(source,result.shape)/volume(source),1e-7)

    def test_residual_sketch_is_added_with_one_shared_boolean_trial(self):
        base = BRepPrimAPI_MakeBox(10,10,2).Shape()
        boss = BRepPrimAPI_MakeBox(gp_Pnt(4,4,2),2,2,3).Shape()
        source = _combine(base,boss,'union')
        atoms = [Atom('box',base,{},{}),Atom('extrusion',boss,{},{})]
        with patch('step2code_direct.hybrid_search.generate_atoms',return_value=atoms):
            result = recognize_hybrid(source,1,Options(hybrid_search='csg-first',max_boolean_trials=1))
        self.assertEqual(result.evidence['solution_route'],'csg_residual_extrusions')
        self.assertEqual(result.evidence['boolean_trials'],1)
        self.assertEqual(result.base.kind,'box')
        self.assertEqual(result.steps[0][1].kind,'extrusion')

    def test_auxiliary_planner_keeps_primitive_roots_and_bounds_sketch_tools(self):
        ref=np.array([1,1,0,0],dtype=bool)
        guidance=Guidance(ref,{0:np.array([1,0,0,0],dtype=bool),
                               1:np.array([0,1,0,0],dtype=bool),
                               2:np.array([0,0,1,0],dtype=bool)})
        atoms=[Atom('box',None,{},{}),Atom('extrusion',None,{},{}),Atom('extrusion',None,{},{})]
        d={}
        programs=propose_csg_programs(guidance,atoms,Options(hybrid_search='csg-first'),
                                     time.monotonic()+1,d,auxiliary=True)
        self.assertTrue(any(error==0 and steps==(('union',1),) for error,base,steps,_ in programs))
        self.assertTrue(all(base==0 for _,base,_,_ in programs))
        selected=residual_operands(0b11,0b01,{1:0b10,2:0b100},[1,2])
        self.assertEqual(selected['union'],[1])
        self.assertNotIn(2,selected['cut'])

    def test_false_sample_match_does_not_pass_exact_acceptance(self):
        source=BRepPrimAPI_MakeBox(10,10,10).Shape()
        small=BRepPrimAPI_MakeBox(9,10,10).Shape()
        other=BRepPrimAPI_MakeBox(8,10,10).Shape()
        atoms=[Atom('box',small,{},{}),Atom('box',other,{},{})]
        guidance=Guidance(np.ones(8,dtype=bool),
                         {0:np.array([1,1,1,1,0,0,0,0],dtype=bool),
                          1:np.array([0,0,0,0,1,1,1,1],dtype=bool)})
        with patch('step2code_direct.hybrid_search.generate_atoms',return_value=atoms), \
             patch('step2code_direct.hybrid_search.build_guidance',return_value=guidance):
            with self.assertRaises(UnsupportedGeometry) as caught:
                recognize_hybrid(source,1,Options(hybrid_search='csg-first',max_boolean_trials=12))
        self.assertGreater(caught.exception.diagnostics['best_symmetric_difference_relative'],1e-7)
        self.assertLessEqual(caught.exception.diagnostics['boolean_trials'],12)

    def test_sampling_failure_keeps_bounded_exact_fallback(self):
        base=BRepPrimAPI_MakeBox(10,10,2).Shape()
        boss=BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(5,5,2),gp_Dir(0,0,1)),1,3).Shape()
        source=_combine(base,boss,'union')
        atoms=[Atom('box',base,{},{}),Atom('cylinder',boss,{},{})]
        with patch('step2code_direct.hybrid_search.generate_atoms',return_value=atoms), \
             patch('step2code_direct.hybrid_search.build_guidance',side_effect=RuntimeError('unavailable')):
            result=recognize_hybrid(source,1,Options(hybrid_search='csg-first',max_boolean_trials=8))
        self.assertEqual(result.evidence['solution_route'],'csg_guided_fallback')
        self.assertLessEqual(result.evidence['boolean_trials'],8)
        self.assertLess(symmetric_difference_volume(source,result.shape)/volume(source),1e-7)

    def test_real_rounded_body_emits_standalone_repeated_builds(self):
        source=rounded_boss()
        code,results=emit_hybrid(source,Options(hybrid_search='csg-first'))
        imports=[n for n in ast.walk(ast.parse(code)) if isinstance(n,(ast.Import,ast.ImportFrom))]
        self.assertEqual(len(imports),1)
        self.assertEqual(imports[0].names[0].name,'simplecadapi')
        namespace={};exec(code,namespace)
        first=returned_shapes(namespace['build']())[0]
        second=returned_shapes(namespace['build']())[0]
        self.assertIsNot(first,second)
        self.assertLess(symmetric_difference_volume(source,first)/volume(source),1e-7)
        self.assertLess(symmetric_difference_volume(source,second)/volume(source),1e-7)
        self.assertLessEqual(results[0].evidence['boolean_trials'],256)

    def test_pinned_import_and_validation_match_recorded_provenance(self):
        root=Path(__file__).resolve().parents[1]/'compat'
        origin=json.loads((root/'ORIGIN.json').read_text(encoding='utf-8'))
        for name,details in origin['files'].items():
            self.assertEqual(hashlib.sha256((root/name).read_bytes()).hexdigest(),details['sha256'])


if __name__=='__main__':
    unittest.main()
