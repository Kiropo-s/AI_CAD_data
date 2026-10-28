"""Closed operation and parameter type registry for STEP Plan 1."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Arg:
    type: str
    unit: str = '1'
    rule: str = ''


@dataclass(frozen=True)
class Signature:
    result: str
    inputs: dict
    args: dict


P2 = Arg('point2', 'mm')
P3 = Arg('point3', 'mm')
DIR = Arg('direction3')
POS = Arg('number', 'mm', 'positive')
NUM = Arg('number', 'mm')
ANGLE = Arg('number', 'rad')
OPS = {
 'curve.line2': Signature('Curve2', {}, dict(start=P2, end=P2)),
 'curve.circle2': Signature('Curve2', {}, dict(center=P2, radius=POS, winding=Arg('winding'))),
 'curve.ellipse2': Signature('Curve2', {}, dict(center=P2, major_radius=POS, minor_radius=POS, major_axis_angle=ANGLE, winding=Arg('winding'))),
 'curve.arc2': Signature('Curve2', {}, dict(center=P2, radius=POS, start_angle=ANGLE, sweep_angle=ANGLE)),
 'curve.bspline2': Signature('Curve2', {}, dict(degree=Arg('integer'), poles=Arg('points2','mm'),
    knots=Arg('numbers'), multiplicities=Arg('integers'), weights=Arg('numbers'), periodic=Arg('boolean'),
    trim=Arg('interval'), reversed=Arg('boolean'))),
 'wire.loop2': Signature('Loop2', dict(curves=('Curve2',1,None)), {}),
 'profile.face2': Signature('Profile2', dict(outer=('Loop2',1,1), holes=('Loop2',0,None)), {}),
 'frame.rigid3': Signature('Frame3', {}, dict(origin=P3, x_axis=DIR, y_axis=DIR)),
 'solid.extrude': Signature('Region3', dict(profile=('Profile2',1,1)), dict(distance=POS)),
 'solid.revolve': Signature('Region3', dict(profile=('Profile2',1,1)), dict(angle=ANGLE)),
 'solid.box': Signature('Region3', {}, dict(width=POS, depth=POS, height=POS)),
 'solid.cylinder': Signature('Region3', {}, dict(radius=POS, height=POS)),
 'solid.cone': Signature('Region3', {}, dict(radius_bottom=Arg('number','mm','nonnegative'),
    radius_top=Arg('number','mm','nonnegative'), height=POS)),
 'solid.sphere': Signature('Region3', {}, dict(radius=POS)),
 'solid.torus': Signature('Region3', {}, dict(major_radius=POS, minor_radius=POS)),
 'boolean.union': Signature('Region3', dict(operands=('Region3',2,None)), {}),
 'boolean.cut': Signature('Region3', dict(target=('Region3',1,1), tool=('Region3',1,1)), {}),
 'boolean.intersect': Signature('Region3', dict(operands=('Region3',2,2)), {}),
 'shape.transform': Signature('Region3', dict(shape=('Region3',1,1), frame=('Frame3',1,1)), {}),
 'shape.uniform_scale': Signature('Region3', dict(shape=('Region3',1,1)),
    dict(factor=Arg('number','1','positive'), center=P3)),
}
PARAMETER_TYPES = {'number','point2','point3','direction3','integer','points2','numbers','integers',
                   'boolean','interval','winding'}
