"""Unroll selected native CSG operands into ordinary readable SCAPI functions."""
import ast
import math
from step2code_direct.emitter import DirectEmitter, literal
from step2code_direct.hybrid import Atom

HYBRID_HELPERS = '''def _place_body(body, origin, x_axis, y_axis):
    """Use rigid rotations/translations; preserve analytic surfaces exactly."""
    z_axis = _validate_frame(origin, x_axis, y_axis)
    # Two Rodrigues rotations align the X axis, then the Y axis.
    # cross((1,0,0), x_axis) = (0,-x_axis[2],x_axis[1]).
    cross = (0.0, -x_axis[2], x_axis[1])
    sine = sum(v*v for v in cross)**0.5
    cosine = max(-1.0, min(1.0, x_axis[0]))
    rotated_y = (0.0, 1.0, 0.0)
    if sine > 1e-12:
        unit = tuple(v/sine for v in cross)
        angle = _angle_degrees(cosine, sine)
        body = scad.rotate_shape(body, angle=angle, axis=unit)
        dot = unit[1]
        cross_y = (-unit[2], 0.0, unit[0])
        rotated_y = tuple(rotated_y[i]*cosine + cross_y[i]*sine + unit[i]*dot*(1-cosine) for i in range(3))
    elif cosine < 0:
        body = scad.rotate_shape(body, angle=180.0, axis=(0.0,0.0,1.0))
        rotated_y = (0.0,-1.0,0.0)
    cross_y = (rotated_y[1]*y_axis[2]-rotated_y[2]*y_axis[1],
               rotated_y[2]*y_axis[0]-rotated_y[0]*y_axis[2],
               rotated_y[0]*y_axis[1]-rotated_y[1]*y_axis[0])
    sine = sum(cross_y[i]*x_axis[i] for i in range(3))
    cosine = max(-1.0,min(1.0,sum(a*b for a,b in zip(rotated_y,y_axis))))
    angle = _angle_degrees(cosine, abs(sine))
    if abs(sine) > 1e-12 or cosine < 0:
        body = scad.rotate_shape(body, angle=angle if sine >= 0 else -angle, axis=x_axis)
    return scad.translate_shape(body, vector=origin)


def _angle_degrees(cosine, sine):
    """Evaluate an unsigned atan2 angle using half-angle reduction."""
    if cosine <= -1.0 + 1e-15:
        return 180.0
    if cosine >= 1.0 - 1e-15:
        return 0.0
    # Reduce tan(theta/2) until atan series converges rapidly.
    tangent = sine/(1.0+cosine)
    factor = 2.0
    while tangent > 0.2:
        tangent = tangent/(1.0+(1.0+tangent*tangent)**0.5)
        factor *= 2.0
    term = tangent
    value = term
    for n in range(1,20):
        term *= -tangent*tangent
        value += term/(2*n+1)
    return factor*value*57.29577951308232


def _validate_axis(axis):
    if (len(axis) != 3 or any(not (-float('inf') < v < float('inf')) for v in axis)
            or abs(sum(v*v for v in axis)-1) > 1e-10):
        raise ValueError('primitive axis must be finite and unit length')
'''


class _Names(ast.NodeTransformer):
    def __init__(self,mapping):
        self.mapping=mapping

    def visit_Name(self,node):
        node.id=self.mapping.get(node.id,node.id)
        return node

    def visit_FunctionDef(self,node):
        self.generic_visit(node)
        node.name=self.mapping.get(node.name,node.name)
        return node


def _prism_operand(emitter,atom,body_index,operand_index):
    """Reuse only the direct native-cap emitter; no IR preparation is involved."""
    temporary=DirectEmitter()
    temporary.add_prism(atom.prism,1)
    prefix=f'BODY_{body_index:02d}_OPERAND_{operand_index:02d}_'
    factory=f'make_operand_{body_index:02d}_{operand_index:02d}'
    mapping={name:prefix+name.removeprefix('FEATURE_01_').removeprefix('BODY_01_')
             for name in temporary.symbols['parameters']}
    mapping.update(make_profile_01=f'make_profile_{body_index:02d}_{operand_index:02d}',apply_feature_01=factory)
    renamer=_Names(mapping)
    constant_tree=renamer.visit(ast.parse('\n'.join(temporary.constants)))
    emitter.constants.append(ast.unparse(ast.fix_missing_locations(constant_tree)))
    for name,value in temporary.symbols['parameters'].items():
        emitter.symbols['parameters'][mapping[name]]=dict(value,source_solid_index=body_index,operand_index=operand_index)
    for text in temporary.functions:
        node=ast.parse(text).body[0]
        if node.name=='apply_feature_01':
            node.args.args=[]
            updated=[]
            for statement in node.body:
                if isinstance(statement,ast.Return) and isinstance(statement.value,ast.Name) and statement.value.id=='bodies':
                    continue
                if (isinstance(statement,ast.Expr) and isinstance(statement.value,ast.Call)
                    and isinstance(statement.value.func,ast.Attribute) and statement.value.func.attr=='append'):
                    statement=ast.Return(statement.value.args[0])
                updated.append(statement)
            node.body=updated
            if isinstance(node.body[0],ast.Expr) and isinstance(node.body[0].value,ast.Constant):
                node.body[0].value=ast.Constant('Build a fresh extrusion operand for the explicit CSG expression.')
        emitter.functions.append(ast.unparse(ast.fix_missing_locations(renamer.visit(node))))
    call=renamer.visit(ast.parse(temporary.calls[0].strip()).body[0].value)
    call.args=[]
    bindings=[]
    for keyword in call.keywords:
        parameter=f'operand_{operand_index:02d}_{keyword.arg}'
        bindings.append((parameter,keyword.value.id))
        keyword.value=ast.Name(id=parameter,ctx=ast.Load())
    return ast.unparse(ast.fix_missing_locations(call)),bindings


