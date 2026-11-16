"""Per-user solver drafts, separate from the shared specimen geometry/material."""
import json
from dataclasses import asdict

from PyQt6.QtCore import QSettings
from model import ANALYSIS_FIELDS, Settings, settings_from_dict

KRATOS_FIELDS = tuple(name for name in Settings.__dataclass_fields__ if name.startswith(("kratos_", "truba_")))
PROFILE_FIELDS = tuple(dict.fromkeys((*ANALYSIS_FIELDS, "mesh_type", "mesh_resolution",
                                     "local_mesh_size", "tetra_strategy", *KRATOS_FIELDS)))


def profile_values(settings):
    return {name: getattr(settings, name) for name in PROFILE_FIELDS}


def default_profile(solver, current):
    values = asdict(current)
    values["solver_backend"] = solver
    if solver == "kratos":
        values.update(mesh_type="HEX8", cores=8, automatic_cores=True,
                      automatic_contact_parameters=True, self_contact_mode="disabled",kratos_line_search=False)
        if values['kratos_contact_method']=='alm' and values['kratos_linear_solver']=='amgcl':
            values['kratos_linear_solver']='sparse_lu'
        for side in ("bottom", "top"):
            from standard_tests import kind
            if kind(current)=='tension':
                values[f"{side}_platen_contact"]='bonded'
            elif values[f"{side}_platen_contact"] not in ("frictional", "frictionless"):
                values[f"{side}_platen_contact"] = "frictional"
        if values["self_contact_mode"] == "rough":
            values["self_contact_mode"] = "frictional"
    return settings_from_dict(values)


class SolverProfileStore:
    def __init__(self, settings=None):
        self.settings = settings if settings is not None else QSettings("RustMech", "RustMech")

    def load(self, solver, current):
        fallback = default_profile(solver, current)
        raw = self.settings.value(f"solverProfiles/{solver}")
        if not raw:
            return fallback
        try:
            saved = json.loads(raw)
            values = asdict(current)
            values.update({name: saved[name] for name in PROFILE_FIELDS if name in saved})
            values["solver_backend"] = solver
            result = settings_from_dict(values)
            # A malformed externally edited preference must not crash the UI.
            from model import validate_settings
            if validate_settings(result):
                return fallback
            if solver == "kratos":
                from dataclasses import replace
                from solver_api import KRATOS_CAPABILITIES
                if replace(KRATOS_CAPABILITIES, implemented=True).validate(result):
                    return fallback
            return result
        except (TypeError, ValueError, KeyError):
            return fallback

    def save(self, solver, values):
        self.settings.setValue(f"solverProfiles/{solver}", json.dumps(values, allow_nan=False))
        self.settings.sync()

    def restore(self, current):
        solver = self.settings.value("solverProfiles/active", current.solver_backend)
        if solver not in ("ansys_mapdl", "kratos", "openfoam"):
            solver = current.solver_backend
        return self.load(solver, current)

    def set_active(self, solver):
        self.settings.setValue("solverProfiles/active", solver)
        self.settings.sync()
