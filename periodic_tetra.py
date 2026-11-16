"""Conforming TPMS and truss cells, cut from a periodic Freudenthal lattice.

This uses VTK's native clipping/tetrahedralization rather than STL chart fitting.
Only complete, verified periods are eligible; arbitrary surfaces use Netgen.
"""

from itertools import permutations, product
import numpy as np
import pyvista as pv

from mesh_resources import require_memory
from structure_library import evaluate_priority_field
from periodic_sampling import has_repeating_cell


class PeriodicUnavailable(ValueError):
    """The requested geometry, resolution or quality needs the Netgen path."""


def period_count(settings):
    ratio = settings.size / settings.cell_size
    count = int(round(ratio))
    if not has_repeating_cell(settings) or count < 2 or not np.isclose(ratio, count, rtol=0, atol=1e-9):
        raise PeriodicUnavailable('requires an integer number of at least two TPMS or lattice periods')
    return count


def _ordered_tetrahedra(clipped):
    # VTK picks polygon diagonals by point ID. Coordinate order makes that
    # choice invariant under translation, including intersections at vertices.
    order = np.lexsort(np.round(clipped.points, 12).T[::-1])
    inverse = np.empty_like(order)
    inverse[order] = np.arange(len(order))
    mixed = clipped.cells.copy()
    mask = np.ones(len(mixed), dtype=bool)
    mask[clipped.offset[:-1] + np.arange(clipped.n_cells)] = False
    mixed[mask] = inverse[mixed[mask]]
    return pv.UnstructuredGrid(mixed, clipped.celltypes, clipped.points[order]).triangulate()


def _face_signature(surface, axis, value, tolerance):
    points = np.asarray(surface.points)
    faces = surface.faces.reshape(-1, 4)[:, 1:]
    faces = faces[np.all(np.abs(points[faces, axis] - value) <= tolerance, axis=1)]
    tangent = [a for a in range(3) if a != axis]
    coordinates = np.rint(points[faces][:, :, tangent] / tolerance).astype(np.int64)
    return {tuple(sorted(map(tuple, face))) for face in coordinates}


def check_periodic_faces(surface, period):
    tolerance = period * 1e-8
    for axis in range(3):
        low = _face_signature(surface, axis, 0, tolerance)
        high = _face_signature(surface, axis, period, tolerance)
        if not low or low != high:
            raise PeriodicUnavailable(f'cell triangles do not match on axis {axis}')


def _cell(settings, threshold, on_progress):
    period = settings.cell_size
    # A cell is uniformly resolved to the local target. This is deliberately
    # conservative: cell copies must share the contact refinement on all faces.
    n = max(24, int(np.ceil(period / min(settings.mesh_resolution, settings.local_mesh_size))))
    require_memory(n ** 3 * 1400, "Periodic cell meshing")
    if on_progress:
        on_progress(f'Meshing one periodic {settings.structure_family} cell ({n} intervals)…')
    h = period / n
    ijk = np.indices((n + 1,) * 3).reshape(3, -1).T
    points = ijk * h
    roots = np.indices((n,) * 3).reshape(3, -1)
    ids = np.ravel_multi_index(roots, (n + 1,) * 3)
    strides = ((n + 1) ** 2, n + 1, 1)
    template = np.array([[0, a, a + b, a + b + c] for a, b, c in permutations(strides)])
    cells = (ids[:, None, None] + template[None, :, :]).reshape(-1, 4)
    corners = points[cells]
    negative = np.linalg.det(corners[:, 1:] - corners[:, :1]) < 0
    cells[negative, :2] = cells[negative, :2][:, ::-1]
    grid = pv.UnstructuredGrid(np.column_stack((np.full(len(cells), 4), cells)).ravel(),
                              np.full(len(cells), pv.CellType.TETRA, dtype=np.uint8), points)
    # Evaluate only the torus, then copy its boundary samples exactly. Some
    # TPMS families have different mathematical periods; verification rejects
    # them rather than silently repeating a different geometry.
    axis = np.arange(n + 1) * h
    values = evaluate_priority_field(settings, axis[:, None, None], axis[None, :, None], axis[None, None, :])
    for a in range(3):
        if not np.allclose(np.take(values, 0, axis=a), np.take(values, n, axis=a), rtol=1e-6, atol=1e-6):
            raise PeriodicUnavailable('field does not repeat at the requested cell size')
    base = np.asarray(values[:-1, :-1, :-1], dtype=float)
    gradient = np.array([(np.roll(base, -1, a) - np.roll(base, 1, a)) / (2 * h) for a in range(3)])
    norm = np.pad(np.linalg.norm(gradient, axis=0), ((0, 1),) * 3, mode='wrap')
    values = np.pad(base, ((0, 1),) * 3, mode='wrap')
    # Snap near-isovalue lattice vertices before clipping to suppress tiny cut
    # elements. The displacement estimate is bounded by the reference spacing.
    snap = min(h * 0.08, settings.surface_resolution * 0.2)
    values[np.abs(values - threshold) / np.maximum(norm, 1e-12) < snap] = threshold
    grid['implicit'] = values.ravel()
    clipped = grid.clip_scalar(scalars='implicit', value=threshold, invert=True)
    grid = _ordered_tetrahedra(clipped)
    cells = np.asarray(grid.cell_connectivity).reshape(-1, 4)
    xyz = grid.points[cells]
    determinants = np.linalg.det(xyz[:, 1:] - xyz[:, :1])
    # Exact isovalue coincidences create zero-volume cells; their collapsed
    # faces disappear when coincident points are merged.
    grid = grid.extract_cells(determinants > h ** 3 * 1e-12).clean(tolerance=period * 1e-10)
    if not grid.n_cells:
        raise PeriodicUnavailable('clipped cell has no solid elements')
    boundary = grid.extract_surface(algorithm='dataset_surface')
    if not boundary.is_manifold:
        raise PeriodicUnavailable('clipped cell boundary is not closed')
    check_periodic_faces(boundary, period)
    quality = grid.cell_quality('scaled_jacobian').cell_data['scaled_jacobian']
    if not np.all(np.isfinite(quality)) or quality.min() < 1e-4 or np.quantile(quality, .01) < .005:
        raise PeriodicUnavailable('cut-cell element quality needs Netgen remeshing')
    return grid


