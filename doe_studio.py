"""Design of Experiments (DoE) generator, Excel-like table, and Batch Solver Runner.

Supports Full Factorial (Grid) and Latin Hypercube Sampling (LHS) across all
TPMS, Truss Lattice, and Porous structures. Collects mechanical metrics for Deep
Learning: Effective Modulus (E*), Effective Poisson's Ratio (nu*), Energy
Absorption (EA), Specific Energy Absorption (SEA), Volumetric EA, Max Force, and
Max Von Mises Stress.
"""
from __future__ import annotations

import csv
import hashlib
import json
import copy
import math
import os
import time
from analysis_process import AnalysisProcess
from doe_execution import ExperimentWorker
from truba_parallel import TrubaParallelMixin
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from typing import Callable

import numpy as np
from PySide6.QtCore import QSignalBlocker, QTimer, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFrame,
    QHeaderView,
    QLabel,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from file_dialog import QFileDialog
from model import MIN_MESH_INPUT_MM, ROOT, Settings, settings_from_dict, validate_geometry
from studio_style import ActionFlowLayout, CONTROL_HEIGHT, NumericLineEdit
from structure_library import (
    TPMS_MORPHOLOGIES,
    resolve_family_and_name,
)


DOE_ACTION_WIDTH = 96
DOE_RUN_WIDTH = 140
DOE_STOP_WIDTH = 108
DOE_TOOLBAR_GAP = 6
LEARNING_PLAN_TAB = 2
MAX_DOE_ROWS = 5000
SNAPSHOT_HEADER = "Design settings (JSON)"

# Field, label, input bounds and default range.
FACTOR_SPECS = (
    ("cell_size", "2. Cell Size (mm)", 0.1, 100.0, 2.0, 4.0),
    ("solid_fraction", "3. Density (%)", 1.0, 99.0, 15.0, 45.0),
)
SIZE_FACTOR_SPEC = ("size", "Cube size (mm)", 1.0, 100.0, 6.0, 8.0)


@dataclass
class ExperimentRow:
    exp_id: str
    enabled: bool
    structure_family: str
    structure_name: str
    tpms_morphology: str
    size: float
    cell_size: float
    solid_fraction: float
    mesh_resolution: float
    compression: float
    random_seed: int
    actual_density: float | None = None
    mass_g: float | None = None
    hex8_elements: int | None = None
    status: str = "Pending"
    max_force_n: float | None = None
    energy_absorption_j: float | None = None
    specific_energy_absorption_j_g: float | None = None
    volumetric_ea_j_cm3: float | None = None
    effective_modulus_mpa: float | None = None
    poisson_ratio: float | None = None
    max_von_mises_mpa: float | None = None
    run_folder: str = ""
    design_settings: Settings | None = None

    target_strain_pct: float | None = None
    started_at: str = ""
    duration_s: float | None = None
    attempts: int = 0

    def dataset_columns(self) -> list:
        """Return stable geometry grouping and analysis metadata for CSV export."""
        geometry = (
            self.structure_family.casefold(), self.structure_name.casefold(),
            self.tpms_morphology.casefold() if self.structure_family.casefold() == "tpms" else "",
            *(float(value) for value in (self.size, self.cell_size, self.solid_fraction)),
        )
        if self.design_settings is not None and self.design_settings.test_profile != 'custom':
            geometry += (self.design_settings.test_profile,self.design_settings.specimen_architecture,self.design_settings.core_fraction)
        group_id = hashlib.sha256(json.dumps(geometry).encode("utf-8")).hexdigest()
        bucket = int(group_id[:8], 16) % 100
        split = "train" if bucket < 70 else "validation" if bucket < 85 else "test"
        strain = 100 * self.compression / self.size if self.size > 0 else ""
        curve = Path(self.run_folder) / "04_Graphs" / "force_displacement.csv"
        return [
            group_id, split, strain, self.target_strain_pct, self.run_folder,
            str(curve) if self.run_folder and curve.is_file() else "",
        ]

    def invalidate_results(self) -> None:
        """Clear values derived from the previous analysis inputs."""
        self.actual_density = None
        self.mass_g = None
        self.hex8_elements = None
        self.status = "Pending"
        self.max_force_n = None
        self.energy_absorption_j = None
        self.specific_energy_absorption_j_g = None
        self.volumetric_ea_j_cm3 = None
        self.effective_modulus_mpa = None
        self.poisson_ratio = None
        self.max_von_mises_mpa = None
        self.run_folder = ""

    def to_settings(self, base: Settings) -> Settings:
        fam, name = resolve_family_and_name(self.structure_family, self.structure_name)
        return replace(
            self.design_settings or base,
            structure_family=fam,
            structure_name=name,
            tpms_morphology=self.tpms_morphology,
            size=float(self.size),
            cell_size=float(self.cell_size),
            solid_fraction=float(self.solid_fraction),
            mesh_resolution=float(self.mesh_resolution),
            compression=float(self.compression),
            random_seed=int(self.random_seed),
        )


def _lhs_samples(
    n_samples: int,
    ranges: list[tuple[float, float]],
    seed: int = 42,
) -> np.ndarray:
    """Generate Latin Hypercube Samples in [low, high] for each dimension."""
    rng = np.random.default_rng(int(seed))
    dims = len(ranges)
    result = np.zeros((n_samples, dims), dtype=float)
    for d, (low, high) in enumerate(ranges):
        if np.isclose(low, high):
            result[:, d] = low
            continue
        cut = np.linspace(0.0, 1.0, n_samples + 1)
        u = rng.uniform(low=cut[:-1], high=cut[1:], size=n_samples)
        rng.shuffle(u)
        result[:, d] = low + u * (high - low)
    return result


def generate_doe_rows(
    designs: list[Settings],
    factors: dict[str, tuple[float, float]],
    lhs_count: int,
    sampling_seed: int,
    target_strain_pct: float | None = None,
) -> list[ExperimentRow]:
    """Expand the selected factors for every saved design."""
    rows = []
    samples = _lhs_samples(lhs_count, list(factors.values()), seed=sampling_seed) if factors else [()]
    for design in designs:
        for sample in samples:
            settings = replace(design, **dict(zip(factors, map(float, sample))))
            rows.append(ExperimentRow(
                exp_id=f"EXP-{len(rows) + 1:04d}",
                enabled=True,
                structure_family=settings.structure_family,
                structure_name=settings.structure_name,
                tpms_morphology=settings.tpms_morphology,
                size=settings.size,
                cell_size=settings.cell_size,
                solid_fraction=settings.solid_fraction,
                mesh_resolution=settings.mesh_resolution,
                compression=(
                    settings.size * target_strain_pct / 100
                    if target_strain_pct is not None else settings.compression
                ),
                target_strain_pct=target_strain_pct,
                random_seed=settings.random_seed,
                design_settings=design,
            ))
    return rows


TABLE_COLUMNS: tuple[str, ...] = (
    "Exp ID",
    "Run",
    "Family",
    "Structure",
    "Morphology",
    "Cube size (mm)",
    "Cell / Pore (mm)",
    "Target Density (%)",
    "Mesh Size (mm)",
    "Compression (mm)",
    "Seed",
    "Actual Density (%)",
    "Mass (g)",
    "FE Elements",
    "Status",
    "Max Force (N)",
    "EA (J)",
    "SEA (J/g)",
    "Vol. EA (J/cm³)",
    "Effective E* (MPa)",
    "Poisson Ratio (ν*)",
    "Max Von Mises (MPa)",
)

COL_EXP_ID = 0
COL_RUN = 1
COL_FAMILY = 2
COL_STRUCTURE = 3
COL_MORPH = 4
COL_SIZE = 5
COL_CELL = 6
COL_DENSITY = 7
COL_MESH = 8
COL_COMP = 9
COL_SEED = 10
COL_ACT_DEN = 11
COL_MASS = 12
COL_ELEMS = 13
COL_STATUS = 14
COL_FORCE = 15
COL_EA = 16
COL_SEA = 17
COL_VOL_EA = 18
COL_MODULUS = 19
COL_POISSON = 20
COL_STRESS = 21

