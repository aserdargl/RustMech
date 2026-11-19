"""Experimental TET4 clipping of an implicit solid on a Cartesian grid."""

from itertools import permutations, product
from operator import index

import numpy as np
import pyvista as pv
from vtkmodules.vtkCommonExecutionModel import vtkAlgorithm
from vtkmodules.vtkFiltersGeneral import vtkTableBasedClipDataSet

from mesh_resources import require_memory
from periodic_tetra import PeriodicUnavailable, face_components
from tetrahedral_mesh import _grid_from_arrays


def _snap_vertices(field, points, values, width, size, fraction):
    """Move a vertex to its nearest sufficiently close interface crossing."""
    spacing = size / (width - 1)
    ids = np.arange(len(points)).reshape((width,) * 3)
    limit = fraction * spacing
    distances = np.full(len(points), limit)
    targets = np.full(len(points), -1, dtype=np.intp)
    for direction in product((0, 1), repeat=3):
        if not any(direction):
            continue
        start = ids[tuple(slice(0, width - step) for step in direction)].ravel()
        end = ids[tuple(slice(step, width) for step in direction)].ravel()
        crossing = ((values[start] < 0) & (values[end] > 0)) | ((values[start] > 0) & (values[end] < 0))
        start, end = start[crossing], end[crossing]
        for near, far in ((start, end), (end, start)):
            # Cube boundary vertices may slide along their incident cube planes.
            locked = ((points[near] == 0) | (points[near] == size)) & (points[near] != points[far])
            distance = np.abs(values[near] / (values[near] - values[far])) * spacing * np.sqrt(sum(direction))
            close = (distance < distances[near]) & ~locked.any(axis=1)
            distances[near[close]] = distance[close]
            targets[near[close]] = far[close]

    selected = np.flatnonzero(targets >= 0)
    if not len(selected):
        return False
    near, far = points[selected], points[targets[selected]]
    edges = far - near
    inside = values[selected] < 0
    # Refine the candidate crossings on the actual field before committing the
    # move. This keeps a sphere or curved sheet close to its analytic surface.
    start, end = np.zeros(len(selected)), np.ones(len(selected))
    for _ in range(44):
        mid = (start + end) / 2
        samples = np.asarray(field(near + mid[:, None] * edges))
        same_side = (samples < 0) == inside
        start[same_side] = mid[same_side]
        end[~same_side] = mid[~same_side]
    displacement = ((start + end) / 2)[:, None] * edges
    within_limit = np.linalg.norm(displacement, axis=1) <= limit
    points[selected[within_limit]] += displacement[within_limit]
    values[selected[within_limit]] = 0
    return np.any(within_limit)


def _determinants(points, cells):
    corners = points[cells]
    edges = corners[:, 1:] - corners[:, :1]
    return np.einsum("ij,ij->i", edges[:, 0], np.cross(edges[:, 1], edges[:, 2]))


def _mesh_topology(grid):
    skin = grid.extract_surface(algorithm="dataset_surface")
    if skin.n_open_edges:
        return None
    try:
        components = np.unique(face_components(grid)).size
    except PeriodicUnavailable:
        return None
    # Every edge of a closed triangle surface is incident to two faces.
    return components, skin.n_points - skin.n_cells // 2


def _quality_floor(grid):
    jacobian = grid.cell_quality("scaled_jacobian").cell_data["scaled_jacobian"]
    return np.array([jacobian.min(), np.percentile(jacobian, 1)])


