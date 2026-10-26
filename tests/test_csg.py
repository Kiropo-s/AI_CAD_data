"""Kernel regressions for hybrid cap extrusion, CSG, and independent edits."""
import ast
import copy
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut,BRepAlgoAPI_Fuse,BRepAlgoAPI_Common
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon,BRepBuilderAPI_MakeFace
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox,BRepPrimAPI_MakeCylinder,BRepPrimAPI_MakeCone,BRepPrimAPI_MakePrism
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.gp import gp_Pnt,gp_Dir,gp_Ax2,gp_Vec
from json2scapi.evaluation.metrics import volume,bbox,symmetric_difference_volume
from json2scapi.runtime.worker import returned_shapes,compound,export_step
from step2code.recognition import recognize,RecognitionOptions,UnsupportedGeometry
from step2code.composite_codegen import CompositeEmitter
from step2code.csg import recognize_csg
from step2code.pipeline import convert_case
from test_step2scapi import cylinder,unsupported_freeform,transform


def hybrid_examples():
    box=BRepPrimAPI_MakeBox(20,15,8).Shape()
    vertical=BRepAlgoAPI_Cut(box,cylinder(2,8,(5,4,0))).Shape()
    cross=BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0,11,4),gp_Dir(1,0,0)),1.5,13).Shape()
    crossed=BRepAlgoAPI_Cut(vertical,cross).Shape()
    blind=BRepAlgoAPI_Cut(box,cylinder(2,4,(6,7,4))).Shape()
    boss=BRepAlgoAPI_Fuse(box,cylinder(3,5,(14,7,8))).Shape()
    polygon=BRepBuilderAPI_MakePolygon()
    for p in ((-3,-2,0),(3,-2,0),(4,0,0),(3,2,0),(-3,2,0),(-4,0,0)):polygon.Add(gp_Pnt(*p))
    polygon.Close();prism=BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(polygon.Wire()).Face(),gp_Vec(0,0,3)).Shape()
    cone=BRepPrimAPI_MakeCone(5,3,3).Shape()
    tapered=BRepAlgoAPI_Common(prism,cone).Shape()
    tapered=BRepAlgoAPI_Cut(tapered,cylinder(.7,3)).Shape()
    return dict(crossed_blind_holes=crossed,blind_hole=blind,localized_boss=boss,tapered_polygon=tapered)


