"""CAD and process regressions for the separate direct route."""
import ast
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire, BRepBuilderAPI_Transform
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeSphere, BRepPrimAPI_MakeCone, BRepPrimAPI_MakePrism
from OCP.GC import GC_MakeArcOfCircle
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from json2scapi.evaluation.metrics import volume, symmetric_difference_volume, bbox
from json2scapi.runtime.worker import compound, export_step, returned_shapes
from step2code_direct.geometry import Options, UnsupportedGeometry, source_solids, recognize_prism
from step2code_direct.emitter import DirectEmitter
from step2code_direct.pipeline import convert_case, verify_bodies, write_json
from step2code_direct.cli import run_case
from step2code_direct.compare import compare_runs

ROOT = Path(__file__).resolve().parents[2]


def cylinder(radius=10, height=5, origin=(0,0,0)):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*origin), gp_Dir(0,0,1)), radius, height).Shape()


def transform(shape):
    pose = gp_Trsf()
    pose.SetRotation(gp_Ax1(gp_Pnt(0,0,0), gp_Dir(1,2,3)), 0.73)
    pose.SetTranslationPart(gp_Vec(37,-21,59))
    return BRepBuilderAPI_Transform(shape, pose, True).Shape()


def arc_prism(major=False):
    wire = BRepBuilderAPI_MakeWire()
    if major:
        a, middle, b = gp_Pnt(5,0,0), gp_Pnt(-5,0,0), gp_Pnt(0,5,0)
        edges = [BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(a,middle,b).Value()).Edge(),
                 BRepBuilderAPI_MakeEdge(b,a).Edge()]
    else:
        a,b,m,c,d = gp_Pnt(0,0,0), gp_Pnt(20,0,0), gp_Pnt(25,5,0), gp_Pnt(20,10,0), gp_Pnt(0,10,0)
        edges = [BRepBuilderAPI_MakeEdge(a,b).Edge(), BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(b,m,c).Value()).Edge(),
                 BRepBuilderAPI_MakeEdge(c,d).Edge(), BRepBuilderAPI_MakeEdge(d,a).Edge()]
    for edge in edges:
        wire.Add(edge)
    return BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(wire.Wire()).Face(), gp_Vec(0,0,3)).Shape()


def fixtures():
    box = BRepPrimAPI_MakeBox(30,20,5).Shape()
    annulus = BRepAlgoAPI_Cut(cylinder(), cylinder(4)).Shape()
    hole = BRepAlgoAPI_Cut(box, cylinder(2,5,(7,8,0))).Shape()
    holes = BRepAlgoAPI_Cut(hole, cylinder(3,5,(20,12,0))).Shape()
    return {'box':box, 'cylinder':cylinder(), 'annulus':annulus, 'box_with_hole':hole, 'two_holes':holes,
            'oblique_box':transform(box), 'oblique_annulus':transform(annulus), 'line_arc':arc_prism(),
            'major_arc':arc_prism(True), 'disjoint':compound([box,cylinder(4,8,(50,0,0))]),
            'overlapping_bodies':compound([box,BRepPrimAPI_MakeBox(gp_Pnt(25,0,0),30,20,5).Shape()]),
            'offset_cylinder':cylinder(7,4,(600,-200,51))}


def generated(shape):
    emitter = DirectEmitter()
    for index, solid in enumerate(source_solids(shape, Options()), 1):
        emitter.add_prism(recognize_prism(solid,index,Options()), index)
    return emitter.emit()


