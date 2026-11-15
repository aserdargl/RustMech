"""Compare Netgen thread counts with an unchanged ASCII boundary, without ANSYS."""

import json
import tempfile
from dataclasses import replace
from pathlib import Path
from time import perf_counter

from mesh_engine import make_surface
from model import Settings
from tetrahedral_mesh import (
    _clean_boundary,
    _platen_samples,
    _read_grid,
    _solid_volume,
    _thin_wall_samples,
)


def main():
    from netgen.meshing import MeshingParameters, SetMessageImportance
    from netgen.stl import STLGeometry
    from pyngcore import SetNumThreads, TaskManager

    settings = replace(
        Settings(),
        mesh_type="TET4",
        mesh_resolution=0.7,
        local_mesh_size=0.35,
    )
    boundary = _clean_boundary(
        make_surface(settings), settings.size, settings.size * 1e-6
    )
    samples = (
        _platen_samples(boundary, settings),
        _thin_wall_samples(
            boundary, settings.local_mesh_size, settings.surface_resolution
        ),
    )
    report = []
    SetMessageImportance(0)
    with tempfile.TemporaryDirectory() as folder:
        for binary, threads in [(False, 1), (False, 8)]:
            start = perf_counter()
            path = Path(folder) / "surface.stl"
            boundary.save(path, binary=binary)
            parameters = MeshingParameters(maxh=settings.mesh_resolution)
            for points in samples:
                for x, y, z in points:
                    parameters.RestrictH(
                        float(x), float(y), float(z), settings.local_mesh_size
                    )
            geometry = STLGeometry(str(path))
            prep = perf_counter() - start
            SetNumThreads(threads)
            start = perf_counter()
            with TaskManager():
                mesh = geometry.GenerateMesh(mp=parameters)
            meshing = perf_counter() - start
            grid = _read_grid(mesh, "TET4")
            row = dict(
                binary=binary,
                threads=threads,
                preparation_seconds=prep,
                meshing_seconds=meshing,
                file_bytes=path.stat().st_size,
                elements=grid.n_cells,
                volume=_solid_volume(grid, "TET4"),
            )
            report.append(row)
            print(json.dumps(row), flush=True)
    Path("netgen_modes.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
