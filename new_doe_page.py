"""Independent operation-specific experiment planner in the DoE window."""
from collections import deque
import csv
import json
from math import isfinite
from time import monotonic
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                               QFormLayout, QHeaderView,
                               QHBoxLayout, QLabel, QLayout, QLineEdit, QMainWindow, QMenu, QPushButton,
                               QScrollArea, QSpinBox, QSplitter,
                               QStackedWidget, QTabWidget, QTableView, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from material_catalog import builtin_materials
from material_library import MaterialCard
from doe_planner import DoENavigation
from new_doe_operations import (MAX_ROWS, OPERATIONS, explicit_numbers, launch, lhs_matrix, matrix, matrix_request,
                                parameters, snapshot, structure_choices, validate)
from new_doe_report import (LEARNING_CSV_NAME, LEARNING_RUN_PREFIX, experiment_name, write_doe_report,
                            write_learning_csv)
from structure_library import ALL_STRUCTURES_BY_FAMILY, STRUCTURE_FAMILIES
from studio_style import (COMBO_WIDTH, CONTROL_HEIGHT, CompactComboBox, CompactSpinBox, NumericLineEdit,
                          SidebarGroupBox)
from window_state import show_restored_window
from ui_language import translate, turkish
from model import CONTACT_NUMERICAL_FIELDS, GEOMETRY_FIELDS, MATERIAL_FIELDS

NUMBER_WIDTH = 110
CONTROL_GAP = 3
LABELS = {"grid": "Voxels per axis", "void_ratio": "Void / solid stiffness", "distribution": "WSL distribution",
          "structure_name": "Structure family / type", "tpms_morphology": "Morphology",
          "young_modulus": "Young’s modulus (MPa)", "cell_size": "Cell size (mm)",
          "solid_fraction": "Solid fraction (%)", "size_x": "Outer X (mm)", "size_y": "Outer Y (mm)",
          "size": "Outer Z (mm)", "material_card": "Material card",
          "fluid_material_card": "Material card", "solid_material_card": "Material card"}

MATERIAL_CARD_FIELDS = {("settings", "material_card"), ("flow", "fluid_material_card"),
                        ("thermal", "solid_material_card")}
FLOW_MATERIAL_PROPERTIES = {("flow", "density"): "density", ("flow", "dynamic_viscosity"): "dynamic_viscosity"}
FEM_MESH_FIELDS = ("mesh_resolution", "mesh_type", "tetra_strategy", "local_mesh_size")
FLOW_MESH_FIELDS = ("cells_across", "mesh_method", "surface_refinement", "boundary_layers", "max_fitted_cells")
FLOW_SOLVER_FIELDS = ("inlet_speed", "iterations", "turbulence", "distribution", "foam_bashrc")
FFT_GROUP = "FFT Homogenization"
PREVIEW_BUTTONS = {"Structure": "Preview structure", "Mesh": "Preview mesh"}
THERMAL_FLUID_PROPERTIES = {("thermal", "water_cp"): "heat_capacity",
                            ("thermal", "water_conductivity"): "conductivity"}
THERMAL_SOLID_PROPERTIES = {("thermal", "solid_density"): "density", ("thermal", "solid_cp"): "heat_capacity",
                            ("thermal", "solid_conductivity"): "conductivity"}


def material_requirements(path, op):
    if path[0] == "settings":
        required = ("young_modulus", "poisson_ratio")
        return "solid", required if op == "fft_solve" else (*required, "density")
    if path[0] == "thermal":
        return "solid", tuple(THERMAL_SOLID_PROPERTIES.values())
    required = tuple(FLOW_MATERIAL_PROPERTIES.values())
    if op == "thermal_solve":
        required += tuple(THERMAL_FLUID_PROPERTIES.values())
    return "fluid", required


def parameter_group(path, op):
    key = path[-1]
    if path in (("options", "resolution"), ("mpm", "grid_mm"), ("mpm", "points_per_axis")):
        return "Mesh"
    if path[0] == "settings":
        if key in ("test_profile", "specimen_architecture", "core_fraction"):
            return "Standard test"
        if key in FEM_MESH_FIELDS:
            return "Mesh"
        if key in GEOMETRY_FIELDS:
            return "Structure"
        if key in MATERIAL_FIELDS:
            return "Solid phase material" if op == "fft_solve" else "Solid material"
        if key.startswith("kratos_"):
            return "Kratos · nonlinear solution"
        if key.startswith(("bottom_platen", "top_platen", "self_")) or "contact" in key or key in CONTACT_NUMERICAL_FIELDS:
            return "Contact and friction"
        return "Solver and loading"
    if path[0] == "mpm":
        return "MPM solid material" if key in ("young_mpa", "poisson_ratio", "density_kg_m3") else "Loading and discretization"
    if path[0] == "thermal":
        if key.startswith("solid_"):
            return "Solid material"
        return "Fluid material" if key.startswith("water_") else "Thermal (CHT)"
    if path[0] == "flow":
        if key in ("density", "dynamic_viscosity", "fluid_material_card"):
            return "Fluid material"
        if key in FLOW_MESH_FIELDS:
            return "Mesh"
        return "Channel · Isothermal CFD"
    return "Peridynamics" if path[0] == "options" else FFT_GROUP


def field_sources(host, op):
    if op == "fft_solve":
        material = host.fft_material_editor
        return dict(grid=host.fft_grid, void_ratio=host.fft_void_ratio, cell_size=host.fft_cell_size,
                    solid_fraction=host.fft_solid_fraction, tpms_morphology=host.fft_morphology_combo,
                    distribution=host.fft_distribution, material_name=material.name,
                    poisson_ratio=material.controls["poisson_ratio"],
                    young_modulus=material.controls["young_modulus"] if material.stress_unit.currentData() == 1 else None,
                    **host.fft_outer_sizes)
    if op == "peridynamic_solve":
        controls = host.peridynamic_settings.controls
        return dict(controls, size_x=controls["size"], size_y=controls["size"])
    sources = dict(host.controls, size_x=host.controls["size"], size_y=host.controls["size"],
                   tpms_morphology=host.morphology_combo,
                   test_profile=host.test_profile_combo, specimen_architecture=host.architecture_combo)
    if op == "mechanical":
        sources.update(host.kratos_panel.fields)
        sources.update(solver_backend=host.solver_combo, loading_mode=host.loading_mode_combo,
                       mesh_type=host.mesh_type_combo, tetra_strategy=host.tetra_strategy_combo,
                       self_contact_mode=host.self_contact_combo, contact_formulation=host.contact_formulation_combo,
                       bottom_platen_contact=host.platen_contact_combos["bottom"],
                       top_platen_contact=host.platen_contact_combos["top"], material_model=host.material_editor.model)
        # Material controls can display stresses in units other than the stored MPa.
        for key in host.material_editor.controls:
            sources.pop(key, None)
    elif op == "mpm_solve":
        sources.update(host.mpm_workspace.controls)
    else:
        ws = host.flow_workspace if op == "flow_solve" else host.thermal_workspace
        sources.update(ws.controls, mesh_method=ws.mesh_method, channel_shape=ws.channel_shape,
                       turbulence=ws.model, distribution=ws.distribution, foam_bashrc=ws.bashrc,
                       empty_pipe=ws.empty, allow_bypass=ws.allow_bypass)
        sources.update(getattr(ws, "thermal_controls", {}))
    return sources


