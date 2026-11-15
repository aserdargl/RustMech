"""NumPy buffers shared with the Rust mesh kernel through a versioned C ABI.

CDLL releases the GIL during native work. Rust writes directly into Python-owned
arrays; no Python-version-specific extension or mesh serialization is needed.
"""

from __future__ import annotations

import ctypes as ct
import os
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
import pyvista as pv


_U8_PTR = ct.POINTER(ct.c_uint8)
_F64_PTR = ct.POINTER(ct.c_double)
_I64_PTR = ct.POINTER(ct.c_int64)
_I32_PTR = ct.POINTER(ct.c_int32)
MAX_AXIS = 150


@lru_cache(maxsize=1)
def _kernel():
    filename = {"win32": "rustmech_core.dll", "darwin": "librustmech_core.dylib"}.get(
        sys.platform, "librustmech_core.so"
    )
    root = Path(__file__).resolve().parent
    roots = [root]
    if getattr(sys, "frozen", False):
        roots.insert(0, Path(sys.executable).resolve().parent)
    configured = os.environ.get("RUSTMECH_CORE_LIBRARY")
    candidates = (
        [Path(configured)]
        if configured
        else [folder / "native" / "bin" / filename for folder in roots]
    )
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        raise RuntimeError(
            "Rust mesh kernel is missing. Run .\\Build-MeshKernel.ps1 from the project folder "
            "and include native/bin in executable packages."
        )
    try:
        library = ct.CDLL(str(path.resolve()))
        library.rustmech_core_abi_version.argtypes = []
        library.rustmech_core_abi_version.restype = ct.c_uint32
        if library.rustmech_core_abi_version() != 4:
            raise RuntimeError(
                "Unsupported Rust mesh ABI; rebuild with .\\Build-MeshKernel.ps1."
            )
        library.rustmech_core_last_error.argtypes = []
        library.rustmech_core_last_error.restype = ct.c_char_p
        library.rustmech_select_voxels.argtypes = [
            _F64_PTR,
            ct.c_size_t,
            ct.c_size_t,
            ct.c_size_t,
            ct.c_size_t,
            ct.c_size_t,
            _U8_PTR,
            _U8_PTR,
            ct.POINTER(ct.c_uint32),
        ]
        library.rustmech_select_voxels.restype = ct.c_int32
        library.rustmech_hex_plan_new.argtypes = [
            _U8_PTR,
            ct.c_size_t,
            ct.c_size_t,
            ct.c_size_t,
            ct.c_size_t,
            ct.c_uint8,
            ct.POINTER(ct.c_size_t),
        ]
        library.rustmech_hex_plan_new.restype = ct.c_void_p
        library.rustmech_hex_plan_write.argtypes = [
            ct.c_void_p,
            ct.c_double,
            _F64_PTR,
            ct.c_size_t,
            _I64_PTR,
            ct.c_size_t,
        ]
        library.rustmech_hex_plan_write.restype = ct.c_int32
        library.rustmech_hex_plan_free.argtypes = [ct.c_void_p]
        library.rustmech_hex_plan_free.restype = None
        library.rustmech_hex_contacts.argtypes = [
            ct.c_void_p,
            _I32_PTR,
            ct.c_size_t,
            _I32_PTR,
            ct.c_size_t,
        ]
        library.rustmech_hex_contacts.restype = ct.c_int32
        library.rustmech_surface_plan_new.argtypes = [
            _F64_PTR,
            ct.c_size_t,
            _I32_PTR,
            ct.c_size_t,
            ct.POINTER(ct.c_size_t),
        ]
        library.rustmech_surface_plan_new.restype = ct.c_void_p
        library.rustmech_surface_plan_write.argtypes = [
            ct.c_void_p,
            _I32_PTR,
            ct.c_size_t,
            _I64_PTR,
            ct.c_size_t,
        ]
        library.rustmech_surface_plan_write.restype = ct.c_int32
        library.rustmech_surface_plan_free.argtypes = [ct.c_void_p]
        library.rustmech_surface_plan_free.restype = None
        library.rustmech_tet_plan_new.argtypes = [
            _I64_PTR,
            ct.c_size_t,
            ct.c_size_t,
            ct.POINTER(ct.c_size_t),
        ]
        library.rustmech_tet_plan_new.restype = ct.c_void_p
        library.rustmech_tet_plan_write.argtypes = [
            ct.c_void_p,
            _F64_PTR,
            _F64_PTR,
            ct.c_size_t,
            _I64_PTR,
            ct.c_size_t,
        ]
        library.rustmech_tet_plan_write.restype = ct.c_int32
        library.rustmech_tet_plan_free.argtypes = [ct.c_void_p]
        library.rustmech_tet_plan_free.restype = None
        library.rustmech_triangle_normals.argtypes = [
            _F64_PTR,
            ct.c_size_t,
            _I64_PTR,
            ct.c_size_t,
            _F64_PTR,
        ]
        library.rustmech_triangle_normals.restype = ct.c_int32
        library.rustmech_tpms.argtypes = [
            ct.c_uint32,
            _F64_PTR,
            ct.c_size_t,
            ct.c_size_t,
            ct.c_size_t,
            _F64_PTR,
            ct.c_size_t,
        ]
        library.rustmech_tpms.restype = ct.c_int32
        library.rustmech_graph_field.argtypes = [
            _F64_PTR,
            ct.c_size_t,
            ct.c_size_t,
            ct.c_size_t,
            _F64_PTR,
            ct.c_size_t,
            _F64_PTR,
            ct.c_size_t,
        ]
        library.rustmech_graph_field.restype = ct.c_int32
        library.rustmech_interpolate.argtypes = [
            _I64_PTR,
            _F64_PTR,
            ct.c_size_t,
            ct.c_size_t,
            ct.c_size_t,
            _F64_PTR,
            ct.c_size_t,
            _F64_PTR,
            ct.c_size_t,
        ]
        library.rustmech_interpolate.restype = ct.c_int32
        library.rustmech_map_indices.argtypes = [
            _I64_PTR,
            ct.c_size_t,
            _I64_PTR,
            ct.c_size_t,
            _I64_PTR,
        ]
        library.rustmech_map_indices.restype = ct.c_int32
        library.rustmech_write_apdl.argtypes = [
            ct.c_char_p,
            ct.c_size_t,
            _F64_PTR,
            _I64_PTR,
            ct.c_size_t,
            _I64_PTR,
            _I64_PTR,
            ct.c_size_t,
            ct.c_size_t,
            ct.c_int32,
        ]
        library.rustmech_write_apdl.restype = ct.c_int32
        library.rustmech_tet_metrics.argtypes = [
            _F64_PTR,
            ct.c_size_t,
            _I64_PTR,
            ct.c_size_t,
            ct.c_size_t,
            _F64_PTR,
            _F64_PTR,
        ]
        library.rustmech_tet_metrics.restype = ct.c_int32
        library.rustmech_von_mises.argtypes = [_F64_PTR, ct.c_size_t, _F64_PTR]
        library.rustmech_von_mises.restype = ct.c_int32
    except (OSError, AttributeError) as exc:
        raise RuntimeError(
            f"Cannot load Rust mesh kernel at {path}; rebuild it: {exc}"
        ) from exc
    return library


