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
import itertools
import json
import math
from analysis_process import AnalysisProcess
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable

import numpy as np
from studio_style import CompactComboBox
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from model import MIN_MESH_INPUT_MM, ROOT, Settings, settings_from_dict, validate_geometry
from studio_style import CONTROL_HEIGHT, NUMERIC_FIELD_WIDTH, CompactDoubleSpinBox, SidebarGroupBox
from structure_library import (
    TPMS_MORPHOLOGIES,
    resolve_family_and_name,
)


DOE_FIELD_WIDTH = NUMERIC_FIELD_WIDTH
DOE_ACTION_WIDTH = 96
DOE_RUN_WIDTH = 140
DOE_STOP_WIDTH = 108
DOE_TOOLBAR_GAP = 6
MAX_DOE_ROWS = 5000
SNAPSHOT_HEADER = "Design settings (JSON)"

# Field, label, input bounds, default range, increment, precision and levels.
FACTOR_SPECS = (
    ("size", "Cube size (mm)", 1.0, 100.0, 6.0, 9.0, 0.5, 3, 3),
    ("cell_size", "Period / pore (mm)", 0.1, 100.0, 2.0, 4.0, 0.25, 3, 5),
    ("solid_fraction", "Solid fraction (%)", 1.0, 99.0, 15.0, 45.0, 5.0, 2, 5),
)


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
    factors: dict[str, tuple[float, float, int]],
    sampling_mode: str,
    lhs_count: int,
    sampling_seed: int,
    target_strain_pct: float | None = None,
) -> list[ExperimentRow]:
    """Expand the selected factors for every saved design."""
    rows = []
    samples = list(_factor_samples(factors, sampling_mode, lhs_count, sampling_seed))
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


def _factor_samples(factors, sampling_mode, lhs_count, sampling_seed):
    if not factors:
        return [()]
    if sampling_mode == "lhs":
        ranges = [(low, high) for low, high, _ in factors.values()]
        return _lhs_samples(lhs_count, ranges, seed=sampling_seed)
    return itertools.product(*[
        np.linspace(low, high, levels) for low, high, levels in factors.values()
    ])


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


