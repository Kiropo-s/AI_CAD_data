"""Explicit graph passes; no arbitrary recursive string replacement."""
import copy
from .graph import topological_order, reachable


def flatten_unions(nodes, roots):
    nodes=copy.deepcopy(nodes)
    for key in topological_order(nodes):
        n=nodes[key]
        if n['op']=='boolean.union':
            refs=[]
            for child in n['inputs']['operands']:
                if nodes[child]['op']=='boolean.union':refs.extend(nodes[child]['inputs']['operands'])
                else:refs.append(child)
            n['inputs']['operands']=refs
    selected=reachable(nodes,roots)
    return {k:v for k,v in nodes.items() if k in selected}