# Result columns restored from an exported table: (header, row attribute, integer).
RESULT_FIELDS = (
    (TABLE_COLUMNS[COL_ACT_DEN], "actual_density", False),
    (TABLE_COLUMNS[COL_MASS], "mass_g", False),
    (TABLE_COLUMNS[COL_ELEMS], "hex8_elements", True),
    (TABLE_COLUMNS[COL_FORCE], "max_force_n", False),
    (TABLE_COLUMNS[COL_EA], "energy_absorption_j", False),
    (TABLE_COLUMNS[COL_SEA], "specific_energy_absorption_j_g", False),
    (TABLE_COLUMNS[COL_VOL_EA], "volumetric_ea_j_cm3", False),
    (TABLE_COLUMNS[COL_MODULUS], "effective_modulus_mpa", False),
    (TABLE_COLUMNS[COL_POISSON], "poisson_ratio", False),
    (TABLE_COLUMNS[COL_STRESS], "max_von_mises_mpa", False),
)
RESULTS_ENV = "RUSTMECH_DOE_RESULTS"
ACTIVE_STATUS_PREFIXES = ("Checking", "Solving", "TRUBA")
INTERRUPTED_STATUS = "Interrupted - check solver/job before retry"


def results_directory() -> Path:
    """Per-session result files; tests and users may redirect them."""
    return Path(os.environ.get(RESULTS_ENV) or ROOT / "DoEResults")


def mark_interrupted(rows) -> None:
    """A saved or exported row cannot still be running: flag it for manual review."""
    for row in rows:
        if row.status.startswith(ACTIVE_STATUS_PREFIXES):
            row.status = INTERRUPTED_STATUS
            row.enabled = False


# Row states derived from the free-text status; the text is for display only.
STATE_PENDING = "pending"
STATE_RUNNING = "running"
STATE_COMPLETED = "completed"
STATE_PARTIAL = "partial"
STATE_FAILED = "failed"
STATE_INVALID = "invalid"
STATE_DISCONNECTED = "disconnected"
STATE_CANCELLED = "cancelled"
STATE_INTERRUPTED = "interrupted"
FAILURE_STATES = frozenset({STATE_FAILED, STATE_INVALID, STATE_DISCONNECTED})
RETRYABLE_STATES = FAILURE_STATES | {STATE_PARTIAL}
TIMEOUT_STATUS = "Failed (timeout)"

RUN_SCOPE_UNFINISHED = "unfinished"  # everything not Completed
RUN_SCOPE_FAILED = "failed"          # only failed/invalid/disconnected rows
RUN_SCOPE_PARTIAL = "partial"        # only partial solutions
POLICY_CONTINUE = "continue"
POLICY_STOP_AFTER = "stop_after"


def classify_status(text: str) -> str:
    """Map a status text to one state; unknown text counts as not yet run."""
    low = (text or "").strip().lower()
    if not low:
        return STATE_PENDING
    if low.startswith("interrupted"):
        return STATE_INTERRUPTED
    if low.startswith("cancel"):
        return STATE_CANCELLED
    if low.startswith(("checking", "solving", "truba")):
        return STATE_RUNNING
    if "partial" in low:
        return STATE_PARTIAL
    if low.startswith("completed"):
        return STATE_COMPLETED
    if "disconnect" in low:
        return STATE_DISCONNECTED
    if low.startswith("invalid"):
        return STATE_INVALID
    if low.startswith(("failed", "error")) or "timeout" in low:
        return STATE_FAILED
    return STATE_PENDING


class _DoEActionBar(QFrame):
    """Reserve the wrapped toolbar height even inside a vertical splitter."""

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.layout() is not None:
            self.setFixedHeight(self.layout().heightForWidth(event.size().width()))