class DoEStudioWidget(QWidget):
    """Interactive Excel-like Design of Experiments (DoE) table & Batch Solver."""

    preview_requested = Signal(object)  # Existing table-row preview.
    boundary_preview_requested = Signal(str, object)
    model_requested = Signal()

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
        self._designs: list[Settings] = []
        self._updating_table = False
        self._active_future = None
        self._active_mode: str | None = None  # "check" or "solve"
        self._queue: list[int] = []
        self._total_queued = 0
        self._stop_requested = False

        self._build_ui()
        self._refresh_plan()
        self._update_badge()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(80)
        self._poll_timer.timeout.connect(self._poll_worker)
        self._poll_timer.start()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(6)

        self.vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        self.vertical_splitter.setChildrenCollapsible(True)
        self.vertical_splitter.setOpaqueResize(True)
        self.vertical_splitter.setHandleWidth(5)

        top_splitter = QSplitter(Qt.Orientation.Horizontal)
        top_splitter.setChildrenCollapsible(True)
        top_splitter.setOpaqueResize(True)
        top_splitter.setHandleWidth(5)
        top_splitter.setMinimumHeight(60)
        top_splitter.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )

        struct_group = SidebarGroupBox("1. Designs from 3D Model")
        struct_group.setMinimumSize(246, 60)
        struct_layout = QVBoxLayout(struct_group.content)
        struct_layout.setContentsMargins(6, 6, 6, 6)
        struct_layout.setSpacing(6)

        hint = QLabel("Create a model, update its preview, then choose Add to DoE.")
        hint.setWordWrap(True)
        struct_layout.addWidget(hint)
        self.design_list = QListWidget()
        self.design_list.setAlternatingRowColors(True)
        self.design_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.design_list.currentRowChanged.connect(self._refresh_plan)
        struct_layout.addWidget(self.design_list, 1)

        design_actions = QHBoxLayout()
        open_model = QPushButton("Go to 3D Model")
        open_model.setObjectName("secondaryActionBtn")
        open_model.clicked.connect(self.model_requested)
        self.remove_design_btn = QPushButton("Remove")
        self.remove_design_btn.setObjectName("destructiveActionBtn")
        self.remove_design_btn.clicked.connect(self._remove_design)
        design_actions.addWidget(open_model)
        design_actions.addWidget(self.remove_design_btn)
        design_actions.addStretch()
        struct_layout.addLayout(design_actions)
        top_splitter.addWidget(struct_group)

        # 2. Right: Parameter Ranges (Min / Max / Steps) & Sampling Strategy
        range_scroll = QScrollArea()
        range_scroll.setWidgetResizable(True)
        range_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        range_scroll.setMinimumSize(140, 60)
        range_scroll.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )
        range_group = SidebarGroupBox("2. Sampling and parameters")
        range_layout = QVBoxLayout(range_group.content)
        range_layout.setContentsMargins(6, 6, 6, 6)
        range_layout.setSpacing(3)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Sampling"))
        self.sampling_combo = CompactComboBox()
        self.sampling_combo.addItem(
            "Full factorial grid", "full_factorial"
        )
        self.sampling_combo.addItem(
            "Latin hypercube (LHS)",
            "lhs",
        )
        self.sampling_combo.currentIndexChanged.connect(self._on_sampling_mode_changed)
        mode_row.addWidget(self.sampling_combo, 1)

        self.lhs_count_label = QLabel("Runs per design")
        self.lhs_count_spin = QSpinBox()
        self.lhs_count_spin.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.lhs_count_spin.setFixedWidth(DOE_FIELD_WIDTH)
        self.lhs_count_spin.setRange(2, 500)
        self.lhs_count_spin.setValue(15)
        mode_row.addWidget(self.lhs_count_label)
        mode_row.addWidget(self.lhs_count_spin)
        range_layout.addLayout(mode_row)
        self.sampling_seed = self._make_ispin(1, 999999, 42)
        self.sampling_seed_label = QLabel("LHS seed")
        mode_row.addWidget(self.sampling_seed_label)
        mode_row.addWidget(self.sampling_seed)
        self.lhs_count_spin.valueChanged.connect(self._refresh_plan)

        strain_row = QHBoxLayout()
        self.common_strain_check = QCheckBox("Common final strain (%)")
        self.common_strain_check.setChecked(True)
        self.common_strain_spin = self._make_dspin(0.1, 95.0, 10.0, 0.5, 2)
        self.common_strain_check.toggled.connect(self.common_strain_spin.setEnabled)
        strain_row.addWidget(self.common_strain_check)
        strain_row.addWidget(self.common_strain_spin)
        strain_row.addStretch()
        range_layout.addLayout(strain_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(5)
        grid.setVerticalSpacing(3)
        for col, header_text in enumerate(("Vary parameter", "Min", "Max", "Levels")):
            hdr = QLabel(header_text)
            hdr.setObjectName("sectionSubheader")
            grid.addWidget(hdr, 0, col)

        self.factor_controls = {}
        self.preview_factor = CompactComboBox()
        self.preview_factor.setAccessibleName("Factor to preview")
        self.preview_factor.setPlaceholderText("Select a variable above")
        for row, spec in enumerate(FACTOR_SPECS, start=1):
            self._add_factor_row(grid, row, spec)
        grid.setColumnStretch(0, 1)
        range_layout.addLayout(grid)

        preview_row = QHBoxLayout()
        preview_row.addWidget(self.preview_factor, 1)
        self.preview_bounds_btn = QPushButton("Preview bounds")
        self.preview_bounds_btn.setObjectName("secondaryActionBtn")
        self.preview_bounds_btn.clicked.connect(self._preview_bounds)
        preview_row.addWidget(self.preview_bounds_btn)
        range_layout.addLayout(preview_row)

        plan_row = QHBoxLayout()
        self.plan_label = QLabel()
        self.plan_label.setWordWrap(True)
        self.plan_label.setObjectName("mutedHintLabel")
        plan_row.addWidget(self.plan_label, 1)
        self.generate_btn = QPushButton("Generate experiments")
        self.generate_btn.setObjectName("primaryLaunchBtn")
        self.generate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.generate_btn.clicked.connect(self.generate_table)
        plan_row.addWidget(self.generate_btn)
        range_layout.addLayout(plan_row)
        range_layout.addStretch()
        range_scroll.setWidget(range_group)
        top_splitter.addWidget(range_scroll)
        top_splitter.setStretchFactor(0, 2)
        top_splitter.setStretchFactor(1, 3)
        self.vertical_splitter.addWidget(top_splitter)

        bottom_panel = QWidget()
        bottom_panel.setMinimumSize(140, 60)
        bottom_panel.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )
        bottom_layout = QVBoxLayout(bottom_panel)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(6)

        # 3. Spreadsheet & Batch Execution Toolbar
        bar_frame = QFrame()
        bar_frame.setObjectName("viewportToolbar")
        bar_frame.setMinimumWidth(140)
        bar_frame.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        bar = QHBoxLayout(bar_frame)
        bar.setContentsMargins(4, 3, 4, 3)
        bar.setSpacing(DOE_TOOLBAR_GAP)

        self.row_count_badge = QLabel("0 Experiments")
        self.row_count_badge.setObjectName("unitBadge")
        bar.addWidget(self.row_count_badge)

        for label, callback in (
            ("+ Row", self.add_blank_row),
            ("− Delete", self.delete_selected_rows),
            ("Preview in 3D", self.preview_selected_row),
            ("Check meshes", self.start_mesh_precheck),
        ):
            btn = QPushButton(label)
            btn.setObjectName(
                "secondaryActionBtn"
                if label in ("Check meshes", "Preview in 3D")
                else "compactActionBtn"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(callback)
            btn.setFixedWidth(DOE_ACTION_WIDTH)
            bar.addWidget(btn)

        data_button = QPushButton("Table data")
        data_button.setObjectName("compactActionBtn")
        data_menu = QMenu(data_button)
        data_menu.addAction("Copy for Excel", self.copy_table_to_clipboard)
        data_menu.addAction("Import CSV…", self.import_csv_dialog)
        data_menu.addAction("Export CSV…", self.export_csv_dialog)
        data_button.setMenu(data_menu)
        data_button.setFixedWidth(DOE_ACTION_WIDTH)
        bar.addWidget(data_button)
        bar.addStretch()

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(DOE_RUN_WIDTH - DOE_STOP_WIDTH - DOE_TOOLBAR_GAP)
        self.progress_bar.setVisible(False)
        bar.addWidget(self.progress_bar)

        self.run_batch_btn = QPushButton("Run batch (ANSYS)")
        self.run_batch_btn.setObjectName("primaryLaunchBtn")
        self.run_batch_btn.setFixedWidth(DOE_RUN_WIDTH)
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

        bottom_layout.addWidget(bar_frame)

        # 4. Excel-like QTableWidget
        self.table = QTableWidget(0, len(TABLE_COLUMNS))
        self.table.setHorizontalHeaderLabels(list(TABLE_COLUMNS))
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
        self.table.itemChanged.connect(self._on_table_item_changed)
        self.table.cellDoubleClicked.connect(self._on_row_double_clicked)
        bottom_layout.addWidget(self.table, 1)

        self.vertical_splitter.addWidget(bottom_panel)
        self.vertical_splitter.setStretchFactor(0, 0)
        self.vertical_splitter.setStretchFactor(1, 1)
        self.vertical_splitter.setSizes([330, 420])
        root.addWidget(self.vertical_splitter, 1)

        self._on_sampling_mode_changed()

    @staticmethod
    def _make_dspin(low: float, high: float, val: float, step: float, dec: int) -> QDoubleSpinBox:
        sp = CompactDoubleSpinBox()
        sp.setRange(low, high)
        sp.setSingleStep(step)
        sp.setValue(val)
        sp.setFixedWidth(DOE_FIELD_WIDTH)
        return sp

    @staticmethod
    def _make_ispin(low: int, high: int, val: int) -> QSpinBox:
        sp = QSpinBox()
        sp.setFixedHeight(CONTROL_HEIGHT)
        sp.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        sp.setRange(low, high)
        sp.setValue(val)
        sp.setFixedWidth(DOE_FIELD_WIDTH)
        return sp

    def _add_factor_row(self, grid, row, spec) -> None:
        field, label, lower, upper, minimum, maximum, step, decimals, levels = spec
        enabled = QCheckBox(label)
        low = self._make_dspin(lower, upper, minimum, step, decimals)
        high = self._make_dspin(lower, upper, maximum, step, decimals)
        count = self._make_ispin(2, 50, levels)
        self.factor_controls[field] = (enabled, low, high, count)
        enabled.toggled.connect(self._refresh_plan)
        for column, control in enumerate((enabled, low, high, count)):
            grid.addWidget(control, row, column)
            if column:
                control.valueChanged.connect(self._refresh_plan)

    def add_design(self, settings: Settings) -> None:
        """Capture a model snapshot or select the matching existing design."""
        if settings in self._designs:
            self.design_list.setCurrentRow(self._designs.index(settings))
            return
        self._designs.append(settings)
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
        self.design_list.addItem(item)
        self.design_list.setCurrentRow(len(self._designs) - 1)
        self._refresh_plan()

    def _remove_design(self) -> None:
        index = self.design_list.currentRow()
        if index >= 0:
            self._designs.pop(index)
            self.design_list.takeItem(index)
            self._refresh_plan()

    def _active_factors(self) -> dict[str, tuple[float, float, int]]:
        return {
            name: (low.value(), high.value(), count.value())
            for name, (enabled, low, high, count) in self.factor_controls.items()
            if enabled.isChecked()
        }

    def _on_sampling_mode_changed(self, *_) -> None:
        self._refresh_plan()

    def _refresh_plan(self, *_) -> None:
        lhs = self.sampling_combo.currentData() == "lhs"
        factors = self._active_factors()
        for control in (self.lhs_count_label, self.lhs_count_spin,
                        self.sampling_seed_label, self.sampling_seed):
            control.setVisible(lhs)
        selected = self.preview_factor.currentData()
        self.preview_factor.clear()
        for name, (enabled, low, high, count) in self.factor_controls.items():
            varies = name in factors
            low.setEnabled(varies)
            high.setEnabled(varies)
            count.setEnabled(varies and not lhs)
            if varies:
                self.preview_factor.addItem(enabled.text(), name)
        if selected in factors:
            self.preview_factor.setCurrentIndex(self.preview_factor.findData(selected))
        elif factors:
            self.preview_factor.setCurrentIndex(0)
        has_design = self.design_list.currentRow() >= 0
        self.remove_design_btn.setEnabled(has_design)
        self.preview_bounds_btn.setEnabled(has_design and bool(factors))
        self.preview_factor.setEnabled(bool(factors))
        total, message = self._plan_summary(factors, lhs)
        self.plan_label.setText(message)
        self.generate_btn.setEnabled(0 < total <= MAX_DOE_ROWS and self._active_mode is None)

    def _plan_summary(self, factors, lhs) -> tuple[int, str]:
        designs = len(self._designs)
        if not designs:
            return 0, "Add a design from 3D Model."
        for name, (low, high, _) in factors.items():
            if low >= high:
                label = self.factor_controls[name][0].text()
                return 0, f"{label}: Min must be below Max."
        per_design = self.lhs_count_spin.value() if lhs and factors else math.prod(
            levels for _, _, levels in factors.values()
        )
        total = designs * per_design
        if total > MAX_DOE_ROWS:
            return total, f"{total:,} runs exceed the {MAX_DOE_ROWS:,}-run limit."
        return total, f"{designs} designs × {per_design:,} runs = {total:,} experiments"

    def _preview_bounds(self) -> None:
        index = self.design_list.currentRow()
        name = self.preview_factor.currentData()
        if index < 0 or name is None:
            return
        base = self._designs[index]
        low, high, _ = self._active_factors()[name]
        label = self.preview_factor.currentText()
        self.boundary_preview_requested.emit(label, {
            f"Reference · {getattr(base, name):g}": base,
            f"Min · {low:g}": replace(base, **{name: low}),
            f"Max · {high:g}": replace(base, **{name: high}),
        })

    def generate_table(self) -> None:
        if not self.generate_btn.isEnabled():
            return
        if self._rows and QMessageBox.question(
            self, "Replace experiments?", "Generating a new plan replaces the current table."
        ) != QMessageBox.StandardButton.Yes:
            return
        self._rows = generate_doe_rows(
            self._designs, self._active_factors(), self.sampling_combo.currentData(),
            self.lhs_count_spin.value(), self.sampling_seed.value(),
            self.common_strain_spin.value() if self.common_strain_check.isChecked() else None,
        )
        self._refresh_table_view()

    def _refresh_table_view(self) -> None:
        self._updating_table = True
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

    def add_blank_row(self) -> None:
        base = self._settings_provider()
        idx = len(self._rows) + 1
        self._rows.append(
            ExperimentRow(
                exp_id=f"EXP-{idx:04d}",
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
            )
        )
        self._refresh_table_view()

    def delete_selected_rows(self) -> None:
        if self._active_future is not None:
            return
        selected_rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        if not selected_rows:
            return
        for r in selected_rows:
            if 0 <= r < len(self._rows):
                self._rows.pop(r)
        for i, row in enumerate(self._rows, start=1):
            row.exp_id = f"EXP-{i:04d}"
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

    def stop_active_queue(self) -> None:
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
        self._launch_next_in_queue()

    def start_mesh_precheck(self) -> None:
        if self._active_future is not None or not self._rows:
            return
        self._queue = [i for i, r in enumerate(self._rows) if r.enabled]
        if not self._queue:
            return
        self._active_mode = "check"
        self._stop_requested = False
        self._total_queued = len(self._queue)
        self.progress_bar.setRange(0, self._total_queued)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.stop_batch_btn.setVisible(True)
        self.run_batch_btn.hide()
        self._refresh_plan()
        self._launch_next_in_queue()

    def start_batch_solve(self) -> None:
        if self._active_future is not None or not self._rows:
            return
        if self._batch_solver_runner is None:
            QMessageBox.information(
                self,
                "Solver Not Configured",
                "Batch solver runner is not attached.",
            )
            return
        self._queue = [
            i
            for i, r in enumerate(self._rows)
            if r.enabled and not r.status.lower().startswith("completed")
        ]
        if not self._queue:
            QMessageBox.information(
                self,
                "No Pending Rows",
                "All checked rows are already marked Completed, or no rows are checked.",
            )
            return
        self._active_mode = "solve"
        self._stop_requested = False
        self._total_queued = len(self._queue)
        self.progress_bar.setRange(0, self._total_queued)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.stop_batch_btn.setVisible(True)
        self.run_batch_btn.hide()
        self._refresh_plan()
        self._launch_next_in_queue()

    def _launch_next_in_queue(self) -> None:
        if self._stop_requested or not self._queue:
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
        if getattr(self,"remote_controller",None) is not None and self.remote_controller.requested:
            row.status = "TRUBA · checking mesh" if self._active_mode=="check" else "TRUBA · solving"
        self._updating_table = True
        self._write_row_cells(r_idx, row)
        self._updating_table = False
        self.table.scrollToItem(self.table.item(r_idx, COL_STATUS))

        s = row.to_settings(self._settings_provider())
        worker = ExperimentWorker(self._mesh_generator, self._batch_solver_runner)
        try:
            controller = getattr(self,"remote_controller",None)
            if controller is not None and controller.requested:
                from truba_remote import run_remote_doe
                context,auth = controller.require("mesh" if self._active_mode=="check" else s.solver_backend,s)
                self._active_future = AnalysisProcess(run_remote_doe,r_idx,s,context,self._active_mode!="check",remote_auth=auth)
            elif self._active_mode == "check":
                self._active_future = AnalysisProcess(worker._evaluate_row_mesh, r_idx, s)
            else:
                self._active_future = AnalysisProcess(worker._solve_row, r_idx, s)
        except Exception as exc:
            self._updating_table = True
            row.status = f"Failed to start: {exc}"
            self._write_row_cells(r_idx, row)
            self._updating_table = False
            self._queue.clear()
            self._launch_next_in_queue()

    def _poll_worker(self) -> None:
        if self._active_future is None or not self._active_future.done():
            return
        fut, self._active_future = self._active_future, None
        try:
            payload = fut.result()
        except Exception as exc:
            payload = {"r_idx": self._queue[0], "status": f"Failed: {exc}"}
        r_idx = payload["r_idx"]
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
            self._updating_table = True
            self._write_row_cells(r_idx, row)
            self._updating_table = False
            if self._active_mode == "solve":
                try:
                    self.export_csv_path(ROOT / "doe_master_dataset.csv")
                except OSError:
                    pass
        done_count = self._total_queued - len(self._queue)
        self.progress_bar.setValue(done_count)
        self._launch_next_in_queue()

    def copy_table_to_clipboard(self) -> None:
        lines = ["\t".join(TABLE_COLUMNS)]
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
        with path.open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow([
                *TABLE_COLUMNS, SNAPSHOT_HEADER, "Geometry group", "Dataset split",
                "Final nominal strain (%)", "Target strain (%)", "Run folder", "Force-displacement CSV",
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
                ])

    def export_csv_dialog(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export Design of Experiments Table (CSV / Excel)",
            str(ROOT / "doe_experiment_table.csv"),
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
        with path.open("r", newline="", encoding="utf-8-sig") as stream:
            reader = csv.reader(stream)
            for idx, raw in enumerate(reader):
                if not raw or all(not cell.strip() for cell in raw):
                    continue
                if idx == 0 and raw[0].strip().lower() == TABLE_COLUMNS[0].lower():
                    strain_column = raw.index("Target strain (%)") if "Target strain (%)" in raw else None
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
        if loaded:
            self._rows = loaded
            self._refresh_table_view()

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

