"""SCAPI sketch construction and deterministic Python literal formatting."""
from __future__ import annotations
import math
import re
from typing import Any, Mapping, Sequence


def fmt_num(value: Any) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('non-finite geometry parameter')
    return repr(number).removesuffix('.0')


def fmt_vec(values: Sequence[float]) -> str:
    return '(' + ', '.join(fmt_num(value) for value in values) + ')'


def fmt_literal(value: Any) -> str:
    if value is None or isinstance(value, (bool, str)):
        return repr(value)
    if isinstance(value, (int, float)):
        return fmt_num(value)
    if isinstance(value, (list, tuple)):
        return '[' + ', '.join(fmt_literal(item) for item in value) + ']'
    if isinstance(value, Mapping):
        return '{' + ', '.join(f'{key!r}: {fmt_literal(item)}' for key,item in value.items()) + '}'
    raise ValueError(f'unsupported literal type: {type(value).__name__}')


def _slug(value: str) -> str:
    return re.sub(r'[^A-Za-z0-9_]+', '_', str(value)).strip('_') or 'feature'


def _vec2(value, path):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f'{path}: expected 2 numbers')
    return tuple(map(float,value))


def _vec3(value, path):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f'{path}: expected 3 numbers')
    return tuple(map(float,value))


def _circumcenter(a, b, c):
    from json2scapi.ir.geometry2d import arc_geometry
    return arc_geometry({'start':a, 'mid':b, 'end':c})[0]


class SketchBuilder:
    """Loop-local exact point identities, after bounded IR preparation."""
    def __init__(self, plane_id, frame, variable):
        self.plane_id, self.frame, self.variable = plane_id, frame, variable
        self.lines = []
        self.points = {}
        self.point_count = self.entity_count = 0
        self.curve_entity_ids = {}
        self.point_expressions = {}

    def point(self, coordinates):
        key = tuple(map(float, coordinates))
        if key not in self.points:
            self.point_count += 1
            point_id = f'p{self.point_count}'
            self.points[key] = point_id
            x,y = self.point_expressions.get(key, (fmt_num(key[0]),fmt_num(key[1])))
            self.lines.append(f'    {self.variable} = scad.add_point_rsketch('
                              f'sketch={self.variable}, point_id={point_id!r}, x={x}, y={y})')
        return self.points[key]

    def next_entity_id(self):
        self.entity_count += 1
        return f'e{self.entity_count}'

    def line(self, start, end):
        entity_id = self.next_entity_id()
        start_id, end_id = self.point(start), self.point(end)
        self.lines.append(f'    {self.variable} = scad.add_line_rsketch('
                          f'sketch={self.variable}, entity_id={entity_id!r}, start={start_id!r}, end={end_id!r})')
        return entity_id

    def arc(self, start, mid, end, center):
        from json2scapi.ir.geometry2d import arc_geometry
        center, _, sweep = arc_geometry({'start':start, 'mid':mid, 'end':end})
        if sweep < 0:
            start,end = end,start
        entity_id = self.next_entity_id()
        start_id, end_id, center_id = self.point(start), self.point(end), self.point(center)
        self.lines.append(f'    {self.variable} = scad.add_arc_rsketch('
                          f'sketch={self.variable}, entity_id={entity_id!r}, '
                          f'start={start_id!r}, end={end_id!r}, center={center_id!r})')
        return entity_id

    def circle(self, center, radius, radius_expr=None):
        entity_id = self.next_entity_id()
        center_id = self.point(center)
        self.lines.append(f'    {self.variable} = scad.add_circle_rsketch('
                          f'sketch={self.variable}, entity_id={entity_id!r}, '
                          f'center={center_id!r}, radius={radius_expr or fmt_num(radius)})')
        return entity_id
