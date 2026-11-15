"""Generate linear or quadratic tetrahedra on the triangulated specimen boundary."""

from dataclasses import dataclass
from hashlib import blake2b
from struct import pack
from threading import Lock

import numpy as np
import pyvista as pv
from vtkmodules.vtkCommonCore import reference
from vtkmodules.vtkCommonDataModel import vtkGenericCell, vtkStaticCellLocator

from model import FEMesh, MAX_FE_ELEMENTS, Settings, TET_EDGES
from netgen_io import geometry_from_surface, restrict_h
from netgen_kernel import generate_arrays, kernel_path
from rust_mesh import quadratic_tetra_arrays, tet_metrics
from surface_optimization import simplify_boundary, spaced_indices
from periodic_tetra import PeriodicUnavailable, generate_periodic


_NETGEN_LOCK = Lock()
_TET_CACHE_LOCK = Lock()
_TET_CACHE_BYTES = 128 * 1024**2
_tet_cache = None
# Netgen orients tetrahedra oppositely to VTK and enumerates edges differently.
_NETGEN_TO_VTK = (1, 0, 2, 3, 4, 5, 7, 8, 6, 9)
_THIN_WALL_ELEMENT_COUNT = 5
_CONTACT_SAMPLE_SPACING_FACTOR = 2


@dataclass(frozen=True)
class _LinearTetrahedra:
    key: bytes
    points: np.ndarray
    cells: np.ndarray
    volume: float
    method: str = 'Netgen'


def clear_tetrahedral_cache():
    """Release the single retained linear mesh (also used by benchmarks)."""
    global _tet_cache
    with _TET_CACHE_LOCK:
        _tet_cache = None


def _tet_cache_key(surface, settings):
    digest = blake2b(digest_size=32)
    digest.update(repr((settings.tetra_strategy, settings.cell_size, settings.structure_family,
                        settings.structure_name, settings.tpms_morphology,
                        settings.solid_fraction, settings.random_seed)).encode())
    digest.update(
        pack(
            "4d",
            settings.size,
            settings.mesh_resolution,
            settings.local_mesh_size,
            settings.surface_resolution,
        )
    )
    # Hash the actual geometry: callers may supply surfaces that share settings.
    # Normals are recomputed by _clean_boundary and are not part of the input.
    for array in (surface.points, surface.faces):
        array = np.ascontiguousarray(array)
        digest.update(str((array.dtype.str, array.shape)).encode("ascii"))
        digest.update(memoryview(array).cast("B"))
    return digest.digest()


def _grid_from_linear(linear, mesh_type):
    if mesh_type == "TET10":
        points, cells = quadratic_tetra_arrays(linear.points, linear.cells)
        cell_type = pv.CellType.QUADRATIC_TETRA
    else:
        # Never expose the cache's buffers to mutable VTK/solver datasets.
        points, cells = linear.points.copy(), linear.cells.copy()
        cell_type = pv.CellType.TETRA
    grid = _grid_from_arrays(points, cells, cell_type)
    grid.field_data['meshing_method'] = [linear.method]
    return grid


def _grid_from_arrays(points, cells, cell_type=pv.CellType.TETRA):
    grid = pv.UnstructuredGrid()
    grid.points = points
    grid.SetCells(int(cell_type), pv.CellArray.from_regular_cells(cells))
    grid.point_data["ansys_node_num"] = np.arange(1, grid.n_points + 1)
    grid.cell_data["ansys_elem_num"] = np.arange(1, grid.n_cells + 1)
    return grid