class GeometryTests(unittest.TestCase):
    def test_twelve_real_build_export_metric_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, shape in fixtures().items():
                with self.subTest(name=name):
                    source = root/(name+'.step')
                    export_step([shape], source)
                    output = root/name
                    report = convert_case(source, output)
                    self.assertEqual(report['status'], 'train_ready', json.dumps(report, ensure_ascii=False)[-4000:])
                    self.assertFalse(report['cad_ir_used'])
                    self.assertTrue(report['geometry_validated'])
                    self.assertFalse(list(output.glob('*.ir.json')))
                    self.assertEqual(report['source_coverage_validation']['decision']['status'], 'train_ready')
                    namespace = runpy.run_path(str(output/'model.scapi.py'))
                    first = returned_shapes(namespace['build']())
                    second = returned_shapes(namespace['build']())
                    self.assertEqual(len(first),len(second))
                    self.assertIsNot(first[0], second[0])
                    self.assertAlmostEqual(sum(volume(s) for s in first), sum(volume(s) for s in second), places=6)

    def test_deterministic_code_and_standalone_import_contract(self):
        shape = transform(arc_prism(True))
        code = generated(shape)
        self.assertEqual(code, generated(shape))
        tree = ast.parse(code)
        imports = [node for node in ast.walk(tree) if isinstance(node,(ast.Import,ast.ImportFrom))]
        self.assertEqual(len(imports),1)
        self.assertEqual(imports[0].names[0].name,'simplecadapi')
        self.assertNotIn('exec(',code)
        self.assertNotIn('eval(',code)
        self.assertNotIn('read_step',code)

    def test_radius_distance_and_position_edits_keep_other_body(self):
        shape = compound([cylinder(),cylinder(3,4,(40,0,0))])
        namespace = {}
        exec(generated(shape), namespace)
        original = returned_shapes(namespace['build']())
        namespace['FEATURE_01_CIRCLE_01_RADIUS_MM'] *= 1.1
        changed = returned_shapes(namespace['build']())
        self.assertAlmostEqual(volume(changed[0])/volume(original[0]),1.21,places=7)
        namespace['FEATURE_01_DISTANCE_MM'] *= 1.1
        elongated = returned_shapes(namespace['build']())
        self.assertAlmostEqual(volume(elongated[0])/volume(changed[0]),1.1,places=7)
        origin = namespace['BODY_01_ORIGIN_MM']
        namespace['BODY_01_ORIGIN_MM'] = tuple(a+b for a,b in zip(origin,(3,-4,8)))
        moved = returned_shapes(namespace['build']())
        self.assertAlmostEqual(volume(moved[0]),volume(elongated[0]),places=6)
        for k,delta in enumerate((3,-4,8,3,-4,8)):
            self.assertAlmostEqual(bbox(moved[0])[k]-bbox(elongated[0])[k],delta,places=6)
        self.assertLess(symmetric_difference_volume(original[1],moved[1])/volume(original[1]),1e-9)

    def test_invalid_edited_parameters_and_frame_are_rejected(self):
        for key, value in [('FEATURE_01_DISTANCE_MM',-1), ('FEATURE_01_CIRCLE_01_RADIUS_MM',float('nan')),
                           ('BODY_01_X_AXIS',(2,0,0)), ('BODY_01_Y_AXIS',(1,0,0))]:
            with self.subTest(key=key):
                namespace = {}
                exec(generated(cylinder()),namespace)
                namespace[key] = value
                with self.assertRaises(ValueError):
                    namespace['build']()

    def test_native_results_are_not_labeled_as_serialized_ir(self):
        from step2code_direct import geometry, emitter, pipeline
        forbidden = ('step2code.recognition','step2code.codegen','step2code.composite_codegen','json2scapi.ir','json2scapi.codegen')
        for module in (geometry, emitter, pipeline):
            tree = ast.parse(Path(module.__file__).read_text(encoding='utf-8-sig'))
            for node in ast.walk(tree):
                if isinstance(node,ast.ImportFrom):
                    self.assertFalse(any((node.module or '').startswith(name) for name in forbidden))
                elif isinstance(node,ast.Import):
                    self.assertFalse(any(alias.name.startswith(forbidden) for alias in node.names))
        native = recognize_prism(source_solids(cylinder(),Options())[0],1,Options())
        self.assertTrue(hasattr(native.face,'IsNull'))
        self.assertFalse(hasattr(native,'entities'))

    def test_isolated_python_can_build_generated_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = Path(tmp)/'model.scapi.py'
            code.write_text(generated(cylinder()),encoding='utf-8')
            script = "import runpy,sys; ns=runpy.run_path(sys.argv[1]); a=ns['build'](); b=ns['build'](); assert a is not b; print('standalone-ok')"
            result = subprocess.run([sys.executable,'-I','-c',script,str(code)],capture_output=True,text=True,timeout=30,cwd=tmp)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('standalone-ok',result.stdout)

    def test_unsupported_geometry_and_partial_sources_do_not_pass(self):
        sphere = BRepPrimAPI_MakeSphere(5).Shape()
        shapes = [sphere, BRepPrimAPI_MakeCone(8,4,10).Shape(), compound([cylinder(),sphere])]
        with tempfile.TemporaryDirectory() as tmp:
            for index, shape in enumerate(shapes):
                source = Path(tmp)/f'unsupported-{index}.step'
                export_step([shape],source)
                output = Path(tmp)/str(index)
                report = convert_case(source,output,options=Options(enable_hybrid=False))
                self.assertEqual(report['status'],'provisional')
                self.assertEqual(report['decision']['reason'],'unsupported_geometry')
                self.assertFalse((output/'model.scapi.py').exists())

    def test_malformed_step_and_output_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'broken.step'
            source.write_text('not a STEP file',encoding='utf-8')
            output = Path(tmp)/'case'
            report = convert_case(source,output)
            self.assertEqual(report['status'],'provisional')
            previous = (output/'report.json').read_bytes()
            with self.assertRaises(FileExistsError):
                convert_case(source,output)
            self.assertEqual(previous,(output/'report.json').read_bytes())

    def test_limits_and_loose_geometry(self):
        with self.assertRaises(ValueError):
            Options(max_candidates=0)
        with self.assertRaises(UnsupportedGeometry):
            source_solids(compound([cylinder(),cylinder(3,4,(40,0,0))]),Options(max_solids=1))
        loose = BRepBuilderAPI_MakeEdge(gp_Pnt(100,0,0),gp_Pnt(101,0,0)).Edge()
        with self.assertRaises(UnsupportedGeometry):
            source_solids(compound([cylinder(),loose]),Options())

    def test_per_body_coverage_rejects_equal_union_with_wrong_mapping(self):
        first, second = cylinder(),cylinder(4,5,(40,0,0))
        self.assertEqual(verify_bodies([first,second],[second,first],1e-7)['decision']['status'],'rejected')
        self.assertEqual(verify_bodies([first,second],[first],1e-7)['decision']['status'],'rejected')

    def test_required_metric_error_cannot_be_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'source.step'
            export_step([cylinder()],source)
            error = {'decision':{'status':'provisional','reason':'required_metric_error'}}
            with patch('step2code_direct.pipeline.evaluate',return_value=error):
                report = convert_case(source,Path(tmp)/'case')
            self.assertEqual(report['status'],'provisional')
            self.assertIsNone(report['geometry_validated'])


