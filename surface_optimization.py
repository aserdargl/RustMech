"""Bounded FE boundary reduction; the displayed reference geometry is untouched."""

import numpy as np
import pyvista as pv
from vtkmodules.vtkFiltersGeneral import vtkDistancePolyDataFilter


def spaced_indices(points, spacing):
    """Keep a representative per spatial bin, in original traversal order."""
    points = np.asarray(points)
    if not len(points):
        return np.empty(0, dtype=np.int64)
    bins = np.floor(points / spacing).astype(np.int64)
    return np.sort(np.unique(bins, axis=0, return_index=True)[1])


def cap_areas(surface, size):
    corners = np.asarray(surface.points)[surface.faces.reshape(-1, 4)[:, 1:]]
    areas = np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0],
                                    corners[:, 2] - corners[:, 0]), axis=1) / 2
    return np.array([areas[np.all(np.isclose(corners[:, :, axis], value,
                                            rtol=0, atol=size * 1e-8), axis=1)].sum()
                     for axis in range(3) for value in (0, size)])


def simplify_boundary(surface, settings, on_progress=None):
    if surface.n_cells < 2000:
        return surface
    tolerance = min(settings.surface_resolution / 4,
                    settings.local_mesh_size / 5, settings.mesh_resolution / 20)
    if on_progress:
        on_progress(f"Reducing FE boundary ({surface.n_cells:,} triangles)…")
    for reduction in (0.9, 0.5):
        candidate = _reduced_boundary(surface, settings, tolerance, reduction)
        if candidate is not None:
            if on_progress:
                on_progress(f"FE boundary reduced: {surface.n_cells:,} → {candidate.n_cells:,} triangles")
            return candidate.compute_normals(auto_orient_normals=True, split_vertices=False)
    if on_progress:
        on_progress('Keeping the reference boundary: reduction exceeds the geometry tolerance')
    return surface


def _reduced_boundary(surface, settings, tolerance, reduction):
    candidate = surface.decimate(reduction, volume_preservation=True)
    # Quadric collapse can move cap vertices slightly off their exact planes.
    # Restore them before checking the topology, contact areas and distance.
    points = np.asarray(candidate.points).copy()
    points[np.abs(points) <= tolerance * 0.6] = 0
    points[np.abs(points - settings.size) <= tolerance * 0.6] = settings.size
    if np.any(points < 0) or np.any(points > settings.size):
        return None
    candidate.points = points
    candidate = candidate.clean(tolerance=settings.size * 1e-10, absolute=True,
                                polys_to_lines=False, lines_to_points=False).triangulate()
    if candidate.n_cells >= surface.n_cells or not candidate.is_manifold:
        return None
    if abs(candidate.volume / surface.volume - 1) > 0.005:
        return None
    before, after = cap_areas(surface, settings.size), cap_areas(candidate, settings.size)
    if np.any(np.abs(after - before) > np.maximum(before * 0.005, settings.size ** 2 * 1e-10)):
        return None
    # Check both directions against triangles, not just nearest vertices.
    distance = vtkDistancePolyDataFilter()
    distance.SetInputData(0, surface)
    distance.SetInputData(1, candidate)
    distance.SignedDistanceOff()
    distance.ComputeSecondDistanceOn()
    distance.Update()
    for output in (distance.GetOutput(), distance.GetSecondDistanceOutput()):
        values = np.asarray(pv.wrap(output).point_data['Distance'])
        if not np.all(np.isfinite(values)) or np.max(np.abs(values)) > tolerance:
            return None
    return candidate