class CSGTests(unittest.TestCase):
    def test_hybrid_examples_build_export_and_measure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name,shape in hybrid_examples().items():
                with self.subTest(name=name):
                    source=root/(name+'.step');export_step([shape],source)
                    report=convert_case(source,root/name)
                    self.assertEqual(report['status'],'train_ready',report.get('error',report.get('decision')))
                    self.assertEqual(report['source_coverage_validation']['status'],'pass')
                    self.assertEqual(report['roundtrip']['decision']['status'],'train_ready')
                    self.assertFalse(report['history_recovered'])
                    self.assertEqual(report['assembly_validation']['status'],'unsupported')
                    methods=[f['method'] for f in report['recognition']['features']]
                    self.assertTrue(any('csg' in method for method in methods),methods)
                    namespace=runpy.run_path(str(root/name/'model.scapi.py'))
                    replay=returned_shapes(namespace['build']())[0]
                    self.assertLessEqual(symmetric_difference_volume(shape,replay)/volume(shape),1e-7)

    def test_independent_scale_placement_and_fresh_builds(self):
        shape=hybrid_examples()['crossed_blind_holes']
        other=BRepPrimAPI_MakeBox(gp_Pnt(70,0,0),4,5,6).Shape()
        payload,evidence=recognize(compound([shape,other]),Path('mixed.step'))
        emitter=CompositeEmitter(payload,evidence);code=emitter.emit();namespace={};exec(code,namespace)
        before=returned_shapes(namespace['build']());instance=emitter.metadata['instances'][0];bindings=instance['bindings']
        namespace[bindings['profile_scale']]=1.1
        after=returned_shapes(namespace['build']())
        self.assertTrue(__import__('math').isclose(volume(after[0])/volume(before[0]),1.1**3,rel_tol=1e-7))
        self.assertEqual(bbox(after[1]),bbox(before[1]))
        namespace[bindings['profile_scale']]=1
        origin=namespace[bindings['origin']];namespace[bindings['origin']]=[v+d for v,d in zip(origin,(3,-4,8))]
        after=returned_shapes(namespace['build']())
        for delta,target in zip([a-b for a,b in zip(bbox(after[0]),bbox(before[0]))],(3,-4,8,3,-4,8)):self.assertAlmostEqual(delta,target,places=6)
        self.assertEqual(bbox(after[1]),bbox(before[1]))
        namespace[bindings['profile_scale']]=0
        with self.assertRaises(ValueError):namespace['build']()

    def test_csg_limits_are_real_rejection_limits(self):
        shape=hybrid_examples()['crossed_blind_holes']
        with self.assertRaises(UnsupportedGeometry):recognize_csg(shape,1,RecognitionOptions(max_csg_candidates=1,max_csg_depth=0))
        try:
            _,_,limited=recognize_csg(shape,1,RecognitionOptions(max_csg_steps=1,max_csg_depth=0))
            self.assertLessEqual(limited['selected']['boolean_count'],1)
        except UnsupportedGeometry:pass
        for kwargs in ({'max_csg_candidates':0},{'max_csg_steps':0},{'max_csg_faces':0},{'max_csg_parts':0},{'max_csg_seconds':0},{'max_csg_seconds':float('nan')},{'max_csg_depth':-1},{'max_csg_depth':4}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):RecognitionOptions(**kwargs)

    def test_associative_union_keeps_disconnected_intermediates_out_of_replay(self):
        from step2code.csg import normalize_recipe
        nodes=dict(a=dict(kind='sphere',origin=[0,0,0],radius=1),
                   b=dict(kind='sphere',origin=[3,0,0],radius=1),
                   c=dict(kind='sphere',origin=[1.5,0,0],radius=1),
                   left=dict(kind='union',inputs=['a','b']),
                   root=dict(kind='union',inputs=['left','c']))
        normalized=normalize_recipe(nodes,'root')
        self.assertEqual(normalized['root']['inputs'],['a','b','c'])
        self.assertNotIn('left',normalized)

    def test_invalid_recipe_and_operation_rejected(self):
        payload,evidence=recognize(hybrid_examples()['blind_hole'],Path('blind.step'))
        key=evidence['features'][0]['operation_id']
        for mode in ('unsupported','missing','cycle','scale'):
            bad=copy.deepcopy(payload);params=bad['entities'][key]['params'];recipe=params['recipe'];root=recipe['root']
            if mode=='unsupported':recipe['nodes'][root]['kind']='load_source_step'
            elif mode=='missing':recipe['root']='absent'
            elif mode=='cycle':recipe['nodes'][root]=dict(kind='cut',inputs=[root,root])
            else:params['profile_scale']=2
            with self.subTest(mode=mode),self.assertRaises(ValueError):CompositeEmitter(bad,evidence).emit()

    def test_unsupported_partial_source_never_exports_success(self):
        source=compound([hybrid_examples()['blind_hole'],unsupported_freeform()])
        with self.assertRaises(UnsupportedGeometry) as captured:recognize(source,Path('partial.step'))
        self.assertEqual(captured.exception.diagnostics['recognized_solid_count'],1)
        self.assertEqual(captured.exception.diagnostics['coverage_status'],'partial')
        self.assertIn('csg',captured.exception.diagnostics)

    def test_deterministic_standalone_code_only_uses_scapi(self):
        shape=transform(hybrid_examples()['crossed_blind_holes'])
        first,ev=recognize(shape,Path('standalone.step'));second,ev2=recognize(shape,Path('standalone.step'))
        code=CompositeEmitter(first,ev).emit()
        self.assertEqual(code,CompositeEmitter(second,ev2).emit())
        tree=ast.parse(code)
        self.assertEqual([a.name for n in tree.body if isinstance(n,ast.Import) for a in n.names],['simplecadapi'])
        self.assertNotIn('read_step',code)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'model.py';path.write_text(code+'\nif __name__ == "__main__":\n    build()\n    build()\n',encoding='utf-8')
            result=subprocess.run([sys.executable,'-I',str(path)],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__':unittest.main()
