"""Bounded native-shape CSG search; no serialized CAD IR or residual embedding.

Only analytic primitives and independently constructible cap extrusions may
become operands. Source differences are measurements, never emitted operands.
"""
from dataclasses import dataclass
import heapq
import math
import time

import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse, BRepAlgoAPI_Common
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepLib import BRepLib
from OCP.BRepPrimAPI import (BRepPrimAPI_MakePrism, BRepPrimAPI_MakeCylinder,
                            BRepPrimAPI_MakeCone, BRepPrimAPI_MakeSphere, BRepPrimAPI_MakeTorus, BRepPrimAPI_MakeBox)
from OCP.GeomAbs import (GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere,
                        GeomAbs_Torus, GeomAbs_Circle)
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopoDS import TopoDS
from OCP.TopTools import TopTools_ListOfShape
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Vec
from json2scapi.evaluation.metrics import area, bbox, volume, solid_count, boolean_volume
from step2code_direct.geometry import (NativePrism, UnsupportedGeometry, explore, check_profile,
                                      face_wires, wire_edges)


@dataclass
class Atom:
    kind: str
    shape: object
    parameters: dict
    evidence: dict
    preferred_operation: str = 'union'
    prism: NativePrism | None = None


@dataclass
class HybridResult:
    """Transient native operands and chosen source-emission steps, not CAD IR."""
    base: Atom
    steps: tuple
    shape: object
    evidence: dict


@dataclass
class State:
    shape: object
    base: int
    steps: tuple
    missing: float
    extra: float
    nonmonotone_steps: int = 0

    @property
    def error(self):
        return self.missing+self.extra


def _valid_single(shape):
    return (not shape.IsNull() and solid_count(shape) == 1
            and BRepCheck_Analyzer(shape).IsValid() and volume(shape) > 0)


def _boolean_builder(constructor, first, second):
    # Configure before Build: two-argument constructors may execute immediately.
    arguments, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
    arguments.Append(first)
    tools.Append(second)
    builder = constructor()
    builder.SetNonDestructive(True)
    builder.SetArguments(arguments)
    builder.SetTools(tools)
    builder.Build()
    return builder


def _cut_volume(first, second):
    operation = _boolean_builder(BRepAlgoAPI_Cut, first, second)
    if not operation.IsDone():
        raise ValueError('Boolean measurement failed')
    return boolean_volume(operation.Shape())


def _difference(reference, candidate, reference_volume):
    missing = _cut_volume(reference, candidate)
    extra = _cut_volume(candidate, reference)
    candidate_volume = volume(candidate)
    if abs((candidate_volume-reference_volume)-(extra-missing)) > max(reference_volume, candidate_volume)*1e-6:
        raise ValueError('Boolean differences disagree with independent volume integration')
    return missing/reference_volume, extra/reference_volume


def _combine(shape, atom, operation):
    constructor = {'union':BRepAlgoAPI_Fuse, 'cut':BRepAlgoAPI_Cut, 'intersect':BRepAlgoAPI_Common}[operation]
    builder = _boolean_builder(constructor, shape, atom)
    if not builder.IsDone():
        raise ValueError('Boolean construction failed')
    builder.SimplifyResult(True, True)
    result = builder.Shape()
    if not _valid_single(result):
        raise ValueError('CSG intermediate is not one valid positive-volume solid')
    return result


def _axis_frame(origin, direction, x_axis=None):
    return gp_Ax2(gp_Pnt(*origin), gp_Dir(*direction)) if x_axis is None else gp_Ax2(gp_Pnt(*origin),gp_Dir(*direction),gp_Dir(*x_axis))