def _primitive_operand(emitter,atom,body_index,operand_index):
    prefix=f'BODY_{body_index:02d}_OPERAND_{operand_index:02d}_'
    factory=f'make_operand_{body_index:02d}_{operand_index:02d}'
    params=dict(atom.parameters)
    params.pop('parameter_origin', None)
    if atom.kind == 'revolution' and params.get('weights') is None:
        params['weights'] = []
    if atom.kind in ('torus', 'revolution'):
        params['angle_deg']=math.degrees(params.pop('angle_radians'))
    bindings=[]
    for key,value in params.items():
        suffix='_DEG' if key=='angle_deg' else '' if key in ('axis','radial_axis','x_axis','y_axis') else '_MM'
        constant=prefix+key.upper()+suffix
        emitter.constants.append(f'{constant} = {literal(value)}')
        emitter.symbols['parameters'][constant]=dict(source_solid_index=body_index,operand_index=operand_index,
            kind=key,origin_kind=atom.evidence['parameter_origin'],edit_policy='independent_operand_parameter')
        bindings.append((f'operand_{operand_index:02d}_{key}',constant))
    lines=[f'def {factory}(*, {", ".join(params)}):',f'    """Create measured {atom.kind} operand {operand_index}."""']
    if atom.kind in ('cylinder','cone','torus','revolution'):
        lines.append('    _validate_axis(axis)')
    if atom.kind=='box':
        if 'origin' in params:
            lines.extend([
                '    _validate_axis(axis)',
                '    _validate_axis(x_axis)',
                '    _validate_axis(y_axis)',
                '    if abs(sum(a*b for a,b in zip(axis, x_axis))) > 1e-10 or abs(sum(a*b for a,b in zip(axis, y_axis))) > 1e-10:',
                "        raise ValueError('oriented box axes must be perpendicular')",
                '    box = scad.make_box_rsolid(width=width, height=height, depth=depth, bottom_face_center=(width / 2.0, height / 2.0, 0.0))',
                '    return _place_body(box, origin, x_axis, y_axis)'])
        else:
            lines.append('    return scad.make_box_rsolid(width=width, height=height, depth=depth, bottom_face_center=bottom_face_center)')
    elif atom.kind=='cylinder':
        lines.append('    return scad.make_cylinder_rsolid(radius=radius, height=height, bottom_face_center=origin, axis=axis)')
    elif atom.kind=='cone':
        lines.append('    return scad.make_cone_rsolid(bottom_radius=bottom_radius, top_radius=top_radius, height=height, bottom_face_center=origin, axis=axis)')
    elif atom.kind=='sphere':
        lines.append('    return scad.make_sphere_rsolid(radius=radius, center=center)')
    elif atom.kind=='torus':
        lines.extend([
            '    _validate_axis(radial_axis)',
            '    if abs(sum(a*b for a,b in zip(axis,radial_axis))) > 1e-10:',
            "        raise ValueError('torus radial axis must be perpendicular to its axis')",
            "    if not (0 < minor_radius < major_radius < float('inf') and 0 < angle_deg <= 360):",
            "        raise ValueError('torus requires 0 < minor radius < major radius and 0 < angle <= 360')",
            '    center = tuple(origin[i] + major_radius*radial_axis[i] for i in range(3))',
            '    tangent = (axis[1]*radial_axis[2]-axis[2]*radial_axis[1],',
            '               axis[2]*radial_axis[0]-axis[0]*radial_axis[2],',
            '               axis[0]*radial_axis[1]-axis[1]*radial_axis[0])',
            '    profile = scad.make_circle_rface(center=center, radius=minor_radius, normal=tangent)',
            '    return scad.revolve_rsolid(profile=profile, axis=axis, angle=angle_deg, origin=origin)'])
    elif atom.kind=='revolution':
        lines.extend([
            '    if not (0 < angle_deg <= 360):',
            "        raise ValueError('revolution angle must be in (0, 360] degrees')",
            '    profile_edge = scad.make_spline_redge(control_points=control_points, degree=degree,',
            '        knots=knots, multiplicities=multiplicities, weights=(weights if weights else None))',
            '    profile_edges = [profile_edge,',
            '        scad.make_line_redge(start=end_point, end=end_axis_point),',
            '        scad.make_line_redge(start=end_axis_point, end=start_axis_point),',
            '        scad.make_line_redge(start=start_axis_point, end=start_point)]',
            '    profile = scad.make_wire_from_edges_rwire(profile_edges)',
            '    return scad.revolve_rsolid(profile=profile, axis=axis, angle=angle_deg, origin=origin)'])
    else:
        raise ValueError(f'Unsupported operand {atom.kind}')
    emitter.functions.append('\n'.join(lines))
    call=f'{factory}('+', '.join(f'{key}=operand_{operand_index:02d}_{key}' for key in params)+')'
    return call,bindings


