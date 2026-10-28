"""Focused regression for exact SurfaceOfRevolution recovery and emission."""
import runpy
from pathlib import Path
import tempfile
import time
import unittest

from json2scapi.evaluation.metrics import symmetric_difference_volume, volume
from json2scapi.runtime.worker import returned_shapes
from step2code_direct.compat.import_step import read_reference
from step2code_direct.geometry import Options, source_solids
from step2code_direct.hybrid import HybridResult, generate_atoms
from step2code_direct.hybrid_codegen import add_hybrid
from step2code_direct.emitter import DirectEmitter


class RevolutionTests(unittest.TestCase):
    def test_bspline_surface_of_revolution_emits_exact_scapi(self):
        source_path = next((Path(__file__).resolve().parents[2] / 'datasets' / 'abc' / 'step' / 'abc_0000').glob('00000014_*.step'))
        reference, _ = read_reference(source_path)
        solids = source_solids(reference, Options())
        diagnostics = {'atom_errors': []}
        atoms = generate_atoms(solids[2], 3, Options(), time.monotonic() + 5.0, diagnostics)
        atom = next(item for item in atoms if item.kind == 'revolution')
        emitter = DirectEmitter()
        add_hybrid(emitter, HybridResult(atom, (), atom.shape, {}), 1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'model.scapi.py'
            path.write_text(emitter.emit(), encoding='utf-8')
            generated = returned_shapes(runpy.run_path(str(path))['build']())[0]
        self.assertLessEqual(symmetric_difference_volume(atom.shape, generated) / volume(atom.shape), 1e-7)


if __name__ == '__main__':
    unittest.main()