def _require_shape(array):
    if array.ndim != 3 or any(not 1 <= n <= MAX_AXIS for n in array.shape):
        raise ValueError(
            f"Voxel input must have three dimensions, each between 1 and {MAX_AXIS}."
        )


def _native_error(library):
    return ValueError(
        library.rustmech_core_last_error().decode("utf-8", errors="replace")
    )


def select_voxels(values: np.ndarray, count: int) -> tuple[np.ndarray, np.ndarray, int]:
    """Select a stable solid fraction and keep its largest face-connected part."""
    values = np.require(values, dtype=np.float64, requirements=["C", "A"])
    _require_shape(values)
    if (
        isinstance(count, (bool, np.bool_))
        or not isinstance(count, (int, np.integer))
        or not 0 <= count <= values.size
    ):
        raise ValueError(
            "Voxel count must be an integer between zero and the sample count."
        )
    retained = np.empty(values.shape, dtype=bool)
    removed = np.empty(values.shape, dtype=bool)
    components = ct.c_uint32()
    library = _kernel()
    status = library.rustmech_select_voxels(
        values.ctypes.data_as(_F64_PTR),
        values.size,
        *values.shape,
        count,
        retained.ctypes.data_as(_U8_PTR),
        removed.ctypes.data_as(_U8_PTR),
        ct.byref(components),
    )
    if status:
        raise _native_error(library)
    return retained, removed, components.value


