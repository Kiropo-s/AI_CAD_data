"""Real CAD regressions for recognition, standalone code, and metric decisions."""
from __future__ import annotations

import ast
import json
import math
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire, BRepBuilderAPI_Transform
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeCone, BRepPrimAPI_MakePrism, BRepPrimAPI_MakeSphere
from OCP.GC import GC_MakeArcOfCircle
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

from json2scapi.evaluation.metrics import read_step, volume
from json2scapi.runtime.worker import compound, export_step, returned_shapes
from step2code.pipeline import convert_case
from step2code.recognition import RecognitionOptions, UnsupportedGeometry, recognize
from step2code.validation import evaluate


def cylinder(radius=10, height=5, origin=(0,0,0)):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*origin),gp_Dir(0,0,1)),radius,height).Shape()


def transform(shape):
    matrix=gp_Trsf()
    matrix.SetRotation(gp_Ax1(gp_Pnt(0,0,0),gp_Dir(1,2,3)),0.73)
    matrix.SetTranslationPart(gp_Vec(37,-21,59))
    return BRepBuilderAPI_Transform(shape,matrix,True).Shape()


def arc_prism(major=False):
    if major:
        start, middle, end = gp_Pnt(5,0,0), gp_Pnt(-5,0,0), gp_Pnt(0,5,0)
        wire=BRepBuilderAPI_MakeWire()
        wire.Add(BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(start,middle,end).Value()).Edge())
        wire.Add(BRepBuilderAPI_MakeEdge(end,start).Edge())
    else:
        a,b,m,c,d = gp_Pnt(0,0,0),gp_Pnt(20,0,0),gp_Pnt(25,5,0),gp_Pnt(20,10,0),gp_Pnt(0,10,0)
        wire=BRepBuilderAPI_MakeWire()
        for edge in [BRepBuilderAPI_MakeEdge(a,b).Edge(),
                     BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(b,m,c).Value()).Edge(),
                     BRepBuilderAPI_MakeEdge(c,d).Edge(),BRepBuilderAPI_MakeEdge(d,a).Edge()]:
            wire.Add(edge)
    return BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(wire.Wire()).Face(),gp_Vec(0,0,3)).Shape()


def fixtures():
    box=BRepPrimAPI_MakeBox(30,20,5).Shape()
    annulus=BRepAlgoAPI_Cut(cylinder(),cylinder(4)).Shape()
    hole=BRepAlgoAPI_Cut(box,cylinder(2,5,(7,8,0))).Shape()
    holes=BRepAlgoAPI_Cut(hole,cylinder(3,5,(20,12,0))).Shape()
    disjoint=compound([box,cylinder(4,8,(50,0,0))])
    overlapping=compound([box,BRepPrimAPI_MakeBox(gp_Pnt(25,0,0),30,20,5).Shape()])
    return {'box':box, 'cylinder':cylinder(), 'annulus':annulus,
            'box_with_hole':hole,'two_holes':holes, 'oblique_box':transform(box),
            'oblique_annulus':transform(annulus),'line_arc':arc_prism(),
            'major_arc':arc_prism(True),'disjoint':disjoint,'overlapping_bodies':overlapping,
            'offset_cylinder':cylinder(7,4,(600,-200,51))}



