import json
import unittest

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt
from step2code.kernel.runtime import compound
from step2code.kernel.brep_graph import graph_from_shape
from step2code.kernel.brep_relations import BrepRelationIndex
from fixtures import cylinder, fixtures, examples


class BrepRelationTests(unittest.TestCase):
    def test_incidence_and_shared_edge_adjacency(self):
        index = graph_from_shape(fixtures()['box']).relation_index()
        face = 'face_000001'
        face_edges = index.find('face-edge', face)
        self.assertEqual(len(face_edges), 4)
        self.assertTrue(all(item['status'] == 'true' for item in face_edges))
        edge = face_edges[0]['right']
        edge_record = index._edges[edge]
        vertex = edge_record['start_vertex_id']
        self.assertEqual(index.relation('edge-vertex', edge, vertex)['status'], 'true')
        adjacent = index.find('face-face', face)
        self.assertEqual(len(adjacent), 4)
        self.assertTrue(all(item['evidence']['shared_edge_ids'] for item in adjacent))

    def test_loop_and_body_containment(self):
        index = graph_from_shape(fixtures()['box_with_hole']).relation_index()
        face = next(item for item in index._faces.values() if item['inner_loop_ids'])
        loop = face['inner_loop_ids'][0]
        occurrence = index.loop_to_occurrences[loop][0]
        self.assertEqual(index.relation('containment', face['id'], loop)['status'], 'true')
        self.assertEqual(index.relation('containment', loop, occurrence)['status'], 'true')

    def test_parallel_coplanar_and_perpendicular_are_evidence_backed(self):
        first = BRepPrimAPI_MakeBox(2, 3, 4).Shape()
        second = BRepPrimAPI_MakeBox(gp_Pnt(10, 0, 0), 2, 3, 4).Shape()
        index = graph_from_shape(compound([first, second])).relation_index()
        faces = list(index._faces)
        parallel = []
        coplanar = []
        perpendicular = []
        same_normal = []
        for left_index, left in enumerate(faces):
            for right in faces[left_index + 1:]:
                if index.relation('parallel', left, right)['status'] == 'true':
                    parallel.append((left, right))
                if index.relation('coplanar', left, right)['status'] == 'true':
                    coplanar.append((left, right))
                if index.relation('perpendicular', left, right)['status'] == 'true':
                    perpendicular.append((left, right))
                if index.relation('same-normal', left, right)['status'] == 'true':
                    same_normal.append((left, right))
        self.assertTrue(parallel)
        self.assertTrue(coplanar)
        self.assertTrue(perpendicular)
        self.assertTrue(same_normal)
        evidence = index.relation('coplanar', *coplanar[0])
        self.assertEqual(evidence['status'], 'true')
        self.assertLessEqual(evidence['evidence']['plane_distance'], evidence['tolerance'])

    def test_coaxial_and_unknown_status(self):
        index = graph_from_shape(compound([cylinder(3, 5), cylinder(2, 4, (0, 0, 10))])).relation_index()
        cylindrical = [face['id'] for face in index._faces.values() if face['surface_type'] == 'GeomAbs_Cylinder']
        self.assertGreaterEqual(len(cylindrical), 2)
        result = index.relation('coaxial', cylindrical[0], cylindrical[1])
        self.assertEqual(result['status'], 'true')
        mixed = graph_from_shape(compound([cylinder(3, 5), examples()['sphere']])).relation_index()
        cylinder_face = next(face['id'] for face in mixed._faces.values() if face['surface_type'] == 'GeomAbs_Cylinder')
        sphere_face = next(face['id'] for face in mixed._faces.values() if face['surface_type'] == 'GeomAbs_Sphere')
        self.assertEqual(mixed.relation('coaxial', cylinder_face, sphere_face)['status'], 'unknown')

    def test_relation_index_roundtrip(self):
        index = graph_from_shape(fixtures()['box']).relation_index()
        payload = json.loads(index.dumps())
        restored = BrepRelationIndex.from_dict(payload)
        self.assertEqual(restored.index_hash, index.index_hash)
        self.assertEqual(restored.relation('face-edge', 'face_000001', 'edge_000001')['status'], 'true')
        payload['incidence']['face_to_loops']['face_000001'] = []
        with self.assertRaises(ValueError):
            BrepRelationIndex.from_dict(payload)


if __name__ == '__main__':
    unittest.main()