def build_hex_grid(
    solid: np.ndarray,
    spacing: float,
    *,
    quadratic: bool = False,
    return_boundaries: bool = False,
):
    """Build VTK HEX8/HEX20 in Rust, retaining the original sorted node numbering."""
    solid = np.require(solid, dtype=bool, requirements=["C", "A"])
    _require_shape(solid)
    if not np.isfinite(spacing) or spacing <= 0:
        raise ValueError("Mesh spacing must be a positive finite number.")
    library = _kernel()
    counts = (ct.c_size_t * 4)()
    plan = library.rustmech_hex_plan_new(
        solid.ctypes.data_as(_U8_PTR),
        solid.size,
        *solid.shape,
        bool(quadratic),
        counts,
    )
    if not plan:
        raise _native_error(library)
    try:
        point_count, cell_count, bottom_count, top_count = counts
        if cell_count == 0:
            empty = pv.UnstructuredGrid()
            return (
                (empty, np.empty(0, np.int32), np.empty(0, np.int32))
                if return_boundaries
                else empty
            )
        points = np.empty((point_count, 3), dtype=np.float64)
        width = 20 if quadratic else 8
        cells = np.empty((cell_count, width), dtype=np.int64)
        status = library.rustmech_hex_plan_write(
            plan,
            spacing,
            points.ctypes.data_as(_F64_PTR),
            points.size,
            cells.ctypes.data_as(_I64_PTR),
            cells.size,
        )
        if status:
            raise _native_error(library)
        if return_boundaries:
            bottom = np.empty(bottom_count, dtype=np.int32)
            top = np.empty(top_count, dtype=np.int32)
            if library.rustmech_hex_contacts(
                plan,
                bottom.ctypes.data_as(_I32_PTR),
                bottom.size,
                top.ctypes.data_as(_I32_PTR),
                top.size,
            ):
                raise _native_error(library)
    finally:
        library.rustmech_hex_plan_free(plan)
    cell_type = (
        pv.CellType.QUADRATIC_HEXAHEDRON if quadratic else pv.CellType.HEXAHEDRON
    )
    # Supply modern VTK connectivity directly, avoiding legacy cell-format
    # import/export and its extra copies of every element's node indices.
    grid = pv.UnstructuredGrid()
    grid.points = points
    grid.SetCells(int(cell_type), pv.CellArray.from_regular_cells(cells))
    grid.point_data["ansys_node_num"] = np.arange(1, point_count + 1, dtype=np.int32)
    grid.cell_data["ansys_elem_num"] = np.arange(1, cell_count + 1, dtype=np.int32)
    return (grid, bottom, top) if return_boundaries else grid


def largest_triangle_surface(points, faces):
    """Merge exactly coincident points and keep the largest point-connected part."""
    original_points = np.asarray(points)
    points = np.require(original_points, dtype=np.float64, requirements=["C", "A"])
    faces = np.asarray(faces)
    if (
        points.ndim != 2
        or points.shape[1] != 3
        or faces.ndim != 2
        or faces.shape[1] != 3
    ):
        raise ValueError("Expected triangular surface coordinates and connectivity.")
    if faces.dtype != np.int32 and (np.any(faces < 0) or np.any(faces >= len(points))):
        raise ValueError("Invalid surface triangle index.")
    faces = np.require(faces, dtype=np.int32, requirements=["C", "A"])
    library = _kernel()
    counts = (ct.c_size_t * 2)()
    plan = library.rustmech_surface_plan_new(
        points.ctypes.data_as(_F64_PTR),
        len(points),
        faces.ctypes.data_as(_I32_PTR),
        len(faces),
        counts,
    )
    if not plan:
        raise _native_error(library)
    try:
        order = np.empty(counts[0], dtype=np.int32)
        triangles = np.empty((counts[1], 3), dtype=np.int64)
        if library.rustmech_surface_plan_write(
            plan,
            order.ctypes.data_as(_I32_PTR),
            order.size,
            triangles.ctypes.data_as(_I64_PTR),
            triangles.size,
        ):
            raise _native_error(library)
    finally:
        library.rustmech_surface_plan_free(plan)
    surface = pv.PolyData()
    surface.points = (
        original_points
        if original_points.dtype in (np.dtype("float32"), np.dtype("float64"))
        else points
    )[order]
    surface.SetPolys(pv.CellArray.from_regular_cells(triangles))
    return surface


def _axes(x, y, z):
    coordinates = tuple(np.asarray(value, dtype=np.float64) for value in (x, y, z))
    expected = ((None, 1, 1), (1, None, 1), (1, 1, None))
    for coordinate, pattern in zip(coordinates, expected):
        if coordinate.ndim != 3 or any(
            size != 1 for size, match in zip(coordinate.shape, pattern) if match == 1
        ):
            return None
    return tuple(np.ascontiguousarray(value.ravel()) for value in coordinates)