class WorkerTests(unittest.TestCase):
    def run_worker(self, script, *, timeout=10, stale=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'source.step'
            source.write_text('source',encoding='utf-8')
            output = root/'case'
            output.mkdir()
            if stale:
                write_json(output/'report.json',{'status':'train_ready','stage':'finished','geometry_validated':True})
            return run_case([sys.executable,'-c',script],output,{'mode':'world','samples':8192},source,timeout)

    def test_timeout_is_provisional(self):
        report = self.run_worker('import time; time.sleep(5)',timeout=0.1)
        self.assertEqual(report['decision']['reason'],'worker_timeout')
        self.assertEqual(report['status'],'provisional')

    def test_crash_overrides_even_a_finished_passing_report(self):
        report = self.run_worker('raise SystemExit(7)',stale=True)
        self.assertEqual(report['status'],'provisional')
        self.assertEqual(report['decision']['reason'],'worker_crash')
        self.assertIsNone(report['geometry_validated'])

    def test_missing_report_is_provisional(self):
        self.assertEqual(self.run_worker('pass')['decision']['reason'],'worker_missing_report')

    def test_missing_source_does_not_abort_batch_accounting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root/'case'
            output.mkdir()
            report = run_case([sys.executable,'-c','pass'], output, {'mode':'world','samples':8192}, root/'missing.step',10)
            self.assertEqual(report['status'],'provisional')
            self.assertIsNone(report['input_sha256'])
            self.assertIn('input_digest_error',report)

    def test_cli_continues_and_preserves_existing_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'input'
            source.mkdir()
            (source/'a.STP').write_text('invalid STEP',encoding='utf-8')
            export_step([cylinder()],source/'b.step')
            output = root/'out'
            command = [sys.executable,'-m','step2code_direct','--input',str(source),'--out',str(output)]
            result = subprocess.run(command,capture_output=True,text=True,cwd=ROOT,timeout=60)
            self.assertEqual(result.returncode,1,result.stderr)
            summary = json.loads((output/'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['counts'],{'provisional':1,'train_ready':1})
            previous = (output/'summary.json').read_bytes()
            result = subprocess.run(command,capture_output=True,text=True,cwd=ROOT,timeout=30)
            self.assertEqual(result.returncode,2)
            self.assertEqual(previous,(output/'summary.json').read_bytes())


class ComparisonTests(unittest.TestCase):
    def make_run(self, root, name, rows, *, direct=False, samples=8192):
        path = root/(name+'.json')
        payload = dict(samples=samples,validation_mode='world',cases=rows)
        if direct:
            payload.update(cad_ir_used=False,capabilities=['analytic_straight_extrusion'])
        write_json(path,payload)
        return path

    def row(self, name='a.step', digest='a'*64, status='train_ready'):
        return dict(input=name,input_sha256=digest,status=status)

    def test_matching_requires_recorded_hash_and_reports_unmatched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = self.make_run(root,'base',[self.row(), self.row('b.step','b'*64,'provisional')])
            b = self.make_run(root,'direct',[self.row(), self.row('b.step','c'*64)],direct=True)
            result = compare_runs(a,b)
            self.assertEqual(result['matched_count'],1)
            self.assertEqual(len(result['unmatched_baseline']),1)
            self.assertEqual(len(result['unmatched_direct']),1)
            self.assertEqual(result['acceptance_rate_difference_percentage_points'],0)
            self.assertEqual(result['inference'],'descriptive_comparison_only')

    def test_settings_mismatch_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = self.make_run(root,'base',[self.row()])
            b = self.make_run(root,'direct',[self.row()],direct=True,samples=4096)
            result = compare_runs(a,b)
            self.assertFalse(result['cases'][0]['same_measurement_settings'])
            self.assertTrue(any('settings' in notice for notice in result['notices']))

    def test_missing_historical_hash_is_not_recomputed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            row = self.row()
            row.pop('input_sha256')
            a = self.make_run(root,'base',[row])
            b = self.make_run(root,'direct',[self.row()],direct=True)
            with self.assertRaisesRegex(ValueError,'Missing recorded source SHA256'):
                compare_runs(a,b)

    def test_incomplete_run_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = self.make_run(root,'base',[self.row()])
            b = self.make_run(root,'direct',[self.row()],direct=True)
            payload = json.loads(b.read_text(encoding='utf-8'))
            payload['run_status'] = 'running'
            write_json(b,payload)
            with self.assertRaisesRegex(ValueError,'incomplete'):
                compare_runs(a,b)


if __name__ == '__main__':
    unittest.main()
