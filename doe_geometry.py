"""Preview library geometries before including them in an experiment plan."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QSplitter, QVBoxLayout, QWidget,
)

from model import validate_geometry
from studio_style import (
    CompactComboBox, CompactDoubleSpinBox, CONTROL_HEIGHT, NUMERIC_FIELD_WIDTH,
    STUDIO_STYLESHEET,
)
from structure_library import (
    ALL_STRUCTURES_BY_FAMILY, STRUCTURE_FAMILIES, TPMS_MORPHOLOGIES,
)


class GeometryLibraryDialog(QDialog):
    def __init__(self, settings, surface_builder, parent=None):
        super().__init__(parent)
        from window_state import install_window_state
        from PySide6.QtWidgets import QApplication
        install_window_state(QApplication.instance())
        self.setObjectName("doeGeometryLibrary")
        self.setWindowTitle("Geometry · library preview")
        self.setStyleSheet(STUDIO_STYLESHEET)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True)
        self.resize(760, 780)
        self.setMinimumSize(520, 480)
        self._base = replace(settings, imported_geometry_path="", imported_geometry_name="",
                             test_profile="custom", specimen_architecture="porous_all")
        self._builder = surface_builder
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="doe-library")
        self._future = None
        self._revision = 0
        self._ready_revision = -1
        self._disposed = False
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setObjectName("geometryPreviewParameters")
        self.splitter.setChildrenCollapsible(False)
        root.addWidget(self.splitter, 1)

        preview = QFrame()
        preview.setObjectName("viewportContainer")
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(1, 1, 1, 1)
        preview_layout.setSpacing(0)
        toolbar = QFrame()
        toolbar.setObjectName("viewportToolbar")
        tools = QHBoxLayout(toolbar)
        tools.setContentsMargins(8, 6, 8, 6)
        tools.setSpacing(6)
        tools.addWidget(QLabel("Geometry preview"))
        tools.addStretch(1)
        for text, view in (("3D", "iso"), ("Top", "top"), ("Front", "front")):
            button = QPushButton(text)
            button.setAutoDefault(False)
            button.setDefault(False)
            button.setFixedSize(60, CONTROL_HEIGHT)
            button.clicked.connect(lambda _checked=False, view=view: self._set_view(view))
            tools.addWidget(button)
        preview_layout.addWidget(toolbar)
        from preview_viewport import create_preview_viewport
        self.plotter = create_preview_viewport(self)
        self.plotter.set_background("#f0f0f0", top="#ffffff")
        self.plotter.interactor.setMinimumSize(240, 180)
        preview_layout.addWidget(self.plotter.interactor, 1)
        hint = QLabel("Drag to rotate · Scroll to zoom · Middle-drag to pan")
        hint.setObjectName("doePreviewHint")
        hint.setWordWrap(True)
        preview_layout.addWidget(hint)
        self.splitter.addWidget(preview)

        parameters = QWidget()
        form = QFormLayout(parameters)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(6)
        self.family = CompactComboBox()
        for key, label in STRUCTURE_FAMILIES.items():
            self.family.addItem(label, key)
        self.structure = CompactComboBox()
        self.morphology = CompactComboBox()
        for key, label in TPMS_MORPHOLOGIES.items():
            self.morphology.addItem(label, key)
        form.addRow("Family", self.family)
        form.addRow("Geometry", self.structure)
        form.addRow("Morphology", self.morphology)
        self.values = {}
        for name, label, lower, upper in (
            ("size", "Size (mm)", 1, 100), ("cell_size", "Cell size (mm)", .1, 100),
            ("solid_fraction", "Relative density (%)", 1, 99),
        ):
            spin = CompactDoubleSpinBox()
            spin.setRange(lower, upper)
            spin.setValue(getattr(settings, name))
            spin.setFixedWidth(NUMERIC_FIELD_WIDTH)
            spin.setKeyboardTracking(False)
            self.values[name] = spin
            form.addRow(label, spin)
            spin.valueChanged.connect(self._draft_changed)
        parameter_scroll = QScrollArea()
        parameter_scroll.setWidgetResizable(True)
        parameter_scroll.setWidget(parameters)
        parameter_scroll.setMinimumHeight(120)
        self.splitter.addWidget(parameter_scroll)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([450, 230])
        self.status = QLabel("Choose a geometry to preview.")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setObjectName("doePreviewStatus")
        self.status.setWordWrap(True)
        # Reserve two lines so a status change does not resize the preview splitter.
        self.status.setMinimumHeight(2 * self.status.fontMetrics().lineSpacing())
        root.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = self.buttons.addButton("Apply", QDialogButtonBox.ButtonRole.ApplyRole)
        self.apply_button.clicked.connect(self._start_preview)
        self.confirm = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.confirm.setText("Include geometry")
        self.confirm.setEnabled(False)
        button_width = max(
            max(button.sizeHint().width(), button.fontMetrics().horizontalAdvance(button.text()) + 28)
            for button in self.buttons.buttons()
        )
        for button in self.buttons.buttons():
            button.setAutoDefault(False)
            button.setDefault(False)
            button.setFixedSize(button_width, CONTROL_HEIGHT)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        from operation_progress import OperationProgress
        self.operation_progress = OperationProgress(self, provider=lambda: (
            self._future is not None, self.status.text(),
        ))
        root.addWidget(self.operation_progress)
        self._poll_timer = QTimer(self, interval=60)
        self._poll_timer.timeout.connect(self._poll)
        self.family.currentIndexChanged.connect(self._populate)
        self.structure.currentIndexChanged.connect(self._draft_changed)
        self.morphology.currentIndexChanged.connect(self._draft_changed)
        self.family.setCurrentIndex(max(0, self.family.findData(settings.structure_family)))
        self._populate()
        self.structure.setCurrentText(settings.structure_name)
        self.morphology.setCurrentIndex(max(0, self.morphology.findData(settings.tpms_morphology)))

    def selected_settings(self):
        return replace(self._base, structure_family=self.family.currentData(),
                       structure_name=self.structure.currentText(),
                       tpms_morphology=self.morphology.currentData(),
                       **{name: spin.value() for name, spin in self.values.items()})

    def _populate(self, *_):
        self.structure.blockSignals(True)
        self.structure.clear()
        self.structure.addItems(ALL_STRUCTURES_BY_FAMILY[self.family.currentData()])
        self.structure.blockSignals(False)
        self.morphology.setEnabled(self.family.currentData() == "tpms")
        self._draft_changed()

    def _draft_changed(self, *_):
        self._revision += 1
        self.confirm.setEnabled(False)
        self.status.setText("Click Apply to generate geometry.")

    def _start_preview(self):
        if self._disposed or self._future is not None:
            return
        for spin in self.values.values():
            spin.interpretText()
        settings = self.selected_settings()
        errors = validate_geometry(settings, include_mesh=False)
        self.plotter.clear()
        self.plotter.render()
        if errors:
            self.status.setText("\n".join(errors))
            return
        self.apply_button.setEnabled(False)
        self.confirm.setEnabled(False)
        self._ready_revision = -1
        self.status.setText("Updating geometry preview…")
        self._future = (self._revision, self._executor.submit(self._builder, settings))
        self._poll_timer.start()

    def _poll(self):
        revision, future = self._future
        if not future.done():
            return
        self._future = None
        self._poll_timer.stop()
        self.apply_button.setEnabled(True)
        if revision != self._revision:
            return
        try:
            surface = future.result()
            if not surface.n_cells:
                raise ValueError("Geometry is empty; adjust its parameters.")
            # clear() removes the light kit; restore the main viewport's lighting
            # and material on every completed geometry build.
            self.plotter.enable_lightkit()
            self.plotter.set_background("#f0f0f0", top="#ffffff")
            self.plotter.add_mesh(surface, color="#4ea8de", smooth_shading=True,
                                  ambient=0.22, specular=0.25, specular_power=20)
            self.plotter.add_axes(interactive=False)
            self._set_view("iso")
            self._ready_revision = revision
            self.status.setText("Rotate to inspect. Include this geometry to configure its mesh and variables.")
            self.confirm.setEnabled(True)
        except Exception as error:
            self.status.setText(f"Preview unavailable: {error}")

    def _set_view(self, view):
        if self._disposed:
            return
        self.plotter.enable_parallel_projection()
        if view == "top":
            self.plotter.view_xy()
        elif view == "front":
            self.plotter.view_xz()
        else:
            self.plotter.view_isometric()
        self.plotter.reset_camera()
        self.plotter.render()

    def accept(self):
        if self._ready_revision == self._revision:
            super().accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
            return
        super().keyPressEvent(event)

    def done(self, result):
        self._dispose()
        super().done(result)

    def closeEvent(self, event):
        self._dispose()
        super().closeEvent(event)

    def _dispose(self, *_):
        if self._disposed:
            return
        self._disposed = True
        self._poll_timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self.plotter.close()
