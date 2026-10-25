"""Synthetic regressions for the source-independent IR geometry contract."""
import ast
import copy
import importlib.util
import json
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from json2scapi.ir.geometry2d import arc_geometry, curve_from_node, location
from json2scapi.ir.preparation import prepare_geometry
from json2scapi.ir.validation import prepare_ir, validate_ir
from json2scapi.codegen.readable import StructuredEmitter
from json2scapi.codegen.api import generate, generate_readable_with_metadata, fmt_num, SketchBuilder
from json2scapi.ir.regions import profile_regions
from json2scapi.adapters.deepcad import convert, ConversionError


def example():
    return json.loads((ROOT/'json2scapi/examples/line_arc_plate.ir.json').read_text(encoding='utf-8'))


def circle_profile(specs):
    nodes = {}
    for i,(x,y,r) in enumerate(specs):
        nodes[f'c{i}'] = {'op':'sketch.circle','params':{'center':[x,y],'radius':r}}
        nodes[f'l{i}'] = {'op':'sketch.loop','inputs':[f'c{i}'],'params':{}}
    return {'inputs':[f'l{i}' for i in range(len(specs))]},nodes


class PreparationTests(unittest.TestCase):
    def test_exact_ir_is_idempotent_and_input_is_unchanged(self):
        ir = example()
        original = copy.deepcopy(ir)
        prepared,metadata = prepare_ir(ir)
        again,second = prepare_ir(prepared)
        self.assertEqual(ir,original)
        self.assertEqual(prepared,again)
        self.assertEqual(second['changes'],[])
        self.assertEqual(metadata['version'],'ir-geometry-v1')

    def test_gaps_are_bounded_recorded_and_all_entrypoints_prepare(self):
        for gap in (5e-7,5e-4):
            ir = example()
            ir['tolerance']['length'] = 1e-3
            ir['entities']['curve_demo_l1']['params']['end'][0] -= gap
            original = copy.deepcopy(ir)
            self.assertEqual(validate_ir(ir),[])
            emitter = StructuredEmitter(ir,'gapped')
            self.assertEqual(ir,original)
            p = emitter.payload['entities']
            self.assertEqual(p['curve_demo_l1']['params']['end'],p['curve_demo_a1']['params']['start'])
            adjustments = emitter.metadata['preparation']['changes']
            self.assertTrue(any(a['kind']=='endpoint' for a in adjustments))
            self.assertTrue(all(a.get('displacement',0) <= 1e-3 for a in adjustments))
            code,meta = generate_readable_with_metadata(ir,'gapped')
            self.assertEqual(code,emitter.emit())
            self.assertEqual(meta,emitter.metadata)
            self.assertEqual(code,generate(ir,'gapped'))
            ast.parse(code)

    def test_outside_tolerance_is_rejected(self):
        ir = example()
        ir['entities']['curve_demo_l1']['params']['end'][0] -= 0.01
        self.assertTrue(validate_ir(ir))
        with self.assertRaisesRegex(ValueError,'gap'):
            StructuredEmitter(ir,'bad')

    def test_transitive_shared_endpoint_cluster_is_not_overmerged(self):
        # One endpoint appears in two loops. Pairwise gaps are <= tolerance,
        # but the stable representative is farther than tolerance from C.
        nodes = {}
        for key,start,end in (('a',[0,0],[10,0]),('b',[10,0],[0.75,0]),
                              ('c',[1.5,0],[10,0]),('d',[10,0],[0.75,0])):
            nodes[key]={'op':'sketch.line','params':{'start':start,'end':end}}
        nodes['l1']={'op':'sketch.loop','inputs':['a','b']}
        nodes['l2']={'op':'sketch.loop','inputs':['c','b']}
        with self.assertRaisesRegex(ValueError,'transitive/shared-curve conflict'):
            prepare_geometry({'entities':nodes,'tolerance':{'length':1.0}})

    def test_close_distinct_loops_are_not_snapped(self):
        profile,nodes=circle_profile([(0,0,2),(4.0005,0,2)])
        prepared,meta=prepare_geometry({'entities':nodes,'tolerance':{'length':0.001}})
        self.assertEqual(meta['changes'],[])
        self.assertEqual(prepared['entities'],nodes)
        with self.assertRaisesRegex(ValueError,'touch or overlap'):
            profile_regions(profile,nodes,0.001)

    def test_auxiliary_arc_conflicts_are_strictly_rejected(self):
        for field,value in (('center',[20.1,5]),('radius',5.1),('ccw',False)):
            ir=example()
            ir['entities']['curve_demo_a1']['params'][field]=value
            with self.assertRaisesRegex(ValueError,f'auxiliary {field} conflicts'):
                generate(ir,'conflict')

    def test_midpoint_perturbation_cannot_be_silently_ignored(self):
        ir=example()
        ir['entities']['curve_demo_a1']['params']['mid'][0]+=0.0005
        self.assertTrue(validate_ir(ir))
        for key in ('center','radius','ccw'):
            ir['entities']['curve_demo_a1']['params'].pop(key)
        self.assertEqual(validate_ir(ir),[])
        prepared,_=prepare_ir(ir)
        self.assertNotEqual(prepared['entities']['curve_demo_a1']['params']['radius'],5)
        self.assertNotEqual(generate(ir,'arc'),generate(example(),'arc'))

    def test_arc_orientation_major_arc_and_translation(self):
        for mid,ccw in (([0,1],True),([0,-1],False)):
            # start (1,0) -> end (-1,0): both are half circles.
            p={'start':[1,0],'mid':mid,'end':[-1,0]}
            center,r,sweep=arc_geometry(p)
            self.assertEqual(sweep>0,ccw)
            self.assertAlmostEqual(abs(sweep),math.pi)
        c,r,s=arc_geometry({'start':[1,0],'mid':[-1,0],'end':[0,1]})
        self.assertAlmostEqual(s,-1.5*math.pi)
        c,r,s=arc_geometry({'start':[1e9+1,1e9],'mid':[1e9,1e9+1],'end':[1e9-1,1e9]})
        self.assertEqual(c,(1e9,1e9))
        self.assertAlmostEqual(r,1)
        with self.assertRaisesRegex(ValueError,'collinear'):
            arc_geometry({'start':[0,0],'mid':[1,1e-14],'end':[2,0]})

    def test_zero_edge_and_self_crossing_are_rejected(self):
        ir=example()
        ir['entities']['curve_demo_l1']['params']['end']=[0,0]
        self.assertTrue(validate_ir(ir))
        points=[[0,0],[2,2],[0,2],[2,0]]
        nodes={f'e{i}':{'op':'sketch.line','params':{'start':p,'end':points[(i+1)%4]}}
               for i,p in enumerate(points)}
        nodes['loop']={'op':'sketch.loop','inputs':list(nodes)}
        with self.assertRaisesRegex(ValueError,'self-intersecting'):
            prepare_geometry({'entities':nodes})

    def test_no_source_and_deterministic_roundtrip_numbers(self):
        ir=example()
        ir.pop('extensions',None)
        for node in ir['entities'].values():
            node.pop('source',None)
        code=generate(ir,'plain')
        self.assertEqual(code,generate(copy.deepcopy(ir),'plain'))
        ast.parse(code)
        for value in (1e-14,1.2345678901234567,1e12+0.125):
            self.assertEqual(float(fmt_num(value)),value)
        builder=SketchBuilder('plane',{},'s')
        self.assertNotEqual(builder.point((0,0)),builder.point((5e-10,0)))

    def test_near_boundary_circle_hole_is_valid(self):
        theta=math.pi/128
        p,n=circle_profile([(0,0,100),(98.99*math.cos(theta),98.99*math.sin(theta),1)])
        self.assertEqual(profile_regions(p,n,0.001),[('l0',['l1'])])

    def test_circle_contact_intersection_and_island(self):
        for x in (8,10,10.0005):
            p,n=circle_profile([(0,0,5),(x,0,5)])
            with self.assertRaisesRegex(ValueError,'touch or overlap'):
                profile_regions(p,n,0.001)
        p,n=circle_profile([(0,0,10),(0,0,6),(0,0,2)])
        self.assertEqual(profile_regions(p,n),[('l0',['l1']),('l2',[])])

    def test_two_semicircles_and_adjacent_overlap(self):
        nodes={
            'a':{'op':'sketch.arc','params':{'start':[1,0],'mid':[0,1],'end':[-1,0]}},
            'b':{'op':'sketch.arc','params':{'start':[-1,0],'mid':[0,-1],'end':[1,0]}},
            'loop':{'op':'sketch.loop','inputs':['a','b']},
        }
        prepared,_=prepare_geometry({'entities':nodes})
        self.assertEqual(profile_regions({'inputs':['loop']},prepared['entities']),[('loop',[])])
        nodes['b']['params']['mid']=[0,1]
        with self.assertRaisesRegex(ValueError,'overlapping'):
            prepare_geometry({'entities':nodes})

    def test_source_metadata_does_not_change_geometric_preparation(self):
        ir=example()
        ir['entities']['curve_demo_l1']['params']['end'][0]-=5e-7
        with_source=copy.deepcopy(ir)
        for key,node in with_source['entities'].items():
            node['source']={'format':'step-test','entity_id':key}
        a,ma=prepare_ir(ir)
        b,mb=prepare_ir(with_source)
        for node in b['entities'].values():
            node.pop('source',None)
        self.assertEqual(a,b)
        self.assertEqual(ma,mb)

    def test_line_arc_containment(self):
        ir=example()
        nodes=ir['entities']
        nodes['circle']={'op':'sketch.circle','params':{'center':[23,5],'radius':1}}
        nodes['hole']={'op':'sketch.loop','inputs':['circle'],'params':{}}
        self.assertEqual(profile_regions({'inputs':['loop_demo_p1_0','hole']},nodes),[('loop_demo_p1_0',['hole'])])
        curves=[curve_from_node(nodes[k]) for k in nodes['loop_demo_p1_0']['inputs']]
        self.assertEqual(location((23,5),curves,1e-6),1)
        self.assertEqual(location((26,5),curves,1e-6),0)

    def test_asymmetric_active_taper_is_rejected(self):
        path=ROOT/'datasets/DeepCAD/cad_json/0000/00000007.json'
        raw=json.loads(path.read_text(encoding='utf-8'))
        feature=next(n for n in raw['entities'].values() if n['type']=='ExtrudeFeature')
        feature['extent_type']='TwoSidesFeatureExtentType'
        feature['extent_one']['distance']['value']=0.02
        feature['extent_two']['distance']['value']=0.01
        for one,two in ((0.1,0),(0,0.1),(0.1,0.2)):
            feature['extent_one']['taper_angle']['value']=one
            feature['extent_two']['taper_angle']['value']=two
            with self.assertRaisesRegex(ConversionError,'differing side taper'):
                convert(raw)
        feature['extent_one']['taper_angle']['value']=0.1
        feature['extent_two']['taper_angle']['value']=0.1
        self.assertEqual(validate_ir(convert(raw)),[])
        feature['extent_two']['taper_angle']['value']=float('nan')
        with self.assertRaisesRegex(ConversionError,'non-finite'):
            convert(raw)