def _primitive_shape(kind, p):
    if kind == 'box':
        if 'origin' in p:
            frame = _axis_frame(p['origin'], p['axis'], p['x_axis'])
            maker = BRepPrimAPI_MakeBox(frame, p['width'], p['height'], p['depth'])
            maker.Build()
            return maker.Shape()
        center=p['bottom_face_center']
        return BRepPrimAPI_MakeBox(gp_Pnt(center[0]-p['width']/2,center[1]-p['height']/2,center[2]),
                                  p['width'],p['height'],p['depth']).Shape()
    if kind == 'cylinder':
        return BRepPrimAPI_MakeCylinder(_axis_frame(p['origin'],p['axis']),p['radius'],p['height']).Shape()
    if kind == 'cone':
        return BRepPrimAPI_MakeCone(_axis_frame(p['origin'],p['axis']),p['bottom_radius'],p['top_radius'],p['height']).Shape()
    if kind == 'sphere':
        return BRepPrimAPI_MakeSphere(gp_Pnt(*p['center']),p['radius']).Shape()
    if kind == 'torus':
        return BRepPrimAPI_MakeTorus(_axis_frame(p['origin'],p['axis'],p['radial_axis']),
                                   p['major_radius'],p['minor_radius'],-math.pi,math.pi,p['angle_radians']).Shape()
    raise ValueError(f'Unknown primitive {kind}')


def _signature(kind, params):
    def key(value):
        return tuple(key(v) for v in value) if isinstance(value,(list,tuple)) else round(float(value),9)
    return kind, tuple((name,key(value)) for name,value in sorted(params.items()))


