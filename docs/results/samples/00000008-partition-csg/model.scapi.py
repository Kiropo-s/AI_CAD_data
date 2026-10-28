"""STEP Plan 1 reconstruction; measured geometry, inferred operations, no source history."""
import math
import simplecadapi as scad

# Independent parameters; dimensions use mm, angles use radians.
P001_BODY_001_FACTOR = 1.0  # 1; independent
P002_BODY_001_ORIGIN = [0.0, 0.0, 0.0]  # mm; independent
P003_BODY_001_X_AXIS = [1.0, 0.0, 0.0]  # 1; independent
P004_BODY_001_Y_AXIS = [0.0, 1.0, 0.0]  # 1; independent

BODIES = None


def make_profile_001(*, meridian=False):
    """Build exact local profile curves and its explicit holes."""
    edge_01_001_half_1 = scad.make_three_point_arc_redge(
        start=_arc_point([0.0, 0.0], 39.370000000000005, -6.283185307179586, meridian),
        middle=_arc_point([0.0, 0.0], 39.370000000000005, -4.71238898038469, meridian),
        end=_arc_point([0.0, 0.0], 39.370000000000005, -3.141592653589793, meridian),
    )
    edge_01_001_half_2 = scad.make_three_point_arc_redge(
        start=_arc_point([0.0, 0.0], 39.370000000000005, -3.141592653589793, meridian),
        middle=_arc_point([0.0, 0.0], 39.370000000000005, -1.5707963267948966, meridian),
        end=_arc_point([0.0, 0.0], 39.370000000000005, 0.0, meridian),
    )
    wire_01 = scad.make_wire_from_edges_rwire(edges=[edge_01_001_half_1, edge_01_001_half_2])
    edge_02_001_half_1 = scad.make_three_point_arc_redge(
        start=_arc_point([0.0, 0.0], 38.1, 0.0, meridian),
        middle=_arc_point([0.0, 0.0], 38.1, 1.5707963267948966, meridian),
        end=_arc_point([0.0, 0.0], 38.1, 3.141592653589793, meridian),
    )
    edge_02_001_half_2 = scad.make_three_point_arc_redge(
        start=_arc_point([0.0, 0.0], 38.1, 3.141592653589793, meridian),
        middle=_arc_point([0.0, 0.0], 38.1, 4.71238898038469, meridian),
        end=_arc_point([0.0, 0.0], 38.1, 6.283185307179586, meridian),
    )
    wire_02 = scad.make_wire_from_edges_rwire(edges=[edge_02_001_half_1, edge_02_001_half_2])
    return scad.make_face_from_wires_rface(outer_wire=wire_01, inner_wires=[wire_02], normal=(0, -1, 0) if meridian else (0, 0, 1))


def make_profile_002(*, meridian=False):
    """Build exact local profile curves and its explicit holes."""
    edge_01_001 = scad.make_line_redge(start=_point([38.1, 1.27], meridian), end=_point([39.37000000000001, 1.27], meridian))
    edge_01_002 = scad.make_spline_redge(
        control_points=[_point(p, meridian) for p in [[39.370000000000005, 1.2700000000000018], [39.370000000000005, 2.54]]],
        degree=1, knots=[-1.2699999999999985, 0.0],
        multiplicities=[2, 2], weights=[1.0, 1.0],
        periodic=False,
    )
    edge_01_003 = scad.make_line_redge(start=_point([39.370000000000005, 2.54], meridian), end=_point([37.4194090512249, 2.54], meridian))
    edge_01_004 = scad.make_line_redge(start=_point([37.4194091, 2.54], meridian), end=_point([36.10460830148864, 2.54], meridian))
    edge_01_005 = scad.make_spline_redge(
        control_points=[_point(p, meridian) for p in [[36.10460830148864, 2.5400000000000005], [36.44490377749324, 1.269999993969332]]],
        degree=1, knots=[-2.9698969726298134, -1.6550962172653012],
        multiplicities=[2, 2], weights=[1.0, 1.0],
        periodic=False,
    )
    edge_01_006 = scad.make_line_redge(start=_point([36.4449037764917, 1.27], meridian), end=_point([38.1, 1.27], meridian))
    wire_01 = scad.make_wire_from_edges_rwire(edges=[edge_01_001, edge_01_002, edge_01_003, edge_01_004, edge_01_005, edge_01_006])
    return scad.make_face_from_wires_rface(outer_wire=wire_01, inner_wires=[], normal=(0, -1, 0) if meridian else (0, 0, 1))


