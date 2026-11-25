"""Independent operation-specific experiment planner in the DoE window."""
import csv
import json
from datetime import datetime
from uuid import uuid4

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox,
                               QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSpinBox,
                               QStackedWidget, QTableView, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from new_doe_operations import (OPERATIONS, explicit_numbers, launch, matrix,
                                parameters, snapshot, structure_choices, validate)
from studio_style import COMBO_WIDTH, CompactComboBox, CompactDoubleSpinBox, CompactSpinBox
from ui_language import translate, turkish

LABELS = {"grid": "Voxels per axis", "void_ratio": "Void / solid stiffness", "distribution": "WSL distribution",
          "structure_name": "Structure family / type", "tpms_morphology": "Morphology",
          "young_modulus": "Young’s modulus (MPa)", "cell_size": "Cell size (mm)",
          "solid_fraction": "Solid fraction (%)", "size": "Specimen size (mm)"}


def field_sources(host, op):
    if op == "fft_solve":
        return dict(grid=host.fft_grid, void_ratio=host.fft_void_ratio, cell_size=host.fft_cell_size,
                    solid_fraction=host.fft_solid_fraction, tpms_morphology=host.fft_morphology_combo)
    if op == "peridynamic_solve":
        return host.peridynamic_settings.controls
    sources = dict(host.controls, tpms_morphology=host.morphology_combo,
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
                       turbulence=ws.model)
        sources.update(getattr(ws, "thermal_controls", {}))
    return sources


