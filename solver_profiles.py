"""Per-user solver drafts, separate from the shared specimen geometry/material."""
import json
from dataclasses import asdict, replace
from pathlib import Path

from PySide6.QtCore import QSettings
from model import ANALYSIS_FIELDS, GEOMETRY_FIELDS, SETTINGS_FILE, Settings, load_settings, settings_from_dict

MESH_FIELDS = ("mesh_resolution", "local_mesh_size", "mesh_type", "tetra_strategy")
STRUCTURE_FIELDS = tuple(name for name in GEOMETRY_FIELDS if name not in MESH_FIELDS)

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

    def save_workspace(self, draft, *, applied=None):
        self.settings.setValue("mechanical/workspaceDraft", json.dumps(asdict(draft), allow_nan=False))
        if applied is not None:
            self.settings.setValue("mechanical/appliedStructure", json.dumps(
                {name: getattr(applied, name) for name in STRUCTURE_FIELDS}, allow_nan=False))

    def restore_applied_structure(self, draft):
        """Restore the geometry used by previews, with the legacy draft as a fallback."""
        raw = self.settings.value("mechanical/appliedStructure")
        if raw:
            try:
                saved = json.loads(raw)
                applied = settings_from_dict(asdict(draft) | {name: saved[name] for name in STRUCTURE_FIELDS})
                if applied.imported_geometry_path and not Path(applied.imported_geometry_path).is_file():
                    applied = replace(applied, imported_geometry_path="", imported_geometry_name="",
                                      imported_geometry_role="compression")
                return applied
            except (TypeError, ValueError, KeyError):
                pass
        return draft

    def restore_workspace(self, legacy_path=SETTINGS_FILE):
        """Restore editable inputs, which may be unfinished or fail solve validation."""
        raw = self.settings.value("mechanical/workspaceDraft")
        if raw:
            try:
                draft = settings_from_dict(json.loads(raw))
            except (TypeError, ValueError):
                pass
            else:
                if draft.imported_geometry_path and not Path(draft.imported_geometry_path).is_file():
                    draft = replace(draft, imported_geometry_path="", imported_geometry_name="",
                                    imported_geometry_role="compression")
                # A temporary TRUBA solver can differ from the locally selected solver.
                active = self.settings.value("solverProfiles/active", draft.solver_backend)
                if active in ("ansys_mapdl", "kratos", "openfoam") and active != draft.solver_backend:
                    return self.load(active, draft)
                return draft
        try:
            legacy = load_settings(legacy_path)
        except (OSError, ValueError, TypeError):
            legacy = Settings()
        return self.restore(legacy)

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
