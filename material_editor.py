"""Named material editing and reusable JSON / true-stress curve input."""
from __future__ import annotations

import csv
from pathlib import Path

from studio_style import CompactComboBox
from PyQt6.QtCore import Qt, pyqtSignal as Signal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from studio_style import ActionFlowLayout, CompactDoubleSpinBox, SidebarGroupBox, configure_form, field_label
from model import (
    MATERIAL_MODELS, ROOT, Settings, load_material, save_material,
    plastic_curve_from_total_true_strain, validate_hardening_curve,
)


class NoWheelDoubleSpinBox(CompactDoubleSpinBox):
    """Double spinbox that ignores mouse wheel scrolling."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event) -> None:
        event.ignore()


class NoWheelComboBox(CompactComboBox):
    """ComboBox that ignores mouse wheel scrolling."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event) -> None:
        event.ignore()


class MaterialEditor(SidebarGroupBox):
    """Edit a material independently of geometry and solver execution."""

    changed = Signal()

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setObjectName("materialGroup")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.setAccessibleName("Material")
        self.setTitle("Material")
        self.active_path = None
        self.controls = {}
        self._stress_factor = 1.0
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(5, 4, 5, 4)
        content_layout.setSpacing(2)

        name_form = QFormLayout()
        configure_form(name_form)
        content_layout.addLayout(name_form)
        self.name = QLineEdit(settings.material_name)
        self.name.setPlaceholderText("e.g. Ti-6Al-4V ELI / 316L SS")
        name_lbl = field_label("Material name", self.name)
        name_form.addRow(name_lbl, self.name)

        actions = ActionFlowLayout(equal_width=True)
        actions.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.load_button = QPushButton("Load")
        self.load_button.setToolTip("Load material file")
        self.load_button.clicked.connect(self.load_file)
        self.save_button = QPushButton("Save")
        self.delete_button = QPushButton("Delete")
        self.edit_button = QPushButton("Edit")
        self.edit_button.setCheckable(True)
        self.edit_button.setToolTip("Enable editing of material properties")
        self.help_button = QPushButton("Help")
        self.help_button.setToolTip("Tangent modulus guide and approximate datasheet calculator")
        self.help_button.clicked.connect(self.show_tangent_help)
        for button in (self.load_button, self.save_button, self.edit_button, self.delete_button, self.help_button):
            button.setAutoDefault(False)
            button.setDefault(False)
            button.setObjectName("secondaryActionBtn")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            actions.addWidget(button)
        self.delete_button.setObjectName("destructiveActionBtn")
        self.edit_button.setObjectName("materialEditButton")
        self.save_button.clicked.connect(self.save_file)
        self.delete_button.clicked.connect(self.delete_file)
        self.edit_button.toggled.connect(self.set_editable)
        self.delete_button.setEnabled(False)
        content_layout.addLayout(actions)

        self.form = QFormLayout()
        self.form.setContentsMargins(0, 0, 0, 0)
        configure_form(self.form)
        content_layout.addLayout(self.form)

        self.stress_unit = NoWheelComboBox()
        for unit, factor in (("Pa", 1e-6), ("kPa", 1e-3), ("MPa", 1.0),
                             ("GPa", 1000.0), ("psi", 0.006894757293168),
                             ("ksi", 6.894757293168)):
            self.stress_unit.addItem(unit, factor)
        self.stress_unit.setCurrentText("MPa")
        self.form.addRow(field_label("Stress unit", self.stress_unit), self.stress_unit)

        self.model = NoWheelComboBox()
        for key, label in MATERIAL_MODELS.items():
            self.model.addItem(label, key)
        self.model.setCurrentIndex(self.model.findData(settings.material_model))
        model_lbl = field_label("Constitutive law", self.model)
        self.form.addRow(model_lbl, self.model)

        field_specs = (
            ("material_density", "Density / g·cm⁻³", 1e-4, 0.1),
            ("young_modulus", "Young's modulus", 1e-6, 1000),
            ("poisson_ratio", "Poisson ratio", -0.999, 0.01),
            ("yield_stress", "True yield stress", 1e-6, 10),
            ("tangent_modulus", "Tangent modulus Eₜ", 0, 100),
        )
        self.stress_fields = {"young_modulus", "yield_stress", "tangent_modulus"}
        self.field_specs = {key: (label, minimum, step) for key, label, minimum, step in field_specs}
        for key, label, minimum, step in field_specs:
            spin = NoWheelDoubleSpinBox()
            spin.setRange(minimum, 0.4999 if key == "poisson_ratio" else 1e9)
            spin.setValue(getattr(settings, key))
            spin.setSingleStep(step)
            spin.setKeyboardTracking(False)
            self.controls[key] = spin
            row_lbl = field_label(label, spin)
            self.form.addRow(row_lbl, spin)

        self.controls["yield_stress"].setToolTip(
            "Initial uniaxial true (Cauchy) yield stress, at zero plastic strain."
        )
        self.controls["tangent_modulus"].setToolTip(
            "Slope of true stress versus total logarithmic strain after yield. "
            "This is a modulus, not a strain. Must satisfy 0 ≤ Eₜ < E."
        )

        self.model_hint = QLabel()
        self.model_hint.setObjectName("mutedHintLabel")
        self.model_hint.setWordWrap(True)
        content_layout.addWidget(self.model_hint)

        self.curve_panel = QWidget()
        self.curve_panel.setObjectName("curvePanel")
        curve_layout = QVBoxLayout(self.curve_panel)
        curve_layout.setContentsMargins(0, 2, 0, 0)
        curve_layout.setSpacing(2)

        curve_header = QLabel("True Stress–Plastic Strain Curve")
        curve_header.setObjectName("sectionSubheader")
        curve_header.setWordWrap(True)
        curve_layout.addWidget(curve_header)

        self.table = QTableWidget(0, 2)
        self.table.setObjectName("curveTable")
        self.table.setHorizontalHeaderLabels(["Plastic strain", "True stress / MPa"])
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setMinimumHeight(130)
        curve_layout.addWidget(self.table, 1)

        curve_buttons = ActionFlowLayout()
        self.curve_buttons = []
        for label, callback in (
            ("+ Row", self.add_row),
            ("− Row", self.remove_row),
            ("CSV…", self.import_curve),
        ):
            button = QPushButton(label)
            button.setObjectName("compactActionBtn")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(callback)
            curve_buttons.addWidget(button)
            self.curve_buttons.append(button)
        curve_layout.addLayout(curve_buttons)

        curve_hint = QLabel(
            "Table: true (Cauchy) stress and plastic logarithmic strain (dimensionless). "
            "First row: zero plastic strain and initial yield stress. "
            "CSV also accepts total true strain with the header true_strain,true_stress; "
            "elastic strain is subtracted using E. Engineering data is not converted."
        )
        curve_hint.setObjectName("mutedHintLabel")
        curve_hint.setWordWrap(True)
        curve_layout.addWidget(curve_hint)

        content_layout.addWidget(self.curve_panel)
        self.fill_curve(settings.hardening_curve)

        self.model.currentIndexChanged.connect(self.update_model)
        self.name.textChanged.connect(lambda *_: self.changed.emit())
        self.table.itemChanged.connect(lambda *_: self.changed.emit())
        self.update_model()
        self.set_editable(False)
        self.stress_unit.currentIndexChanged.connect(self.change_stress_unit)
        self.update_unit_labels()
        self.set_expanded(True)

    def show_tangent_help(self, *_) -> None:
        from tangent_modulus_help import TangentModulusHelpDialog

        values = self.numeric_values()
        self.tangent_help = TangentModulusHelpDialog(
            values["young_modulus"], values["yield_stress"],
            self.stress_unit.currentText(), self._stress_factor, self,
        )
        self.tangent_help.estimate_selected.connect(self.apply_tangent_estimate)
        self.tangent_help.open()

    def apply_tangent_estimate(self, young_modulus, yield_stress, tangent_modulus) -> None:
        self.edit_button.setChecked(True)
        self.model.setCurrentIndex(self.model.findData("bilinear_isotropic"))
        for key, value in (("young_modulus", young_modulus), ("yield_stress", yield_stress),
                           ("tangent_modulus", tangent_modulus)):
            self.controls[key].setValue(value / self._stress_factor)
        self.changed.emit()

    def update_unit_labels(self) -> None:
        unit = self.stress_unit.currentText()
        for key in self.stress_fields:
            self.form.labelForField(self.controls[key]).setText(
                f"{self.field_specs[key][0]} / {unit}"
            )
        self.table.setHorizontalHeaderLabels(["Plastic log strain", f"True stress / {unit}"])
        self.curve_buttons[-1].setToolTip(
            f"CSV: plastic_strain,true_stress or true_strain,true_stress (stress in {unit}). "
            "Without a header, strain is plastic logarithmic strain. Start at initial yield."
        )

    def change_stress_unit(self, *_) -> None:
        factor = float(self.stress_unit.currentData())
        ratio = self._stress_factor / factor
        for key in self.stress_fields:
            control = self.controls[key]
            control.blockSignals(True)
            value = control.value() * ratio
            _, minimum, step = self.field_specs[key]
            control.setRange(minimum / factor, 1e9 / factor)
            control.setSingleStep(step / factor)
            control.setValue(value)
            control.blockSignals(False)
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item and item.text().strip():
                try:
                    item.setText(format(float(item.text()) * ratio, ".15g"))
                except ValueError:
                    pass
        self.table.blockSignals(False)
        self._stress_factor = factor
        self.update_unit_labels()
        self.changed.emit()

    def numeric_values(self) -> dict:
        return {
            key: control.value() * (self._stress_factor if key in self.stress_fields else 1)
            for key, control in self.controls.items()
        }

    def set_numeric_values(self, values) -> None:
        for key, control in self.controls.items():
            control.setValue(values[key] / (self._stress_factor if key in self.stress_fields else 1))

    def set_editable(self, editable: bool) -> None:
        self.name.setReadOnly(not editable)
        self.model.setEnabled(editable)
        for control in self.controls.values():
            control.setReadOnly(not editable)
            control.setButtonSymbols(
                QDoubleSpinBox.ButtonSymbols.UpDownArrows
                if editable else QDoubleSpinBox.ButtonSymbols.NoButtons
            )
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
            if editable else QAbstractItemView.EditTrigger.NoEditTriggers
        )
        for button in self.curve_buttons:
            button.setEnabled(editable)

    def set_active_path(self, path: Path | None) -> None:
        self.active_path = path
        self.delete_button.setEnabled(path is not None)
        self.delete_button.setToolTip(
            f"Delete material file: {path}" if path else "Load or save a material before deleting it"
        )

    def update_model(self, *_) -> None:
        bilinear = self.model.currentData() == "bilinear_isotropic"
        for key in ("yield_stress", "tangent_modulus"):
            self.form.setRowVisible(self.controls[key], bilinear)
        self.curve_panel.setVisible(self.model.currentData() == "multilinear_isotropic")
        self.model_hint.setText({
            "linear_elastic": "Elastic response only; plastic deformation is not modeled.",
            "bilinear_isotropic": "Bilinear approximation: E, true yield stress and Eₜ. "
                "Choose the true stress–strain curve when measured hardening data is available.",
            "multilinear_isotropic": "Multilinear isotropic hardening from a true stress–strain curve. "
                "E defines the elastic part; the table defines plastic hardening.",
        }.get(self.model.currentData(), ""))
        self.changed.emit()

    def extra_values(self) -> dict:
        rows = []
        for row in range(self.table.rowCount()):
            pair = [
                self.table.item(row, col).text().strip() if self.table.item(row, col) else ""
                for col in range(2)
            ]
            if not any(pair):
                continue
            converted = []
            for text in pair:
                try:
                    converted.append(float(text))
                except ValueError:
                    converted.append(text)
            if len(converted) == 2 and isinstance(converted[1], float):
                converted[1] *= self._stress_factor
            rows.append(tuple(converted))
        return {
            "material_name": self.name.text().strip(),
            "material_model": self.model.currentData(),
            "hardening_curve": tuple(rows),
        }

    def material_settings(self) -> Settings:
        values = self.numeric_values()
        return Settings(**values, **self.extra_values())

    def fill_curve(self, points) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(max(2, len(points)))
        self.table.clearContents()
        for row, point in enumerate(points):
            for col, value in enumerate(point):
                self.table.setItem(row, col, QTableWidgetItem(format(value / self._stress_factor if col == 1 else value, ".15g")))
        self.table.blockSignals(False)

    def add_row(self, *_) -> None:
        self.table.insertRow(self.table.rowCount())

    def remove_row(self, *_) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self.changed.emit()

    def import_curve_path(self, path: Path) -> None:
        points = []
        total_true_strain = False
        header_seen = False
        with path.open(newline="", encoding="utf-8-sig") as stream:
            for row in csv.reader(stream):
                if not row or all(not value.strip() for value in row):
                    continue
                if len(row) != 2:
                    raise ValueError(
                        f"CSV needs exactly two columns: plastic strain, true stress / {self.stress_unit.currentText()}."
                    )
                try:
                    points.append((float(row[0]), float(row[1]) * self._stress_factor))
                except ValueError:
                    strain_header = row[0].strip().lower().replace(" ", "_")
                    if points or header_seen or strain_header not in ("plastic_strain", "true_strain"):
                        raise ValueError(
                            "CSV needs numeric data and an optional plastic_strain or true_strain header. "
                            "Engineering strain is not supported."
                        ) from None
                    stress_header = row[1].strip().lower().replace(" ", "_")
                    allowed_stress = {"true_stress", f"true_stress_{self.stress_unit.currentText().lower()}"}
                    if strain_header == "plastic_strain":
                        allowed_stress.add("stress")  # Legacy plastic-curve CSV files.
                    if stress_header not in allowed_stress:
                        raise ValueError("Stress header must identify true_stress in the selected stress unit.")
                    total_true_strain = strain_header == "true_strain"
                    header_seen = True

        if total_true_strain:
            points = plastic_curve_from_total_true_strain(points, self.numeric_values()["young_modulus"])

        errors = validate_hardening_curve(points)
        if errors:
            raise ValueError("\n".join(errors))
        self.fill_curve(points)
        self.model.setCurrentIndex(self.model.findData("multilinear_isotropic"))
        self.changed.emit()

    def import_curve(self, *_) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Import material curve", str(ROOT), "CSV (*.csv)"
        )
        if filename:
            try:
                self.import_curve_path(Path(filename))
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Cannot import curve", str(exc))

    def load_path(self, path: Path) -> None:
        values = load_material(path)
        self.name.setText(values["material_name"])
        self.set_numeric_values(values)
        self.fill_curve(values["hardening_curve"])
        self.model.setCurrentIndex(self.model.findData(values["material_model"]))
        self.set_active_path(path)
        self.edit_button.setChecked(False)
        self.changed.emit()

    def load_file(self, *_) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Load material", str(ROOT / "materials"), "JSON (*.json)"
        )
        if filename:
            try:
                self.load_path(Path(filename))
            except (OSError, ValueError, TypeError) as exc:
                QMessageBox.warning(self, "Cannot load material", str(exc))

    def save_file(self, *_) -> None:
        folder = ROOT / "materials"
        folder.mkdir(exist_ok=True)
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save material", str(self.active_path or folder / "material.json"), "JSON (*.json)"
        )
        if filename:
            try:
                save_material(self.material_settings(), Path(filename))
                self.set_active_path(Path(filename))
                self.edit_button.setChecked(False)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Cannot save material", str(exc))

    def delete_file(self, *_) -> None:
        path = self.active_path
        if path is None:
            return
        answer = QMessageBox.question(
            self, "Delete material", f"Delete the material file?\n{path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            path.unlink()
        except OSError as exc:
            QMessageBox.warning(self, "Cannot delete material", str(exc))
            return
        defaults = Settings()
        self.name.setText(defaults.material_name)
        self.set_numeric_values(vars(defaults))
        self.fill_curve(defaults.hardening_curve)
        self.model.setCurrentIndex(self.model.findData(defaults.material_model))
        self.set_active_path(None)
        self.edit_button.setChecked(False)
        self.changed.emit()
