"""Geometry and mesh orchestration without GUI or plotting imports."""

from __future__ import annotations

from dataclasses import dataclass, replace
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from hashlib import blake2b

import numpy as np
import pyvista as pv
from skimage.measure import marching_cubes

from model import DERIVED_SOLID_MESH_TYPES, FEMesh, Settings, TETRAHEDRAL_MESH_TYPES, require_geometry
from rust_mesh import build_hex_grid, largest_triangle_surface, select_voxels
from structure_library import evaluate_priority_field
from periodic_sampling import sample_box, sample_priority_grid
from mesh_resources import require_memory
from tetrahedral_mesh import generate_tetrahedral_mesh


_single_surface = ContextVar('single_surface', default=False)


@contextmanager
def single_surface_mode():
    """Keep nested compute-node geometry work on the whole-surface path."""
    token = _single_surface.set(True)
    try:
        yield
    finally:
        _single_surface.reset(token)


def gyroid_field(x, y, z, cell_size: float):
    k = 2 * np.pi / cell_size
    return (
        np.sin(k * x) * np.cos(k * y)
        + np.sin(k * y) * np.cos(k * z)
        + np.sin(k * z) * np.cos(k * x)
    )


@lru_cache(maxsize=16)
def _surface_threshold_for_settings(
    structure_family: str,
    structure_name: str,
    tpms_morphology: str,
    random_seed: int,
    dimensions: tuple,
    period: float,
    fraction: float,
) -> float:
    probe_settings = Settings(
        size_x=dimensions[0], size_y=dimensions[1], size=dimensions[2],
        cell_size=period,
        solid_fraction=fraction,
        structure_family=structure_family,
        structure_name=structure_name,
        tpms_morphology=tpms_morphology,
        random_seed=random_seed,
    )
    x, y, z = [(np.arange(101) + 0.5) * length / 101 for length in dimensions]
    field = evaluate_priority_field(
        probe_settings,
        x[:, None, None],
        y[None, :, None],
        z[None, None, :],
    )
    return float(np.quantile(field, fraction / 100))


def make_surface(settings: Settings, *, surface_backend="flying_edges", chunked=None, on_progress=None) -> pv.PolyData:
    require_geometry(settings, include_mesh=False)
    if settings.test_profile != 'custom':
        from standard_tests import make_standard_surface
        return make_standard_surface(settings)
    if settings.imported_geometry_path:
        from imported_geometry import read_surface
        surface = read_surface(settings.imported_geometry_path, require_single=settings.imported_geometry_role == 'compression')
        if settings.imported_geometry_role == 'compression' and (not np.isclose(surface.bounds[4], 0, atol=settings.size * 1e-6) or not np.isclose(surface.bounds[5], settings.size, atol=settings.size * 1e-6)):
            raise ValueError('Imported geometry height has changed. Import it again.')
        return surface
    if settings.rectangular:
        lengths = np.asarray(settings.dimensions)
        counts = np.maximum(4, np.ceil(lengths / settings.surface_resolution).astype(int))
        require_memory(int(np.prod(counts + 3)) * 40, "Rectangular reference surface")
        step = lengths / counts
        axes = [np.arange(-1, n + 2) * h for n, h in zip(counts, step)]
        field = sample_box(settings, axes, dtype=np.float32, threshold=geometry_threshold(settings))
        field[[0, -1], :, :] = field[:, [0, -1], :] = field[:, :, [0, -1]] = 1
        surface = extract_reference_surface(field, 1., counts, backend=surface_backend)
        surface.points = surface.points.astype(float) * step
        surface.field_data['_rustmech_reference'] = np.frombuffer(_surface_signature(surface, settings), dtype=np.uint8).copy()
        return surface
    n = max(4, int(np.ceil(settings.size / settings.surface_resolution)))
    spacing = settings.size / n
    require_memory((n + 3) * 40, 'Reference surface sampling axes')
    axis = np.arange(-1, n + 2, dtype=float) * spacing
    from chunked_surface import should_chunk, build_chunked_surface
    if not _single_surface.get() and (chunked is True or (chunked is None and should_chunk(len(axis)))):
        surface = build_chunked_surface(settings, axis, spacing, backend=surface_backend, on_progress=on_progress)
        surface.field_data['_rustmech_reference'] = np.frombuffer(_surface_signature(surface, settings), dtype=np.uint8).copy()
        return surface
    require_memory((n + 3) ** 3 * 12, "Reference surface sampling")
    threshold = geometry_threshold(settings)
    # Bound temporary field buffers and stay within the native sampler's
    # per-call limit even for automatically refined thin-wall geometries.
    field = sample_priority_grid(settings, axis, dtype=np.float32, threshold=threshold)
    # Close the field with a positive ghost layer. Clamping the scalar field
    # to zero on cube faces creates ambiguous caps; a tiny offset instead
    # creates sliver triangles that are unsuitable for volume meshing.
    field[[0, -1], :, :] = 1
    field[:, [0, -1], :] = 1
    field[:, :, [0, -1]] = 1
    surface = extract_reference_surface(field, spacing, settings.size, backend=surface_backend)
    surface.field_data['_surface_mode'] = ['single']
    surface.field_data['_rustmech_reference'] = np.frombuffer(_surface_signature(surface, settings), dtype=np.uint8).copy()
    return surface


