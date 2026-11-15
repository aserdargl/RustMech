"""Measure the current release against the next optimization, without ANSYS."""

import argparse
import gc
import json
import statistics
import tempfile
from dataclasses import replace
from pathlib import Path
from time import perf_counter


def reference_surface_cleanup(vertices, faces):
    import numpy as np
    import pyvista as pv

    triangles = np.column_stack((np.full(len(faces), 3), faces)).ravel()
    surface = pv.PolyData(vertices, triangles).clean(
        polys_to_lines=False, lines_to_points=False
    ).triangulate()
    surface = surface.connectivity(extraction_mode="largest", label_regions=False)
    return surface.compute_normals(auto_orient_normals=True, split_vertices=False)


def measure(function, repeats=5):
    times = []
    for _ in range(repeats):
        gc.collect()
        start = perf_counter()
        value = function()
        times.append(perf_counter() - start)
        del value
    return statistics.median(times)


def main():
    import nonlinear_compression as studio
    from mapdl_backend import write_apdl_mesh

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = studio.Settings()
    result = {"surface_default_seconds": measure(lambda: studio.make_surface(settings))}
    for size in [100, 150]:
        for kind in ["HEX8", "HEX20"]:
            config = replace(settings, mesh_resolution=6.0 / size, mesh_type=kind)
            result[f"{kind}_{size}_seconds"] = measure(
                lambda: studio.generate_volume_mesh(config)
            )
    mesh = studio.generate_volume_mesh(
        replace(settings, mesh_resolution=0.1, mesh_type="HEX20")
    )
    with tempfile.TemporaryDirectory() as folder:
        result["apdl_hex20_seconds"] = measure(
            lambda: write_apdl_mesh(mesh, Path(folder) / "mesh.inp"), 3
        )
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
