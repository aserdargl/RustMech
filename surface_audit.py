"""Tolerance-based triangle checks and explicitly sampled normal chord lengths.

No Boolean repair or coordinate welding is performed. A bounded intersection
search never reports a clean surface if its candidate budget is exhausted.
Normal chords are measurements at face centres, not a global thickness proof.
"""
from __future__ import annotations

import math
import numpy as np
import vtk


def _cross2(a, b):
    return float(a[0]*b[1]-a[1]*b[0])


def _inside2(point, triangle, tolerance):
    sides = [_cross2(triangle[(i+1)%3]-triangle[i], point-triangle[i]) /
             max(np.linalg.norm(triangle[(i+1)%3]-triangle[i]),np.finfo(float).tiny) for i in range(3)]
    return min(sides) >= -tolerance or max(sides) <= tolerance


def _improper_intersection(first, second, common, tolerance, normal=None, other_normal=None):
    """Allow only the vertex/edge that both triangles actually share."""
    if not len(common):
        return bool(vtk.vtkTriangle.TrianglesIntersect(*first,*second))
    if len(common)==3:
        return True
    if normal is None:
        normal = np.cross(first[1]-first[0], first[2]-first[0])
        normal /= np.linalg.norm(normal)
    if other_normal is None:
        other_normal = np.cross(second[1]-second[0],second[2]-second[0])
        other_normal /= np.linalg.norm(other_normal)
    if len(common)==1:
        # A triangle wholly on one side of the other plane can meet it only
        # at their shared vertex. This also avoids ill-conditioned line
        # constructions for nearly coplanar adjacent faces.
        for triangle,plane in ((first,other_normal),(second,normal)):
            distances = (triangle-common[0]) @ plane
            nonzero = distances[np.abs(distances)>np.finfo(float).eps*32]
            if len(nonzero)==2 and nonzero[0]*nonzero[1]>0:
                return False
    coplanar = np.max(np.abs((second-first[0]) @ normal)) <= tolerance
    if coplanar:
        axis = int(np.argmax(np.abs(normal)))
        a, b = np.delete(first, axis, axis=1), np.delete(second, axis, axis=1)
        if len(common) == 2:
            edge = np.delete(common, axis, axis=1)
            extra_a = next((p for p in a if not any(np.linalg.norm(p-q) <= tolerance for q in edge)),None)
            extra_b = next((p for p in b if not any(np.linalg.norm(p-q) <= tolerance for q in edge)),None)
            if extra_a is None or extra_b is None:
                return True
            return _cross2(edge[1]-edge[0],extra_a-edge[0])*_cross2(edge[1]-edge[0],extra_b-edge[0]) > 0
        shared = np.delete(common[0], axis)
        for points, other in ((a,b),(b,a)):
            for point in points:
                if np.linalg.norm(point-shared)>tolerance and _inside2(point,other,tolerance):
                    return True
        # An overlap with crossing edges may contain no original vertices.
        for i in range(3):
            for j in range(3):
                p, r, q, s = a[i], a[(i+1)%3]-a[i], b[j], b[(j+1)%3]-b[j]
                determinant = _cross2(r,s)
                if abs(determinant)>tolerance*np.linalg.norm(r)*np.linalg.norm(s):
                    t,u = _cross2(q-p,s)/determinant,_cross2(q-p,r)/determinant
                    if 0 <= t <= 1 and 0 <= u <= 1 and np.linalg.norm(p+t*r-shared)>tolerance:
                        return True
        return False
    if len(common) == 2:
        return False
    def endpoint(triangle,plane):
        vertices = np.asarray([p for p in triangle if np.linalg.norm(p-common[0])>0])
        distances = (vertices-common[0]) @ plane
        for p,d in zip(vertices,distances):
            if abs(d)<=np.finfo(float).eps*32:
                return p-common[0]
        if distances[0]*distances[1]<0:
            return vertices[0]+distances[0]/(distances[0]-distances[1])*(vertices[1]-vertices[0])-common[0]
        return None
    a,b = endpoint(first,other_normal),endpoint(second,normal)
    return bool(a is not None and b is not None and min(np.linalg.norm(a),np.linalg.norm(b))>tolerance and np.dot(a,b)>0)