def display(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def parameter_value(request, path):
    return (request if len(path) == 1 else request[path[0]])[path[-1]]


def number(value, source):
    if isinstance(source, (QSpinBox, QDoubleSpinBox)):
        return NumericLineEdit(value, min(value, source.minimum()), max(value, source.maximum()),
                               width=NUMBER_WIDTH)
    bound = 2147483647 if type(value) is int else max(1e15, abs(value))
    return NumericLineEdit(value, -bound, bound, width=NUMBER_WIDTH)


def text_field(value=""):
    field = QLineEdit(value)
    field.setFixedHeight(CONTROL_HEIGHT)
    return field


class Factor:
    def __init__(self, table, row, variations, variation_row, path, value, source, op, changed, *, sampling=False):
        self.value = value
        self.path = path
        key = path[-1]
        self.label = LABELS.get(key, key.replace("_", " ").capitalize())
        if source is not None and key not in LABELS:
            parent = source.parentWidget()
            if parent is not None:
                label = next((label for label in parent.findChildren(QLabel)
                              if label.buddy() is source), None)
                if label is not None:
                    self.label = label.text()
        for view, cell in ((table, (row, 1)), (variations, (variation_row, 0))):
            item = QTableWidgetItem(self.label)
            item.setToolTip(translate(self.label))
            view.setItem(*cell, item)
        self.variable = QCheckBox()
        self.variable.setAccessibleName(f"{translate('Variable')} · {translate(self.label)}")
        self.variable.setToolTip(translate("Variable"))
        self.variations = variations
        self.variation_row = variation_row
        self.choices = []
        self.material_card = path in MATERIAL_CARD_FIELDS
        if key == "structure_name":
            self.choices = structure_choices(op)
        elif isinstance(source, QComboBox):
            self.choices = [(source.itemText(i), source.itemData(i)) for i in range(source.count())
                            if source.itemData(i) is not None and source.model().item(i).isEnabled()]
            if not self.choices and isinstance(value, str) and source.findText(value) >= 0:
                self.choices = [(source.itemText(i), source.itemText(i)) for i in range(source.count())
                                if source.model().item(i).isEnabled()]
        elif key in ("automatic_contact_parameters", "automatic_cores"):
            self.choices = [("Automatic", None), ("True", True), ("False", False)]
        elif key == "imported_geometry_role":
            self.choices = [("Compression", "compression"), ("General geometry", "general")]
        elif type(value) is bool:
            self.choices = [("True", True), ("False", False)]
        self.numeric = not self.choices and type(value) in (int, float)
        self.optional_number = value is None and key in ("minimum_substeps", "maximum_substeps", "stl_resolution")
        self.sampling = sampling
        self.can_vary = not sampling or self.numeric or self.optional_number
        if self.can_vary:
            table.setCellWidget(row, 0, self.variable)
        else:
            self.variable.setParent(table)
            self.variable.setEnabled(False)
            self.variable.hide()
        self.levels_seeded = False
        if self.choices:
            self.fixed = CompactComboBox()
            self.selection = QPushButton("Select values")
            self.selection.setFixedHeight(CONTROL_HEIGHT)
            self.menu = QMenu(self.selection)
            self.selection.setMenu(self.menu)
            for title, data in self.choices:
                self.fixed.addItem(title, data)
                action = self.menu.addAction(title)
                action.setCheckable(True)
                action.setChecked(data == value)
                action.setData(data)
                action.toggled.connect(changed)
            self.fixed.setCurrentIndex(self.fixed.findData(value))
            self.fixed.currentIndexChanged.connect(changed)
        elif self.numeric or self.optional_number:
            initial = value if self.numeric else (0.1 if key == "stl_resolution" else 1)
            self.first = number(initial, source)
            self.last = number(initial, source)
            self.mode = CompactComboBox()
            self.mode.addItem("Min / max", "bounds")
            self.mode.addItem("Value list", "list")
            self.selection = text_field()
            self.selection.setPlaceholderText("16, 24, 40" if type(initial) is int else "0.5, 1, 2.5")
            self.selection.setToolTip("Separate values with commas. For decimal commas, use a semicolon between values.")
            self.selection.setAccessibleName(self.label + " " + translate("Value list"))
            self.selection.textChanged.connect(changed)
            if self.numeric:
                self.fixed = number(initial, source)
                self.fixed.valueChanged.connect(changed)
            else:
                self.fixed = text_field("null")
                self.fixed.textChanged.connect(changed)
            self.bounds = QWidget()
            bounds = QHBoxLayout(self.bounds)
            bounds.setContentsMargins(0, 0, 0, 0)
            bounds.setSpacing(CONTROL_GAP)
            for box, label in ((self.first, "Lower bound"), (self.last, "Upper bound")):
                box.setAccessibleName(f"{translate(self.label)} · {translate(label)}")
                box.setToolTip(translate(label))
                bounds.addWidget(box)
                box.valueChanged.connect(changed)
            bounds.addStretch()
            self.level_entry = QStackedWidget()
            self.level_entry.addWidget(self.bounds)
            self.level_entry.addWidget(self.selection)
            self.mode.currentIndexChanged.connect(self.update_level_entry)
            self.mode.currentIndexChanged.connect(changed)
        else:
            if self.material_card:
                # Keep the existing JSON level editor and stored sweeps for material cards.
                self.fixed = CompactComboBox()
                self.fixed.addItem("Custom material", None)
                kind, required = material_requirements(path, op)
                for entry in builtin_materials():
                    if entry.kind == kind and all(prop in entry.properties for prop in required):
                        self.fixed.addItem(entry.name, entry.card().to_dict())
                self.select_material(value)
                self.fixed.setToolTip(translate(self.fixed.currentText()))
                self.fixed.currentTextChanged.connect(lambda title: self.fixed.setToolTip(translate(title)))
                self.fixed.currentIndexChanged.connect(changed)
            else:
                self.fixed = text_field(display(value))
                self.fixed.textChanged.connect(changed)
            self.selection = text_field()
            self.selection.setPlaceholderText('["value 1", "value 2"]')
            self.selection.setToolTip("Enter the levels as a JSON list.")
            self.selection.textChanged.connect(changed)
        if self.numeric or self.optional_number:
            levels = QWidget()
            controls = QVBoxLayout(levels)
            controls.setContentsMargins(0, 0, 0, 0)
            controls.setSpacing(1)
            if sampling:
                self.mode.setParent(levels)
                self.mode.hide()
            else:
                controls.addWidget(self.mode)
            controls.addWidget(self.level_entry)
            variations.setCellWidget(variation_row, 1, levels)
            variations.setRowHeight(variation_row, CONTROL_HEIGHT + 2 if sampling else 2 * CONTROL_HEIGHT + 2)
        else:
            variations.setCellWidget(variation_row, 1, self.selection)
        self.fixed_panel = self.fixed
        if key == "structure_name":
            self.add_structure_family(value)
            table.setRowHeight(row, 2 * CONTROL_HEIGHT + 2)
        table.setCellWidget(row, 2, self.fixed_panel)
        self.toggle(False)
        self.variable.toggled.connect(self.toggle)
        self.variable.toggled.connect(changed)
        for editor in (self.fixed, self.selection):
            editor.setAccessibleName(self.label + " " + editor.accessibleName())
            if isinstance(editor, QPushButton):
                editor.setAutoDefault(False)

    def toggle(self, enabled):
        self.variations.setRowHidden(self.variation_row, not enabled)
        self.fixed_panel.setEnabled(not enabled)
        if enabled and not self.levels_seeded:
            if self.choices:
                for action in self.menu.actions():
                    action.setChecked(action.data() == self.fixed.currentData())
            elif self.numeric:
                self.first.setValue(self.fixed.value())
                self.last.setValue(self.fixed.value())
            self.levels_seeded = True

    def update_level_entry(self):
        self.level_entry.setCurrentWidget(self.selection if self.mode.currentData() == "list" else self.bounds)

    def add_structure_family(self, value):
        names = {name for _, name in self.choices}
        self.family = CompactComboBox()
        self.family.setAccessibleName("Structure family")
        self.fixed.setAccessibleName("Structure type")
        for family, members in ALL_STRUCTURES_BY_FAMILY.items():
            if names.intersection(members):
                self.family.addItem(STRUCTURE_FAMILIES[family], family)
                if value in members:
                    self.family.setCurrentIndex(self.family.count() - 1)
        self.fixed_panel = QWidget()
        controls = QVBoxLayout(self.fixed_panel)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(1)
        controls.addWidget(self.family)
        controls.addWidget(self.fixed)
        self.family.currentIndexChanged.connect(self.update_structure_choices)
        self.update_structure_choices()

    def update_structure_choices(self):
        previous = self.fixed.currentData()
        choices = ALL_STRUCTURES_BY_FAMILY[self.family.currentData()]
        self.fixed.clear()
        for label, name in self.choices:
            if name in choices:
                self.fixed.addItem(label.split(" / ", 1)[-1], name)
        self.fixed.setCurrentIndex(max(0, self.fixed.findData(previous)))

    def draft(self):
        state = {"variable": self.variable.isChecked(), "seeded": self.levels_seeded}
        if self.choices:
            state.update(fixed=self.fixed.currentData(),
                         choices=[action.data() for action in self.menu.actions() if action.isChecked()])
        elif self.material_card:
            state.update(fixed=self.fixed.currentData(), levels=self.selection.text())
        else:
            state.update(fixed=self.fixed.value() if self.numeric else self.fixed.text(),
                         levels=self.selection.text())
            if self.numeric or self.optional_number:
                state.update(mode=self.mode.currentData(), lower=self.first.value(), upper=self.last.value())
        return state

    def restore_draft(self, state):
        if not isinstance(state, dict):
            return
        fixed = state.get("fixed")
        if self.choices:
            choices = state.get("choices")
            legacy_levels = state.get("levels")
            if isinstance(legacy_levels, str):
                if fixed == "null" and self.value is None:
                    fixed = None
                try:
                    choices = json.loads(legacy_levels)
                except ValueError:
                    pass
            if self.path[-1] == "structure_name":
                for family, members in ALL_STRUCTURES_BY_FAMILY.items():
                    if fixed in members and self.family.findData(family) >= 0:
                        self.family.setCurrentIndex(self.family.findData(family))
                        break
            index = self.fixed.findData(fixed)
            if index >= 0:
                self.fixed.setCurrentIndex(index)
            if isinstance(choices, list):
                for action in self.menu.actions():
                    action.setChecked(action.data() in choices)
        else:
            if self.material_card and "fixed" in state:
                if isinstance(fixed, str):
                    try:
                        fixed = json.loads(fixed)
                    except ValueError:
                        fixed = self.value
                if fixed is None or isinstance(fixed, dict):
                    self.select_material(fixed)
            elif self.numeric:
                self.restore_number(self.fixed, fixed)
            elif isinstance(fixed, str):
                self.fixed.setText(fixed)
            elif self.optional_number and type(fixed) in (int, float):
                self.fixed.setText(display(fixed))
            levels = state.get("levels")
            if isinstance(levels, str):
                self.selection.setText(levels)
            if self.numeric or self.optional_number:
                self.restore_number(self.first, state.get("lower"))
                self.restore_number(self.last, state.get("upper"))
                index = self.mode.findData(state.get("mode"))
                if not self.sampling and index >= 0:
                    self.mode.setCurrentIndex(index)
        self.levels_seeded = state.get("seeded") is True or state.get("variable") is True
        self.variable.setChecked(self.can_vary and state.get("variable") is True)

    def select_material(self, data):
        self.selected_material = data
        for index in range(self.fixed.count()):
            if self.fixed.itemData(index) == data:
                self.fixed.setCurrentIndex(index)
                return
        self.fixed.addItem(data.get("name") or "Material card", data)
        self.fixed.setCurrentIndex(self.fixed.count() - 1)

    @staticmethod
    def restore_number(control, value):
        if type(value) in (int, float) and control.minimum() <= value <= control.maximum() and isfinite(value):
            control.setValue(value)

    def levels_for_matrix(self):
        varying = self.variable.isChecked()
        if self.choices:
            values = ([a.data() for a in self.menu.actions() if a.isChecked()]
                      if varying else [self.fixed.currentData()])
        elif (self.numeric or self.optional_number) and varying:
            if not self.sampling and self.mode.currentData() == "list":
                values = explicit_numbers(self.selection.text(), type(self.first.value()),
                                          self.first.minimum(), self.first.maximum())
            else:
                if self.last.value() <= self.first.value():
                    raise ValueError("The upper bound must exceed the lower bound.")
                values = [self.first.value(), self.last.value()]
        elif self.numeric:
            values = [self.fixed.value()]
        elif varying:
            values = json.loads(self.selection.text())
        elif self.material_card:
            values = [self.fixed.currentData()]
        else:
            text = self.fixed.text()
            values = [text if isinstance(self.value, str) else json.loads(text)]
        if not isinstance(values, list) or not values or (varying and len(values) < 2):
            raise ValueError("Choose at least two distinct levels for a variable.")
        if varying and len(set(map(display, values))) != len(values):
            raise ValueError("Each level must be distinct.")
        if isinstance(self.value, str) and not all(isinstance(v, str) for v in values):
            raise ValueError("Enter text values.")
        return values


class ExperimentMatrix(QAbstractTableModel):
    """Display every frozen input without allocating a Qt item per cell."""

    def __init__(self, parent):
        super().__init__(parent)
        self.reset([], [])

    def reset(self, rows, factors):
        self.beginResetModel()
        self.rows = rows
        self.columns = sorted(factors, key=lambda f: not f.variable.isChecked())
        self.states = ["Ready"] * len(rows)
        self.details = {}
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 1 + len(self.columns)

    def headerData(self, section, orientation, role):
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Vertical:
                return experiment_name(section + 1)
            return translate(self.columns[section - 1].label if section else "Status")

    def data(self, index, role):
        if not index.isValid() or role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        row, column = index.row(), index.column()
        if column == 0:
            if role == Qt.ItemDataRole.ToolTipRole and self.details.get(row):
                return self.details[row]
            return translate(self.states[row])
        path = self.columns[column - 1].path
        return display(parameter_value(self.rows[row], path))

    def set_state(self, row, state, detail=""):
        self.states[row] = state
        self.details[row] = detail
        index = self.index(row, 0)
        self.dataChanged.emit(index, index)


class DoeTrubaJobsWindow(QMainWindow):
    """Slurm jobs confirmed for the latest TRUBA run from this DoE page."""

    def __init__(self, parent):
        super().__init__(parent, Qt.WindowType.Window)
        self.setObjectName("doeTrubaJobsWindow")
        self.setWindowTitle("DoE · TRUBA jobs")
        self.resize(600, 320)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(7)
        self.table = QTableWidget(0, 3)
        self.table.setAccessibleName("TRUBA jobs")
        self.table.setHorizontalHeaderLabels(["Experiments", "Slurm ID", "Status"])
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(CONTROL_HEIGHT)
        self.table.setColumnWidth(0, 120)
        self.table.setColumnWidth(1, 105)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        self.notice = QLabel()
        self.notice.setObjectName("mutedHintLabel")
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.results_button = QPushButton("Open result folder")
        self.results_button.setStyleSheet("padding: 0 8px;")
        self.results_button.setAutoDefault(False)
        self.results_button.setEnabled(False)
        self.results_button.clicked.connect(self.open_selected_results)
        layout.addWidget(self.results_button, alignment=Qt.AlignmentFlag.AlignRight)
        self.table.itemSelectionChanged.connect(self.update_results_button)
        self.setCentralWidget(content)
        self.job_rows = {}

    def selected_results_directory(self):
        rows = self.table.selectionModel().selectedRows(0)
        if rows:
            directory = rows[0].data(Qt.ItemDataRole.UserRole)
            if directory and Path(directory).is_dir():
                return directory
        return None

    def update_results_button(self):
        self.results_button.setEnabled(self.selected_results_directory() is not None)

    def open_selected_results(self):
        directory = self.selected_results_directory()
        if directory is None:
            self.results_button.setEnabled(False)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(directory))

    def begin(self):
        self.job_rows.clear()
        self.table.setRowCount(0)
        self.results_button.setEnabled(False)
        self.notice.setText("Submitting analysis to TRUBA…")
        show_restored_window(self)

    def update_job(self, experiment, record):
        job_id = record.get("job_id")
        if not job_id:
            return
        from truba_job_list import background_status

        row = self.job_rows.get(experiment)
        if row is None:
            row = self.table.rowCount()
            self.job_rows[experiment] = row
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(experiment))
            self.table.setItem(row, 1, QTableWidgetItem())
            self.table.setItem(row, 2, QTableWidgetItem())
        self.table.item(row, 1).setText(str(job_id))
        state = record.get("state", "")
        local_directory = record.get("local_directory")
        results = str(Path(local_directory) / "output") if state == "downloaded" and local_directory else None
        self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, results)
        self.update_results_button()
        stage = state.partition(" · Running experiment ")[0]
        status = "Cancelled" if stage.lower() == "cancelled" else background_status({"state": stage})
        self.table.item(row, 2).setText(status)
        self.table.item(row, 2).setToolTip(state)
        self.notice.clear()


