import json
from pathlib import Path
import tempfile
import unittest
from step2code.pipeline import convert_case
from step2code.kernel.runtime import export_step,returned_shapes,compound
from step2code.kernel.import_step import read_reference
from step2code.verification.geometry import evaluate
from step2code.verification.policy import decide,THRESHOLDS
from step2code.verification.coverage import verify_source_coverage
from step2code.recognition import recognize
from step2code.verification.metrics import volume
from fixtures import fixtures,unsupported_freeform


class PipelineTests(unittest.TestCase):
    def test_complete_artifacts_and_new_outcomes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.step';export_step([fixtures()['two_holes']],source)
            r=convert_case(source,root/'case')
            self.assertEqual(r['outcome'],'accepted',r)
            self.assertEqual(r['eligibility']['parameter_edit'],'not_run')
            self.assertEqual(r['eligibility']['serialization'],'pass')
            self.assertTrue((root/'case/model.plan.json').exists())
            self.assertFalse((root/'case/model.ir.json').exists())
            self.assertFalse((root/'case/model.prepared.ir.json').exists())
            for name in ('source','native','wrapper','step_readback'):
                self.assertEqual(r['signatures'][name]['solid_count'],1)
            state=json.loads((root/'case/case-state.json').read_text())
            self.assertEqual(state['body_states']['1']['state'],'complete')
            self.assertTrue((root/'case/work/body_001/candidate.plan.json').is_file())

    def test_invalid_input_and_output_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'bad.step';source.write_text('not STEP')
            r=convert_case(source,root/'case');self.assertEqual(r['outcome'],'invalid_input')
            before=(root/'case/report.json').read_bytes()
            with self.assertRaises(FileExistsError):convert_case(source,root/'case')
            self.assertEqual(before,(root/'case/report.json').read_bytes())

    def test_partial_source_keeps_checkpoint_no_final_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'partial.step'
            export_step([fixtures()['box'],unsupported_freeform()],source)
            r=convert_case(source,root/'case')
            self.assertEqual(r['outcome'],'incomplete');self.assertFalse((root/'case/model.plan.json').exists())
            self.assertTrue((root/'case/work/body_001/candidate.plan.json').exists())

    def test_source_body_count_coverage(self):
        source=fixtures()['disjoint'];plan,provenance=recognize(source,Path('nonexistent.step'))
        from step2code.kernel.execute import execute
        bodies=execute(plan).bodies
        good=verify_source_coverage(source,bodies,provenance['output_map'],plan.data['outputs'])
        self.assertEqual(good['status'],'pass')
        self.assertEqual(verify_source_coverage(source,bodies[:1],provenance['output_map'],plan.data['outputs'])['status'],'fail')
        self.assertEqual(verify_source_coverage(source,list(reversed(bodies)),provenance['output_map'],plan.data['outputs'])['status'],'fail')

    def test_world_translation_and_scale_rejected(self):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCP.gp import gp_Trsf,gp_Vec,gp_Pnt
        source=fixtures()['box']
        for mode in ('translation','scale'):
            t=gp_Trsf()
            if mode=='translation':t.SetTranslation(gp_Vec(12,0,0))
            else:t.SetScale(gp_Pnt(0,0,0),1.1)
            candidate=BRepBuilderAPI_Transform(source,t,True).Shape()
            r=evaluate(source,candidate)
            self.assertEqual(r['decision']['outcome'],'rejected')

    def test_required_error_is_not_pass(self):
        r=dict(checks={key:'pass' for key in THRESHOLDS},metric_errors=[dict(required=True)],coordinate_check=dict(status='resolved'))
        self.assertEqual(decide(r)['outcome'],'incomplete')

    def test_reliable_mismatch_dominates_other_metric_error(self):
        for key in THRESHOLDS:
            r=dict(checks={name:'pass' for name in THRESHOLDS},metric_errors=[dict(required=True)],coordinate_check=dict(status='resolved'))
            r['checks'][key]='fail';self.assertEqual(decide(r)['outcome'],'rejected')

    def test_impossible_occupied_union_volume_is_unreliable(self):
        from unittest.mock import patch
        from step2code.verification.conditioning import occupied_space
        from step2code.verification.metrics import GeometryMetricsError
        # A union cannot shrink below either operand or exceed their sum, even if kernel validity says true.
        for values in ([10,7,3],[10,7,20]):
            with patch('step2code.verification.conditioning.volume',side_effect=values):
                with self.assertRaises(GeometryMetricsError):occupied_space(fixtures()['disjoint'])

    def test_meter_source_imported_to_mm_once(self):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'meter.step'
            export_step([BRepPrimAPI_MakeBox(.03,.02,.005).Shape()],source)
            text=source.read_text();self.assertIn('SI_UNIT(.MILLI.,.METRE.)',text)
            source.write_text(text.replace('SI_UNIT(.MILLI.,.METRE.)','SI_UNIT($,.METRE.)'))
            shape,evidence=read_reference(source)
            self.assertIn('metre',evidence['source_length_units']);self.assertAlmostEqual(volume(shape),3000,places=5)
            result=convert_case(source,root/'case');self.assertEqual(result['outcome'],'accepted',result)

    def test_valid_surface_input_reports_domain_not_invalid_topology(self):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.gp import gp_Pln,gp_Pnt,gp_Dir
        face=BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(0,0,0),gp_Dir(0,0,1)),-2,2,-2,2).Face()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'surface.step';export_step([face],source)
            report=convert_case(source,root/'case')
            self.assertEqual(report['outcome'],'incomplete',report)
            self.assertEqual(report['reason_code'],'domain.no_solids')
            self.assertFalse((root/'case/model.plan.json').exists())
