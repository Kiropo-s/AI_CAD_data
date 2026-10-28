"""Contract attacks and invariants, including the old schema acceptance defects."""
import copy
import json
import math
from pathlib import Path
import unittest
from step2code.plan import Plan, PlanError, loads, node
from step2code.plan.graph import dependencies,topological_order,rewrite_ids
from helpers import box_document


class PlanContractTests(unittest.TestCase):
    def reject(self,doc,code):
        with self.assertRaises(PlanError) as caught:Plan.from_dict(doc)
        self.assertEqual(caught.exception.code,code)
        self.assertTrue(caught.exception.pointer or code.startswith('json.'))

    def test_roundtrip_canonical_hash(self):
        doc=box_document();p=Plan.from_dict(doc)
        self.assertEqual(p.sha256,loads(json.dumps(doc,sort_keys=False,indent=3)).sha256)
        self.assertEqual(p.data,doc)

    def test_immutable_ingress_and_egress(self):
        doc=box_document();p=Plan.from_dict(doc);doc['nodes'].clear();p.data['nodes'].clear()
        self.assertIn('box',p.data['nodes'])

    def test_legacy_formats_rejected(self):
        for doc in ({'ir_version':'0.1'},{'format':'step-reconstruction-plan-v1','plan_version':'1'}):
            with self.subTest(doc=doc):self.reject(doc,'schema.unsupported_format')

    def test_unknown_version_and_bool(self):
        for value in (999,'1',True,1.0):
            d=box_document();d['version']=value;self.reject(d,'schema.unsupported_version')

    def test_missing_empty_outputs(self):
        d=box_document();d['outputs']=[];self.reject(d,'schema.outputs')
        del d['outputs'];self.reject(d,'schema.fields')

    def test_missing_root(self):
        d=box_document();d['outputs'][0]['node']='absent';self.reject(d,'schema.missing_reference')

    def test_duplicate_output_id(self):
        d=box_document();d['outputs']*=2;self.reject(d,'schema.output_id')

    def test_unknown_fields_everywhere(self):
        for location in ('top','node','args','binding'):
            d=box_document()
            target={'top':d,'node':d['nodes']['box'],'args':d['nodes']['box']['args'],
                    'binding':d['nodes']['box']['args']['width']}[location]
            target['hidden']='box';self.reject(d,'schema.binding' if location=='binding' else 'schema.fields')

    def test_duplicate_json_keys(self):
        with self.assertRaises(PlanError) as e:loads('{"format":"step-plan","format":"x"}')
        self.assertEqual(e.exception.code,'json.duplicate_key')

    def test_nonfinite_rejected(self):
        for value in (math.nan,math.inf,-math.inf):
            d=box_document();d['nodes']['box']['args']['width']['literal']=value
            self.reject(d,'json.nonfinite')
        with self.assertRaises(PlanError):loads('{"value":NaN}')

    def test_unknown_op(self):
        d=box_document();d['nodes']['box']['op']='solid.brep';self.reject(d,'schema.unknown_op')

    def test_typed_reference(self):
        d=box_document();d['nodes']['scale']=node('shape.uniform_scale',dict(shape=['box']),factor=1.,center=[0,0,0])
        d['nodes']['scale']['inputs']['shape']=['scale'];d['outputs'][0]['node']='scale'
        self.reject(d,'graph.cycle')
        d=box_document();d['nodes']['box']=node('solid.extrude',dict(profile=['box']),distance=2.)
        self.reject(d,'type.input_mismatch')

    def test_missing_dependency_and_arity(self):
        d=box_document();d['nodes']['scale']=node('shape.uniform_scale',dict(shape=['unknown']),factor=1.,center=[0,0,0])
        d['outputs'][0]['node']='scale';self.reject(d,'schema.missing_reference')
        d['nodes']['scale']['inputs']['shape']=[];self.reject(d,'type.input_arity')

    def test_unreachable_graph_rejected(self):
        d=box_document();d['nodes']['orphan']=node('solid.sphere',radius=1.)
        self.reject(d,'graph.unreachable')

    def test_parameter_binding_and_dimension(self):
        d=box_document();d['parameters']['w']=dict(type='number',unit='mm',value=2.,editability='independent')
        d['nodes']['box']['args']['width']=dict(param='w');p=Plan.from_dict(d)
        self.assertEqual(p.with_parameters(dict(w=3.)).data['parameters']['w']['value'],3.)
        d['parameters']['w']['unit']='rad';self.reject(d,'type.parameter_dimension')

    def test_fixed_unknown_unused_parameters(self):
        d=box_document();d['parameters']['w']=dict(type='number',unit='mm',value=2.,editability='fixed')
        self.reject(d,'parameter.unused');d['nodes']['box']['args']['width']=dict(param='w')
        with self.assertRaises(PlanError):Plan.from_dict(d).with_parameters(dict(w=3.))
        with self.assertRaises(PlanError):Plan.from_dict(d).with_parameters(dict(missing=1))

    def test_frame_rejects_nonunit_nonorthogonal(self):
        d=box_document();d['nodes']['frame']=node('frame.rigid3',origin=[0,0,0],x_axis=[1,0,0],y_axis=[1,0,0])
        d['nodes']['out']=node('shape.transform',dict(shape=['box'],frame=['frame']));d['outputs'][0]['node']='out'
        self.reject(d,'geometry.frame');d['nodes']['frame']['args']['y_axis']['literal']=[0,2,0]
        self.reject(d,'type.value')

    def test_bool_is_not_length(self):
        d=box_document();d['nodes']['box']['args']['width']['literal']=True;self.reject(d,'type.value')

    def test_graph_order_and_rewrite_only_dependencies(self):
        nodes=dict(z=node('solid.sphere',radius=2),a=node('shape.uniform_scale',dict(shape=['z']),factor=1.,center=[0,0,0]))
        self.assertEqual(topological_order(nodes),['z','a'])
        renamed=rewrite_ids(nodes,dict(z='new'))
        self.assertEqual(dependencies(renamed['a']),('new',))
        self.assertEqual(renamed['new']['args'],nodes['z']['args'])
        with self.assertRaises(PlanError):rewrite_ids(nodes,dict(z='a'))

    def test_schema_operation_registry_consistency(self):
        from step2code.plan.registry import OPS
        schema=json.loads((Path(__file__).resolve().parents[1]/'plan/schema.json').read_text())
        choices=schema['properties']['nodes']['additionalProperties']['oneOf']
        self.assertEqual({v['properties']['op']['const'] for v in choices},set(OPS))
        for item in choices:
            op=item['properties']['op']['const']
            self.assertEqual(set(item['properties']['args']['required']),set(OPS[op].args))
            self.assertEqual(set(item['properties']['inputs']['required']),set(OPS[op].inputs))

    def test_proposed_example_is_accepted(self):
        d=json.loads((Path(__file__).resolve().parents[1]/'docs/ir-redesign/example-00000006.plan.json').read_text())
        Plan.from_dict(d)
