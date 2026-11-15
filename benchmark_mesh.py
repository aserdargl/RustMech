"""Compare mesh kernels with the original NumPy/SciPy implementation (no solver)."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from time import perf_counter

import numpy as np
import pyvista as pv
from scipy import ndimage

from model import FEMesh, HEX_EDGES, Settings
from structure_library import (
    TPMS_FUNCTIONS,
    evaluate_priority_field,
    resolve_family_and_name,
)


CORNERS = np.array(
    [
        (0, 0, 0),
        (1, 0, 0),
        (1, 1, 0),
        (0, 1, 0),
        (0, 0, 1),
        (1, 0, 1),
        (1, 1, 1),
        (0, 1, 1),
    ],
    dtype=np.int32,
)


def reference_field(settings, x, y, z):
    """Original dense TPMS evaluation; other structure fields are unchanged."""
    family, name = resolve_family_and_name(
        settings.structure_family, settings.structure_name
    )
    if family != "tpms":
        return evaluate_priority_field(settings, x, y, z)
    k = 2.0 * np.pi / settings.cell_size
    coords = np.broadcast_arrays(k * x, k * y, k * z)
    raw = np.asarray(TPMS_FUNCTIONS[name](*coords), dtype=np.float64)
    if settings.tpms_morphology == "network_a":
        return raw
    if settings.tpms_morphology == "network_b":
        return -raw
    return np.abs(raw)


def reference_volume_mesh(settings):
    """NumPy/SciPy HEX oracle using the fixed reference geometry boundary."""
    from mesh_engine import geometry_threshold
    from model import require_geometry

    require_geometry(settings)
    n = int(np.ceil(settings.size / settings.mesh_resolution))
    spacing = settings.size / n
    axis = (np.arange(n) + 0.5) * spacing
    values = reference_field(
        settings, axis[:, None, None], axis[None, :, None], axis[None, None, :]
    )
    count = int(np.count_nonzero(values < geometry_threshold(settings)))
    retained, removed, components = reference_select(values, count)
    quadratic = settings.mesh_type == "HEX20"
    removed_grid = reference_grid(removed, spacing, quadratic=quadratic)
    grid = reference_grid(retained, spacing, quadratic=quadratic)
    ids = grid.point_data["ansys_node_num"]
    bottom = ids[np.isclose(grid.points[:, 2], 0, rtol=0, atol=spacing * 1e-8)]
    top = ids[np.isclose(grid.points[:, 2], settings.size, rtol=0, atol=spacing * 1e-8)]
    return FEMesh(
        grid,
        spacing,
        n,
        100 * grid.n_cells / n**3,
        1,
        bottom,
        top,
        removed_grid,
        components,
        count,
    )


def reference_select(values, count):
    """Original stable ranking and six-neighbour component selection."""
    order = np.argsort(values.ravel(), kind="stable")
    solid = np.zeros(values.size, dtype=bool)
    solid[order[:count]] = True
    solid = solid.reshape(values.shape)
    labels, components = ndimage.label(solid, ndimage.generate_binary_structure(3, 1))
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    retained = labels == int(np.argmax(sizes)) if components else np.zeros_like(solid)
    return retained, solid & ~retained, int(components)


def reference_grid(solid, spacing, *, quadratic=False):
    """Original shared-node assembly, preserved as a benchmark/test oracle."""
    count = int(np.count_nonzero(solid))
    if not count:
        return pv.UnstructuredGrid()
    offsets, scale, cell_type = CORNERS, 1, pv.CellType.HEXAHEDRON
    if quadratic:
        offsets = np.vstack(
            (2 * CORNERS, [CORNERS[a] + CORNERS[b] for a, b in HEX_EDGES])
        )
        scale, cell_type = 2, pv.CellType.QUADRATIC_HEXAHEDRON
    shape = tuple(scale * dimension + 1 for dimension in solid.shape)
    origins = np.ravel_multi_index((scale * np.argwhere(solid)).T, shape)
    local_ids = np.ravel_multi_index(offsets.T, shape)
    keys, connectivity = np.unique(origins[:, None] + local_ids, return_inverse=True)
    width = len(offsets)
    grid = pv.UnstructuredGrid(
        np.column_stack(
            (np.full(count, width), connectivity.reshape(-1, width))
        ).ravel(),
        np.full(count, cell_type, np.uint8),
        np.column_stack(np.unravel_index(keys, shape)) * (spacing / scale),
    )
    grid.point_data["ansys_node_num"] = np.arange(1, grid.n_points + 1, dtype=np.int32)
    grid.cell_data["ansys_elem_num"] = np.arange(1, count + 1, dtype=np.int32)
    return grid


def _median_call(function, repeats):
    times = []
    result = None
    for _ in range(repeats):
        result = None
        start = perf_counter()
        result = function()
        times.append(perf_counter() - start)
    return result, statistics.median(times)


def _peak_memory_mib():
    if sys.platform == "win32":
        import ctypes as ct
        from ctypes import wintypes

        class MemoryCounters(ct.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("page_fault_count", wintypes.DWORD),
            ] + [
                (name, ct.c_size_t)
                for name in [
                    "peak_working_set",
                    "working_set",
                    "quota_peak_paged_pool",
                    "quota_paged_pool",
                    "quota_peak_nonpaged_pool",
                    "quota_nonpaged_pool",
                    "pagefile_usage",
                    "peak_pagefile_usage",
                ]
            ]

        counters = MemoryCounters()
        counters.cb = ct.sizeof(counters)
        process_info = ct.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
        process_info.argtypes = [
            wintypes.HANDLE,
            ct.POINTER(MemoryCounters),
            wintypes.DWORD,
        ]
        process_info.restype = wintypes.BOOL
        if not process_info(wintypes.HANDLE(-1), ct.byref(counters), counters.cb):
            raise ct.WinError(ct.get_last_error())
        return counters.peak_working_set / 1024**2
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024**2 if sys.platform == "darwin" else 1024)


def memory_case(n, mesh_type):
    row = dict(subdivisions=n, mesh_type=mesh_type)
    for backend in ["reference", "rust"]:
        output = subprocess.check_output(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--sizes",
                str(n),
                "--types",
                mesh_type,
                "--memory-worker",
                backend,
            ],
            text=True,
        )
        row[f"{backend}_peak_working_set_mib"] = json.loads(output)[
            "peak_working_set_mib"
        ]
    return row


def run_case(n, mesh_type, repeats, baseline_only=False):
    import nonlinear_compression as studio

    settings = replace(Settings(), mesh_resolution=6.0 / n, mesh_type=mesh_type)
    spacing = settings.size / n
    axis = (np.arange(n) + 0.5) * spacing
    values, field_time = _median_call(
        lambda: reference_field(
            settings, axis[:, None, None], axis[None, :, None], axis[None, None, :]
        ),
        repeats,
    )
    count = max(1, round(n**3 * settings.solid_fraction / 100))
    masks, select_time = _median_call(lambda: reference_select(values, count), repeats)
    retained, removed, _ = masks
    quadratic = mesh_type == "HEX20"
    _, assembly_time = _median_call(
        lambda: (
            reference_grid(retained, spacing, quadratic=quadratic),
            reference_grid(removed, spacing, quadratic=quadratic),
        ),
        repeats,
    )
    row = dict(
        subdivisions=n,
        mesh_type=mesh_type,
        retained_elements=int(retained.sum()),
        field_seconds=field_time,
        reference_select_seconds=select_time,
        reference_assembly_seconds=assembly_time,
    )
    if not baseline_only:
        from rust_mesh import select_voxels

        _, native_select_time = _median_call(
            lambda: select_voxels(values, count), repeats
        )
        _, native_assembly_time = _median_call(
            lambda: (
                studio.build_hex_grid(retained, spacing, quadratic=quadratic),
                studio.build_hex_grid(removed, spacing, quadratic=quadratic),
            ),
            repeats,
        )
        _, native_field_time = _median_call(
            lambda: evaluate_priority_field(
                settings, axis[:, None, None], axis[None, :, None], axis[None, None, :]
            ),
            repeats,
        )
        # Warm up both paths, then alternate their order to limit timing bias.
        studio.generate_volume_mesh(settings)
        reference_volume_mesh(settings)
        reference_times, native_times = [], []
        for repetition in range(repeats):
            calls = [
                (reference_volume_mesh, reference_times),
                (studio.generate_volume_mesh, native_times),
            ]
            if repetition % 2:
                calls.reverse()
            for function, times in calls:
                start = perf_counter()
                mesh = function(settings)
                times.append(perf_counter() - start)
                del mesh
        total_time = statistics.median(native_times)
        baseline_total = statistics.median(reference_times)
        row.update(
            rust_select_seconds=native_select_time,
            rust_assembly_seconds=native_assembly_time,
            optimized_field_seconds=native_field_time,
            rust_volume_mesh_seconds=total_time,
            reference_volume_mesh_seconds=baseline_total,
            selection_speedup=select_time / native_select_time,
            assembly_speedup=assembly_time / native_assembly_time,
            volume_speedup=baseline_total / total_time,
        )
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[30, 60, 100])
    parser.add_argument(
        "--types", nargs="+", choices=["HEX8", "HEX20"], default=["HEX8", "HEX20"]
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument(
        "--memory-only",
        action="store_true",
        help="Measure peak process memory in isolated child processes.",
    )
    parser.add_argument(
        "--memory-worker", choices=["reference", "rust"], help=argparse.SUPPRESS
    )
    parser.add_argument("--output", type=Path, default=Path("mesh_benchmark.json"))
    args = parser.parse_args()
    if args.repeats < 1 or any(n < 4 or n > 150 for n in args.sizes):
        parser.error("Use at least one repeat and sizes between 4 and 150.")
    if args.memory_worker:
        import nonlinear_compression as studio

        settings = replace(
            Settings(), mesh_resolution=6.0 / args.sizes[0], mesh_type=args.types[0]
        )
        function = (
            reference_volume_mesh
            if args.memory_worker == "reference"
            else studio.generate_volume_mesh
        )
        mesh = function(settings)
        print(
            json.dumps(
                dict(
                    peak_working_set_mib=_peak_memory_mib(), elements=mesh.grid.n_cells
                )
            )
        )
        return
    report = dict(
        python=platform.python_version(),
        platform=platform.platform(),
        numpy=np.__version__,
        repeats=args.repeats,
        cases=[],
    )
    for n in args.sizes:
        for mesh_type in args.types:
            row = (
                memory_case(n, mesh_type)
                if args.memory_only
                else run_case(n, mesh_type, args.repeats, args.baseline_only)
            )
            report["cases"].append(row)
            print(json.dumps(row), flush=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