def extract_reference_surface(field, spacing, size, *, backend="flying_edges"):
    """Gitterburg's Flying Edges path, retaining RustMech's closed solid boundary.

    Ambiguous contours fall back to Lewiner's topology-preserving extraction.
    Both paths consume the same signed samples, ghost layer and clipping bounds.
    """
    if backend == "flying_edges":
        vertices, faces = contour_arrays(field, spacing, backend=backend)
        if len(faces):
            surface = _project_reference_surface(vertices, faces, spacing, size)
            if surface.n_cells and _reference_is_closed(surface):
                surface.field_data['_surface_extractor'] = ['flying_edges']
                return surface
        backend = "marching_cubes"
    vertices, faces = contour_arrays(field, spacing, backend=backend)
    surface = _project_reference_surface(vertices, faces, spacing, size)
    surface.field_data['_surface_extractor'] = ['marching_cubes']
    return surface


def contour_arrays(field, spacing=1.0, *, backend="flying_edges"):
    """Raw, uncapped contour for a single owned block of grid cells."""
    if backend == "flying_edges":
        from vtkmodules.vtkCommonDataModel import vtkImageData
        from vtkmodules.vtkFiltersCore import vtkFlyingEdges3D
        from vtkmodules.util.numpy_support import numpy_to_vtk, vtk_to_numpy

        image = vtkImageData()
        image.SetDimensions(*field.shape)
        image.SetSpacing(*((spacing,) * 3))
        values = np.asarray(field, dtype=np.float32).ravel(order="F")
        image.GetPointData().SetScalars(numpy_to_vtk(values, deep=False))
        extractor = vtkFlyingEdges3D()
        extractor.SetInputData(image)
        extractor.SetValue(0, 0.0)
        extractor.ComputeNormalsOff()
        extractor.ComputeGradientsOff()
        extractor.ComputeScalarsOff()
        extractor.Update()
        poly = extractor.GetOutput()
        if poly.GetNumberOfPolys():
            vertices = vtk_to_numpy(poly.GetPoints().GetData()).copy()
            # Flying Edges points toward the negative phase; solids here are negative.
            faces = vtk_to_numpy(poly.GetPolys().GetConnectivityArray()).reshape(-1, 3)[:, ::-1].copy()
            return vertices, faces
        return np.empty((0, 3), np.float32), np.empty((0, 3), np.int32)
    if backend != "marching_cubes":
        raise ValueError(f"Unknown surface extractor: {backend}")
    vertices, faces, _, _ = marching_cubes(
        field, level=0, spacing=(spacing,) * 3,
        allow_degenerate=False, gradient_direction="descent",
    )
    return vertices, faces