def make_profile_003(*, meridian=False):
    """Build exact local profile curves and its explicit holes."""
    edge_01_001 = scad.make_spline_redge(
        control_points=[_point(p, meridian) for p in [[37.419409054490096, 2.5399999878056576], [36.738818102445265, 5.080000000000004]]],
        degree=1, knots=[-5.259202996484424, -2.629601485617698],
        multiplicities=[2, 2], weights=[1.0, 1.0],
        periodic=False,
    )
    edge_01_002 = scad.make_line_redge(start=_point([36.738818102445265, 5.08], meridian), end=_point([35.424017352711274, 5.08], meridian))
    edge_01_003 = scad.make_spline_redge(
        control_points=[_point(p, meridian) for p in [[35.424017352711274, 5.080000000000001], [36.10460830148864, 2.54]]],
        degree=1, knots=[-5.599498470872025, -2.9698969726298134],
        multiplicities=[2, 2], weights=[1.0, 1.0],
        periodic=False,
    )
    edge_01_004 = scad.make_line_redge(start=_point([36.10460830148864, 2.54], meridian), end=_point([37.419409099999996, 2.54], meridian))
    wire_01 = scad.make_wire_from_edges_rwire(edges=[edge_01_001, edge_01_002, edge_01_003, edge_01_004])
    return scad.make_face_from_wires_rface(outer_wire=wire_01, inner_wires=[], normal=(0, -1, 0) if meridian else (0, 0, 1))


def make_profile_004(*, meridian=False):
    """Build exact local profile curves and its explicit holes."""
    edge_01_001 = scad.make_line_redge(start=_point([140.3164246031788, -117.27673682398287], meridian), end=_point([84.46998841227021, -117.27673682398287], meridian))
    edge_01_002 = scad.make_three_point_arc_redge(
        start=_arc_point([112.3932065077245, -112.3932065077245], 28.347045299999998, -2.968452375549724, meridian),
        middle=_arc_point([112.3932065077245, -112.3932065077245], 28.347045299999998, -1.5707963267948961, meridian),
        end=_arc_point([112.3932065077245, -112.3932065077245], 28.347045299999998, -0.17314027804006837, meridian),
    )
    wire_01 = scad.make_wire_from_edges_rwire(edges=[edge_01_001, edge_01_002])
    return scad.make_face_from_wires_rface(outer_wire=wire_01, inner_wires=[], normal=(0, -1, 0) if meridian else (0, 0, 1))


def make_profile_005(*, meridian=False):
    """Build exact local profile curves and its explicit holes."""
    edge_01_001 = scad.make_spline_redge(
        control_points=[_point(p, meridian) for p in [[36.738818102445265, 5.080000000000003], [36.39852262805658, 6.3500000000000005]]],
        degree=1, knots=[-6.574003745605527, -5.259202996484424],
        multiplicities=[2, 2], weights=[1.0, 1.0],
        periodic=False,
    )
    edge_01_002 = scad.make_line_redge(start=_point([36.39852262805659, 6.35], meridian), end=_point([35.08372187832259, 6.35], meridian))
    edge_01_003 = scad.make_spline_redge(
        control_points=[_point(p, meridian) for p in [[35.08372187832259, 6.35], [35.424017352711274, 5.080000000000001]]],
        degree=1, knots=[-6.914299219993128, -5.599498470872025],
        multiplicities=[2, 2], weights=[1.0, 1.0],
        periodic=False,
    )
    edge_01_004 = scad.make_line_redge(start=_point([35.424017352711274, 5.08], meridian), end=_point([36.73881810244527, 5.08], meridian))
    wire_01 = scad.make_wire_from_edges_rwire(edges=[edge_01_001, edge_01_002, edge_01_003, edge_01_004])
    return scad.make_face_from_wires_rface(outer_wire=wire_01, inner_wires=[], normal=(0, -1, 0) if meridian else (0, 0, 1))


def make_profile_006(*, meridian=False):
    """Build exact local profile curves and its explicit holes."""
    edge_01_001 = scad.make_line_redge(start=_point([140.43478322365706, -116.54350198211203], meridian), end=_point([139.15028836636364, -116.54350198211203], meridian))
    edge_01_002 = scad.make_three_point_arc_redge(
        start=_arc_point([112.3932065077245, -112.3932065077245], 27.0770453, -0.15388388207782855, meridian),
        middle=_arc_point([112.3932065077245, -112.3932065077245], 27.0770453, -0.19530226112257854, meridian),
        end=_arc_point([112.3932065077245, -112.3932065077245], 27.0770453, -0.23672064016732852, meridian),
    )
    edge_01_003 = scad.make_three_point_arc_redge(
        start=_arc_point([112.3932065077245, -112.3932065077245], 27.0770453, -0.23672064016732808, meridian),
        middle=_arc_point([112.3932065077245, -112.3932065077245], 27.0770453, -1.6122147058396452, meridian),
        end=_arc_point([112.3932065077245, -112.3932065077245], 27.0770453, -2.9877087715119623, meridian),
    )
    edge_01_004 = scad.make_line_redge(start=_point([85.63612464908536, -116.54350198211203], meridian), end=_point([84.35162979179195, -116.54350198211203], meridian))
    edge_01_005 = scad.make_three_point_arc_redge(
        start=_arc_point([112.3932065077245, -112.3932065077245], 28.347045299999998, -2.994654308190883, meridian),
        middle=_arc_point([112.3932065077245, -112.3932065077245], 28.347045299999998, -1.5707963267948972, meridian),
        end=_arc_point([112.3932065077245, -112.3932065077245], 28.347045299999998, -0.14693834539891126, meridian),
    )
    wire_01 = scad.make_wire_from_edges_rwire(edges=[edge_01_001, edge_01_002, edge_01_003, edge_01_004, edge_01_005])
    return scad.make_face_from_wires_rface(outer_wire=wire_01, inner_wires=[], normal=(0, -1, 0) if meridian else (0, 0, 1))