def intersection_report(surface, *, max_pairs=None, marker_limit=64, tolerance=1e-10, progress=None):
    """AABB broad phase, VTK predicates, and shared-simplex classification.

    Coordinates are normalized before testing. Results use floating-point
    tolerance, not an exact-arithmetic certificate. Triangle IDs are unchanged.
    """
    if max_pairs is not None and (type(max_pairs) is not int or max_pairs<0):
        raise ValueError("Intersection candidate budget must be nonnegative or unlimited.")
    if not math.isfinite(tolerance) or tolerance<=0:
        raise ValueError("Use a positive geometric tolerance.")
    mesh = surface.triangulate().copy(deep=True)
    points = np.asarray(mesh.points,dtype=np.float64)
    if not mesh.n_cells or not np.isfinite(points).all():
        raise ValueError("Surface must have finite coordinates and triangles.")
    origin = points.min(axis=0)
    scale = float(np.linalg.norm(np.ptp(points,axis=0)))
    if scale <= 0:
        raise ValueError("Surface has no extent.")
    mesh.points = (points-origin)/scale
    faces = mesh.faces.reshape(-1,4)[:,1:]
    triangles = mesh.points[faces]
    normals = np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0])
    areas = np.linalg.norm(normals,axis=1)
    normals /= np.maximum(areas[:,None],np.finfo(float).tiny)
    degenerate = np.flatnonzero(areas <= tolerance*tolerance)
    locator = vtk.vtkStaticCellLocator()
    locator.SetDataSet(mesh)
    locator.BuildLocator()
    candidates = vtk.vtkIdList()
    complete, tested, count, pairs, affected = True, 0, 0, [], set(map(int,degenerate))
    bounds_low, bounds_high = triangles.min(axis=1)-tolerance, triangles.max(axis=1)+tolerance
    for i, triangle in enumerate(triangles):
        if progress is not None and i%5000 == 0:
            progress(f"Checking surface intersections: {i:,}/{len(triangles):,} triangles, {tested:,} candidate pairs…")
        if areas[i] <= tolerance*tolerance:
            continue
        low, high = bounds_low[i],bounds_high[i]
        locator.FindCellsWithinBounds([low[0],high[0],low[1],high[1],low[2],high[2]], candidates)
        ids = np.asarray([candidates.GetId(k) for k in range(candidates.GetNumberOfIds())],dtype=np.int64)
        mask = (ids>i) & (areas[ids]>tolerance*tolerance)
        mask &= np.all(bounds_high[ids]>=bounds_low[i],axis=1) & np.all(bounds_low[ids]<=bounds_high[i],axis=1)
        for j in ids[mask]:
            j = int(j)
            if max_pairs is not None and tested >= max_pairs:
                complete = False
                break
            tested += 1
            shared_ids = faces[i][np.any(faces[i][:,None]==faces[j][None,:],axis=1)]
            if _improper_intersection(triangle,triangles[j],mesh.points[shared_ids],tolerance,normals[i],normals[j]):
                count += 1
                affected.update((i,j))
                if len(pairs)<marker_limit:
                    pairs.append({"triangles":[i,j],"centre_mm":((points[faces[i]].mean(axis=0)+points[faces[j]].mean(axis=0))/2).tolist()})
        if not complete:
            break
    status = "invalid" if count or len(degenerate) else "clear" if complete else "incomplete"
    return {"status":status,"complete":complete,"candidate_pairs_tested":tested,
            "intersecting_pairs":count,"degenerate_triangles":len(degenerate),
            "affected_triangle_ids":sorted(affected),"markers":pairs,
            "tolerance_mm":scale*tolerance,"predicate":"VTK triangle predicates; normalized floating-point coordinates"}


