"""Non-mutating, deterministic sketch preparation; independent of source format."""
from __future__ import annotations

import copy
import math
from json2scapi.ir.geometry2d import arc_geometry, curve_from_node, loop_curves

PREPARATION_VERSION = 'ir-geometry-v1'


def prepare_geometry(payload):
    prepared = copy.deepcopy(payload)
    entities = prepared['entities']
    tolerance = float(prepared.get('tolerance', {}).get('length', 1e-6))
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError('tolerance.length: expected positive finite number')
    changes = []
    arcs = {}
    for key in sorted(entities):
        node = entities[key]
        p = node.get('params', {})
        if node.get('op') == 'sketch.arc':
            try:
                center, radius, sweep = arc_geometry(p)
                if 'center' in p and math.dist(center, p['center']) > tolerance:
                    raise ValueError('auxiliary center conflicts with start/mid/end')
                if 'radius' in p and abs(radius-float(p['radius'])) > tolerance:
                    raise ValueError('auxiliary radius conflicts with start/mid/end')
                if 'ccw' in p and p['ccw'] != (sweep > 0):
                    raise ValueError('auxiliary ccw conflicts with start/mid/end')
                arcs[key] = (center, radius, sweep)
            except (ValueError, OverflowError) as error:
                raise ValueError(f'entities.{key}.params: {error}') from error
        if node.get('op') in {'sketch.line', 'sketch.circle'}:
            c = curve_from_node(node)
            if (c.kind == 'line' and math.dist(c.start,c.end) <= tolerance) or (c.kind == 'circle' and c.radius <= tolerance):
                raise ValueError(f'entities.{key}: degenerate curve at declared tolerance')
    # Nodes are endpoint FIELD identities; only ordered loop adjacency creates unions.
    parents = {}
    def find(item):
        parents.setdefault(item, item)
        if parents[item] != item:
            parents[item] = find(parents[item])
        return parents[item]
    for key in sorted(entities):
        loop = entities[key]
        if loop.get('op') != 'sketch.loop':
            continue
        ids = loop['inputs']
        if len(ids) == 1 and entities[ids[0]]['op'] == 'sketch.circle':
            continue
        for a,b in zip(ids, ids[1:]+ids[:1]):
            if entities[a]['op'] not in {'sketch.line','sketch.arc'} or entities[b]['op'] not in {'sketch.line','sketch.arc'}:
                raise ValueError(f'entities.{key}: line/arc loop expected')
            first, second = (a,'end'), (b,'start')
            pa, pb = entities[a]['params']['end'], entities[b]['params']['start']
            if math.dist(pa,pb) > tolerance:
                raise ValueError(f'entities.{key}: gap between {a!r}.end and {b!r}.start exceeds tolerance')
            x,y = find(first),find(second)
            if x != y:
                parents[max(x,y)] = min(x,y)
    groups = {}
    for endpoint in sorted(parents):
        groups.setdefault(find(endpoint), []).append(endpoint)
    for members in groups.values():
        representative = min(members)
        value = list(entities[representative[0]]['params'][representative[1]])
        for key,field in members:
            old = entities[key]['params'][field]
            distance = math.dist(old,value)
            if distance > tolerance:
                raise ValueError(f'entities.{key}.params.{field}: shared endpoint normalization exceeds tolerance (transitive/shared-curve conflict)')
            if old != value:
                changes.append({'ir_id':key, 'path':f'params.{field}', 'original':list(old),
                                'prepared':list(value), 'displacement':distance, 'kind':'endpoint',
                                'representative':{'ir_id':representative[0], 'path':f'params.{representative[1]}'}})
                entities[key]['params'][field] = list(value)
    for key in sorted(arcs):
        p = entities[key]['params']
        center,radius,sweep = arc_geometry(p)
        # Materialize derived values in the prepared copy, including missing auxiliaries.
        # Re-preparing that copy performs no additional adjustment.
        for field,value in (('center',list(center)), ('radius',radius), ('ccw',sweep > 0)):
            old = p.get(field)
            if old != value:
                changes.append({'ir_id':key, 'path':f'params.{field}', 'original':copy.deepcopy(old),
                                'prepared':copy.deepcopy(value), 'kind':'derived_arc'})
                p[field] = value
    for key in sorted(entities):
        if entities[key].get('op') == 'sketch.loop':
            try:
                loop_curves(entities[key], entities, tolerance)
            except ValueError as error:
                raise ValueError(f'entities.{key}: {error}') from error
    return prepared, {'version':PREPARATION_VERSION, 'length_tolerance':tolerance, 'changes':changes}