class DoeRunDialog(QDialog):
    def __init__(self, parent, target, batch_size, parallel_experiments, *, run):
        super().__init__(parent)
        self.setObjectName("doeRunSettings")
        self.setWindowTitle("Run settings")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(7)
        form = QFormLayout()
        form.setVerticalSpacing(3)
        layout.addLayout(form)
        self.target = CompactComboBox()
        self.target.addItem("Local", "local")
        self.target.addItem("TRUBA", "truba")
        self.target.addItem("TRUBA (one job)", "truba_matrix")
        self.target.setCurrentIndex(max(0, self.target.findData(target)))
        target_label = QLabel("Run location")
        target_label.setBuddy(self.target)
        self.target.setAccessibleName(target_label.text())
        form.addRow(target_label, self.target)

        self.batch_size = CompactSpinBox()
        self.batch_size.setFixedSize(NUMBER_WIDTH, CONTROL_HEIGHT)
        self.batch_size.setRange(1, MAX_ROWS)
        self.batch_size.setValue(batch_size)
        batch_label = QLabel("Experiments per script")
        batch_label.setBuddy(self.batch_size)
        self.batch_size.setAccessibleName(batch_label.text())
        form.addRow(batch_label, self.batch_size)

        self.parallel_experiments = CompactSpinBox()
        self.parallel_experiments.setFixedSize(NUMBER_WIDTH, CONTROL_HEIGHT)
        self.parallel_experiments.setRange(1, 224)
        self.parallel_experiments.setValue(parallel_experiments)
        parallel_label = QLabel("Concurrent experiments")
        parallel_label.setBuddy(self.parallel_experiments)
        self.parallel_experiments.setAccessibleName(parallel_label.text())
        self.parallel_experiments.setToolTip(translate("Experiments share the CPU cores and RAM of one Slurm job."))
        form.addRow(parallel_label, self.parallel_experiments)

        def update_controls():
            target = self.target.currentData()
            self.batch_size.setEnabled(target == "truba")
            batch_label.setEnabled(self.batch_size.isEnabled())
            self.parallel_experiments.setEnabled(target == "truba_matrix")
            parallel_label.setEnabled(self.parallel_experiments.isEnabled())

        self.target.currentIndexChanged.connect(update_controls)
        update_controls()
        layout.addStretch()
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("Cancel")
        self.confirm = QPushButton("Run" if run else "Save")
        self.confirm.setProperty("primary", True)
        width = max(button.fontMetrics().horizontalAdvance(label)
                    for button in (cancel, self.confirm)
                    for label in (button.text(), turkish(button.text()))) + 24
        for button in (cancel, self.confirm):
            button.setAutoDefault(False)
            button.setProperty("managesLocalizedWidth", True)
            button.setFixedSize(width, CONTROL_HEIGHT)
            actions.addWidget(button)
        cancel.clicked.connect(self.reject)
        self.confirm.clicked.connect(self.accept)
        layout.addLayout(actions)


