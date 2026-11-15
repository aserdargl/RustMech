"""Compare the original Netgen path, current cold generation and order reuse."""

import argparse
from dataclasses import replace
import json
from pathlib import Path
from statistics import median
from time import perf_counter
from unittest.mock import patch

import numpy as np
import pyvista as pv

from mesh_engine import make_surface
from model import Settings
import netgen_io
from rust_mesh import quadratic_tetra_arrays, tet_metrics
import tetrahedral_mesh as tetra


def main():
    from netgen.meshing import MeshingParameters, SetMessageImportance

    SetMessageImportance(0)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("tetra_acceleration.json"))
    parser.add_argument(
        "--preview-only",
        action="store_true",
        help="Measure actual spawned worker order switches",
    )
    args = parser.parse_args()
    settings = replace(
        Settings(),
        mesh_type="TET10",
        mesh_resolution=0.7,
        local_mesh_size=0.35,
    )
    if args.preview_only:
        from threading import Event
        from mesh_preview_worker import PreviewWorker

        worker, cached, rows = PreviewWorker(), None, []
        try:
            for kind in ("TET4", "TET10", "TET4"):
                stages = []
                start = perf_counter()
                cached = worker.build(
                    replace(settings, mesh_type=kind), cached, stages.append, Event()
                )
                rows.append(
                    dict(
                        mesh_type=kind,
                        seconds=perf_counter() - start,
                        elements=cached.mesh.grid.n_cells,
                        nodes=cached.mesh.grid.n_points,
                        solve_errors=cached.mesh.solve_errors,
                        stages=stages,
                    )
                )
                print(json.dumps(rows[-1]), flush=True)
        finally:
            worker.close()
        args.output.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        return
    surface = make_surface(settings)
    bridge = netgen_io._array_bridge()
    if bridge is None:
        raise RuntimeError("Build the array bridge with Build-NetgenBridge.ps1 first")
    report = dict(
        settings=dict(
            size=settings.size,
            surface_resolution=settings.surface_resolution,
            mesh_resolution=settings.mesh_resolution,
            local_mesh_size=settings.local_mesh_size,
        ),
        repeats=args.repeats,
        before=[],
        after=[],
        warm_cache=[],
    )
    baseline = None

    def measured_boundary(boundary, config, kind, samples, progress, *, legacy=False):
        timing = {}
        start = perf_counter()
        mp = MeshingParameters(maxh=config.mesh_resolution)
        context = patch.object(
            netgen_io, "_array_bridge", return_value=None if legacy else bridge
        )
        with context:
            netgen_io.restrict_h(mp, samples, config.local_mesh_size)
            geometry = netgen_io.geometry_from_surface(boundary)
        timing["transfer_seconds"] = perf_counter() - start
        start = perf_counter()
        native = geometry.GenerateMesh(mp=mp)
        timing["netgen_seconds"] = perf_counter() - start
        start = perf_counter()
        grid = tetra._read_grid(native, kind)
        timing["read_seconds"] = perf_counter() - start
        if legacy:
            start = perf_counter()
            corners = np.asarray(grid.cell_connectivity).reshape(-1, 10)[:, :4]
            points, cells = quadratic_tetra_arrays(
                grid.points[: int(corners.max()) + 1], corners
            )
            timing["rust_tet10_seconds"] = perf_counter() - start
            # Compare cell geometry, independent of midpoint node numbering.
            np.testing.assert_array_equal(
                points[cells],
                grid.points[np.asarray(grid.cell_connectivity).reshape(-1, 10)],
            )
            timing["verification_seconds"] = perf_counter() - start
        measured_boundary.timing = timing
        return grid

    for repetition in range(args.repeats):
        # Alternate order to limit drift from CPU warm-up and thermal changes.
        for mode in ("before", "after") if repetition % 2 == 0 else ("after", "before"):
            start = perf_counter()
            if mode == "before":
                boundary = tetra._clean_boundary(
                    surface, settings.size, settings.size * 1e-6
                )
                groups = (
                    tetra._platen_samples(boundary, settings),
                    tetra._thin_wall_samples(
                        boundary, settings.local_mesh_size, settings.surface_resolution
                    ),
                )
                grid = measured_boundary(
                    boundary, settings, "TET10", groups, None, legacy=True
                )
                volume = tetra._solid_volume(grid, "TET10")
            else:
                with patch.object(
                    tetra, "_mesh_boundary", side_effect=measured_boundary
                ):
                    mesh = tetra.generate_tetrahedral_mesh(
                        settings, surface, use_cache=False
                    )
                grid = mesh.grid
                volume = mesh.fraction * settings.size**3 / 100
            elapsed = perf_counter() - start
            elapsed -= measured_boundary.timing.get("verification_seconds", 0.0)
            corners = np.asarray(grid.cell_connectivity).reshape(-1, 10)[:, :4]
            geometry = np.array(grid.points[corners])
            if baseline is None:
                baseline = geometry
            delta = (
                float(np.max(np.abs(geometry - baseline)))
                if geometry.shape == baseline.shape
                else None
            )
            quality_grid = pv.UnstructuredGrid()
            quality_grid.points = grid.points
            quality_grid.SetCells(
                int(pv.CellType.TETRA),
                pv.CellArray.from_regular_cells(np.ascontiguousarray(corners)),
            )
            quality = np.asarray(
                quality_grid.cell_quality("scaled_jacobian").cell_data[
                    "scaled_jacobian"
                ]
            )
            row = dict(
                repetition=repetition + 1,
                seconds=elapsed,
                **measured_boundary.timing,
                elements=grid.n_cells,
                nodes=grid.n_points,
                volume=volume,
                max_corner_delta=delta,
                jacobian_min=float(quality.min()),
                jacobian_mean=float(quality.mean()),
                jacobian_p01=float(np.quantile(quality, 0.01)),
                invalid_elements=int(np.count_nonzero(tet_metrics(grid)[0] <= 0)),
            )
            report[mode].append(row)
            print(json.dumps(dict(mode=mode, **row)), flush=True)
            args.output.write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )

    # Populate through the real production path, then time both order switches.
    tetra.clear_tetrahedral_cache()
    tetra.generate_tetrahedral_mesh(settings, surface)
    for kind in ("TET4", "TET10", "TET4", "TET10", "TET4", "TET10"):
        start = perf_counter()
        mesh = tetra.generate_tetrahedral_mesh(
            replace(settings, mesh_type=kind), surface
        )
        report["warm_cache"].append(
            dict(
                mesh_type=kind,
                seconds=perf_counter() - start,
                elements=mesh.grid.n_cells,
                nodes=mesh.grid.n_points,
            )
        )
    report["medians"] = {
        mode: {
            field: median(row[field] for row in report[mode])
            for field in (
                "seconds",
                "transfer_seconds",
                "netgen_seconds",
                "read_seconds",
            )
        }
        for mode in ("before", "after")
    }
    report["medians"]["tet4_reuse_seconds"] = median(
        row["seconds"] for row in report["warm_cache"] if row["mesh_type"] == "TET4"
    )
    report["medians"]["tet10_reuse_seconds"] = median(
        row["seconds"] for row in report["warm_cache"] if row["mesh_type"] == "TET10"
    )
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["medians"]), flush=True)


if __name__ == "__main__":
    main()