def _thin_wall_samples(surface, local_size, surface_spacing, *, sample_spacing=None):
    """Locate opposing walls with inward rays from boundary samples."""
    reach = _THIN_WALL_ELEMENT_COUNT * local_size
    locator = vtkStaticCellLocator()
    locator.SetDataSet(surface)
    locator.BuildLocator()
    # Slice plain arrays in this hot loop: pyvista_ndarray propagates dataset
    # metadata on every slice and arithmetic operation. Convert once, also
    # replacing the two per-ray float64 conversions.
    points = np.asarray(surface.points, dtype=float)
    normals = np.asarray(surface.point_normals, dtype=float)
    cell_normals = np.asarray(surface.cell_normals)
    epsilon = min(local_size, surface_spacing) * 1e-4
    cell = vtkGenericCell()
    samples = []
    indices = (spaced_indices(points, sample_spacing) if sample_spacing else range(len(points)))
    for index in indices:
        point, normal = points[index], normals[index]
        direction = -normal
        start = point + epsilon * direction
        end = point + reach * direction
        hit = [0.0, 0.0, 0.0]
        hit_cell = reference(0)
        if (
            locator.IntersectWithLine(
                start,
                end,
                epsilon,
                reference(0.0),
                hit,
                [0.0] * 3,
                reference(0),
                hit_cell,
                cell,
            )
            and np.dot(cell_normals[int(hit_cell)], direction) > 0.5
        ):
            samples.extend((point, hit, (point + hit) / 2))
    return samples


def _platen_samples(surface, settings):
    if settings.test_profile != 'custom':
        # Specimen boundaries already carry shoulder/contact detail. Do not
        # sample an enclosing cube or project rectangular coupons onto it.
        return np.asarray(surface.points)[spaced_indices(surface.points, settings.local_mesh_size)]
    faces = surface.faces.reshape(-1, 4)
    z = surface.points[faces[:, 1:], 2]
    platen_faces = faces[(z == 0).all(axis=1) | (z == settings.size).all(axis=1)]
    if not len(platen_faces):
        return np.empty((0, 3))
    caps = pv.PolyData(surface.points, platen_faces.ravel()).clean()
    # Sample the contact planes directly. Subdividing arbitrary triangles can
    # generate hundreds of thousands of redundant points after decimation.
    n = max(1, int(np.ceil(settings.size / (_CONTACT_SAMPLE_SPACING_FACTOR * settings.local_mesh_size))))
    if 2 * (n + 1) ** 2 > MAX_FE_ELEMENTS:
        raise ValueError(
            "The platen refinement exceeds the sampling limit. Increase the local mesh size."
        )
    axis = np.linspace(0, settings.size, n + 1)
    x, y = np.meshgrid(axis, axis, indexing='ij')
    xy = np.column_stack((x.ravel(), y.ravel()))
    points = np.vstack([np.column_stack((xy, np.full(len(xy), z))) for z in (0, settings.size)])
    probed = pv.PolyData(points).sample(caps, tolerance=settings.size * 1e-9)
    points = points[np.asarray(probed.point_data['vtkValidPointMask'], dtype=bool)]
    # Keep rim samples too, so narrow contact strips between grid positions
    # are represented even when a coarse plane grid misses their interiors.
    rim = caps.extract_feature_edges(boundary_edges=True, feature_edges=False,
                                     non_manifold_edges=False, manifold_edges=False)
    edges = np.asarray(rim.lines).reshape(-1, 3)[:, 1:]
    starts, ends = np.asarray(rim.points)[edges[:, 0]], np.asarray(rim.points)[edges[:, 1]]
    divisions = np.maximum(1, np.ceil(np.linalg.norm(ends - starts, axis=1) /
                                    (_CONTACT_SAMPLE_SPACING_FACTOR * settings.local_mesh_size)).astype(np.int64))
    if len(points) + int(divisions.sum()) > MAX_FE_ELEMENTS:
        raise ValueError('The platen refinement exceeds the sampling limit. Increase the local mesh size.')
    owners = np.repeat(np.arange(len(edges)), divisions)
    steps = np.arange(len(owners)) - np.repeat(np.cumsum(divisions) - divisions, divisions)
    edge_points = starts[owners] + (ends - starts)[owners] * (steps / divisions[owners])[:, None]
    points = np.vstack((points, np.asarray(rim.points), edge_points))
    return points[spaced_indices(points, settings.local_mesh_size / 2)]


