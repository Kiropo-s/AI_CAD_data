"""CLI boundaries and process failure handling without running native CAD."""
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from step2code.cli import main
from step2code.recognition import RecognitionOptions


class CLITests(unittest.TestCase):
    def test_timeout_and_native_crash_are_provisional_and_batch_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'inputs';source.mkdir()
            (source/'a.step').write_text('placeholder')
            (source/'b.STP').write_text('placeholder')
            output=root/'run'
            effects=[subprocess.TimeoutExpired(['worker'],1),subprocess.CompletedProcess(['worker'],-1073741819)]
            with patch('step2code.cli.subprocess.run',side_effect=effects), contextlib.redirect_stdout(io.StringIO()):
                result=main(['--input',str(source),'--out',str(output),'--timeout','1'])
            self.assertEqual(result,1)
            summary=json.loads((output/'summary.json').read_text())
            self.assertEqual(summary['counts'],{'provisional':2})
            self.assertTrue((output/'summary.csv').is_file())
            for row in summary['cases']:
                self.assertEqual(row['reason'],'worker_error')
                self.assertIsNone(row['volume_iou'])

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.step';source.write_text('placeholder')
            output=root/'run';output.mkdir();sentinel=output/'keep.txt';sentinel.write_text('keep')
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                main(['--input',str(source),'--out',str(output)])
            self.assertEqual(exc.exception.code,2)
            self.assertEqual(sentinel.read_text(),'keep')

    def test_recognition_options_reject_invalid_tolerance(self):
        for options in ({'length_tolerance':0},{'length_tolerance':float('nan')},{'max_candidates':0}):
            with self.subTest(options=options),self.assertRaises(ValueError):
                RecognitionOptions(**options)


if __name__=='__main__':
    unittest.main()
