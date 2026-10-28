"""Verified meridian-section recognition for full axisymmetric revolutions."""
from __future__ import annotations

import math
import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
from OCP.GeomAbs import (GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere,
                         GeomAbs_Torus, GeomAbs_SurfaceOfRevolution, GeomAbs_Plane)
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_SOLID
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt

from step2code.verification.metrics import bbox, volume, symmetric_difference_volume
from step2code.plan.model import node as plan_node
from .context import call,capture_sources


def _axes(solid, tolerance):
    from step2code.recognition import explore
    axes = []
    for index, item in enumerate(explore(solid, TopAbs_FACE), 1):
        surface = BRepAdaptor_Surface(TopoDS.Face_s(item))
        kind = surface.GetType()
        if kind == GeomAbs_Cylinder: axis = surface.Cylinder().Axis()
        elif kind == GeomAbs_Cone: axis = surface.Cone().Axis()
        elif kind == GeomAbs_Torus: axis = surface.Torus().Axis()
        elif kind == GeomAbs_Sphere: axis = surface.Sphere().Position().Axis()
        elif kind == GeomAbs_SurfaceOfRevolution: axis = surface.AxeOfRevolution()
        else: continue
        direction = np.array(axis.Direction().Coord())
        # Deterministic sign and closest point on the line to the world origin.
        pivot = int(np.argmax(np.abs(direction)))
        if direction[pivot] < 0: direction = -direction
        location = np.array(axis.Location().Coord())
        origin = location - direction * float(location @ direction)
        if any(np.linalg.norm(np.cross(direction, d)) < 1e-10 and
               np.linalg.norm(origin-o) < tolerance for o,d,_ in axes): continue
        axes.append((origin, direction, index))
    return axes


def _check_axis_compatibility(solid, origin, axis, tolerance):
    """Necessary boundary conditions before expensive section/rotation booleans."""
    from step2code.recognition import explore
    for index,item in enumerate(explore(solid,TopAbs_FACE),1):
        surface=BRepAdaptor_Surface(TopoDS.Face_s(item));kind=surface.GetType()
        if kind==GeomAbs_Plane:
            normal=np.array(surface.Plane().Axis().Direction().Coord())
            if np.linalg.norm(np.cross(normal,axis))>1e-6:
                raise ValueError(f'Plane face {index} is not perpendicular to the full-revolution axis')
        elif kind in (GeomAbs_Cylinder,GeomAbs_Cone,GeomAbs_Torus):
            geometry=(surface.Cylinder() if kind==GeomAbs_Cylinder else
                      surface.Cone() if kind==GeomAbs_Cone else surface.Torus())
            other_axis=geometry.Axis()
            direction=np.array(other_axis.Direction().Coord())
            location=np.array(other_axis.Location().Coord())
            if (np.linalg.norm(np.cross(direction,axis))>1e-6 or
                np.linalg.norm(np.cross(location-origin,axis))>tolerance):
                raise ValueError(f'Analytic face {index} is not coaxial with the candidate axis')
        elif kind==GeomAbs_Sphere:
            center=np.array(surface.Sphere().Location().Coord())
            if np.linalg.norm(np.cross(center-origin,axis))>tolerance:
                raise ValueError(f'Sphere face {index} center is off the candidate axis')