def _project_reference_surface(vertices, faces, spacing, size):
    vertices -= spacing
    np.clip(vertices, 0, size, out=vertices)
    # VTK's float32 spacing/origin arithmetic can leave nominal cap vertices
    # one ULP inside the specimen. Put only roundoff-close coordinates back
    # on their exact loading planes before merging and removing degeneracies.
    tolerance = np.finfo(vertices.dtype).eps * max(np.max(size), spacing) * 2
    for start in range(0, len(vertices), 262_144):
        points = vertices[start:start + 262_144]
        points[np.abs(points) <= tolerance] = 0
        np.copyto(points, size, where=np.abs(points - size) <= tolerance, casting="unsafe")
    # Projection can collapse triangles on cap edges. Remove them before the
    # native merge so they cannot become spurious lines or corrupt normals.
    distinct = np.empty(len(faces), dtype=bool)
    for start in range(0, len(faces), 262_144):
        corners = vertices[faces[start:start + 262_144]]
        distinct[start:start + len(corners)] = (
            np.any(corners[:, 0] != corners[:, 1], axis=1)
            & np.any(corners[:, 1] != corners[:, 2], axis=1)
            & np.any(corners[:, 2] != corners[:, 0], axis=1)
        )
    if not np.all(distinct):
        faces = faces[distinct]
    return surface_from_marching_cubes(vertices, faces)


def _surface_signature(surface, settings):
    digest = blake2b(digest_size=32)
    digest.update(repr((settings.structure_family, settings.structure_name,
                        settings.tpms_morphology, settings.random_seed, settings.dimensions,
                        settings.cell_size, settings.solid_fraction, settings.surface_resolution)).encode())
    for values in (surface.points, surface.faces):
        digest.update(memoryview(np.ascontiguousarray(values)).cast('B'))
    return digest.digest()


def geometry_threshold(settings: Settings) -> float:
    """One solid boundary shared by the reference surface and every FE mesh."""
    return _surface_threshold_for_settings(
        settings.structure_family,
        settings.structure_name,
        settings.tpms_morphology,
        settings.random_seed,
        settings.dimensions,
        settings.cell_size,
        settings.solid_fraction,
    )


def surface_from_marching_cubes(vertices, faces):
    try:
        surface = largest_triangle_surface(vertices, faces)
    except ValueError as error:
        if "Degenerate triangles" not in str(error):
            raise
        return _vtk_surface_cleanup(vertices, faces)
    # Lewiner marching cubes with gradient_direction='descent' already returns
    # consistent outward winding for the capped negative-inside scalar field.
    # Exact isovalue coincidences at a coarse cube boundary can nevertheless
    # form open/non-manifold patches; retain VTK's orientation repair for them.
    if not surface.is_manifold:
        return _vtk_surface_cleanup(vertices, faces)
    result = surface.compute_normals(
        consistent_normals=False, auto_orient_normals=False, split_vertices=False
    )
    # The unchanged connectivity was checked just above. Do not scan millions
    # of edges a second time in the caller after computing only normals.
    result._rustmech_fresh_closed_surface = True
    return result


def _reference_is_closed(surface):
    """Use only on a freshly constructed, unmodified reference surface."""
    checked = getattr(surface, '_rustmech_fresh_closed_surface', None)
    return surface.is_manifold if checked is None else checked


def _vtk_surface_cleanup(vertices, faces):
    # Keep VTK's original traversal order when degenerate/open/non-manifold
    # patches require orientation repair; its result can depend on that order.
    triangles = np.column_stack((np.full(len(faces), 3), faces)).ravel()
    surface = pv.PolyData(vertices, triangles).clean(
        polys_to_lines=False, lines_to_points=False
    ).triangulate()
    surface = surface.connectivity(extraction_mode="largest", label_regions=False)
    return surface.compute_normals(auto_orient_normals=True, split_vertices=False)


