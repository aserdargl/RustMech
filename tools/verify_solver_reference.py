"""Run an isolated, licensed reference without changing workspace settings.

The cube checks the solver/contact/result chain against F = E*A*delta/L
(Poisson ratio zero). The gyroid uses the application's geometry and defaults
for frictional platen and self-contact, at a small prescribed displacement.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import psutil
import pyvista as pv

from mapdl_backend import create_mapdl_backend
from mesh_engine import generate_volume_mesh
from model import FEMesh, Settings, loading_target_reached, write_json
from nonlinear_compression import extract_curve
from solver_api import AnalysisRequest


def cube_mesh() -> FEMesh:
    coordinates = np.meshgrid(*([np.linspace(0., 1., 5)] * 3), indexing="ij")
    grid = pv.StructuredGrid(*coordinates).cast_to_unstructured_grid()
    ids = np.arange(1, grid.n_points + 1)
    grid.point_data["ansys_node_num"] = ids
    grid.cell_data["ansys_elem_num"] = np.arange(1, grid.n_cells + 1)
    return FEMesh(grid, .25, 4, 100., 1, ids[np.isclose(grid.points[:, 2], 0)],
                  ids[np.isclose(grid.points[:, 2], 1)], pv.UnstructuredGrid(), 1, grid.n_cells)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("cube", "gyroid"), default="cube")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compression", type=float)
    parser.add_argument("--mesh-size", type=float)
    parser.add_argument("--substeps", type=int)
    args = parser.parse_args()
    # Inventory only. The backend explicitly starts a new local instance and
    # PyMAPDL selects an available port; existing sessions receive no commands.
    active = [p.pid for p in psutil.process_iter(["name"])
              if (p.info["name"] or "").lower() in ("ansys.exe", "ansys252.exe")]
    folder = args.output or (Path(__file__).resolve().parents[1] / "results" /
                             ("Reference_" + args.case + "_" + datetime.now().strftime("%Y%m%d_%H%M%S")))
    folder = folder.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    settings = (Settings(size=1., cell_size=1., mesh_resolution=.25, compression=.001,
                         material_model="linear_elastic", poisson_ratio=0., cores=1,
                         substeps=10, self_contact_mode="disabled") if args.case == "cube" else
                Settings(mesh_resolution=.3, compression=.006, cores=1, substeps=10))
    overrides = {name: value for name, value in (
        ("compression", args.compression), ("mesh_resolution", args.mesh_size),
        ("substeps", args.substeps)) if value is not None}
    settings = replace(settings, **overrides)
    mesh = cube_mesh() if args.case == "cube" else generate_volume_mesh(settings)
    mesh.grid.save(folder / "specimen.vtu")
    write_json(folder / "settings.json", asdict(settings))
    report = {"case": args.case, "status": "solving", "settings": asdict(settings),
              "nodes": mesh.grid.n_points, "elements": mesh.grid.n_cells,
              "existing_ansys_pids": active}
    write_json(folder / "reference.json", report)
    print(f"Reference: {folder}", flush=True)
    execution = create_mapdl_backend().solve(AnalysisRequest(settings, mesh, folder))
    report.update(error=execution.error, diagnostics=execution.diagnostics,
                  solve_attempted=execution.solve_attempted)
    passed = False
    try:
        if execution.results is None:
            raise RuntimeError(execution.error or "No solver results")
        result = execution.results
        history, balance = extract_curve(result)
        np.savetxt(folder / "history.csv", history, delimiter=",", comments="",
                   header="time,compression_mm,top_force_N,bottom_force_N,residual_N")
        target_reached = loading_target_reached(settings, history[-1, 1], history[-1, 2], history[-1, 0])
        report.update(reactions=balance, reached_compression_mm=float(history[-1, 1]),
                      final_time=float(history[-1, 0]), final_force_N=float(history[-1, 2]),
                      target_reached=target_reached, saved_sets=result.nsets)
        passed = not execution.error and target_reached and not balance["force_balance_warning"]
        if args.case == "cube":
            expected = settings.young_modulus * settings.size * settings.compression
            relative_error = abs(history[-1, 2] - expected) / expected
            report.update(expected_force_N=expected, relative_force_error=float(relative_error))
            passed = passed and relative_error < .01
    except Exception as exc:
        report["verification_error"] = str(exc)
    report["status"] = "passed" if passed else "failed"
    write_json(folder / "reference.json", report)
    print(json.dumps(report, indent=2), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