def _read_grid(mesh, mesh_type):
    count = len(mesh.Elements3D())
    if not 1 <= count <= MAX_FE_ELEMENTS:
        raise ValueError(
            f"{mesh_type} mesh has {count:,} elements. Limit: {MAX_FE_ELEMENTS:,}. "
            "Increase the mesh sizes."
        )
    if mesh_type == "TET10":
        mesh.SecondOrder()
        order, cell_type = _NETGEN_TO_VTK, pv.CellType.QUADRATIC_TETRA
    else:
        order, cell_type = _NETGEN_TO_VTK[:4], pv.CellType.TETRA
    connectivity = mesh.Elements3D().NumPy()["nodes"][:, order] - 1
    points = np.array(mesh.Coordinates())
    if mesh_type == "TET10":
        for i, (start, end) in enumerate(TET_EDGES, start=4):
            points[connectivity[:, i]] = (
                points[connectivity[:, start]] + points[connectivity[:, end]]
            ) / 2
    return _grid_from_arrays(points, connectivity, cell_type)


def _solid_volume(grid, mesh_type):
    determinants, _ = tet_metrics(grid)
    if not np.all(np.isfinite(determinants) & (determinants > 0)):
        raise ValueError(f"{mesh_type} mesh contains inverted or zero-volume elements.")
    return float(determinants.sum() / 6)


def _clean_boundary(surface, size, tolerance):
    if not surface.is_manifold:
        raise ValueError(
            "Tetrahedral meshing requires a closed reference surface. "
            "Check the structure, period / pore scale and solid fraction."
        )
    boundary = surface.copy()
    boundary.points[np.isclose(boundary.points, 0, rtol=0, atol=tolerance)] = 0
    boundary.points[np.isclose(boundary.points, size, rtol=0, atol=tolerance)] = size
    boundary = boundary.clean(tolerance=tolerance, absolute=True).triangulate()
    if not boundary.is_manifold:
        raise ValueError(
            "The reference surface has gaps after vertex merging. "
            "Check the structure, period / pore scale and solid fraction."
        )
    return boundary.compute_normals(auto_orient_normals=True, split_vertices=False)


def _mesh_boundary(boundary, settings, mesh_type, refinement_points, on_progress):
    optimized = kernel_path()
    if optimized is not None:
        with _NETGEN_LOCK:
            if on_progress:
                on_progress("Loading the boundary into Netgen…")
            points, cells = generate_arrays(
                boundary,
                settings,
                refinement_points,
                library_path=optimized,
                on_progress=on_progress,
            )
            if mesh_type == "TET10":
                points, cells = quadratic_tetra_arrays(points, cells)
                return _grid_from_arrays(points, cells, pv.CellType.QUADRATIC_TETRA)
            return _grid_from_arrays(points, cells)
    try:
        from netgen.meshing import MeshingParameters, SetMessageImportance
    except (ImportError, OSError) as exc:
        raise ValueError(
            "Tetrahedral meshing requires the Netgen Python package (netgen-mesher)."
        ) from exc
    with _NETGEN_LOCK:
        SetMessageImportance(0)
        parameters = MeshingParameters(maxh=settings.mesh_resolution)
        restrict_h(parameters, refinement_points, settings.local_mesh_size)
        geometry = geometry_from_surface(boundary)
        if on_progress:
            on_progress("Generating tetrahedra in Netgen…")
        mesh = geometry.GenerateMesh(mp=parameters)
        return _read_grid(mesh, mesh_type)