@unittest.skipUnless(importlib.util.find_spec('OCP'),'CAD kernel missing')
class PreparedGeometryTests(unittest.TestCase):
    def test_near_boundary_hole_builds_with_expected_volume(self):
        from OCP.BRepCheck import BRepCheck_Analyzer
        from json2scapi.adapters.deepcad import convert
        path=ROOT/'datasets/DeepCAD/cad_json/0000/00000007.json'
        ir=convert(json.loads(path.read_text(encoding='utf-8')))
        nodes=ir['entities']
        profile=next(n for n in nodes.values() if n['op']=='sketch.profile')
        circle=nodes[nodes[profile['inputs'][0]]['inputs'][0]]
        circle['params']={'center':[0,0],'radius':100}
        theta=math.pi/128
        nodes['hole_circle']={'type':'SketchCircle','op':'sketch.circle','inputs':[],
            'params':{'center':[98.99*math.cos(theta),98.99*math.sin(theta)],'radius':1}}
        nodes['hole_loop']={'type':'SketchLoop','op':'sketch.loop','inputs':['hole_circle'],
            'params':{'closed':True,'is_outer':False}}
        profile['inputs'].append('hole_loop')
        profile['params']['regions'][0]['holes'].append('hole_loop')
        ns={}
        exec(generate(ir,'near_hole'),ns)
        model=ns['build']()
        self.assertTrue(BRepCheck_Analyzer(model.wrapped).IsValid())
        distance=nodes[ir['results'][0]]['params']['distance']
        self.assertAlmostEqual(model.get_volume(),math.pi*(100**2-1)*distance,places=5)

    def test_gap_builds_and_midpoint_changes_geometry(self):
        from OCP.BRepCheck import BRepCheck_Analyzer
        def build(ir):
            ns={}
            exec(generate(ir,'synthetic'),ns)
            model=ns['build']()
            self.assertTrue(BRepCheck_Analyzer(model.wrapped).IsValid())
            return model
        original=build(example())
        for gap in (5e-7,5e-4):
            ir=example()
            ir['tolerance']['length']=1e-3
            ir['entities']['curve_demo_l1']['params']['end'][0]-=gap
            self.assertAlmostEqual(build(ir).get_volume(),original.get_volume(),places=6)
        ir=example()
        p=ir['entities']['curve_demo_a1']['params']
        p['mid'][0]+=0.0005
        for key in ('center','radius','ccw'):
            p.pop(key)
        self.assertGreater(build(ir).get_volume(),original.get_volume()+0.01)


if __name__=='__main__':
    unittest.main()