def make_profile_007(*, meridian=False):
    """Build exact local profile curves and its explicit holes."""
    edge_01_001 = scad.make_spline_redge(
        control_points=[_point(p, meridian) for p in [[36.39852262805658, 6.350000000000001], [35.377636204890536, 10.16]]],
        degree=1, knots=[-10.518405992968841, -6.574003745605527],
        multiplicities=[2, 2], weights=[1.0, 1.0],
        periodic=False,
    )
    edge_01_002 = scad.make_line_redge(start=_point([35.377636204890536, 10.16], meridian), end=_point([34.062835455156545, 10.16], meridian))
    edge_01_003 = scad.make_spline_redge(
        control_points=[_point(p, meridian) for p in [[34.062835455156545, 10.159999999999998], [35.08372187832259, 6.3500000000000005]]],
        degree=1, knots=[-10.858701467356441, -6.914299219993128],
        multiplicities=[2, 2], weights=[1.0, 1.0],
        periodic=False,
    )
    edge_01_004 = scad.make_line_redge(start=_point([35.08372187832259, 6.35], meridian), end=_point([36.39852262805658, 6.35], meridian))
    wire_01 = scad.make_wire_from_edges_rwire(edges=[edge_01_001, edge_01_002, edge_01_003, edge_01_004])
    return scad.make_face_from_wires_rface(outer_wire=wire_01, inner_wires=[], normal=(0, -1, 0) if meridian else (0, 0, 1))


def make_profile_008(*, meridian=False):
    """Build exact local profile curves and its explicit holes."""
    edge_01_001 = scad.make_line_redge(start=_point([35.03734073050185, 11.43], meridian), end=_point([33.7225399813915, 11.43], meridian))
    edge_01_002 = scad.make_spline_redge(
        control_points=[_point(p, meridian) for p in [[33.72253998205617, 11.429999995191963], [34.062835455156545, 10.159999999999998]]],
        degree=1, knots=[-12.173502211499903, -10.858701467356441],
        multiplicities=[2, 2], weights=[1.0, 1.0],
        periodic=False,
    )
    edge_01_003 = scad.make_line_redge(start=_point([34.062835455156545, 10.16], meridian), end=_point([35.377636204890536, 10.16], meridian))
    edge_01_004 = scad.make_spline_redge(
        control_points=[_point(p, meridian) for p in [[35.377636204890536, 10.16], [35.03734073050185, 11.430000000000001]]],
        degree=1, knots=[-11.833206742089947, -10.518405992968841],
        multiplicities=[2, 2], weights=[1.0, 1.0],
        periodic=False,
    )
    wire_01 = scad.make_wire_from_edges_rwire(edges=[edge_01_001, edge_01_002, edge_01_003, edge_01_004])
    return scad.make_face_from_wires_rface(outer_wire=wire_01, inner_wires=[], normal=(0, -1, 0) if meridian else (0, 0, 1))


def make_profile_009(*, meridian=False):
    """Build exact local profile curves and its explicit holes."""
    edge_01_001 = scad.make_three_point_arc_redge(
        start=_arc_point([112.3932065077245, -112.3932065077245], 27.0770453, -0.1265350838997743, meridian),
        middle=_arc_point([112.3932065077245, -112.3932065077245], 27.0770453, -0.1816278620335514, meridian),
        end=_arc_point([112.3932065077245, -112.3932065077245], 27.0770453, -0.23672064016732852, meridian),
    )
    edge_01_002 = scad.make_three_point_arc_redge(
        start=_arc_point([112.3932065077245, -112.3932065077245], 27.0770453, -0.23672064016732763, meridian),
        middle=_arc_point([112.3932065077245, -112.3932065077245], 27.0770453, -1.6258891049286723, meridian),
        end=_arc_point([112.3932065077245, -112.3932065077245], 27.0770453, -3.015057569690017, meridian),
    )
    edge_01_003 = scad.make_line_redge(start=_point([85.5326391936127, -115.81026714024121], meridian), end=_point([84.25286759942497, -115.81026714024121], meridian))
    edge_01_004 = scad.make_three_point_arc_redge(
        start=_arc_point([112.3932065077245, -112.3932065077245], 28.347045299999998, -3.020754992269057, meridian),
        middle=_arc_point([112.3932065077245, -112.3932065077245], 28.347045299999998, -1.5707963267948966, meridian),
        end=_arc_point([112.3932065077245, -112.3932065077245], 28.347045299999998, -0.12083766132073626, meridian),
    )
    edge_01_005 = scad.make_line_redge(start=_point([140.53354541602403, -115.81026714024121], meridian), end=_point([139.25377382183632, -115.81026714024121], meridian))
    wire_01 = scad.make_wire_from_edges_rwire(edges=[edge_01_001, edge_01_002, edge_01_003, edge_01_004, edge_01_005])
    return scad.make_face_from_wires_rface(outer_wire=wire_01, inner_wires=[], normal=(0, -1, 0) if meridian else (0, 0, 1))


