"""Cold first-mesh measurements; every repetition constructs a fresh Netgen geometry."""

import argparse
import ctypes as ct
from dataclasses import dataclass
import json
from pathlib import Path
from time import perf_counter, process_time

import numpy as np
import pyvista as pv

from mesh_engine import make_surface
from model import Settings
from netgen_kernel import generate_arrays
from tetrahedral_mesh import (
    _clean_boundary,
    _platen_samples,
    _thin_wall_samples,
    _solid_volume,
    _read_grid,
)


@dataclass(frozen=True)
class MeasuredSettings(Settings):
    """Fixed reference surface for kernel comparisons, independent of UI sampling."""

    reference_spacing: float = 0.15

    @property
    def surface_resolution(self):
        return self.reference_spacing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("stock", "private"), required=True)
    parser.add_argument("--library", type=Path)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name", default="Gyroid")
    parser.add_argument("--surface-spacing", type=float, default=0.15)
    parser.add_argument(
        "--fixture", type=Path, help="Use a frozen boundary/refinement fixture"
    )
    parser.add_argument(
        "--family", default="tpms", choices=("tpms", "lattice", "porous")
    )
    parser.add_argument(
        "--cpu", type=int, help="Pin this benchmark process to one logical CPU"
    )
    args = parser.parse_args()
    if args.cpu is not None:
        if not 0 <= args.cpu < 64:
            raise ValueError("CPU index must be between 0 and 63")
        windows = ct.WinDLL("kernel32", use_last_error=True)
        windows.GetCurrentProcess.restype = ct.c_void_p
        windows.SetProcessAffinityMask.argtypes = [ct.c_void_p, ct.c_size_t]
        if not windows.SetProcessAffinityMask(
            windows.GetCurrentProcess(), 1 << args.cpu
        ):
            raise ct.WinError(ct.get_last_error())
    settings = MeasuredSettings(
        mesh_type="TET4",
        reference_spacing=args.surface_spacing,
        mesh_resolution=0.7,
        local_mesh_size=0.35,
        structure_name=args.name,
        structure_family=args.family,
    )
    if args.fixture:
        boundary = pv.read(args.fixture / "boundary.vtp")
        with np.load(args.fixture / "refinement.npz") as fixture:
            samples = (fixture["platens"], fixture["walls"])
    else:
        boundary = _clean_boundary(
            make_surface(settings), settings.size, settings.size * 1e-6
        )
        samples = (
            _platen_samples(boundary, settings),
            _thin_wall_samples(
                boundary, settings.local_mesh_size, settings.surface_resolution
            ),
        )
    rows = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for iteration in range(args.repeats):
        timers = {}
        start, cpu = perf_counter(), process_time()
        if args.backend == "private":
            points, cells = generate_arrays(
                boundary, settings, samples, library_path=args.library, timings=timers
            )
            grid = pv.UnstructuredGrid()
            grid.points = points
            grid.SetCells(
                int(pv.CellType.TETRA), pv.CellArray.from_regular_cells(cells)
            )
        else:
            from netgen.meshing import MeshingParameters, SetMessageImportance
            from netgen_io import geometry_from_surface, restrict_h
            import pyngcore

            SetMessageImportance(0)
            parameters = MeshingParameters(maxh=settings.mesh_resolution)
            restrict_h(parameters, samples, settings.local_mesh_size)
            geometry = geometry_from_surface(boundary)
            pyngcore.ResetTimers()
            netgen_start = perf_counter()
            mesh = geometry.GenerateMesh(mp=parameters)
            timers["RustMech::GenerateMesh"] = perf_counter() - netgen_start
            grid = _read_grid(mesh, "TET4")
            timers.update(
                {
                    row["name"]: row["time"]
                    for row in pyngcore.Timers()
                    if row["time"] > 0.05
                }
            )
        elapsed, cpu_elapsed = perf_counter() - start, process_time() - cpu
        quality = np.asarray(
            grid.cell_quality("scaled_jacobian").cell_data["scaled_jacobian"]
        )
        row = dict(
            iteration=iteration + 1,
            backend=args.backend,
            structure_name=args.name,
            surface_triangles=boundary.n_cells,
            seconds=elapsed,
            cpu_seconds=cpu_elapsed,
            netgen_seconds=timers.get("RustMech::GenerateMesh"),
            elements=grid.n_cells,
            nodes=grid.n_points,
            volume=_solid_volume(grid, "TET4"),
            jacobian_min=float(quality.min()),
            jacobian_mean=float(quality.mean()),
            jacobian_p01=float(np.quantile(quality, 0.01)),
            timers=timers,
        )
        rows.append(row)
        grid.save(args.output.with_suffix(".vtu"))
        args.output.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
