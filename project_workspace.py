"""Explicit project save/open actions; opening never launches computation."""
from dataclasses import asdict
import json
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox, QAbstractSpinBox, QWidget

from analysis_project import AnalysisProject, load_project, save_project
from solver_api import get_solver_capabilities


class ProjectWorkspace:
    def __init__(self, window):
        self.window = window
        self.project = None
        self.path = None
        self.saved_snapshot = None

    def busy(self):
        w = self.window
        return (w.analysis_job is not None or w.future is not None
                or any(area.job is not None for area in (w.flow_workspace, w.thermal_workspace))
                or any(getattr(area, "_active_mode", None) is not None
                       for area in (w.doe_studio, w.analysis_queue_widget) if area is not None))

    def capture(self):
        w = self.window
        for spin in w.findChildren(QAbstractSpinBox):
            spin.interpretText()
        project = AnalysisProject.capture(w.collect(), w.flow_workspace.collect(),
                    w.thermal_workspace.collect(), w.thermal_workspace.collect_thermal(), self.project)
        project.active_study = {2: "flow", 3: "cht"}.get(w.workspace_tabs.currentIndex(), "mechanical")
        for kind, area in (("flow", w.flow_workspace), ("cht", w.thermal_workspace)):
            if area.display_directory:
                path = str(Path(area.display_directory).resolve())
                results = project.studies[kind]["results"]
                if not any(item["path"] == path for item in results):
                    from uuid import uuid4
                    results.append({"id": uuid4().hex, "path": path})
        return project

    @staticmethod
    def fingerprint(project):
        return json.dumps(project.to_dict(), sort_keys=True, allow_nan=False)

    def save_path(self, path):
        if self.busy():
            raise RuntimeError("Wait for the current operation to finish before saving a project.")
        project = self.capture()
        if self.path is None:
            project.name = Path(path).stem.removesuffix(".rustmech")
        save_project(project, path)
        self.project, self.path = project, Path(path)
        self.saved_snapshot = self.fingerprint(project)
        self.window.status.setText("Project saved: " + str(self.path))

    def save(self, save_as=False):
        try:
            if self.busy():
                raise RuntimeError("Wait for the current operation to finish before saving a project.")
            path = None if save_as else self.path
            if path is None:
                filename, _ = QFileDialog.getSaveFileName(self.window, "Save analysis project",
                    str(self.path or Path.home()/"analysis.rustmech.json"), "RustMech project (*.rustmech.json)")
                if not filename:
                    return False
                path = Path(filename)
                if not str(path).lower().endswith(".json"):
                    path = Path(str(path)+".rustmech.json")
            self.save_path(path)
            return True
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self.window, "Analysis project", str(error))
            return False

    def _preflight(self, project):
        w = self.window
        settings, flow, cht_flow, thermal = project.workspace_settings()
        from model import validate_geometry, validate_material
        errors = validate_geometry(settings, include_mesh=False) + validate_material(settings)
        if errors:
            raise ValueError("\n".join(errors))
        for area, config in ((w.flow_workspace, flow), (w.thermal_workspace, cht_flow)):
            for key, control in area.controls.items():
                if not control.minimum() <= getattr(config, key) <= control.maximum():
                    raise ValueError(f"{key} is outside the supported workspace range.")
            if area.model.findData(config.turbulence) < 0:
                raise ValueError("Unknown flow model.")
            if area.channel_shape.findData(config.channel_shape) < 0:
                raise ValueError("Unknown channel section.")
        if cht_flow.turbulence != "laminar":
            raise ValueError("The CHT workspace currently requires laminar flow.")
        for key, control in w.thermal_workspace.thermal_controls.items():
            if not control.minimum() <= getattr(thermal, key) <= control.maximum():
                raise ValueError(f"{key} is outside the supported workspace range.")
        if w.solver_combo.findData(settings.solver_backend) < 0:
            raise ValueError("Unknown mechanical solver.")
        for combo, value in ((w.loading_mode_combo, settings.loading_mode),
                             (w.self_contact_combo, settings.self_contact_mode),
                             (w.contact_formulation_combo, settings.contact_formulation),
                             (w.mesh_type_combo, settings.mesh_type),
                             (w.tetra_strategy_combo, settings.tetra_strategy),
                             *((combo,getattr(settings,f"{side}_platen_contact"))
                               for side,combo in w.platen_contact_combos.items())):
            if combo.findData(value) < 0:
                raise ValueError(f"Unsupported mechanical setting: {value}")
        return settings, flow, cht_flow, thermal

    def open_path(self, path):
        if self.busy():
            raise RuntimeError("Wait for the current operation to finish before opening a project.")
        project = load_project(path)
        values = self._preflight(project)
        # All parsing and file checks precede mutations. Keep a rollback snapshot
        # for unexpected UI failures as well as malformed input.
        before = self.capture()
        try:
            self._apply(*values, active=project.active_study)
        except Exception:
            self._apply(*before.workspace_settings(), active=before.active_study)
            raise
        self.project, self.path = project, Path(path)
        self.saved_snapshot = self.fingerprint(self.capture())
        self.window.status.setText("Project opened. Use Apply to preview; run analysis explicitly.")

    def _apply(self, settings, flow, cht_flow, thermal, *, active):
        w = self.window
        widgets = w.findChildren(QWidget)
        blocked = [(widget, widget.blockSignals(True)) for widget in widgets]
        try:
            w.debounce.stop()
            w._analysis_after_mesh = None
            w.load_from_doe_row(settings, preview=False)
            w.solver_capabilities = get_solver_capabilities(settings.solver_backend)
            w._active_solver_id = settings.solver_backend
            w.solver_combo.setCurrentIndex(w.solver_combo.findData(settings.solver_backend))
            w._apply_solver_profile(settings)
            editor = w.material_editor
            editor.name.setText(settings.material_name)
            editor.material_card = settings.material_card
            editor.set_numeric_values(asdict(settings))
            editor.fill_curve(settings.hardening_curve)
            editor.model.setCurrentIndex(editor.model.findData(settings.material_model))
            editor.set_active_path(None)
            editor.update_model()
            w.active_geometry = settings
            if settings.test_profile == "custom":
                w._custom_experiment = settings
            w._mesh_requested = False
            w.preview = None
            w.plotter.clear()
            for area, config in ((w.flow_workspace, flow), (w.thermal_workspace, cht_flow)):
                area.channel_shape.setCurrentIndex(area.channel_shape.findData(config.channel_shape))
                area.allow_bypass.setChecked(config.allow_bypass)
                area.fluid_material_card = config.fluid_material_card
                for key, control in area.controls.items():
                    control.setValue(getattr(config, key))
                area.model.setCurrentIndex(area.model.findData(config.turbulence))
                area.empty.setChecked(config.empty_pipe)
                area.distribution.setText(config.distribution)
                area.bashrc.setText(config.foam_bashrc)
                area.grid = None
                area.display_directory = area.case_directory = None
                area.plotter.clear()
                area.case_label.setText("No case loaded")
                area.results.setText("Project inputs restored. Build or open results explicitly.")
            area = w.thermal_workspace
            area.play_timer.stop()
            area.thermal_report = area.solid_grid = None
            area.time_slider.setRange(0, 0)
            area.play.setText("Play")
            area.figure.clear()
            area.chart.draw_idle()
            for key, control in area.thermal_controls.items():
                control.setValue(getattr(thermal, key))
            area.solid_material_card = thermal.solid_material_card
            w.workspace_tabs.setCurrentIndex({"flow": 2, "cht": 3}.get(active, 0))
            w.workspace_navigation.setCurrentIndex(w.workspace_tabs.currentIndex())
        finally:
            for widget, previous in blocked:
                widget.blockSignals(previous)
        w._sync_solver_ui()
        w._sync_contact_ui()
        w.refresh_validity()
        w._sync_workspace_context(w.workspace_tabs.currentIndex())
        w.flow_workspace.inputs_changed()
        w.thermal_workspace.inputs_changed()

    def open(self):
        try:
            if self.busy():
                raise RuntimeError("Wait for the current operation to finish before opening a project.")
            filename, _ = QFileDialog.getOpenFileName(self.window, "Open analysis project",
                str(self.path.parent if self.path else Path.home()),
                "RustMech project or legacy settings/case (*.json)")
            if not filename:
                return
            current = self.capture()
            # A fresh workspace also contains user edits worth preserving.
            if self.saved_snapshot is None or self.fingerprint(current) != self.saved_snapshot:
                answer = QMessageBox.question(self.window, "Analysis project",
                    "Save the current workspace before opening another project?",
                    QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel)
                if answer == QMessageBox.StandardButton.Cancel:
                    return
                if answer == QMessageBox.StandardButton.Save and not self.save():
                    return
            self.open_path(filename)
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self.window, "Analysis project", str(error))
