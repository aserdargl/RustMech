"""Small licensed MAPDL checks, isolated from the user's running study."""
from dataclasses import replace
import json
from pathlib import Path
import sys
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pyvista as pv
from mapdl_backend import create_mapdl_backend
from model import FEMesh, Settings
from nonlinear_compression import extract_curve, export_graphs
from mesh_engine import generate_volume_mesh
from solver_api import AnalysisRequest


def main():
    root = Path(__file__).resolve().parents[1] / "design" / "loading-contact-verification" / datetime.now().strftime("%Y%m%d_%H%M%S")
    root.mkdir(parents=True)
    axes = np.linspace(0, 1, 5)
    grid = pv.StructuredGrid(*np.meshgrid(axes, axes, axes, indexing="ij")).cast_to_unstructured_grid()
    ids = np.arange(1, grid.n_points + 1)
    grid.point_data["ansys_node_num"] = ids
    grid.cell_data["ansys_elem_num"] = np.arange(1, grid.n_cells + 1)
    mesh = FEMesh(grid, .25, 4, 100., 1, ids[np.isclose(grid.points[:, 2], 0)],
                  ids[np.isclose(grid.points[:, 2], 1)], pv.UnstructuredGrid(), 1, grid.n_cells)
    base = Settings(size=1, cell_size=1, mesh_resolution=.25, compression=.001,
                    material_model="linear_elastic", cores=1, substeps=10,
                    self_contact_mode="disabled", bottom_platen_contact="frictionless",
                    top_platen_contact="frictionless", contact_damping_factor=0)
    report = []
    for mode in ("displacement", "force"):
        settings = replace(base, loading_mode=mode, applied_force=200.)
        directory = root / mode
        directory.mkdir()
        execution = create_mapdl_backend().solve(AnalysisRequest(settings, mesh, directory))
        if execution.error or execution.results is None:
            raise RuntimeError(f"{mode}: {execution.error}; see {directory}")
        history, reactions = extract_curve(execution.results)
        graphs = export_graphs(history, settings, directory, mesh, images=False)
        assert graphs["completed"], graphs
        assert not reactions["force_balance_warning"], reactions
        assert np.isclose(graphs["final_force_N"], 200, rtol=.02), graphs
        assert np.isclose(graphs["reached_compression_mm"], .001, rtol=.02), graphs
        report.append({"mode": mode, "sets": execution.results.nsets, "graphs": graphs,
                       "reactions": reactions, "diagnostics": execution.diagnostics})
        print(json.dumps(report[-1]), flush=True)
    (root / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    # A cellular specimen also exercises frictional self-contact and plasticity.
    settings = Settings(size=1, cell_size=.5, mesh_resolution=.05, compression=.01,
                        cores=1, substeps=40)
    mesh = generate_volume_mesh(settings)
    directory = root / "gyroid"
    directory.mkdir()
    execution = create_mapdl_backend().solve(AnalysisRequest(settings, mesh, directory))
    if execution.error or execution.results is None:
        raise RuntimeError(f"gyroid: {execution.error}; see {directory}")
    history, reactions = extract_curve(execution.results)
    graphs = export_graphs(history, settings, directory, mesh, images=False)
    assert graphs["completed"] and not reactions["force_balance_warning"], (graphs, reactions)
    report.append({"mode": "gyroid_frictional_self_contact", "sets": execution.results.nsets,
                   "graphs": graphs, "reactions": reactions})
    (root / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report[-1]), flush=True)
    print(root, flush=True)


if __name__ == "__main__":
    main()
