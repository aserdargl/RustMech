"""Mesh and solve individual DoE rows without loading the desktop workspace."""

from model import Settings, validate_geometry


class ExperimentWorker:
    """Picklable job context; no UI objects cross the process boundary."""

    def __init__(self, mesh_generator, batch_solver_runner):
        self._mesh_generator = mesh_generator
        self._batch_solver_runner = batch_solver_runner

    def _evaluate_row_mesh(
        self, r_idx: int, settings: Settings
    ) -> dict:
        errors = validate_geometry(settings)
        if errors:
            return {"r_idx": r_idx, "status": f"Invalid ({errors[0]})"}
        try:
            from analysis_process import report_analysis_step
            report_analysis_step("Generating finite element mesh…")
            mesh = self._mesh_generator(settings)
            vol_solid_mm3 = (float(mesh.grid.volume) if settings.test_profile != 'custom' else
                             settings.size**3 * float(mesh.fraction)/100.)
            mass_g = float(getattr(settings, "material_density", 7.85)) * vol_solid_mm3 * 1e-3
            if mesh.solve_errors:
                return {
                    "r_idx": r_idx,
                    "actual_density": float(mesh.fraction),
                    "mass_g": mass_g,
                    "hex8_elements": int(mesh.grid.n_cells),
                    "status": "Mesh Disconnected",
                }
            return {
                "r_idx": r_idx,
                "actual_density": float(mesh.fraction),
                "mass_g": mass_g,
                "hex8_elements": int(mesh.grid.n_cells),
                "status": "Mesh Ready",
            }
        except Exception as exc:
            return {"r_idx": r_idx, "status": f"Error: {exc}"}

    def _solve_row(self, r_idx: int, settings: Settings) -> dict:
        errors = validate_geometry(settings)
        if errors:
            return {"r_idx": r_idx, "status": f"Invalid ({errors[0]})"}
        try:
            from analysis_process import report_analysis_step
            report_analysis_step("Generating finite element mesh…")
            mesh = self._mesh_generator(settings)
            vol_solid_mm3 = (settings.size**3) * (float(mesh.fraction) / 100.0)
            mass_g = float(getattr(settings, "material_density", 7.85)) * vol_solid_mm3 * 1e-3
            if mesh.solve_errors:
                return {
                    "r_idx": r_idx,
                    "actual_density": float(mesh.fraction),
                    "mass_g": mass_g,
                    "hex8_elements": int(mesh.grid.n_cells),
                    "status": "Mesh Disconnected",
                }
            summary = self._batch_solver_runner(settings, mesh)
            graphs = summary.get("graphs", {})
            fields = summary.get("fields", {})
            return {
                "r_idx": r_idx,
                "actual_density": float(mesh.fraction),
                "mass_g": float(graphs.get("specimen_mass_g", mass_g)),
                "hex8_elements": int(mesh.grid.n_cells),
                "status": "Completed"
                if summary.get("status") == "completed"
                else str(summary.get("status", "Completed")),
                "max_force_n": float(graphs["maximum_force_N"])
                if "maximum_force_N" in graphs
                else None,
                "energy_absorption_j": float(graphs["energy_absorption_J"])
                if "energy_absorption_J" in graphs
                else None,
                "specific_energy_absorption_j_g": float(graphs["specific_energy_absorption_J_g"])
                if "specific_energy_absorption_J_g" in graphs
                else None,
                "volumetric_ea_j_cm3": float(graphs["volumetric_energy_absorption_J_cm3"])
                if "volumetric_energy_absorption_J_cm3" in graphs
                else None,
                "effective_modulus_mpa": float(graphs["effective_young_modulus_MPa"])
                if "effective_young_modulus_MPa" in graphs
                else None,
                "poisson_ratio": float(fields["effective_poisson_ratio"])
                if fields.get("effective_poisson_ratio") is not None
                else None,
                "max_von_mises_mpa": float(fields["maximum_von_mises_MPa"])
                if "maximum_von_mises_MPa" in fields
                else None,
                "run_folder": str(summary.get("run_folder", "")),
            }
        except Exception as exc:
            return {"r_idx": r_idx, "status": f"Failed: {exc}"}
