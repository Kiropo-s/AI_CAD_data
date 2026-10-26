"""STEP-specific code planning on top of the shared safe sketch preparation."""
from __future__ import annotations

import ast
import copy
import hashlib
from itertools import groupby

from json2scapi.codegen.readable import StructuredEmitter, POLYLINE_HELPER
from json2scapi.codegen.sketch_builder import fmt_literal, fmt_num

CODE_STRUCTURE_SPEC = 'step-readable-v1'


class _LocalProfile(ast.NodeTransformer):
    """Make placement explicit without changing prepared sketch coordinates."""
    def __init__(self, names):
        self.names = names

    def visit_Name(self, node):
        return ast.copy_location(ast.Name(id=self.names.get(node.id, node.id), ctx=node.ctx), node)

    def visit_Call(self, node):
        self.generic_visit(node)
        if isinstance(node.func, ast.Attribute) and node.func.attr == 'make_sketch_rsketch':
            for keyword in node.keywords:
                if keyword.arg == 'name':
                    keyword.value = ast.Constant('inferred_profile')
                elif keyword.arg == 'plane':
                    keyword.value = ast.Dict(
                        keys=[ast.Constant(k) for k in ('origin', 'x_axis', 'y_axis')],
                        values=[ast.Name(id=k, ctx=ast.Load()) for k in ('origin', 'x_axis', 'y_axis')])
        return node


