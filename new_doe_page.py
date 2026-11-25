"""Independent operation-specific experiment planner in the DoE window."""
import csv
import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox,
                               QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QScrollArea, QSpinBox,
                               QStackedWidget, QTabWidget, QTableView, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from new_doe_operations import (OPERATIONS, explicit_numbers, launch, matrix,
                                parameters, snapshot, structure_choices, validate)
from structure_library import ALL_STRUCTURES_BY_FAMILY, STRUCTURE_FAMILIES
from studio_style import (COMBO_WIDTH, CONTROL_HEIGHT, CompactComboBox, CompactDoubleSpinBox,
                          CompactSpinBox, SidebarGroupBox)
from ui_language import translate, turkish
from model import CONTACT_NUMERICAL_FIELDS, GEOMETRY_FIELDS, MATERIAL_FIELDS, settings_from_dict, validate_geometry

FFT_OUTER_AXES = ("size_x", "size_y", "size")
LABELS = {"grid": "Voxels per axis", "void_ratio": "Void / solid stiffness", "distribution": "WSL distribution",
          "structure_name": "Structure family / type", "tpms_morphology": "Morphology",
          "young_modulus": "Young’s modulus (MPa)", "cell_size": "Cell size (mm)",
          "solid_fraction": "Solid fraction (%)", "size_x": "Outer X (mm)", "size_y": "Outer Y (mm)",
          "size": "Outer Z (mm)", "material_card": "Material card"}


def parameter_group(path, op):
    key = path[-1]
    if path[0] == "settings":
        if key in ("test_profile", "specimen_architecture", "core_fraction"):
            return "Standard test"
        if key in ("mesh_resolution", "mesh_type", "tetra_strategy", "local_mesh_size"):
            return "Mesh"
        if key in GEOMETRY_FIELDS:
            return "Structure / Dimensions"
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
        if key in ("cells_across", "mesh_method", "surface_refinement", "boundary_layers", "max_fitted_cells"):
            return "Mesh"
        return "Channel · Isothermal CFD"
    return "Peridynamics" if path[0] == "options" else "FFT Homogenization"