def _linear_tetrahedra(settings, surface, on_progress, use_cache, periodic_threshold=None):
    global _tet_cache
    key = _tet_cache_key(surface, settings)
    key += pack('d', periodic_threshold) if periodic_threshold is not None else b'custom'
    # Netgen is serialized already. Keep cache lookup+publication atomic too,
    # so concurrent equivalent calls do not generate the same expensive mesh.
    with _TET_CACHE_LOCK:
        if use_cache and _tet_cache is not None and _tet_cache.key == key:
            if on_progress:
                on_progress("Reusing the existing volume mesh…")
            return _tet_cache
        grid = None
        method = 'Netgen'
        if settings.tetra_strategy != 'netgen':
            try:
                if periodic_threshold is None:
                    raise PeriodicUnavailable('the supplied surface is not an unchanged generated reference')
                grid = generate_periodic(settings, surface, periodic_threshold, on_progress)
                method = f'Periodic {settings.structure_family.upper()} cells'
            except PeriodicUnavailable as exc:
                if settings.tetra_strategy == 'periodic':
                    raise ValueError(f'Periodic mesh unavailable: {exc}. Use Auto or Netgen, or increase the local size.') from exc
                if on_progress:
                    on_progress(f'Using Netgen: {exc}.')
        if grid is None:
            boundary = _clean_boundary(surface, settings.size, settings.size * 1e-6)
            if not settings.imported_geometry_path and settings.test_profile == 'custom':
                boundary = simplify_boundary(boundary, settings, on_progress)
            if on_progress:
                on_progress("Locating thin walls and platen contact regions…")
            platen_samples = _platen_samples(boundary, settings)
            thin_walls = _thin_wall_samples(
                boundary, settings.local_mesh_size, settings.surface_resolution,
                sample_spacing=settings.local_mesh_size / 2,
            )
            thin_walls = np.asarray(thin_walls).reshape(-1, 3)
            thin_walls = thin_walls[spaced_indices(thin_walls, settings.local_mesh_size / 2)]
            if on_progress:
                on_progress(f'Local refinement: {len(platen_samples):,} contact / {len(thin_walls):,} thin-wall points')
            grid = _mesh_boundary(
                boundary, settings, "TET4", (platen_samples, thin_walls), on_progress
            )
        volume = _solid_volume(grid, "TET4")
        points = np.array(grid.points)
        cells = np.array(grid.cell_connectivity).reshape(-1, 4)
        points.flags.writeable = cells.flags.writeable = False
        linear = _LinearTetrahedra(key, points, cells, volume, method)
        if use_cache:
            _tet_cache = (
                linear if points.nbytes + cells.nbytes <= _TET_CACHE_BYTES else None
            )
        return linear


def generate_tetrahedral_mesh(
    settings: Settings, surface: pv.PolyData, *, on_progress=None, use_cache=True,
    periodic_threshold=None,
) -> FEMesh:
    """Mesh a closed specimen and identify nodes on both platen planes."""
    mesh_type = "TET4" if settings.mesh_type == "TET4" else "TET10"
    tolerance = settings.size * 1e-6
    linear = _linear_tetrahedra(settings, surface, on_progress, use_cache, periodic_threshold)
    if on_progress:
        on_progress(f"Assembling {mesh_type} elements…")
    grid = _grid_from_linear(linear, mesh_type)
    ids = grid.point_data["ansys_node_num"]
    z = grid.points[:, 2]
    bottom = ids[np.isclose(z, 0, rtol=0, atol=tolerance)]
    top = ids[np.isclose(z, settings.size, rtol=0, atol=tolerance)]
    return FEMesh(
        grid,
        settings.mesh_resolution,
        None,
        100 * linear.volume / (np.prod(np.asarray(surface.bounds).reshape(3, 2)[:, 1] - np.asarray(surface.bounds).reshape(3, 2)[:, 0]) if settings.imported_geometry_path or settings.test_profile != 'custom' else settings.size**3),
        1,
        bottom,
        top,
        pv.UnstructuredGrid(),
        1,
        grid.n_cells,
    )