class NewDoePage(QWidget):
    def __init__(self, host, preferences=None, *, sampling=False):
        super().__init__()
        self.host = host
        self.preferences = preferences
        self.sampling = sampling
        self.preference_prefix = "deepLearning" if sampling else "doe"
        self._loaded_operation = None
        self._drafts = {}
        self._run_options = {}
        self._parallel_experiments = {}
        self._draft_timer = QTimer(self, interval=300, singleShot=True)
        self._draft_timer.timeout.connect(self.save_draft)
        self.rows = []
        self.matrix_current = False
        self.factors = []
        self.job = self.directory = None
        self.truba_jobs_window = None
        self.submitted_rows = {}
        self.pending_checks = deque()
        self.separate_remote = False
        self.loaded = False
        self.timer = QTimer(self, interval=200)
        self.timer.timeout.connect(self.advance)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 1, 2, 1)
        layout.setSpacing(2)
        self.operation = CompactComboBox()
        for title, op in OPERATIONS.items():
            self.operation.addItem(title, op)
        operation_label = QLabel("Analysis Type")
        operation_label.setBuddy(self.operation)
        self.operation.setAccessibleName(operation_label.text())
        self.operation.setCurrentIndex(self.operation.findData("fft_solve"))
        self.inputs = QScrollArea()
        self.inputs.setWidgetResizable(True)
        self.analysis_settings = QWidget()
        analysis_layout = QVBoxLayout(self.analysis_settings)
        analysis_layout.setContentsMargins(0, 0, 0, 0)
        analysis_layout.setSpacing(0)
        operation_row = QHBoxLayout()
        operation_row.setContentsMargins(2, 2, 2, 0)
        operation_row.setSpacing(6)
        operation_row.addWidget(operation_label)
        operation_row.addWidget(self.operation)
        operation_row.addStretch()
        analysis_layout.addLayout(operation_row)
        analysis_layout.addWidget(self.inputs, 1)
        self.input_tabs = QTabWidget()
        self.input_tabs.setMinimumWidth(320)
        self.input_tabs.addTab(self.analysis_settings, "Analysis settings")
        self.variations = self.table(["Parameter", "Min / max" if sampling else "Values"])
        self.variations.setAccessibleName("Variable settings")
        self.variations.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.input_tabs.addTab(self.variations, "Variable settings")
        self.input_navigation = DoENavigation(self.input_tabs)
        self.sections = {}
        self.run = QPushButton("Run")
        self.run.clicked.connect(lambda: self.open_run_options(run=True))
        self.stop = QPushButton("Stop")
        self.stop.clicked.connect(self.cancel)
        self.open = QPushButton("Results")
        self.open.setToolTip("Results folder")
        self.open.clicked.connect(self.open_results)
        self.commands = (self.run, self.stop, self.open)
        for button in self.commands:
            button.setAutoDefault(False)
            button.setProperty("managesLocalizedWidth", True)
            button.setFixedHeight(CONTROL_HEIGHT)
        gear = QIcon(str(Path(__file__).resolve().parent / "assets" / "ui" / "run-settings.svg"))
        self.run_settings = QPushButton(gear, "")
        self.run_settings.setToolTip("Run settings")
        self.run_settings.setAccessibleName("Run settings")
        self.run_settings.setAutoDefault(False)
        self.run_settings.setProperty("managesLocalizedWidth", True)
        self.run_settings.setFixedSize(CONTROL_HEIGHT, CONTROL_HEIGHT)
        self.run_settings.setStyleSheet("QPushButton { padding: 0; }")
        self.run_settings.clicked.connect(lambda: self.open_run_options())

        settings_panel = QWidget()
        settings_layout = QVBoxLayout(settings_panel)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(2)
        settings_layout.addWidget(self.input_navigation)
        settings_layout.addWidget(self.input_tabs, 1)
        if sampling:
            self.lhs_count = NumericLineEdit(100, 2, 5000)
            self.lhs_seed = NumericLineEdit(42, 1, 999999)
            sampling_group = SidebarGroupBox("Deep Learning parameters")
            form = QFormLayout(sampling_group.content)
            form.setContentsMargins(4, 2, 4, 4)
            form.setSpacing(CONTROL_GAP)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
            form.addRow("LHS sample count", self.lhs_count)
            form.addRow("Seed", self.lhs_seed)
            settings_layout.addWidget(sampling_group)
            for control in (self.lhs_count, self.lhs_seed):
                control.valueChanged.connect(self.inputs_changed)
        self.apply_label = "Generate ML matrix" if sampling else "Apply"
        self.apply_button = QPushButton(self.apply_label)
        self.apply_button.setProperty("primary", True)
        self.apply_button.setFixedHeight(CONTROL_HEIGHT)
        self.apply_button.setAutoDefault(False)
        self.apply_button.setProperty("managesLocalizedWidth", True)
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.create)
        settings_layout.addWidget(self.apply_button, alignment=Qt.AlignmentFlag.AlignRight)
        matrix_panel = QWidget()
        matrix_layout = QVBoxLayout(matrix_panel)
        matrix_layout.setContentsMargins(0, 0, 0, 0)
        matrix_layout.setSpacing(0)
        matrix_title = QLabel("Experiment matrix")
        matrix_title.setObjectName("sectionSubheader")
        matrix_title.setFixedHeight(CONTROL_HEIGHT)
        matrix_title.setContentsMargins(4, 0, 0, 0)
        if sampling:
            self.export_csv = QPushButton("Export CSV")
            self.export_csv.setFixedHeight(CONTROL_HEIGHT)
            self.export_csv.setAutoDefault(False)
            self.export_csv.setEnabled(False)
            self.export_csv.clicked.connect(self.export_learning_results)
            matrix_heading = QHBoxLayout()
            matrix_heading.addWidget(matrix_title, 1)
            matrix_heading.addWidget(self.export_csv)
            matrix_layout.addLayout(matrix_heading)
        else:
            matrix_layout.addWidget(matrix_title)
        self.preview = QTableView()
        self.preview.setAccessibleName("Experiment matrix")
        self.preview.setMinimumWidth(220)
        self.preview.setAlternatingRowColors(True)
        self.sheet = ExperimentMatrix(self.preview)
        self.preview.setModel(self.sheet)
        self.preview.verticalHeader().setDefaultSectionSize(26)
        matrix_layout.addWidget(self.preview, 1)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("learningOperationSettingsAndMatrix" if sampling else "newDoeSettingsAndMatrix")
        self.splitter.setHandleWidth(7)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(settings_panel)
        self.splitter.addWidget(matrix_panel)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([350, 600])
        layout.addWidget(self.splitter, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.truba_jobs_button = QPushButton("TRUBA jobs")
        self.truba_jobs_button.setAutoDefault(False)
        self.truba_jobs_button.hide()
        self.truba_jobs_button.clicked.connect(self.open_truba_jobs)
        command_row = QHBoxLayout()
        command_row.setSpacing(4)
        command_row.addWidget(self.status, 1)
        command_row.addWidget(self.truba_jobs_button)
        command_row.addWidget(self.run_settings)
        for button in self.commands:
            command_row.addWidget(button)
        matrix_layout.addLayout(command_row)
        self.run.setEnabled(False)
        self.stop.setEnabled(False)
        self.open.setEnabled(False)
        if host is None:
            self.operation.setEnabled(False)
            self.run_settings.setEnabled(False)
        if self.preferences is not None:
            last_operation = self.preferences.value(f"{self.preference_prefix}/lastOperation", "", type=str)
            index = self.operation.findData(last_operation)
            if index >= 0:
                self.operation.setCurrentIndex(index)
        self.operation.currentIndexChanged.connect(self.load)

    def run_options(self, operation):
        target = "truba" if self.host is not None and self.host.remote_controller.requested else "local"
        if self.preferences is None:
            return self._run_options.get(operation, (target, 2))
        key = f"{self.preference_prefix}/runOptions/{operation}"
        paired = self.preferences.value(f"{self.preference_prefix}/pairTrubaExperiments/{operation}", True, type=bool)
        return (self.preferences.value(f"{key}/target", target, type=str),
                self.preferences.value(f"{key}/batchSize", 2 if paired else 1, type=int))

    def open_run_options(self, *, run=False):
        operation = self.operation.currentData()
        key = f"{self.preference_prefix}/runOptions/{operation}"
        parallel = (self._parallel_experiments.get(operation, 1) if self.preferences is None else
                    self.preferences.value(f"{key}/parallelExperiments", 1, type=int))
        dialog = DoeRunDialog(self, *self.run_options(operation), parallel, run=run)

        def confirm():
            target, size = dialog.target.currentData(), dialog.batch_size.value()
            parallel = dialog.parallel_experiments.value()
            if self.preferences is None:
                self._run_options[operation] = (target, size)
                self._parallel_experiments[operation] = parallel
            else:
                self.preferences.setValue(f"{key}/target", target)
                self.preferences.setValue(f"{key}/batchSize", size)
                self.preferences.setValue(f"{key}/parallelExperiments", parallel)
            if run:
                self.start(remote=target != "local", separate=target != "truba_matrix", batch_size=size,
                           parallel_experiments=parallel)

        dialog.accepted.connect(confirm)
        show_restored_window(dialog)
        return dialog

    @staticmethod
    def table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().hide()
        table.verticalHeader().setMinimumSectionSize(25)
        table.verticalHeader().setDefaultSectionSize(26)
        return table

    def showEvent(self, event):
        super().showEvent(event)
        self.size_operation()
        command_width = max(button.fontMetrics().horizontalAdvance(label)
                            + (24 if button.menu() else 16)
                            for button in self.commands
                            for label in (button.text(), turkish(button.text())))
        for button in self.commands:
            button.setFixedWidth(command_width)
        apply_width = max(self.apply_button.fontMetrics().horizontalAdvance(text)
                          for text in (self.apply_label, turkish(self.apply_label))) + 16
        self.apply_button.setFixedWidth(apply_width)
        if self.host is not None and not self.loaded:
            self.load()

    def size_operation(self):
        metrics = self.operation.fontMetrics()
        width = max(metrics.horizontalAdvance(label)
                    for title in OPERATIONS
                    for label in (title, turkish(title))) + 34
        self.operation.setFixedWidth(width)

    def invalidate(self, *_):
        self.matrix_current = False
        self.run.setEnabled(False)
        self.status.clear()

    def inputs_changed(self, *_):
        if self.loaded:
            self.invalidate()
            self._draft_timer.start()

    def save_draft(self, *, commit=False):
        if not self.loaded:
            return
        if commit:
            for field in self.findChildren(NumericLineEdit):
                field.interpretText()
        self._draft_timer.stop()
        op = self._loaded_operation
        draft = {"/".join(factor.path): factor.draft() for factor in self.factors}
        if self.sampling:
            draft["sampling"] = {"count": self.lhs_count.value(), "seed": self.lhs_seed.value()}
        if self._drafts.get(op) != draft:
            self._drafts[op] = draft
            if self.preferences is not None:
                self.preferences.setValue(f"{self.preference_prefix}/operationDrafts/{op}", json.dumps(draft, ensure_ascii=False))
        if self.preferences is not None:
            self.preferences.sync()

    def load(self, *_):
        if self.host is None:
            return
        self.save_draft(commit=True)
        self.loaded = False
        self.rows = []
        self.sheet.reset([], [])
        if self.sampling:
            self.export_csv.setEnabled(False)
        self.invalidate()
        expanded = {title: not section.content.isHidden() for title, section in self.sections.items()}
        self.sections.clear()
        self.factors.clear()
        self.input_tabs.setCurrentWidget(self.analysis_settings)
        self.variations.setRowCount(0)
        panel = QWidget()
        stack = QVBoxLayout(panel)
        stack.setContentsMargins(2, 2, 2, 2)
        stack.setSpacing(2)
        stack.setAlignment(Qt.AlignmentFlag.AlignTop)
        stack.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.inputs.setWidget(panel)
        try:
            op = self.operation.currentData()
            draft = self._drafts.get(op)
            if draft is None and self.preferences is not None:
                try:
                    key = f"{self.preference_prefix}/operationDrafts/{op}"
                    draft = json.loads(self.preferences.value(key, "{}", type=str))
                except ValueError:
                    draft = None
            if not isinstance(draft, dict):
                draft = {}
            self._drafts[op] = draft
            if self.sampling:
                options = draft.get("sampling", {})
                self.lhs_count.setValue(100)
                self.lhs_seed.setValue(42)
                if isinstance(options, dict):
                    for control, name in ((self.lhs_count, "count"), (self.lhs_seed, "seed")):
                        if type(options.get(name)) is int:
                            Factor.restore_number(control, options[name])
            self.base = snapshot(self.host, op)
            sources = field_sources(self.host, op)
            groups = {title: [] for title in ("Standard test", "Structure", "Mesh")}
            fields = list(parameters(self.base))
            variation_rows = {path: index for index, (path, _) in enumerate(fields)}
            self.variations.setRowCount(len(fields))
            for path, value in fields:
                groups.setdefault(parameter_group(path, op), []).append((path, value))
            factors = {}
            for title, members in groups.items():
                if not members:
                    continue
                section = SidebarGroupBox(title)
                self.sections[title] = section
                stack.addWidget(section)
                body = QVBoxLayout(section.content)
                body.setContentsMargins(2, 0, 2, 1)
                if title == "Solid phase material":
                    actions = QHBoxLayout()
                    width = max(self.fontMetrics().horizontalAdvance(text) for label in ("Library", "Load")
                                for text in (label, turkish(label))) + 26
                    for label, callback in (("Library", self.open_fft_material_library),
                                            ("Load", self.load_fft_material)):
                        button = QPushButton(label)
                        button.setAutoDefault(False)
                        button.setProperty("managesLocalizedWidth", True)
                        button.setFixedWidth(width)
                        button.clicked.connect(callback)
                        actions.addWidget(button)
                    actions.addStretch()
                    body.addLayout(actions)
                table = self.table(["", "Parameter", "Fixed value"])
                table.horizontalHeaderItem(0).setToolTip(translate("Variable"))
                header = table.horizontalHeader()
                header.setMinimumSectionSize(0)
                header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
                header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
                header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
                table.setColumnWidth(0, 24)
                table.setColumnWidth(2, COMBO_WIDTH + 6)
                body.addWidget(table)
                table.setRowCount(len(members))
                for row, (path, value) in enumerate(members):
                    factor = Factor(table, row, self.variations, variation_rows[path], path, value,
                                    sources.get(path[-1]), op, self.inputs_changed, sampling=self.sampling)
                    factor.restore_draft(draft.get("/".join(path)))
                    factors[path] = factor
                table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                frame = 2 * table.frameWidth()
                table.setFixedHeight(table.horizontalHeader().sizeHint().height() + table.verticalHeader().length() + frame)
                preview_group = "Mesh" if title == FFT_GROUP else title
                if preview_group in PREVIEW_BUTTONS:
                    actions = QHBoxLayout()
                    actions.setSpacing(4)
                    actions.addStretch()
                    preview = QPushButton(PREVIEW_BUTTONS[preview_group])
                    preview.setObjectName("structurePreviewBtn" if preview_group == "Structure" else "meshPreviewBtn")
                    preview.setAutoDefault(False)
                    preview.setProperty("managesLocalizedWidth", True)
                    width = max(preview.fontMetrics().horizontalAdvance(text)
                                for label in PREVIEW_BUTTONS.values()
                                for text in (label, turkish(label))) + 24
                    preview.setFixedSize(width, CONTROL_HEIGHT)
                    preview.clicked.connect(
                        lambda _checked=False, mesh=preview_group == "Mesh": self.open_input_preview(mesh))
                    actions.addWidget(preview)
                    body.addLayout(actions)
                if title == "Structure" and not self.sampling:
                    button = QPushButton("Apply")
                    button.setObjectName("structureApplyBtn")
                    button.setAutoDefault(False)
                    button.setFixedSize(max(button.fontMetrics().horizontalAdvance(text)
                                            for text in ("Apply", turkish("Apply"))) + 24, CONTROL_HEIGHT)
                    button.clicked.connect(self.create)
                    actions.addWidget(button)
                section.set_expanded(expanded.get(title, op == "fft_solve" or title == "Structure"))
            # Keep matrix combinations and CSV columns in the operation's parameter order.
            self.factors = [factors[path] for path, _ in fields]
            for factor in self.factors:
                if factor.material_card:
                    factor.fixed.activated.connect(
                        lambda index, factor=factor: self.apply_selected_material(factor.fixed.itemData(index),
                                                                                 factor.path))
            values_width = 2 * NUMBER_WIDTH + CONTROL_GAP
            for button in self.variations.findChildren(QPushButton):
                button.ensurePolished()
                width = max(button.fontMetrics().horizontalAdvance(label)
                            for label in ("Select values", turkish("Select values"))) + 28
                button.setProperty("managesLocalizedWidth", True)
                button.setFixedWidth(width)
                values_width = max(values_width, width)
            self.variations.setColumnWidth(1, values_width + 4)
            self.loaded = True
            self._loaded_operation = op
            if self.preferences is not None:
                self.preferences.setValue(f"{self.preference_prefix}/lastOperation", op)
        except Exception as error:
            self.status.setText(str(error))
            self.loaded = False
        self.apply_button.setEnabled(self.loaded)

    def open_input_preview(self, mesh):
        from doe_preview import DoeInputPreview

        if not self.loaded:
            return
        op = self.base["operation"]
        dimensions = {}
        varying = False
        try:
            for factor in self.factors:
                group, key = factor.path[0], factor.path[-1]
                section = parameter_group(factor.path, op)
                if group == "settings":
                    relevant = section in ("Standard test", "Structure") or (mesh and section == "Mesh")
                else:
                    relevant = mesh and (section == "Mesh" or (op == "fft_solve" and factor.path == ("grid",)) or
                                         (group == "flow" and section == "Channel · Isothermal CFD"
                                          and key not in FLOW_SOLVER_FIELDS))
                if not relevant:
                    continue
                if isinstance(factor.fixed, NumericLineEdit):
                    factor.fixed.interpretText()
                if factor.variable.isChecked() and (factor.numeric or factor.optional_number):
                    factor.first.interpretText()
                    factor.last.interpretText()
                try:
                    dimensions[factor.path] = factor.levels_for_matrix()[:1]
                except (ValueError, TypeError) as error:
                    raise ValueError(f"{factor.label}: {error}") from error
                varying |= factor.variable.isChecked()
            request = next(matrix(self.base, dimensions))
        except (TypeError, ValueError) as error:
            self.status.setText(str(error))
            return
        for previous in self.findChildren(DoeInputPreview, options=Qt.FindChildOption.FindDirectChildrenOnly):
            if previous.objectName() == ("mesh" if mesh else "structure"):
                previous.close()
        dialog = DoeInputPreview(self, request, mesh=mesh, varying=varying)
        show_restored_window(dialog)
        return dialog

    def open_fft_material_library(self):
        from material_library_dialog import MaterialLibraryDialog
        from window_state import show_restored_window

        if not hasattr(self, "fft_material_library"):
            self.fft_material_library = MaterialLibraryDialog(
                self, required=("young_modulus", "poisson_ratio"))
            self.fft_material_library.material_added.connect(self.apply_fft_material)
        show_restored_window(self.fft_material_library)

    def load_fft_material(self):
        from file_dialog import QFileDialog
        from material_library import read_material_card

        path, _ = QFileDialog.getOpenFileName(
            self, "Load material", str(Path(__file__).resolve().parent / "materials"),
            "Material card (*.json *.csv)")
        if path:
            try:
                self.apply_fft_material(read_material_card(path))
            except (OSError, TypeError, ValueError) as error:
                self.status.setText(str(error))

    def apply_fft_material(self, card):
        if self.operation.currentData() != "fft_solve" or self.timer.isActive():
            return
        self.apply_selected_material(card.to_dict(), ("settings", "material_card"))

    def apply_selected_material(self, data, path):
        fields = {factor.path: factor for factor in self.factors}
        factor = fields[path]
        if data is None:
            factor.select_material(data)
            return
        op = self.operation.currentData()
        try:
            card = MaterialCard.from_dict(data)
            kind, required = material_requirements(path, op)
            if card.kind != kind:
                raise ValueError(f"Expected a {kind} material card.")
            if path[0] == "settings":
                if op == "fft_solve":
                    properties = card.evaluate(293.15, required)
                    values = dict(material_name=card.name, young_modulus=properties["young_modulus"] / 1e6,
                                  poisson_ratio=properties["poisson_ratio"])
                else:
                    values = card.mechanical_values()
                    del values["material_card"]
                assignments = {("settings", key): value for key, value in values.items()}
            else:
                temperature = 293.15
                if path[0] == "thermal":
                    properties = THERMAL_SOLID_PROPERTIES
                    temperature = fields[("thermal", "initial_solid_c")].fixed.value() + 273.15
                else:
                    properties = FLOW_MATERIAL_PROPERTIES
                    if op == "thermal_solve":
                        properties = {**properties, **THERMAL_FLUID_PROPERTIES}
                        temperature = fields[("thermal", "inlet_temperature_c")].fixed.value() + 273.15
                values = card.evaluate(temperature, required)
                assignments = {field: values[key] for field, key in properties.items()}
            for field, value in assignments.items():
                editor = fields[field].fixed
                if isinstance(editor, NumericLineEdit) and not editor.minimum() <= value <= editor.maximum():
                    raise ValueError(f"{fields[field].label}: material value is outside the supported range.")
        except (TypeError, ValueError) as error:
            factor.select_material(factor.selected_material)
            self.status.setText(str(error))
            return
        factor.select_material(data)
        for field, value in assignments.items():
            editor = fields[field].fixed
            if isinstance(editor, QComboBox):
                editor.setCurrentIndex(editor.findData(value))
            elif isinstance(editor, NumericLineEdit):
                editor.setValue(value)
            else:
                editor.setText(display(value))

    def create(self):
        if not self.loaded or self.timer.isActive():
            return
        for field in self.input_tabs.findChildren(NumericLineEdit):
            field.interpretText()
        if self.sampling:
            self.lhs_count.interpretText()
            self.lhs_seed.interpretText()
        self.invalidate()
        dimensions = {}
        try:
            for factor in self.factors:
                try:
                    dimensions[factor.path] = factor.levels_for_matrix()
                except (ValueError, TypeError) as error:
                    raise ValueError(f"{factor.label}: {error}") from error
            rows = []
            requests = (lhs_matrix(self.base, dimensions, self.lhs_count.value(), self.lhs_seed.value())
                        if self.sampling else matrix(self.base, dimensions))
            for row in requests:
                try:
                    validate(row)
                except (ValueError, TypeError) as error:
                    raise ValueError(f"Experiment {len(rows) + 1}: {error}") from error
                rows.append(row)
        except (TypeError, ValueError) as error:
            self.status.setText(str(error))
            return
        self.rows = rows
        self.sheet.reset(rows, self.factors)
        if self.sampling:
            self.export_csv.setEnabled(False)
        for i, factor in enumerate(self.sheet.columns, 1):
            width = max(self.fontMetrics().horizontalAdvance(label) for label in (factor.label, turkish(factor.label)))
            self.preview.setColumnWidth(i, max(100, width + 20))
        self.matrix_current = True
        self.run.setEnabled(True)
        self.status.setText(f"{len(rows)} experiments ready.")
        self.save_draft()

    def start(self, *, remote=None, separate=True, batch_size=None, parallel_experiments=1):
        from doe_studio import results_directory
        if not self.matrix_current or not self.rows or self.timer.isActive():
            return
        controller = self.host.remote_controller
        if remote is None:
            remote = controller.requested
        self.controller = controller if remote else None
        self.grouped_remote = remote and not separate
        self.separate_remote = remote and separate
        if batch_size is None:
            _, batch_size = self.run_options(self.operation.currentData())
        self.batch_size = batch_size if self.separate_remote else 1
        self.submission_size = 1
        self.submitted_rows.clear()
        self.pending_checks.clear()
        self.remote_id = None
        self.predecessor_job_id = None
        self.stop_requested = False
        if remote:
            if controller.auth() is None:
                self.status.setText(translate("Connect to TRUBA, then run the matrix again."))
                return
            try:
                request = matrix_request(self.rows)
            except ValueError as error:
                self.status.setText(translate(str(error)))
                return
        prefix = LEARNING_RUN_PREFIX if self.sampling else "DoE_"
        directory = results_directory() / (prefix + datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid4().hex[:8])
        try:
            directory.mkdir(parents=True)
            with (directory / "matrix.csv").open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                writer.writerow(["Sample", "Operation", *[".".join(f.path) for f in self.factors]])
                for i, row in enumerate(self.rows, 1):
                    writer.writerow([experiment_name(i), row["operation"],
                                     *(display(parameter_value(row, factor.path)) for factor in self.factors)])
        except OSError as error:
            self.status.setText(str(error))
            return
        self.directory = directory
        self.index = self.failures = 0
        for i in range(len(self.rows)):
            self.sheet.set_state(i, "Queued")
        self.open.setEnabled(True)
        self.lock(True)
        if remote:
            if self.truba_jobs_window is None:
                self.truba_jobs_window = DoeTrubaJobsWindow(self.window())
                if self.sampling:
                    self.truba_jobs_window.setObjectName("learningTrubaJobsWindow")
            self.truba_jobs_button.show()
            self.truba_jobs_window.begin()
        if self.grouped_remote:
            try:
                if parallel_experiments > 1 and len(self.rows) > 1:
                    request["parallel_experiments"] = min(parallel_experiments, len(self.rows))
                self.job = launch(request, self.directory, controller)
            except Exception as error:
                self.finish_remote("Failed", str(error))
                return
            self.status.setText(translate("Running experiment matrix on TRUBA."))
        self.timer.start()
        if not self.grouped_remote:
            self.advance()

    def open_truba_jobs(self):
        if self.truba_jobs_window is not None:
            show_restored_window(self.truba_jobs_window)

    def lock(self, busy):
        for widget in (self.operation, self.input_navigation, self.input_tabs, self.apply_button, self.run_settings):
            widget.setEnabled(not busy)
        self.run.setEnabled(not busy and self.matrix_current)
        self.stop.setEnabled(busy)
        if busy and self.sampling:
            self.export_csv.setEnabled(False)

    def record(self, state, *, index=None, **details):
        index = self.index if index is None else index
        case = self.directory / f"row_{index + 1:05d}"
        self.sheet.set_state(index, state, str(details.get("error", details.get("results", ""))))
        data = dict(experiment=index + 1, state=state, execution_target="truba" if self.controller else "local",
                    inputs=self.rows[index], **details)
        if self.remote_id and not self.separate_remote:
            data["slurm_job_id"] = self.remote_id
        from truba_jobs import write_record
        write_record(case / "experiment.json", data)

    def finish_remote(self, state, message):
        for index in range(len(self.rows)):
            self.sheet.set_state(index, state, message)
        self.finish(message)

    @staticmethod
    def experiment_range(index, count):
        return f"{index + 1}–{index + count}" if count > 1 else str(index + 1)

    def update_current_truba_job(self, record):
        experiment = (self.experiment_range(0, len(self.rows)) if self.grouped_remote
                      else self.experiment_range(self.index, self.submission_size))
        self.truba_jobs_window.update_job(experiment, record)

    def show_remote_progress(self):
        record = self.job.remote_status() or {}
        if not record.get("job_id"):
            return
        self.remote_id = record["job_id"]
        self.update_current_truba_job(record)
        if not self.grouped_remote:
            experiments = self.experiment_range(self.index, self.submission_size)
            self.status.setText(f"TRUBA #{self.remote_id} · DoE {experiments}/{len(self.rows)} · {record['state']}")
            return
        number = record.get("experiment_index")
        if type(number) is int and 1 <= number <= len(self.rows):
            last = record.get("experiment_end", number)
            if self.index <= number - 1:
                for previous in range(self.index, number - 1):
                    self.sheet.set_state(previous, "Awaiting results")
                self.index = number - 1
                for active in range(self.index, last):
                    if self.sheet.states[active] != "Running":
                        self.sheet.set_state(active, "Running")
            stage = record['state'].partition(' · Running experiment')[0]
            experiments = self.experiment_range(number - 1, last - number + 1)
            self.status.setText(f"TRUBA #{self.remote_id} · DoE {experiments}/{len(self.rows)} · {stage}")
        else:
            self.status.setText(f"TRUBA #{self.remote_id} · {record['state']}")

    def advance_remote(self):
        done = self.job.done()
        self.show_remote_progress()
        if not done:
            return
        job, self.job = self.job, None
        try:
            output = Path(job.result())
        except Exception as error:
            self.finish_remote("Failed", str(error))
            return
        for index in range(len(self.rows)):
            case = output / f"row_{index + 1:05d}"
            try:
                record = json.loads((case / "experiment.json").read_text(encoding="utf-8"))
                state = record["state"]
                if state not in ("Completed", "Failed"):
                    raise ValueError("Incomplete experiment record.")
                detail = record.get("error") or str(case)
            except (OSError, KeyError, ValueError) as error:
                state, detail = "Failed", str(error)
            self.sheet.set_state(index, state, detail)
            self.failures += state == "Failed"
        self.finish(f"{len(self.rows)} experiments finished · {self.failures} failed.", grouped_output=output)

    def update_submitted_row(self, record):
        request_id = record.get("request_id")
        if request_id not in self.submitted_rows:
            return
        from truba_job_list import terminal_record
        from truba_remote import doe_row_result

        index, size = self.submitted_rows[request_id]
        self.truba_jobs_window.update_job(self.experiment_range(index, size), record)
        state = record.get("state", "")
        job_id = record.get("job_id")
        details = {"slurm_job_id": job_id} if job_id else {}
        if state == "downloaded":
            output = Path(record["local_directory"]) / "output"
            for offset in range(size):
                if size > 1:
                    row_state, result = doe_row_result(output / f"row_{offset + 1:05d}")
                else:
                    row_state, result = "Completed", {"results": str(output)}
                self.record(row_state, index=index + offset, **result, **details)
                self.failures += row_state == "Failed"
        elif terminal_record(record):
            self.failures += size
            label = "Stopped" if state.upper().startswith("CANCELLED") else "Failed"
            for row in range(index, index + size):
                self.record(label, index=row, error=state, **details)
        else:
            label = "Running" if state.startswith("RUNNING") else "Queued"
            detail = f"TRUBA #{job_id or '…'} · {state}"
            for row in range(index, index + size):
                if self.sheet.details.get(row) != detail:
                    self.record(label, index=row, **details)
                    self.sheet.set_state(row, label, detail)
            return
        del self.submitted_rows[request_id]

    def track_submission(self, record):
        self.submitted_rows[record["request_id"]] = (self.index, self.submission_size)
        job_id = record.get("job_id")
        # A fast job can finish between the submission reply and this UI tick.
        path = self.case / "truba" / "remote_job.json"
        if path.is_file():
            record = json.loads(path.read_text(encoding="utf-8"))
        self.update_submitted_row(record)
        self.pending_checks.append((monotonic() + 2, record["request_id"]))
        return record.get("job_id") or job_id

    def skip_dependent_batches(self, error):
        for index in range(self.index, len(self.rows)):
            self.record("Skipped", index=index, error=str(error))
        self.index = len(self.rows)

    def refresh_submitted_rows(self):
        # SSH polling and result downloads are owned by TrubaSession. Read only
        # durable local snapshots here, including cancellations from the Jobs panel.
        now = monotonic()
        for _ in range(min(16, len(self.pending_checks))):
            due, request_id = self.pending_checks[0]
            if due > now:
                break
            self.pending_checks.popleft()
            if request_id not in self.submitted_rows:
                continue
            index, _ = self.submitted_rows[request_id]
            path = self.directory / f"row_{index + 1:05d}" / "truba" / "remote_job.json"
            if path.is_file():
                self.update_submitted_row(json.loads(path.read_text(encoding="utf-8")))
            if request_id in self.submitted_rows:
                self.pending_checks.append((now + 2, request_id))

    def advance(self):
        if self.stop_requested:
            return
        if self.grouped_remote:
            self.advance_remote()
            return
        try:
            if self.separate_remote:
                self.refresh_submitted_rows()
            if self.job is not None:
                if self.controller is not None:
                    self.show_remote_progress()
                if not self.job.done():
                    return
                job, self.job = self.job, None
                try:
                    result = job.result()
                    if result is None:
                        raise RuntimeError("Solver returned without results.")
                    if self.separate_remote:
                        if self.batch_size > 1 and not result.get("job_id"):
                            raise RuntimeError("Slurm did not confirm the preceding batch.")
                        self.predecessor_job_id = self.track_submission(result)
                    else:
                        path = result["directory"] if isinstance(result, dict) else str(result)
                except Exception as error:
                    durable = self.case / "truba" / "remote_job.json"
                    saved = json.loads(durable.read_text(encoding="utf-8")) if self.separate_remote and durable.is_file() else {}
                    if saved.get("job_id") or saved.get("submission_attempted"):
                        self.predecessor_job_id = self.track_submission(saved)
                    else:
                        self.failures += self.submission_size
                        for index in range(self.index, self.index + self.submission_size):
                            self.record("Failed", index=index, error=str(error))
                    if self.batch_size > 1 and not saved.get("job_id"):
                        self.index += self.submission_size
                        self.skip_dependent_batches(error)
                        return
                else:
                    if not self.separate_remote:
                        self.record("Completed", results=path)
                self.index += self.submission_size
            if self.index == len(self.rows):
                skipped = self.sheet.states.count("Skipped")
                suffix = f" · {skipped} " + translate("not submitted") if skipped else ""
                if self.submitted_rows:
                    job_count = (len(self.rows) + self.batch_size - 1) // self.batch_size
                    self.status.setText(f"TRUBA · {len(self.submitted_rows)} / {job_count} "
                                        + translate("jobs pending or running") + suffix)
                    return
                self.finish(f"{self.index - skipped} experiments finished · {self.failures} failed." + suffix)
                return
            self.case = self.directory / f"row_{self.index + 1:05d}"
            self.submission_size = min(self.batch_size, len(self.rows) - self.index)
            self.remote_id = None
            end = self.index + self.submission_size
            for index in range(self.index, end):
                (self.directory / f"row_{index + 1:05d}").mkdir()
                self.record("Submitting" if self.separate_remote else "Running", index=index)
            if self.separate_remote:
                experiments = self.experiment_range(self.index, self.submission_size)
                self.status.setText(f"TRUBA · {translate('Submitting')} · DoE {experiments}/{len(self.rows)}")
            else:
                self.status.setText(f"Running experiment {self.index + 1} / {len(self.rows)}")
            try:
                row = (matrix_request(self.rows[self.index:end])
                       if self.submission_size > 1 else self.rows[self.index])
                if self.controller is not None:
                    row = dict(row, experiment_index=self.index + 1, experiment_count=len(self.rows))
                if self.batch_size > 1 and self.predecessor_job_id is not None:
                    row["predecessor_job_id"] = self.predecessor_job_id
                self.job = launch(row, self.case, self.controller)
            except Exception as error:
                for index in range(self.index, end):
                    self.record("Failed", index=index, error=str(error))
                self.failures += self.submission_size
                self.index = end
                if self.batch_size > 1:
                    self.skip_dependent_batches(error)
        except OSError as error:
            self.finish(f"Cannot save experiment progress: {error} · {self.directory}")

    def finish(self, message, *, grouped_output=None):
        self.timer.stop()
        self.lock(False)
        try:
            write_doe_report(self.directory, states=self.sheet.states, details=self.sheet.details,
                             grouped_output=grouped_output)
        except (OSError, ValueError, TypeError, ImportError) as error:
            message += f" · {translate('Excel report could not be saved')}: {error}"
        if self.sampling:
            try:
                write_learning_csv(self.directory, states=self.sheet.states, details=self.sheet.details,
                                   grouped_output=grouped_output, only_finished=True)
            except (OSError, ValueError, TypeError) as error:
                message += f" · {translate('CSV export failed')}: {error}"
            self.learning_results_output = grouped_output
            self.export_csv.setEnabled(True)
        self.status.setText(message)
        if self.controller is not None and self.truba_jobs_window is not None:
            self.truba_jobs_window.notice.setText(message)

    def detach_remote(self):
        if self.separate_remote and self.timer.isActive():
            # Slurm and the durable background monitor now own every accepted job.
            first_unsent = self.index + (self.submission_size if self.job is not None else 0)
            message = translate("Remote experiment detached.")
            try:
                for i in range(first_unsent, len(self.rows)):
                    self.record("Skipped", index=i)
            except OSError as error:
                message += f" · {error}"
            self.submitted_rows.clear()
            self.finish(message)
        elif self.job is not None and self.job.is_remote:
            self.finish_remote("Detached", translate("Remote experiment detached."))
        else:
            return None
        job, self.job = self.job, None
        return job

    def cancel(self):
        if not self.timer.isActive():
            return
        self.stop_requested = self.separate_remote
        if self.job is not None:
            try:
                self.job.force_stop()
            except Exception as error:
                self.status.setText(str(error))
                return
            self.job = None
            if self.controller is not None:
                directory = self.directory if self.grouped_remote else self.case
                try:
                    record = json.loads((directory / "truba" / "remote_job.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
                else:
                    self.update_current_truba_job(record)
            if self.grouped_remote:
                self.finish_remote("Stopped", translate("Experiment matrix stopped."))
                return
            try:
                for index in range(self.index, self.index + self.submission_size):
                    self.record("Stopped", index=index)
            except OSError as error:
                self.finish(str(error))
                return
        if self.separate_remote:
            from truba_jobs import cancel_remote
            for i in range(self.index, len(self.rows)):
                if self.sheet.states[i] == "Queued":
                    self.sheet.set_state(i, "Skipped")
            self.index = len(self.rows)
            errors = []
            submitted = list(self.submitted_rows.values())
            if self.batch_size > 1:
                # Cancel dependent jobs before their predecessors can release them.
                submitted.reverse()
            for index, _ in submitted:
                path = self.directory / f"row_{index + 1:05d}" / "truba" / "remote_job.json"
                try:
                    cancel_remote(path, self.controller.auth())
                    self.update_submitted_row(json.loads(path.read_text(encoding="utf-8")))
                except Exception as error:
                    errors.append(str(error))
                    if self.batch_size > 1:
                        break
            if errors:
                self.status.setText(errors[0])
                return
        for i in range(self.index, len(self.rows)):
            if self.sheet.states[i] == "Queued":
                self.sheet.set_state(i, "Skipped")
        self.finish("Stopped. Finished results are saved.")

    def open_results(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.directory)))

    def export_learning_results(self):
        from file_dialog import QFileDialog

        if not self.export_csv.isEnabled():
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", str(self.directory / LEARNING_CSV_NAME), "CSV (*.csv)")
        if not filename:
            return
        target = Path(filename if filename.lower().endswith(".csv") else filename + ".csv")
        try:
            write_learning_csv(self.directory, target, states=self.sheet.states, details=self.sheet.details,
                               grouped_output=self.learning_results_output)
        except (OSError, ValueError, TypeError) as error:
            self.status.setText(f"{translate('CSV export failed')}: {error}")
            return
        self.status.setText(translate("CSV exported."))
