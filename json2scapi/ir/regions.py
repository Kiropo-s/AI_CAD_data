"""Analytic filled regions: authoritative geometry, not source outer flags."""
from __future__ import annotations
from typing import Any, Mapping
from json2scapi.ir.geometry2d import boundary_distance, location, loop_curves
from json2scapi.ir.preparation import prepare_geometry


def profile_regions(profile: Mapping[str, Any], entities: Mapping[str, Mapping[str, Any]],
                    tolerance: float = 1e-6, *, prepared: bool = False) -> list[tuple[str, list[str]]]:
    loop_ids = profile['inputs']
    if not loop_ids:
        raise ValueError('profile has no loops')
    if not prepared:
        # Adapter may call before the complete IR exists. Prepare this profile's
        # dependency closure; full-document preparation later checks shared uses.
        ids = set(loop_ids)
        ids.update(curve for key in loop_ids for curve in entities[key]['inputs'])
        document, _ = prepare_geometry({'entities':{key:entities[key] for key in sorted(ids)},
                                        'tolerance':{'length':tolerance}})
        entities = document['entities']
    geometries = [loop_curves(entities[key], entities, tolerance) for key in loop_ids]
    parent = [None] * len(loop_ids)
    for i,(a,_) in enumerate(geometries):
        for j in range(i+1,len(loop_ids)):
            b,_ = geometries[j]
            if any(boundary_distance(x,y) <= tolerance for x in a for y in b):
                raise ValueError(f'profile loops {loop_ids[i]} and {loop_ids[j]} touch or overlap')
    for i,(curves,area) in enumerate(geometries):
        candidates = []
        for j,(outer,outer_area) in enumerate(geometries):
            if i == j or outer_area <= area:
                continue
            classification = location(curves[0].start, outer, tolerance)
            if classification == -1:
                raise ValueError('ambiguous profile containment at declared tolerance')
            if classification == 1:
                candidates.append(j)
        if candidates:
            parent[i] = min(candidates,key=lambda j:geometries[j][1])
    depths = []
    for i in range(len(loop_ids)):
        seen, cursor = set(), parent[i]
        while cursor is not None:
            if cursor in seen:
                raise ValueError('cyclic profile containment')
            seen.add(cursor)
            cursor = parent[cursor]
        depths.append(len(seen))
    return [(loop_ids[i], [loop_ids[j] for j in range(len(loop_ids)) if parent[j] == i])
            for i,depth in enumerate(depths) if depth % 2 == 0]