def recognize_revolution(solid, number, options):
    from step2code.recognition.prism import explore, _profile, UnsupportedGeometry
    source_volume = volume(solid)
    if not math.isfinite(source_volume) or source_volume <= 0:
        raise UnsupportedGeometry('Nonpositive source solid volume for revolution')
    bounds = np.array(bbox(solid)); scale = float(np.linalg.norm(bounds[3:]-bounds[:3]))
    corners = np.array([[x,y,z] for x in (bounds[0],bounds[3])
                         for y in (bounds[1],bounds[4]) for z in (bounds[2],bounds[5])])
    attempts=[]
    axes = _axes(solid, options.length_tolerance)
    for origin, axis, source_face_index in axes[:options.max_revolve_candidates]:
        record=dict(axis_source_face_index=source_face_index, origin_mm=origin.tolist(), axis=axis.tolist())
        try:
            _check_axis_compatibility(solid,origin,axis,max(options.length_tolerance*10,scale*1e-7))
            seed=np.eye(3)[int(np.argmin(np.abs(axis)))]
            radial=seed-axis*float(seed@axis);radial/=np.linalg.norm(radial)
            normal=np.cross(radial,axis)
            axial=(corners-origin)@axis
            reach=max(np.linalg.norm(corners-origin,axis=1))+scale
            polygon=BRepBuilderAPI_MakePolygon()
            for radius,height in ((0,min(axial)-scale),(reach,min(axial)-scale),
                                  (reach,max(axial)+scale),(0,max(axial)+scale)):
                polygon.Add(gp_Pnt(*(origin+radial*radius+axis*height)))
            polygon.Close()
            section_plane=BRepBuilderAPI_MakeFace(polygon.Wire()).Face()
            common=call('meridian_section',BRepAlgoAPI_Common,solid,section_plane);common.Build()
            if not common.IsDone() or not BRepCheck_Analyzer(common.Shape()).IsValid():
                raise ValueError('Meridian section boolean failed')
            faces=[TopoDS.Face_s(face) for face in explore(common.Shape(),TopAbs_FACE)]
            if not faces or len(faces)>options.max_section_faces:
                raise ValueError(f'Meridian section has unsupported face count: {len(faces)}')
            frame=dict(origin=origin.tolist(),x_axis=radial.tolist(),y_axis=axis.tolist(),z_axis=normal.tolist())
            nodes={};frames=[];profiles=[];parts=[]
            for section_index, face in enumerate(faces,1):
                edges=[]
                for edge in explore(face,TopAbs_EDGE):
                    if not any(edge.IsSame(e) for e in edges):edges.append(edge)
                if len(edges)>options.max_section_edges:
                    raise ValueError('Meridian section edge limit exceeded')
                prefix=f'solid_{number:02d}_section_{section_index:02d}'
                source_id=f'solid_{number:02d}.section_{section_index:02d}.inferred_revolution'
                local, frame_id, profile_id=_profile(face,frame,prefix,source_id,options.length_tolerance,edges,options.max_spline_poles)
                # Section edges are derived geometry, not original STEP edge ids.
                for node in local.values():
                    if 'source' in node:
                        source=node['source'];source['derived_section']=True
                        if 'source_edge_index' in source:
                            source['section_edge_index']=source.pop('source_edge_index')
                nodes.update(local);frames.append(frame_id);profiles.append(profile_id)
                part=call('make_revolve',BRepPrimAPI_MakeRevol,face,gp_Ax1(gp_Pnt(*origin),gp_Dir(*axis)),2*math.pi,True).Shape()
                if part.IsNull() or not BRepCheck_Analyzer(part).IsValid():raise ValueError('Invalid revolved section')
                parts.append(part)
            candidate=parts[0]
            for part in parts[1:]:
                fuse=BRepAlgoAPI_Fuse(candidate,part);fuse.Build()
                if not fuse.IsDone():raise ValueError('Revolved section union failed')
                candidate=fuse.Shape()
            if not BRepCheck_Analyzer(candidate).IsValid() or len(list(explore(candidate,TopAbs_SOLID)))!=1:
                raise ValueError('Revolution did not reconstruct one valid solid')
            record['bbox_error_mm'] = float(np.max(np.abs(np.array(bbox(candidate))-bounds)))
            # Cheap necessary conditions avoid expensive Cuts of clearly wrong
            # rotations. They never replace the final source-difference proof.
            record['coarse_bbox_rejection_tolerance_mm']=max(options.length_tolerance*100,scale*1e-3)
            if record['bbox_error_mm']>record['coarse_bbox_rejection_tolerance_mm']:
                raise ValueError('Revolved section fails coarse source bounding-box check')
            candidate_volume=volume(candidate)
            if not math.isfinite(candidate_volume) or candidate_volume<=0:
                raise ValueError('Revolved section has nonpositive or nonfinite volume')
            record['coarse_volume_relative_error']=abs(candidate_volume-source_volume)/source_volume
            if record['coarse_volume_relative_error']>1e-3:
                raise ValueError('Revolved section volume differs from source by more than 0.001')
            difference=call('source_difference',symmetric_difference_volume,solid,candidate)/source_volume
            if not math.isfinite(difference) or difference>options.reconstruction_relative_tolerance:
                raise ValueError(f'Revolved section differs from source: {difference}')
            feature_id=f'solid_{number:02d}_revolve'
            roots=[]
            for i,(profile_id,frame_id) in enumerate(zip(profiles,frames),1):
                # Local revolve +Z corresponds to measured meridian +v.
                nodes[frame_id]=plan_node('frame.rigid3',origin=origin.tolist(),
                    x_axis=radial.tolist(),y_axis=np.cross(axis,radial).tolist())
                local_id=f'{feature_id}_section_{i:02d}_local'
                world_id=f'{feature_id}_section_{i:02d}_world'
                nodes[local_id]=plan_node('solid.revolve',dict(profile=[profile_id]),angle=2*math.pi)
                nodes[world_id]=plan_node('shape.transform',dict(shape=[local_id],frame=[frame_id]))
                roots.append(world_id)
            if len(roots)>1:
                nodes[feature_id]=plan_node('boolean.union',dict(operands=roots))
            else:
                nodes[feature_id]=nodes.pop(roots[0])
            record.update(status='recognized',symmetric_difference_relative=difference,
                          section_face_count=len(faces),section_profile_ids=profiles)
            attempts.append(record)
            source_edges=[]
            source_faces=list(explore(solid,TopAbs_FACE))
            for edge in explore(solid,TopAbs_EDGE):
                if not any(edge.IsSame(e) for e in source_edges):source_edges.append(edge)
            evidence=dict(solid_index=number,method='verified_meridian_revolution',node_sources=capture_sources(nodes),selected=record,attempts=attempts,
                candidate_selection_rule='unique analytic surface axes in imported face order; deterministic axis sign',
                history_recovered=False,coordinate_frame=dict(selected_frame=frame,
                    application='meridian coordinates transformed once; revolve axis in world frame',adjustments=[]),
                profile_nodes=[key for key,n in nodes.items() if n['op'].startswith('curve.')],
                source_face_indices=list(range(1,len(source_faces)+1)),
                source_topology=dict(face_count=len(source_faces),edge_count=len(source_edges),
                    faces=[dict(face_index=i,edge_indices=sorted({
                        j for edge in explore(face,TopAbs_EDGE)
                        for j,original in enumerate(source_edges,1) if edge.IsSame(original)}))
                        for i,face in enumerate(source_faces,1)]),
                axis_candidate_count=len(axes),axis_candidate_limit=options.max_revolve_candidates,
                approximation='line/circle/arc or bounded kernel B-spline replay; no converter fitting; kernel section accuracy checked against source',
                spline_curve_count=sum(n['op']=='curve.bspline2' for n in nodes.values()))
            return nodes,[feature_id],evidence
        except Exception as exc:
            record.update(status='not_recognized',reason=f'{type(exc).__name__}: {exc}');attempts.append(record)
    raise UnsupportedGeometry(f'Solid {number} has no verified supported full revolution',
        dict(solid_index=number,method='verified_meridian_revolution',axis_candidate_count=len(axes),
             axis_candidate_limit=options.max_revolve_candidates,attempts=attempts))
