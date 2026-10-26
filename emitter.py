"""Emit explicit SCAPI calls directly while traversing a verified native STEP cap."""
import ast
import math
import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GeomAbs import GeomAbs_Line, GeomAbs_Circle
from OCP.TopAbs import TopAbs_REVERSED
from OCP.TopoDS import TopoDS
from step2code_direct.geometry import face_wires, wire_edges

CODE_STRUCTURE_SPEC = 'step-direct-readable-v2'

HELPERS = '''def _point(local, origin, x_axis, y_axis):
    """Place a local profile point in the supplied world frame."""
    u, v = local
    return tuple(origin[i] + u*x_axis[i] + v*y_axis[i] for i in range(3))


def _validate_frame(origin, x_axis, y_axis):
    if any(len(v) != 3 for v in (origin, x_axis, y_axis)):
        raise ValueError('placement vectors must have three components')
    if any(not (-float('inf') < value < float('inf'))
           for vector in (origin, x_axis, y_axis) for value in vector):
        raise ValueError('placement must be finite')
    if any(abs(sum(v*v for v in axis)-1) > 1e-10 for axis in (x_axis, y_axis)):
        raise ValueError('axes must have unit length')
    if abs(sum(a*b for a, b in zip(x_axis, y_axis))) > 1e-10:
        raise ValueError('axes must be perpendicular')
    return (x_axis[1]*y_axis[2]-x_axis[2]*y_axis[1],
            x_axis[2]*y_axis[0]-x_axis[0]*y_axis[2],
            x_axis[0]*y_axis[1]-x_axis[1]*y_axis[0])


def _as_model(bodies):
    if not bodies:
        raise ValueError('build produced no solids')
    if len(bodies) == 1:
        return bodies[0]
    assembly = scad.make_assembly_rassembly('step-direct-reconstruction')
    for index, body in enumerate(bodies, 1):
        part = scad.make_part_rpart(f'body-{index:02d}', body)
        assembly = scad.add_component_rassembly(
            assembly, part, f'component-{index:02d}', scad.identity_placement_rplacement())
    return assembly
'''


def literal(value):
    if isinstance(value, (list, tuple)):
        return '(' + ', '.join(literal(v) for v in value) + (',' if len(value) == 1 else '') + ')'
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('nonfinite source coordinate')
    return repr(value)


def positive_winding_edges(wire, native):
    """SCAPI reverses each hole internally: supply all loops CCW.

    Integrate line/circle signed area analytically; never polygonize an arc.
    Only edge orientation and traversal order change, not source coordinates.
    """
    edges = wire_edges(wire, native.face)
    direction = np.cross(native.x_axis, native.y_axis)
    signed_area = 0.0
    for edge in edges:
        curve = BRepAdaptor_Curve(edge)
        first, last = curve.FirstParameter(), curve.LastParameter()
        if edge.Orientation() == TopAbs_REVERSED:
            first, last = last, first
        def local(point):
            delta = np.array(point.Coord())-native.origin
            return np.array([delta @ native.x_axis, delta @ native.y_axis])
        start, end = local(curve.Value(first)), local(curve.Value(last))
        if curve.GetType() == GeomAbs_Line:
            signed_area += 0.5*(start[0]*end[1]-start[1]*end[0])
        else:
            center = local(curve.Circle().Location())
            sweep = (last-first)*float(np.array(curve.Circle().Axis().Direction().Coord()) @ direction)
            signed_area += 0.5*(center[0]*(end[1]-start[1])-center[1]*(end[0]-start[0])
                                +curve.Circle().Radius()**2*sweep)
    if not math.isfinite(signed_area) or signed_area == 0:
        raise ValueError('Cannot determine profile loop winding')
    if signed_area < 0:
        return [TopoDS.Edge_s(edge.Reversed()) for edge in reversed(edges)]
    return edges


