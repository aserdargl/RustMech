"""Replay an exported specimen in an isolated MAPDL run for contact diagnostics."""
from dataclasses import replace
import argparse
from functools import partial
import json
from pathlib import Path
import sys
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pyvista as pv
import mapdl_backend as backend
from model import FEMesh, load_settings
from nonlinear_compression import extract_curve
from solver_api import AnalysisRequest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--tolerance", type=float)
    parser.add_argument("--stiffness", type=float)
    parser.add_argument("--update", type=int)
    parser.add_argument("--detection", type=int)
    parser.add_argument("--port", type=int, help="Dedicated MAPDL port for concurrent verification.")
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    args.source = args.source.resolve()
    args.destination = args.destination.resolve()
    settings = load_settings(args.source / "settings.json")
    if not args.full:
        settings = replace(settings, compression=settings.compression / settings.substeps,
                           substeps=1, minimum_substeps=1, maximum_substeps=100)
    if args.tolerance is not None:
        settings = replace(settings, platen_penetration_tolerance_factor=args.tolerance)
    if args.stiffness is not None:
        settings = replace(settings, normal_contact_stiffness_factor=args.stiffness)
    grid = pv.read(args.source / "02_MeshQuality" / "mesh.vtu")
    ids = grid.point_data["ansys_node_num"]
    quality = json.loads((args.source / "02_MeshQuality" / "quality_summary.json").read_text())
    mesh = FEMesh(grid, quality["actual_spacing_mm"], quality["subdivisions"],
                  quality["actual_solid_fraction_percent"], 1,
                  ids[np.isclose(grid.points[:, 2], 0)],
                  ids[np.isclose(grid.points[:, 2], settings.size)],
                  pv.UnstructuredGrid(), 1, grid.n_cells)
    configure = backend.configure_contact

    def configured(mapdl, settings, element_type, pair, mode):
        configure(mapdl, settings, element_type, pair, mode)
        if pair in (backend.BOTTOM_PLATEN_PAIR, backend.TOP_PLATEN_PAIR):
            if args.update is not None:
                mapdl.keyopt(element_type, 10, args.update)
            if args.detection is not None:
                mapdl.keyopt(element_type, 4, args.detection)

    backend.configure_contact = configured
    args.destination.mkdir(parents=True, exist_ok=False)
    started = monotonic()
    solver = backend.create_mapdl_backend()
    if args.port is not None:
        solver.launcher = partial(solver.launcher, port=args.port)
    execution = solver.solve(AnalysisRequest(settings, mesh, args.destination))
    report = {"seconds": monotonic() - started, "error": execution.error,
              "source": str(args.source), "update_override": args.update,
              "detection_override": args.detection,
              "tolerance": settings.platen_penetration_tolerance_factor,
              "stiffness_factor": settings.normal_contact_stiffness_factor,
              "compression": settings.compression, "diagnostics": execution.diagnostics}
    if execution.results is not None:
        history, balance = extract_curve(execution.results)
        report.update(sets=execution.results.nsets, balance=balance,
                      final=history[-1].tolist(),
                      completed=bool(np.isclose(history[-1, 0], 1.0) and
                                     np.isclose(history[-1, 1], settings.compression)))
    (args.destination / "verification.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)
    if execution.error or not report.get("completed") or report["balance"]["force_balance_warning"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
