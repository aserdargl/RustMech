"""Browse, create and select materials in the native library."""
from html import escape

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFormLayout, QHBoxLayout,
                              QLabel, QLineEdit, QListWidget, QPushButton, QSizePolicy,
                              QSplitter, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget)

from material_catalog import ASSUMED_PROPERTIES, catalog_materials, save_custom_material
from material_library import MaterialCard, PROPERTY_UNITS
from studio_style import (CONTROL_HEIGHT, CompactDoubleSpinBox, configure_form,
                          field_label, stepper_with_unit)
from ui_language import translate


PROPERTY_LABELS = {
    "density": "Density", "young_modulus": "Young's modulus", "poisson_ratio": "Poisson ratio",
    "yield_stress": "Yield stress", "tangent_modulus": "Tangent modulus",
    "conductivity": "Thermal conductivity", "heat_capacity": "Specific heat capacity",
    "thermal_expansion": "Thermal expansion", "dynamic_viscosity": "Dynamic viscosity",
}

SOLID_PROPERTIES = ("density", "young_modulus", "poisson_ratio", "conductivity",
                    "heat_capacity", "thermal_expansion")
FLUID_PROPERTIES = ("density", "dynamic_viscosity", "conductivity", "heat_capacity")


def library_button(label, *, primary=False):
    button = QPushButton(label)
    button.setObjectName("secondaryActionBtn")
    button.setProperty("primary", primary)
    button.setAutoDefault(False)
    button.setDefault(False)
    button.setMinimumWidth(76)
    button.setFixedHeight(CONTROL_HEIGHT)
    button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return button


class MaterialDraft(QWidget):
    def __init__(self, kind, required):
        super().__init__()
        self.setObjectName("materialLibraryDraft")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.kind = kind
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(QLabel("New material"))
        form = QFormLayout()
        configure_form(form)
        layout.addLayout(form)
        self.name = QLineEdit()
        form.addRow(field_label("Material name", self.name), self.name)
        self.fields = {}
        self.optional = {}
        for key in SOLID_PROPERTIES if kind == "solid" else FLUID_PROPERTIES:
            label = PROPERTY_LABELS[key]
            control = CompactDoubleSpinBox()
            control.setAccessibleName(label)
            minimum, maximum, step = {
                "poisson_ratio": (-1, .5, .01),
                "thermal_expansion": (-1, 1, .000001),
                "dynamic_viscosity": (0, 1e9, .00001),
            }.get(key, (0, 1e9, 1))
            control.setRange(minimum, maximum)
            control.setSingleStep(step)
            self.fields[key] = control
            unit = "MPa" if key == "young_modulus" else PROPERTY_UNITS[key]
            row = stepper_with_unit(control, unit)
            if key in required:
                form.addRow(field_label(label, control), row)
            else:
                toggle = QCheckBox(label)
                self.optional[key] = toggle
                toggle.toggled.connect(control.setEnabled)
                control.setEnabled(False)
                form.addRow(toggle, row)
        layout.addStretch()

    def reset(self):
        self.name.clear()
        for control in self.fields.values():
            control.setValue(0)
        for toggle in self.optional.values():
            toggle.setChecked(False)

    def card(self):
        if not self.name.text().strip():
            raise ValueError("Enter a material name.")
        properties = {}
        for key, control in self.fields.items():
            if not control.isEnabled():
                continue
            control.interpretText()
            properties[key] = control.value() * (1e6 if key == "young_modulus" else 1)
        return MaterialCard(self.name.text(), self.kind, properties)


