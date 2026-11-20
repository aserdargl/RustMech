"""Live, bounded automatic mesh refinement using the existing batch solver."""
import csv
from concurrent.futures import CancelledError
from dataclasses import replace
import math
from pathlib import Path
from queue import Empty
from multiprocessing import get_context
from time import monotonic
from uuid import uuid4

from studio_style import CompactSpinBox, CompactComboBox
from PySide6.QtCore import QSettings, QSignalBlocker, QTimer, Qt
from PySide6.QtGui import QActionGroup, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from analysis_process import AnalysisProcess
from convergence_execution import (
    Level, METRICS, _solve_level_process, read_curve, response_metrics,
    solve_level as solve_level, valid_metrics,
)
from file_dialog import QFileDialog
from model import ANALYSIS_FIELDS, MESH_TYPES, TETRAHEDRAL_MESH_TYPES, validate_settings
from solver_api import SOLVER_REGISTRY, get_solver_capabilities
from standard_tests import kind
from mesh_convergence_style import CONVERGENCE_STYLESHEET
from studio_style import (CONTROL_HEIGHT, NUMERIC_FIELD_WIDTH, STUDIO_STYLESHEET, CompactDoubleSpinBox,
                          configure_form, mesh_precision_stepper, stepper_with_unit)

REMOTE_RESULT_HEADERS = ("Slurm job ID", "Slurm script", "Slurm state", "Solver CPUs",
                         "Allocated RAM (GiB)", "Peak RAM (GiB)")
RESULT_HEADERS = ("Mesh / elements", "Mesh (s)", "Solve (s)", "Force (N)",
                  "Modulus (MPa)", "SEA (J/g)", "Max Δ / ref.", "Status", *REMOTE_RESULT_HEADERS)
MAX_FAILURE_RETRIES = 3
TRUBA_BATCH_SIZE = 5
MIN_TRUBA_LEVEL_CPUS = 16
TIME_UNITS = (
    ("cs", "Centiseconds (cs)", 0.01),
    ("s", "Seconds (s)", 1.0),
    ("min", "Minutes (min)", 60.0),
    ("h", "Hours (h)", 3600.0),
)


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
    """Refine after a failure, or split a large response jump after a success."""
    last = levels[-1]
    base_size = last.settings.mesh_resolution
    attempted = [level.settings.mesh_resolution for level in levels]
    if last.status == "completed":
        ordered = ordered_levels(levels, selected)
        fine = ordered[-1]
        base_size = fine.settings.mesh_resolution
        if len(ordered) >= 2:
            coarse = ordered[-2]
            gap = coarse.settings.mesh_resolution / base_size
            if gap > 1.2 and max(differences(coarse, fine)[i] for i in selected) > max(10, 4 * tolerance):
                midpoint = math.sqrt(coarse.settings.mesh_resolution * base_size)
                if not any(math.isclose(midpoint, size) for size in attempted):
                    return midpoint, "Intermediate"
    size = base_size / factor
    while any(math.isclose(size, previous) for previous in attempted):
        size /= factor
    return size, "Refinement"


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
        self.horizontalScrollBar().rangeChanged.connect(self._sync_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_height()

    def _sync_height(self):
        height = self.widget().sizeHint().height()
        if self.width() < self.widget().minimumSizeHint().width():
            height += self.horizontalScrollBar().sizeHint().height()
        if self.height() != height:
            self.setFixedHeight(height)


class MeshConvergenceDialog(QDialog):
    def __init__(self, settings, mesh_generator, solver, owner=None):
        # Keep the study above its workspace without blocking it or pinning to the desktop.
        super().__init__(owner, Qt.WindowType.Window | Qt.WindowType.WindowMinMaxButtonsHint
                         | Qt.WindowType.WindowCloseButtonHint)
        self.setWindowTitle("Mesh Convergence")
        self.setObjectName("meshConvergenceDialog")
        self.setStyleSheet(STUDIO_STYLESHEET + CONVERGENCE_STYLESHEET)
        self.selected_settings = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        self.setToolTip(f"{settings.structure_name} · {settings.size:g} mm · {settings.material_name}")
        self.remote_controller = getattr(owner, "remote_controller", None)
        self.study = MeshConvergenceWidget(settings, mesh_generator, solver, self)
        layout.addWidget(self.study)
        self.resize(1080, 430)

    def done(self, result):
        if self.study.future is not None:
            self.study.request_stop()
            return
        super().done(result)


class ScriptResourceDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        # The table stylesheet owns the selection background. Paint the resource
        # color inside it so selected rows retain both the highlight and their color.
        base = QStyleOptionViewItem(option)
        self.initStyleOption(base, index)
        base.text = ""
        base.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, base, painter, base.widget)
        painter.save()
        chip = option.rect.adjusted(4, 3, -4, -3)
        painter.fillRect(chip, index.data(Qt.ItemDataRole.BackgroundRole))
        painter.setFont(option.font)
        painter.setPen(QColor("#0F172A"))
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, str(index.data()))
        painter.restore()