def generate_atoms(solid, index, options, deadline, diagnostics):
    """Measure only analytic source surfaces and valid planar cap wires."""
    primitives, revolutions, prisms, seen = [], [], [], set()
    faces = [TopoDS.Face_s(f) for f in explore(solid,TopAbs_FACE)]
    planar = []
    errors = diagnostics['atom_errors']
    primitive_limit = max(1,options.max_hybrid_atoms//2)

    def add_primitive(kind, parameters, face_index, role):
        signature = _signature(kind,parameters)
        if signature in seen or len(primitives) >= primitive_limit:
            return
        seen.add(signature)
        shape = _primitive_shape(kind,parameters)
        if _valid_single(shape):
            primitives.append(Atom(kind,shape,parameters,{'source_face_index':face_index,
                'parameter_origin':'measured_analytic_surface_and_trim_bounds',
                'feature_role':'subtractive' if role == 'cut' else ('clip' if role == 'intersect' else 'additive')},role))

    # A measured world bounding box is an explicit inferred clipping/base operand.
    # It is never accepted without exact source differences and the final metrics.
    bounds=bbox(solid)
    widths=[bounds[i+3]-bounds[i] for i in range(3)]
    if min(widths)>options.length_tolerance:
        add_primitive('box',dict(width=widths[0],height=widths[1],depth=widths[2],
            bottom_face_center=[(bounds[0]+bounds[3])/2,(bounds[1]+bounds[4])/2,bounds[2]]),None,'intersect')
        if primitives:
            primitives[-1].evidence['parameter_origin']='derived_reference_world_bbox'

    for face_index, face in enumerate(faces,1):
        if time.monotonic() >= deadline:
            break
        try:
            surface = BRepAdaptor_Surface(face)
            kind = surface.GetType()
            role = 'cut' if face.Orientation() == TopAbs_REVERSED else 'union'
            if kind == GeomAbs_Plane:
                plane = surface.Plane()
                planar.append((area(face),face_index,face,plane))
                continue
            if kind == GeomAbs_Sphere:
                sphere = surface.Sphere()
                add_primitive('sphere',{'center':list(sphere.Location().Coord()),'radius':float(sphere.Radius())},face_index,role)
            elif kind in (GeomAbs_Cylinder,GeomAbs_Cone):
                first, last = surface.FirstVParameter(),surface.LastVParameter()
                if not math.isfinite(first+last) or last-first <= options.length_tolerance:
                    continue
                analytic = surface.Cylinder() if kind == GeomAbs_Cylinder else surface.Cone()
                origin = np.array(analytic.Location().Coord())
                axis = np.array(analytic.Axis().Direction().Coord())
                if kind == GeomAbs_Cylinder:
                    height = last-first
                    bottom = origin+first*axis
                    params = dict(origin=bottom.tolist(),axis=axis.tolist(),height=height,radius=float(analytic.Radius()))
                    primitive_kind = 'cylinder'
                else:
                    angle = analytic.SemiAngle()
                    height = (last-first)*math.cos(angle)
                    bottom = origin+first*math.cos(angle)*axis
                    r1, r2 = analytic.RefRadius()+first*math.sin(angle),analytic.RefRadius()+last*math.sin(angle)
                    if min(r1,r2) < -options.length_tolerance or height <= options.length_tolerance:
                        continue
                    params = dict(origin=bottom.tolist(),axis=axis.tolist(),height=height,
                                  bottom_radius=max(0.0,r1),top_radius=max(0.0,r2))
                    primitive_kind = 'cone'
                    add_primitive(primitive_kind,params,face_index,role)
                    # A trimmed conical face often exposes only the final shell's
                    # short V interval. Also offer the same analytic cone over the
                    # source solid's axis projection. This covers constructions
                    # such as a polygonal blank intersected with a cone whose
                    # useful extent is larger than the visible cone face.
                    try:
                        corners = np.array([[x,y,z] for x in (bounds[0],bounds[3])
                                                   for y in (bounds[1],bounds[4])
                                                   for z in (bounds[2],bounds[5])], dtype=float)
                        location = np.array(analytic.Location().Coord(), dtype=float)
                        axis_unit = np.array(analytic.Axis().Direction().Coord(), dtype=float)
                        projections = corners @ axis_unit
                        extended_first, extended_last = float(projections.min()), float(projections.max())
                        base_projection = float(bottom @ axis_unit)
                        if extended_last-extended_first > height+options.length_tolerance:
                            slope = (r2-r1)/max(height, options.length_tolerance)
                            er1 = r1 + (extended_first-base_projection)*slope
                            er2 = r1 + (extended_last-base_projection)*slope
                            if min(er1,er2) >= -options.length_tolerance:
                                extended_origin = location + (extended_first-float(location @ axis_unit))*axis_unit
                                extended = dict(origin=extended_origin.tolist(),
                                                axis=axis_unit.tolist(),
                                                height=extended_last-extended_first,
                                                bottom_radius=max(0.0,er1),
                                                top_radius=max(0.0,er2))
                                add_primitive('cone',extended,face_index,role)
                    except Exception as exc:
                        if len(errors)<24:
                            errors.append({'face_index':face_index,
                                           'error':f'extended_cone:{type(exc).__name__}: {exc}'})
                    continue
                # Normalize axis sign only for deduplication and readable parameters.
                leading = next((v for v in axis if abs(v)>1e-12),1)
                if leading < 0:
                    params['origin'] = (np.array(params['origin'])+params['height']*axis).tolist()
                    params['axis'] = (-axis).tolist()
                    if primitive_kind == 'cone':
                        params['bottom_radius'],params['top_radius'] = params['top_radius'],params['bottom_radius']
                add_primitive(primitive_kind,params,face_index,role)
            elif kind == GeomAbs_Torus:
                torus = surface.Torus()
                if torus.MajorRadius() <= torus.MinorRadius():
                    continue
                first, last = surface.FirstUParameter(),surface.LastUParameter()
                sweep = min(2*math.pi,last-first)
                if not math.isfinite(first+last) or sweep <= 1e-10:
                    continue
                radial = (math.cos(first)*np.array(torus.XAxis().Direction().Coord())
                          +math.sin(first)*np.array(torus.YAxis().Direction().Coord()))
                params = dict(origin=list(torus.Location().Coord()),axis=list(torus.Axis().Direction().Coord()),
                              radial_axis=radial.tolist(),major_radius=float(torus.MajorRadius()),
                              minor_radius=float(torus.MinorRadius()),angle_radians=sweep)
                add_primitive('torus',params,face_index,role)
                if sweep < 2*math.pi-1e-9:
                    add_primitive('torus',dict(params,angle_radians=2*math.pi),face_index,role)
        except Exception as exc:
            if len(errors)<24:
                errors.append({'face_index':face_index,'error':f'{type(exc).__name__}: {exc}'})

    # Recover bounded solids from true OCC SurfaceOfRevolution faces before
    # generic atom search.  The native shape and the emitted spline profile
    # share the same measured basis curve; no mesh approximation is used.
    try:
        from step2code_direct.revolution import generate_revolution_atoms
        recovered, revolution_errors = generate_revolution_atoms(
            solid, index, options, deadline, Atom)
        revolutions.extend(recovered[:options.max_revolution_atoms])
        errors.extend(revolution_errors)
    except Exception as exc:
        if len(errors) < 24:
            errors.append({'stage':'revolution_recovery',
                           'error':f'{type(exc).__name__}: {exc}'})

    # A planar source face can define a finite, editable half-space clip.
    # The broad oriented box is only a search operand; exact OCC differences
    # and the final CD/IoU/V checks still authorize every accepted program.
    if len(primitives) < primitive_limit:
        bounds = np.array(bbox(solid), dtype=float)
        center = (bounds[:3] + bounds[3:]) / 2.0
        diagonal = float(np.linalg.norm(bounds[3:] - bounds[:3]))
        seen_planes = set()
        for face_area, face_index, face, plane in planar:
            if len(primitives) >= primitive_limit or len(seen_planes) >= 12:
                break
            normal = np.array(plane.Axis().Direction().Coord(), dtype=float)
            normal /= np.linalg.norm(normal)
            position = float(np.dot(np.array(plane.Location().Coord()), normal))
            key = (tuple(np.round(normal, 9)), round(position, 7))
            if key in seen_planes:
                continue
            seen_planes.add(key)
            if float(np.dot(center - np.array(plane.Location().Coord()), normal)) < 0:
                normal = -normal
            seed = np.eye(3)[int(np.argmin(np.abs(normal)))]
            x_axis = seed - normal * float(seed @ normal)
            x_axis /= np.linalg.norm(x_axis)
            y_axis = np.cross(normal, x_axis)
            origin = np.array(plane.Location().Coord()) - diagonal * (x_axis + y_axis)
            add_primitive('box', dict(width=2.0 * diagonal, height=2.0 * diagonal,
                                      depth=2.0 * diagonal, origin=origin.tolist(),
                                      axis=normal.tolist(), x_axis=x_axis.tolist(),
                                      y_axis=y_axis.tolist()), face_index, 'intersect')
            if primitives:
                primitives[-1].evidence['parameter_origin'] = 'measured_planar_halfspace_clip'

    planar.sort(key=lambda item:(-item[0],tuple(item[3].Location().Coord()),tuple(item[3].Axis().Direction().Coord()),item[1]))
    prism_limit = options.max_hybrid_atoms-len(primitives)
    for _,face_index,source_face,plane in planar[:options.max_candidates]:
        if len(prisms) >= prism_limit or time.monotonic() >= deadline:
            break
        normal = np.array(plane.Axis().Direction().Coord())
        direction = normal if source_face.Orientation() == TopAbs_REVERSED else -normal
        x_axis = np.array(plane.XAxis().Direction().Coord())
        y_axis = np.cross(direction,x_axis)
        origin = np.array(plane.Location().Coord())
        separations = sorted({float((np.array(other.Location().Coord())-origin) @ direction)
            for _,_,_,other in planar
            if abs(abs(float(np.array(other.Axis().Direction().Coord()) @ direction))-1)<1e-10
            and float((np.array(other.Location().Coord())-origin) @ direction)>options.length_tolerance})
        # Prefer full extent, then nearer parallel cap levels. This is bounded.
        distances = list(dict.fromkeys(([separations[-1]]+separations[:3]) if separations else []))
        variants = [(source_face,False)]
        try:
            if len(face_wires(source_face)) > 1:
                filled = BRepBuilderAPI_MakeFace(plane,face_wires(source_face)[0],True).Face()
                filled = TopoDS.Face_s(filled.Oriented(source_face.Orientation()))
                variants.append((filled,True))
        except Exception:
            pass
        for face,filled_holes in variants:
            if len(prisms) >= prism_limit or time.monotonic() >= deadline:
                break
            try:
                check_profile(face,options.length_tolerance)
                first = BRepAdaptor_Curve(wire_edges(face_wires(face)[0],face)[0])
                anchor = first.Circle().Location() if first.GetType() == GeomAbs_Circle else first.Value(first.FirstParameter())
            except Exception:
                continue
            for distance in distances:
                if len(prisms) >= prism_limit or time.monotonic() >= deadline:
                    break
                try:
                    builder = BRepPrimAPI_MakePrism(face,gp_Vec(*(direction*distance).tolist()))
                    if not builder.IsDone():
                        continue
                    shape = TopoDS.Solid_s(builder.Shape())
                    if not BRepLib.OrientClosedSolid_s(shape) or not _valid_single(shape):
                        continue
                    evidence = dict(source_face_index=face_index,source_solid_index=index,
                                    distance_origin_kind='measured',distance_method='parallel_cap_separation',
                                    filled_holes=filled_holes,method='cap_extrusion_operand')
                    role = 'cut' if str(source_face.Orientation()).endswith('REVERSED') else 'union'
                    evidence['feature_role'] = 'subtractive' if role == 'cut' else 'additive'
                    native = NativePrism(face,list(anchor.Coord()),x_axis.tolist(),y_axis.tolist(),distance,evidence)
                    key = ('prism',face_index,round(distance,12),filled_holes)
                    if key in seen:
                        continue
                    seen.add(key)
                    prisms.append(Atom('extrusion',shape,{},evidence,role,native))
                except Exception as exc:
                    if len(errors)<24:
                        errors.append({'face_index':face_index,'error':f'{type(exc).__name__}: {exc}'})
    # Keep the more structural operands ahead of clipping boxes and noisy
    # extrusion variants.  The search can still use every retained atom.
    atoms = primitives + revolutions + prisms
    diagnostics.update(primitive_atom_count=len(primitives),
                       revolution_atom_count=len(revolutions),
                       extrusion_atom_count=len(prisms),
                       atom_count=len(atoms))
    return atoms[:options.max_hybrid_atoms]


def _recognize_legacy(solid,index,options,on_progress=None):
    started = time.monotonic()
    deadline = started+options.hybrid_timeout_seconds
    diagnostics = dict(source_solid_index=index,method='native_hybrid_csg_v1',atom_errors=[],
                       max_boolean_steps=options.max_boolean_steps,max_boolean_trials=options.max_boolean_trials,
                       time_budget_seconds=options.hybrid_timeout_seconds,boolean_trials=0,valid_trials=0,
                       best_symmetric_difference_relative=None)
    source_volume = volume(solid)
    tolerance = options.reconstruction_relative_tolerance
    atoms = generate_atoms(solid,index,options,deadline,diagnostics)
    diagnostics['atom_count'] = len(atoms)
    queue,serial,states = [],0,[]

    def progress():
        diagnostics['elapsed_seconds'] = time.monotonic()-started
        if on_progress:
            on_progress(dict(diagnostics))

    def accepted(state):
        diagnostics.update(status='recognized',stop_reason='verified_geometry',
            best_symmetric_difference_relative=state.error,selected_base_atom=state.base,
            selected_steps=[{'operation':op,'atom_index':n,'kind':atoms[n].kind} for op,n in state.steps],
            selected_operands=[{'atom_index':n,'kind':atoms[n].kind,'source':atoms[n].evidence}
                               for n in dict.fromkeys([state.base]+[n for _,n in state.steps])])
        progress()
        return HybridResult(atoms[state.base],tuple((op,atoms[n]) for op,n in state.steps),state.shape,dict(diagnostics))

    for atom_index,atom in enumerate(atoms):
        if time.monotonic() >= deadline:
            break
        try:
            missing,extra = _difference(solid,atom.shape,source_volume)
            state = State(atom.shape,atom_index,(),missing,extra)
            if state.error <= tolerance:
                return accepted(state)
            if missing < 1-tolerance:
                states.append(state)
        except Exception as exc:
            if len(diagnostics['atom_errors'])<24:
                diagnostics['atom_errors'].append({'atom_index':atom_index,'error':f'{type(exc).__name__}: {exc}'})
    states.sort(key=lambda s:(s.error,s.base))
    seeds=list(states[:options.hybrid_beam_width])
    # A hollow cone may initially be much worse than a small correct cylinder.
    # Keep analytic exterior seeds as alternatives before greedy pruning.
    exterior=sorted((state for state in states if atoms[state.base].kind != 'box'
                     and atoms[state.base].preferred_operation == 'union'
                     and atoms[state.base].kind != 'extrusion'), key=lambda s:(s.missing,s.error,s.base))
    for state in exterior[:options.hybrid_beam_width]:
        if not any(state is existing for existing in seeds):
            seeds.append(state)
    for state in seeds:
        heapq.heappush(queue,(state.error,serial,state)); serial+=1
    if states:
        diagnostics['best_symmetric_difference_relative'] = states[0].error
    progress()
    signatures = set()
    while queue and diagnostics['boolean_trials'] < options.max_boolean_trials and time.monotonic() < deadline:
        _,_,state = heapq.heappop(queue)
        if len(state.steps) >= options.max_boolean_steps:
            continue
        proposals=[]
        for n,atom in enumerate(atoms):
            if n == state.base:
                continue
            operations=[]
            if state.extra > tolerance:
                operations.extend(['cut','intersect'])
            if state.missing > tolerance:
                operations.append('union')
            for operation in operations:
                if (operation,n) in state.steps:
                    continue
                # Prefer source surface polarity; retain alternatives for cavities/clipping.
                priority = 0 if operation == atom.preferred_operation else 1
                proposals.append((priority,n,operation))
        proposals.sort()
        children=[]
        for _,n,operation in proposals:
            if diagnostics['boolean_trials'] >= options.max_boolean_trials or time.monotonic() >= deadline:
                break
            signature = (state.base,state.steps+((operation,n),))
            if signature in signatures:
                continue
            signatures.add(signature)
            diagnostics['boolean_trials']+=1
            try:
                shape = _combine(state.shape,atoms[n].shape,operation)
                missing,extra = _difference(solid,shape,source_volume)
                next_state = State(shape,state.base,state.steps+((operation,n),),missing,extra)
                diagnostics['valid_trials']+=1
                if next_state.error <= tolerance:
                    return accepted(next_state)
                # A bounded monotone search; temporary regressions are deliberately unsupported.
                if next_state.error < state.error-max(tolerance*0.01,1e-12):
                    children.append(next_state)
                    best = diagnostics['best_symmetric_difference_relative']
                    if best is None or next_state.error < best:
                        diagnostics['best_symmetric_difference_relative'] = next_state.error
            except Exception:
                continue
        children.sort(key=lambda s:(s.error,len(s.steps),s.steps))
        for child in children[:options.hybrid_beam_width]:
            heapq.heappush(queue,(child.error,serial,child)); serial+=1
        # Keep only the best few pending alternatives to bound memory and search breadth.
        initial_seeds=[item for item in queue if not item[2].steps]
        descendants=[item for item in queue if item[2].steps]
        queue = initial_seeds+heapq.nsmallest(options.hybrid_beam_width,descendants)
        heapq.heapify(queue)
        progress()
    reason = ('time_budget' if time.monotonic() >= deadline else
              'boolean_trial_budget' if diagnostics['boolean_trials'] >= options.max_boolean_trials else
              'no_monotone_solution_within_depth_and_operands')
    diagnostics.update(status='not_recognized',stop_reason=reason)
    progress()
    raise UnsupportedGeometry(f'Solid {index}: bounded hybrid reconstruction found no verified solution',diagnostics)


def recognize_hybrid(solid, index, options, on_progress=None, seed_prisms=()):
    if options.hybrid_search == 'legacy':
        return _recognize_legacy(solid, index, options, on_progress)
    from step2code_direct.hybrid_search import recognize_guided
    return recognize_guided(solid, index, options, on_progress, seed_prisms=seed_prisms)