class ExperimentWindow(QMainWindow):
    """Window for experiment and analysis queue workspaces."""

    def __init__(self, studio, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        from window_state import install_window_state
        install_window_state(QApplication.instance())
        self.setCentralWidget(studio)
        self.resize(1180, 780)
        self.setMinimumSize(680, 480)

    def open_workspace(self):
        from window_state import show_restored_window
        show_restored_window(self)

class DoEStudioWindow(ExperimentWindow):
    def __init__(self, studio, parent=None):
        super().__init__(studio, parent)
        self.setObjectName("doeStudioWindow")
        self.setWindowTitle("DoE · RustMech")

    def closeEvent(self, event):
        self.centralWidget().save_workspace_draft()
        super().closeEvent(event)


class ExperimentTableWidget(TrubaParallelMixin, QWidget):
    """Shared experiment table and local/TRUBA execution lifecycle."""

    preview_requested = Signal(object)
    editable_experiments = False
    autosave_recovery_plan = False

    def __init__(
        self,
        settings_provider: Callable[[], Settings],
        mesh_generator: Callable[[Settings], object],
        batch_solver_runner: Callable[[Settings, object], dict] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings_provider = settings_provider
        self._mesh_generator = mesh_generator
        self._batch_solver_runner = batch_solver_runner
        self._rows: list[ExperimentRow] = []
        self._updating_table = False
        self._active_future = None
        self._active_mode: str | None = None  # "check" or "solve"
        self._queue: list[int] = []
        self._total_queued = 0
        self._stop_requested = False
        self._dataset_path = None
        self._persistence_error = ""
        self._footer_default = None
        # Run control (Faz 2): settings are frozen when a run starts.
        self._frozen_base: Settings | None = None
        self._row_started = 0.0
        self._failures = 0
        self._durations: list[float] = []
        self.failure_policy = POLICY_CONTINUE
        self.max_failures = 3
        self.max_retries = 0
        self.row_timeout_s = 0.0  # 0 disables the per-row limit
        self.run_scope = RUN_SCOPE_UNFINISHED

        self._build_ui()
        from operation_progress import OperationProgress
        self.operation_progress = OperationProgress(self, provider=self._operation_state)
        progress_policy = self.operation_progress.sizePolicy()
        progress_policy.setRetainSizeWhenHidden(False)
        self.operation_progress.setSizePolicy(progress_policy)
        self.layout().addWidget(self.operation_progress)
        self.progress_bar = self.operation_progress.bar
        self._update_badge()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(80)
        self._poll_timer.timeout.connect(self._poll_worker)
        self._poll_timer.start()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 4)
        root.setSpacing(4)
        root.addWidget(self._build_experiment_panel(), 1)
        root.addWidget(self.footer_label)

    @property
    def table_columns(self):
        if self.editable_experiments:
            return (*TABLE_COLUMNS, "Dataset split", "Geometry group")
        return TABLE_COLUMNS

    def _build_experiment_panel(self):
        panel = QWidget()
        panel.setMinimumSize(140, 60)
        panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # 3. Spreadsheet & Batch Execution Toolbar
        bar_frame = _DoEActionBar()
        bar_frame.setObjectName("viewportToolbar")
        bar_frame.setMinimumWidth(140)
        bar_frame.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        bar = ActionFlowLayout(bar_frame)
        bar.setContentsMargins(6, 4, 6, 4)
        bar.setSpacing(DOE_TOOLBAR_GAP)

        self.row_count_badge = QLabel("0 Experiments")
        self.row_count_badge.setObjectName("unitBadge")
        bar.addWidget(self.row_count_badge)

        actions = []
        if self.editable_experiments:
            actions.append(("+ Row", self.add_blank_row))
        actions.extend((
            ("− Delete", self.delete_selected_rows),
            ("Preview in 3D", self.preview_selected_row),
            ("Check meshes", self.start_mesh_precheck),
        ))
        table_buttons = []
        for label, callback in actions:
            btn = QPushButton(label)
            btn.setObjectName("secondaryActionBtn" if label in ("Check meshes", "Preview in 3D")
                              else "compactActionBtn")
            btn.setAutoDefault(False)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(callback)
            table_buttons.append(btn)
            bar.addWidget(btn)

        if self.editable_experiments:
            data_button = QPushButton("Table data")
            data_button.setObjectName("compactActionBtn")
            data_button.setAutoDefault(False)
            data_menu = QMenu(data_button)
            data_menu.addAction("Copy for Excel", self.copy_table_to_clipboard)
            data_menu.addAction("Import CSV", self.import_csv_dialog)
            data_menu.addAction("Export CSV", self.export_csv_dialog)
            data_button.setMenu(data_menu)
            table_buttons.append(data_button)
            bar.addWidget(data_button)
        self._size_action_group(*table_buttons)

        self.run_batch_btn = QPushButton("Run batch (ANSYS)")
        self.run_batch_btn.setObjectName("primaryLaunchBtn")
        self._size_action_group(self.run_batch_btn)
        self.run_batch_btn.setAutoDefault(False)
        self.run_batch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_batch_btn.clicked.connect(self.start_batch_solve)
        bar.addWidget(self.run_batch_btn)

        self.stop_batch_btn = QPushButton("Forced Stop")
        self.stop_batch_btn.setObjectName("forcedStopBtn")
        self.stop_batch_btn.setAutoDefault(False)
        self.stop_batch_btn.setFixedWidth(DOE_STOP_WIDTH)
        self.stop_batch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_batch_btn.setVisible(False)
        self.stop_batch_btn.clicked.connect(self.stop_active_queue)
        bar.addWidget(self.stop_batch_btn)

        layout.addWidget(bar_frame)

        # 4. Excel-like QTableWidget
        self.table = QTableWidget(0, len(self.table_columns))
        self.table.setObjectName("doeExperimentTable")
        self.table.setShowGrid(False)
        self.table.setHorizontalHeaderLabels(self.table_columns)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for column, title in enumerate(self.table_columns):
            width = self.table.fontMetrics().horizontalAdvance(title) + 18
            self.table.setColumnWidth(column, max(85, min(240, width)))
            self.table.horizontalHeaderItem(column).setToolTip(title)
        self.table.itemChanged.connect(self._on_table_item_changed)
        self.table.cellDoubleClicked.connect(self._on_row_double_clicked)
        layout.addWidget(self.table, 1)

        self.footer_label = QLabel("Edit values directly in the table · Double-click a row to preview · Table data for CSV and Excel")
        self.footer_label.setObjectName("mutedHintLabel")
        self.footer_label.setWordWrap(True)
        return panel

    def _size_action_group(self, *buttons):
        """Keep related actions equal and large enough for their styled labels."""
        if not hasattr(self, "_action_groups"):
            self._action_groups = []
        if buttons not in self._action_groups:
            self._action_groups.append(buttons)
        for button in buttons:
            button.ensurePolished()
        width = max(max(button.sizeHint().width(),
                        button.fontMetrics().horizontalAdvance(button.text())
                        + 24 + (16 if button.menu() else 0)) for button in buttons)
        for button in buttons:
            button.setFixedSize(width, CONTROL_HEIGHT)

    def showEvent(self, event):
        super().showEvent(event)
        for buttons in self._action_groups:
            self._size_action_group(*buttons)

    def _refresh_plan(self):
        pass

    def _refresh_table_view(self) -> None:
        self._updating_table = True
        self.table.setColumnCount(len(self.table_columns))
        self.table.setHorizontalHeaderLabels(self.table_columns)
        self.table.setRowCount(len(self._rows))
        for r_idx, row in enumerate(self._rows):
            self._write_row_cells(r_idx, row)
        self.table.resizeColumnsToContents()
        self._updating_table = False
        self._update_badge()

    def _write_row_cells(self, r_idx: int, row: ExperimentRow) -> None:
        def _set_cell(
            col: int, text: str, editable: bool = True, align_right: bool = False
        ) -> QTableWidgetItem:
            it = QTableWidgetItem(text)
            if not editable:
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if align_right:
                it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            else:
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(r_idx, col, it)
            return it

        _set_cell(COL_EXP_ID, row.exp_id, editable=False)

        run_item = QTableWidgetItem()
        run_item.setFlags(
            Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
        )
        run_item.setCheckState(Qt.CheckState.Checked if row.enabled else Qt.CheckState.Unchecked)
        self.table.setItem(r_idx, COL_RUN, run_item)

        _set_cell(COL_FAMILY, row.structure_family, editable=True)
        _set_cell(COL_STRUCTURE, row.structure_name, editable=True)
        _set_cell(COL_MORPH, row.tpms_morphology, editable=True)
        _set_cell(COL_SIZE, f"{row.size:.3f}", editable=True, align_right=True)
        _set_cell(COL_CELL, f"{row.cell_size:.3f}", editable=True, align_right=True)
        _set_cell(COL_DENSITY, f"{row.solid_fraction:.2f}", editable=True, align_right=True)
        _set_cell(COL_MESH, f"{row.mesh_resolution:.3f}", editable=True, align_right=True)
        _set_cell(COL_COMP, f"{row.compression:.4f}", editable=True, align_right=True)
        _set_cell(COL_SEED, str(row.random_seed), editable=True, align_right=True)
        if self.editable_experiments:
            group, split, *_ = row.dataset_columns()
            _set_cell(len(TABLE_COLUMNS), split, editable=False)
            _set_cell(len(TABLE_COLUMNS) + 1, group, editable=False)

        _set_cell(
            COL_ACT_DEN,
            f"{row.actual_density:.2f}" if row.actual_density is not None else "—",
            editable=False,
            align_right=True,
        )
        _set_cell(
            COL_MASS,
            f"{row.mass_g:.4f}" if row.mass_g is not None else "—",
            editable=False,
            align_right=True,
        )
        _set_cell(
            COL_ELEMS,
            f"{row.hex8_elements:,}" if row.hex8_elements is not None else "—",
            editable=False,
            align_right=True,
        )
        status_item = _set_cell(COL_STATUS, row.status, editable=False)
        self._style_status_cell(status_item, row.status)

        _set_cell(
            COL_FORCE,
            f"{row.max_force_n:.2f}" if row.max_force_n is not None else "—",
            editable=False,
            align_right=True,
        )
        _set_cell(
            COL_EA,
            f"{row.energy_absorption_j:.4f}" if row.energy_absorption_j is not None else "—",
            editable=False,
            align_right=True,
        )
        _set_cell(
            COL_SEA,
            f"{row.specific_energy_absorption_j_g:.4f}"
            if row.specific_energy_absorption_j_g is not None
            else "—",
            editable=False,
            align_right=True,
        )
        _set_cell(
            COL_VOL_EA,
            f"{row.volumetric_ea_j_cm3:.4f}" if row.volumetric_ea_j_cm3 is not None else "—",
            editable=False,
            align_right=True,
        )
        _set_cell(
            COL_MODULUS,
            f"{row.effective_modulus_mpa:.2f}" if row.effective_modulus_mpa is not None else "—",
            editable=False,
            align_right=True,
        )
        _set_cell(
            COL_POISSON,
            f"{row.poisson_ratio:.4f}" if row.poisson_ratio is not None else "—",
            editable=False,
            align_right=True,
        )
        _set_cell(
            COL_STRESS,
            f"{row.max_von_mises_mpa:.2f}" if row.max_von_mises_mpa is not None else "—",
            editable=False,
            align_right=True,
        )

    @staticmethod
    def _style_status_cell(item: QTableWidgetItem, status: str) -> None:
        st = status.lower()
        if "ready" in st or "completed" in st:
            item.setBackground(QBrush(QColor("#dcfce7")))
            item.setForeground(QBrush(QColor("#166534")))
        elif "invalid" in st or "failed" in st or "error" in st or "disconnected" in st:
            item.setBackground(QBrush(QColor("#fee2e2")))
            item.setForeground(QBrush(QColor("#991b1b")))
        elif "checking" in st or "solving" in st or "running" in st:
            item.setBackground(QBrush(QColor("#e0f2fe")))
            item.setForeground(QBrush(QColor("#075985")))
        else:
            item.setBackground(QBrush(QColor("#f1f5f9")))
            item.setForeground(QBrush(QColor("#334155")))

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_table:
            return
        r = item.row()
        c = item.column()
        if not (0 <= r < len(self._rows)):
            return
        row = self._rows[r]
        if c == COL_RUN:
            row.enabled = item.checkState() == Qt.CheckState.Checked
            self._update_badge()
            return
        if not COL_FAMILY <= c <= COL_SEED:
            return
        if self._active_mode is not None:
            self._restore_row_cells(r, row)
            return

        previous = self._analysis_inputs(row)
        try:
            if c in (COL_FAMILY, COL_STRUCTURE):
                fam_text = self.table.item(r, COL_FAMILY).text().strip()
                name_text = self.table.item(r, COL_STRUCTURE).text().strip()
                fam, name = resolve_family_and_name(fam_text, name_text)
                row.structure_family = fam
                row.structure_name = name
            elif c == COL_MORPH:
                val = item.text().strip().lower()
                if val in TPMS_MORPHOLOGIES:
                    row.tpms_morphology = val
            elif c == COL_SIZE:
                row.size = max(0.5, float(item.text()))
                if row.target_strain_pct is not None:
                    row.compression = row.size * row.target_strain_pct / 100
            elif c == COL_CELL:
                row.cell_size = max(0.1, min(row.size, float(item.text())))
            elif c == COL_DENSITY:
                row.solid_fraction = float(np.clip(float(item.text()), 1.0, 99.0))
            elif c == COL_MESH:
                row.mesh_resolution = max(MIN_MESH_INPUT_MM, float(item.text()))
            elif c == COL_COMP:
                compression = max(0.001, min(row.size * 0.95, float(item.text())))
                if compression != row.compression:
                    row.target_strain_pct = None
                    row.compression = compression
            elif c == COL_SEED:
                row.random_seed = int(float(item.text()))
        except ValueError:
            pass
        if self._analysis_inputs(row) != previous:
            row.invalidate_results()
        self._restore_row_cells(r, row)
        self._update_badge()

    @staticmethod
    def _analysis_inputs(row: ExperimentRow) -> tuple:
        return (
            row.structure_family, row.structure_name, row.tpms_morphology,
            row.size, row.cell_size, row.solid_fraction, row.mesh_resolution,
            row.compression, row.random_seed,
        )

    def _restore_row_cells(self, index: int, row: ExperimentRow) -> None:
        self._updating_table = True
        try:
            self._write_row_cells(index, row)
        finally:
            self._updating_table = False

    def _update_badge(self) -> None:
        enabled_count = sum(1 for r in self._rows if r.enabled)
        completed_count = sum(1 for r in self._rows if "Completed" in r.status)
        ready_count = sum(1 for r in self._rows if "Ready" in r.status)
        self.row_count_badge.setText(
            f"{len(self._rows)} rows · {enabled_count} active\n"
            f"{ready_count} ready · {completed_count} solved"
        )

    def delete_selected_rows(self) -> None:
        if self._active_future is not None:
            return
        selected_rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        if not selected_rows:
            return
        for r in selected_rows:
            if 0 <= r < len(self._rows):
                self._rows.pop(r)
        self._refresh_table_view()

    def _on_row_double_clicked(self, row_index: int, _col_index: int) -> None:
        self._emit_preview_for_row(row_index)

    def preview_selected_row(self) -> None:
        row_index = self.table.currentRow()
        if row_index < 0 and self._rows:
            row_index = 0
        if row_index >= 0:
            self._emit_preview_for_row(row_index)

    def _emit_preview_for_row(self, row_index: int) -> None:
        if 0 <= row_index < len(self._rows):
            base = self._settings_provider()
            target_settings = self._rows[row_index].to_settings(base)
            self.preview_requested.emit(target_settings)

    def _stop_serial(self) -> None:
        if self._active_future is not None:
            try:
                self._active_future.force_stop()
            except Exception as exc:
                QMessageBox.critical(self, "Forced Stop failed", str(exc))
                return
            self._active_future = None
            if self._queue:
                index = self._queue[0]
                self._rows[index].status = "Cancelled (Forced Stop)"
                self._updating_table = True
                self._write_row_cells(index, self._rows[index])
                self._updating_table = False
        self._stop_requested = True
        self._queue.clear()
        if self._active_mode == "solve":
            self._persist_progress()
        self._launch_next_in_queue()

    def start_mesh_precheck(self) -> None:
        if self._active_future is not None or self._active_mode is not None or not self._rows:
            return
        self._queue = [i for i, r in enumerate(self._rows) if r.enabled]
        if not self._queue:
            return
        self._active_mode = "check"
        self._begin_run()
        self._total_queued = len(self._queue)
        self.progress_bar.setRange(0, self._total_queued)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.stop_batch_btn.setVisible(True)
        self.run_batch_btn.hide()
        self._refresh_plan()
        self._launch_next_in_queue()

    def start_batch_solve(self) -> None:
        if self._active_future is not None or self._active_mode is not None or not self._rows:
            return
        if self._batch_solver_runner is None:
            QMessageBox.information(
                self,
                "Solver Not Configured",
                "Batch solver runner is not attached.",
            )
            return
        self._queue = self.rows_to_run()
        if not self._queue:
            QMessageBox.information(
                self,
                "No Pending Rows",
                "No checked row matches the run scope (completed rows are skipped).",
            )
            return
        self._active_mode = "solve"
        self._dataset_path = self._new_dataset_path()
        self._begin_run()
        self._queue, notes = self._preflight(self._queue)
        if notes:
            self._report_persistence_problem(" | ".join(notes[:3]))
        if not self._queue:
            self._active_mode = None
            self._frozen_base = None
            self._refresh_plan()
            return
        self._total_queued = len(self._queue)
        self.progress_bar.setRange(0, self._total_queued)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.stop_batch_btn.setVisible(True)
        self.run_batch_btn.hide()
        self._refresh_plan()
        self._launch_next_in_queue()

    def rows_to_run(self, scope: str | None = None) -> list[int]:
        """Checked rows the next Run will solve; Completed rows are always skipped."""
        scope = scope or self.run_scope
        wanted = {
            RUN_SCOPE_FAILED: FAILURE_STATES,
            RUN_SCOPE_PARTIAL: {STATE_PARTIAL},
        }.get(scope)
        result = []
        for index, row in enumerate(self._rows):
            state = classify_status(row.status)
            if not row.enabled or state == STATE_COMPLETED:
                continue
            if wanted is not None and state not in wanted:
                continue
            result.append(index)
        return result

    def _preflight(self, queue: list[int]) -> tuple[list[int], list[str]]:
        """Check every queued row before the first solve; bad rows become Invalid, not mid-run surprises."""
        from model import validate_settings
        base = self._run_base()
        valid, notes = [], []
        for index in queue:
            row = self._rows[index]
            try:
                errors = validate_settings(row.to_settings(base))
            except Exception as exc:
                errors = [str(exc)]
            if errors:
                row.status = f"Invalid: {errors[0]}"
                notes.append(f"{row.exp_id}: {errors[0]}")
                self._updating_table = True
                self._write_row_cells(index, row)
                self._updating_table = False
            else:
                valid.append(index)
        try:
            import shutil
            probe = results_directory()
            while not probe.exists() and probe != probe.parent:
                probe = probe.parent
            free_gib = shutil.disk_usage(probe).free / 2**30
            if free_gib < 1.0:
                notes.append(f"Low disk space for results: {free_gib:.1f} GiB free.")
        except OSError:
            pass
        if len(valid) != len(queue):
            notes.insert(0, f"{len(queue) - len(valid)} row(s) failed the pre-run check and were skipped.")
        return valid, notes

    def _begin_run(self) -> None:
        """Freeze the settings once; later edits in the UI do not reach queued rows."""
        self._stop_requested = False
        self._failures = 0
        self._durations = []
        self._frozen_base = copy.deepcopy(self._settings_provider())

    def _run_base(self) -> Settings:
        return self._frozen_base if self._frozen_base is not None else self._settings_provider()

    def _eta_text(self) -> str:
        if not self._durations or not self._queue:
            return ""
        remaining = sum(self._durations) / len(self._durations) * len(self._queue)
        minutes, seconds = divmod(int(remaining), 60)
        hours, minutes = divmod(minutes, 60)
        return f"ETA ~{hours}h {minutes:02d}m" if hours else f"ETA ~{minutes}m {seconds:02d}s"

    def _launch_next_serial(self) -> None:
        if self._stop_requested or not self._queue:
            self._frozen_base = None
            self._active_mode = None
            self.progress_bar.setVisible(False)
            self.stop_batch_btn.setVisible(False)
            self.run_batch_btn.show()
            self._refresh_plan()
            self._update_badge()
            return
        r_idx = self._queue[0]
        row = self._rows[r_idx]
        row.status = "Checking..." if self._active_mode == "check" else "Solving (ANSYS)..."
        use_truba = self._uses_truba()
        if use_truba:
            row.status = "TRUBA · checking mesh" if self._active_mode=="check" else "TRUBA · solving"
        self._updating_table = True
        self._write_row_cells(r_idx, row)
        self._updating_table = False
        self.table.scrollToItem(self.table.item(r_idx, COL_STATUS))
        row.started_at = datetime.now().isoformat(timespec="seconds")
        row.duration_s = None
        self._row_started = time.monotonic()

        s = row.to_settings(self._run_base())
        worker = ExperimentWorker(self._mesh_generator, self._batch_solver_runner)
        try:
            if use_truba:
                from truba_remote import run_remote_doe
                s = self._truba_row_settings(s)
                context,auth = self._require_truba_row(
                    "mesh" if self._active_mode=="check" else s.solver_backend, s)
                self._active_future = AnalysisProcess(run_remote_doe, r_idx, s, context,
                                                      self._active_mode != "check", remote_auth=auth)
            elif self._active_mode == "check":
                self._active_future = AnalysisProcess(worker._evaluate_row_mesh, r_idx, s)
            else:
                self._active_future = AnalysisProcess(worker._solve_row, r_idx, s)
        except Exception as exc:
            # A start failure is an ordinary row failure: the policy decides what follows.
            self._active_future = None
            self._apply_payload({"r_idx": r_idx, "status": f"Failed to start: {exc}"})
            self._launch_next_in_queue()

    def _serial_operation_state(self):
        if self._active_future is None or not self._queue:
            return False, ""
        index = self._queue[0]
        row = self._rows[index]
        message = f"{row.exp_id} · {row.status}"
        record = self._active_future.remote_status() if hasattr(self._active_future, "remote_status") else None
        if record:
            message += f" · TRUBA {record.get('job_id', 'pending')} · {record.get('state', 'preparing')}"
        elif hasattr(self._active_future, "progress_status"):
            stage = self._active_future.progress_status()
            if stage:
                message += " · " + stage
        eta = self._eta_text()
        if eta:
            message += f" · {eta}"
        return True, message, self._total_queued - len(self._queue), self._total_queued

    def _poll_serial(self) -> None:
        if self._active_future is None:
            return
        if not self._active_future.done():
            if (
                self.row_timeout_s > 0
                and self._queue
                and time.monotonic() - self._row_started > self.row_timeout_s
            ):
                self._time_out_active_row()
            return
        fut, self._active_future = self._active_future, None
        try:
            payload = fut.result()
        except Exception as exc:
            payload = {"r_idx": self._queue[0], "status": f"Failed: {exc}"}
        self._apply_payload(payload)
        self._launch_next_in_queue()

    def _time_out_active_row(self) -> None:
        """Stop a stuck row, mark it Failed (timeout) and let the policy continue."""
        future, self._active_future = self._active_future, None
        r_idx = self._queue[0]
        try:
            future.force_stop()
        except Exception as exc:
            self._report_persistence_problem(f"Timed-out row could not be stopped: {exc}")
        self._apply_payload({"r_idx": r_idx, "status": TIMEOUT_STATUS})
        self._launch_next_in_queue()

    def _apply_payload(self, payload: dict) -> None:
        """Record one row's outcome, then retry, stop or move on per the failure policy."""
        r_idx = payload["r_idx"]
        retry = False
        if self._queue and self._queue[0] == r_idx:
            self._queue.pop(0)
        if 0 <= r_idx < len(self._rows):
            row = self._rows[r_idx]
            for attr in (
                "actual_density",
                "mass_g",
                "hex8_elements",
                "status",
                "max_force_n",
                "energy_absorption_j",
                "specific_energy_absorption_j_g",
                "volumetric_ea_j_cm3",
                "effective_modulus_mpa",
                "poisson_ratio",
                "max_von_mises_mpa",
                "run_folder",
            ):
                if attr in payload and payload[attr] is not None:
                    setattr(row, attr, payload[attr])
            if self._active_mode == "check" and "Ready" not in row.status:
                row.enabled = False
            row.duration_s = round(time.monotonic() - self._row_started, 3)
            state = classify_status(row.status)
            if state == STATE_COMPLETED:
                self._durations.append(row.duration_s)
            elif state in FAILURE_STATES:
                if row.attempts < self.max_retries and self._active_mode == "solve":
                    row.attempts += 1
                    retry = True
                else:
                    self._failures += 1
            self._updating_table = True
            self._write_row_cells(r_idx, row)
            self._updating_table = False
            if self._active_mode == "solve":
                self._persist_progress()
            if retry:
                self._queue.insert(0, r_idx)
            elif (
                self.failure_policy == POLICY_STOP_AFTER
                and self._failures >= max(1, self.max_failures)
                and self._queue
            ):
                self._queue.clear()
                self._stop_requested = True
                self._report_persistence_problem(
                    f"Run stopped after {self._failures} failed row(s) (failure policy)."
                )
        done_count = self._total_queued - len(self._queue)
        self.progress_bar.setValue(done_count)

    def _persist_progress(self) -> None:
        """Save results and a recovery copy of the plan; failures stay visible."""
        problems = []
        path = self._dataset_path or self._new_dataset_path()
        self._dataset_path = path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.export_csv_path(path)
        except OSError as error:
            problems.append(f"Results CSV was not saved: {error}")
        if self.autosave_recovery_plan:
            try:
                from doe_plan import write_plan
                write_plan(path.with_suffix(".rustmech-doe.json"), self.capture_plan())
            except (OSError, ValueError, TypeError) as error:
                problems.append(f"Recovery plan was not saved: {error}")
        self._report_persistence_problem("; ".join(problems))

    def _report_persistence_problem(self, message: str) -> None:
        if message == self._persistence_error:
            return
        if self._footer_default is None:
            self._footer_default = self.footer_label.text()
        self._persistence_error = message
        self.footer_label.setText(message or self._footer_default)
        self.footer_label.setToolTip(message)

    def copy_table_to_clipboard(self) -> None:
        lines = ["\t".join(self.table.horizontalHeaderItem(c).text() for c in range(self.table.columnCount()))]
        for r in range(self.table.rowCount()):
            row_vals = []
            for c in range(self.table.columnCount()):
                item = self.table.item(r, c)
                if c == COL_RUN:
                    row_vals.append(
                        "1" if item and item.checkState() == Qt.CheckState.Checked else "0"
                    )
                else:
                    row_vals.append(item.text() if item else "")
            lines.append("\t".join(row_vals))
        QApplication.clipboard().setText("\n".join(lines))

    def export_csv_path(self, path: Path) -> None:
        """Write beside the target, then replace it: a failed write keeps the old file."""
        path = Path(path)
        temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        try:
            self._write_csv(temporary)
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def _write_csv(self, path: Path) -> None:
        with path.open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow([
                *TABLE_COLUMNS, SNAPSHOT_HEADER, "Geometry group", "Dataset split",
                "Final nominal strain (%)", "Target strain (%)", "Run folder", "Force-displacement CSV",
                "Started at", "Duration (s)", "Attempts",
            ])
            for row in self._rows:
                writer.writerow([
                    row.exp_id,
                    1 if row.enabled else 0,
                    row.structure_family,
                    row.structure_name,
                    row.tpms_morphology,
                    row.size,
                    row.cell_size,
                    row.solid_fraction,
                    row.mesh_resolution,
                    row.compression,
                    row.random_seed,
                    "" if row.actual_density is None else f"{row.actual_density:.4f}",
                    "" if row.mass_g is None else f"{row.mass_g:.6f}",
                    "" if row.hex8_elements is None else row.hex8_elements,
                    row.status,
                    "" if row.max_force_n is None else f"{row.max_force_n:.4f}",
                    "" if row.energy_absorption_j is None else f"{row.energy_absorption_j:.6f}",
                    ""
                    if row.specific_energy_absorption_j_g is None
                    else f"{row.specific_energy_absorption_j_g:.6f}",
                    "" if row.volumetric_ea_j_cm3 is None else f"{row.volumetric_ea_j_cm3:.6f}",
                    "" if row.effective_modulus_mpa is None else f"{row.effective_modulus_mpa:.4f}",
                    "" if row.poisson_ratio is None else f"{row.poisson_ratio:.6f}",
                    "" if row.max_von_mises_mpa is None else f"{row.max_von_mises_mpa:.4f}",
                    json.dumps(asdict(row.design_settings)) if row.design_settings else "",
                    *row.dataset_columns(),
                    row.started_at,
                    "" if row.duration_s is None else f"{row.duration_s:.3f}",
                    row.attempts,
                ])

    def export_csv_dialog(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export Design of Experiments Table (CSV / Excel)",
            str(ROOT / "ml_experiment_matrix.csv"),
            "CSV Spreadsheet (*.csv)",
        )
        if filename:
            try:
                self.export_csv_path(Path(filename))
            except OSError as exc:
                QMessageBox.warning(self, "Export Error", str(exc))

    def import_csv_path(self, path: Path) -> None:
        loaded: list[ExperimentRow] = []
        strain_column = None
        if self._active_mode is not None:
            raise ValueError("Stop the active queue before importing a table.")
        snapshot_column = None
        columns = {name.lower(): index for index, name in enumerate(TABLE_COLUMNS)}
        with path.open("r", newline="", encoding="utf-8-sig") as stream:
            reader = csv.reader(stream)
            for idx, raw in enumerate(reader):
                if not raw or all(not cell.strip() for cell in raw):
                    continue
                if idx == 0 and raw[0].strip().lower() == TABLE_COLUMNS[0].lower():
                    strain_column = raw.index("Target strain (%)") if "Target strain (%)" in raw else None
                    columns = {cell.strip().lower(): index for index, cell in reversed(list(enumerate(raw)))}
                    if SNAPSHOT_HEADER in raw:
                        snapshot_column = raw.index(SNAPSHOT_HEADER)
                    continue
                fam, name = resolve_family_and_name(raw[2].strip(), raw[3].strip())
                snapshot = raw[snapshot_column].strip() if snapshot_column is not None and len(raw) > snapshot_column else ""
                target_strain = raw[strain_column].strip() if strain_column is not None and len(raw) > strain_column else ""
                loaded.append(
                    ExperimentRow(
                        design_settings=settings_from_dict(json.loads(snapshot)) if snapshot else None,
                        exp_id=raw[0].strip() or f"EXP-{len(loaded)+1:04d}",
                        enabled=raw[1].strip().lower() not in ("0", "false", "no"),
                        structure_family=fam,
                        structure_name=name,
                        tpms_morphology=raw[4].strip() or "sheet",
                        size=float(raw[5]),
                        cell_size=float(raw[6]),
                        solid_fraction=float(raw[7]),
                        mesh_resolution=float(raw[8]),
                        compression=float(raw[9]),
                        target_strain_pct=float(target_strain) if target_strain else None,
                        random_seed=int(float(raw[10])) if len(raw) > 10 and raw[10].strip() else 42,
                    )
                )
                self._restore_result_cells(loaded[-1], raw, columns)
        mark_interrupted(loaded)
        if loaded:
            self._rows = loaded
            self._refresh_table_view()

    @staticmethod
    def _restore_result_cells(row: ExperimentRow, raw: list, columns: dict) -> None:
        """Re-read the result columns an export wrote so completed rows stay completed."""
        def cell(name: str) -> str:
            index = columns.get(name.lower())
            return raw[index].strip() if index is not None and index < len(raw) else ""

        for header, attribute, integer in RESULT_FIELDS:
            text = cell(header)
            try:
                value = (int(float(text)) if integer else float(text)) if text else None
            except (ValueError, OverflowError):
                value = None
            if isinstance(value, float) and not math.isfinite(value):
                value = None
            setattr(row, attribute, value)
        row.status = cell("Status") or "Pending"
        row.run_folder = cell("Run folder")
        row.started_at = cell("Started at")
        try:
            row.duration_s = float(cell("Duration (s)")) if cell("Duration (s)") else None
            row.attempts = int(float(cell("Attempts") or 0))
        except ValueError:
            row.duration_s, row.attempts = None, 0

    def import_csv_dialog(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Import Design of Experiments CSV",
            str(ROOT),
            "CSV Spreadsheet (*.csv)",
        )
        if filename:
            try:
                self.import_csv_path(Path(filename))
            except Exception as exc:
                QMessageBox.warning(self, "Import Error", str(exc))

    @property
    def rows(self) -> list[ExperimentRow]:
        return self._rows

    def dispose(self) -> None:
        self._stop_requested = True
        self._poll_timer.stop()
        self.stop_active_queue()


class DoEStudioWidget(ExperimentTableWidget):
    """Operation-specific DoE and DeepLearning dataset generation."""

    editable_experiments = True
    autosave_recovery_plan = True

    def __init__(self, settings_provider, mesh_generator, batch_solver_runner=None, parent=None,
                 *, operation_host=None, preferences=None):
        self._operation_host = operation_host
        self._preferences = preferences
        self._workspace_draft = None
        self._plan_path = None
        self._designs = []
        self._design_factors = []
        self._selected_design = -1
        self._loading_design = False
        super().__init__(settings_provider, mesh_generator, batch_solver_runner, parent)
        self.layout().addWidget(self.planner.plan_footer)
        self._refresh_plan()
        self._saved_plan_state = self.capture_plan()

    def _build_ui(self):
        from doe_planner import SimpleDoEPlanner
        from doe_sample_settings import SampleSettingsPage
        from new_doe_page import NewDoePage

        root = QVBoxLayout(self)
        self.setObjectName("doeWorkspace")
        root.setContentsMargins(6, 6, 6, 4)
        root.setSpacing(4)
        self.vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        self.vertical_splitter.setObjectName("doePlannerWorkspaceV2")
        self.vertical_splitter.setChildrenCollapsible(True)
        self.vertical_splitter.setOpaqueResize(True)
        self.vertical_splitter.setHandleWidth(3)

        self.planner = SimpleDoEPlanner(self, (*FACTOR_SPECS, SIZE_FACTOR_SPEC))
        self.vertical_splitter.addWidget(self.planner)
        self.planner.learning_layout.addWidget(self._build_experiment_panel(), 1)

        self.new_doe = NewDoePage(self._operation_host, self._preferences)
        self.planner.tabs.insertTab(0, self.new_doe, "DoE")
        self.planner.tabs.setCurrentWidget(self.new_doe)
        # Keep saved sample rules and combinations available to legacy plans.
        self.sample_settings = SampleSettingsPage(self)
        self.sample_settings.hide()

        def select_page(index):
            learning = self.planner.tabs.widget(index) is self.planner.learning_page
            self.planner.plan_footer.setVisible(learning)
            self.new_doe.size_navigation(self.planner.navigation, not learning)
            self.footer_label.setVisible(learning)

        self.planner.navigation.add_tabs(self.planner.tabs)
        self.planner.tabs.currentChanged.connect(select_page)
        self.vertical_splitter.setStretchFactor(0, 0)
        self.vertical_splitter.setSizes([730])
        root.addWidget(self.vertical_splitter, 1)
        root.addWidget(self.footer_label)
        select_page(self.planner.tabs.currentIndex())

    def showEvent(self, event):
        super().showEvent(event)
        self.restore_workspace_draft()
        if self.settings_tabs.currentWidget() is self.new_doe:
            self.new_doe.size_navigation(self.planner.navigation, True)

    def dispose(self):
        self.save_workspace_draft()
        self.new_doe.cancel()
        self.sample_settings.save_preferences()
        super().dispose()

    def capture_plan(self, *, commit=False, inputs_only=False):
        from doe_plan import PLAN_FORMAT, PLAN_VERSION
        if commit:
            # Save the text still being edited, even before focus leaves the field.
            for field in self.findChildren(NumericLineEdit):
                field.interpretText()
        return dict(
            format=PLAN_FORMAT, version=PLAN_VERSION,
            designs=[dict(settings=asdict(design), factors=factors.copy(),
                          enabled=self.design_list.item(i).checkState() == Qt.CheckState.Checked)
                     for i, (design, factors) in enumerate(zip(self._designs, self._design_factors))],
            draft=asdict(self.planner.saved_draft),
            variable_editor={name: dict(enabled=enabled.isChecked(), min=low.value(),
                                        max=high.value())
                             for name, (enabled, low, high) in self.factor_controls.items()},
            options=dict(ml_count=self.ml_count_spin.value(), ml_seed=self.ml_seed_spin.value(),
                         common_strain_enabled=self.common_strain_check.isChecked(),
                         common_strain=self.common_strain_spin.value()),
            rows=[] if inputs_only else [asdict(row) for row in self._rows],
            sample_settings={} if inputs_only else self.sample_settings.state(),
            selection=dict(design=self._selected_design, tab=LEARNING_PLAN_TAB),
        )

    def restore_workspace_draft(self):
        from doe_plan import decode_plan

        if self._preferences is None or self._workspace_draft is not None:
            return
        raw = self._preferences.value("doe/classicDraft", "", type=str)
        if raw:
            try:
                plan = decode_plan(json.loads(raw))
            except ValueError:
                pass
            else:
                self._restore_plan(plan, select_tab=False)
                self._saved_plan_state = self.capture_plan()
        self._workspace_draft = self.capture_plan(inputs_only=True)

    def save_workspace_draft(self):
        self.new_doe.save_draft(commit=True)
        if self._workspace_draft is None:
            return
        draft = self.capture_plan(commit=True, inputs_only=True)
        if draft != self._workspace_draft:
            self._preferences.setValue("doe/classicDraft", json.dumps(draft, ensure_ascii=False))
            self._preferences.sync()
            self._workspace_draft = draft

    def _set_plan_path(self, path):
        self._plan_path = Path(path).resolve()
        self.plan_file_label.setText(self._plan_path.name)
        self.plan_file_label.setToolTip(str(self._plan_path))

    def save_plan_path(self, path):
        if self._active_mode is not None:
            raise RuntimeError('Wait for the current batch to finish or stop it before saving the plan.')
        from doe_plan import write_plan
        payload = self.capture_plan(commit=True)
        write_plan(path, payload)
        self._saved_plan_state = payload
        self._set_plan_path(path)

    def open_plan_path(self, path):
        from doe_plan import read_plan
        if self._active_mode is not None:
            raise RuntimeError('Wait for the current batch to finish or stop it before opening a plan.')
        self._restore_plan(read_plan(path))
        self._set_plan_path(path)
        self._saved_plan_state = self.capture_plan()

    def _restore_plan(self, plan, *, select_tab=True):
        from doe_planner import geometry_group
        self._loading_design = True
        controls = (self.design_list, self.ml_count_spin, self.ml_seed_spin,
                    self.common_strain_check, self.common_strain_spin)
        blockers = [QSignalBlocker(control) for control in controls]
        try:
            self._selected_design = -1
            self.design_list.clear()
            self._designs = [entry['settings'] for entry in plan['designs']]
            self._design_factors = [entry['factors'] for entry in plan['designs']]
            self.planner._group_factors.clear()
            for i, entry in enumerate(plan['designs']):
                item = QListWidgetItem()
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if entry['enabled'] else Qt.CheckState.Unchecked)
                self.design_list.addItem(item)
                self._update_design_caption(i)
                self.planner._group_factors[geometry_group(entry['settings'])] = entry['factors'].copy()
            options = plan['options']
            for control, key in ((self.ml_count_spin, 'ml_count'), (self.ml_seed_spin, 'ml_seed'),
                                 (self.common_strain_spin, 'common_strain')):
                control.setValue(options[key])
            self.common_strain_check.setChecked(options['common_strain_enabled'])
            self.common_strain_spin.setEnabled(options['common_strain_enabled'])
            self.planner.saved_draft = plan['draft']
            selection = plan['selection']
            self.design_list.setCurrentRow(selection['design'])
            self._rows = plan['rows']
            mark_interrupted(self._rows)
            self._queue.clear()
            self._stop_requested = False
        finally:
            self._loading_design = False
            del blockers
        self._select_design(selection['design'])
        self._loading_design = True
        try:
            for name, state in plan['variable_editor'].items():
                enabled, low, high = self.factor_controls[name]
                enabled.setChecked(state['enabled'])
                low.setValue(state['min'])
                high.setValue(state['max'])
        finally:
            self._loading_design = False
        self._refresh_plan()
        self._refresh_table_view()
        self.sample_settings.restore_state(plan.get("sample_settings", {}))
        if select_tab:
            self.settings_tabs.setCurrentWidget(self.planner.learning_page)

    def save_plan_dialog(self):
        from doe_plan import PLAN_FILTER
        if self._active_mode is not None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, 'Save DoE plan', str(self._plan_path or Path.home() / 'plan.rustmech-doe.json'), PLAN_FILTER)
        if path:
            if not path.lower().endswith('.json'):
                path += '.rustmech-doe.json'
            try:
                self.save_plan_path(path)
            except (OSError, ValueError, RuntimeError) as error:
                QMessageBox.warning(self, 'Cannot save DoE plan', str(error))

    def open_plan_dialog(self):
        from doe_plan import PLAN_FILTER, read_plan
        if self._active_mode is not None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, 'Open DoE plan', str(self._plan_path.parent if self._plan_path else Path.home()), PLAN_FILTER)
        if not path:
            return
        try:
            plan = read_plan(path)
            current = self.capture_plan(commit=True)
            changed = any(current[key] != self._saved_plan_state[key] for key in current if key != 'selection')
            if changed and QMessageBox.question(
                self, 'Unsaved DoE plan', 'Opening this file replaces your unsaved plan. Continue?',
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            ) != QMessageBox.StandardButton.Discard:
                return
            self._restore_plan(plan)
            self._set_plan_path(path)
            self._saved_plan_state = self.capture_plan()
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self, 'Cannot open DoE plan', str(error))

    def _add_factor_controls(self, parent, spec) -> None:
        field, label, lower, upper, minimum, maximum = spec
        enabled = QCheckBox(label, parent)
        low = NumericLineEdit(minimum, lower, upper)
        high = NumericLineEdit(maximum, lower, upper)
        low.setToolTip("Lowest value included in this variable's range.")
        high.setToolTip("Highest value included in this variable's range.")
        self.factor_controls[field] = (enabled, low, high)
        enabled.toggled.connect(self._refresh_plan)
        for control in (low, high):
            control.setParent(parent)
            control.valueChanged.connect(self._refresh_plan)

    def _update_design_caption(self, index):
        settings = self._designs[index]
        self.design_list.item(index).setText(
            f"{settings.structure_name} | {settings.size:g} mm · {settings.solid_fraction:g}% · {settings.mesh_type}")
        self.design_list.item(index).setToolTip(
            f"{settings.structure_family} · {settings.tpms_morphology}\nCube: {settings.size:g} mm · Cell: {settings.cell_size:g} mm")

    def _included_designs(self):
        # Designs supplying range endpoints must not multiply identical runs.
        included, signatures = [], []
        for index, (design, factors) in enumerate(zip(self._designs, self._design_factors)):
            if self.design_list.item(index).checkState() != Qt.CheckState.Checked:
                continue
            signature = (replace(design, **{name: limits[0] for name, limits in factors.items()}), factors)
            if signature not in signatures:
                signatures.append(signature)
                included.append((design, factors))
        return included

    def add_design(self, settings: Settings, *, allow_duplicate=False) -> None:
        """Capture a model snapshot or select the matching existing design."""
        if not allow_duplicate and settings in self._designs:
            self.design_list.setCurrentRow(self._designs.index(settings))
            return
        self._designs.append(settings)
        from doe_planner import geometry_group
        self._design_factors.append(self.planner._group_factors.get(geometry_group(settings), {}).copy())
        morphology = TPMS_MORPHOLOGIES[settings.tpms_morphology].split(" (")[0]
        name = settings.structure_name
        if settings.structure_family == "tpms":
            name += f" · {morphology}"
        item = QListWidgetItem(
            f"{name} | {settings.size:g} mm · {settings.solid_fraction:g}%"
        )
        item.setToolTip(
            f"{name} | Cube {settings.size:g} mm · Period {settings.cell_size:g} mm"
            f" · Solid {settings.solid_fraction:g}% · {settings.mesh_type} · {settings.material_name}"
        )
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        self.design_list.addItem(item)
        self.design_list.setCurrentRow(len(self._designs) - 1)
        self._refresh_plan()

    def _select_design(self, index):
        self._selected_design = index
        self._loading_design = True
        try:
            has_design = 0 <= index < len(self._designs)
            if has_design:
                settings = self._designs[index]
                for field, control in self.design_value_controls.items():
                    control.setValue(getattr(settings, field))
            factors = self._design_factors[index] if has_design else {}
            for spec in (*FACTOR_SPECS, SIZE_FACTOR_SPEC):
                field, _, _, _, low, high = spec
                enabled, lo, hi = self.factor_controls[field]
                enabled.setChecked(field in factors)
                lower, upper = factors.get(field, (low, high))
                lo.setValue(lower)
                hi.setValue(upper)
        finally:
            self._loading_design = False
        self._refresh_plan()

    def _edit_design(self, *_):
        if (self._loading_design or self._selected_design < 0
                or self._active_mode is not None):
            return
        index = self._selected_design
        settings = replace(self._designs[index],
                           **{field: control.value() for field, control in self.design_value_controls.items()})
        self._designs[index] = settings
        self._update_design_caption(index)
        self._refresh_plan()

    def _active_factors(self) -> dict[str, tuple[float, float]]:
        return {
            name: (low.value(), high.value())
            for name, (enabled, low, high) in self.factor_controls.items()
            if enabled.isChecked()
        }

    def _refresh_plan(self, *_) -> None:
        if self._loading_design:
            return
        factors = self._active_factors()
        if 0 <= self._selected_design < len(self._design_factors):
            from doe_planner import geometry_group
            group = geometry_group(self._designs[self._selected_design])
            self.planner._group_factors[group] = factors.copy()
            for index, design in enumerate(self._designs):
                if geometry_group(design) == group:
                    self._design_factors[index] = factors.copy()
        for name, (_, low, high) in self.factor_controls.items():
            varies = name in factors
            low.setEnabled(varies)
            high.setEnabled(varies)
        idle = self._active_mode is None
        self.design_list.setEnabled(idle)
        self.sample_settings.setEnabled(idle)
        self.open_plan_btn.setEnabled(idle)
        self.save_plan_btn.setEnabled(idle)
        ml_total, ml_message = self._plan_summary()
        self.ml_plan_label.setText(ml_message + "\nUses each geometry's selected variables. Without variables, only its reference case is generated.")
        self.generate_ml_btn.setEnabled(0 < ml_total <= MAX_DOE_ROWS and idle)

    def _plan_summary(self):
        designs = self._included_designs()
        if not designs:
            return 0, "Add a geometry and check the saved designs to include in your experiment plan."
        total = 0
        for design, factors in designs:
            errors = validate_geometry(design, include_mesh=False)
            if errors:
                return 0, f"{design.structure_name}: {errors[0]}"
            for name, (low, high) in factors.items():
                if low >= high:
                    return 0, f"{design.structure_name} · {name}: Min must be below Max."
                if design.imported_geometry_path:
                    return 0, "Imported geometry has fixed dimensions; disable geometry variables."
            total += self.ml_count_spin.value() if factors else 1
        if total > MAX_DOE_ROWS:
            return total, f"{total:,} runs exceed the {MAX_DOE_ROWS:,}-run limit."
        return total, f"{len(designs)} reference configurations · {total:,} experiments"

    def generate_ml_table(self):
        if not self.generate_ml_btn.isEnabled() or self._active_mode is not None:
            return
        if self._rows and QMessageBox.question(
            self, "Replace experiments?", "Generating a new plan replaces the current table."
        ) != QMessageBox.StandardButton.Yes:
            return
        rows = []
        for design, factors in self._included_designs():
            rows.extend(generate_doe_rows(
                [design], factors, self.ml_count_spin.value(), self.ml_seed_spin.value(),
                self.common_strain_spin.value() if self.common_strain_check.isChecked() else None,
            ))
        for index, row in enumerate(rows, 1):
            row.exp_id = f"ML-{index:04d}"
            errors = validate_geometry(row.to_settings(self._settings_provider()), include_mesh=False)
            if errors:
                QMessageBox.warning(self, "Invalid experiment range", f"{row.exp_id} · {row.structure_name}: {errors[0]}\nAdjust this geometry's fixed values or variable ranges.")
                return
        self.sample_settings.apply_names(rows)
        self._rows = rows
        self._refresh_table_view()

    def add_blank_row(self) -> None:
        if self._active_mode is not None:
            return
        base = self._designs[self._selected_design] if self._selected_design >= 0 else self._settings_provider()
        from doe_sample_rules import unique_name

        exp_id = unique_name(f"ML-{len(self._rows) + 1:04d}", (row.exp_id for row in self._rows))
        self._rows.append(
            ExperimentRow(
                exp_id=exp_id,
                enabled=True,
                structure_family=base.structure_family,
                structure_name=base.structure_name,
                tpms_morphology=base.tpms_morphology,
                size=base.size,
                cell_size=base.cell_size,
                solid_fraction=base.solid_fraction,
                mesh_resolution=base.mesh_resolution,
                compression=base.compression,
                random_seed=base.random_seed,
                design_settings=base,
            )
        )
        self._refresh_table_view()

    def _new_dataset_path(self) -> Path:
        stem = self._plan_path.name.split(".")[0] if self._plan_path else "unsaved-plan"
        return results_directory() / f"{stem}_{datetime.now():%Y%m%d-%H%M%S}.csv"