def tpms_field(kind, x, y, z, k):
    axes = _axes(x, y, z)
    if axes is None:
        return None
    tables = []
    for axis in axes:
        angle = k * axis
        tables.append(
            np.column_stack(
                (np.sin(angle), np.cos(angle), np.sin(2.0 * angle), np.cos(2.0 * angle))
            )
        )
    tables = np.require(
        np.concatenate(tables), dtype=np.float64, requirements=["C", "A"]
    )
    output = np.empty(tuple(len(axis) for axis in axes), dtype=np.float64)
    library = _kernel()
    if library.rustmech_tpms(
        kind,
        tables.ctypes.data_as(_F64_PTR),
        *output.shape,
        output.ctypes.data_as(_F64_PTR),
        output.size,
    ):
        raise _native_error(library)
    return output


def graph_field(x, y, z, edges):
    axes = _axes(x, y, z)
    if axes is None:
        return None
    period = 2.0 * np.pi
    wrapped = np.ascontiguousarray(
        np.concatenate([np.mod(axis, period) / period for axis in axes])
    )
    edges = np.require(edges, dtype=np.float64, requirements=["C", "A"])
    if edges.ndim != 3 or edges.shape[1:] != (2, 3):
        raise ValueError("Lattice edges must have shape (edge_count, 2, 3).")
    output = np.empty(tuple(len(axis) for axis in axes), dtype=np.float64)
    library = _kernel()
    if library.rustmech_graph_field(
        wrapped.ctypes.data_as(_F64_PTR),
        *output.shape,
        edges.ctypes.data_as(_F64_PTR),
        len(edges),
        output.ctypes.data_as(_F64_PTR),
        output.size,
    ):
        raise _native_error(library)
    return output


def interpolate_field(x, y, z, axis, reference, size):
    axes = _axes(x, y, z)
    if axes is None:
        return None
    axis = np.asarray(axis, dtype=np.float64)
    if axis.ndim != 1 or len(axis) < 2 or not np.all(np.diff(axis) > 0):
        raise ValueError("Reference coordinates must be strictly increasing.")
    reference = np.require(reference, dtype=np.float64, requirements=["C", "A"])
    if reference.shape != (len(axis),) * 3:
        raise ValueError("Porous reference dimensions do not match its coordinates.")
    indices, weights = [], []
    for coordinates in axes:
        coordinates = np.clip(coordinates, 0.0, size)
        index = np.clip(
            np.searchsorted(axis, coordinates, side="right") - 1, 0, len(axis) - 2
        )
        indices.append(index)
        weights.append((coordinates - axis[index]) / (axis[index + 1] - axis[index]))
    indices = np.require(
        np.concatenate(indices), dtype=np.int64, requirements=["C", "A"]
    )
    weights = np.require(
        np.concatenate(weights), dtype=np.float64, requirements=["C", "A"]
    )
    output = np.empty(tuple(len(coordinates) for coordinates in axes), dtype=np.float64)
    library = _kernel()
    if library.rustmech_interpolate(
        indices.ctypes.data_as(_I64_PTR),
        weights.ctypes.data_as(_F64_PTR),
        *output.shape,
        reference.ctypes.data_as(_F64_PTR),
        len(axis),
        output.ctypes.data_as(_F64_PTR),
        output.size,
    ):
        raise _native_error(library)
    return output


def map_indices(source_ids, target_ids):
    source = np.require(
        np.asarray(source_ids).ravel(), dtype=np.int64, requirements=["C", "A"]
    )
    target = np.require(
        np.asarray(target_ids).ravel(), dtype=np.int64, requirements=["C", "A"]
    )
    output = np.empty(len(target), dtype=np.int64)
    library = _kernel()
    if library.rustmech_map_indices(
        source.ctypes.data_as(_I64_PTR),
        source.size,
        target.ctypes.data_as(_I64_PTR),
        target.size,
        output.ctypes.data_as(_I64_PTR),
    ):
        raise _native_error(library)
    return output


