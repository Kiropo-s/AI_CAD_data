"""Windows report replacement must survive a transient sharing lock."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from step2code_direct.pipeline import write_json


class ReportIOTests(unittest.TestCase):
    def test_transient_lock_retries_atomically_and_preserves_new_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'report.json'
            path.write_text('{"old":true}',encoding='utf-8')
            original=Path.replace
            attempts=[]
            def replace(temporary,target):
                attempts.append(1)
                if len(attempts)<3:
                    self.assertEqual(json.loads(path.read_text(encoding='utf-8')),{'old':True})
                    raise PermissionError('temporary destination sharing lock')
                return original(temporary,target)
            with patch.object(Path,'replace',replace), patch('step2code_direct.pipeline.time.sleep') as wait:
                write_json(path,{'new':True})
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')),{'new':True})
            self.assertEqual(len(attempts),3)
            self.assertEqual(wait.call_count,2)
            self.assertFalse(path.with_suffix('.json.tmp').exists())

    def test_persistent_lock_is_bounded_and_keeps_previous_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'report.json';path.write_text('{"old":true}',encoding='utf-8')
            with patch.object(Path,'replace',side_effect=PermissionError('locked')) as replace, \
                 patch('step2code_direct.pipeline.time.sleep') as wait:
                with self.assertRaises(PermissionError):write_json(path,{'new':True})
            self.assertEqual(replace.call_count,6)
            self.assertEqual(wait.call_count,5)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')),{'old':True})


if __name__=='__main__':unittest.main()