def normal_chords(surface, cell_mm, *, max_samples=2048, required_cells=3, progress=None):
    """Measure first inward wall and outward gap hits at selected face centres."""
    if not math.isfinite(cell_mm) or cell_mm <= 0 or type(max_samples) is not int or max_samples<1:
        raise ValueError("Use a positive mesh size and surface sample count.")
    mesh = surface.triangulate().compute_normals(cell_normals=True,point_normals=False,
                                                consistent_normals=True,auto_orient_normals=True,split_vertices=False)
    points = np.asarray(mesh.points,dtype=float)
    if not mesh.n_cells or not np.isfinite(points).all():
        raise ValueError("Surface must have finite coordinates and triangles.")
    scale = float(np.linalg.norm(np.ptp(points,axis=0)))
    centres = mesh.cell_centers().points
    if scale <= 0 or mesh.n_open_edges or not mesh.is_manifold:
        raise ValueError("Normal chord measurements require a closed, manifold surface.")
    ids = np.unique(np.linspace(0,mesh.n_cells-1,min(max_samples,mesh.n_cells),dtype=int))
    tree = vtk.vtkOBBTree()
    tree.SetDataSet(mesh)
    tree.SetTolerance(max(scale*1e-12,np.finfo(float).eps))
    tree.BuildLocator()
    epsilon = max(scale*1e-9,np.finfo(float).eps*max(1,float(np.abs(points).max()))*32)
    normals = mesh.cell_data["Normals"]
    values = {"wall":[],"pore":[]}
    markers = []
    for sample,index in enumerate(ids):
        if progress is not None and sample%128 == 0:
            progress(f"Measuring sampled wall/pore chords: {sample}/{len(ids)} faces…")
        centre, normal = centres[index], np.asarray(normals[index],dtype=float)
        normal /= np.linalg.norm(normal)
        for kind, direction in (("wall",-normal),("pore",normal)):
            hits, cells = vtk.vtkPoints(),vtk.vtkIdList()
            hits.SetDataTypeToDouble()
            tree.IntersectWithLine(centre+direction*epsilon,centre+direction*(2*scale),hits,cells)
            distances = [float(np.dot(np.asarray(hits.GetPoint(k))-centre,direction)) for k in range(hits.GetNumberOfPoints())]
            positive = [distance for distance in distances if distance>epsilon*2]
            if positive:
                distance = min(positive)
                values[kind].append((int(index),distance))
                if distance/cell_mm < required_cells and len(markers)<64:
                    markers.append({"kind":kind,"triangle":int(index),"length_mm":distance,
                                    "cells_across":distance/cell_mm,"centre_mm":centre.tolist()})
    report = {"method":"face-centre normal chords; sampled minimum, not global minimum thickness",
              "sampled_faces":len(ids),"total_faces":mesh.n_cells,"sample_fraction":len(ids)/mesh.n_cells,
              "reference_cell_mm":cell_mm,"required_cells":required_cells,"markers":markers}
    for kind, measurements in values.items():
        minimum = min((value for _,value in measurements),default=None)
        report[kind+"_hits"] = len(measurements)
        report[kind+"_minimum_sampled_mm"] = minimum
        report[kind+"_minimum_cells"] = minimum/cell_mm if minimum is not None else None
    return report,values


def audit_surface(surface, cell_mm, *, max_pairs=2_000_000, max_samples=2048, progress=None):
    intersection = intersection_report(surface,max_pairs=max_pairs,progress=progress)
    annotated = surface.triangulate().copy(deep=True)
    intersection["open_edges"] = annotated.n_open_edges
    intersection["manifold"] = bool(annotated.is_manifold)
    if annotated.n_open_edges or not annotated.is_manifold:
        intersection["status"] = "invalid"
    annotated.cell_data["SurfaceIssue"] = np.isin(np.arange(annotated.n_cells),intersection["affected_triangle_ids"]).astype(np.uint8)
    report = {"intersection":intersection,"chords":None}
    if intersection["status"]=="clear":
        chords, values = normal_chords(annotated,cell_mm,max_samples=max_samples,progress=progress)
        report["chords"] = chords
        for kind, measurements in values.items():
            array = np.full(annotated.n_cells,np.nan)
            for index,value in measurements:
                array[index] = value
            annotated.cell_data[kind.title()+"Chord_mm"] = array
    return report,annotated
