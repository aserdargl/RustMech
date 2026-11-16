"""Live, bounded automatic mesh refinement using the existing batch solver."""
import csv
from dataclasses import dataclass, field, replace
import math
from pathlib import Path
from queue import Empty
from multiprocessing import get_context
from time import monotonic

from studio_style import CompactSpinBox, CompactComboBox
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
    QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from analysis_process import AnalysisProcess
from model import ANALYSIS_FIELDS, TETRAHEDRAL_MESH_TYPES, validate_settings
from solver_api import SOLVER_REGISTRY, get_solver_capabilities
from mesh_convergence_style import CONVERGENCE_STYLESHEET
from studio_style import CONTROL_HEIGHT, NUMERIC_FIELD_WIDTH, STUDIO_STYLESHEET, CompactDoubleSpinBox, configure_form

METRICS = (
    ("Peak force (N)", "maximum_force_N"),
    ("Effective modulus (MPa)", "effective_young_modulus_MPa"),
    ("SEA (J/g)", "specific_energy_absorption_J_g"),
)
RESULT_HEADERS = ("Mesh / elements", "Mesh (s)", "Solve (s)", "Force (N)",
                  "Modulus (MPa)", "SEA (J/g)", "Max Δ / ref.", "Status")
TIME_UNITS = (
    ("cs", "Centiseconds (cs)", 0.01),
    ("s", "Seconds (s)", 1.0),
    ("min", "Minutes (min)", 60.0),
    ("h", "Hours (h)", 3600.0),
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


def differences(coarse, fine):
    result = []
    for a, b in zip(coarse.metrics, fine.metrics):
        try:
            a, b = float(a), float(b)
        except (TypeError, ValueError):
            result.append(None)
            continue
        if not math.isfinite(a) or not math.isfinite(b):
            result.append(None)
            continue
        result.append(100 * abs(b - a) / abs(b) if b else (0.0 if a == b else math.inf))
    return result


def pair_passes(coarse, fine, tolerance, selected, absolute):
    if not coarse.elements < fine.elements:
        return False
    # Nearly identical sizes or identical discrete meshes cannot verify refinement.
    if coarse.settings.mesh_resolution / fine.settings.mesh_resolution < 1.05:
        return False
    delta = differences(coarse, fine)
    return all(delta[i] <= tolerance or
               abs(float(fine.metrics[i]) - float(coarse.metrics[i])) <= absolute[i]
               for i in selected)


def ordered_levels(levels, selected=range(len(METRICS))):
    return sorted((v for v in levels if v.status == "completed" and valid_metrics(v, selected)),
                  key=lambda v: v.settings.mesh_resolution, reverse=True)


def converged_level(levels, tolerance, selected, absolute):
    ordered = ordered_levels(levels, selected)
    if len(ordered) < 3:
        return None
    coarse, medium, fine = ordered[-3:]
    if (pair_passes(coarse, medium, tolerance, selected, absolute) and
            pair_passes(medium, fine, tolerance, selected, absolute)):
        return fine
    return None


def next_mesh_size(levels, tolerance, selected, factor):
    """Split a large response jump before spending on a still finer mesh."""
    ordered = ordered_levels(levels, selected)
    fine = ordered[-1]
    if len(ordered) >= 2:
        coarse = ordered[-2]
        gap = coarse.settings.mesh_resolution / fine.settings.mesh_resolution
        if gap > 1.2 and max(differences(coarse, fine)[i] for i in selected) > max(10, 4 * tolerance):
            return math.sqrt(coarse.settings.mesh_resolution * fine.settings.mesh_resolution), "Intermediate"
    return fine.settings.mesh_resolution / factor, "Refinement"


class ConvergenceToolbarScroll(QScrollArea):
    """Keep one toolbar row usable when restored on a narrower monitor."""
    def __init__(self, toolbar):
        super().__init__()
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setWidget(toolbar)
        self.setFixedHeight(toolbar.sizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        height = self.widget().sizeHint().height()
        if self.width() < self.widget().minimumSizeHint().width():
            height += self.horizontalScrollBar().sizeHint().height()
        if self.height() != height:
            self.setFixedHeight(height)


class MeshConvergenceDialog(QDialog):
    def __init__(self, settings, mesh_generator, solver, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mesh Convergence")
        self.setObjectName("meshConvergenceDialog")
        self.setStyleSheet(STUDIO_STYLESHEET + CONVERGENCE_STYLESHEET)
        self.selected_settings = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        self.setToolTip(f"{settings.structure_name} · {settings.size:g} mm · {settings.material_name}")
        self.remote_controller = getattr(parent,"remote_controller",None)
        self.study = MeshConvergenceWidget(settings, mesh_generator, solver, self)
        layout.addWidget(self.study)
        self.resize(1080, 430)

    def done(self, result):
        if self.study.future is not None:
            self.study.request_stop()
            return
        super().done(result)


class MeshConvergenceWidget(QWidget):
    def __init__(self, settings, mesh_generator, solver, parent=None):
        super().__init__(parent)
        self.setObjectName("meshConvergenceStudy")
        self.setStyleSheet(STUDIO_STYLESHEET + CONVERGENCE_STYLESHEET)
        from ui_copy import install_copy_support
        from PySide6.QtWidgets import QApplication
        install_copy_support(QApplication.instance())
        self.settings, self.mesh_generator, self.solver = settings, mesh_generator, solver
        self.remote_controller = getattr(parent,"remote_controller",None)
        self.stop_requested = get_context("spawn").Event()
        self.updates = None
        self.future = None
        self.rows = []
        self.time_units = {1: "s", 2: "s"}
        self.recommended = None
        self.selected = list(range(len(METRICS)))
        self.manual_pending = []
        self.timer = QTimer(self, interval=100)
        self.timer.timeout.connect(self._poll)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.toolbar = QWidget()
        self.toolbar.setObjectName("convergenceToolbar")
        controls = QHBoxLayout(self.toolbar)
        controls.setContentsMargins(5, 4, 5, 4)
        controls.setSpacing(3)
        controls.addWidget(QLabel("Solver"))
        self.solver_combo = CompactComboBox()
        self.solver_combo.setFixedWidth(138)
        self.solver_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        for identifier, capabilities in SOLVER_REGISTRY.items():
            self.solver_combo.addItem(capabilities.display_name, identifier)
            self.solver_combo.setItemData(self.solver_combo.count() - 1,
                                         capabilities.unavailable_reason or capabilities.display_name,
                                         Qt.ItemDataRole.ToolTipRole)
        self.solver_combo.setCurrentIndex(max(0, self.solver_combo.findData(settings.solver_backend)))
        controls.addWidget(self.solver_combo)
        self.start_size = self._number(settings.mesh_resolution, 1e-9, 1e6, " mm")
        self.tolerance = self._number(2, 0.01, 100, " %")
        for control in (self.start_size, self.tolerance):
            control.setFixedWidth(NUMERIC_FIELD_WIDTH - 2)
        controls.addWidget(QLabel("Starting mesh"))
        controls.addWidget(self.start_size)
        controls.addWidget(QLabel("Tolerance"))
        controls.addWidget(self.tolerance)
        controls.addStretch(1)
        self.export_button = self._button(controls, "Export CSV", self.export)
        self.apply_button = self._button(controls, "Use this mesh", self.apply_mesh)
        self.stop_button = self._button(controls, "Stop", self.request_stop)
        self.force_stop_button = self._button(controls, "Forced Stop", self.force_stop)
        self.force_stop_button.setToolTip("Immediately terminate the active mesh/solver process tree and cancel remaining levels.")
        self.run_button = self._button(controls, "Run", self.start)
        self.run_button.setObjectName("primaryLaunchBtn")
        action_buttons = (self.export_button, self.apply_button, self.stop_button,
                          self.force_stop_button, self.run_button)
        for button in action_buttons:
            button.ensurePolished()
        button_width = max(button.sizeHint().width() for button in action_buttons)
        button_height = max(button.sizeHint().height() for button in action_buttons)
        for button in action_buttons:
            button.setFixedSize(button_width, button_height)
        self.stop_button.setEnabled(False)
        self.force_stop_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.toolbar_scroll = ConvergenceToolbarScroll(self.toolbar)
        layout.addWidget(self.toolbar_scroll)

        self.advanced = QWidget()
        self.advanced.setObjectName("convergenceAdvanced")
        form = QFormLayout(self.advanced)
        form.setContentsMargins(6, 5, 6, 5)
        configure_form(form)
        self.automatic = QCheckBox("Automatic mesh selection")
        self.automatic.setChecked(True)
        form.addRow(self.automatic)
        self.manual = QLineEdit()
        self.manual.setPlaceholderText("Coarse to fine, e.g. 0.3, 0.2, 0.15 (mm)")
        self.manual.setEnabled(False)
        self.automatic.toggled.connect(lambda checked: self.manual.setEnabled(not checked))
        form.addRow("Manual global sizes", self.manual)
        self.local_size = self._number(settings.local_mesh_size, 1e-9, 1e6, " mm")
        self.local_size.setToolTip("Starting local size; scales with global size at every level.")
        if settings.mesh_type in TETRAHEDRAL_MESH_TYPES:
            form.addRow("Starting local size", self.local_size)
        self.factor = self._number(1.3, 1.21, 2)
        form.addRow("Refinement factor", self.factor)
        self.minimum = self._number(settings.mesh_resolution / 10, 1e-9, 1e6, " mm")
        form.addRow("Minimum global size", self.minimum)
        self.max_levels = CompactSpinBox()
        self.max_levels.setRange(3, 100)
        self.max_levels.setValue(12)
        self.max_levels.setFixedHeight(CONTROL_HEIGHT)
        self.max_levels.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.max_levels.setFixedWidth(NUMERIC_FIELD_WIDTH)
        form.addRow("Maximum attempts", self.max_levels)
        self.max_elements = CompactSpinBox()
        self.max_elements.setRange(0, 2_147_483_647)
        self.max_elements.setSpecialValueText("Unlimited")
        self.max_elements.setValue(0)
        self.max_elements.setFixedHeight(CONTROL_HEIGHT)
        self.max_elements.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.max_elements.setFixedWidth(NUMERIC_FIELD_WIDTH)
        form.addRow("Maximum elements", self.max_elements)
        self.max_minutes = self._number(120, 0.1, 100000, " min")
        self.max_minutes.setToolTip("Budget checked between stages/levels; the active solver finishes its current level.")
        form.addRow("Study time budget", self.max_minutes)
        self.metric_checks, self.absolute = [], []
        for label, _ in METRICS:
            check = QCheckBox(label)
            check.setChecked(True)
            absolute = self._number(1e-9, 0, 1e12)
            absolute.setToolTip("Absolute change tolerance in this metric's units, used near zero.")
            row = QHBoxLayout()
            row.addWidget(check)
            row.addWidget(QLabel("Absolute tolerance"))
            row.addWidget(absolute)
            form.addRow(row)
            self.metric_checks.append(check)
            self.absolute.append(absolute)
        self.advanced.hide()
        layout.addWidget(self.advanced)

        self.results = QTableWidget(0, len(RESULT_HEADERS))
        self.results.setHorizontalHeaderLabels(RESULT_HEADERS)
        # Install the shared copy actions before supplying our own context menu;
        # otherwise the application's Polish filter would replace this policy.
        from ui_copy import configure_copying
        configure_copying(self.results)
        self.results.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.results.customContextMenuRequested.connect(self._results_context_menu)
        self.results.setWordWrap(False)
        self.results.setMinimumHeight(180)
        self.results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results.setAlternatingRowColors(False)
        self.results.setShowGrid(True)
        self.results.verticalHeader().setDefaultSectionSize(25)
        self.results.verticalHeader().setMinimumSectionSize(25)
        self.results.verticalHeader().setFixedWidth(28)
        header = self.results.horizontalHeader()
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._header_context_menu)
        for column in self.time_units:
            self.results.horizontalHeaderItem(column).setToolTip("Right-click to change the time unit.")
        header.setFixedHeight(25)
        header.setMinimumSectionSize(50)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for column, width in enumerate((230, 70, 70, 95, 115, 90, 115, 160)):
            self.results.setColumnWidth(column, width)
        for column in (3, 4, 5):
            self.results.horizontalHeaderItem(column).setToolTip(METRICS[column - 3][0])
        self.results.horizontalHeaderItem(6).setToolTip(
            "Maximum difference across the selected metrics, relative to the reference row. "
            "Hover over a result for every metric's difference."
        )
        self.results_panel = QFrame()
        self.results_panel.setObjectName("convergenceResultsPanel")
        results_layout = QVBoxLayout(self.results_panel)
        results_layout.setContentsMargins(5, 5, 5, 5)
        results_layout.setSpacing(4)
        results_header = QWidget()
        results_header.setObjectName("convergenceResultsHeader")
        header_layout = QHBoxLayout(results_header)
        header_layout.setContentsMargins(6, 1, 6, 1)
        header_layout.setSpacing(8)
        results_title = QLabel("Results")
        results_title.setObjectName("convergenceResultsTitle")
        results_title.setMinimumHeight(21)
        header_layout.addWidget(results_title)
        self.status = QLabel("Ready")
        self.status.setObjectName("convergenceStatus")
        self.status.setWordWrap(True)
        self.status.setToolTip("Automatic refinement stops when two consecutive comparisons meet tolerance.")
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(self.status, 1)
        results_layout.addWidget(results_header)
        results_layout.addWidget(self.results, 1)
        layout.addWidget(self.results_panel, 1)
        from operation_progress import OperationProgress
        self.operation_progress = OperationProgress(self, provider=lambda: (
            self.future is not None,
            (self.status.text() + " · " + self.rows[-1].status) if self.rows else self.status.text(),
        ))
        layout.addWidget(self.operation_progress)
        self.solver_combo.currentIndexChanged.connect(self._solver_changed)
        if self.remote_controller is not None: self.remote_controller.changed.connect(self._solver_changed)
        self._solver_changed()
        self._configure_metrics()

    def _results_context_menu(self, position):
        column = self.results.columnAt(position.x())
        self._show_time_menu(column, self.results.viewport().mapToGlobal(position), copy_actions=True)

    def _header_context_menu(self, position):
        header = self.results.horizontalHeader()
        column = header.logicalIndexAt(position)
        if column in self.time_units:
            self._show_time_menu(column, header.viewport().mapToGlobal(position))

    def _show_time_menu(self, column, position, copy_actions=False):
        menu = QMenu(self.results)
        menu.setObjectName("convergenceTimeUnitMenu")
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if column in self.time_units:
            group = QActionGroup(menu)
            group.setExclusive(True)
            for unit, label, _ in TIME_UNITS:
                action = menu.addAction(label)
                action.setData(unit)
                action.setCheckable(True)
                action.setChecked(self.time_units[column] == unit)
                group.addAction(action)
                action.triggered.connect(
                    lambda _checked=False, col=column, selected=unit: self._set_time_unit(col, selected))
        if copy_actions:
            if not menu.isEmpty():
                menu.addSeparator()
            menu.addActions(self.results.actions())
        menu.popup(position)

    def _set_time_unit(self, column, unit):
        self.time_units[column] = unit
        label = "Mesh" if column == 1 else "Solve"
        self.results.horizontalHeaderItem(column).setText(f"{label} ({unit})")
        for index in range(len(self.rows)):
            self._render(index)

    def _format_time(self, seconds, column):
        if seconds is None:
            return "—"
        divisor = next(scale for unit, _, scale in TIME_UNITS if unit == self.time_units[column])
        return f"{seconds / divisor:.2f}"

    def _configure_metrics(self):
        metrics = response_metrics(self.settings)
        for column,(label,_) in enumerate(metrics,3):
            self.results.horizontalHeaderItem(column).setText(label)
            self.results.horizontalHeaderItem(column).setToolTip(label)
        from standard_tests import kind
        if kind(self.settings)=='compression':
            for column,label in enumerate(('Displ. (mm)' if self.settings.loading_mode=='force' else 'Force (N)','Modulus (MPa)','SEA (J/g)'),3):
                self.results.horizontalHeaderItem(column).setText(label)
        for check, (label, _) in zip(self.metric_checks, metrics):
            check.setText(label)
        self.status.setText("Ready · uses loading and contact conditions from the main setup")

    def _set_status(self, message):
        self.status.setText(message)
        self.status.setToolTip(message)
        tone = ("success" if message.startswith("Converged") else
                "error" if "Failed" in message or "failed" in message else
                "warning" if "not confirmed" in message else "")
        if self.status.property("tone") != tone:
            self.status.setProperty("tone", tone)
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)

    def _solver_changed(self):
        if self.remote_controller is not None: self.remote_controller.apply_solver_combo(self.solver_combo)
        capabilities = get_solver_capabilities(self.solver_combo.currentData())
        self.solver_combo.setToolTip(capabilities.display_name if capabilities.implemented else
                                    capabilities.unavailable_reason)
        self.run_button.setEnabled(self._execution_available() and self.future is None)
        self.run_button.setToolTip("Run mesh convergence" if capabilities.implemented else
                                   capabilities.unavailable_reason)

    def _execution_available(self):
        if self.remote_controller is not None and self.remote_controller.requested:
            return self.remote_controller.solver_enabled(self.solver_combo.currentData())
        return get_solver_capabilities(self.solver_combo.currentData()).implemented

    def _number(self, value, low, high, suffix=""):
        control = CompactDoubleSpinBox()
        control.setRange(low, high)
        control.setValue(value)
        control.setSuffix(suffix)
        return control

    def _button(self, layout, title, action):
        button = QPushButton(title)
        button.setAutoDefault(False)
        button.setDefault(False)
        button.setObjectName("secondaryActionBtn")
        button.clicked.connect(action)
        layout.addWidget(button)
        return button

    def level_settings(self, size):
        scale = size / self.start_size.value()
        capabilities = get_solver_capabilities(self.solver_combo.currentData())
        level = replace(self.settings, mesh_resolution=size,
                        solver_backend=capabilities.identifier,
                        cores=min(self.settings.cores, capabilities.max_cores))
        if self.settings.mesh_type in TETRAHEDRAL_MESH_TYPES:
            level = replace(level, local_mesh_size=self.local_size.value() * scale)
        errors = validate_settings(level)
        if errors:
            raise ValueError(errors[0])
        return level

    def start(self):
        if self.future is not None:
            return
        try:
            capabilities = get_solver_capabilities(self.solver_combo.currentData())
            if not capabilities.implemented:
                raise ValueError(capabilities.unavailable_reason)
            if not self._execution_available(): raise ValueError("This solver is not available and supported in the selected TRUBA runtime.")
            self.selected = [i for i, check in enumerate(self.metric_checks) if check.isChecked()]
            if not self.selected:
                raise ValueError("Select at least one response metric in Advanced.")
            self.manual_pending = []
            if self.automatic.isChecked():
                first = self.level_settings(self.start_size.value())
            else:
                sizes = [float(v.strip()) for v in self.manual.text().split(",")]
                if len(sizes) < 3 or any(a <= b for a, b in zip(sizes, sizes[1:])):
                    raise ValueError("Enter at least three strictly decreasing global sizes.")
                self.manual_pending = [self.level_settings(size) for size in sizes]
                first = self.manual_pending.pop(0)
            if first.mesh_resolution < self.minimum.value():
                raise ValueError("Starting size is below the minimum global size.")
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid convergence plan", str(exc))
            return
        self.rows.clear()
        self.recommended = None
        self.results.setRowCount(0)
        self.stop_requested.clear()
        self.started = monotonic()
        self._set_running(True)
        self._launch(first, "Starting mesh")
        if self.future is not None:
            self.timer.start()

    def _set_running(self, running):
        for control in (self.start_size, self.tolerance, self.advanced, self.solver_combo):
            control.setEnabled(not running)
        self.run_button.setEnabled(not running and
                                   self._execution_available())
        self.stop_button.setEnabled(running)
        self.force_stop_button.setEnabled(running)
        self.export_button.setEnabled(not running and bool(self.rows))
        self.apply_button.setEnabled(not running and self.recommended is not None)

    def _launch(self, settings, kind):
        self.rows.append(Level(settings, kind=kind))
        self.results.insertRow(len(self.rows) - 1)
        self.stage_started = monotonic()
        self._render(len(self.rows) - 1)
        self.results.scrollToBottom()
        self._set_status(f"{kind}: {settings.mesh_resolution:.6g} mm")
        self.updates = get_context("spawn").Queue()
        try:
            controller = self.remote_controller
            if controller is not None and controller.requested:
                from truba_remote import run_remote_level
                context,auth = controller.require(settings.solver_backend,settings)
                self.future = AnalysisProcess(run_remote_level,settings,context,self.max_elements.value(),self.selected,remote_auth=auth)
                self.rows[-1].status = "TRUBA job"
                self._render(len(self.rows)-1)
            else:
                self.future = AnalysisProcess(
                    _solve_level_process, settings, self.mesh_generator, self.solver,
                    self.updates, self.stop_requested, self.max_elements.value(), self.selected)
        except Exception as exc:
            self.rows[-1].status = f"Failed: {exc}"
            self._render(len(self.rows) - 1)
            self._close_updates()
            self._finish(f"Failed to start convergence: {exc}")

    def request_stop(self):
        self.stop_requested.set()
        self._set_status("Stopping after the current stage…")

    def _close_updates(self):
        if self.updates is not None:
            self.updates.cancel_join_thread()
            self.updates.close()
            self.updates = None

    def force_stop(self):
        if self.future is None:
            return
        self.stop_requested.set()
        try:
            self.future.force_stop()
        except Exception as exc:
            self._set_status(f"Forced Stop failed: {exc}")
            return
        self.future = None
        self._close_updates()
        self.manual_pending.clear()
        level = self.rows[-1]
        elapsed = monotonic() - self.stage_started
        if level.status == "Meshing":
            level.mesh_seconds = elapsed
        elif level.status == "Solving":
            level.solve_seconds = max(0, elapsed - (level.mesh_seconds or 0))
        level.status = "Cancelled (Forced Stop)"
        self.recommended = None
        self._refresh_differences()
        self._finish("Cancelled (Forced Stop); convergence not confirmed. Partial results can be exported.")

    @staticmethod
    def _numeric(value):
        try:
            return math.isfinite(float(value))
        except (TypeError, ValueError):
            return False

    def _render(self, index):
        level = self.rows[index]
        mesh = f"{level.settings.mesh_resolution:.6g} mm"
        if level.elements:
            mesh += f" · {level.elements:,} elements"
        elapsed = monotonic() - self.stage_started if index == len(self.rows) - 1 else 0
        mesh_time, solve_time = level.mesh_seconds, level.solve_seconds
        if level.status == "Meshing":
            mesh_time = elapsed
        elif level.status == "Solving":
            solve_time = max(0, elapsed - (mesh_time or 0))
        delta, delta_hint = "—", "No reference comparison yet."
        if level.reference is not None:
            selected_differences = [level.differences[i] for i in self.selected
                                    if level.differences[i] is not None]
            maximum = f"{max(selected_differences):.2f}%" if selected_differences else "—"
            delta = f"{maximum} · #{level.reference + 1}"
            delta_hint = "\n".join(
                f"{label}: {f'{v:.2f}%' if v is not None else '—'} · #{level.reference + 1}"
                for label, v in zip(("Displacement" if level.settings.loading_mode == "force" else "Force", "Modulus", "SEA"), level.differences)
            )
        values = [mesh, self._format_time(mesh_time, 1), self._format_time(solve_time, 2),
                  *[f"{float(v):.6g}" if self._numeric(v) else "—" for v in level.metrics],
                  delta, level.status]
        for col, value in enumerate(values):
            item = QTableWidgetItem(value)
            detail = f"{level.kind}\n{level.status}\n{level.folder}"
            detail += (f"\nControl: {level.settings.loading_mode}; target displacement: {level.settings.compression:g} mm"
                       if level.settings.loading_mode == "displacement" else
                       f"\nControl: force; target: {level.settings.applied_force:g} N")
            if level.final_force is not None:
                detail += f"\nFinal force: {level.final_force:.6g} N; displacement: {level.final_strain * level.settings.size / 100:.6g} mm"
            if col == 0 and level.settings.mesh_type in TETRAHEDRAL_MESH_TYPES:
                detail += f"\nLocal mesh: {level.settings.local_mesh_size:.6g} mm"
            item.setToolTip(delta_hint if col == 6 else detail)
            item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter |
                                  (Qt.AlignmentFlag.AlignRight if 1 <= col <= 5
                                   else Qt.AlignmentFlag.AlignLeft))
            self.results.setItem(index, col, item)

    def _poll(self):
        if self.future is None:
            return
        if self.remote_controller is not None and self.remote_controller.requested and not self.future.done():
            record = self.future.remote_status()
            if record: self._set_status(f"TRUBA job {record.get('job_id','pending')} · {record.get('state','preparing')}")
        if monotonic() - self.started >= 60 * self.max_minutes.value():
            self.stop_requested.set()
        while True:
            try:
                update = self.updates.get_nowait()
            except Empty:
                break
            update.kind = self.rows[-1].kind
            self.rows[-1] = update
        self._render(len(self.rows) - 1)
        if not self.future.done():
            if hasattr(self.future, "progress_status"):
                stage = self.future.progress_status()
                if stage:
                    self._set_status(f"Level {len(self.rows)} · {stage}")
            return
        try:
            level = self.future.result()
        except Exception as exc:
            level = replace(self.rows[-1], status=f"Failed: {exc}")
        self.future = None
        # A worker can finish just after queue draining. Discard its remaining
        # snapshots before the next worker is launched.
        while True:
            try:
                self.updates.get_nowait()
            except Empty:
                break
        self._close_updates()
        level.kind = self.rows[-1].kind
        self.rows[-1] = level
        self._refresh_differences()
        if monotonic() - self.started >= 60 * self.max_minutes.value():
            self._finish("Time budget reached; convergence not confirmed.")
            return
        if self.stop_requested.is_set():
            self._finish("Stopped; convergence not confirmed. Partial results can be exported.")
            return
        if level.status != "completed":
            self._finish(f"Convergence not confirmed: {level.status}")
            return
        self.recommended = converged_level(
            self.rows, self.tolerance.value(), self.selected, [c.value() for c in self.absolute])
        if self.recommended is not None:
            index = next(i for i, v in enumerate(self.rows) if v is self.recommended)
            self.results.selectRow(index)
            self._finish(f"Converged · use #{index + 1}, {self.recommended.settings.mesh_resolution:.6g} mm. "
                         "Two consecutive comparisons meet tolerance.")
            return
        if len(self.rows) >= self.max_levels.value():
            self._finish("Attempt limit reached; convergence not confirmed.")
            return
        if self.automatic.isChecked():
            size, kind = next_mesh_size(self.rows, self.tolerance.value(), self.selected, self.factor.value())
        elif self.manual_pending:
            size, kind = self.manual_pending.pop(0).mesh_resolution, "Manual"
        else:
            self._finish("Manual levels exhausted; convergence not confirmed.")
            return
        if size < self.minimum.value():
            self._finish("Minimum mesh size reached; convergence not confirmed.")
            return
        try:
            settings = self.level_settings(size)
        except ValueError as exc:
            self._finish(f"Mesh limit reached; convergence not confirmed: {exc}")
            return
        self._launch(settings, kind)

    def _refresh_differences(self):
        ordered = ordered_levels(self.rows, self.selected)
        for coarse, fine in zip(ordered, ordered[1:]):
            fine.reference = next(i for i, v in enumerate(self.rows) if v is coarse)
            fine.differences = differences(coarse, fine)
        for index in range(len(self.rows)):
            self._render(index)

    def _finish(self, message):
        self.timer.stop()
        self._set_running(False)
        self._set_status(message)

    def apply_mesh(self):
        if self.recommended is not None and self.future is None:
            dialog = self.window()
            dialog.selected_settings = self.recommended.settings
            dialog.accept()

    def export(self):
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export mesh convergence", "mesh_convergence.csv", "CSV (*.csv)")
        if not filename:
            return
        try:
            with Path(filename).open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                writer.writerow(("Level", "Kind", "Global (mm)", "Local (mm)", "Elements", "Mesh (s)",
                                 "Solve (s)", *(v[0] for v in response_metrics(self.rows[0].settings)), "Reference level",
                                 "Displacement difference (%)" if self.rows[0].settings.loading_mode == "force" else "Force difference (%)",
                                 "Modulus difference (%)", "SEA difference (%)",
                                 "Final strain (%)", "Status", "Run folder", "Final force (N)", *ANALYSIS_FIELDS))
                for i, v in enumerate(self.rows):
                    writer.writerow((i + 1, v.kind, v.settings.mesh_resolution,
                                     v.settings.local_mesh_size if v.settings.mesh_type in TETRAHEDRAL_MESH_TYPES else "",
                                     v.elements, v.mesh_seconds, v.solve_seconds, *v.metrics,
                                     v.reference + 1 if v.reference is not None else "",
                                     *(v.differences or [None] * 3), v.final_strain, v.status, v.folder,
                                     v.final_force, *(getattr(v.settings, name) for name in ANALYSIS_FIELDS)))
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))

    def dispose(self):
        self.timer.stop()
        self.stop_requested.set()
        self.force_stop()
        if self.future is None:
            self._close_updates()
