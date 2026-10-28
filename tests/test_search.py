"""Search quality regressions: exact acceptance, distractors and fallback."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from json2scapi.evaluation.metrics import volume, symmetric_difference_volume
from json2scapi.runtime.worker import export_step
from step2code_direct.geometry import Options, UnsupportedGeometry
from step2code_direct.hybrid import Atom, recognize_hybrid
from step2code_direct.search_guidance import Guidance


def _fixture():
    base = BRepPrimAPI_MakeBox(10, 10, 2).Shape()
    boss = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(5, 5, 2), gp_Dir(0, 0, 1)), 1, 3).Shape()
    source = BRepAlgoAPI_Fuse(base, boss).Shape()
    atoms = [Atom('box', base, {}, {})]
    for i in range(6):
        distractor = BRepPrimAPI_MakeCylinder(
            gp_Ax2(gp_Pnt(1 + i, 1, 0), gp_Dir(0, 0, 1)), 0.25, 1).Shape()
        atoms.append(Atom('cylinder', distractor, {}, {}))
    atoms.append(Atom('cylinder', boss, {}, {}))
    return source, atoms


class SearchTests(unittest.TestCase):
    def test_residual_ordering_finds_boss_under_one_trial_with_distractors(self):
        source, atoms = _fixture()
        options = Options(max_boolean_trials=1)
        with patch('step2code_direct.hybrid_search.generate_atoms', return_value=atoms):
            result = recognize_hybrid(source, 1, options)
        self.assertEqual(result.evidence['boolean_trials'], 1)
        self.assertEqual(result.steps[0][0], 'union')
        self.assertIs(result.steps[0][1], atoms[-1])
        self.assertLess(symmetric_difference_volume(source, result.shape) / volume(source), 1e-7)
        with patch('step2code_direct.hybrid.generate_atoms', return_value=atoms):
            with self.assertRaises(UnsupportedGeometry):
                recognize_hybrid(source, 1, replace(options, hybrid_search='legacy'))

    def test_zero_sample_error_cannot_accept_geometric_mismatch(self):
        source, atoms = _fixture()
        # Deliberately hide the missing boss from the approximate occupancy hint.
        guidance = Guidance(np.ones(8, dtype=bool), {0: np.ones(8, dtype=bool)})
        with patch('step2code_direct.hybrid_search.generate_atoms', return_value=atoms[:1]), \
             patch('step2code_direct.hybrid_search.build_guidance', return_value=guidance):
            with self.assertRaises(UnsupportedGeometry) as caught:
                recognize_hybrid(source, 1, Options())
        self.assertGreater(caught.exception.diagnostics['best_symmetric_difference_relative'], 1e-7)
        self.assertEqual(caught.exception.diagnostics['exact_score_calls'], 1)

    def test_sampling_failure_retains_exact_search_and_reports_error(self):
        source, atoms = _fixture()
        with patch('step2code_direct.hybrid_search.generate_atoms', return_value=[atoms[0], atoms[-1]]), \
             patch('step2code_direct.hybrid_search.build_guidance', side_effect=RuntimeError('classifier unavailable')):
            result = recognize_hybrid(source, 1, Options())
        self.assertLess(symmetric_difference_volume(source, result.shape) / volume(source), 1e-7)
        self.assertIn('classifier unavailable', result.evidence['sampling_errors'][0]['error'])
        for stage in ('generate_atoms', 'sampling', 'exact_scoring', 'boolean_construction'):
            self.assertIn(stage, result.evidence['stage_times_seconds'])

    def test_cli_propagates_search_mode_to_worker_and_report(self):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'sphere.step'
            export_step([BRepPrimAPI_MakeSphere(5).Shape()], source)
            for mode in ('lookahead', 'guided', 'legacy', 'csg-first'):
                output = root / mode
                result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'step2code_direct',
                    '--input', str(source), '--out', str(output), '--samples', '256',
                    '--hybrid-search', mode], capture_output=True, text=True,
                    cwd=Path(__file__).resolve().parents[2], timeout=45)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                summary = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
                report = json.loads(Path(summary['cases'][0]['report']).read_text(encoding='utf-8'))
                self.assertEqual(summary['configuration']['recognition']['hybrid_search'], mode)
                self.assertEqual(report['configuration']['recognition']['hybrid_search'], mode)
                self.assertEqual(report['status'], 'train_ready')


if __name__ == '__main__':
    unittest.main()