def make_profile_010(*, meridian=False):
    """Build exact local profile curves and its explicit holes."""
    edge_01_001_half_1 = scad.make_three_point_arc_redge(
        start=_arc_point([112.3932065077245, 112.3932065077245], 35.037340730501846, 0.0, meridian),
        middle=_arc_point([112.3932065077245, 112.3932065077245], 35.037340730501846, 1.5707963267948966, meridian),
        end=_arc_point([112.3932065077245, 112.3932065077245], 35.037340730501846, 3.141592653589793, meridian),
    )
    edge_01_001_half_2 = scad.make_three_point_arc_redge(
        start=_arc_point([112.3932065077245, 112.3932065077245], 35.037340730501846, 3.141592653589793, meridian),
        middle=_arc_point([112.3932065077245, 112.3932065077245], 35.037340730501846, 4.71238898038469, meridian),
        end=_arc_point([112.3932065077245, 112.3932065077245], 35.037340730501846, 6.283185307179586, meridian),
    )
    wire_01 = scad.make_wire_from_edges_rwire(edges=[edge_01_001_half_1, edge_01_001_half_2])
    return scad.make_face_from_wires_rface(outer_wire=wire_01, inner_wires=[], normal=(0, -1, 0) if meridian else (0, 0, 1))


def make_profile_011(*, meridian=False):
    """Build exact local profile curves and its explicit holes."""
    edge_01_001 = scad.make_line_redge(start=_point([0.0, 0.0], meridian), end=_point([-8.881784197001252e-16, 3.048000000000002], meridian))
    edge_01_002 = scad.make_line_redge(start=_point([0.0, 3.048000000000002], meridian), end=_point([-8.89, 3.048000000000002], meridian))
    edge_01_003 = scad.make_line_redge(start=_point([-8.889999999999995, 3.048000000000002], meridian), end=_point([-8.889999999999993, 0.0], meridian))
    edge_01_004 = scad.make_line_redge(start=_point([-8.89, 0.0], meridian), end=_point([0.0, 0.0], meridian))
    wire_01 = scad.make_wire_from_edges_rwire(edges=[edge_01_001, edge_01_002, edge_01_003, edge_01_004])
    return scad.make_face_from_wires_rface(outer_wire=wire_01, inner_wires=[], normal=(0, -1, 0) if meridian else (0, 0, 1))


