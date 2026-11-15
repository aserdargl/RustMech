"""Compare first Netgen generation in independent fresh processes."""

import argparse
import hashlib
import json
from pathlib import Path
from statistics import median
import subprocess
import sys

import numpy as np
import pyvista as pv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--cpu", type=int, default=2)
    parser.add_argument(
        "--library",
        type=Path,
        default=Path("native/bin/netgen_kernel/rustmech_netgen.dll"),
    )
    parser.add_argument(
        "--output", type=Path, default=Path("netgen_cold_comparison.json")
    )
    parser.add_argument("--family", default="tpms")
    parser.add_argument("--name", default="Gyroid")
    parser.add_argument("--surface-spacing", type=float, default=0.15)
    parser.add_argument(
        "--fixture", type=Path, help="Reuse an existing frozen input fixture"
    )
    args = parser.parse_args()
    project = Path(__file__).resolve().parent
    folder = project / "benchmarks" / "netgen_kernel" / args.output.stem
    folder.mkdir(parents=True, exist_ok=True)
    # Freeze the exact arrays once. A concurrent UI/geometry change must not
    # contaminate a comparison of the two meshing kernels.
    from benchmark_netgen_kernel import MeasuredSettings
    from mesh_engine import make_surface
    from tetrahedral_mesh import _clean_boundary, _platen_samples, _thin_wall_samples

    settings = MeasuredSettings(
        mesh_type="TET4",
        mesh_resolution=0.7,
        local_mesh_size=0.35,
        reference_spacing=args.surface_spacing,
        structure_family=args.family,
        structure_name=args.name,
    )
    fixture_folder = args.fixture.resolve() if args.fixture else folder
    if not args.fixture:
        boundary = _clean_boundary(
            make_surface(settings), settings.size, settings.size * 1e-6
        )
        boundary.save(folder / "boundary.vtp")
        platens = _platen_samples(boundary, settings)
        walls = np.asarray(
            _thin_wall_samples(
                boundary, settings.local_mesh_size, settings.surface_resolution
            )
        ).reshape(-1, 3)
        np.savez_compressed(
            folder / "refinement.npz", platens=np.asarray(platens), walls=walls
        )
    rows = {"stock": [], "private": []}
    geometries = {}
    for trial in range(args.repeats):
        for backend in ("stock", "private") if trial % 2 == 0 else ("private", "stock"):
            output = folder / f"{backend}_{trial + 1}.json"
            command = [
                sys.executable,
                str(project / "benchmark_netgen_kernel.py"),
                "--backend",
                backend,
                "--cpu",
                str(args.cpu),
                "--output",
                str(output),
                "--family",
                args.family,
                "--name",
                args.name,
                "--surface-spacing",
                str(args.surface_spacing),
                "--fixture",
                str(fixture_folder),
            ]
            if backend == "private":
                command.extend(("--library", str(args.library.resolve())))
            completed = subprocess.run(
                command, cwd=project, capture_output=True, text=True, timeout=240
            )
            (folder / f"{backend}_{trial + 1}.log").write_text(
                completed.stdout + completed.stderr, encoding="utf-8"
            )
            completed.check_returncode()
            row = json.loads(output.read_text())[0]
            rows[backend].append(row)
            geometries[backend] = pv.read(output.with_suffix(".vtu"))
            print(
                json.dumps(
                    dict(
                        trial=trial + 1,
                        backend=backend,
                        seconds=row["seconds"],
                        netgen_seconds=row["netgen_seconds"],
                    )
                ),
                flush=True,
            )
            if len(geometries) == 2:
                before, after = geometries["stock"], geometries["private"]
                np.testing.assert_array_equal(before.points, after.points)
                np.testing.assert_array_equal(
                    before.cell_connectivity, after.cell_connectivity
                )
    report = dict(
        repeats=args.repeats,
        fresh_process_each_run=True,
        cpu=args.cpu,
        geometry=args.name,
        surface_spacing=args.surface_spacing,
        boundary_sha256=hashlib.sha256(
            (fixture_folder / "boundary.vtp").read_bytes()
        ).hexdigest(),
        refinement_sha256=hashlib.sha256(
            (fixture_folder / "refinement.npz").read_bytes()
        ).hexdigest(),
        coordinates_and_connectivity_bit_identical=True,
        measurements=rows,
    )
    report["medians"] = {
        backend: {
            name: median(row[name] for row in rows[backend])
            for name in ("seconds", "netgen_seconds", "cpu_seconds")
        }
        for backend in rows
    }
    report["netgen_speedup"] = (
        report["medians"]["stock"]["netgen_seconds"]
        / report["medians"]["private"]["netgen_seconds"]
    )
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["medians"]), flush=True)


if __name__ == "__main__":
    main()
