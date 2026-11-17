"""Native, read-only material catalogue with inspectable source links."""
from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                              QPushButton, QSplitter, QTextBrowser, QVBoxLayout, QWidget)

from material_catalog import ASSUMED_PROPERTIES, builtin_materials
from material_library import PROPERTY_UNITS
from studio_style import CONTROL_HEIGHT
from ui_language import translate


PROPERTY_LABELS = {
    "density": "Density", "young_modulus": "Young's modulus", "poisson_ratio": "Poisson ratio",
    "yield_stress": "Yield stress", "tangent_modulus": "Tangent modulus",
    "conductivity": "Thermal conductivity", "heat_capacity": "Specific heat capacity",
    "thermal_expansion": "Thermal expansion", "dynamic_viscosity": "Dynamic viscosity",
}


class MaterialLibraryDialog(QDialog):
    material_added = Signal(object)

    def __init__(self, parent=None, *, kind="solid", required=None, selected_name=""):
        super().__init__(parent)
        self.setObjectName("materialLibraryDialog")
        self.setWindowTitle("Material library")
        self.resize(900, 620)
        self.setSizeGripEnabled(True)
        self.required = tuple(required if required is not None else
                              ("density", "young_modulus", "poisson_ratio"))
        self.entries = [entry for entry in builtin_materials() if entry.kind == kind]
        self.setStyleSheet("""
            QDialog#materialLibraryDialog { background: #F5F5F3; }
            QLabel#materialLibraryTitle { background: #BFDBFE; border: 1px solid #2563EB;
                border-radius: 6px; color: #0F172A; font-size: 14px; padding: 0 6px; }
            QWidget#materialLibraryListPanel { background: #FCFCFB;
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
        for entry in self.entries:
            self.material_list.addItem(entry.name)
            self.material_list.item(self.material_list.count()-1).setToolTip(entry.condition)
        left_layout.addWidget(self.material_list, 1)
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
        self.splitter.addWidget(left)
        self.splitter.addWidget(self.details_splitter)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([280, 600])
        layout.addWidget(self.splitter, 1)
        footer = QHBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setObjectName("mutedHintLabel")
        footer.addWidget(self.status, 1)
        self.add_button = QPushButton("Add")
        self.add_button.setProperty("primary", True)
        self.close_button = QPushButton("Close")
        for button in (self.add_button, self.close_button):
            button.setAutoDefault(False)
            button.setDefault(False)
            button.setObjectName("secondaryActionBtn")
            button.setFixedSize(76, CONTROL_HEIGHT)
            footer.addWidget(button)
        layout.addLayout(footer)
        self.close_button.clicked.connect(self.reject)
        self.add_button.clicked.connect(self.add_selected)
        self.material_list.currentRowChanged.connect(self.show_material)
        self.search.textChanged.connect(self.filter_materials)
        row = next((i for i, entry in enumerate(self.entries) if entry.name == selected_name), 0)
        self.material_list.setCurrentRow(row)
        self.show_material()
        manager = getattr(QApplication.instance(), "_rustmech_language", None)
        if manager is not None:
            manager.changed.connect(self.show_material)

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
        entry = self.selected_entry()
        self.add_button.setEnabled(False)
        if entry is None:
            self.details.clear()
            self.sources.clear()
            self.status.setText(translate("No matching materials."))
            return
        t = lambda value: escape(translate(value))
        html = [f"<h3>{escape(entry.name)}</h3><p>{escape(entry.category)}<br>{escape(entry.condition)}</p>",
                f"<p>{t('Reference temperature')}: {entry.reference_temperature_K - 273.15:g} °C<br>",
                f"{t('Constant properties. Solids use linear elasticity; no plastic curve is supplied.')}</p>"]
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
            if value is not None and key in ("young_modulus", "yield_stress", "tangent_modulus"):
                value, unit = value / 1e6, "MPa"
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
            self.status.setText(translate("Add selects this material. Analysis does not start."))

    def add_selected(self):
        entry = self.selected_entry()
        if entry is not None and self.add_button.isEnabled():
            self.material_added.emit(entry.card())
            self.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # Search/list Enter must not implicitly add a material or close.
            event.accept()
        else:
            super().keyPressEvent(event)