def make_body_001():
    """Reconstruct one independent output in world coordinates."""
    operand_001 = scad.extrude_rsolid(profile=make_profile_001(), direction=(0, 0, 1), distance=_positive(_positive(3.8099999999999996, 'solid_01_partition_001_solid_01_extrude_local.distance'), "distance"))
    operand_002 = _place(operand_001, _point3([0.0, 0.0, 1.2699999999999996], 'solid_01_partition_001_solid_01_frame.origin'), [1.0, 0.0, -0.0], [0.0, -1.0, 0.0])
    operand_003 = scad.revolve_rsolid(profile=make_profile_002(meridian=True), axis=(0, 0, 1), origin=(0, 0, 0), angle=math.degrees(_full_turn(_finite_number(6.283185307179586, 'solid_01_partition_002_solid_01_revolve_section_01_local.angle'))))
    operand_004 = _place(operand_003, _point3([0.0, 0.0, 0.0], 'solid_01_partition_002_solid_01_section_01_frame.origin'), [1.0, 0.0, 0.0], [-0.0, 1.0, 0.0])
    operand_005 = scad.revolve_rsolid(profile=make_profile_003(meridian=True), axis=(0, 0, 1), origin=(0, 0, 0), angle=math.degrees(_full_turn(_finite_number(6.283185307179586, 'solid_01_partition_003_solid_01_revolve_section_01_local.angle'))))
    operand_006 = _place(operand_005, _point3([0.0, 0.0, 0.0], 'solid_01_partition_003_solid_01_section_01_frame.origin'), [1.0, 0.0, 0.0], [0.0, 1.0, -0.0])
    operand_007 = scad.extrude_rsolid(profile=make_profile_004(), direction=(0, 0, 1), distance=_positive(_positive(1.2699999999999996, 'solid_01_partition_004_solid_01_csg_tool_007_local.distance'), "distance"))
    operand_008 = _place(operand_007, _point3([-112.3932065077245, -112.3932065077245, 6.35], 'solid_01_partition_004_solid_01_csg_tool_007_frame.origin'), [1.0, 0.0, 0.0], [0.0, -1.0, 0.0])
    operand_009 = scad.make_box_rsolid(width=_positive(242.3542488491899, 'solid_01_partition_004_solid_01_csg_tool_010_local.width'), height=_positive(242.3542488491899, 'solid_01_partition_004_solid_01_csg_tool_010_local.depth'), depth=_positive(242.3542488491899, 'solid_01_partition_004_solid_01_csg_tool_010_local.height'), bottom_face_center=(0, 0, 0))
    operand_010 = _place(operand_009, _point3([0.0, 8.091432755729798, 0.7937499891113191], 'solid_01_partition_004_solid_01_csg_tool_010_frame.origin'), [1.0, 0.0, 0.0], [0.0, -0.4999999999999999, 0.8660254037844388])
    operand_011 = scad.cut_rsolid(operand_008, operand_010, skip_non_intersecting=False, tracking_policy="graph")
    operand_012 = scad.revolve_rsolid(profile=make_profile_005(meridian=True), axis=(0, 0, 1), origin=(0, 0, 0), angle=math.degrees(_full_turn(_finite_number(6.283185307179586, 'solid_01_partition_005_solid_01_revolve_section_01_local.angle'))))
    operand_013 = _place(operand_012, _point3([0.0, 0.0, 0.0], 'solid_01_partition_005_solid_01_section_01_frame.origin'), [1.0, 0.0, 0.0], [0.0, 1.0, -0.0])
    operand_014 = scad.make_box_rsolid(width=_positive(56.44055457652086, 'solid_01_partition_006_solid_01_csg_tool_001_local.width'), height=_positive(3.6673079429056687, 'solid_01_partition_006_solid_01_csg_tool_001_local.depth'), depth=_positive(3.811133735392558, 'solid_01_partition_006_solid_01_csg_tool_001_local.height'), bottom_face_center=(0, 0, 0))
    operand_015 = _place(operand_014, _point3([0.0, 4.51691289658038, 6.349433130125785], 'solid_01_partition_006_solid_01_csg_tool_001_frame.origin'), [1, 0, 0], [0, 1, 0])
    operand_016 = scad.make_box_rsolid(width=_positive(226.74620733352575, 'solid_01_partition_006_solid_01_csg_tool_006_local.width'), height=_positive(226.74620733352575, 'solid_01_partition_006_solid_01_csg_tool_006_local.depth'), depth=_positive(226.74620733352575, 'solid_01_partition_006_solid_01_csg_tool_006_local.height'), bottom_face_center=(0, 0, 0))
    operand_017 = _place(operand_016, _point3([0.0, 5.066839027983491, 8.572499997822064], 'solid_01_partition_006_solid_01_csg_tool_006_frame.origin'), [1.0, 0.0, 0.0], [0.0, 0.4999999999999999, -0.8660254037844388])
    operand_018 = scad.cut_rsolid(operand_015, operand_017, skip_non_intersecting=False, tracking_policy="graph")
    operand_019 = scad.make_box_rsolid(width=_positive(226.74620733352575, 'solid_01_partition_006_solid_01_csg_tool_007_local.width'), height=_positive(226.74620733352575, 'solid_01_partition_006_solid_01_csg_tool_007_local.depth'), depth=_positive(226.74620733352575, 'solid_01_partition_006_solid_01_csg_tool_007_local.height'), bottom_face_center=(0, 0, 0))
    operand_020 = _place(operand_019, _point3([0.0, 4.516912896580379, 6.35], 'solid_01_partition_006_solid_01_csg_tool_007_frame.origin'), [1.0, 0.0, 0.0], [0.0, -1.0, 0.0])
    operand_021 = scad.cut_rsolid(operand_018, operand_020, skip_non_intersecting=False, tracking_policy="graph")
    operand_022 = scad.make_box_rsolid(width=_positive(226.74620733352575, 'solid_01_partition_006_solid_01_csg_tool_008_local.width'), height=_positive(226.74620733352575, 'solid_01_partition_006_solid_01_csg_tool_008_local.depth'), depth=_positive(226.74620733352575, 'solid_01_partition_006_solid_01_csg_tool_008_local.height'), bottom_face_center=(0, 0, 0))
    operand_023 = _place(operand_022, _point3([0.0, 4.516912896580379, 10.16], 'solid_01_partition_006_solid_01_csg_tool_008_frame.origin'), [1.0, 0.0, 0.0], [0.0, 1.0, 0.0])
    operand_024 = scad.cut_rsolid(operand_021, operand_023, skip_non_intersecting=False, tracking_policy="graph")
    operand_025 = scad.make_cylinder_rsolid(radius=_positive(28.347045299999998, 'solid_01_partition_006_solid_01_csg_tool_009_local.radius'), height=_positive(3.8111337353925583, 'solid_01_partition_006_solid_01_csg_tool_009_local.height'), bottom_face_center=(0, 0, 0), axis=(0, 0, 1))
    operand_026 = _place(operand_025, _point3([0.0, 0.0, 10.160566865518343], 'solid_01_partition_006_solid_01_csg_tool_009_frame.origin'), [1.0, 0.0, 0.0], [0.0, -1.0, 0.0])
    operand_027 = scad.intersect_rsolid(operand_024, operand_026)
    operand_028 = scad.make_box_rsolid(width=_positive(226.74620733352575, 'solid_01_partition_006_solid_01_csg_tool_010_local.width'), height=_positive(226.74620733352575, 'solid_01_partition_006_solid_01_csg_tool_010_local.depth'), depth=_positive(226.74620733352575, 'solid_01_partition_006_solid_01_csg_tool_010_local.height'), bottom_face_center=(0, 0, 0))
    operand_029 = _place(operand_028, _point3([0.0, 3.9669867651772606, 7.937499997822059], 'solid_01_partition_006_solid_01_csg_tool_010_frame.origin'), [1.0, 0.0, 0.0], [0.0, -0.4999999999999999, 0.8660254037844388])
    operand_030 = scad.cut_rsolid(operand_027, operand_029, skip_non_intersecting=False, tracking_policy="graph")
    operand_031 = scad.extrude_rsolid(profile=make_profile_006(), direction=(0, 0, 1), distance=_positive(_positive(3.8100000000000005, 'solid_01_partition_007_solid_01_csg_tool_002_local.distance'), "distance"))
    operand_032 = _place(operand_031, _point3([-112.3932065077245, -112.3932065077245, 10.16], 'solid_01_partition_007_solid_01_csg_tool_002_frame.origin'), [1.0, 0.0, 0.0], [0.0, -1.0, 0.0])
    operand_033 = scad.make_box_rsolid(width=_positive(244.79604269291417, 'solid_01_partition_007_solid_01_csg_tool_009_local.width'), height=_positive(244.79604269291417, 'solid_01_partition_007_solid_01_csg_tool_009_local.depth'), depth=_positive(244.79604269291417, 'solid_01_partition_007_solid_01_csg_tool_009_local.height'), bottom_face_center=(0, 0, 0))
    operand_034 = _place(operand_033, _point3([0.0, 7.999778388136832, 3.492500010517915], 'solid_01_partition_007_solid_01_csg_tool_009_frame.origin'), [1.0, 0.0, 0.0], [0.0, -0.4999999999999999, 0.8660254037844388])
    operand_035 = scad.cut_rsolid(operand_032, operand_034, skip_non_intersecting=False, tracking_policy="graph")
    operand_036 = scad.revolve_rsolid(profile=make_profile_007(meridian=True), axis=(0, 0, 1), origin=(0, 0, 0), angle=math.degrees(_full_turn(_finite_number(6.283185307179586, 'solid_01_partition_008_solid_01_revolve_section_01_local.angle'))))
    operand_037 = _place(operand_036, _point3([0.0, 0.0, 0.0], 'solid_01_partition_008_solid_01_section_01_frame.origin'), [1.0, 0.0, 0.0], [0.0, 1.0, -0.0])
    operand_038 = scad.revolve_rsolid(profile=make_profile_008(meridian=True), axis=(0, 0, 1), origin=(0, 0, 0), angle=math.degrees(_full_turn(_finite_number(6.283185307179586, 'solid_01_partition_009_solid_01_revolve_section_01_local.angle'))))
    operand_039 = _place(operand_038, _point3([0.0, 0.0, 0.0], 'solid_01_partition_009_solid_01_section_01_frame.origin'), [1.0, 0.0, 0.0], [0.0, 1.0, -0.0])
    operand_040 = scad.make_box_rsolid(width=_positive(56.695397997001606, 'solid_01_partition_010_solid_01_csg_tool_001_local.width'), height=_positive(32.49864817138915, 'solid_01_partition_010_solid_01_csg_tool_001_local.depth'), depth=_positive(1.27130739700162, 'solid_01_partition_010_solid_01_csg_tool_001_local.height'), bottom_face_center=(0, 0, 0))
    operand_041 = _place(operand_040, _point3([0.0, -12.098374912806229, 10.159346301499184], 'solid_01_partition_010_solid_01_csg_tool_001_frame.origin'), [1, 0, 0], [0, 1, 0])
    operand_042 = scad.make_cylinder_rsolid(radius=_positive(28.347045299999998, 'solid_01_partition_010_solid_01_csg_tool_010_local.radius'), height=_positive(1.2713073970016198, 'solid_01_partition_010_solid_01_csg_tool_010_local.height'), bottom_face_center=(0, 0, 0), axis=(0, 0, 1))
    operand_043 = _place(operand_042, _point3([0.0, 0.0, 11.430653698500805], 'solid_01_partition_010_solid_01_csg_tool_010_frame.origin'), [1.0, 0.0, 0.0], [0.0, -1.0, 0.0])
    operand_044 = scad.intersect_rsolid(operand_041, operand_043)
    operand_045 = scad.make_box_rsolid(width=_positive(261.4394003222086, 'solid_01_partition_010_solid_01_csg_tool_011_local.width'), height=_positive(261.4394003222086, 'solid_01_partition_010_solid_01_csg_tool_011_local.depth'), depth=_positive(261.4394003222086, 'solid_01_partition_010_solid_01_csg_tool_011_local.height'), bottom_face_center=(0, 0, 0))
    operand_046 = _place(operand_045, _point3([0.0, -12.098374912806236, 11.43], 'solid_01_partition_010_solid_01_csg_tool_011_frame.origin'), [1.0, 0.0, 0.0], [0.0, 1.0, 0.0])
    operand_047 = scad.cut_rsolid(operand_044, operand_046, skip_non_intersecting=False, tracking_policy="graph")
    operand_048 = scad.make_box_rsolid(width=_positive(261.4394003222086, 'solid_01_partition_010_solid_01_csg_tool_012_local.width'), height=_positive(261.4394003222086, 'solid_01_partition_010_solid_01_csg_tool_012_local.depth'), depth=_positive(261.4394003222086, 'solid_01_partition_010_solid_01_csg_tool_012_local.height'), bottom_face_center=(0, 0, 0))
    operand_049 = _place(operand_048, _point3([0.0, -0.18683518811246813, 17.67213066651486], 'solid_01_partition_010_solid_01_csg_tool_012_frame.origin'), [1.0, 0.0, 0.0], [0.0, 0.4999999999999999, -0.8660254037844388])
    operand_050 = scad.cut_rsolid(operand_047, operand_049, skip_non_intersecting=False, tracking_policy="graph")
    operand_051 = scad.make_box_rsolid(width=_positive(261.4394003222086, 'solid_01_partition_010_solid_01_csg_tool_013_local.width'), height=_positive(261.4394003222086, 'solid_01_partition_010_solid_01_csg_tool_013_local.depth'), depth=_positive(261.4394003222086, 'solid_01_partition_010_solid_01_csg_tool_013_local.height'), bottom_face_center=(0, 0, 0))
    operand_052 = _place(operand_051, _point3([0.0, -12.098374912806236, 10.16], 'solid_01_partition_010_solid_01_csg_tool_013_frame.origin'), [1.0, 0.0, 0.0], [0.0, -1.0, 0.0])
    operand_053 = scad.cut_rsolid(operand_050, operand_052, skip_non_intersecting=False, tracking_policy="graph")
    operand_054 = scad.extrude_rsolid(profile=make_profile_009(), direction=(0, 0, 1), distance=_positive(_positive(1.2699999999999996, 'solid_01_partition_011_solid_01_csg_tool_002_local.distance'), "distance"))
    operand_055 = _place(operand_054, _point3([-112.3932065077245, -112.3932065077245, 11.43], 'solid_01_partition_011_solid_01_csg_tool_002_frame.origin'), [1.0, 0.0, 0.0], [0.0, -1.0, 0.0])
    operand_056 = scad.make_box_rsolid(width=_positive(246.2724259495469, 'solid_01_partition_011_solid_01_csg_tool_008_local.width'), height=_positive(246.2724259495469, 'solid_01_partition_011_solid_01_csg_tool_008_local.depth'), depth=_positive(246.2724259495469, 'solid_01_partition_011_solid_01_csg_tool_008_local.height'), bottom_face_center=(0, 0, 0))
    operand_057 = _place(operand_056, _point3([0.0, 6.8082717693256, 5.556250011853535], 'solid_01_partition_011_solid_01_csg_tool_008_frame.origin'), [1.0, 0.0, 0.0], [0.0, -0.4999999999999999, 0.8660254037844388])
    operand_058 = scad.cut_rsolid(operand_055, operand_057, skip_non_intersecting=False, tracking_policy="graph")
    operand_059 = scad.extrude_rsolid(profile=make_profile_010(), direction=(0, 0, 1), distance=_positive(_positive(1.2699999999999996, 'solid_01_partition_012_solid_01_csg_tool_007_local.distance'), "distance"))
    operand_060 = _place(operand_059, _point3([-112.3932065077245, -112.3932065077245, 11.43], 'solid_01_partition_012_solid_01_csg_tool_007_frame.origin'), [1.0, 0.0, 0.0], [0.0, 1.0, 0.0])
    operand_061 = scad.make_cylinder_rsolid(radius=_positive(27.0770453, 'solid_01_partition_012_solid_01_csg_tool_024_local.radius'), height=_positive(1.2719823740509124, 'solid_01_partition_012_solid_01_csg_tool_024_local.height'), bottom_face_center=(0, 0, 0), axis=(0, 0, 1))
    operand_062 = _place(operand_061, _point3([0.0, 0.0, 12.700991187025455], 'solid_01_partition_012_solid_01_csg_tool_024_frame.origin'), [1.0, 0.0, 0.0], [0.0, -1.0, 0.0])
    operand_063 = scad.cut_rsolid(operand_060, operand_062, skip_non_intersecting=False, tracking_policy="graph")
    operand_064 = _cone(_nonnegative(34.696779668350146, 'solid_01_partition_012_solid_01_csg_tool_026_local.radius_bottom'), _nonnegative(35.03760631826487, 'solid_01_partition_012_solid_01_csg_tool_026_local.radius_top'), _positive(1.2719823740509106, 'solid_01_partition_012_solid_01_csg_tool_026_local.height'))
    operand_065 = _place(operand_064, _point3([0.0, 0.0, 12.700991187025455], 'solid_01_partition_012_solid_01_csg_tool_026_frame.origin'), [1.0, 0.0, 0.0], [0.0, -1.0, 0.0])
    operand_066 = scad.intersect_rsolid(operand_063, operand_065)
    operand_067 = scad.extrude_rsolid(profile=make_profile_011(), direction=(0, 0, 1), distance=_positive(_positive(1.2699999999999996, 'solid_01_partition_012_solid_01_residual_cut_01_solid_01_extrude_local.distance'), "distance"))
    operand_068 = _place(operand_067, _point3([4.445, 32.4110452561248, 12.7], 'solid_01_partition_012_solid_01_residual_cut_01_solid_01_frame.origin'), [1.0, 0.0, -0.0], [0.0, -1.0, 0.0])
    operand_069 = scad.cut_rsolid(operand_066, operand_068, skip_non_intersecting=False, tracking_policy="graph")
    operand_070 = scad.union_rsolid([operand_002, operand_004, operand_006, operand_011, operand_013, operand_030, operand_035, operand_037, operand_039, operand_053, operand_058, operand_069], tracking_policy="graph")
    scale = _positive(_positive(P001_BODY_001_FACTOR, 'body_001_scale.factor'), "scale")
    operand_071 = scad.scale_rsolid(operand_070, factors=(scale, scale, scale), center=_point3([0.0, 0.0, 5.079999999999999], 'body_001_scale.center'))
    operand_072 = _place(operand_071, _point3(P002_BODY_001_ORIGIN, 'body_001_placement.origin'), P003_BODY_001_X_AXIS, P004_BODY_001_Y_AXIS)
    return operand_072