def add_hybrid(emitter,result,body_index):
    if not getattr(emitter,'uses_hybrid',False):
        emitter.functions.append(HYBRID_HELPERS)
        emitter.uses_hybrid=True
    body=f'BODY_{body_index:02d}'
    emitter.constants.extend([f'# Source body {body_index}: rigid delta; operand locations below are measured world coordinates.',
        f'{body}_ORIGIN_MM = (0.0, 0.0, 0.0)',f'{body}_X_AXIS = (1.0, 0.0, 0.0)',f'{body}_Y_AXIS = (0.0, 1.0, 0.0)'])
    for suffix in ('ORIGIN_MM','X_AXIS','Y_AXIS'):
        emitter.symbols['parameters'][f'{body}_{suffix}']=dict(source_solid_index=body_index,kind='placement',
            edit_policy='rigid_world_delta_about_world_origin')
    atoms=[]
    for atom in [result.base]+[atom for _,atom in result.steps]:
        if not any(atom is other for other in atoms):
            atoms.append(atom)
    calls={}
    bindings=[]
    operand_records=[]
    for operand_index,atom in enumerate(atoms,1):
        call,local_bindings=(_prism_operand if atom.kind=='extrusion' else _primitive_operand)(emitter,atom,body_index,operand_index)
        calls[id(atom)]=call
        bindings.extend(local_bindings)
        operand_records.append(dict(operand_index=operand_index,kind=atom.kind,source=atom.evidence))
    signature=', '.join(['origin','x_axis','y_axis']+[name for name,_ in bindings])
    lines=[f'def make_body_{body_index:02d}(*, {signature}):',
           '    """Reconstruct one source body with explicit CSG operations."""',
           f'    body = {calls[id(result.base)]}']
    functions={'union':'union_rsolid','cut':'cut_rsolid','intersect':'intersect_rsolid'}
    for step_index,(operation,atom) in enumerate(result.steps,1):
        lines.extend([f'    tool_{step_index:02d} = {calls[id(atom)]}',
                      f'    body = scad.{functions[operation]}(body, tool_{step_index:02d})'])
    lines.append('    return _place_body(body, origin, x_axis, y_axis)')
    emitter.functions.append('\n'.join(lines))
    arguments=[f'origin={body}_ORIGIN_MM',f'x_axis={body}_X_AXIS',f'y_axis={body}_Y_AXIS']
    arguments.extend(f'{name}={constant}' for name,constant in bindings)
    emitter.calls.append(f'    bodies.append(make_body_{body_index:02d}('+', '.join(arguments)+'))')
    emitter.symbols['bodies'].append(dict(source_solid_index=body_index,strategy='hybrid_csg',
        body_function=f'make_body_{body_index:02d}',operands=operand_records,
        boolean_operations=[operation for operation,_ in result.steps],
        placement_semantics='rigid delta applied after CSG; rotation about world origin',
        history_recovered=False))


def _ensure_hybrid_helpers(emitter):
    if not getattr(emitter, 'uses_hybrid', False):
        emitter.functions.append(HYBRID_HELPERS)
        emitter.uses_hybrid = True


