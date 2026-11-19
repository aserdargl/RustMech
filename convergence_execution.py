"""Mesh and solve a refinement level without loading the desktop workspace."""

import csv
from dataclasses import dataclass, field, replace
import math
from pathlib import Path
from time import monotonic


METRICS = (
    ("Peak force (N)", "maximum_force_N"),
    ("Effective modulus (MPa)", "effective_young_modulus_MPa"),
    ("SEA (J/g)", "specific_energy_absorption_J_g"),
)


def response_metrics(settings):
    from standard_tests import kind
    if kind(settings) != 'compression':
        return ((('Actuator travel (mm)','reached_actuator_travel_mm') if settings.loading_mode=='force' else METRICS[0]),
                ('Measured deformation (mm)','final_measured_deformation_mm'),('External work (J)','external_work_J'))
    # Comparing prescribed force with itself would falsely establish convergence.
    return ((("Displacement (mm)", "reached_compression_mm"), *METRICS[1:])
            if settings.loading_mode == "force" else METRICS)


@dataclass
class Level:
    settings: object
    kind: str = "Refinement"
    elements: int = 0
    mesh_seconds: float | None = None
    solve_seconds: float | None = None
    metrics: list = field(default_factory=lambda: [None] * len(METRICS))
    status: str = "Meshing"
    folder: str = ""
    final_strain: float | None = None
    reference: int | None = None
    differences: list = field(default_factory=list)
    final_force: float | None = None


def read_curve(folder, size):
    path = Path(folder) / "04_Graphs" / "force_displacement.csv"
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return [(100 * float(row.get('Actuator_travel_mm',row.get('Compression_mm'))) / size, float(row.get('Force_N',row.get('Compression_Force_N'))))
                for row in csv.DictReader(stream)]


def valid_metrics(level, selected=range(len(METRICS))):
    try:
        return all(math.isfinite(float(level.metrics[i])) for i in selected)
    except (TypeError, ValueError):
        return False


def solve_level(settings, mesh_generator, solver, publish=None, stop=None,
                max_elements=0, selected=range(len(METRICS))):
    """Publish snapshots through a queue; never touch widgets from the worker."""
    level = Level(settings)
    stage, started = "mesh", monotonic()
    try:
        from analysis_process import report_analysis_step
        report_analysis_step("Generating finite element mesh…")
        mesh = mesh_generator(settings)
        level.mesh_seconds = monotonic() - started
        level.elements = mesh.grid.n_cells
        if mesh.solve_errors:
            raise ValueError(mesh.solve_errors[0])
        if max_elements and level.elements > max_elements:
            level.status = f"Element limit exceeded ({max_elements:,}); not solved"
        elif stop is not None and stop.is_set():
            level.status = "Stopped after meshing"
        else:
            level.status = "Solving"
            if publish:
                publish(replace(level, metrics=list(level.metrics)))
            stage, started = "solve", monotonic()
            result = solver(settings, mesh)
            level.solve_seconds = monotonic() - started
            level.folder = result.get("run_folder", "")
            level.metrics = [result.get("graphs", {}).get(key) for _, key in response_metrics(settings)]
            level.status = result.get("status", "Unknown")
            if level.status == "completed":
                curve = read_curve(level.folder, settings.size) if level.folder else []
                level.final_strain = curve[-1][0] if curve else None
                level.final_force = curve[-1][1] if curve else None
                actual, target = ((level.final_force, settings.applied_force)
                                  if settings.loading_mode == "force" else
                                  (level.final_strain, 100 * settings.compression / settings.size))
                if not curve or not math.isclose(actual, target, rel_tol=1e-5, abs_tol=1e-7):
                    level.status = "Target force not verified" if settings.loading_mode == "force" else "Target strain not verified"
                elif not valid_metrics(level, selected):
                    level.status = "Missing or non-finite response metrics"
    except Exception as exc:
        if stage == "mesh":
            level.mesh_seconds = monotonic() - started
        else:
            level.solve_seconds = monotonic() - started
        level.status = f"Failed: {exc}"
    return level


def _solve_level_process(settings, mesh_generator, solver, updates, stop, max_elements, selected):
    return solve_level(settings, mesh_generator, solver, updates.put, stop, max_elements, selected)