def generate_volume_mesh(
    settings: Settings, surface: pv.PolyData | None = None, *, on_progress=None
) -> FEMesh:
    require_geometry(settings)
    if settings.mesh_type in DERIVED_SOLID_MESH_TYPES:
        from derived_solid_mesh import derive_solid_mesh

        base = generate_volume_mesh(replace(settings, mesh_type="HEX8"), surface, on_progress=on_progress)
        if on_progress:
            on_progress(f"Assembling {settings.mesh_type} elements…")
        return derive_solid_mesh(base, settings.mesh_type)
    if settings.test_profile != 'custom':
        from standard_tests import make_standard_mesh
        return make_standard_mesh(settings, surface, on_progress)
    if settings.mesh_type in TETRAHEDRAL_MESH_TYPES:
        surface = surface if surface is not None else make_surface(settings, on_progress=on_progress)
        provenance = surface.field_data.get('_rustmech_reference')
        periodic_threshold = (geometry_threshold(settings)
                              if provenance is not None and bytes(provenance) == _surface_signature(surface, settings)
                              else None)
        return generate_tetrahedral_mesh(
            settings,
            surface,
            on_progress=on_progress,
            periodic_threshold=periodic_threshold,
        )
    if on_progress:
        on_progress(f"Sampling geometry for {settings.mesh_type}…")
    n = int(np.ceil(settings.size / settings.mesh_resolution))
    spacing = settings.size / n
    axis = (np.arange(n) + 0.5) * spacing
    if settings.rectangular:
        lengths = np.asarray(settings.dimensions)
        counts = np.maximum(1, np.ceil(lengths / settings.mesh_resolution).astype(int))
        steps = lengths / counts
        axes = [(np.arange(m) + 0.5) * h for m, h in zip(counts, steps)]
        values = sample_box(settings, axes)
    else:
        values = sample_priority_grid(settings, axis)
    # Sample the fixed geometry at voxel centers. Retargeting the solid fraction
    # on each mesh would move the implicit boundary during a convergence study.
    count = int(np.count_nonzero(values < geometry_threshold(settings)))
    retained, removed, original_components = select_voxels(values, count)
    del values
    if on_progress:
        on_progress(f"Assembling {settings.mesh_type} elements…")
    quadratic = settings.mesh_type == "HEX20"
    removed_grid = build_hex_grid(removed, spacing, quadratic=quadratic)
    del removed
    grid, bottom, top = build_hex_grid(
        retained, spacing, quadratic=quadratic, return_boundaries=True
    )
    if settings.rectangular:
        for part in (grid, removed_grid):
            if part.n_points:
                part.points *= steps / spacing
    return FEMesh(
        grid,
        spacing,
        n,
        100 * grid.n_cells / retained.size,
        1,
        bottom,
        top,
        removed_grid,
        int(original_components),
        count,
    )


@dataclass
class Preview:
    surface: pv.PolyData
    mesh: FEMesh | None
    key: tuple


def preview_key(settings: Settings) -> tuple:
    return (
        settings.structure_family,
        settings.structure_name,
        settings.tpms_morphology,
        settings.random_seed,
        settings.size,
        settings.cell_size,
        settings.solid_fraction,
        settings.surface_resolution,
        settings.mesh_resolution,
        settings.mesh_type,
        settings.local_mesh_size,
        settings.tetra_strategy,
        settings.imported_geometry_path,
        settings.imported_geometry_name,
        settings.imported_geometry_role,
        settings.test_profile, settings.specimen_architecture, settings.core_fraction,
        *settings.dimensions[:2],
    )


def build_preview(
    settings: Settings, cached: Preview | None = None, *, on_progress=None, include_mesh=False
) -> Preview:
    """Prepare geometry; create a volume mesh only on an explicit request.

    A compatible existing FE mesh can be retained without doing any meshing.
    Changing its settings invalidates it even for a geometry-only request.
    """
    key = preview_key(settings)
    if cached and cached.key[:8] == key[:8] and cached.key[12:] == key[12:]:
        surface = cached.surface
    else:
        if on_progress:
            on_progress("Preparing the specimen surface…")
        surface = make_surface(settings, on_progress=on_progress)

    def mesh_key(preview_settings_key):
        if preview_settings_key[9] in TETRAHEDRAL_MESH_TYPES:
            return preview_settings_key
        return (*preview_settings_key[:7], *preview_settings_key[8:10], *preview_settings_key[12:])

    mesh = None
    if cached and cached.mesh is not None and mesh_key(cached.key) == mesh_key(key):
        mesh = cached.mesh
    elif include_mesh:
        mesh = generate_volume_mesh(settings, surface, on_progress=on_progress)
    return Preview(surface, mesh, key)
