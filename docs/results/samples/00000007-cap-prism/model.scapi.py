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
        start=_arc_point([0.0, 0.0], 91.0485145, -6.283185307179586, meridian),
        middle=_arc_point([0.0, 0.0], 91.0485145, -4.71238898038469, meridian),
        end=_arc_point([0.0, 0.0], 91.0485145, -3.141592653589793, meridian),
    )
    edge_01_001_half_2 = scad.make_three_point_arc_redge(
        start=_arc_point([0.0, 0.0], 91.0485145, -3.141592653589793, meridian),
        middle=_arc_point([0.0, 0.0], 91.0485145, -1.5707963267948966, meridian),
        end=_arc_point([0.0, 0.0], 91.0485145, 0.0, meridian),
    )
    wire_01 = scad.make_wire_from_edges_rwire(edges=[edge_01_001_half_1, edge_01_001_half_2])
    return scad.make_face_from_wires_rface(outer_wire=wire_01, inner_wires=[], normal=(0, -1, 0) if meridian else (0, 0, 1))


def make_body_001():
    """Reconstruct one independent output in world coordinates."""
    operand_001 = scad.extrude_rsolid(profile=make_profile_001(), direction=(0, 0, 1), distance=_positive(_positive(25.4, 'solid_01_extrude_local.distance'), "distance"))
    operand_002 = _place(operand_001, _point3([0.0, 0.0, 0.0], 'solid_01_frame.origin'), [1.0, 0.0, -0.0], [-0.0, 1.0, 0.0])
    scale = _positive(_positive(P001_BODY_001_FACTOR, 'body_001_scale.factor'), "scale")
    operand_003 = scad.scale_rsolid(operand_002, factors=(scale, scale, scale), center=_point3([0.0, 0.0, 12.700000000000001], 'body_001_scale.center'))
    operand_004 = _place(operand_003, _point3(P002_BODY_001_ORIGIN, 'body_001_placement.origin'), P003_BODY_001_X_AXIS, P004_BODY_001_Y_AXIS)
    return operand_004


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