def display(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def number(value, source):
    integer = type(value) is int
    box = CompactSpinBox() if integer else CompactDoubleSpinBox()
    if isinstance(source, (QSpinBox, QDoubleSpinBox)):
        box.setRange(min(value, source.minimum()), max(value, source.maximum()))
        box.setSingleStep(source.singleStep())
    else:
        bound = 2147483647 if integer else max(1e15, abs(value))
        box.setRange(-bound, bound)
    box.setFixedWidth(110)
    box.setValue(value)
    return box


class Factor:
    def __init__(self, table, row, path, value, source, op, changed):
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
        self.variable = QCheckBox()
        self.variable.setAccessibleName(self.label + " variable")
        table.setCellWidget(row, 1, self.variable)
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
        if self.choices:
            self.fixed = CompactComboBox()
            self.selection = QPushButton("Select values")
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
            self.initial_display = self.first.value()
            self.last = number(initial, source)
            self.mode = CompactComboBox()
            self.mode.addItem("Min / max", "bounds")
            self.mode.addItem("Value list", "list")
            self.selection = QLineEdit()
            self.selection.setPlaceholderText("16, 24, 40" if type(initial) is int else "0.5, 1, 2.5")
            self.selection.setToolTip("Separate values with commas. For decimal commas, use a semicolon between values.")
            self.selection.setAccessibleName(self.label + " " + translate("Value list"))
            self.selection.textChanged.connect(changed)
            self.fixed = self.first
            if self.optional_number:
                self.fixed = QLineEdit("null")
                self.fixed.textChanged.connect(changed)
            self.lower_cell = QStackedWidget()
            self.lower_cell.addWidget(self.first)
            if self.optional_number:
                self.lower_cell.addWidget(self.fixed)
            self.lower_blank = QWidget()
            self.lower_cell.addWidget(self.lower_blank)
            self.upper_cell = QStackedWidget()
            self.upper_cell.addWidget(self.last)
            self.upper_cell.addWidget(self.selection)
            self.upper_blank = QWidget()
            self.upper_cell.addWidget(self.upper_blank)
            self.extra = [self.upper_cell, self.mode]
            self.mode.currentIndexChanged.connect(lambda: self.toggle(self.variable.isChecked()))
            self.mode.currentIndexChanged.connect(changed)
            for box in (self.first, self.last):
                box.valueChanged.connect(changed)
        else:
            self.fixed = QLineEdit(display(value))
            self.fixed.textChanged.connect(changed)
            self.selection = QLineEdit()
            self.selection.setPlaceholderText('["value 1", "value 2"]')
            self.selection.setToolTip("Enter the levels as a JSON list.")
            self.selection.textChanged.connect(changed)
            self.extra = [self.selection]
        for i, widget in enumerate(self.extra, 3):
            table.setCellWidget(row, i, widget)
            widget.setEnabled(False)
        if self.numeric or self.optional_number:
            table.setCellWidget(row, 2, self.lower_cell)
            self.toggle(False)
        else:
            table.setCellWidget(row, 2, self.fixed)
        self.variable.toggled.connect(self.toggle)
        self.variable.toggled.connect(changed)
        for editor in (self.fixed, *self.extra):
            editor.setAccessibleName(self.label + " " + editor.accessibleName())
            if isinstance(editor, QPushButton):
                editor.setAutoDefault(False)

    def toggle(self, enabled):
        for widget in self.extra:
            widget.setEnabled(enabled)
        self.fixed.setEnabled(self.numeric or not enabled)
        if self.numeric or self.optional_number:
            listed = enabled and self.mode.currentData() == "list"
            self.lower_cell.setCurrentWidget(self.lower_blank if listed else self.first if enabled else self.fixed)
            self.upper_cell.setCurrentWidget(self.selection if listed else self.last if enabled else self.upper_blank)

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
            value = self.first.value()
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
        bar = QHBoxLayout()
        layout.addLayout(bar)
        self.operation = CompactComboBox()
        for title, op in OPERATIONS.items():
            self.operation.addItem(title, op)
        bar.addWidget(QLabel("Analysis"))
        bar.addWidget(self.operation)
        self.reload = QPushButton("Read workspace settings")
        bar.addWidget(self.reload)
        bar.addStretch()
        self.inputs = self.table(["Parameter", "Variable", "Fixed / minimum", "Maximum / values", "Input mode"])
        layout.addWidget(self.inputs, 3)
        buttons = QHBoxLayout()
        layout.addLayout(buttons)
        for name, title, callback in (("generate", "Create matrix", self.create), ("run", "Run", self.start),
                                      ("stop", "Stop", self.cancel), ("open", "Results folder", self.open_results)):
            button = QPushButton(title)
            button.setAutoDefault(False)
            button.clicked.connect(callback)
            buttons.addWidget(button)
            setattr(self, name, button)
        actions = (self.generate, self.run, self.stop, self.open)
        width = max(self.fontMetrics().horizontalAdvance(label) + 24 for button in actions
                    for label in (button.text(), turkish(button.text())))
        for button in actions:
            button.setProperty("managesLocalizedWidth", True)
            button.setFixedWidth(width)
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

    @staticmethod
    def table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setDefaultSectionSize(29)
        return table

    def showEvent(self, event):
        super().showEvent(event)
        if not self.loaded:
            self.load()

    def invalidate(self, *_):
        self.rows = []
        self.sheet.reset([], [])
        self.run.setEnabled(False)
        self.status.setText("Choose variable parameters and their levels, then create the matrix.")

    def load(self, *_):
        self.invalidate()
        self.inputs.setRowCount(0)
        self.factors.clear()
        try:
            op = self.operation.currentData()
            self.base = snapshot(self.host, op)
            sources = field_sources(self.host, op)
            for path, value in parameters(self.base):
                row = self.inputs.rowCount()
                self.inputs.insertRow(row)
                self.factors.append(Factor(self.inputs, row, path, value, sources.get(path[-1]), op, self.invalidate))
            self.inputs.resizeColumnsToContents()
            self.inputs.setColumnWidth(2, max(self.inputs.columnWidth(2), COMBO_WIDTH + 6))
            self.inputs.setColumnWidth(4, COMBO_WIDTH + 6)
            self.loaded = True
        except Exception as error:
            self.status.setText(str(error))
            self.loaded = False
        self.generate.setEnabled(self.loaded)

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
            for row in matrix(self.base, dimensions):
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
        for widget in (self.operation, self.reload, self.inputs, self.generate, self.run):
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