class DirectEmitter:
    """Collect emitted source, never an executable intermediate CAD document."""
    def __init__(self):
        self.constants, self.functions, self.calls = [], [], []
        self.symbols = {'code_structure_spec': CODE_STRUCTURE_SPEC, 'history_recovered': False,
                        'route': 'native_step_geometry_to_scapi', 'cad_ir_used': False,
                        'bodies': [], 'parameters': {}}

    def add_prism(self, native, index):
        prefix = f'FEATURE_{index:02d}'
        body = f'BODY_{index:02d}'
        constants = [f'# Source solid {index}: independent dimensions and world placement.',
                     f'{prefix}_DISTANCE_MM = {literal(native.distance)}',
                     f'{body}_ORIGIN_MM = {literal(native.origin)}',
                     f'{body}_X_AXIS = {literal(native.x_axis)}',
                     f'{body}_Y_AXIS = {literal(native.y_axis)}']
        parameters = self.symbols['parameters']
        parameters[f'{prefix}_DISTANCE_MM'] = dict(kind='length', units='mm',
            source_solid_index=index, origin_kind=native.evidence['distance_origin_kind'],
            method=native.evidence['distance_method'], edit_policy='independent_positive_length')
        for suffix in ('ORIGIN_MM', 'X_AXIS', 'Y_AXIS'):
            parameters[f'{body}_{suffix}'] = dict(kind='placement', source_solid_index=index,
                                                  edit_policy='independent_rigid_frame')
        lines = ['    direction = _validate_frame(origin, x_axis, y_axis)']
        radius_arguments, radius_bindings = [], []
        loop_records = []
        for loop_index, wire in enumerate(face_wires(native.face), 1):
            names, edge_records = [], []
            for edge_index, edge in enumerate(positive_winding_edges(wire, native), 1):
                curve = BRepAdaptor_Curve(edge)
                first, last = curve.FirstParameter(), curve.LastParameter()
                if edge.Orientation() == TopAbs_REVERSED:
                    first, last = last, first
                name = f'loop_{loop_index:02d}_edge_{edge_index:03d}'
                names.append(name)

                def local(point):
                    delta = np.array(point.Coord())-native.origin
                    return [float(delta @ native.x_axis), float(delta @ native.y_axis)]

                def point(parameter):
                    return f'_point({literal(local(curve.Value(parameter)))}, origin, x_axis, y_axis)'

                complete_circle = (curve.GetType() == GeomAbs_Circle
                                   and abs(abs(last-first)-2*math.pi) <= 1e-7)
                if curve.GetType() == GeomAbs_Line:
                    expression = f'scad.make_line_redge(start={point(first)}, end={point(last)})'
                    kind = 'line'
                elif complete_circle:
                    number = len(radius_arguments)+1
                    argument = f'circle_radius_{number:02d}'
                    constant = f'{prefix}_CIRCLE_{number:02d}_RADIUS_MM'
                    radius_arguments.append(argument)
                    radius_bindings.append(f'{argument}={constant}')
                    constants.append(f'{constant} = {literal(curve.Circle().Radius())}')
                    parameters[constant] = dict(kind='radius', units='mm', source_solid_index=index,
                        loop_index=loop_index, edge_index=edge_index, origin_kind='measured',
                        edit_policy='independent_positive_radius')
                    center = f'_point({literal(local(curve.Circle().Location()))}, origin, x_axis, y_axis)'
                    expression = f'scad.make_circle_redge(center={center}, radius={argument}, normal=direction)'
                    kind = 'circle'
                else:
                    expression = (f'scad.make_three_point_arc_redge(start={point(first)}, '
                                  f'middle={point((first+last)/2)}, end={point(last)})')
                    kind = 'three_point_arc'
                lines.append(f'    {name} = {expression}')
                edge_records.append(dict(edge_index=edge_index, kind=kind, local_coordinates='explicit_in_profile_function'))
            lines.append(f'    loop_{loop_index:02d} = scad.make_wire_from_edges_rwire([{", ".join(names)}])')
            loop_records.append(dict(loop_index=loop_index, is_outer=loop_index == 1, edges=edge_records,
                                     emitted_winding='counterclockwise; SCAPI reverses inner wires'))
        holes = ', '.join(f'loop_{i:02d}' for i in range(2, len(loop_records)+1))
        lines.append(f'    return scad.make_face_from_wires_rface(outer_wire=loop_01, inner_wires=[{holes}], normal=direction)')
        extra = ''.join(f', {a}' for a in radius_arguments)
        signature = f'origin, x_axis, y_axis{extra}'
        profile = [f'def make_profile_{index:02d}(*, {signature}):',
                   '    """Reconstruct the measured outer wire and holes; no source file required."""', *lines]
        forwarded = ''.join(f', {a}={a}' for a in radius_arguments)
        feature = [f'def apply_feature_{index:02d}(bodies, *, origin, x_axis, y_axis, distance{extra}):',
                   '    """Append one fresh straight extrusion for this source solid."""',
                   '    direction = _validate_frame(origin, x_axis, y_axis)',
                   "    if not (0 < distance < float('inf')):",
                   "        raise ValueError('distance must be positive and finite')"]
        for argument in radius_arguments:
            feature.extend([f"    if not (0 < {argument} < float('inf')):",
                            "        raise ValueError('circle radius must be positive and finite')"])
        feature.extend([f'    profile = make_profile_{index:02d}(origin=origin, x_axis=x_axis, y_axis=y_axis{forwarded})',
                        '    bodies.append(scad.extrude_rsolid(profile=profile, direction=direction, distance=distance))',
                        '    return bodies'])
        self.constants.extend(constants + [''])
        self.functions.extend(['\n'.join(profile), '\n'.join(feature)])
        bindings = ''.join(f', {binding}' for binding in radius_bindings)
        self.calls.append(f'    apply_feature_{index:02d}(bodies, origin={body}_ORIGIN_MM, x_axis={body}_X_AXIS, '
                          f'y_axis={body}_Y_AXIS, distance={prefix}_DISTANCE_MM{bindings})')
        self.symbols['bodies'].append(dict(source_solid_index=index, profile_function=f'make_profile_{index:02d}',
            feature_function=f'apply_feature_{index:02d}', loops=loop_records,
            local_profile_editing='fixed literals except complete-circle radii; semantic width/height not inferred'))

    def emit(self):
        if not self.calls:
            raise ValueError('No verified source solids')
        build = ['def build():', '    """Read current parameters and rebuild all bodies in world coordinates."""',
                 '    bodies = []', *self.calls, '    global BODIES', '    BODIES = list(bodies)',
                 '    return _as_model(bodies)']
        code = '\n\n\n'.join([
            '\n'.join(['"""Direct STEP reconstruction; inferred geometry, no recovered source history."""',
                        '', 'import simplecadapi as scad', '', *self.constants, 'BODIES = None']),
            *self.functions, '\n'.join(build), HELPERS]).rstrip()+'\n'
        tree = ast.parse(code)
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
        self.symbols['structure'] = dict(lines=len(code.splitlines()), function_count=len(functions),
            build_lines=next(n.end_lineno-n.lineno+1 for n in functions if n.name == 'build'),
            max_function_lines=max(n.end_lineno-n.lineno+1 for n in functions),
            feature_count=len(self.calls), profile_count=len(self.calls), independent_parameter_count=len(self.symbols['parameters']))
        return code