class StepEmitter:
    """Only one new, one-sided analytic prism per source solid is supported."""
    def __init__(self, payload, evidence):
        self.base = StructuredEmitter(payload, 'step_reconstruction')
        self.payload = self.base.payload
        self.evidence = evidence
        self.metadata = self.base.metadata
        self.metadata.update(code_structure_spec=CODE_STRUCTURE_SPEC,
                             emitter='step-prism-emitter-v1', templates={}, instances=[],
                             parameters={}, placements={}, reconstruction_operations=[],
                             source_coverage=copy.deepcopy(evidence['source_coverage']),
                             history_recovered=False)
        self.instances = []
        self.templates = []
        fingerprints = {}
        for feature in self.base.plan.features:
            if len(feature.steps) != 1:
                raise ValueError('STEP prism emitter requires one operation per solid')
            step = feature.steps[0]
            entity = self.payload['entities'][step]
            params = entity['params']
            if (params['operation'], params['extent'], params.get('taper', 0)) != ('new', 'one_side', 0):
                raise ValueError('Unsupported STEP operation contract')
            profile = self.base.plan.profiles[entity['inputs'][0]]
            frame = self.payload['entities'][self.payload['entities'][profile.ir_id]['params']['plane']]['params']['frame']
            # Renumber curve arguments locally; values remain independent per instance.
            counters, names = {}, {}
            for parameter in profile.parameters:
                counters[parameter.kind] = counters.get(parameter.kind, 0) + 1
                names[parameter.argument] = f'{parameter.kind}_{counters[parameter.kind]:02d}'
            node = ast.parse('\n'.join(self.base._profile(profile))).body[0]
            node.name = 'profile_template'
            node.body[0] = ast.Expr(ast.Constant('Build a local profile in the supplied world frame.'))
            node.args = ast.arguments(posonlyargs=[], args=[], vararg=None,
                kwonlyargs=[ast.arg(arg=k) for k in ('origin', 'x_axis', 'y_axis', *names.values())],
                kw_defaults=[None]*(3+len(names)), kwarg=None, defaults=[])
            node = ast.fix_missing_locations(_LocalProfile(names).visit(node))
            fingerprint = ast.dump(node, include_attributes=False)
            if fingerprint not in fingerprints:
                number = len(self.templates)+1
                node.name = f'make_profile_{number:02d}'
                template = dict(number=number, profile=node.name, feature=f'apply_feature_{number:02d}',
                                node=node, arguments=list(names.values()),
                                fingerprint=hashlib.sha256(fingerprint.encode()).hexdigest())
                fingerprints[fingerprint] = template
                self.templates.append(template)
            template = fingerprints[fingerprint]
            distance = feature.dimensions[step, 'distance']
            index = feature.number
            bindings = {k: f'BODY_{index:02d}_{k.upper()}' + ('_MM' if k == 'origin' else '')
                        for k in ('origin', 'x_axis', 'y_axis')}
            bindings['distance'] = distance.name
            bindings.update({names[p.argument]: p.name for p in profile.parameters})
            instance = dict(number=index, step=step, profile=profile.ir_id, frame=frame,
                            template=template, bindings=bindings)
            self.instances.append(instance)
            self._record(instance, feature, names)
        # Shared sketch metadata was indexed by source instance functions. Keep
        # that evidence under IR ids and publish only functions actually emitted.
        source_profiles = self.metadata['profiles']
        self.metadata['source_profiles'] = {value['ir_id']:value for value in source_profiles.values()}
        self.metadata['profiles'] = {}
        self.metadata['symbols'] = {key:value for key,value in self.metadata['symbols'].items()
                                    if value.get('kind') != 'profile_function'}
        for template in self.templates:
            members = [i for i in self.instances if i['template'] is template]
            self.metadata['profiles'][template['profile']] = {'ir_ids':[i['profile'] for i in members]}
            self.metadata['symbols'][template['profile']] = dict(kind='profile_function',
                ir_ids=[i['profile'] for i in members])
            self.metadata['symbols'][template['feature']] = dict(kind='feature_function',
                ir_ids=[i['step'] for i in members])
            self.metadata['feature_groups'][template['feature']] = [i['step'] for i in members]

    def _record(self, instance, feature, names):
        index, step = instance['number'], instance['step']
        selected = self.evidence['features'][index-1]['selected']
        evidence_ref = f'recognition.features[{index-1}]'
        for parameter in feature.parameters:
            distance = parameter.kind == 'distance'
            origin_kind = selected.get('distance_origin_kind', 'derived') if distance else 'measured'
            item = dict(value=parameter.value, units='mm', quantity='length', kind=parameter.kind,
                        origin_kind=origin_kind, edit_policy='independent', expression=None,
                        evidence_refs=[evidence_ref+'.selected' if distance else evidence_ref+'.profile_edges'],
                        argument='distance' if distance else names[parameter.argument],
                        bindings=[{**p, 'source': self.payload['entities'][p['ir_id']].get('source', {})}
                                  for p in parameter.paths],
                        design_parameter_recovered=False)
            self.metadata['parameters'][parameter.name] = item
            self.metadata['symbols'][parameter.name] = item
        plane_id = self.payload['entities'][instance['profile']]['params']['plane']
        for key in ('origin', 'x_axis', 'y_axis'):
            name = instance['bindings'][key]
            item = dict(value=instance['frame'][key], units='mm' if key == 'origin' else '1',
                        quantity='position' if key == 'origin' else 'direction', origin_kind='inferred',
                        edit_policy='independent', expression=None, argument=key,
                        constraints='finite origin; paired axes must be orthonormal',
                        evidence_refs=[evidence_ref+'.coordinate_frame'],
                        bindings=[{'ir_id': plane_id, 'path': 'params.frame.'+key}],
                        design_parameter_recovered=False)
            self.metadata['parameters'][name] = item
            self.metadata['symbols'][name] = item
        template = instance['template']
        self.metadata['instances'].append(dict(source_solid_index=index, output_body_index=index-1,
            operation=step, template=template['feature'], bindings=instance['bindings'],
            shared_design_parameters=False))
        self.metadata['placements'][f'BODY_{index:02d}'] = dict(
            local_to_world=instance['frame'], application='sketch_plane_once',
            direction_expression='cross(x_axis, y_axis)', evidence_refs=[evidence_ref+'.coordinate_frame'])
        self.metadata['reconstruction_operations'].append(dict(
            id=step, operation='new_prism', inputs=[instance['profile']], depends_on=[],
            source_solid_indices=[index], output_body_indices=[index-1], history_recovered=False))

    def emit(self):
        lines = ['"""STEP reconstruction (step-readable-v1); inferred operations, no source history."""',
                 '', 'import simplecadapi as scad', '']
        for instance in self.instances:
            index = instance['number']
            lines.append(f'# Body {index:02d}: independent shape dimensions (mm).')
            for parameter in self.base.plan.features[index-1].parameters:
                lines.append(f'{parameter.name} = {fmt_num(parameter.value)}')
            lines.append(f'# Body {index:02d}: world placement; axes are dimensionless.')
            for key in ('origin', 'x_axis', 'y_axis'):
                lines.append(f'{instance["bindings"][key]} = {fmt_literal(instance["frame"][key])}')
            lines.append('')
        lines += ['BODIES = None', '', '']
        for template in self.templates:
            lines += [ast.unparse(template['node']), '', '']
        for template in self.templates:
            arguments = ['origin', 'x_axis', 'y_axis', 'distance', *template['arguments']]
            signature = ', '.join(arguments)
            profile_args = ', '.join(f'{k}={k}' for k in arguments if k != 'distance')
            lines += [f'def {template["feature"]}(bodies, *, {signature}):',
                      '    """Append a fresh analytic prism; direction follows the supplied frame."""',
                      '    direction = _validate_frame(origin, x_axis, y_axis)',
                      "    if not (0 < distance < float('inf')):",
                      "        raise ValueError('distance must be positive and finite')",
                      f'    faces = {template["profile"]}({profile_args})',
                      '    for face in faces:',
                      '        bodies.append(scad.extrude_rsolid(profile=face, direction=direction, distance=distance))',
                      '    return bodies', '', '']
            self.metadata['templates'][template['feature']] = dict(
                profile_function=template['profile'], fingerprint=template['fingerprint'],
                reuse_rule='exact prepared profile AST after explicit parameter/placement binding',
                design_relationship_recovered=False)
        # Group only adjacent instances: source reconstruction order is preserved.
        runs = [list(group) for _, group in groupby(self.instances, key=lambda i:i['template']['number'])]
        helper_names = {}
        for run_number, run in enumerate(runs, 1):
            if len(run) < 3:
                continue
            name = f'_feature_instances_{run_number:02d}'
            helper_names[run_number] = name
            lines += [f'def {name}():', '    """Read independent instance constants at build time."""']
            for instance in run:
                values = ', '.join(f'{key}={value}' for key, value in instance['bindings'].items())
                lines.append(f'    yield dict({values})')
            lines += ['', '']
        lines += ['def build():', '    """Rebuild all inferred bodies in world coordinates."""', '    bodies = []']
        for run_number, run in enumerate(runs, 1):
            if run_number in helper_names:
                lines += [f'    for instance in {helper_names[run_number]}():',
                          f'        bodies = {run[0]["template"]["feature"]}(bodies, **instance)']
            else:
                for instance in run:
                    values = ', '.join(f'{key}={value}' for key, value in instance['bindings'].items())
                    lines += [f'    bodies = {instance["template"]["feature"]}(bodies, {values})']
        lines += ['    global BODIES', '    BODIES = list(bodies)', '    return _as_model(bodies)', '', '']
        if self.base.uses_polyline:
            lines += [POLYLINE_HELPER, '', '']
        lines += [_HELPERS, '']
        code = '\n'.join(lines)
        tree = ast.parse(code)
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        self.metadata['structure'] = dict(
            lines=len(code.splitlines()), function_count=len(functions),
            build_lines=next(n.end_lineno-n.lineno+1 for n in functions if n.name=='build'),
            max_function_lines=max(n.end_lineno-n.lineno+1 for n in functions),
            feature_count=len(self.instances), profile_count=len(self.instances),
            template_count=len(self.templates), instance_count=len(self.instances),
            independent_parameter_count=len(self.metadata['parameters']),
            polyline_helper=self.base.uses_polyline)
        return code