def tile_cell(cell, period, count, on_progress=None):
    require_memory(count ** 3 * (cell.n_points * 96 + cell.n_cells * 512),
                   "Periodic mesh replication and connectivity")
    if on_progress:
        on_progress(f'Replicating and welding {count}×{count}×{count} cells…')
    offsets = np.array(list(product(range(count), repeat=3))) * period
    points = (np.asarray(cell.points)[None, :, :] + offsets[:, None, :]).reshape(-1, 3)
    corners = np.asarray(cell.cell_connectivity).reshape(-1, 4)
    cells = (corners[None, :, :] + np.arange(len(offsets))[:, None, None] * cell.n_points).reshape(-1, 4)
    # Weld only coordinates equal to numerical precision, never a FE-length
    # tolerance that could fuse distinct thin walls.
    tolerance = period * 1e-10
    _, first, inverse = np.unique(np.rint(points / tolerance).astype(np.int64), axis=0,
                                   return_index=True, return_inverse=True)
    points, cells = points[first], inverse[cells]
    grid = pv.UnstructuredGrid(np.column_stack((np.full(len(cells), 4), cells)).ravel(),
                              np.full(len(cells), pv.CellType.TETRA, dtype=np.uint8), points)
    # Only the completed specimen may discard isolated exterior fragments;
    # cell fragments can connect through neighbouring cells after replication.
    labels = face_components(grid)
    largest = np.bincount(labels).argmax()
    grid = grid.extract_cells(labels == largest).clean(tolerance=0)
    boundary = grid.extract_surface(algorithm='dataset_surface')
    if not boundary.is_manifold:
        raise PeriodicUnavailable('replicated boundary is not manifold')
    p = np.asarray(boundary.points)
    f = boundary.faces.reshape(-1, 4)[:, 1:]
    for axis in range(3):
        for index in range(1, count):
            if np.any(np.all(np.abs(p[f, axis] - index * period) <= tolerance * 10, axis=1)):
                raise PeriodicUnavailable('an unmatched triangle remains at a cell interface')
    return grid


def face_components(grid):
    """Connectivity through a complete triangular face, not a shared vertex."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    cells = np.asarray(grid.cell_connectivity).reshape(-1, 4)
    face = np.sort(cells[:, ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3))].reshape(-1, 3), axis=1)
    # Lexicographic face IDs avoid overflowing the old n**3 packed key on
    # large meshes while retaining exact (rather than hashed) adjacency.
    order = np.lexsort(face.T[::-1])
    ordered = face[order]
    same = np.all(ordered[1:] == ordered[:-1], axis=1)
    if np.any(same[1:] & same[:-1]):
        raise PeriodicUnavailable('more than two tetrahedra share a face')
    left, right = order[:-1][same] // 4, order[1:][same] // 4
    graph = coo_matrix((np.ones(len(left), dtype=np.uint8), (left, right)),
                       shape=(len(cells), len(cells))).tocsr()
    return connected_components(graph, directed=False, return_labels=True)[1]


def generate_periodic(settings, reference, threshold, on_progress=None):
    count = period_count(settings)
    cell = _cell(settings, threshold, on_progress)
    grid = tile_cell(cell, settings.cell_size, count, on_progress)
    if abs(grid.volume / reference.volume - 1) > .015:
        raise PeriodicUnavailable('periodic volume differs from the reference by more than 1.5%')
    return grid