def unsupported_freeform():
    """An anisotropically transformed sphere is outside analytic CSG support."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_GTransform
    from OCP.gp import gp_GTrsf,gp_Mat
    transform=gp_GTrsf();transform.SetVectorialPart(gp_Mat(1.,0.,0.,0.,1.3,0.,0.,0.,.8))
    return BRepBuilderAPI_GTransform(BRepPrimAPI_MakeSphere(8).Shape(),transform,True).Shape()

class ConversionTests(unittest.TestCase):
    def test_standalone_cases_and_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name,shape in fixtures().items():
                with self.subTest(name=name):
                    source=root/(name+'.step')
                    export_step([shape],source)
                    target=root/name
                    report=convert_case(source,target)
                    self.assertEqual(report['status'],'train_ready', json.dumps(report,ensure_ascii=False)[-5000:])
                    self.assertFalse(report['history_recovered'])
                    self.assertEqual(report['geometry']['checks'],{
                        'volume_relative_error':'pass','volume_iou':'pass','chamfer_relative':'pass'})
                    code=(target/'model.scapi.py').read_text(encoding='utf-8')
                    tree=ast.parse(code)
                    imports=[node for node in ast.walk(tree) if isinstance(node,(ast.Import,ast.ImportFrom))]
                    self.assertEqual(len(imports),1)
                    self.assertEqual(imports[0].names[0].name,'simplecadapi')
                    self.assertNotIn(str(source),code)
                    for part in ('def make_profile_01(', 'def apply_feature_01(', 'def build():', 'BODIES = None'):
                        self.assertIn(part,code)
                    namespace=runpy.run_path(str(target/'model.scapi.py'))
                    first=returned_shapes(namespace['build']())
                    second=returned_shapes(namespace['build']())
                    self.assertAlmostEqual(sum(volume(s) for s in first),sum(volume(s) for s in second),places=6)
                    self.assertIsNot(first[0],second[0])
                    self.assertTrue((target/'model.symbols.json').is_file())
                    self.assertTrue((target/'model.prepared.ir.json').is_file())

    def test_deterministic_recognition_and_codegen(self):
        from json2scapi.codegen.readable import StructuredEmitter
        shape=transform(arc_prism())
        first,_=recognize(shape,Path('source.step'))
        second,_=recognize(shape,Path('source.step'))
        self.assertEqual(first,second)
        self.assertEqual(StructuredEmitter(first,'sample').emit(),StructuredEmitter(second,'sample').emit())

    def test_top_level_dimensions_are_read_at_build_time(self):
        from json2scapi.codegen.readable import StructuredEmitter
        ir,_=recognize(cylinder(),Path('cylinder.step'))
        code=StructuredEmitter(ir,'cylinder').emit()
        namespace={};exec(code,namespace)
        original=volume(returned_shapes(namespace['build']())[0])
        radius=next(k for k in namespace if k.endswith('_RADIUS_MM'))
        namespace[radius]*=2
        changed=volume(returned_shapes(namespace['build']())[0])
        self.assertAlmostEqual(changed/original,4,places=7)

    def test_unsupported_shapes_do_not_produce_passing_code(self):
        unsupported=unsupported_freeform()
        with self.assertRaises(UnsupportedGeometry):
            recognize(unsupported,Path('unsupported.step'))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'unsupported.step';export_step([unsupported],source)
            report=convert_case(source,root/'case')
            self.assertEqual(report['status'],'provisional')
            self.assertEqual(report['decision']['reason'],'unsupported_geometry')
            self.assertFalse((root/'case'/'model.scapi.py').exists())

    def test_recognition_limits(self):
        with self.assertRaises(UnsupportedGeometry):
            recognize(compound([cylinder(),cylinder(3,4,(40,0,0))]),Path('two.step'),RecognitionOptions(max_solids=1))

    def test_bad_step_is_provisional(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'broken.step';source.write_text('not STEP')
            report=convert_case(source,root/'case')
            self.assertEqual(report['status'],'provisional')
            self.assertEqual(report['decision']['reason'],'read_reference_error')

    def test_existing_case_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'report.json').write_text('{}')
            with self.assertRaises(FileExistsError):
                convert_case(Path('missing.step'),root)
            self.assertEqual((root/'report.json').read_text(),'{}')


class MetricTests(unittest.TestCase):
    def test_same_geometry_passes(self):
        shape=BRepPrimAPI_MakeBox(20,15,5).Shape()
        self.assertEqual(evaluate(shape,shape)['decision']['status'],'train_ready')

    def test_world_mode_rejects_translation(self):
        reference=BRepPrimAPI_MakeBox(20,15,5).Shape()
        shifted=BRepPrimAPI_MakeBox(gp_Pnt(4,0,0),20,15,5).Shape()
        result=evaluate(reference,shifted)
        self.assertEqual(result['decision']['status'],'rejected')
        self.assertEqual(result['checks']['volume_relative_error'],'pass')
        self.assertEqual(result['checks']['volume_iou'],'fail')

    def test_world_mode_rejects_scale(self):
        result=evaluate(cylinder(10,5),cylinder(20,10))
        self.assertEqual(result['decision']['status'],'rejected')
        self.assertEqual(result['checks']['volume_relative_error'],'fail')

    def test_missing_required_measurement_is_not_pass(self):
        with patch('step2code.validation._pose_metrics',side_effect=RuntimeError('boolean failure')):
            result=evaluate(cylinder(),cylinder())
        self.assertEqual(result['decision']['status'],'provisional')
        self.assertTrue(result['metric_errors'])

    def test_similarity_is_explicitly_available(self):
        result=evaluate(cylinder(10,5),cylinder(20,10,(100,0,0)),mode='similarity')
        self.assertEqual(result['decision']['status'],'train_ready')
        self.assertEqual(result['evaluation_version'],'similarity-shape-v4')


if __name__=='__main__':
    unittest.main()
