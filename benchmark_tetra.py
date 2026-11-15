"""Compare thin-wall sampling with the original PyVista-array loop."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from time import perf_counter

import numpy as np
from vtkmodules.vtkCommonCore import reference
from vtkmodules.vtkCommonDataModel import vtkGenericCell, vtkStaticCellLocator

from model import Settings


def reference_thin_walls(surface, local_size, surface_spacing):
    reach = 5 * local_size
    locator = vtkStaticCellLocator()
    locator.SetDataSet(surface)
    locator.BuildLocator()
    points, normals, cell_normals = (
        surface.points,
        surface.point_normals,
        surface.cell_normals,
    )
    epsilon = min(local_size, surface_spacing) * 1e-4
    cell = vtkGenericCell()
    samples = []
    for point, normal in zip(points, normals):
        direction = -normal.astype(float)
        point = point.astype(float)
        start, end = point + epsilon * direction, point + reach * direction
        hit, hit_cell = [0.0, 0.0, 0.0], reference(0)
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


def main():
    from nonlinear_compression import generate_volume_mesh, make_surface
    from tetrahedral_mesh import _clean_boundary, _thin_wall_samples

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full",
        action="store_true",
        help="Also generate complete TET4/TET10 meshes with Netgen.",
    )
    args = parser.parse_args()
    settings = replace(
        Settings(),
        mesh_type="TET4",
        mesh_resolution=0.7,
        local_mesh_size=0.35,
    )
    surface = make_surface(settings)
    boundary = _clean_boundary(surface, settings.size, settings.size * 1e-6)
    report = dict(surface_triangles=surface.n_cells)
    for name, function in [
        ("reference", reference_thin_walls),
        ("optimized", _thin_wall_samples),
    ]:
        start = perf_counter()
        samples = function(
            boundary, settings.local_mesh_size, settings.surface_resolution
        )
        report[f"{name}_thin_wall_seconds"] = perf_counter() - start
        if name == "reference":
            expected = np.asarray(samples)
        else:
            np.testing.assert_array_equal(np.asarray(samples), expected)
            report["samples"] = len(samples)
    report["samples_identical"] = True
    report["thin_wall_speedup"] = (
        report["reference_thin_wall_seconds"] / report["optimized_thin_wall_seconds"]
    )
    print(json.dumps(report), flush=True)
    if args.full:
        report["meshes"] = []
        for mesh_type in ["TET4", "TET10"]:
            start = perf_counter()
            stages = []

            def on_progress(stage):
                stages.append(dict(stage=stage, elapsed_seconds=perf_counter() - start))

            mesh = generate_volume_mesh(
                replace(settings, mesh_type=mesh_type), surface, on_progress=on_progress
            )
            report["meshes"].append(
                dict(
                    mesh_type=mesh_type,
                    seconds=perf_counter() - start,
                    elements=mesh.grid.n_cells,
                    nodes=mesh.grid.n_points,
                    solve_errors=mesh.solve_errors,
                    stages=stages,
                )
            )
            print(json.dumps(report["meshes"][-1]), flush=True)
    Path("tetra_benchmark.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