class MaterialLibraryDialog(QDialog):
    material_added = Signal(object)

    def __init__(self, parent=None, *, kind="solid", required=None, selected_name="",
                 library_settings=None):
        super().__init__(parent)
        self.setObjectName("materialLibraryDialog")
        self.setWindowTitle("Material library")
        self.resize(900, 620)
        self.setSizeGripEnabled(True)
        self.kind = kind
        self.library_settings = (library_settings if library_settings is not None
                                 else QSettings("RustMech", "RustMech"))
        default_required = (("density", "young_modulus", "poisson_ratio") if kind == "solid"
                            else ("density", "dynamic_viscosity"))
        self.required = tuple(required if required is not None else default_required)
        self.entries = []
        self.setStyleSheet("""
            QDialog#materialLibraryDialog { background: #F5F5F3; }
            QLabel#materialLibraryTitle { background: #BFDBFE; border: 1px solid #2563EB;
                border-radius: 6px; color: #0F172A; font-size: 14px; padding: 0 6px; }
            QWidget#materialLibraryListPanel, QWidget#materialLibraryDraft { background: #FCFCFB;
                border: 1px solid #D5DBE2; border-radius: 7px; }
            QListWidget#materialLibraryList { background: #FCFCFB; border: none; font-size: 11px; }
            QListWidget#materialLibraryList::item { padding: 6px 4px; border-radius: 3px; }
            QListWidget#materialLibraryList::item:selected { background: #BFDBFE; color: #0F172A; }
            QListWidget#materialLibraryList::item:hover:!selected { background: #E5ECF4; }
            QTextBrowser#materialLibraryDetails, QTextBrowser#materialLibrarySources { background: #FFFFFF;
                border: 1px solid #D5DBE2; border-radius: 7px; padding: 7px; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(7)
        title = QLabel("Material library")
        title.setObjectName("materialLibraryTitle")
        title.setFixedHeight(30)
        layout.addWidget(title)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("materialLibrarySplitter")
        self.splitter.setChildrenCollapsible(False)
        left = QWidget()
        left.setObjectName("materialLibraryListPanel")
        left.setMinimumWidth(170)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(6, 6, 6, 6)
        left_layout.setSpacing(3)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search materials...")
        self.search.setAccessibleName("Search materials")
        left_layout.addWidget(self.search)
        self.material_list = QListWidget()
        self.material_list.setObjectName("materialLibraryList")
        self.material_list.setAccessibleName("Materials")
        self.material_list.setProperty("preserveItemText", True)
        left_layout.addWidget(self.material_list, 1)
        self.new_button = library_button("Add", primary=True)
        self.new_button.setAccessibleName("Add material")
        left_layout.addWidget(self.new_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.details = QTextBrowser()
        self.details.setObjectName("materialLibraryDetails")
        self.details.setAccessibleName("Material properties and sources")
        self.details.setOpenExternalLinks(True)
        self.sources = QTextBrowser()
        self.sources.setObjectName("materialLibrarySources")
        self.sources.setAccessibleName("Datasheets and references")
        self.sources.setOpenExternalLinks(True)
        self.details_splitter = QSplitter(Qt.Orientation.Vertical)
        self.details_splitter.setObjectName("materialDetailsSplitter")
        self.details_splitter.setChildrenCollapsible(False)
        self.details_splitter.addWidget(self.details)
        self.details_splitter.addWidget(self.sources)
        self.details_splitter.setSizes([390, 150])
        self.draft = MaterialDraft(kind, self.required)
        self.right_panel = QStackedWidget()
        self.right_panel.addWidget(self.details_splitter)
        self.right_panel.addWidget(self.draft)
        self.splitter.addWidget(left)
        self.splitter.addWidget(self.right_panel)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([280, 600])
        layout.addWidget(self.splitter, 1)
        footer = QHBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setObjectName("mutedHintLabel")
        footer.addWidget(self.status, 1)
        self.add_button = library_button("Use", primary=True)
        self.close_button = library_button("Close")
        self.save_button = library_button("Save", primary=True)
        self.cancel_button = library_button("Cancel")
        for button in (self.add_button, self.close_button, self.save_button, self.cancel_button):
            footer.addWidget(button)
        layout.addLayout(footer)
        self.close_button.clicked.connect(self.reject)
        self.add_button.clicked.connect(self.add_selected)
        self.new_button.clicked.connect(self.begin_draft)
        self.save_button.clicked.connect(self.save_draft)
        self.cancel_button.clicked.connect(self.end_draft)
        self.finished.connect(self.end_draft)
        self.material_list.currentRowChanged.connect(self.show_material)
        self.search.textChanged.connect(self.filter_materials)
        self.refresh_materials(selected_name=selected_name)
        self.end_draft()
        manager = getattr(QApplication.instance(), "_rustmech_language", None)
        if manager is not None:
            manager.changed.connect(self.show_material)

    def refresh_materials(self, *, selected_id=None, selected_name=""):
        current = self.selected_entry()
        if selected_id is None and current is not None:
            selected_id = current.identifier
        entries = [entry for entry in catalog_materials(self.library_settings)
                   if entry.kind == self.kind]
        if entries != self.entries:
            self.material_list.clear()
            self.entries = entries
            for entry in entries:
                self.material_list.addItem(entry.name)
                self.material_list.item(self.material_list.count() - 1).setToolTip(entry.condition)
        row = next((i for i, entry in enumerate(self.entries)
                    if entry.identifier == selected_id or entry.name == selected_name), 0)
        self.material_list.setCurrentRow(row)
        self.filter_materials(self.search.text())

    def showEvent(self, event):
        self.refresh_materials()
        super().showEvent(event)

    def begin_draft(self):
        self.draft.reset()
        self.right_panel.setCurrentWidget(self.draft)
        for widget in (self.search, self.material_list, self.new_button):
            widget.setEnabled(False)
        self.add_button.hide()
        self.close_button.hide()
        self.save_button.show()
        self.cancel_button.show()
        self.status.clear()
        self.draft.name.setFocus()

    def end_draft(self, *_):
        was_editing = self.right_panel.currentWidget() is self.draft
        self.right_panel.setCurrentWidget(self.details_splitter)
        for widget in (self.search, self.material_list, self.new_button):
            widget.setEnabled(True)
        self.save_button.hide()
        self.cancel_button.hide()
        self.add_button.show()
        self.close_button.show()
        self.show_material()
        if was_editing and self.isVisible():
            self.new_button.setFocus()

    def save_draft(self):
        try:
            identifier = save_custom_material(self.draft.card(), self.library_settings)
        except (OSError, ValueError) as error:
            self.status.setText(translate(str(error)))
            return
        self.end_draft()
        self.search.clear()
        self.refresh_materials(selected_id=identifier)
        self.status.setText(translate("Material saved to the library."))
        self.material_list.setFocus()

    def selected_entry(self):
        row = self.material_list.currentRow()
        return self.entries[row] if row >= 0 and not self.material_list.item(row).isHidden() else None

    def filter_materials(self, text):
        text = text.casefold().strip()
        for index, entry in enumerate(self.entries):
            self.material_list.item(index).setHidden(text not in
                f"{entry.name} {entry.category} {entry.condition}".casefold())
        if self.selected_entry() is None:
            row = next((i for i in range(len(self.entries)) if not self.material_list.item(i).isHidden()), -1)
            self.material_list.setCurrentRow(row)
        self.show_material()

    def show_material(self, *_):
        if self.right_panel.currentWidget() is self.draft:
            return
        entry = self.selected_entry()
        self.add_button.setEnabled(False)
        if entry is None:
            self.details.clear()
            self.sources.clear()
            self.status.setText(translate("No matching materials."))
            return
        t = lambda value: escape(translate(value))
        html = [f"<h3>{escape(entry.name)}</h3><p>{t(entry.category)}"]
        if entry.user_card is None:
            html.append(f"<br>{escape(entry.condition)}</p>"
                        f"<p>{t('Reference temperature')}: {entry.reference_temperature_K - 273.15:g} °C<br>"
                        f"{t('Constant properties. Solids use linear elasticity; no plastic curve is supplied.')}")
        html.append("</p>")
        if entry.assumptions:
            html.append(f"<p style='color:#92400E'>{t('Includes engineering estimates; see marked values and assumptions below.')}</p>")
        html.append(f"<h4>{t('Properties')}</h4><table width='100%' cellspacing='0' cellpadding='5'>")
        for index, (key, label) in enumerate(PROPERTY_LABELS.items()):
            if key == "dynamic_viscosity" and entry.kind == "solid":
                continue
            if entry.kind == "fluid" and key in ("young_modulus", "poisson_ratio", "yield_stress", "tangent_modulus", "thermal_expansion"):
                continue
            value = entry.properties.get(key)
            unit = PROPERTY_UNITS[key]
            if isinstance(value, (int, float)) and key in ("young_modulus", "yield_stress", "tangent_modulus"):
                value, unit = value / 1e6, "MPa"
            if isinstance(value, dict):
                display = t("Temperature-dependent")
            else:
                display = t("Not supplied") if value is None else f"{value:.7g} {escape(unit)}"
            if key in ASSUMED_PROPERTIES.get(entry.identifier, ()):
                display += f" <span style='color:#92400E'>({t('estimate')})</span>"
            colour = "#F1F5F9" if index % 2 == 0 else "#FFFFFF"
            html.append(f"<tr bgcolor='{colour}'><td>{t(label)}</td><td>{display}</td></tr>")
        html.append("</table>")
        if entry.assumptions:
            html.append(f"<h4>{t('Engineering assumptions')}</h4><p>{escape(entry.assumptions)}</p>")
        if entry.notes:
            html.append(f"<h4>{t('Conditions and limitations')}</h4><p>{escape(entry.notes)}</p>")
        source_html = [f"<h4>{t('Datasheets and references')}</h4>"]
        for title, url, location in entry.references:
            source_html.append(f'<p><a href="{escape(url, quote=True)}">{escape(title)}</a><br>{escape(location)}</p>')
        self.details.setHtml("".join(html))
        self.sources.setHtml("".join(source_html))
        missing = set(self.required) - entry.properties.keys()
        if missing:
            self.status.setText(translate("Required properties not supplied:") + " " +
                                ", ".join(translate(PROPERTY_LABELS[key]) for key in sorted(missing)))
        else:
            self.add_button.setEnabled(True)
            self.status.clear()

    def add_selected(self):
        entry = self.selected_entry()
        if entry is not None and self.add_button.isEnabled():
            self.material_added.emit(entry.card())
            self.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # Search/list Enter must not implicitly add a material or close.
            event.accept()
        elif event.key() == Qt.Key.Key_Escape and self.right_panel.currentWidget() is self.draft:
            self.end_draft()
            event.accept()
        else:
            super().keyPressEvent(event)
