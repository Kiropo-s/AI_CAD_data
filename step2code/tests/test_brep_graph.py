import json
import unittest

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace
from OCP.gp import gp_Pnt

from step2code.kernel.brep_graph import graph_from_shape, graph_hash
from fixtures import fixtures, unsupported_freeform


class BrepGraphTests(unittest.TestCase):
    def test_box_has_stable_ids_and_hash(self):
        first = graph_from_shape(BRepPrimAPI_MakeBox(2, 3, 4).Shape())
        second = graph_from_shape(BRepPrimAPI_MakeBox(2, 3, 4).Shape())
        self.assertEqual(first.graph_hash, second.graph_hash)
        self.assertEqual(first['entity_counts']['solid_count'], 1)
        self.assertEqual(first['entity_counts']['face_count'], 6)
        self.assertEqual(first['entity_counts']['edge_count'], 12)
        self.assertEqual(first['entity_counts']['vertex_count'], 8)
        self.assertEqual(first['entities']['faces'][0]['id'], 'face_000001')
        self.assertEqual(first['entities']['edges'][-1]['id'], 'edge_000012')

    def test_roundtrip_hash_verification(self):
        graph = graph_from_shape(fixtures()['box_with_hole'])
        payload = json.loads(graph.dumps())
        self.assertEqual(payload['graph_hash'], graph.graph_hash)
        restored = type(graph).from_dict(payload)
        self.assertEqual(restored.graph_hash, graph.graph_hash)
        payload['entities']['faces'][0]['area_mm2'] += 1.0
        with self.assertRaises(ValueError):
            type(graph).from_dict(payload)

    def test_hole_has_outer_and_inner_loops_and_occurrences(self):
        graph = graph_from_shape(fixtures()['box_with_hole'])
        entities = graph['entities']
        self.assertTrue(any(face['inner_loop_ids'] for face in entities['faces']))
        self.assertEqual(len(entities['edge_occurrences']), sum(len(loop['edge_occurrence_ids']) for loop in entities['loops']))
        self.assertTrue(all(item['edge_id'].startswith('edge_') for item in entities['edge_occurrences']))
        self.assertTrue(all(item['start_vertex_id'] and item['end_vertex_id'] for item in entities['edge_occurrences']))

    def test_surface_and_curve_records_are_never_dropped(self):
        graph = graph_from_shape(unsupported_freeform())
        entities = graph['entities']
        self.assertEqual(len(entities['faces']), graph['entity_counts']['face_count'])
        self.assertEqual(len(entities['edges']), graph['entity_counts']['edge_count'])
        self.assertEqual(len(entities['surfaces']), graph['entity_counts']['surface_count'])
        self.assertEqual(len(entities['curves']), graph['entity_counts']['curve_count'])
        self.assertTrue(all(face['surface_id'] for face in entities['faces']))
        self.assertTrue(all(edge['curve_id'] for edge in entities['edges']))

    def test_empty_shape_returns_partial_graph(self):
        graph = graph_from_shape(BRepBuilderAPI_MakeFace().Face())
        self.assertIn(graph['status']['valid'], (True, False, None))
        self.assertIn('entities', graph.to_dict())


    def test_large_source_uses_explicit_summary_mode_without_dropping_entities(self):
        graph = graph_from_shape(fixtures()['disjoint'], max_detailed_solids=1)
        self.assertEqual(graph['detail_status'], 'summary_only')
        self.assertEqual(graph['status']['reason'], 'large_shape_signature_skipped')
        self.assertEqual(len(graph['entities']['solids']), graph['entity_counts']['solid_count'])
        self.assertEqual(len(graph['entities']['faces']), graph['entity_counts']['face_count'])
        self.assertEqual(len(graph['entities']['edges']), graph['entity_counts']['edge_count'])
        self.assertTrue(all(item['parameters']['parameter_status'] == 'skipped_large_source'
                            for item in graph['entities']['surfaces']))

    def test_graph_hash_function_matches_object(self):
        graph = graph_from_shape(fixtures()['cylinder'])
        payload = graph.to_dict()
        self.assertEqual(graph_hash(payload), graph.graph_hash)


if __name__ == '__main__':
    unittest.main()
