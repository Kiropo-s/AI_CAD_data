import math
import unittest

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.Geom import Geom_Ellipse
from OCP.GeomAbs import GeomAbs_Plane
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Ax2, gp_Dir, gp_Elips, gp_Pnt, gp_Vec

from step2code.recognition.profile import ProfileError, extract_curve, extract_profile, emit_profile_nodes
from fixtures import arc_prism


FRAME = {
    'origin': [0.0, 0.0, 0.0],
    'x_axis': [1.0, 0.0, 0.0],
    'y_axis': [0.0, 1.0, 0.0],
    'z_axis': [0.0, 0.0, 1.0],
}


def planar_cap(shape):
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        surface = BRepAdaptor_Surface(face)
        if surface.GetType() == GeomAbs_Plane:
            normal = surface.Plane().Axis().Direction().Coord()
            if abs(abs(normal[2]) - 1.0) < 1e-8:
                return face
        explorer.Next()
    raise AssertionError('no planar cap')


class ProfileExtractionTests(unittest.TestCase):
    def test_mixed_line_arc_loop_is_ordered_and_closed(self):
        evidence = extract_profile(planar_cap(arc_prism()), FRAME)
        self.assertEqual(evidence.outer_index, 0)
        self.assertEqual(evidence.self_intersection_status, 'pass')
        operations = {curve.operation for curve in evidence.loops[0].curves}
        self.assertIn('sketch.line', operations)
        self.assertIn('sketch.arc', operations)

    def test_full_ellipse_is_exact(self):
        ellipse = Geom_Ellipse(gp_Elips(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0)), 5, 3))
        edge = BRepBuilderAPI_MakeEdge(ellipse).Edge()
        evidence = extract_curve(edge, FRAME)
        self.assertEqual(evidence.operation, 'sketch.ellipse')
        self.assertEqual(evidence.params['source_parameter_range'][0], 0.0)
        self.assertAlmostEqual(abs(evidence.params['source_parameter_range'][1]), 2 * math.pi)

    def test_partial_ellipse_has_explicit_capability_reason(self):
        ellipse = Geom_Ellipse(gp_Elips(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0)), 5, 3))
        edge = BRepBuilderAPI_MakeEdge(ellipse, 0.0, math.pi / 2).Edge()
        with self.assertRaises(ProfileError) as caught:
            extract_curve(edge, FRAME)
        self.assertEqual(caught.exception.reason_code, 'profile.curve_unsupported')
        self.assertTrue(caught.exception.details['partial'])

    def test_reversed_bspline_direction_is_preserved_in_lowering(self):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
        from OCP.Geom import Geom_BSplineCurve
        from OCP.TColgp import TColgp_Array1OfPnt
        from OCP.TColStd import TColStd_Array1OfReal, TColStd_Array1OfInteger
        from OCP.gp import gp_Pnt

        poles = TColgp_Array1OfPnt(1, 4)
        for index, point in enumerate(((0, 0, 0), (6, 0, 0), (8, 10, 0), (0, 10, 0)), 1):
            poles.SetValue(index, gp_Pnt(*point))
        knots = TColStd_Array1OfReal(1, 2)
        knots.SetValue(1, 0.0); knots.SetValue(2, 1.0)
        multiplicities = TColStd_Array1OfInteger(1, 2)
        multiplicities.SetValue(1, 4); multiplicities.SetValue(2, 4)
        spline = Geom_BSplineCurve(poles, knots, multiplicities, 3, False)
        reversed_edge = TopoDS.Edge_s(BRepBuilderAPI_MakeEdge(spline).Edge().Reversed())
        closing_edge = BRepBuilderAPI_MakeEdge(gp_Pnt(0, 0, 0), gp_Pnt(0, 10, 0)).Edge()
        wire = BRepBuilderAPI_MakeWire()
        wire.Add(reversed_edge); wire.Add(closing_edge)

        evidence = extract_profile(BRepBuilderAPI_MakeFace(wire.Wire()).Face(), FRAME)
        spline_curve = evidence.loops[0].curves[0]
        self.assertEqual(spline_curve.operation, 'sketch.bspline')
        self.assertEqual(spline_curve.params['start'], [0.0, 10.0])
        self.assertEqual(spline_curve.params['end'], [0.0, 0.0])
        self.assertEqual(spline_curve.params['control_points'][0], [0.0, 10.0])
        self.assertEqual(spline_curve.params['control_points'][-1], [0.0, 0.0])
        nodes, _, _ = emit_profile_nodes(evidence, 'reversed', 'test')
        lowered = next(value for value in nodes.values() if value['op'] == 'curve.bspline2')
        self.assertFalse(lowered['args']['reversed']['literal'])

    def test_profile_error_is_machine_classified(self):
        edge = BRepBuilderAPI_MakeEdge(gp_Pnt(0, 0, 0), gp_Pnt(1, 0, 0)).Edge()
        wire = BRepBuilderAPI_MakeWire(edge).Wire()
        face = BRepBuilderAPI_MakeFace(wire).Face()
        with self.assertRaises(ProfileError) as caught:
            extract_profile(face, FRAME)
        self.assertIn(caught.exception.reason_code, {'profile.open_loop', 'profile.self_intersection'})


if __name__ == '__main__':
    unittest.main()
