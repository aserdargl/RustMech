"""Shared window, results directory, and analysis queue execution."""
from __future__ import annotations

import csv
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

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from model import ROOT, Settings, validate_geometry
from studio_style import ActionFlowLayout, CONTROL_HEIGHT
from structure_library import resolve_family_and_name


DOE_STOP_WIDTH = 108
DOE_TOOLBAR_GAP = 6
SNAPSHOT_HEADER = "Design settings (JSON)"

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

    started_at: str = ""
    duration_s: float | None = None
    attempts: int = 0

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

RESULTS_ENV = "RUSTMECH_DOE_RESULTS"


def results_directory() -> Path:
    """Per-session result files; tests and users may redirect them."""
    return Path(os.environ.get(RESULTS_ENV) or ROOT / "DoEResults")


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

class ExperimentTableWidget(TrubaParallelMixin, QWidget):
    """Shared experiment table and local/TRUBA execution lifecycle."""

    preview_requested = Signal(object)

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
        self._results_path = None
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

        actions = (
            ("− Delete", self.delete_selected_rows),
            ("Preview in 3D", self.preview_selected_row),
            ("Check meshes", self.start_mesh_precheck),
        )
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
        self.table = QTableWidget(0, len(TABLE_COLUMNS))
        self.table.setObjectName("doeExperimentTable")
        self.table.setShowGrid(False)
        self.table.setHorizontalHeaderLabels(TABLE_COLUMNS)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for column, title in enumerate(TABLE_COLUMNS):
            width = self.table.fontMetrics().horizontalAdvance(title) + 18
            self.table.setColumnWidth(column, max(85, min(240, width)))
            self.table.horizontalHeaderItem(column).setToolTip(title)
        self.table.cellDoubleClicked.connect(self._on_row_double_clicked)
        layout.addWidget(self.table, 1)

        self.footer_label = QLabel()
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

    def _refresh_table_view(self) -> None:
        self._updating_table = True
        self.table.setColumnCount(len(TABLE_COLUMNS))
        self.table.setHorizontalHeaderLabels(TABLE_COLUMNS)
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
        self._results_path = self._new_results_path()
        self._begin_run()
        self._queue, notes = self._preflight(self._queue)
        if notes:
            self._report_persistence_problem(" | ".join(notes[:3]))
        if not self._queue:
            self._active_mode = None
            self._frozen_base = None
            return
        self._total_queued = len(self._queue)
        self.progress_bar.setRange(0, self._total_queued)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.stop_batch_btn.setVisible(True)
        self.run_batch_btn.hide()
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
        """Save queue results and display any write error."""
        problems = []
        path = self._results_path or self._new_results_path()
        self._results_path = path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.export_csv_path(path)
        except OSError as error:
            problems.append(f"Results CSV was not saved: {error}")
        self._report_persistence_problem("; ".join(problems))

    def _report_persistence_problem(self, message: str) -> None:
        if message == self._persistence_error:
            return
        if self._footer_default is None:
            self._footer_default = self.footer_label.text()
        self._persistence_error = message
        self.footer_label.setText(message or self._footer_default)
        self.footer_label.setToolTip(message)

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
                *TABLE_COLUMNS, SNAPSHOT_HEADER, "Final nominal strain (%)", "Run folder", "Force-displacement CSV",
                "Started at", "Duration (s)", "Attempts",
            ])
            for row in self._rows:
                curve = Path(row.run_folder) / "04_Graphs" / "force_displacement.csv"
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
                    100 * row.compression / row.size if row.size > 0 else "",
                    row.run_folder,
                    str(curve) if row.run_folder and curve.is_file() else "",
                    row.started_at,
                    "" if row.duration_s is None else f"{row.duration_s:.3f}",
                    row.attempts,
                ])

    @property
    def rows(self) -> list[ExperimentRow]:
        return self._rows

    def dispose(self) -> None:
        self._stop_requested = True
        self._poll_timer.stop()
        self.stop_active_queue()
