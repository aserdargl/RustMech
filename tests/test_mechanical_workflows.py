"""Mechanical workflow contracts at the local and TRUBA entry points."""

import builtins
import csv
from dataclasses import asdict
import json
from pathlib import Path

import numpy as np
import pytest

from analysis_process import run_single_analysis
from mesh_engine import generate_volume_mesh
from model import Settings
from remote_worker import run_operation
from solver_api import SolverExecution, get_solver_capabilities


@pytest.fixture(autouse=True)
def block_desktop_imports(monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.split(".")[0] in {"nonlinear_compression", "PySide6", "pyvistaqt"}:
            pytest.fail(f"Mechanical worker imported {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)


class SavedSolution:
    nsets = 1
    time_values = np.array([1.0])
    stress_definition = "fixture stress"

    def __init__(self, request, force_imbalance):
        self.mesh = request.mesh
        self.travel = request.settings.compression
        self.force_imbalance = force_imbalance

    def top_platen_displacement(self, index):
        return -self.travel

    def platen_reactions(self, index):
        return 2.0 - self.force_imbalance, -2.0

    def displacement(self, index, ids):
        np.testing.assert_array_equal(ids, self.mesh.grid.point_data["ansys_node_num"])
        displacement = np.zeros((len(ids), 3))
        displacement[:, 2] = -self.travel * self.mesh.grid.points[:, 2]
        return displacement

    def equivalent_stress(self, index, ids):
        return np.full(len(ids), 2.0)


class MechanicalBackend:
    def __init__(self, settings, *, error=None, force_imbalance=0.0):
        self.capabilities = get_solver_capabilities(settings.solver_backend)
        self.error = error
        self.force_imbalance = force_imbalance
        self.request = None

    def solve(self, request):
        self.request = request
        return SolverExecution(
            results=SavedSolution(request, self.force_imbalance),
            error=self.error,
            solve_attempted=True,
            diagnostics={"mesh_import_verified": True},
        )


def assert_report(folder, settings, status, *, images):
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == status
    assert summary["solver"] == settings.solver_backend
    assert summary["solve_attempted"] is True
    assert summary["licensed_solve_executed"] is (settings.solver_backend == "ansys_mapdl")
    assert summary["solver_diagnostics"] == {"mesh_import_verified": True}
    assert summary["graphs"]["final_force_N"] == 2.0
    assert summary["graphs"]["external_compression_work_J"] == pytest.approx(
        settings.compression / 1000
    )
    assert summary["fields"]["maximum_displacement_mm"] == pytest.approx(settings.compression)
    assert summary["fields"]["maximum_von_mises_MPa"] == 2.0
    assert (folder / "01_Geometry" / "gyroid.stl").is_file()
    assert (folder / "02_MeshQuality" / "mesh.vtu").is_file()
    assert (folder / "03_Results" / "deformed_shape.vtu").is_file()
    with (folder / "04_Graphs" / "force_displacement.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [float(row["Compression_Force_N"]) for row in rows] == [0.0, 2.0]
    assert (folder / "04_Graphs" / "force_displacement.png").is_file() is images
    return summary


def specimen(solver):
    return Settings(
        size=1, cell_size=1, mesh_type="HEX8", mesh_resolution=0.1, compression=0.1,
        solver_backend=solver, material_model="linear_elastic", self_contact_mode="disabled",
    )


@pytest.mark.parametrize(
    ("error", "imbalance", "status"),
    [
        (None, 0.0, "completed"),
        (None, 0.1, "completed_with_force_balance_warning"),
        ("solver interrupted", 0.0, "partial_solution"),
    ],
)
@pytest.mark.parametrize("custom_folder", [False, True])
def test_local_analysis_reports_supplied_mesh(monkeypatch, tmp_path, error, imbalance, status, custom_folder):
    settings = specimen("ansys_mapdl")
    mesh = generate_volume_mesh(settings)
    backend = MechanicalBackend(settings, error=error, force_imbalance=imbalance)
    monkeypatch.setattr("solver_api.create_solver_backend", lambda *args: backend)
    monkeypatch.setattr("mechanical_analysis.ROOT", tmp_path)
    # Keep numerical and PNG graph output real without requiring an OpenGL window.
    monkeypatch.setattr("mechanical_results.save_image", lambda *args, **kwargs: None)

    selected = tmp_path / "User named results" if custom_folder else None
    if selected is not None:
        selected.mkdir()
    if error:
        with pytest.raises(RuntimeError, match="partial results are labelled"):
            run_single_analysis(settings, mesh, result_folder=selected)
        if selected is None:
            folder, = (tmp_path / "HumanResults").iterdir()
        else:
            folder = selected
    else:
        folder = Path(run_single_analysis(settings, mesh, result_folder=selected))

    assert backend.request.mesh is mesh
    if selected is not None:
        assert folder == selected
        assert backend.request.run_directory == selected / "Raw"
        assert not (tmp_path / "HumanResults").exists()
    else:
        assert backend.request.run_directory == tmp_path / "results" / folder.name
    summary = assert_report(folder, settings, status, images=True)
    assert summary["reactions"]["force_balance_warning"] is bool(imbalance)
    if error:
        assert summary["solver_error"] == error
        assert (folder / "error.txt").is_file()


@pytest.mark.parametrize("operation", ["mechanical", "doe_solve", "convergence_level"])
def test_truba_worker_reports_mechanics(monkeypatch, tmp_path, operation):
    import mechanical_analysis

    monkeypatch.chdir(tmp_path)
    local_root = tmp_path / "local"
    monkeypatch.setattr(mechanical_analysis, "ROOT", local_root)
    settings = specimen("kratos")
    mesh = generate_volume_mesh(settings)
    backend = MechanicalBackend(settings)
    monkeypatch.setattr("remote_worker.probe", lambda request: {"kratos": True, "mesh_types": ["HEX8"]})
    monkeypatch.setattr("remote_worker._local_kratos", lambda: backend)
    monkeypatch.setattr("mesh_engine.generate_volume_mesh", lambda _: mesh)
    output = tmp_path / "output"
    request = dict(operation=operation, settings=asdict(settings), row=7, max_elements=0, selected=[0])
    payload = run_operation(request, output)
    assert mechanical_analysis.ROOT == local_root

    if operation == "mechanical":
        run_folder = payload["run_folder"]
    elif operation == "doe_solve":
        assert payload["row"]["r_idx"] == 7
        assert payload["row"]["status"] == "Completed"
        run_folder = payload["row"]["run_folder"]
    else:
        assert payload["level"]["status"] == "completed"
        assert payload["level"]["elements"] == mesh.grid.n_cells
        run_folder = payload["level"]["folder"]
    folder = Path(run_folder)
    assert folder.parent == output / "HumanResults"
    assert backend.request.run_directory == output / "results" / folder.name
    assert backend.request.mesh is mesh
    summary = assert_report(folder, settings, "completed", images=False)
    if operation == "mechanical":
        assert payload["summary"] == {**summary, "run_folder": run_folder}
