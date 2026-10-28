import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from step2code.batch.checkpoint import Checkpoints
from step2code.batch.events import Events
from step2code.plan import Plan
from step2code.plan.codec import write_json
from step2code.batch.identity import digest
from step2code.batch.diagnostics import topology_summary, recognition_diagnostics
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from helpers import box_document


class BatchTests(unittest.TestCase):
    def test_topology_summary_is_sidecar_only_and_deterministic(self):
        shape = BRepPrimAPI_MakeBox(2, 3, 4).Shape()
        first = topology_summary(shape)
        second = topology_summary(shape)
        self.assertEqual(first, second)
        self.assertEqual(first['p1_graph_status'], 'summary_only')
        self.assertEqual(first['entity_counts'], {
            'solid_count': 1,
            'face_count': 6,
            'edge_count': 12,
            'vertex_count': 8,
        })
        self.assertEqual(first['surface_type_histogram'], {'GeomAbs_Plane': 6})
        self.assertEqual(first['curve_type_histogram'], {'GeomAbs_Line': 12})

    def test_recognition_diagnostics_normalizes_strategy_failures(self):
        report = {
            'input': r'F:\\cases\\sample.step',
            'outcome': 'incomplete',
            'stage': 'recognition',
            'reason_code': 'search.no_verified_candidate',
            'elapsed_seconds': 1.25,
            'identity': {'source_sha256': 'source-hash'},
            'implementation': {'fingerprint': 'code-hash'},
            'recognition_failure': {
                'reason_code': 'search.no_verified_candidate',
                'prism': {'reason_code': 'capability.profile_boundary', 'attempts': []},
                'csg': {'reason_code': 'csg.candidate_budget_exhausted', 'attempts': [1, 2]},
            },
        }
        diagnostics = recognition_diagnostics(report)
        self.assertEqual(diagnostics['format'], 'step-recognition-diagnostics')
        self.assertTrue(diagnostics['sidecar_only'])
        self.assertEqual(diagnostics['case_id'], 'sample')
        self.assertEqual([item['strategy_id'] for item in diagnostics['recognizer_attempts']], ['prism', 'csg'])
        self.assertEqual(diagnostics['recognizer_attempts'][1]['attempt_count'], 2)

    def test_checkpoint_identity_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);identity=dict(source='abc',config={'limit':1})
            store=Checkpoints(root,identity);p=Plan.from_dict(box_document());store.save(1,p,{'method':'test'},{})
            restored=Checkpoints(root,identity,resume=True).restore();self.assertEqual(restored[1][0],p)
            with self.assertRaises(ValueError):Checkpoints(root,dict(source='different'),resume=True)
            path=root/'work/body_001/candidate.plan.json';d=p.data;d['nodes']['box']['args']['width']['literal']=5
            write_json(path,d)
            with self.assertRaises(ValueError):Checkpoints(root,identity,resume=True).restore()

    def test_event_sequence_survives_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'events.jsonl';e=Events(path,'case');e(event='start',phase='native')
            Events(path,'case')(event='end',phase='native')
            rows=[json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([r['sequence'] for r in rows],[1,2])

    def test_real_worker_timeout_keeps_ten_committed_bodies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            script='''import sys,time
from pathlib import Path
from step2code.plan import Plan,empty_document,node
from step2code.batch.checkpoint import Checkpoints
from step2code.batch.events import Events
root=Path(sys.argv[1]);store=Checkpoints(root,dict(source='controlled'))
events=Events(root/'events.jsonl','controlled')
for i in range(1,12):
 events(event='start',phase='native.execute',source_body_id=f'body_{i:03d}')
 if i==11:
  (root/'blocked').write_text('native call start')
  time.sleep(60)
 d=empty_document();d['nodes']['box']=node('solid.box',width=2,depth=3,height=4)
 d['outputs']=[dict(id=f'body_{i:03d}',node='box',expected_kind='solid')]
 store.save(i,Plan.from_dict(d),dict(method='controlled'),{})
 events(event='end',phase='native.execute',source_body_id=f'body_{i:03d}')
'''
            process=subprocess.Popen([sys.executable,'-c',script,str(root)],cwd=Path(__file__).resolve().parents[2],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                deadline=time.monotonic()+20
                while not (root/'blocked').exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue((root/'blocked').exists())
                process.kill();process.communicate(timeout=10)
                restored=Checkpoints(root,dict(source='controlled'),resume=True).restore()
                self.assertEqual(sorted(restored),list(range(1,11)))
                last=json.loads((root/'events.jsonl').read_text().splitlines()[-1])
                self.assertEqual((last['event'],last['source_body_id']),('start','body_011'))
            finally:
                if process.poll() is None:process.kill();process.communicate(timeout=10)

    def test_cli_timeout_batch_counts_and_preserves_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);inputs=root/'inputs';inputs.mkdir()
            for name in ('a.step','b.step'):(inputs/name).write_text('invalid input; worker times out before import')
            command=[sys.executable,'-m','step2code','--input',str(inputs),'--out',str(root/'run'),'--timeout','.05']
            result=subprocess.run(command,cwd=Path(__file__).resolve().parents[2],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            manifest=json.loads((root/'run/manifest.json').read_text());summary=json.loads((root/'run/summary.json').read_text())
            self.assertEqual((manifest['run_state'],manifest['selected_count'],manifest['completed_count']),('completed',2,2))
            self.assertEqual(summary['counts'],{'incomplete':2})
            before=(root/'run/manifest.json').read_bytes()
            again=subprocess.run(command,cwd=Path(__file__).resolve().parents[2],capture_output=True,text=True,timeout=30)
            self.assertNotEqual(again.returncode,0);self.assertEqual(before,(root/'run/manifest.json').read_bytes())

    def test_schema_is_independent_and_agrees_on_structural_cases(self):
        import jsonschema
        schema=json.loads((Path(__file__).resolve().parents[1]/'plan/schema.json').read_text())
        validator=jsonschema.Draft202012Validator(schema);jsonschema.Draft202012Validator.check_schema(schema)
        validator.validate(box_document())
        for location,value in [('version',999),('outputs',[]),('format','legacy')]:
            d=box_document();d[location]=value
            with self.assertRaises(jsonschema.ValidationError):validator.validate(d)

    def test_atomic_write_retries_only_windows_contention(self):
        import os
        actual=os.replace
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'state.json';write_json(target,dict(before=True))
            error=PermissionError('temporary sharing violation');error.winerror=32
            attempts=[]
            def replace(source,destination):
                attempts.append(source)
                if len(attempts)<3:raise error
                return actual(source,destination)
            with patch('step2code.plan.codec.os.replace',side_effect=replace):write_json(target,dict(after=True))
            self.assertEqual(len(attempts),3)
            self.assertEqual(json.loads(target.read_text()),dict(after=True))
            with patch('step2code.plan.codec.os.replace',side_effect=PermissionError('permanent permission failure')):
                with self.assertRaises(PermissionError):write_json(target,dict(after=False))
            self.assertEqual(json.loads(target.read_text()),dict(after=True))
            self.assertEqual(list(Path(tmp).glob('*.tmp')),[])

    @unittest.skipUnless(sys.platform=='win32','Windows venv launcher regression')
    def test_timeout_terminates_native_descendants(self):
        import ctypes
        from step2code.batch.process import terminate
        with tempfile.TemporaryDirectory() as tmp:
            marker=Path(tmp)/'child.pid'
            script='import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]); open(sys.argv[1],"w").write(str(p.pid)); time.sleep(60)'
            process=subprocess.Popen([sys.executable,'-c',script,str(marker)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            handle=None
            try:
                deadline=time.monotonic()+15
                while not marker.exists() and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(marker.exists())
                native=ctypes.WinDLL('kernel32',use_last_error=True)
                native.OpenProcess.restype=ctypes.c_void_p
                native.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint]
                native.CloseHandle.argtypes=[ctypes.c_void_p]
                handle=native.OpenProcess(0x00100000,False,int(marker.read_text()))
                self.assertTrue(handle)
                terminate(process);process.communicate(timeout=5)
                self.assertEqual(native.WaitForSingleObject(handle,5000),0)
            finally:
                if process.poll() is None:terminate(process)
                if handle:native.CloseHandle(handle)

    def test_cli_resume_skips_valid_completed_artifacts(self):
        from step2code.kernel.runtime import export_step
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.step';export_step([BRepPrimAPI_MakeBox(2,3,4).Shape()],source)
            command=[sys.executable,'-m','step2code','--input',str(source),'--out',str(root/'run'),'--timeout','30']
            first=subprocess.run(command,cwd=Path(__file__).resolve().parents[2],capture_output=True,text=True,timeout=40)
            self.assertEqual(first.returncode,0,first.stdout+first.stderr)
            report=root/'run/cases/source/report.json';before=report.read_bytes()
            second=subprocess.run(command+['--resume'],cwd=Path(__file__).resolve().parents[2],capture_output=True,text=True,timeout=40)
            self.assertEqual(second.returncode,0,second.stdout+second.stderr)
            self.assertEqual(before,report.read_bytes())
            manifest=root/'run/manifest.json';original=manifest.read_bytes()
            source.write_bytes(source.read_bytes()+b'\n')
            third=subprocess.run(command+['--resume'],cwd=Path(__file__).resolve().parents[2],capture_output=True,text=True,timeout=40)
            self.assertNotEqual(third.returncode,0)
            self.assertEqual(original,manifest.read_bytes())

    def test_changed_semantic_config_or_code_invalidates_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);identity=dict(source='fixed',configuration={'samples':8192},implementation='revision1')
            Checkpoints(root,identity).save(1,Plan.from_dict(box_document()),{}, {})
            for changed in (dict(identity,implementation='revision2'),dict(identity,configuration={'samples':4096})):
                with self.assertRaises(ValueError):Checkpoints(root,changed,resume=True)