def field_sources(host, op):
    if op == "fft_solve":
        material = host.fft_material_editor
        return dict(host.fft_outer_sizes, grid=host.fft_grid, void_ratio=host.fft_void_ratio, cell_size=host.fft_cell_size,
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


def number(value, source):
    integer = type(value) is int
    box = CompactSpinBox() if integer else CompactDoubleSpinBox()
    if isinstance(source, (QSpinBox, QDoubleSpinBox)):
        if isinstance(source, QDoubleSpinBox):
            box.setDecimals(source.decimals())
        box.setRange(min(value, source.minimum()), max(value, source.maximum()))
        box.setSingleStep(source.singleStep())
        box.setSpecialValueText(source.specialValueText())
    else:
        bound = 2147483647 if integer else max(1e15, abs(value))
        box.setRange(-bound, bound)
    box.setFixedWidth(110)
    box.setKeyboardTracking(False)
    box.setValue(value)
    return box


def text_field(value=""):
    field = QLineEdit(value)
    field.setFixedHeight(CONTROL_HEIGHT)
    return field


class Factor:
    def __init__(self, table, row, variations, variation_row, path, value, source, op, changed):
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
        table.setItem(row, 0, QTableWidgetItem(self.label))
        variations.setItem(variation_row, 0, QTableWidgetItem(self.label))
        self.variable = QCheckBox()
        self.variable.setAccessibleName(self.label + " variable")
        table.setCellWidget(row, 2, self.variable)
        self.variations = variations
        self.variation_row = variation_row
        self.choices = []
        if key == "structure_name":
            self.choices = structure_choices(op)
        elif isinstance(source, QComboBox):
            self.choices = [(source.itemText(i), source.itemData(i)) for i in range(source.count())
                            if source.itemData(i) is not None and source.model().item(i).isEnabled()]
        elif type(value) is bool:
            self.choices = [("True", True), ("False", False)]
        self.numeric = not self.choices and type(value) in (int, float)
        self.optional_number = value is None and key in ("minimum_substeps", "maximum_substeps", "stl_resolution")
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
            self.extra = [self.selection]
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
                self.initial_display = self.fixed.value()
                self.fixed.valueChanged.connect(changed)
            else:
                self.fixed = text_field("null")
                self.fixed.textChanged.connect(changed)
            self.lower_cell = QStackedWidget()
            self.lower_cell.addWidget(self.first)
            self.lower_blank = QWidget()
            self.lower_cell.addWidget(self.lower_blank)
            self.upper_cell = QStackedWidget()
            self.upper_cell.addWidget(self.last)
            self.upper_cell.addWidget(self.selection)
            self.extra = [self.lower_cell, self.upper_cell, self.mode]
            self.mode.currentIndexChanged.connect(lambda: self.toggle(self.variable.isChecked()))
            self.mode.currentIndexChanged.connect(changed)
            for box in (self.first, self.last):
                box.valueChanged.connect(changed)
        else:
            self.fixed = text_field(display(value))
            self.fixed.textChanged.connect(changed)
            self.selection = text_field()
            self.selection.setPlaceholderText('["value 1", "value 2"]')
            self.selection.setToolTip("Enter the levels as a JSON list.")
            self.selection.textChanged.connect(changed)
            self.extra = [self.selection]
        first_column = 1 if self.numeric or self.optional_number else 2
        for i, widget in enumerate(self.extra, first_column):
            variations.setCellWidget(variation_row, i, widget)
        self.fixed_panel = self.fixed
        if key == "structure_name":
            self.add_structure_family(value)
        table.setCellWidget(row, 1, self.fixed_panel)
        self.toggle(False)
        self.variable.toggled.connect(self.toggle)
        self.variable.toggled.connect(changed)
        for editor in (self.fixed, *self.extra):
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
        if self.numeric or self.optional_number:
            listed = self.mode.currentData() == "list"
            self.lower_cell.setCurrentWidget(self.lower_blank if listed else self.first)
            self.upper_cell.setCurrentWidget(self.selection if listed else self.last)

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
        controls = QHBoxLayout(self.fixed_panel)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(3)
        controls.addWidget(self.family)
        controls.addWidget(self.fixed)
        controls.addStretch()
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

    def levels_for_matrix(self):
        varying = self.variable.isChecked()
        if self.choices:
            values = ([a.data() for a in self.menu.actions() if a.isChecked()]
                      if varying else [self.fixed.currentData()])
        elif (self.numeric or self.optional_number) and varying:
            if self.mode.currentData() == "list":
                values = explicit_numbers(self.selection.text(), type(self.first.value()),
                                          self.first.minimum(), self.first.maximum())
            else:
                if self.last.value() <= self.first.value():
                    raise ValueError("The upper bound must exceed the lower bound.")
                values = [self.first.value(), self.last.value()]
        elif self.numeric:
            value = self.fixed.value()
            values = [self.value if value == self.initial_display else value]
        elif varying:
            values = json.loads(self.selection.text())
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
                return section + 1
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
        data = self.rows[row]
        return display((data if len(path) == 1 else data[path[0]])[path[-1]])

    def set_state(self, row, state, detail=""):
        self.states[row] = state
        self.details[row] = detail
        index = self.index(row, 0)
        self.dataChanged.emit(index, index)


class NewDoePage(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.rows = []
        self.factors = []
        self.job = self.directory = None
        self.loaded = False
        self.timer = QTimer(self, interval=200)
        self.timer.timeout.connect(self.advance)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 1, 2, 1)
        layout.setSpacing(2)
        bar = QHBoxLayout()
        layout.addLayout(bar)
        self.operation = CompactComboBox()
        for title, op in OPERATIONS.items():
            self.operation.addItem(title, op)
        bar.addWidget(QLabel("Analysis"))
        bar.addWidget(self.operation)
        self.apply_button = QPushButton("Apply")
        self.apply_button.setAutoDefault(False)
        self.apply_button.setToolTip("Generate the selected experiment geometry, or the first geometry in the draft.")
        self.apply_button.clicked.connect(self.apply_geometry)
        bar.addWidget(self.apply_button)
        self.reload = QPushButton("Read workspace settings")
        self.reload.setAutoDefault(False)
        bar.addWidget(self.reload)
        bar.addStretch()
        self.inputs = QScrollArea()
        self.inputs.setWidgetResizable(True)
        self.input_tabs = QTabWidget()
        self.input_tabs.addTab(self.inputs, "Analysis settings")
        self.variations = self.table(["Parameter", "Lower bound", "Maximum / values", "Input mode"])
        self.variations.setAccessibleName("Variable settings")
        self.input_tabs.addTab(self.variations, "Variable settings")
        self.sections = {}
        layout.addWidget(self.input_tabs, 3)
        buttons = QHBoxLayout()
        layout.insertLayout(1, buttons)
        for name, title, callback in (("generate", "Create matrix", self.create), ("run", "Run", self.start),
                                      ("stop", "Stop", self.cancel), ("open", "Results folder", self.open_results)):
            button = QPushButton(title)
            button.setAutoDefault(False)
            button.clicked.connect(callback)
            buttons.addWidget(button)
            setattr(self, name, button)
        self.commands = (self.apply_button, self.reload, self.generate, self.run, self.stop, self.open)
        self.button_width = 0
        buttons.addStretch()
        self.preview = QTableView()
        self.preview.setAlternatingRowColors(True)
        self.sheet = ExperimentMatrix(self.preview)
        self.preview.setModel(self.sheet)
        self.preview.verticalHeader().setDefaultSectionSize(26)
        layout.addWidget(self.preview, 2)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.run.setEnabled(False)
        self.stop.setEnabled(False)
        self.open.setEnabled(False)
        self.operation.currentIndexChanged.connect(self.load)
        self.reload.clicked.connect(self.load)

    def size_navigation(self, navigation, active):
        nav = navigation.findChildren(QPushButton)
        if active:
            self._nav_sizes = [(b, b.minimumWidth(), b.maximumWidth(), b.property("managesLocalizedWidth")) for b in nav]
            titles = [b.text() for b in (*nav, *self.commands)] + ["Select values"]
            self.button_width = max(self.fontMetrics().horizontalAdvance(text) + 26
                                    for title in titles for text in (title, turkish(title)))
            for button in (*nav, *self.findChildren(QPushButton)):
                button.setProperty("managesLocalizedWidth", True)
                button.setFixedWidth(self.button_width)
        else:
            for button, low, high, managed in getattr(self, "_nav_sizes", ()):
                button.setMinimumWidth(low)
                button.setMaximumWidth(high)
                button.setProperty("managesLocalizedWidth", managed)
            self._nav_sizes = []

    @staticmethod
    def table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().hide()
        table.verticalHeader().setMinimumSectionSize(25)
        table.verticalHeader().setDefaultSectionSize(26)
        return table

    def showEvent(self, event):
        super().showEvent(event)
        if not self.loaded:
            self.load()

    def invalidate(self, *_):
        self.rows = []
        self.sheet.reset([], [])
        self.preview.hide()
        self.run.setEnabled(False)
        self.status.setText("Choose variable parameters and their levels, then create the matrix.")

    def load(self, *_):
        self.invalidate()
        expanded = {title: not section.content.isHidden() for title, section in self.sections.items()}
        self.sections.clear()
        self.factors.clear()
        self.input_tabs.setCurrentWidget(self.inputs)
        self.variations.setRowCount(0)
        panel = QWidget()
        stack = QVBoxLayout(panel)
        stack.setContentsMargins(2, 2, 2, 2)
        stack.setSpacing(2)
        stack.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.inputs.setWidget(panel)
        try:
            op = self.operation.currentData()
            self.base = snapshot(self.host, op)
            sources = field_sources(self.host, op)
            groups = {"Structure / Dimensions": []}
            fields = list(parameters(self.base))
            if op == "fft_solve":
                # The workspace uses zero to display "1 cell", even though
                # its solver snapshot always contains resolved dimensions.
                fields = [(path, sources[path[-1]].value() if path[-1] in FFT_OUTER_AXES else value)
                          for path, value in fields]
            variation_rows = {path: index for index, (path, _) in enumerate(fields)}
            self.variations.setRowCount(len(fields))
            for path, value in fields:
                groups.setdefault(parameter_group(path, op), []).append((path, value))
            factors, tables = {}, []
            button_width = self.button_width
            for title, members in groups.items():
                section = SidebarGroupBox(title)
                self.sections[title] = section
                stack.addWidget(section)
                body = QVBoxLayout(section.content)
                body.setContentsMargins(2, 0, 2, 1)
                if title == "Solid phase material":
                    actions = QHBoxLayout()
                    width = button_width or max(self.fontMetrics().horizontalAdvance(translate(label))
                                                for label in ("Library", "Load")) + 26
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
                table = self.table(["Parameter", "Fixed value", "Variable"])
                tables.append(table)
                body.addWidget(table)
                table.setRowCount(len(members))
                for row, (path, value) in enumerate(members):
                    factors[path] = Factor(table, row, self.variations, variation_rows[path], path, value,
                                           sources.get(path[-1]), op, self.invalidate)
                table.ensurePolished()
                table.resizeColumnsToContents()
                width = max((factors[path].fixed_panel.sizeHint().width() for path, _ in members),
                            default=COMBO_WIDTH)
                table.setColumnWidth(1, max(table.columnWidth(1), width + 6))
                table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                section.set_expanded(expanded.get(title, op == "fft_solve" or title == "Structure / Dimensions"))
            widths = [max(table.columnWidth(column) for table in tables) for column in range(3)]
            for table in tables:
                for column, width in enumerate(widths):
                    table.setColumnWidth(column, width)
                frame = 2 * table.frameWidth()
                table.setMinimumWidth(sum(widths) + frame)
                table.setFixedHeight(table.horizontalHeader().sizeHint().height() + table.verticalHeader().length() + frame)
            # Keep matrix combinations and CSV columns in the operation's parameter order.
            self.factors = [factors[path] for path, _ in fields]
            self.variations.setColumnWidth(0, max(
                [100, *(self.fontMetrics().horizontalAdvance(translate(f.label)) + 18 for f in self.factors)]))
            for column, width in ((1, 116), (2, max(180, button_width + 4)), (3, COMBO_WIDTH + 6)):
                self.variations.setColumnWidth(column, width)
            for button in self.variations.findChildren(QPushButton):
                button.setProperty("managesLocalizedWidth", True)
                button.setFixedWidth(button_width or button.sizeHint().width())
            if self.button_width:
                for button in panel.findChildren(QPushButton):
                    button.setProperty("managesLocalizedWidth", True)
                    button.setFixedWidth(self.button_width)
            self.loaded = True
        except Exception as error:
            self.status.setText(str(error))
            self.loaded = False
        self.generate.setEnabled(self.loaded)
        self.apply_button.setEnabled(self.loaded)

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
        values = card.evaluate(293.15, ("young_modulus", "poisson_ratio"))
        fields = {factor.path[-1]: factor for factor in self.factors}
        fields["material_name"].fixed.setText(card.name)
        fields["material_card"].fixed.setText(display(card.to_dict()))
        fields["young_modulus"].fixed.setValue(values["young_modulus"] / 1e6)
        fields["poisson_ratio"].fixed.setValue(values["poisson_ratio"])

    def experiments(self, dimensions):
        for request in matrix(self.base, dimensions):
            if request["operation"] == "fft_solve":
                settings = request["settings"]
                for key in FFT_OUTER_AXES:
                    if settings[key] == 0:
                        settings[key] = settings["cell_size"]
            yield request

    def apply_geometry(self):
        selected = self.preview.currentIndex()
        if self.rows:
            index = selected.row() if selected.isValid() else 0
            request = self.rows[index]
            label = f"Experiment {index + 1}"
        else:
            # Preview one geometry without generating a matrix or requiring solver inputs.
            dimensions = {}
            try:
                for factor in self.factors:
                    if factor.path[0] == "settings" and factor.path[-1] in GEOMETRY_FIELDS:
                        if ((factor.numeric or factor.optional_number) and factor.variable.isChecked()
                                and factor.mode.currentData() == "bounds"):
                            value = factor.first.value()
                        else:
                            value = factor.levels_for_matrix()[0]
                        dimensions[factor.path] = [value]
                request = next(self.experiments(dimensions))
            except (TypeError, ValueError) as error:
                self.status.setText(f"{factor.label}: {error}")
                return
            label = "Geometry preview"
        settings = settings_from_dict(request["settings"])
        errors = validate_geometry(settings, include_mesh=False)
        if errors:
            self.status.setText("\n".join(errors))
            return
        self.host._show_doe_bounds("NEW DOE", {label: settings})

    def create(self):
        self.invalidate()
        dimensions = {}
        try:
            for factor in self.factors:
                try:
                    dimensions[factor.path] = factor.levels_for_matrix()
                except (ValueError, TypeError) as error:
                    raise ValueError(f"{factor.label}: {error}") from error
            rows = []
            for row in self.experiments(dimensions):
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
        self.preview.show()
        for i, factor in enumerate(self.sheet.columns, 1):
            width = max(self.fontMetrics().horizontalAdvance(label) for label in (factor.label, turkish(factor.label)))
            self.preview.setColumnWidth(i, max(100, width + 20))
        self.run.setEnabled(True)
        self.status.setText(f"{len(rows)} experiments ready.")

    def start(self):
        from doe_studio import results_directory
        if not self.rows or self.timer.isActive():
            return
        directory = results_directory() / ("NEW_DOE_" + datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid4().hex[:8])
        try:
            directory.mkdir(parents=True)
            with (directory / "matrix.csv").open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                writer.writerow(["Experiment", "Operation", *[".".join(f.path) for f in self.factors]])
                for i, row in enumerate(self.rows, 1):
                    writer.writerow([i, row["operation"], *(display(v) for _, v in parameters(row))])
        except OSError as error:
            self.status.setText(str(error))
            return
        self.directory = directory
        self.index = self.failures = 0
        self.controller = self.host.remote_controller if self.host.remote_controller.requested else None
        for i in range(len(self.rows)):
            self.sheet.set_state(i, "Queued")
        self.open.setEnabled(True)
        self.lock(True)
        self.timer.start()
        self.advance()

    def lock(self, busy):
        for widget in (self.operation, self.reload, self.input_tabs, self.apply_button, self.generate, self.run):
            widget.setEnabled(not busy)
        self.stop.setEnabled(busy)

    def record(self, state, **details):
        self.sheet.set_state(self.index, state, str(details.get("error", details.get("results", ""))))
        data = dict(experiment=self.index + 1, state=state, inputs=self.rows[self.index], **details)
        temporary = self.case / "experiment.tmp"
        temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.case / "experiment.json")

    def advance(self):
        try:
            if self.job is not None:
                if not self.job.done():
                    return
                job, self.job = self.job, None
                try:
                    result = job.result()
                    if result is None:
                        raise RuntimeError("Solver returned without results.")
                    path = result["directory"] if isinstance(result, dict) else str(result)
                except Exception as error:
                    self.failures += 1
                    self.record("Failed", error=str(error))
                else:
                    self.record("Completed", results=path)
                self.index += 1
            if self.index == len(self.rows):
                self.finish(f"{self.index} experiments finished · {self.failures} failed.")
                return
            self.case = self.directory / f"row_{self.index + 1:05d}"
            self.case.mkdir()
            self.record("Running")
            self.status.setText(f"Running experiment {self.index + 1} / {len(self.rows)}")
            try:
                self.job = launch(self.rows[self.index], self.case, self.controller)
            except Exception as error:
                self.record("Failed", error=str(error))
                self.failures += 1
                self.index += 1
        except OSError as error:
            self.finish(f"Cannot save experiment progress: {error} · {self.directory}")

    def finish(self, message):
        self.timer.stop()
        self.lock(False)
        self.status.setText(message)

    def detach_remote(self):
        if self.job is None or not self.job.is_remote:
            return None
        job, self.job = self.job, None
        self.finish("Remote experiment detached.")
        try:
            self.record("Detached", remote_job=job.remote_status())
        except OSError as error:
            self.status.setText(str(error))
        return job

    def cancel(self):
        if not self.timer.isActive():
            return
        if self.job is not None:
            try:
                self.job.force_stop()
            except Exception as error:
                self.status.setText(str(error))
                return
            self.job = None
            try:
                self.record("Stopped")
            except OSError as error:
                self.finish(str(error))
                return
        for i in range(self.index, len(self.rows)):
            if self.sheet.states[i] == "Queued":
                self.sheet.set_state(i, "Skipped")
        self.finish("Stopped. Finished results are saved.")

    def open_results(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.directory)))