def build():
    """Build fresh independent bodies, then wrap them at identity placement."""
    global BODIES
    BODIES = [
        make_body_001(),
    ]
    return _as_model(BODIES)


def _finite_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(name + " must be finite")
    return value


def _nonnegative(value, name):
    _finite_number(value, name)
    if value < 0:
        raise ValueError(name + " must be nonnegative")
    return value


def _point3(value, name):
    if len(value) != 3:
        raise ValueError(name + " must have three components")
    return tuple(_finite_number(c, name) for c in value)


def _full_turn(angle):
    if abs(angle-2*math.pi) > 1e-12:
        raise ValueError("only a full revolution is supported")
    return angle


def _cone(bottom, top, height):
    if bottom+top == 0:
        raise ValueError("cone radii cannot both be zero")
    if abs(bottom-top) <= 1e-14:
        return scad.make_cylinder_rsolid(radius=bottom, height=height)
    return scad.make_cone_rsolid(bottom_radius=bottom, top_radius=top, height=height)


def _torus_sizes(major, minor):
    if major <= minor:
        raise ValueError("major torus radius must exceed minor radius")
    return major, minor


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(name + " must be positive and finite")
    return value


def _frame(origin, x_axis, y_axis):
    if any(len(v) != 3 for v in (origin, x_axis, y_axis)):
        raise ValueError("frame vectors must have three components")
    if any(isinstance(c, bool) or not isinstance(c, (int, float)) or not math.isfinite(c)
           for v in (origin, x_axis, y_axis) for c in v):
        raise ValueError("frame vectors must be finite")
    if any(abs(sum(c*c for c in v)-1) > 1e-10 for v in (x_axis, y_axis)):
        raise ValueError("frame axes must be unit vectors")
    if abs(sum(x*y for x,y in zip(x_axis, y_axis))) > 1e-10:
        raise ValueError("frame axes must be perpendicular")
    z_axis = (x_axis[1]*y_axis[2]-x_axis[2]*y_axis[1],
              x_axis[2]*y_axis[0]-x_axis[0]*y_axis[2],
              x_axis[0]*y_axis[1]-x_axis[1]*y_axis[0])
    return [[x_axis[i], y_axis[i], z_axis[i], origin[i]] for i in range(3)] + [[0, 0, 0, 1]]


