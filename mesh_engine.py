"""Geometry and mesh orchestration without GUI or plotting imports."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from hashlib import blake2b

import numpy as np
import pyvista as pv
from skimage.measure import marching_cubes

from model import FEMesh, Settings, TETRAHEDRAL_MESH_TYPES, require_geometry
from rust_mesh import build_hex_grid, largest_triangle_surface, select_voxels
from structure_library import evaluate_priority_field
from periodic_sampling import sample_priority_grid
from tetrahedral_mesh import generate_tetrahedral_mesh


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
    size: float,
    period: float,
    fraction: float,
) -> float:
    probe_settings = Settings(
        size=size,
        cell_size=period,
        solid_fraction=fraction,
        structure_family=structure_family,
        structure_name=structure_name,
        tpms_morphology=tpms_morphology,
        random_seed=random_seed,
    )
    axis = (np.arange(101) + 0.5) * size / 101
    field = evaluate_priority_field(
        probe_settings,
        axis[:, None, None],
        axis[None, :, None],
        axis[None, None, :],
    )
    return float(np.quantile(field, fraction / 100))


def make_surface(settings: Settings) -> pv.PolyData:
    require_geometry(settings)
    if settings.test_profile != 'custom':
        from standard_tests import make_standard_surface
        return make_standard_surface(settings)
    if settings.imported_geometry_path:
        from imported_geometry import read_surface
        surface = read_surface(settings.imported_geometry_path)
        if not np.isclose(surface.bounds[4], 0, atol=settings.size * 1e-6) or not np.isclose(surface.bounds[5], settings.size, atol=settings.size * 1e-6):
            raise ValueError('Imported geometry height has changed. Import it again.')
        return surface
    n = max(4, int(np.ceil(settings.size / settings.surface_resolution)))
    spacing = settings.size / n
    axis = np.arange(-1, n + 2, dtype=float) * spacing
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
    vertices, faces, _, _ = marching_cubes(
        field,
        level=0,
        spacing=(spacing,) * 3,
        allow_degenerate=False,
        gradient_direction="descent",
    )
    vertices -= spacing
    np.clip(vertices, 0, settings.size, out=vertices)
    # Projection can collapse triangles on cap edges. Remove them before the
    # native merge so they cannot become spurious lines or corrupt normals.
    corners = vertices[faces]
    distinct = (
        np.any(corners[:, 0] != corners[:, 1], axis=1)
        & np.any(corners[:, 1] != corners[:, 2], axis=1)
        & np.any(corners[:, 2] != corners[:, 0], axis=1)
    )
    faces = faces[distinct]
    surface = surface_from_marching_cubes(vertices, faces)
    surface.field_data['_rustmech_reference'] = np.frombuffer(_surface_signature(surface, settings), dtype=np.uint8).copy()
    return surface


def _surface_signature(surface, settings):
    digest = blake2b(digest_size=32)
    digest.update(repr((settings.structure_family, settings.structure_name,
                        settings.tpms_morphology, settings.random_seed, settings.size,
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
        settings.size,
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
    return surface.compute_normals(
        consistent_normals=False, auto_orient_normals=False, split_vertices=False
    )


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
    if settings.test_profile != 'custom':
        from standard_tests import make_standard_mesh
        return make_standard_mesh(settings, surface, on_progress)
    if settings.mesh_type in TETRAHEDRAL_MESH_TYPES:
        surface = surface if surface is not None else make_surface(settings)
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
    values = sample_priority_grid(settings, axis)
    # Sample the fixed geometry at voxel centers. Retargeting the solid fraction
    # on each mesh would move the implicit boundary during a convergence study.
    count = int(np.count_nonzero(values < geometry_threshold(settings)))
    retained, removed, original_components = select_voxels(values, count)
    if on_progress:
        on_progress(f"Assembling {settings.mesh_type} elements…")
    quadratic = settings.mesh_type == "HEX20"
    removed_grid = build_hex_grid(removed, spacing, quadratic=quadratic)
    grid, bottom, top = build_hex_grid(
        retained, spacing, quadratic=quadratic, return_boundaries=True
    )
    return FEMesh(
        grid,
        spacing,
        n,
        100 * grid.n_cells / n**3,
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
    mesh: FEMesh
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
        settings.test_profile, settings.specimen_architecture, settings.core_fraction,
    )


def build_preview(
    settings: Settings, cached: Preview | None = None, *, on_progress=None
) -> Preview:
    key = preview_key(settings)
    if cached and cached.key[:8] == key[:8] and cached.key[13:] == key[13:]:
        surface = cached.surface
    else:
        if on_progress:
            on_progress("Preparing the specimen surface…")
        surface = make_surface(settings)

    def mesh_key(preview_settings_key):
        if preview_settings_key[9] in TETRAHEDRAL_MESH_TYPES:
            return preview_settings_key
        return (*preview_settings_key[:7], *preview_settings_key[8:10], *preview_settings_key[13:])

    mesh = (
        cached.mesh
        if cached and mesh_key(cached.key) == mesh_key(key)
        else generate_volume_mesh(settings, surface, on_progress=on_progress)
    )
    return Preview(surface, mesh, key)
