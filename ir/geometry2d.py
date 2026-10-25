"""Analytic sketch primitives shared by IR preparation, regions and emission.

No CAD kernel or polygon tessellation is used for authoritative predicates.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

TAU = 2 * math.pi
Point = tuple[float, float]


def sub(a, b):
    return a[0] - b[0], a[1] - b[1]


def cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def arc_geometry(params):
    """Compute from three points in a translated frame to avoid cancellation."""
    a, b, c = (tuple(map(float, params[key])) for key in ('start', 'mid', 'end'))
    u, v = sub(b, a), sub(c, a)
    scale = max(math.hypot(*u), math.hypot(*v), math.dist(b, c))
    if scale == 0 or min(math.dist(a, b), math.dist(b, c), math.dist(a, c)) <= scale * 1e-14:
        raise ValueError('degenerate arc points')
    u, v = tuple(x / scale for x in u), tuple(x / scale for x in v)
    determinant = 2 * cross(u, v)
    if abs(determinant) <= 1e-12:
        raise ValueError('collinear or numerically unreliable arc points')
    uu, vv = dot(u, u), dot(v, v)
    center = (a[0] + scale * (uu * v[1] - vv * u[1]) / determinant,
              a[1] + scale * (u[0] * vv - v[0] * uu) / determinant)
    radius = math.dist(a, center)
    angles = [math.atan2(p[1] - center[1], p[0] - center[0]) for p in (a, b, c)]
    sweep = (angles[2] - angles[0]) % TAU
    if (angles[1] - angles[0]) % TAU > sweep:
        sweep -= TAU
    if not math.isfinite(radius) or sweep == 0:
        raise ValueError('numerically unreliable arc')
    return center, radius, sweep


@dataclass(frozen=True)
class Curve:
    kind: str
    start: Point
    end: Point
    center: Point = (0.0, 0.0)
    radius: float = 0.0
    sweep: float = 0.0

    @property
    def angle(self):
        return math.atan2(self.start[1] - self.center[1], self.start[0] - self.center[0])

    def at(self, t):
        if self.kind == 'line':
            return tuple(a + t * (b - a) for a, b in zip(self.start, self.end))
        angle = self.angle + t * self.sweep
        return (self.center[0] + self.radius * math.cos(angle),
                self.center[1] + self.radius * math.sin(angle))

    def includes_angle(self, angle, epsilon=1e-12):
        if abs(self.sweep) >= TAU - epsilon:
            return True
        delta = ((angle - self.angle) if self.sweep > 0 else (self.angle - angle)) % TAU
        return delta <= abs(self.sweep) + epsilon or TAU - delta <= epsilon

    def distance(self, p):
        if self.kind == 'line':
            v, w = sub(self.end, self.start), sub(p, self.start)
            t = max(0.0, min(1.0, dot(v, w) / dot(v, v)))
            return math.dist(p, self.at(t))
        angle = math.atan2(p[1] - self.center[1], p[0] - self.center[0])
        if self.includes_angle(angle):
            return abs(math.dist(p, self.center) - self.radius)
        return min(math.dist(p, self.start), math.dist(p, self.end))

    def signed_area(self, origin=(0.0, 0.0)):
        a, b = sub(self.start, origin), sub(self.end, origin)
        if self.kind == 'line':
            return cross(a, b) / 2
        c = sub(self.center, origin)
        return (c[0] * (b[1] - a[1]) - c[1] * (b[0] - a[0]) + self.radius**2 * self.sweep) / 2


def curve_from_node(node):
    p = node['params']
    kind = node['op'].removeprefix('sketch.')
    if kind == 'line':
        return Curve(kind, tuple(p['start']), tuple(p['end']))
    if kind == 'circle':
        center, radius = tuple(p['center']), float(p['radius'])
        point = (center[0] + radius, center[1])
        return Curve(kind, point, point, center, radius, TAU)
    if kind == 'arc':
        center, radius, sweep = arc_geometry(p)
        return Curve(kind, tuple(p['start']), tuple(p['end']), center, radius, sweep)
    raise ValueError(f'unsupported sketch curve: {kind}')


def intersections(a, b):
    """Return exact boundary candidates and whether boundaries overlap."""
    if a.kind == 'line' and b.kind != 'line':
        return intersections(b, a)
    if a.kind == 'line':
        v, w, q = sub(a.end, a.start), sub(b.end, b.start), sub(b.start, a.start)
        denominator = cross(v, w)
        scale = math.hypot(*v) * math.hypot(*w)
        if abs(denominator) <= 1e-14 * scale:
            if abs(cross(q, v)) > 1e-12 * max(math.hypot(*v), 1e-300):
                return [], False
            ts = sorted(dot(sub(p, a.start), v) / dot(v, v) for p in (b.start, b.end))
            lo, hi = max(0.0, ts[0]), min(1.0, ts[1])
            if hi < lo:
                return [], False
            return [a.at(lo)], (hi - lo) * math.hypot(*v) > 1e-12
        t, u = cross(q, w) / denominator, cross(q, v) / denominator
        return ([a.at(t)] if -1e-12 <= t <= 1+1e-12 and -1e-12 <= u <= 1+1e-12 else []), False
    if b.kind == 'line':
        v, offset = sub(b.end, b.start), sub(b.start, a.center)
        length2 = dot(v, v)
        t0 = -dot(offset, v) / length2
        foot = b.at(t0)
        residual = a.radius**2 - math.dist(foot, a.center)**2
        epsilon = 1e-13 * max(a.radius**2, 1e-300)
        if residual < -epsilon:
            return [], False
        shift = math.sqrt(max(0.0, residual) / length2)
        points = [b.at(t) for t in (t0-shift, t0+shift) if -1e-12 <= t <= 1+1e-12]
        return [p for p in points if a.includes_angle(math.atan2(p[1]-a.center[1], p[0]-a.center[0]))], False
    d = math.dist(a.center, b.center)
    epsilon = 1e-12 * max(a.radius, b.radius, 1e-300)
    if d <= epsilon and abs(a.radius-b.radius) <= epsilon:
        candidates = [p for p in (a.start, a.end, b.start, b.end)
                      if a.distance(p) <= epsilon and b.distance(p) <= epsilon]
        # Interior points detect coincident arc intervals, not just shared endpoints.
        overlap = (abs(a.sweep) >= TAU-1e-12 or abs(b.sweep) >= TAU-1e-12)
        for first, second in ((a, b), (b, a)):
            breaks = [0.0, 1.0]
            for p in (second.start, second.end):
                angle = math.atan2(p[1]-first.center[1], p[0]-first.center[0])
                delta = ((angle-first.angle) if first.sweep > 0 else (first.angle-angle)) % TAU
                if delta < abs(first.sweep):
                    breaks.append(delta / abs(first.sweep))
            breaks.sort()
            for lo, hi in zip(breaks, breaks[1:]):
                if hi-lo > 1e-12 and second.distance(first.at((lo+hi)/2)) <= epsilon:
                    overlap = True
        return candidates, overlap
    if d == 0 or d > a.radius+b.radius+epsilon or d < abs(a.radius-b.radius)-epsilon:
        return [], False
    x = (a.radius*a.radius - b.radius*b.radius + d*d) / (2*d)
    height2 = a.radius*a.radius - x*x
    if height2 < -epsilon * max(a.radius, b.radius):
        return [], False
    h = math.sqrt(max(0.0, height2))
    ux, uy = ((b.center[i]-a.center[i])/d for i in (0, 1))
    points = [(a.center[0]+x*ux-sign*h*uy, a.center[1]+x*uy+sign*h*ux) for sign in (-1, 1)]
    return [p for p in points if all(c.includes_angle(math.atan2(p[1]-c.center[1], p[0]-c.center[0])) for c in (a, b))], False


def boundary_distance(a, b):
    points, overlap = intersections(a, b)
    if points or overlap:
        return 0.0
    distances = [c.distance(p) for c, other in ((a,b), (b,a)) for p in (other.start, other.end)]
    if a.kind == 'line' and b.kind != 'line':
        a, b = b, a
    if a.kind != 'line' and b.kind == 'line':
        v = sub(b.end, b.start)
        length = math.hypot(*v)
        normal = (-v[1]/length, v[0]/length)
        for sign in (-1, 1):
            p = (a.center[0]+sign*a.radius*normal[0], a.center[1]+sign*a.radius*normal[1])
            angle = math.atan2(p[1]-a.center[1], p[0]-a.center[0])
            if a.includes_angle(angle):
                distances.append(b.distance(p))
    elif a.kind != 'line' and b.kind != 'line':
        angle = math.atan2(b.center[1]-a.center[1], b.center[0]-a.center[0])
        for c, other in ((a,b), (b,a)):
            for theta in (angle, angle+math.pi):
                if c.includes_angle(theta):
                    distances.append(other.distance((c.center[0]+c.radius*math.cos(theta), c.center[1]+c.radius*math.sin(theta))))
    return min(distances)


def loop_curves(loop, entities, tolerance):
    curves = [curve_from_node(entities[key]) for key in loop['inputs']]
    if not curves:
        raise ValueError('empty sketch loop')
    if any(c.kind == 'circle' for c in curves) and len(curves) != 1:
        raise ValueError('circle must be the only edge in its loop')
    for c in curves:
        if (c.kind == 'line' and math.dist(c.start,c.end) <= tolerance) or (c.kind != 'line' and c.radius <= tolerance):
            raise ValueError('degenerate sketch curve at declared tolerance')
    for i, a in enumerate(curves):
        if a.kind == 'circle':
            continue
        if a.end != curves[(i+1) % len(curves)].start:
            raise ValueError('sketch loop is not exactly closed after preparation')
        for j in range(i+1, len(curves)):
            b = curves[j]
            adjacent = j == i+1 or (i == 0 and j == len(curves)-1)
            points, overlap = intersections(a,b)
            shared = [p for p in (a.start,a.end) if p in (b.start,b.end)]
            if overlap or (adjacent and any(all(math.dist(p,q) > tolerance for q in shared) for p in points)):
                raise ValueError('self-intersecting or overlapping sketch loop')
            if not adjacent and boundary_distance(a,b) <= tolerance:
                raise ValueError('self-intersecting or touching sketch loop')
    area = abs(sum(c.signed_area(curves[0].start) for c in curves))
    if area <= tolerance*tolerance:
        raise ValueError('degenerate sketch loop area')
    return curves, area


def location(p, curves, tolerance):
    """Even/odd analytic horizontal-ray intersections, split at arc extrema."""
    if any(c.distance(p) <= tolerance for c in curves):
        return -1
    count = 0
    for c in curves:
        if c.kind == 'line':
            a,b = c.start,c.end
            if (a[1] > p[1]) != (b[1] > p[1]):
                x = a[0] + (p[1]-a[1])*(b[0]-a[0])/(b[1]-a[1])
                count += x > p[0]
            continue
        start, end = c.angle, c.angle+c.sweep
        cuts = [start, end]
        for k in range(-5, 6):
            angle = math.pi/2+k*math.pi
            if min(start,end) < angle < max(start,end):
                cuts.append(angle)
        cuts.sort(reverse=c.sweep < 0)
        for lo,hi in zip(cuts,cuts[1:]):
            ya,yb = (c.center[1]+c.radius*math.sin(t) for t in (lo,hi))
            if (ya > p[1]) != (yb > p[1]):
                xoffset = math.sqrt(max(0.0, c.radius**2-(p[1]-c.center[1])**2))
                x = c.center[0] + (xoffset if math.cos((lo+hi)/2) >= 0 else -xoffset)
                count += x > p[0]
    return 1 if count % 2 else 0