def _merge_hybrid_piece(emitter, result, body_index, part_index):
    """Merge one temporary hybrid emitter under names private to a partition cell."""
    temporary = DirectEmitter()
    add_hybrid(temporary, result, 1)
    prefix = f'BODY_{body_index:02d}_PART_{part_index:03d}_'
    mapping = {}
    for name in temporary.symbols['parameters']:
        mapping[name] = prefix + name
    helper_names = {'_place_body', '_angle_degrees', '_validate_axis'}
    function_names = []
    for text in temporary.functions:
        module = ast.parse(text)
        for node in module.body:
            if isinstance(node, ast.FunctionDef) and node.name not in helper_names:
                function_names.append(node.name)
    for name in function_names:
        mapping[name] = prefix.lower() + name
    renamer = _Names(mapping)
    for text in temporary.constants:
        if text.strip():
            tree = renamer.visit(ast.parse(text))
            emitter.constants.append(ast.unparse(ast.fix_missing_locations(tree)))
    for name, value in temporary.symbols['parameters'].items():
        emitter.symbols['parameters'][mapping[name]] = dict(
            value, source_solid_index=body_index, partition_index=part_index)
    for text in temporary.functions:
        module = ast.parse(text)
        nodes = [node for node in module.body
                 if not (isinstance(node, ast.FunctionDef) and node.name in helper_names)]
        if not nodes:
            continue
        updated = []
        for node in nodes:
            updated.append(ast.unparse(ast.fix_missing_locations(renamer.visit(node))))
        emitter.functions.append('\n\n'.join(updated))
    call_module = ast.parse(temporary.calls[0].strip())
    call_expr = call_module.body[0].value
    if isinstance(call_expr, ast.Call) and isinstance(call_expr.func, ast.Attribute):
        call_expr = call_expr.args[0]
    elif isinstance(call_expr, ast.Call):
        call_expr = call_expr
    else:
        raise ValueError('temporary hybrid call has unexpected structure')
    call_expr = renamer.visit(call_expr)
    return ast.unparse(ast.fix_missing_locations(call_expr))


def _merge_prism_piece(emitter, native, body_index, part_index):
    atom = Atom('extrusion', None, {}, dict(native.evidence), 'union', native)
    return _prism_operand(emitter, atom, body_index, part_index)


def add_partition(emitter, result, body_index):
    """Emit one source body as a verified union of direct native partition pieces."""
    _ensure_hybrid_helpers(emitter)
    body = f'BODY_{body_index:02d}'
    emitter.constants.extend([
        f'# Source body {body_index}: verified planar partition union.',
        f'{body}_ORIGIN_MM = (0.0, 0.0, 0.0)',
        f'{body}_X_AXIS = (1.0, 0.0, 0.0)',
        f'{body}_Y_AXIS = (0.0, 1.0, 0.0)'])
    for suffix in ('ORIGIN_MM', 'X_AXIS', 'Y_AXIS'):
        emitter.symbols['parameters'][f'{body}_{suffix}'] = dict(
            source_solid_index=body_index, kind='placement',
            edit_policy='rigid_world_delta_about_world_origin')
    piece_calls = []
    piece_records = []
    for part_index, piece in enumerate(result.parts, 1):
        if piece.method == 'prism':
            call, bindings = _merge_prism_piece(emitter, piece.result, body_index, part_index)
            binding_map = {name: constant for name, constant in bindings}
            call_tree = ast.parse(call, mode='eval').body
            call_tree = _Names(binding_map).visit(call_tree)
            piece_call = ast.unparse(ast.fix_missing_locations(call_tree))
        elif piece.method == 'hybrid':
            piece_call = _merge_hybrid_piece(emitter, piece.result, body_index, part_index)
        else:
            raise ValueError(f'unsupported partition piece method: {piece.method}')
        piece_calls.append(piece_call)
        source = getattr(piece.result, 'evidence', {})
        piece_records.append(dict(part_index=part_index, method=piece.method,
                                 source=source))
    if not piece_calls:
        raise ValueError('partition result has no pieces')
    lines = [f'def make_body_{body_index:02d}(*, origin, x_axis, y_axis):',
             '    """Reconstruct one source body from independently verified planar cells."""',
             f'    pieces = [{piece_calls[0]}']
    for piece_call in piece_calls[1:]:
        lines.append(f'        , {piece_call}')
    lines.extend([
        '    ]',
        '    merged = scad.boolean_rsolidlist(pieces, [], operation="union")',
        '    if len(merged) != 1:',
        '        raise ValueError("partition union did not produce one solid")',
        '    body = merged[0]',
    ])
    lines.append('    return _place_body(body, origin, x_axis, y_axis)')
    emitter.functions.append('\n'.join(lines))
    emitter.calls.append(
        f'    bodies.append(make_body_{body_index:02d}(origin={body}_ORIGIN_MM, '
        f'x_axis={body}_X_AXIS, y_axis={body}_Y_AXIS))')
    emitter.symbols['bodies'].append(dict(
        source_solid_index=body_index, strategy='planar_partition_union',
        body_function=f'make_body_{body_index:02d}', part_count=len(result.parts),
        parts=piece_records, boolean_operations=['union'] * max(0, len(result.parts) - 1),
        symmetric_difference_relative=result.evidence.get('symmetric_difference_relative'),
        placement_semantics='rigid delta applied after partition union; rotation about world origin',
        history_recovered=False))