_HELPERS = '''def _validate_frame(origin, x_axis, y_axis):
    """Require finite, unit, perpendicular axes; never silently normalize placement."""
    if any(len(v) != 3 for v in (origin, x_axis, y_axis)):
        raise ValueError('placement vectors must have three components')
    if any(not (-float('inf') < value < float('inf')) for v in (origin, x_axis, y_axis) for value in v):
        raise ValueError('placement must be finite')
    if any(abs(sum(v*v for v in axis)-1) > 1e-10 for axis in (x_axis, y_axis)):
        raise ValueError('placement axes must have unit length')
    if abs(sum(x*y for x, y in zip(x_axis, y_axis))) > 1e-10:
        raise ValueError('placement axes must be perpendicular')
    return (x_axis[1]*y_axis[2]-x_axis[2]*y_axis[1],
            x_axis[2]*y_axis[0]-x_axis[0]*y_axis[2],
            x_axis[0]*y_axis[1]-x_axis[1]*y_axis[0])


def _as_model(bodies):
    """Return world solids in an identity-placement assembly when needed."""
    if not bodies:
        raise ValueError('model produced no solids')
    if len(bodies) == 1:
        return bodies[0]
    assembly = scad.make_assembly_rassembly('step-reconstruction')
    for index, body in enumerate(bodies):
        part = scad.make_part_rpart(f'body-{index+1:02d}', body)
        assembly = scad.add_component_rassembly(
            assembly, part, f'component-{index+1:02d}', scad.identity_placement_rplacement(),
        )
    return assembly'''