def build_trimmed_tet4(field, subdivisions, *, size=1.0, snap_boundary=True):
    """Mesh field(points) <= 0 in [0, size]³ using linear edge intersections.

    This prototype only resolves features sampled by the background grid.
    Snap nearby background vertices to the boundary when it improves the lower
    tail of element quality and preserves the unsnapped mesh's topology checks.
    Disable snapping to reproduce the original clipping.
    """
    subdivisions = index(subdivisions)
    if subdivisions < 1 or not np.isfinite(size) or size <= 0:
        raise ValueError("The grid requires positive subdivisions and a positive finite size.")
    width = subdivisions + 1
    bytes_per_voxel = 2200 if snap_boundary else 1400
    require_memory(width**3 * 64 + subdivisions**3 * bytes_per_voxel, "Trimmed TET4 meshing")
    axis = np.linspace(0.0, size, width)
    points = np.stack(np.meshgrid(axis, axis, axis, indexing="ij"), axis=-1).reshape(-1, 3)
    values = np.asarray(field(points), dtype=float)
    if values.shape != (len(points),) or not np.isfinite(values).all():
        raise ValueError("The field must return one finite scalar per grid vertex.")
    # Treat evaluation roundoff at a grid vertex as an exact intersection.
    zero_tolerance = np.max(np.abs(values)) * (16 * np.finfo(float).eps)
    values = np.where(np.abs(values) <= zero_tolerance, 0, values)
    if not np.any(values < 0):
        raise ValueError("No solid interior was sampled by the Cartesian grid.")

    # The six monotone paths between opposite cube corners choose the same
    # face diagonals in every adjacent voxel.
    steps = (width * width, width, 1)
    voxel_origins = np.arange(width**3).reshape((width,) * 3)[:-1, :-1, :-1].ravel()
    offsets = np.asarray([(0, a, a + b, a + b + c) for a, b, c in permutations(steps)])
    connectivity = (voxel_origins[:, None, None] + offsets).reshape(-1, 4)
    baseline = _clip_tets(points, values, connectivity)
    if not snap_boundary:
        return baseline
    topology = _mesh_topology(baseline)
    if topology is None:
        return baseline
    quality_floor = _quality_floor(baseline)

    # The six tetrahedra have the signs of their axis permutations.
    orientation = np.array([1, -1, -1, 1, 1, -1])
    for fraction in (0.18, 0.15, 0.09, 0.045):
        moved, snapped = points.copy(), values.copy()
        if not _snap_vertices(field, moved, snapped, width, size, fraction):
            break
        if np.any(_determinants(moved, connectivity).reshape(-1, 6) * orientation <= 0):
            continue
        try:
            candidate = _clip_tets(moved, snapped, connectivity)
        except ValueError:
            continue
        candidate_quality = _quality_floor(candidate)
        if (np.all(candidate_quality >= quality_floor) and np.any(candidate_quality > quality_floor)
                and _mesh_topology(candidate) == topology):
            candidate.field_data["snap_fraction"] = [fraction]
            return candidate
    return baseline


def _clip_tets(points, values, connectivity):
    background = pv.UnstructuredGrid()
    background.points = points
    background.SetCells(int(pv.CellType.TETRA), pv.CellArray.from_regular_cells(connectivity))
    background.point_data["level_set"] = values
    background.set_active_scalars("level_set")

    clip = vtkTableBasedClipDataSet()
    clip.SetInputData(background)
    clip.SetInsideOut(True)
    clip.SetOutputPointsPrecision(vtkAlgorithm.DOUBLE_PRECISION)
    clip.Update()
    # Triangulate as one grid so neighboring cut cells share triangulations.
    # Merge exact coincidences when an intersection lands on a grid vertex.
    clipped = pv.wrap(clip.GetOutput()).clean(tolerance=0).triangulate()
    if not clipped.n_cells or not np.all(clipped.celltypes == pv.CellType.TETRA):
        raise ValueError("Clipping did not produce a solid TET4 mesh.")

    cells = np.asarray(clipped.cell_connectivity).reshape(-1, 4)
    ordered = np.sort(cells, axis=1)
    # Exact coincidences can collapse a cut cell to a face, edge or point.
    cells = cells[np.all(np.diff(ordered, axis=1) != 0, axis=1)]
    used, connectivity = np.unique(cells, return_inverse=True)
    points = np.asarray(clipped.points)[used]
    cells = connectivity.reshape(-1, 4)
    determinants = _determinants(points, cells)
    if not len(cells) or not np.all(np.isfinite(determinants) & (determinants != 0)):
        raise ValueError("Clipping produced zero-volume or invalid tetrahedra.")
    reversed_orientation = determinants < 0
    cells[reversed_orientation] = cells[reversed_orientation][:, [1, 0, 2, 3]]
    return _grid_from_arrays(points, cells)
