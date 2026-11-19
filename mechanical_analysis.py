"""Qt-independent entry points for mechanical runs and result recovery."""
from __future__ import annotations

import json
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pyvista as pv

from analysis_process import report_analysis_step
from mechanical_results import export_field_results, export_geometry, export_graphs, extract_curve
from mesh_engine import generate_volume_mesh
from model import FEMesh, ROOT, Settings, require_settings, settings_from_dict, write_json
from solver_api import AnalysisRequest, SolverBackend, create_solver_backend


@dataclass(frozen=True)
class RunPaths:
    human: Path
    raw: Path

    @classmethod
    def create(cls, offline: bool = False, output_directory=None, *, result_root=None) -> RunPaths:
        stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
        tag = ("OfflineCheck_" if offline else "Run_") + stamp
        root = ROOT if result_root is None else Path(result_root)
        paths = (cls(Path(output_directory) / tag, Path(output_directory) / tag / "Raw")
                 if output_directory else cls(root / "HumanResults" / tag, root / "results" / tag))
        for path in [
            paths.raw,
            *(
                paths.human / name
                for name in (
                    "01_Geometry",
                    "02_MeshQuality",
                    "03_Results",
                    "04_Graphs",
                )
            ),
        ]:
            path.mkdir(parents=True, exist_ok=False)
        return paths


def run_analysis(
    settings: Settings,
    mesh: FEMesh,
    backend: SolverBackend,
    images: bool = True,
    output_directory=None,
    *,
    result_root=None,
) -> Path:
    total_stages = 6
    report_analysis_step("Validating analysis settings and mesh…", 0, total_stages)
    require_settings(settings)
    errors = backend.capabilities.validate(settings) + mesh.solve_errors
    if errors:
        raise ValueError("\n".join(errors))
    paths = RunPaths.create(output_directory=output_directory, result_root=result_root)
    report = {
        "status": "preparing",
        "licensed_solve_executed": False,
        "solver": backend.capabilities.identifier,
        "settings": asdict(settings),
    }
    from standard_tests import profile_report
    report['test_profile']=profile_report(settings)
    status = paths.human / "summary.json"
    print(f"Results: {paths.human}", flush=True)
    try:
        write_json(status, report)
        report_analysis_step("Exporting geometry and mesh…", 1, total_stages)
        report["mesh"] = export_geometry(settings, mesh, paths, images=images)
        report["status"] = "solving"
        write_json(status, report)
        print(
            f"Using the existing FE mesh: {mesh.grid.n_points} nodes, "
            f"{mesh.grid.n_cells} elements; no specimen remeshing.\n"
            f"Starting {backend.capabilities.display_name}...",
            flush=True,
        )
        report_analysis_step(f"Solving · {backend.capabilities.display_name}…", 2, total_stages)
        execution = backend.solve(AnalysisRequest(settings, mesh, paths.raw))
        report["solve_attempted"] = execution.solve_attempted
        report["licensed_solve_executed"] = execution.solve_attempted and backend.capabilities.identifier == "ansys_mapdl"
        report["solver_diagnostics"] = execution.diagnostics
        if execution.error:
            report["solver_error"] = execution.error
        if execution.results is None:
            raise RuntimeError(
                execution.error or "Solver did not return results."
            )
        result = execution.results
        report_analysis_step("Extracting reactions and response curves…", 3, total_stages)
        history, report["reactions"] = extract_curve(result)
        report_analysis_step("Exporting graphs…", 4, total_stages)
        report["graphs"] = export_graphs(
            history, settings, paths.human / "04_Graphs", mesh=mesh, images=images, result=result
        )
        report_analysis_step("Exporting result fields…", 5, total_stages)
        report["fields"] = export_field_results(
            result, mesh, paths.human / "03_Results", images=images
        )
        if execution.error or not report["graphs"]["completed"]:
            report["status"] = "partial_solution"
        elif report["reactions"]["force_balance_warning"]:
            report["status"] = "completed_with_force_balance_warning"
        else:
            report["status"] = "completed"
        write_json(status, report)
        print(
            f"Analysis status: {report['status']}\nResults: {paths.human}",
            flush=True,
        )
        if report["status"] == "partial_solution":
            raise RuntimeError(
                "Analysis stopped before clean completion; partial results are labelled."
            )
        return paths.human
    except Exception as exc:
        if report["status"] != "partial_solution":
            report["status"] = "failed"
        report["error"] = str(exc)
        write_json(status, report)
        (paths.human / "error.txt").write_text(
            traceback.format_exc(), encoding="utf-8"
        )
        raise


def resume_kratos_analysis(record_path,remote_auth=None,job_marker=None):
    from kratos_backend import KratosBackend
    record=json.loads(Path(record_path).read_text(encoding="utf-8"))
    local=Path(record["local_directory"])
    payload=json.loads((local/"input.json").read_text(encoding="utf-8"))
    settings=settings_from_dict(payload["settings"])
    grid=pv.read(local/"submitted_mesh.vtu")
    meta=payload["mesh_metadata"]
    ids=np.asarray(grid.point_data["ansys_node_num"])
    z=np.asarray(grid.points)[:,2]
    mesh=FEMesh(grid,float(meta["spacing"]),meta["subdivisions"],float(meta["fraction"]),int(meta["components"]),
                ids[np.isclose(z,z.min(),rtol=0,atol=max(settings.size*1e-9,1e-12))],
                ids[np.isclose(z,z.max(),rtol=0,atol=max(settings.size*1e-9,1e-12))],
                pv.UnstructuredGrid(),int(meta["original_components"]),int(meta["candidate_elements"]))
    class ResumedBackend(KratosBackend):
        def solve(self,request):
            return self.resume(AnalysisRequest(request.settings,request.mesh,local))
    return run_analysis(settings,mesh,ResumedBackend(remote_auth,job_marker))


def run_batch_experiment(
    settings: Settings,
    mesh: FEMesh,
    backend_factory=None,
    output_directory=None,
    *,
    result_root=None,
) -> dict:
    if backend_factory is None:
        backend_factory = lambda: create_solver_backend(settings.solver_backend)
    options = {"output_directory": output_directory} if output_directory else {}
    if result_root is not None:
        options["result_root"] = result_root
    human_path = run_analysis(settings, mesh, backend_factory(), images=False, **options)
    summary_data = json.loads(
        (human_path / "summary.json").read_text(encoding="utf-8")
    )
    summary_data["run_folder"] = str(human_path)
    return summary_data


def offline_check(settings: Settings, images: bool = True) -> Path:
    mesh = generate_volume_mesh(settings)
    paths = RunPaths.create(offline=True)
    report = {
        "status": "offline_mesh_check",
        "licensed_solve_executed": False,
    }
    try:
        report["mesh"] = export_geometry(settings, mesh, paths, images)
        report["status"] = (
            "passed" if not mesh.solve_errors else "mesh_not_solve_ready"
        )
        write_json(paths.human / "offline_report.json", report)
    except Exception as exc:
        report.update(status="failed", error=str(exc))
        write_json(paths.human / "offline_report.json", report)
        raise
    print(json.dumps(report, indent=2))
    print(
        f"OFFLINE ONLY — no licensed ANSYS solve. Outputs: {paths.human}"
    )
    if mesh.solve_errors:
        raise ValueError("\n".join(mesh.solve_errors))
    return paths.human