class MeshConvergenceWidget(QWidget):
    def __init__(self, settings, mesh_generator, solver, parent=None):
        super().__init__(parent)
        self.setObjectName("meshConvergenceStudy")
        self.setStyleSheet(STUDIO_STYLESHEET + CONVERGENCE_STYLESHEET)
        from ui_copy import install_copy_support
        from PySide6.QtWidgets import QApplication
        install_copy_support(QApplication.instance())
        self.settings, self.mesh_generator, self.solver = settings, mesh_generator, solver
        self.level_base_settings = settings
        self.local_solver_before_truba = None
        self.remote_controller = getattr(parent,"remote_controller",None)
        self.stop_requested = get_context("spawn").Event()
        self.updates = None
        self.future = None
        self.remote_jobs = {}
        self.remote_outcomes = {}
        self.remote_records = {}
        self.study_plan = {}
        self.remote_study_base = None
        self.saved_geometry_source = None
        self.saved_jobs = {}
        self.saved_study_key = None
        self.history_only = False
        self.single_remote_run = False
        self.planned_scripts = {}
        self.next_script_number = 1
        self.explicit_script_run = False
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
        self.tolerance = self._number(2, 0.01, 100)
        self.tolerance.setFixedWidth(NUMERIC_FIELD_WIDTH - 2)
        controls.addWidget(QLabel("Tolerance"))
        controls.addWidget(stepper_with_unit(self.tolerance, "%"))
        self.run_on_truba = QCheckBox("Run on TRUBA")
        self.run_on_truba.setToolTip(
            f"Automatic mode submits up to {TRUBA_BATCH_SIZE} levels per batch. "
            "Add scripts in the preview to choose which levels and resources to submit. "
            "A resource session is optional. "
            "If the solver changes for TRUBA, the study uses that solver's main workspace profile.")
        controls.addWidget(self.run_on_truba)
        self.run_on_truba.setVisible(self.remote_controller is not None)
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
        self.single_truba_mesh = QCheckBox("Run only the starting mesh on TRUBA")
        self.single_truba_mesh.setToolTip("Use the job's full RAM budget for one mesh without refining it.")
        self.single_truba_mesh.hide()
        form.addRow(self.single_truba_mesh)
        self.manual = QLineEdit()
        self.manual.setPlaceholderText("Coarse to fine, e.g. 0.3, 0.2, 0.15 (mm)")
        self.manual.setEnabled(False)
        self.automatic.toggled.connect(lambda checked: self.manual.setEnabled(not checked))
        form.addRow("Manual global sizes", self.manual)
        self.factor = self._number(1.3, 1.21, 2)
        form.addRow("Refinement factor", self.factor)
        self.minimum = self._number(settings.mesh_resolution / 10, 1e-9, 1e6)
        form.addRow("Minimum global size", stepper_with_unit(self.minimum, "mm"))
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
        self.max_minutes = self._number(120, 0.1, 100000)
        self.max_minutes.setToolTip("Budget checked between stages/levels; the active solver finishes its current level.")
        self.time_budget_field = stepper_with_unit(self.max_minutes, "min")
        form.addRow("Study time budget", self.time_budget_field)
        self.truba_wall_minutes = CompactSpinBox()
        self.truba_wall_minutes.setRange(1, 999 * 60 + 59)
        self.truba_wall_minutes.setFixedHeight(CONTROL_HEIGHT)
        self.truba_wall_minutes.setFixedWidth(NUMERIC_FIELD_WIDTH)
        self.truba_wall_minutes.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.truba_wall_minutes.setKeyboardTracking(False)
        self.truba_wall_minutes.setToolTip("Slurm time limit for each mesh analysis, in minutes.")
        self.truba_wall_field = stepper_with_unit(self.truba_wall_minutes, "min")
        form.addRow("Wall time per analysis", self.truba_wall_field)
        form.setRowVisible(self.truba_wall_field, False)
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
        self._build_script_plan(layout)

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
        for column, width in enumerate((230, 70, 70, 95, 115, 90, 115, 160, 90, 155, 150, 76, 130, 108)):
            self.results.setColumnWidth(column, width)
        for position, column in enumerate((8, 9, 11, 12, 13, 10), 1):
            header.moveSection(header.visualIndex(column), position)
        self.results.horizontalHeaderItem(11).setToolTip("CPU threads assigned to this solver step.")
        self.results.horizontalHeaderItem(12).setToolTip("RAM reserved for this mesh level.")
        self.results.horizontalHeaderItem(13).setToolTip("Peak RAM reported by Slurm for this level after the job ends.")
        self._show_remote_columns(False)
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
        self.history_combo = CompactComboBox()
        self.history_combo.setFixedWidth(190)
        self.history_combo.setToolTip("Previously submitted TRUBA mesh convergence studies")
        self.history_combo.activated.connect(self._restore_saved_study)
        self.history_combo.hide()
        header_layout.addWidget(self.history_combo)
        self.append_button = self._button(header_layout, "Add 2 Extra", self.append_remote_levels)
        self.append_button.setObjectName("convergenceAppendButton")
        self.append_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.append_button.setToolTip("Submit the next two levels from this study while the current jobs run.")
        self.append_button.hide()
        self.status = QLabel("Ready")
        self.status.setObjectName("convergenceStatus")
        self.status.setWordWrap(True)
        self.status.setToolTip("Automatic refinement stops when two consecutive comparisons meet tolerance.")
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(self.status, 1)
        results_layout.addWidget(results_header)
        results_layout.addWidget(self.results, 1)
        layout.addWidget(self.results_panel, 1)

        self.mesh_settings = QWidget()
        mesh_fields = QHBoxLayout(self.mesh_settings)
        mesh_fields.setContentsMargins(5, 0, 0, 0)
        mesh_fields.setSpacing(2)
        self.mesh_type_combo = CompactComboBox()
        for mesh_type in MESH_TYPES:
            self.mesh_type_combo.addItem(mesh_type, mesh_type)
        self.mesh_type_combo.setCurrentIndex(self.mesh_type_combo.findData(settings.mesh_type))
        element_label = QLabel("Element")
        element_label.setBuddy(self.mesh_type_combo)
        mesh_fields.addWidget(element_label)
        mesh_fields.addWidget(self.mesh_type_combo)
        self.start_size = self._number(settings.mesh_resolution, 1e-9, 1e6)
        self.local_size = self._number(settings.local_mesh_size, 1e-9, 1e6)
        self.local_size.setToolTip("Starting local size; scales with global size at every level.")
        for text, control in (("Mesh Size", self.start_size), ("Local Size", self.local_size)):
            label = QLabel(text)
            label.setBuddy(control)
            mesh_fields.addWidget(label)
            mesh_fields.addWidget(stepper_with_unit(control, "mm"))
        preferences = QSettings("RustMech", "RustMech")
        self.mesh_precision = mesh_precision_stepper(
            preferences, "meshConvergence/meshDisplayDecimals",
            self.start_size, self.local_size,
        )
        self.mesh_precision.setFixedWidth(36)
        precision_label = QLabel("Decimal")
        precision_label.setBuddy(self.mesh_precision)
        mesh_fields.addWidget(precision_label)
        mesh_fields.addWidget(self.mesh_precision)
        self.truba_resource_info = QLabel("Automatic CPU and RAM sharing")
        self.truba_resource_info.setObjectName("mutedHintLabel")
        self.truba_resource_info.setToolTip(
            "Two levels share one Slurm allocation when each can use at least 16 CPUs. "
            "A single level uses the full allocation.")
        remote_preferences = self.remote_controller.preferences if self.remote_controller is not None else preferences
        walltime_key = "meshConvergence/trubaWallMinutes"
        self.truba_wall_minutes.setValue(remote_preferences.value(walltime_key, 30, type=int))
        self.truba_wall_minutes.valueChanged.connect(lambda value: remote_preferences.setValue(walltime_key, value))
        mesh_fields.addWidget(self.truba_resource_info)
        self.truba_resource_info.hide()
        from operation_progress import OperationProgress
        self.operation_progress = OperationProgress(self, provider=lambda: (
            self.future is not None,
            (self.status.text() + " · " + self.rows[-1].status) if self.rows else self.status.text(),
        ), bar_at_end=True)
        self.operation_progress.layout().setContentsMargins(4, 0, 4, 0)
        self.operation_progress.setFixedHeight(CONTROL_HEIGHT)
        self.operation_progress.label.setMinimumWidth(110)
        progress_policy = self.operation_progress.sizePolicy()
        progress_policy.setRetainSizeWhenHidden(False)
        self.operation_progress.setSizePolicy(progress_policy)

        footer_widget = QWidget()
        footer = QHBoxLayout(footer_widget)
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(layout.spacing())
        footer.addWidget(self.mesh_settings, alignment=Qt.AlignmentFlag.AlignLeft)
        footer.addWidget(self.operation_progress, 1)
        self.footer_scroll = ConvergenceToolbarScroll(footer_widget)
        layout.addWidget(self.footer_scroll)

        self.solver_combo.currentIndexChanged.connect(self._solver_changed)
        self.mesh_type_combo.currentIndexChanged.connect(self._mesh_type_changed)
        self.start_size.valueChanged.connect(self._sync_local_size_limit)
        if self.remote_controller is not None:
            self.run_on_truba.toggled.connect(self._select_execution_target)
            self.remote_controller.changed.connect(self._solver_changed)
            self.remote_controller.window.truba_dialog.connection_changed.connect(self._attach_saved_jobs)
        self._configure_metrics()
        self._solver_changed()
        studies = self._refresh_saved_studies()
        if studies:
            self._restore_saved_study(0, studies)

    def _build_script_plan(self, layout):
        self.script_plan_panel = QWidget()
        self.script_plan_panel.setObjectName("convergenceScriptPlan")
        panel = QVBoxLayout(self.script_plan_panel)
        panel.setContentsMargins(6, 5, 6, 5)
        panel.setSpacing(4)
        toolbar = QWidget()
        controls = QHBoxLayout(toolbar)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(QLabel("Slurm script plan"))
        self.script_choice = CompactComboBox()
        self.script_choice.setFixedWidth(110)
        self.script_choice.addItem("New script", None)
        controls.addWidget(self.script_choice)
        self.script_queue = CompactComboBox()
        self.script_queue.setFixedWidth(290)
        self.script_queue.setAccessibleName("Slurm queue and resource limits")
        controls.addWidget(self.script_queue)
        controls.addWidget(QLabel("Levels"))
        self.script_count = CompactSpinBox()
        self.script_count.setRange(1, 100)
        self.script_count.setFixedSize(48, CONTROL_HEIGHT)
        controls.addWidget(self.script_count)
        controls.addWidget(QLabel("Script RAM"))
        self.script_ram = self._number(1, 1 / 1024, 4096)
        self.script_ram.setFixedSize(NUMERIC_FIELD_WIDTH, CONTROL_HEIGHT)
        controls.addWidget(stepper_with_unit(self.script_ram, "GiB"))
        self.script_add = self._button(controls, "Add", self._add_planned_script)
        controls.addStretch()
        panel.addWidget(ConvergenceToolbarScroll(toolbar))
        self.script_preview = QTableWidget(0, 5)
        self.script_preview.setObjectName("convergenceScriptPreview")
        self.script_preview.setToolTip("Add levels before running. Right-click a planned row to remove it.")
        self.script_preview.setHorizontalHeaderLabels(
            ["Slurm script", "Queue", "Mesh (mm)", "Solver CPUs", "RAM per mesh (GiB)"])
        self.script_preview.verticalHeader().hide()
        self.script_preview.verticalHeader().setDefaultSectionSize(24)
        self.script_preview.setMaximumHeight(146)
        self.script_preview.setMinimumHeight(80)
        self.script_preview.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.script_preview.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.script_preview.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for column in (3, 4):
            self.script_preview.setItemDelegateForColumn(column, ScriptResourceDelegate(self.script_preview))
        self.script_preview.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.script_preview.customContextMenuRequested.connect(self._script_plan_menu)
        panel.addWidget(self.script_preview)
        layout.addWidget(self.script_plan_panel)
        self.script_plan_panel.hide()
        self.script_choice.currentIndexChanged.connect(self._select_planned_script)
        self.script_queue.currentIndexChanged.connect(self._apply_script_queue_limit)

    def _refresh_script_queues(self):
        catalog = getattr(self.remote_controller, "queue_catalog", None)
        if not isinstance(catalog, dict):
            return
        from truba_resources import BASELINES

        current = self.script_queue.currentData()
        initial = current is None
        if initial:
            current = self.remote_controller.controls['partition'].currentData()
        with QSignalBlocker(self.script_queue):
            self.script_queue.clear()
            for name, baseline in BASELINES.items():
                limit = catalog.get(name)
                if limit is None or not limit.available:
                    continue
                cores = min(baseline.max_cpus, limit.max_cpus)
                memory = min(baseline.memory_ceiling(cores), limit.memory_ceiling(cores))
                gpus = min(baseline.max_gpus, limit.max_gpus)
                label = f"{name} · Max CPU {cores} · GPU {gpus} · RAM {memory / 1024:.1f} GiB"
                self.script_queue.addItem(label, name)
                item = self.script_queue.model().item(self.script_queue.count() - 1)
                item.setEnabled(not limit.min_gpus and not baseline.min_gpus)
                item.setToolTip(label if item.isEnabled() else f"{label}\nMesh convergence uses CPU queues.")
            index = self.script_queue.findData(current)
            if index < 0 or not self.script_queue.model().item(index).isEnabled():
                index = next((i for i in range(self.script_queue.count())
                              if self.script_queue.model().item(i).isEnabled()), -1)
            self.script_queue.setCurrentIndex(index)
        self._apply_script_queue_limit(reset=initial or current != self.script_queue.currentData())

    def _apply_script_queue_limit(self, _index=None, *, reset=True):
        name = self.script_queue.currentData()
        self.script_add.setEnabled(name is not None)
        if name is None:
            return
        from truba_resources import BASELINES

        baseline = BASELINES[name]
        limit = self.remote_controller.queue_catalog[name]
        cores = min(baseline.max_cpus, limit.max_cpus)
        memory = min(baseline.memory_ceiling(cores), limit.memory_ceiling(cores))
        self.script_ram.setMaximum(memory / 1024)
        if reset:
            requested = self.remote_controller.controls['memory_mb'].value()
            self.script_ram.setValue(min(requested, memory / 1024))

    def _select_planned_script(self):
        script = self.planned_scripts.get(self.script_choice.currentData())
        if script is not None:
            context, _ = script
            self.script_queue.setCurrentIndex(self.script_queue.findData(context.partition))
            self.script_ram.setValue(context.memory_mb / 1024)

    def _available_planned_sizes(self):
        if self.automatic.isChecked():
            sizes = [self.start_size.value() / self.factor.value() ** i for i in range(self.max_levels.value())]
        else:
            sizes = [float(part.strip()) for part in self.manual.text().split(",")]
            if any(not math.isfinite(size) for size in sizes) or any(a <= b for a, b in zip(sizes, sizes[1:])):
                raise ValueError("Enter strictly decreasing global mesh sizes.")
            sizes = sizes[:self.max_levels.value()]
        return [size for size in sizes if size >= self.minimum.value()]

    def _add_planned_script(self):
        number = self.script_choice.currentData()
        previous, existing = self.planned_scripts.get(number, (None, []))
        try:
            used = [size for _, sizes in self.planned_scripts.values() for size in sizes]
            available = [size for size in self._available_planned_sizes()
                         if not any(math.isclose(size, value) for value in used)]
            count = self.script_count.value()
            if count > len(available) or len(used) + count > self.max_levels.value():
                raise ValueError("There are not enough mesh sizes within the study limits.")
            sizes = sorted([*existing, *available[:count]], reverse=True)
            settings = [self.level_settings(size) for size in sizes]
            walltime = previous.walltime if previous is not None else self._script_walltime()
            context = self.remote_controller.plan_convergence_script(
                settings[0], self.script_queue.currentData(), len(sizes),
                round(self.script_ram.value() * 1024), walltime)
        except (ValueError, RuntimeError, KeyError) as error:
            self._set_status(f"Unable to add Slurm script: {error}")
            return
        if number is None:
            number = self.next_script_number
            self.next_script_number += 1
            self.script_choice.addItem(f"Script {number}", number)
        self.planned_scripts[number] = context, sizes
        self._render_script_preview()
        self._set_status("Slurm plan ready")

    def _render_script_preview(self):
        from truba_remote import convergence_level_resources

        self.script_preview.setRowCount(sum(len(sizes) for _, sizes in self.planned_scripts.values()))
        row = 0
        for number, (context, sizes) in self.planned_scripts.items():
            for size, resources in zip(sizes, convergence_level_resources(context, len(sizes)), strict=True):
                values = (f"Script {number}", context.partition, f"{size:.8g}",
                          str(resources['cores']), f"{resources['memory_mb'] / 1024:.2f}")
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, (number, size))
                    item.setBackground(QColor(("#E5E7EB", "#E5E7EB", "#E5E7EB", "#BFDBFE", "#BBF7D0")[column]))
                    item.setToolTip(f"{context.cores} CPU · {context.memory_mb / 1024:.2f} GiB · {context.walltime}")
                    self.script_preview.setItem(row, column, item)
                row += 1

    def _script_plan_menu(self, position):
        row = self.script_preview.rowAt(position.y())
        if row < 0 or self.future is not None:
            return
        if not self.script_preview.item(row, 0).isSelected():
            self.script_preview.selectRow(row)
        selected = [self.script_preview.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
                    for index in self.script_preview.selectionModel().selectedRows()]
        menu = QMenu(self.script_preview)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.addAction("Remove selected mesh levels", lambda: self._remove_planned_levels(selected))
        menu.popup(self.script_preview.viewport().mapToGlobal(position))

    def _remove_planned_levels(self, selection):
        for number, size in selection:
            context, sizes = self.planned_scripts[number]
            sizes.remove(size)
            if not sizes:
                del self.planned_scripts[number]
                self.script_choice.removeItem(self.script_choice.findData(number))
        self._render_script_preview()

    def _results_context_menu(self, position):
        column = self.results.columnAt(position.x())
        row = self.results.rowAt(position.y())
        self._show_time_menu(column, self.results.viewport().mapToGlobal(position), copy_actions=True, row=row)

    def _header_context_menu(self, position):
        header = self.results.horizontalHeader()
        column = header.logicalIndexAt(position)
        if column in self.time_units:
            self._show_time_menu(column, header.viewport().mapToGlobal(position))

    def _show_time_menu(self, column, position, copy_actions=False, row=-1):
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
        if (self.future is None and self.run_on_truba.isChecked()
                and self.remote_records.get(row, {}).get("failure_memory_mb")):
            if not menu.isEmpty():
                menu.addSeparator()
            menu.addAction("Retry this mesh with more RAM", lambda: self._retry_memory_failure(row))
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

    def _configure_metrics(self, settings=None):
        settings = settings or self.settings
        metrics = response_metrics(settings)
        for column,(label,_) in enumerate(metrics,3):
            self.results.horizontalHeaderItem(column).setText(label)
            self.results.horizontalHeaderItem(column).setToolTip(label)
        if kind(settings)=='compression':
            for column,label in enumerate(('Displ. (mm)' if settings.loading_mode=='force' else 'Force (N)','Modulus (MPa)','SEA (J/g)'),3):
                self.results.horizontalHeaderItem(column).setText(label)
        for check, (label, _) in zip(self.metric_checks, metrics):
            check.setText(label)

    def _set_status(self, message):
        self.status.setText(message)
        self.status.setToolTip(message)
        tone = ("success" if message.startswith("Converged") else
                "error" if "failed" in message.lower() or message.startswith("Insufficient RAM") else
                "warning" if "not confirmed" in message else "")
        if self.status.property("tone") != tone:
            self.status.setProperty("tone", tone)
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)

    def _select_execution_target(self, enabled):
        self.remote_controller.toggle.setChecked(enabled)
        # The shared controller can reject mode changes while another job is active.
        self._sync_execution_target()

    def _sync_execution_target(self):
        with QSignalBlocker(self.run_on_truba):
            self.run_on_truba.setChecked(self.remote_controller.requested)
        self.single_truba_mesh.setVisible(self.run_on_truba.isChecked())
        remote = self.run_on_truba.isChecked()
        self.truba_resource_info.setVisible(remote)
        self.script_plan_panel.setVisible(remote)
        if remote:
            self._refresh_script_queues()
        self.advanced.layout().setRowVisible(self.time_budget_field, not remote)
        self.advanced.layout().setRowVisible(self.truba_wall_field, remote)
        self._show_remote_columns(remote or bool(self.remote_records))
        self._refresh_append_button()

    def _show_remote_columns(self, visible):
        for column in range(len(RESULT_HEADERS) - len(REMOTE_RESULT_HEADERS), len(RESULT_HEADERS)):
            self.results.setColumnHidden(column, not visible)

    def _solver_changed(self):
        if not self.solver_combo.isEnabled():
            return
        remote = self.remote_controller
        self.level_base_settings = self.settings
        if remote is not None:
            if remote.requested and not self.local_solver_before_truba:
                self.local_solver_before_truba = self.solver_combo.currentData()
            self._sync_execution_target()
            remote.apply_solver_combo(self.solver_combo, allow_direct_convergence=True)
            if not remote.requested and self.local_solver_before_truba:
                previous_solver = self.local_solver_before_truba
                self.local_solver_before_truba = None
                self.solver_combo.setCurrentIndex(self.solver_combo.findData(previous_solver))
            if remote.requested and self.solver_combo.currentData() != self.settings.solver_backend:
                active = remote.window.collect()
                solver = self.solver_combo.currentData()
                if active.solver_backend != solver:
                    active = remote.window.solver_profile_store.load(solver, self.settings)
                self.level_base_settings = replace(
                    active, loading_mode=self.settings.loading_mode,
                    compression=self.settings.compression, applied_force=self.settings.applied_force,
                    substeps=self.settings.substeps, minimum_substeps=self.settings.minimum_substeps,
                    maximum_substeps=self.settings.maximum_substeps,
                )
        capabilities = get_solver_capabilities(self.solver_combo.currentData())
        self.solver_combo.setToolTip(capabilities.display_name if capabilities.implemented else
                                    capabilities.unavailable_reason)
        mesh_types = set(capabilities.supported_mesh_types)
        if self.settings.imported_geometry_path:
            mesh_types.intersection_update(TETRAHEDRAL_MESH_TYPES)
        if capabilities.identifier == "kratos" and kind(self.settings) == "flexure":
            mesh_types.intersection_update(("HEX8",))
        if remote is not None and remote.requested and remote.ready:
            mesh_types.intersection_update(remote.capabilities.get("mesh_types", ()))
        for index in range(self.mesh_type_combo.count()):
            self.mesh_type_combo.model().item(index).setEnabled(
                self.mesh_type_combo.itemData(index) in mesh_types)
        if self.mesh_type_combo.currentData() not in mesh_types:
            self.mesh_type_combo.setCurrentIndex(next(
                (i for i, mesh_type in enumerate(MESH_TYPES) if mesh_type in mesh_types), -1))
        self._mesh_type_changed()
        if self.run_on_truba.isChecked() and not remote.ready:
            hint = "Run submits mesh levels directly to the TRUBA queue."
            if not self.rows:
                self._set_status(f"TRUBA · {hint}")
            self.run_button.setToolTip(hint)
        else:
            if not self.rows:
                self._set_status("Ready · uses loading and contact conditions from the main setup")
            self.run_button.setToolTip("Run mesh convergence" if capabilities.implemented else
                                       capabilities.unavailable_reason)

    def _mesh_type_changed(self):
        self._sync_local_size_limit()
        self.local_size.setEnabled(self.mesh_type_combo.currentData() in TETRAHEDRAL_MESH_TYPES)
        self.run_button.setEnabled(
            self.future is None and self._mesh_type_supported() and self._execution_available())

    def _sync_local_size_limit(self):
        if self.mesh_type_combo.currentData() in TETRAHEDRAL_MESH_TYPES:
            self.local_size.setMaximum(self.start_size.value())

    def _mesh_type_supported(self):
        index = self.mesh_type_combo.currentIndex()
        return index >= 0 and self.mesh_type_combo.model().item(index).isEnabled()

    def _execution_available(self):
        if self.run_on_truba.isChecked():
            return self.remote_controller.convergence_solver_enabled(self.solver_combo.currentData())
        return get_solver_capabilities(self.solver_combo.currentData()).implemented

    def _number(self, value, low, high):
        control = CompactDoubleSpinBox()
        control.setRange(low, high)
        control.setValue(value)
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
        if self.remote_study_base is not None:
            level = self._study_level(size)
        else:
            capabilities = get_solver_capabilities(self.solver_combo.currentData())
            level = replace(self.level_base_settings, mesh_resolution=size,
                            mesh_type=self.mesh_type_combo.currentData(),
                            solver_backend=capabilities.identifier,
                            cores=min(self.level_base_settings.cores, capabilities.max_cores))
            if self.run_on_truba.isChecked():
                level = replace(level, cores=MIN_TRUBA_LEVEL_CPUS, automatic_cores=False)
            if level.mesh_type in TETRAHEDRAL_MESH_TYPES:
                local_ratio = self.local_size.value() / self.start_size.value()
                level = replace(level, local_mesh_size=size * local_ratio)
        errors = validate_settings(level)
        if errors:
            raise ValueError(errors[0])
        return level

    def _study_level(self, size):
        base = self.remote_study_base
        level = replace(base, mesh_resolution=size, cores=MIN_TRUBA_LEVEL_CPUS, automatic_cores=False)
        if level.mesh_type in TETRAHEDRAL_MESH_TYPES:
            level = replace(level, local_mesh_size=size * (base.local_mesh_size / base.mesh_resolution))
        return level

    def start(self):
        if self.future is not None or self.saved_jobs:
            return
        previous_base = self.remote_study_base
        self.remote_study_base = None
        explicit = self.run_on_truba.isChecked() and bool(self.planned_scripts)
        self.single_remote_run = self.run_on_truba.isChecked() and self.single_truba_mesh.isChecked() and not explicit
        submissions = []
        try:
            capabilities = get_solver_capabilities(self.solver_combo.currentData())
            if not capabilities.implemented:
                raise ValueError(capabilities.unavailable_reason)
            if not self._execution_available(): raise ValueError("This solver is not available and supported in the selected TRUBA runtime.")
            if not self._mesh_type_supported():
                raise ValueError("Unsupported FE mesh type.")
            self.selected = [i for i, check in enumerate(self.metric_checks) if check.isChecked()]
            if not self.selected:
                raise ValueError("Select at least one response metric in Advanced.")
            self.manual_pending = []
            if explicit:
                all_sizes = [size for _, sizes in self.planned_scripts.values() for size in sizes]
                if len(all_sizes) > self.max_levels.value() or min(all_sizes) < self.minimum.value():
                    raise ValueError("The preview exceeds the current study limits.")
                for number, (context, sizes) in self.planned_scripts.items():
                    settings = [self.level_settings(size) for size in sizes]
                    verified = self.remote_controller.plan_convergence_script(
                        settings[0], context.partition, len(settings), context.memory_mb, context.walltime)
                    if (verified.cores, verified.memory_mb) != (context.cores, context.memory_mb):
                        raise ValueError("The queue limits changed. Review and add the script again.")
                    submissions.append(([(level, f"Script {number}") for level in settings], verified))
                first = max((plan[0][0] for plan, _ in submissions), key=lambda level: level.mesh_resolution)
            elif self.automatic.isChecked() or self.single_remote_run:
                first = self.level_settings(self.start_size.value())
            else:
                sizes = [float(v.strip()) for v in self.manual.text().split(",")]
                if len(sizes) < 3 or any(a <= b for a, b in zip(sizes, sizes[1:])):
                    raise ValueError("Enter at least three strictly decreasing global sizes.")
                self.manual_pending = [self.level_settings(size) for size in sizes]
                first = self.manual_pending.pop(0)
            if first.mesh_resolution < self.minimum.value():
                raise ValueError("Starting size is below the minimum global size.")
        except (ValueError, RuntimeError, KeyError) as exc:
            self.remote_study_base = previous_base
            QMessageBox.warning(self, "Invalid convergence plan", str(exc))
            return
        self.history_only = False
        self.explicit_script_run = explicit
        self.saved_geometry_source = None
        self.saved_study_key = None
        self.study_plan = {}
        if self.run_on_truba.isChecked():
            self.remote_study_base = first
            self.study_plan = self._study_snapshot(first)
        self._configure_metrics(first)
        self.rows.clear()
        self.remote_records.clear()
        self._show_remote_columns(self.run_on_truba.isChecked())
        self.recommended = None
        self.consecutive_failures = 0
        self.results.setRowCount(0)
        self.stop_requested.clear()
        self.started = monotonic()
        self._set_running(True)
        if explicit:
            auth = self.remote_controller.auth()
            for plan, context in submissions:
                self._submit_remote_levels(plan, context, auth, exact_group=True)
            if self.future is None:
                self.remote_outcomes.clear()
                self._finish(self.rows[-1].status)
        else:
            self._launch(first, "Starting mesh")
        if self.future is not None:
            self.timer.start()

    def _set_running(self, running):
        for control in (self.mesh_settings, self.tolerance, self.advanced, self.solver_combo,
                        self.run_on_truba, self.script_plan_panel):
            control.setEnabled(not running)
        if running:
            self.run_button.setEnabled(False)
        else:
            self._solver_changed()
        self.stop_button.setEnabled(running)
        self.force_stop_button.setEnabled(running)
        self.export_button.setEnabled(not running and bool(self.rows))
        self.apply_button.setEnabled(not running and self.recommended is not None)
        self.history_combo.setEnabled(not running)
        self._refresh_append_button()

    def _study_snapshot(self, first):
        return dict(id=uuid4().hex, automatic=self.automatic.isChecked(),
                    factor=self.factor.value(), minimum=self.minimum.value(),
                    max_levels=self.max_levels.value(), max_elements=self.max_elements.value(),
                    wall_minutes=self.truba_wall_minutes.value(), tolerance=self.tolerance.value(),
                    selected=self.selected, absolute=[control.value() for control in self.absolute],
                    manual_sizes=[first.mesh_resolution, *(level.mesh_resolution for level in self.manual_pending)],
                    single=self.single_remote_run, explicit_scripts=self.explicit_script_run)

    def _next_append_sizes(self):
        limit = min(2, self.max_levels.value() - len(self.rows))
        if limit <= 0 or self.single_remote_run or not self.rows:
            return []
        if not self.automatic.isChecked():
            return [level.mesh_resolution for level in self.manual_pending[:limit]
                    if level.mesh_resolution >= self.minimum.value()]
        finest = min(row.settings.mesh_resolution for row in self.rows)
        return [size for offset in range(1, limit + 1)
                if (size := finest / self.factor.value() ** offset) >= self.minimum.value()]

    def _refresh_append_button(self):
        self.append_button.setVisible(self.remote_controller is not None and
                                      (self.run_on_truba.isChecked() or bool(self.remote_records)))
        self.append_button.setEnabled(bool(self.remote_jobs or self.saved_jobs)
                                      and not self.explicit_script_run
                                      and not self.stop_requested.is_set()
                                      and bool(self._next_append_sizes()))

    def append_remote_levels(self):
        if not self.append_button.isEnabled():
            return
        sizes = self._next_append_sizes()
        manual = not self.automatic.isChecked()
        try:
            if self.saved_study_key and getattr(self.remote_controller.auth(), "username", None) != self.saved_study_key[0]:
                raise ValueError("Connect to TRUBA with this study's account to add mesh levels.")
            if self.saved_geometry_source is not None:
                geometry = self.saved_geometry_source.saved_geometry()
                self.remote_study_base = replace(self.remote_study_base, imported_geometry_path=str(geometry))
                self.manual_pending = [self._study_level(level.mesh_resolution) for level in self.manual_pending]
                self.saved_geometry_source = None
            plan = [(self.level_settings(size), "Manual" if manual else "Refinement") for size in sizes]
            if not self.remote_controller.requested:
                self.remote_controller.toggle.setChecked(True)
            context, auth = self._require_remote_submission(plan[0][0], len(plan))
            if self.saved_study_key and getattr(auth, "username", None) != self.saved_study_key[0]:
                raise ValueError("Connect to TRUBA with this study's account to add mesh levels.")
        except (ValueError, RuntimeError, OSError) as error:
            self._set_status(f"Failed to add mesh levels: {error}")
            return
        if manual:
            del self.manual_pending[:len(plan)]
        self.history_only = False
        self._submit_remote_levels(plan, context, auth)
        if self.future is not None:
            self.timer.start()

    def _refresh_saved_studies(self):
        if self.remote_controller is None:
            return {}
        from convergence_history import saved_mesh_studies

        current = self.history_combo.currentData()
        state_dir = self.remote_controller.window.truba_dialog.state_dir
        if not isinstance(state_dir, Path):
            return {}
        studies = saved_mesh_studies(state_dir)
        self.history_combo.clear()
        for key, jobs in studies.items():
            first = jobs[0]
            settings = first.levels[0]
            name = first.record.get("study_name") or settings.imported_geometry_name or settings.structure_name
            self.history_combo.addItem(f"{name} · Slurm {first.record['job_id']}", key)
        index = self.history_combo.findData(current)
        if index >= 0:
            self.history_combo.setCurrentIndex(index)
        self.history_combo.setVisible(bool(studies))
        return studies

    def _restore_saved_study(self, index, studies=None):
        if self.future is not None:
            return
        key = self.history_combo.itemData(index)
        jobs = (studies if studies is not None else self._refresh_saved_studies()).get(key)
        if not jobs:
            return
        self.history_combo.setCurrentIndex(self.history_combo.findData(key))
        self.saved_study_key = key
        self.history_only = True
        self.saved_jobs.clear()
        self.remote_outcomes.clear()
        self.remote_records.clear()
        self.rows.clear()
        self.results.setRowCount(0)
        self.recommended = None
        self.stop_requested.clear()
        self.consecutive_failures = 0
        self.started = monotonic()
        self.remote_study_base = base = jobs[0].levels[0]
        self.saved_geometry_source = jobs[0] if base.imported_geometry_path else None
        for combo, value in ((self.solver_combo, base.solver_backend), (self.mesh_type_combo, base.mesh_type)):
            with QSignalBlocker(combo):
                combo.setCurrentIndex(combo.findData(value))
        saved = next((job.plan for job in jobs if job.plan), {})
        selection = saved.get("selected", jobs[0].request.get("selected")) or list(range(len(METRICS)))
        self.selected = selection
        self.single_remote_run = bool(saved.get("single"))
        self.explicit_script_run = bool(saved.get("explicit_scripts"))
        original_wall = jobs[0].record.get("context", {}).get("walltime", "00:30:00")
        try:
            hours, minutes, _seconds = map(int, original_wall.split(":"))
            wall_minutes = hours * 60 + minutes
        except ValueError:
            wall_minutes = 30
        second = jobs[0].levels[1] if len(jobs[0].levels) > 1 else None
        factor = base.mesh_resolution / second.mesh_resolution if second else 1.3
        self.automatic.setChecked(saved.get("automatic", True))
        self.start_size.setValue(base.mesh_resolution)
        self.local_size.setValue(base.local_mesh_size)
        for name, default in (
            ("tolerance", 2), ("factor", factor), ("minimum", base.mesh_resolution / 10),
            ("max_levels", max(12, sum(len(job.levels) for job in jobs))),
            ("max_elements", jobs[0].request.get("max_elements", self.max_elements.value())),
            ("truba_wall_minutes", saved.get("wall_minutes", wall_minutes)),
        ):
            control = getattr(self, name)
            with QSignalBlocker(control):
                control.setValue(saved.get(name, default))
        for index, check in enumerate(self.metric_checks):
            check.setChecked(index in selection)
        for control, value in zip(self.absolute, saved.get("absolute", [1e-9] * len(METRICS)), strict=True):
            control.setValue(value)
        self.manual_pending = []
        manual_sizes = saved.get("manual_sizes", [])
        if manual_sizes and not self.automatic.isChecked():
            self.manual.setText(", ".join(map(str, manual_sizes)))
            attempted = [level.mesh_resolution for job in jobs for level in job.levels]
            self.manual_pending = [self._study_level(size) for size in manual_sizes
                                   if not any(math.isclose(size, used) for used in attempted)]
        self.study_plan = saved or {**self._study_snapshot(base), "id": key[1]}
        with QSignalBlocker(self.run_on_truba):
            self.run_on_truba.setChecked(True)
        self.single_truba_mesh.setChecked(self.single_remote_run)
        self.single_truba_mesh.show()
        self.truba_resource_info.show()
        self.script_plan_panel.show()
        self._refresh_script_queues()
        self.advanced.layout().setRowVisible(self.time_budget_field, False)
        self.advanced.layout().setRowVisible(self.truba_wall_field, True)
        self._configure_metrics(base)
        self._show_remote_columns(True)
        for job in jobs:
            stages = job.plan.get("kinds", [])
            group = tuple(self._add_level(level, stage, f"TRUBA · {job.record.get('state', 'submitted')}")
                          for level, stage in zip(job.levels, stages or ["Saved mesh"] * len(job.levels), strict=True))
            self._update_remote_records(group, job.record)
            for row in group:
                self.remote_records[row].setdefault("solver_cores", self.rows[row].settings.cores)
            results = job.cached_results()
            if results is None:
                self.saved_jobs[group] = job
            else:
                for row, result in zip(group, results, strict=True):
                    self._collect_level(row, result, self.remote_records[row])
        self._refresh_differences()
        if self.saved_jobs:
            self._set_running(True)
            self.stop_button.setEnabled(False)
            self.force_stop_button.setEnabled(False)
            self.history_combo.setEnabled(True)
            self._set_status("Saved TRUBA jobs · connect to refresh their status")
            self._attach_saved_jobs()
        else:
            self._finish_saved_study()

    def _attach_saved_jobs(self, _connected=True):
        if not _connected or not self.saved_jobs or self.remote_controller.closing:
            return
        auth = self.remote_controller.auth()
        if auth is None or auth.username != self.saved_study_key[0]:
            return
        from truba_remote import resume_convergence_levels

        for group, job in list(self.saved_jobs.items()):
            try:
                self.remote_jobs[group] = AnalysisProcess(resume_convergence_levels, job.path, remote_auth=auth)
            except (ValueError, OSError, RuntimeError) as error:
                self._set_status(f"Failed to track saved TRUBA jobs: {error}")
                continue
            del self.saved_jobs[group]
        self.future = next(iter(self.remote_jobs.values()), None)
        if self.future is not None:
            self._set_running(True)
            self.timer.start()

    def _finish_saved_study(self):
        self.recommended = converged_level(
            self.rows, self.tolerance.value(), self.selected, [control.value() for control in self.absolute])
        self._finish("Saved TRUBA mesh levels restored.")

    def _launch(self, settings, kind):
        if self.run_on_truba.isChecked():
            self._launch_remote_batch(settings, kind)
            return
        self._add_level(settings, kind)
        self.updates = get_context("spawn").Queue()
        try:
            self.future = AnalysisProcess(
                _solve_level_process, settings, self.mesh_generator, self.solver,
                self.updates, self.stop_requested, self.max_elements.value(), self.selected)
        except Exception as exc:
            self.rows[-1].status = f"Failed: {exc}"
            self._render(len(self.rows) - 1)
            self._close_updates()
            self._finish(f"Failed to start convergence: {exc}")

    def _add_level(self, settings, kind, status="Meshing"):
        self.rows.append(Level(settings, kind=kind, status=status))
        self.results.insertRow(len(self.rows) - 1)
        self.stage_started = monotonic()
        self._render(len(self.rows) - 1)
        self.results.scrollToBottom()
        self._set_status(f"{kind}: {settings.mesh_resolution:.6g} mm")
        return len(self.rows) - 1

    def _script_walltime(self):
        hours, minutes = divmod(self.truba_wall_minutes.value(), 60)
        return f"{hours:02d}:{minutes:02d}:00"

    def _require_remote_submission(self, settings, max_jobs):
        return self.remote_controller.require_convergence(
            settings, max_jobs=max_jobs, walltime=self._script_walltime())

    def _launch_remote_batch(self, settings, kind):
        limit = 1 if self.single_remote_run else min(TRUBA_BATCH_SIZE, self.max_levels.value() - len(self.rows))
        if not self.automatic.isChecked():
            limit = min(limit, 1 + len(self.manual_pending))
        try:
            context, auth = self._require_remote_submission(settings, limit)
        except Exception as exc:
            message = f"Failed to start convergence: {exc}"
            self._add_level(settings, kind, message)
            self._finish(message)
            return
        limit = min(limit, context.parallel_jobs)
        plan = [(settings, kind)]
        if self.automatic.isChecked():
            attempted = [row.settings.mesh_resolution for row in self.rows]
            size = settings.mesh_resolution
            while len(plan) < limit:
                size /= self.factor.value()
                if size < self.minimum.value():
                    break
                if any(math.isclose(size, previous) for previous in attempted):
                    continue
                try:
                    plan.append((self.level_settings(size), "Refinement"))
                except ValueError:
                    break
        else:
            while len(plan) < limit and self.manual_pending and self.manual_pending[0].mesh_resolution >= self.minimum.value():
                plan.append((self.manual_pending.pop(0), "Manual"))

        self._submit_remote_levels(plan, context, auth)

    def _submit_remote_levels(self, plan, context, auth, *, exact_group=False):
        from truba_remote import (convergence_level_resources, run_remote_level, run_remote_level_group,
                                  run_remote_level_pair)

        context = replace(context, session_id="", parallel_jobs=1)
        indices = [self._add_level(settings, kind, "TRUBA · preparing submission")
                   for settings, kind in plan]
        group_size = len(plan) if exact_group else (
            2 if context.cores >= 2 * plan[0][0].cores and context.memory_mb >= 2 else 1)
        for offset in range(0, len(indices), group_size):
            group = tuple(indices[offset:offset + group_size])
            levels = [self.rows[index].settings for index in group]
            if len(group) == 1:
                target, settings = run_remote_level, replace(levels[0], cores=context.cores)
            else:
                target = run_remote_level_pair if len(group) == 2 else run_remote_level_group
                settings = levels
            for index, share in zip(group, convergence_level_resources(context, len(group)), strict=True):
                self.remote_records[index] = dict(solver_cores=share['cores'], level_memory_mb=share['memory_mb'])
                self._render(index)
            try:
                self.remote_jobs[group] = AnalysisProcess(
                    target, settings, context, self.max_elements.value(), self.selected,
                    remote_auth=auth, remote_options={"study": {
                        **self.study_plan, "row": group[0], "kinds": [self.rows[index].kind for index in group],
                    }})
            except Exception as exc:
                for index in group:
                    self.rows[index].status = f"Failed to start convergence: {exc}"
                    self.remote_outcomes[index] = False
                    self._render(index)
        self.future = next(iter(self.remote_jobs.values()), None)
        self._refresh_append_button()
        if self.future is None and not exact_group:
            self.remote_outcomes.clear()
            self._finish(self.rows[-1].status)
        elif self.future is not None:
            self._set_status(f"TRUBA · {sum(map(len, self.remote_jobs))} analyses active")

    def _retry_memory_failure(self, row):
        memory_mb = self.remote_records.get(row, {}).get("failure_memory_mb")
        if self.future is not None or not self.run_on_truba.isChecked() or not memory_mb:
            return
        settings = replace(self.rows[row].settings, cores=MIN_TRUBA_LEVEL_CPUS)
        try:
            context, auth = self._require_remote_submission(settings, 1)
            if context.memory_mb <= memory_mb:
                raise ValueError(f"This mesh previously exceeded {memory_mb / 1024:.1f} GiB. "
                                 "Increase the TRUBA RAM request or select a queue with more RAM.")
        except (ValueError, RuntimeError, OSError) as exc:
            self._set_status(f"Failed to retry mesh: {exc}")
            return
        self.single_remote_run = True
        self.recommended = None
        self.stop_requested.clear()
        self.started = monotonic()
        self._set_running(True)
        self._submit_remote_levels([(settings, "RAM retry")], context, auth)
        if self.future is not None:
            self.timer.start()

    def request_stop(self):
        self.stop_requested.set()
        self._refresh_append_button()
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
        if self.remote_jobs:
            errors = []
            for group, job in list(self.remote_jobs.items()):
                record = job.remote_status()
                self._update_remote_records(group, record)
                try:
                    job.force_stop()
                except Exception as exc:
                    errors.append(str(exc))
                    continue
                del self.remote_jobs[group]
                for index in group:
                    self.rows[index].status = "Cancelled (Forced Stop)"
            self.future = next(iter(self.remote_jobs.values()), None)
            self._refresh_differences()
            if errors:
                self._set_status(f"Forced Stop failed: {errors[0]}")
                return
            self.remote_outcomes.clear()
            self.manual_pending.clear()
            self._finish("Cancelled (Forced Stop). Partial results can be exported.")
            return
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
        mesh = f"{level.settings.mesh_type} · {level.settings.mesh_resolution:.6g} mm"
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
        record = self.remote_records.get(index, {})
        remote_values = self._remote_values(record)
        script = record.get("script_name")
        script_paths = "\n".join(f"{record[field]}/{script}" for field in
                                 ("local_directory", "remote_directory") if record.get(field) and script)
        resources = record.get("context", {})
        if script_paths and resources.get("cores"):
            script_paths += (f"\nRequested CPUs: {resources['cores']} · Solver CPUs: {record.get('solver_cores') or '—'}"
                             f"\nRAM: {resources['memory_mb'] / 1024:g} GiB · Time: {resources['walltime']}"
                             f"\nQueue request: {resources['partition']}")
            if record.get('level_memory_mb'):
                script_paths += f"\nRAM for this level: {record['level_memory_mb'] / 1024:g} GiB"
        values = [mesh, self._format_time(mesh_time, 1), self._format_time(solve_time, 2),
                  *[f"{float(v):.6g}" if self._numeric(v) else "—" for v in level.metrics],
                  delta, level.status, *(value or "—" for value in remote_values)]
        tooltips = {6: delta_hint, 8: values[8], 9: script_paths or values[9], 10: values[10]}
        if memory_mb := record.get("failure_memory_mb"):
            tooltips[7] = f"{level.status}\nRequested RAM: {memory_mb / 1024:.1f} GiB"
            if directory := record.get("local_directory"):
                tooltips[7] += f"\n{Path(directory) / 'slurm.err'}"
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
            item.setToolTip(tooltips.get(col, detail))
            item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter |
                                  (Qt.AlignmentFlag.AlignRight if 1 <= col <= 5 or col in (11, 12, 13)
                                   else Qt.AlignmentFlag.AlignLeft))
            self.results.setItem(index, col, item)

    def _poll(self):
        if self.future is None:
            return
        if self.remote_jobs:
            self._poll_remote_batch()
            return
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
        index = len(self.rows) - 1
        retry_allowed = self._collect_level(index, self._job_result(self.future))
        self.future = None
        # The worker can finish just after queue draining.
        self._close_updates()
        self._after_levels([(index, retry_allowed)])

    @staticmethod
    def _remote_values(record):
        state = ("OUT_OF_MEMORY" if record.get("failure_kind") == "out_of_memory" else
                 record.get("slurm_status") or record.get("step_status") or record.get("state", ""))
        allocated = record.get('level_memory_mb', record.get('context', {}).get('memory_mb'))
        peak = record.get('peak_ram_mib')
        return (str(record.get("job_id", "")), record.get("script_name", ""), state,
                str(record.get('solver_cores') or ''),
                f'{allocated / 1024:.2f}' if allocated is not None else '',
                f'{peak / 1024:.2f}' if peak is not None else '')

    def _poll_remote_batch(self):
        for group, job in list(self.remote_jobs.items()):
            done = job.done()
            changed = self._update_remote_records(group, job.remote_status())
            if done:
                results = self._job_result(job)
                if len(group) == 1 or results is None or isinstance(results, Exception):
                    results = [results] * len(group)
                for index, result in zip(group, results, strict=True):
                    self.remote_outcomes[index] = self._collect_level(index, result, self.remote_records.get(index))
                    self._render(index)
                del self.remote_jobs[group]
            else:
                for index in changed:
                    record = self.remote_records[index]
                    stage = record.get('level_stage') or record.get('state', 'preparing submission')
                    self.rows[index].status = f"TRUBA · {stage}"
                    self._render(index)
        self.future = next(iter(self.remote_jobs.values()), None)
        if self.future is not None:
            if not self.stop_requested.is_set():
                self._set_status(f"TRUBA · {sum(map(len, self.remote_jobs))} analyses active")
            return
        if self.saved_jobs:
            self.timer.stop()
            self._refresh_append_button()
            return
        outcomes = sorted(self.remote_outcomes.items())
        self.remote_outcomes.clear()
        self._after_levels(outcomes)

    def _update_remote_records(self, group, snapshot):
        changed = []
        if isinstance(snapshot, dict) and snapshot:
            shares = snapshot.get('level_resources')
            progress = snapshot.get('level_progress', ())
            peaks = snapshot.get('level_peak_ram_mib', ())
            for offset, index in enumerate(group):
                record = dict(snapshot)
                previous = self.remote_records.get(index, {})
                for name in ('solver_cores', 'level_memory_mb'):
                    if not record.get(name) and name in previous:
                        record[name] = previous[name]
                if shares and len(shares) == len(group):
                    record.update(solver_cores=shares[offset]['cores'], level_memory_mb=shares[offset]['memory_mb'])
                if offset < len(peaks) and peaks[offset] is not None:
                    record['peak_ram_mib'] = peaks[offset]
                if offset < len(progress) and str(record.get('state', '')).startswith('RUNNING'):
                    record['level_stage'] = progress[offset]
                if record != previous:
                    self.remote_records[index] = record
                    changed.append(index)
        return changed

    @staticmethod
    def _job_result(job):
        try:
            return job.result()
        except Exception as error:
            return error

    def _collect_level(self, index, result, remote_record=None):
        from truba_remote import SlurmOutOfMemory

        remote_record = remote_record or {}
        previous = self.rows[index]
        retry_allowed = True
        try:
            if isinstance(result, Exception):
                raise result
            level = result
            if level is None:
                level = replace(previous, status="TRUBA job detached")
                retry_allowed = False
        except CancelledError:
            level = replace(previous, status="Analysis cancelled")
            retry_allowed = False
        except Exception as exc:
            level = replace(previous, status=f"Failed: {exc}")
            if isinstance(exc, SlurmOutOfMemory) or remote_record.get("failure_kind") == "out_of_memory":
                memory_mb = exc.memory_mb if isinstance(exc, SlurmOutOfMemory) else remote_record.get("failure_memory_mb")
                memory_mb = remote_record.get('level_memory_mb', memory_mb)
                self.remote_records[index] = dict(remote_record, failure_kind="out_of_memory", failure_memory_mb=memory_mb)
                level.status = "Insufficient RAM"
                retry_allowed = False
            elif remote_record.get("submission_attempted") or remote_record.get("job_id"):
                from truba_jobs import SLURM_TERMINAL_STATES
                accounting = remote_record.get("slurm_status") or remote_record.get("step_status", "")
                state = accounting.partition("|")[0].partition(" ")[0].rstrip("+")
                retry_allowed = (state in SLURM_TERMINAL_STATES and state != "CANCELLED"
                                 and remote_record.get("state") != "cancelled")
        level.kind = previous.kind
        self.rows[index] = level
        return retry_allowed

    def _after_levels(self, outcomes):
        self._refresh_differences()
        if self.history_only and not self.stop_requested.is_set():
            self._finish_saved_study()
            return
        if not self.run_on_truba.isChecked() and monotonic() - self.started >= 60 * self.max_minutes.value():
            self._finish("Time budget reached; convergence not confirmed.")
            return
        if self.stop_requested.is_set():
            self._finish("Stopped; convergence not confirmed. Partial results can be exported.")
            return
        if any(self.remote_records.get(index, {}).get("failure_kind") == "out_of_memory" for index, _ in outcomes):
            self._finish("Insufficient RAM. Further refinement stopped. Right-click the affected row to retry with more RAM.")
            return
        if self.single_remote_run:
            level = self.rows[outcomes[-1][0]]
            message = "Single mesh completed." if level.status == "completed" else level.status
            self._finish(message)
            return
        for index, retry_allowed in outcomes:
            level = self.rows[index]
            if not retry_allowed or level.status.startswith("Element limit exceeded"):
                self._finish(f"Convergence not confirmed: {level.status}")
                return
            if level.status == "completed":
                self.consecutive_failures = 0
            else:
                self.consecutive_failures += 1
                if self.consecutive_failures > MAX_FAILURE_RETRIES:
                    self._finish(f"Convergence not confirmed after {MAX_FAILURE_RETRIES} failed retries. "
                                 f"{level.status}")
                    return
        self.recommended = converged_level(
            self.rows, self.tolerance.value(), self.selected, [c.value() for c in self.absolute])
        if self.recommended is not None:
            index = next(i for i, v in enumerate(self.rows) if v is self.recommended)
            self.results.selectRow(index)
            self._finish(f"Converged · use #{index + 1}, {self.recommended.settings.mesh_resolution:.6g} mm. "
                         "Two consecutive comparisons meet tolerance.")
            return
        if self.explicit_script_run:
            self._finish("Planned levels finished; convergence not confirmed.")
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
        if self.consecutive_failures:
            kind = f"Retry {self.consecutive_failures}/{MAX_FAILURE_RETRIES}"
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
        if self.study_plan:
            self._refresh_saved_studies()
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
                                 "Final strain (%)", "Status", "Run folder", "Final force (N)", *ANALYSIS_FIELDS,
                                 *REMOTE_RESULT_HEADERS, "TRUBA job folder", "Requested CPUs"))
                for i, v in enumerate(self.rows):
                    record = self.remote_records.get(i, {})
                    writer.writerow((i + 1, v.kind, v.settings.mesh_resolution,
                                     v.settings.local_mesh_size if v.settings.mesh_type in TETRAHEDRAL_MESH_TYPES else "",
                                     v.elements, v.mesh_seconds, v.solve_seconds, *v.metrics,
                                     v.reference + 1 if v.reference is not None else "",
                                     *(v.differences or [None] * 3), v.final_strain, v.status, v.folder,
                                     v.final_force, *(getattr(v.settings, name) for name in ANALYSIS_FIELDS),
                                     *self._remote_values(record), record.get("remote_directory", ""),
                                     record.get("context", {}).get("cores", "")))
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))

    def dispose(self):
        self.timer.stop()
        self.stop_requested.set()
        self.force_stop()
        if self.future is None:
            self._close_updates()

    def detach_remote_jobs(self):
        jobs = list(self.remote_jobs.values())
        if jobs:
            self.remote_jobs.clear()
            self.remote_outcomes.clear()
            self.future = None
            self.dispose()
        return jobs
