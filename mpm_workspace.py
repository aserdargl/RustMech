"""MPM analysis setup and force–displacement results in the main workspace."""

from dataclasses import asdict
import json
from pathlib import Path

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QFormLayout, QGroupBox, QLabel, QPushButton, QScrollArea, QSizePolicy,
                               QSplitter, QVBoxLayout, QWidget)

from analysis_process import AnalysisProcess
from file_dialog import QFileDialog, choose_analysis_results_folder
from local_kratos import MPM_INSTALL_REQUIRED, install_local_kratos, local_mpm_python
from model import ROOT
from mpm_analysis import MPMSettings, SUMMARY_NAME, read_mpm_result, run_local_mpm
from operation_progress import OperationProgress
from studio_style import (ActionFlowLayout, CompactDoubleSpinBox, CompactSpinBox, configure_form,
                          field_label, stepper_with_unit)
from truba_remote import run_remote_operation
from ui_language import translate


class MPMWorkspace(QWidget):
    SETTINGS_KEY = 'mpm/settings'

    def __init__(self, geometry_provider, parent, preferences):
        super().__init__(parent)
        self.geometry_provider = geometry_provider
        self.preferences = preferences
        self.remote_controller = None
        self.job = None
        self.installing = False
        self.display_directory = None
        self.controls = {}
        self.setObjectName('mpmWorkspace')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        self.splitter = QSplitter()
        self.splitter.setObjectName('mpmWorkspaceSplitter')
        self.splitter.setHandleWidth(3)
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter, 1)

        sidebar = QScrollArea()
        sidebar.setWidgetResizable(True)
        sidebar.setMinimumWidth(275)
        panel = QWidget()
        panel.setObjectName('sidebarPanel')
        left = QVBoxLayout(panel)
        left.setContentsMargins(7, 5, 7, 5)
        left.setSpacing(4)
        note = QLabel('Uses the TPMS from Model. The lower platen is fixed and the upper platen moves along −Z.')
        note.setObjectName('mutedHintLabel')
        note.setWordWrap(True)
        left.addWidget(note)

        self.loading = QGroupBox('Loading and discretization')
        form = QFormLayout(self.loading)
        configure_form(form)
        defaults = MPMSettings()
        for spec in (
            ('stroke_mm', 'Platen travel', 'mm', .000001, 10000., 6),
            ('duration_ms', 'Loading time', 'ms', .000001, 10000., 6),
            ('grid_mm', 'Background cell size', 'mm', .001, 10000., 3),
            ('points_per_axis', 'Points per cell axis', '', 1, 4, 0),
        ):
            self._add_field(form, defaults, *spec)
        left.addWidget(self.loading)

        self.material = QGroupBox('MPM solid material')
        self.material.setObjectName('materialGroup')
        form = QFormLayout(self.material)
        configure_form(form)
        for spec in (
            ('young_mpa', 'Young’s modulus', 'MPa', .001, 1e7, 3),
            ('poisson_ratio', 'Poisson ratio', '', 0., .4899, 4),
            ('density_kg_m3', 'Density', 'kg/m³', .001, 1e6, 3),
        ):
            self._add_field(form, defaults, *spec)
        copy_material = self._button('Copy Model properties', self.copy_model_material)
        form.addRow(copy_material)
        left.addWidget(self.material)
        note = QLabel('Compressible Neo-Hookean material. Frictionless penalty contact at both platens.')
        note.setObjectName('mutedHintLabel')
        note.setWordWrap(True)
        left.addWidget(note)

        actions = ActionFlowLayout(equal_width=True)
        self.run_button = self._button('Run MPM', self.run, primary=True)
        self.stop_button = self._button('Stop', self.cancel)
        self.open_button = self._button('Open MPM result…', self.open_case)
        self.folder_button = self._button('Open result folder', self.open_folder)
        for button in (self.run_button, self.stop_button, self.open_button, self.folder_button):
            actions.addWidget(button)
        left.addLayout(actions)
        self.install_button = self._button('Install local Kratos MPM', self.install_runtime)
        left.addWidget(self.install_button, 0, Qt.AlignmentFlag.AlignLeft)
        left.addStretch(1)
        sidebar.setWidget(panel)
        self.splitter.addWidget(sidebar)

        result = QGroupBox('Force–displacement curve')
        right = QVBoxLayout(result)
        self.figure = Figure(figsize=(6, 3), layout='constrained', facecolor='#FFFFFF')
        self.chart = FigureCanvasQTAgg(self.figure)
        self.chart.setMinimumWidth(180)
        right.addWidget(self.chart, 1)
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        right.addWidget(self.result_label)
        self.splitter.addWidget(result)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([300, 900])
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.operation_progress = OperationProgress(self, provider=lambda: (self.job is not None, self.status.text()))
        layout.addWidget(self.operation_progress)
        self.timer = QTimer(self, interval=250)
        self.timer.timeout.connect(self.poll)

        try:
            saved = MPMSettings(**json.loads(preferences.value(self.SETTINGS_KEY, '{}')))
            self.apply_settings(saved)
        except (ValueError, TypeError):
            self.apply_settings(defaults)
        for control in self.controls.values():
            control.valueChanged.connect(self.save_preferences)
        self.clear_result()
        self.update_execution_availability()

    def _add_field(self, form, defaults, name, label, unit, minimum, maximum, decimals):
        control = CompactDoubleSpinBox() if decimals else CompactSpinBox()
        control.setObjectName('mpm_' + name)
        if decimals:
            control.setDecimals(decimals)
        control.setRange(minimum, maximum)
        control.setValue(getattr(defaults, name))
        control.setKeyboardTracking(False)
        self.controls[name] = control
        form.addRow(field_label(label, control), stepper_with_unit(control, unit) if unit else control)

    @staticmethod
    def _button(label, action, *, primary=False):
        button = QPushButton(label)
        button.setObjectName('primaryActionBtn' if primary else 'secondaryActionBtn')
        button.setAutoDefault(False)
        button.setDefault(False)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        button.clicked.connect(action)
        return button

    def collect(self):
        return MPMSettings(**{name: control.value() for name, control in self.controls.items()})

    def validate_settings(self, settings):
        for name, control in self.controls.items():
            if not control.minimum() <= getattr(settings, name) <= control.maximum():
                raise ValueError(f'{name} is outside the supported MPM input range.')

    def apply_settings(self, settings):
        self.validate_settings(settings)
        for name, control in self.controls.items():
            control.setValue(getattr(settings, name))

    def save_preferences(self):
        self.preferences.setValue(self.SETTINGS_KEY, json.dumps(asdict(self.collect())))

    def copy_model_material(self):
        try:
            geometry = self.geometry_provider()
            values = asdict(self.collect())
            values.update(young_mpa=geometry.young_modulus, poisson_ratio=geometry.poisson_ratio,
                          density_kg_m3=geometry.material_density * 1000.)
            self.apply_settings(MPMSettings(**values))
            self.status.setText('Copied the Model elastic properties into MPM.')
        except (TypeError, ValueError) as error:
            self.status.setText(str(error))

    @property
    def running_analysis(self):
        return self.job is not None and not self.installing

    def update_execution_availability(self):
        busy = self.job is not None
        remote = self.remote_controller is not None and self.remote_controller.requested
        ready = (self.remote_controller.analysis_solver_enabled('mpm') if remote else local_mpm_python() is not None)
        self.run_button.setEnabled(ready and not busy)
        self.run_button.setToolTip('' if ready else (
            'Connect to TRUBA and prepare its Kratos MPM runtime.' if remote else MPM_INSTALL_REQUIRED))
        self.install_button.setVisible(not remote and not ready)
        self.install_button.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
        self.open_button.setEnabled(not busy)
        self.folder_button.setEnabled(self.display_directory is not None)
        self.loading.setEnabled(not busy)
        self.material.setEnabled(not busy)

    def _track(self, job, status, *, installing=False):
        self.job = job
        self.installing = installing
        self.status.setText(status)
        self.update_execution_availability()
        self.timer.start()

    def install_runtime(self):
        if self.job is not None:
            return
        try:
            self._track(AnalysisProcess(install_local_kratos, True), 'Installing local Kratos MPM…', installing=True)
        except (RuntimeError, OSError) as error:
            self.status.setText(str(error))

    def run(self):
        if self.job is not None:
            return
        try:
            geometry, settings = self.geometry_provider(), self.collect()
            settings.validate(geometry)
            controller = self.remote_controller
            remote = controller is not None and controller.requested
            if not remote and local_mpm_python() is None:
                raise RuntimeError(MPM_INSTALL_REQUIRED)
            directory = choose_analysis_results_folder(self)
            if directory is None:
                return
            if remote:
                context, auth = controller.require_analysis('mpm')
                request = dict(operation='mpm_solve', settings=asdict(geometry), mpm=asdict(settings))
                job = AnalysisProcess(
                    run_remote_operation, request, context, remote_auth=auth,
                    remote_options={"directory": directory / "TRUBA"},
                )
                self._track(job, 'Submitting MPM to TRUBA…')
            else:
                job = AnalysisProcess(run_local_mpm, geometry, settings, str(directory))
                self._track(job, 'Preparing MPM compression…')
        except (RuntimeError, OSError, ValueError) as error:
            self.status.setText(str(error))

    def poll(self):
        if self.job is None:
            return
        if not self.job.done():
            if self.job.is_remote:
                record = self.job.remote_status() or {}
                self.status.setText(f"TRUBA job {record.get('job_id', 'pending')} · {record.get('state', 'preparing')}")
            elif not self.installing and (progress := self.job.progress_status()):
                self.status.setText(progress)
            return
        job, self.job = self.job, None
        installing, self.installing = self.installing, False
        self.timer.stop()
        try:
            directory = job.result()
            if installing:
                self.status.setText('Local Kratos MPM is ready.')
            else:
                self.load_directory(directory)
        except Exception as error:
            self.status.setText(str(error)[-1700:])
        self.update_execution_availability()

    def cancel(self):
        if self.job is None:
            return
        try:
            self.job.force_stop()
        except (OSError, RuntimeError) as error:
            self.status.setText(str(error))
            return
        self.job = None
        self.installing = False
        self.timer.stop()
        self.status.setText('MPM operation stopped.')
        self.update_execution_availability()

    def dispose(self):
        self.cancel()
        self.timer.stop()

    def _plot(self, curve=None):
        self.figure.clear()
        axes = self.figure.add_subplot(111)
        axes.set_xlabel(translate('Compression displacement / mm'))
        axes.set_ylabel(translate('Platen reaction / N'))
        axes.grid(alpha=.22)
        if curve is not None:
            axes.plot(curve[:, 1], curve[:, 2], color='#2563EB', label=translate('Upper platen'))
            axes.plot(curve[:, 1], curve[:, 3], color='#B45309', label=translate('Lower platen'))
            axes.legend()
        self.chart.draw_idle()

    def clear_result(self):
        self.display_directory = None
        self._plot()
        self.result_label.setText('Run MPM or open a saved result to view the force–displacement curve.')
        self.folder_button.setEnabled(False)

    def load_directory(self, directory):
        directory = Path(directory)
        summary, curve = read_mpm_result(directory)
        peak = translate('{name} · {points:,} points · peak upper reaction {force:.5g} N').format(
            name=summary['geometry']['structure_name'], points=summary['points'], force=summary['peak_upper_force_n'])
        quality = translate('Maximum platen overlap {overlap:.4g} mm · background cell {cell:.4g} mm').format(
            overlap=summary['maximum_overlap_mm'], cell=summary['grid_mm'])
        self._plot(curve)
        self.display_directory = directory
        self.result_label.setText(f'{peak}\n{quality}')
        self.status.setText(str(directory))
        self.folder_button.setEnabled(True)

    def open_case(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Open MPM result', str(ROOT / 'results'),
                                                 f'MPM result ({SUMMARY_NAME})')
        if filename:
            try:
                self.load_directory(Path(filename).parent)
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.status.setText(str(error))

    def open_folder(self):
        if self.display_directory is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.display_directory)))