def _place(body, origin, x_axis, y_axis):
    """Lower a rigid frame through analytic-preserving rotation and translation."""
    m = _frame(origin, x_axis, y_axis)
    # Stable matrix-to-unit-quaternion conversion, including half-turns.
    trace = m[0][0]+m[1][1]+m[2][2]
    if trace > 0:
        t = 2*math.sqrt(trace+1)
        w, x, y, z = t/4, (m[2][1]-m[1][2])/t, (m[0][2]-m[2][0])/t, (m[1][0]-m[0][1])/t
    else:
        i = max(range(3), key=lambda j: m[j][j])
        j, k = (i+1)%3, (i+2)%3
        t = 2*math.sqrt(max(0, 1+m[i][i]-m[j][j]-m[k][k]))
        q = [0., 0., 0.]
        q[i], q[j], q[k] = t/4, (m[j][i]+m[i][j])/t, (m[k][i]+m[i][k])/t
        w, x, y, z = (m[k][j]-m[j][k])/t, *q
    length = math.sqrt(x*x+y*y+z*z)
    if length > 1e-15:
        angle = math.degrees(2*math.atan2(length, w))
        body = scad.rotate_shape(body, angle=angle, axis=(x/length, y/length, z/length), origin=(0, 0, 0))
    return scad.translate_shape(body, vector=tuple(origin))


def _point(point, meridian):
    x, y = point
    return (x, 0, y) if meridian else (x, y, 0)


def _arc_point(center, radius, angle, meridian):
    return _point((center[0]+radius*math.cos(angle), center[1]+radius*math.sin(angle)), meridian)


def _as_model(bodies):
    if len(bodies) == 1:
        return bodies[0]
    assembly = scad.make_assembly_rassembly("step-plan-model")
    for index, body in enumerate(bodies, 1):
        part = scad.make_part_rpart(f"body-{index:03d}", body)
        assembly = scad.add_component_rassembly(assembly, part, f"component-{index:03d}",
                                               scad.identity_placement_rplacement())
    return assembly

