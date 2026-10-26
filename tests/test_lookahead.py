"""Exact verification and budget regressions for sampled CSG lookahead."""
from dataclasses import replace
import io
import time
import unittest
from unittest.mock import patch

import numpy as np
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeCone, BRepPrimAPI_MakeTorus
from OCP.BRepTools import BRepTools
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from json2scapi.evaluation.metrics import volume, symmetric_difference_volume
from step2code_direct.geometry import Options, UnsupportedGeometry, recognize_prism, source_solids
from step2code_direct.hybrid import Atom, recognize_hybrid, _combine, _difference
from step2code_direct.occupancy_search import propose_programs
from step2code_direct.search_guidance import Guidance
from test_hybrid import emit_hybrid
from json2scapi.runtime.worker import returned_shapes


def rounded_boss():
    torus = BRepPrimAPI_MakeTorus(8, 2).Shape()
    top = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0,0,0), gp_Dir(0,0,1)),10,3).Shape()
    core = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0,0,-2), gp_Dir(0,0,1)),8,5).Shape()
    body = _combine(_combine(torus, top, 'union'), core, 'union')
    tool = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(3,0,-2), gp_Dir(0,0,1)),1,5).Shape()
    return _combine(body, tool, 'cut')


class LookaheadTests(unittest.TestCase):
    def test_rounded_body_with_off_axis_hole_emits_independent_exact_code(self):
        source = rounded_boss()
        code, results = emit_hybrid(source, Options())
        namespace = {}; exec(code, namespace)
        actual = returned_shapes(namespace['build']())[0]
        self.assertLess(symmetric_difference_volume(source, actual)/volume(source), 1e-7)
        self.assertEqual(results[0].evidence['solution_route'], 'occupancy_lookahead')
        self.assertLessEqual(results[0].evidence['boolean_trials'], 256)
        self.assertIn('scad.cut_rsolid', code)

    def test_lookahead_sampled_match_still_requires_exact_geometry(self):
        source = BRepPrimAPI_MakeBox(10,10,10).Shape()
        small = BRepPrimAPI_MakeBox(9,10,10).Shape()
        other = BRepPrimAPI_MakeBox(gp_Pnt(0,0,0),8,10,10).Shape()
        atoms = [Atom('box',small,{},{}), Atom('box',other,{},{})]
        # Fake masks predict a perfect match for a real 10% volume deficit.
        guidance = Guidance(np.ones(8,dtype=bool),
                            {0:np.array([1,1,1,1,0,0,0,0],dtype=bool),
                             1:np.array([0,0,0,0,1,1,1,1],dtype=bool)})
        with patch('step2code_direct.hybrid_search.generate_atoms',return_value=atoms), \
             patch('step2code_direct.hybrid_search.build_guidance',return_value=guidance):
            with self.assertRaises(UnsupportedGeometry) as caught:
                recognize_hybrid(source,1,Options(max_boolean_trials=12))
        d=caught.exception.diagnostics
        self.assertGreater(d['lookahead_programs_evaluated'],0)
        self.assertGreater(d['best_symmetric_difference_relative'],1e-7)
        self.assertLessEqual(d['boolean_trials'],12)

    def test_near_prism_survives_failure_without_becoming_a_success(self):
        source=BRepPrimAPI_MakeCone(10,9.5,10).Shape()
        with self.assertRaises(UnsupportedGeometry) as caught:
            recognize_prism(source,1,Options())
        candidates=caught.exception.native_candidates
        self.assertGreater(len(candidates),0)
        self.assertLessEqual(len(candidates),3)
        self.assertGreater(candidates[0].evidence['symmetric_difference_relative'],1e-7)
        result=recognize_hybrid(source,1,Options(),seed_prisms=candidates)
        self.assertGreater(result.evidence['retained_prism_atoms'],0)
        self.assertLess(symmetric_difference_volume(source,result.shape)/volume(source),1e-7)
        with self.assertRaises(UnsupportedGeometry) as legacy:
            recognize_prism(source,1,Options(hybrid_search='guided'))
        self.assertEqual(legacy.exception.native_candidates,[])

    def test_cached_boolean_branches_do_not_modify_shared_operands(self):
        shape=BRepPrimAPI_MakeTorus(8,2).Shape()
        clip=BRepPrimAPI_MakeBox(gp_Pnt(-12,-12,0),24,24,12).Shape()
        def serialized(s):
            stream=io.BytesIO(); BRepTools.Write_s(s,stream); return stream.getvalue()
        before=[serialized(s) for s in (shape,clip)]
        cut=_combine(shape,clip,'cut')
        common=_combine(shape,clip,'intersect')
        _difference(shape,common,volume(shape))
        after=[serialized(s) for s in (shape,clip)]
        self.assertEqual(before,after)
        self.assertAlmostEqual(volume(cut)+volume(common),volume(shape),places=6)

    def test_occupancy_planner_can_cross_a_temporary_error_increase(self):
        # At A: missing bit1. Union B adds it but also wrong bit2; only
        # subsequent cut C reaches the reference. First step is a plateau.
        ref=np.array([1,1,0,0],dtype=bool)
        g=Guidance(ref,{0:np.array([1,0,0,0],dtype=bool),
                        1:np.array([0,1,1,0],dtype=bool),
                        2:np.array([0,0,1,0],dtype=bool)})
        d={}; programs=propose_programs(g,Options(max_boolean_steps=2),time.monotonic()+2,d)
        self.assertTrue(any(error==0 and base==0 and steps==( ('union',1),('cut',2))
                            for error,base,steps,_ in programs))


if __name__=='__main__':
    unittest.main()
