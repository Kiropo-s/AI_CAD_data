"""The sole dependency traversal and ID rewriting implementation."""
import copy
import heapq
from .model import PlanError


def dependencies(node):
    return tuple(ref for refs in node['inputs'].values() for ref in refs)


def reachable(nodes, roots):
    found, todo = set(), list(roots)
    while todo:
        key = todo.pop()
        if key in found:
            continue
        found.add(key)
        todo.extend(dependencies(nodes[key]))
    return found


def topological_order(nodes):
    incoming = {key:len(set(dependencies(value))) for key,value in nodes.items()}
    children = {key:[] for key in nodes}
    for key,value in nodes.items():
        for parent in set(dependencies(value)):
            if parent not in nodes:
                raise PlanError('schema.missing_reference', '/nodes/'+key+'/inputs', parent)
            children[parent].append(key)
    ready = [key for key,count in incoming.items() if count == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        key = heapq.heappop(ready)
        order.append(key)
        for child in children[key]:
            incoming[child] -= 1
            if incoming[child] == 0:
                heapq.heappush(ready, child)
    if len(order) != len(nodes):
        key = next(key for key,count in incoming.items() if count)
        raise PlanError('graph.cycle', '/nodes/'+key+'/inputs', 'Cyclic dependency')
    return order


def rewrite_ids(nodes, mapping):
    """Rewrite node IDs and declared dependencies only. Literals are untouched."""
    result = {}
    for key,value in nodes.items():
        new_key = mapping.get(key,key)
        if new_key in result:
            raise PlanError('graph.id_collision', '/nodes/'+new_key, 'Non-injective ID mapping')
        item = copy.deepcopy(value)
        item['inputs'] = {role:[mapping.get(ref,ref) for ref in refs]
                          for role,refs in item['inputs'].items()}
        result[new_key] = item
    return result