def write_apdl(path, points, node_ids, connectivity, element_ids, element_type):
    points = np.require(points, dtype=np.float64, requirements=["C", "A"])
    node_ids = np.require(node_ids, dtype=np.int64, requirements=["C", "A"])
    connectivity = np.require(connectivity, dtype=np.int64, requirements=["C", "A"])
    element_ids = np.require(element_ids, dtype=np.int64, requirements=["C", "A"])
    if (
        points.shape != (node_ids.size, 3)
        or node_ids.ndim != 1
        or element_ids.ndim != 1
        or connectivity.ndim != 2
        or connectivity.shape[0] != element_ids.size
    ):
        raise ValueError("Inconsistent APDL mesh array dimensions.")
    encoded = str(Path(path)).encode("utf-8")
    library = _kernel()
    if library.rustmech_write_apdl(
        encoded,
        len(encoded),
        points.ctypes.data_as(_F64_PTR),
        node_ids.ctypes.data_as(_I64_PTR),
        node_ids.size,
        connectivity.ctypes.data_as(_I64_PTR),
        element_ids.ctypes.data_as(_I64_PTR),
        element_ids.size,
        connectivity.shape[1],
        element_type,
    ):
        raise _native_error(library)


def tet_metrics(grid):
    points = np.require(grid.points, dtype=np.float64, requirements=["C", "A"])
    cells = np.require(
        grid.cell_connectivity.reshape(grid.n_cells, -1),
        dtype=np.int64,
        requirements=["C", "A"],
    )
    determinants = np.empty(grid.n_cells, dtype=np.float64)
    ratios = np.empty(grid.n_cells, dtype=np.float64)
    library = _kernel()
    if library.rustmech_tet_metrics(
        points.ctypes.data_as(_F64_PTR),
        len(points),
        cells.ctypes.data_as(_I64_PTR),
        len(cells),
        cells.shape[1],
        determinants.ctypes.data_as(_F64_PTR),
        ratios.ctypes.data_as(_F64_PTR),
    ):
        raise _native_error(library)
    return determinants, ratios


def quadratic_tetra_arrays(points, corners):
    """Build VTK TET10 connectivity and shared straight-edge nodes in Rust."""
    points = np.require(points, dtype=np.float64, requirements=["C", "A"])
    corners = np.require(corners, dtype=np.int64, requirements=["C", "A"])
    if (
        points.ndim != 2
        or points.shape[1] != 3
        or corners.ndim != 2
        or corners.shape[1] != 4
    ):
        raise ValueError("Expected points (n,3) and tetrahedra (m,4).")
    library = _kernel()
    counts = (ct.c_size_t * 2)()
    plan = library.rustmech_tet_plan_new(
        corners.ctypes.data_as(_I64_PTR), len(corners), len(points), counts
    )
    if not plan:
        raise _native_error(library)
    try:
        output = np.empty((counts[0], 3), dtype=np.float64)
        cells = np.empty((counts[1], 10), dtype=np.int64)
        if library.rustmech_tet_plan_write(
            plan,
            points.ctypes.data_as(_F64_PTR),
            output.ctypes.data_as(_F64_PTR),
            len(output),
            cells.ctypes.data_as(_I64_PTR),
            cells.size,
        ):
            raise _native_error(library)
        return output, cells
    finally:
        library.rustmech_tet_plan_free(plan)


def triangle_normals(points, faces):
    """Double precision facet normals with the original ASCII writer's order."""
    points = np.require(points, dtype=np.float64, requirements=["C", "A"])
    faces = np.require(faces, dtype=np.int64, requirements=["C", "A"])
    if (
        points.ndim != 2
        or points.shape[1] != 3
        or faces.ndim != 2
        or faces.shape[1] != 3
    ):
        raise ValueError("Expected triangle points and indices with three columns.")
    output = np.empty((len(faces), 3), dtype=np.float64)
    library = _kernel()
    if library.rustmech_triangle_normals(
        points.ctypes.data_as(_F64_PTR),
        len(points),
        faces.ctypes.data_as(_I64_PTR),
        len(faces),
        output.ctypes.data_as(_F64_PTR),
    ):
        raise _native_error(library)
    return output


def von_mises(tensors):
    tensors = np.require(tensors, dtype=np.float64, requirements=["C", "A"])
    if tensors.ndim != 2 or tensors.shape[1] != 6:
        raise ValueError("Stress tensors must have six components per node.")
    output = np.empty(len(tensors), dtype=np.float64)
    library = _kernel()
    if library.rustmech_von_mises(
        tensors.ctypes.data_as(_F64_PTR), len(tensors), output.ctypes.data_as(_F64_PTR)
    ):
        raise _native_error(library)
    return output
