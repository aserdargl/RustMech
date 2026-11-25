"""Preview library geometries before including them in an experiment plan."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from PySide6.QtCore import QSize, QTimer, Qt, Signal
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
    resolve_family_and_name,
)


class ActiveGeometryPanel(QWidget):
    """Apply builds a surface preview; Add snapshots the current parameters."""

    geometry_added = Signal(object)

    def __init__(self, settings_provider, builder_provider, parent=None, *, embedded=False):
        super().__init__(parent)
        self._settings_provider = settings_provider
        self._builder_provider = builder_provider
        self._settings = None
        self._requested_settings = None
        self._build_pending = False
        self._source_settings = None
        self._base = None
        self._loading = False
        self._ready = None
        self._future = None
        self._disposed = False
        self._cancel_event = None
        self._progress_status = None
        self._embedded = embedded
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="active-geometry")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setObjectName("doeNewGeometrySections" if embedded else "doeActiveGeometrySections")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(3)
        layout.addWidget(self.splitter, 1)
        preview = QFrame()
        preview.setObjectName("viewportContainer")
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(4, 4, 4, 4)
        preview_layout.setSpacing(4)
        self.details = QLabel()
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.details.setWordWrap(True)
        preview_layout.addWidget(self.details)
        self.details.setVisible(not embedded)
        views = QHBoxLayout()
        views.setSpacing(4)
        for text, view in (() if embedded else (("3D", "iso"), ("Top", "top"), ("Front", "front"))):
            button = QPushButton(text)
            button.setFixedSize(60, CONTROL_HEIGHT)
            button.setAutoDefault(False)
            button.setDefault(False)
            button.clicked.connect(lambda _checked=False, view=view: self._set_view(view))
            views.addWidget(button)
        views.addStretch()
        preview_layout.addLayout(views)
        self.preview_host = QWidget()
        self.preview_host.setMinimumSize(240, 200)
        self.preview_layout = QVBoxLayout(self.preview_host)
        self.preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.addWidget(self.preview_host, 1)
        self.plotter = None
        if not embedded:
            self._ensure_plotter()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        preview_layout.addWidget(self.status)
        self.splitter.addWidget(preview)

        parameters = QWidget()
        parameter_layout = QVBoxLayout(parameters)
        parameter_layout.setContentsMargins(4, 6, 4, 4)
        parameter_layout.setSpacing(4)
        parameter_layout.addWidget(QLabel("Structure / Dimensions"))
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(4)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        self.family = CompactComboBox()
        self.structure = CompactComboBox()
        self.morphology = CompactComboBox()
        for key, label in STRUCTURE_FAMILIES.items():
            self.family.addItem(label, key)
        for key, label in TPMS_MORPHOLOGIES.items():
            self.morphology.addItem(label, key)
        form.addRow("Structure family", self.family)
        form.addRow("Structure type", self.structure)
        form.addRow("Morphology", self.morphology)
        self.family.currentIndexChanged.connect(self._family_changed)
        self.structure.currentIndexChanged.connect(self._dimensions_changed)
        self.morphology.currentIndexChanged.connect(self._dimensions_changed)
        self.values = {}
        for name, label, lower, upper in (
            ("size", "Size (mm)", 1, 100),
            ("cell_size", "Cell size (mm)", .1, 100),
            ("solid_fraction", "Density (%)", 1, 99),
        ):
            spin = CompactDoubleSpinBox()
            spin.setRange(lower, upper)
            self.values[name] = spin
            form.addRow(label, spin)
            spin.valueChanged.connect(self._dimensions_changed)
        parameter_layout.addLayout(form)
        actions = QHBoxLayout()
        actions.setSpacing(4)
        self.apply_button = QPushButton("Apply")
        self.apply_button.setFixedSize(64, CONTROL_HEIGHT)
        self.apply_button.setAutoDefault(False)
        self.apply_button.setDefault(False)
        self.apply_button.setToolTip("Generate the geometry preview using the entered structure and dimensions.")
        self.apply_button.clicked.connect(self._apply)
        actions.addWidget(self.apply_button)
        self.add_button = QPushButton("Add")
        self.add_button.setFixedSize(64, CONTROL_HEIGHT)
        self.add_button.setAutoDefault(False)
        self.add_button.setDefault(False)
        self.add_button.setToolTip("Add the current structure and dimensions to DoE without generating an FE mesh.")
        self.add_button.setEnabled(False)
        self.add_button.clicked.connect(self._add)
        actions.addWidget(self.add_button)
        actions.addStretch()
        parameter_layout.addLayout(actions)
        parameter_layout.addStretch()
        parameter_scroll = QScrollArea()
        parameter_scroll.setWidgetResizable(True)
        parameter_scroll.setWidget(parameters)
        parameter_scroll.setMinimumHeight(270 if embedded else 140)
        self.splitter.addWidget(parameter_scroll)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([260, 280] if embedded else [440, 190])
        self.timer = QTimer(self, interval=200)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

        if not embedded:
            from operation_progress import OperationProgress
            self.operation_progress = OperationProgress(self, provider=lambda: (
                self._future is not None, self.status.text(),
            ))
            layout.addWidget(self.operation_progress)

    def _ensure_plotter(self):
        if self.plotter is None:
            from preview_viewport import create_preview_viewport
            self.plotter = create_preview_viewport(self)
            self.plotter.set_background("#e9eef4", top="#ffffff")
            self.plotter.enable_anti_aliasing("fxaa")
            self.plotter.interactor.setMinimumSize(240, 200)
            self.preview_layout.addWidget(self.plotter.interactor)

    def sizeHint(self):
        return QSize(300, 650)

    def showEvent(self, event):
        super().showEvent(event)
        if not self.property("splitterPreferencesRestored"):
            self.setProperty("splitterPreferencesRestored", True)
            # This dock is created on demand, after the main window restoration.
            from PySide6.QtWidgets import QApplication
            store = getattr(QApplication.instance(), "_rustmech_window_state", None)
            if store is not None:
                state = store.settings.value(
                    f"{store.key(self.window())}/splitters/{self.splitter.objectName()}")
                if state is not None:
                    self.splitter.restoreState(state)

    def _sync_source(self):
        source = self._settings_provider()
        if source == self._source_settings:
            return
        self._source_settings = replace(source)
        self._requested_settings = replace(source)
        self._base = replace(source)
        self._loading = True
        try:
            imported = bool(source.imported_geometry_path)
            standard = source.test_profile != "custom"
            family, name = resolve_family_and_name(source.structure_family, source.structure_name)
            self.family.setCurrentIndex(self.family.findData(family))
            self._populate_structures(name)
            self.morphology.setCurrentIndex(max(0, self.morphology.findData(source.tpms_morphology)))
            for combo in (self.family, self.structure, self.morphology):
                combo.setEnabled(not imported)
                combo.setToolTip("Structure is fixed for imported geometry." if imported else "")
            self.morphology.setEnabled(not imported and family != "lattice")
            for name, spin in self.values.items():
                spin.setValue(getattr(source, name))
                spin.setEnabled(not imported and not standard)
                spin.setToolTip("Dimensions are fixed for imported and standard specimens."
                                if imported or standard else "")
        finally:
            self._loading = False

    def selected_settings(self):
        self._sync_source()
        structure = {} if self._base.imported_geometry_path else dict(
            structure_family=self.family.currentData(),
            structure_name=self.structure.currentText(),
            tpms_morphology=("sheet" if self.family.currentData() == "lattice"
                             else self.morphology.currentData()),
        )
        return replace(self._base, **structure,
                       **{name: spin.value() for name, spin in self.values.items()})

    def restore_draft(self, settings):
        """Restore parameter inputs without submitting a preview or analysis."""
        if self._cancel_event is not None:
            self._cancel_event.set()
        if self._future is not None:
            self._future[1].cancel()
        self._future = None
        snapshot = replace(settings)
        self._settings_provider = lambda: snapshot
        self._source_settings = None
        self._sync_source()
        self._settings = replace(snapshot)
        self._ready = None
        self._build_pending = False
        if self.plotter is not None:
            self.plotter.clear()
            self.plotter.render()
        self.status.setText('Plan opened. Click Apply to preview geometry.')

    def _populate_structures(self, selected_name=""):
        self.structure.blockSignals(True)
        try:
            self.structure.clear()
            self.structure.addItems(ALL_STRUCTURES_BY_FAMILY[self.family.currentData()])
            index = self.structure.findText(selected_name)
            self.structure.setCurrentIndex(max(0, index))
        finally:
            self.structure.blockSignals(False)

    def _family_changed(self, *_):
        self._populate_structures()
        self.morphology.setEnabled(self.family.currentData() != "lattice")
        self._dimensions_changed()

    def _dimensions_changed(self, *_):
        if not self._loading:
            self.add_button.setEnabled(not validate_geometry(self.selected_settings(), include_mesh=False))
            self.refresh()

    def _apply(self):
        for spin in self.values.values():
            spin.interpretText()
        settings = self.selected_settings()
        errors = validate_geometry(settings, include_mesh=False)
        if errors:
            self.status.setText("\n".join(errors))
            return
        self._requested_settings = replace(settings)
        # Allow an explicit retry after an unavailable or failed preview.
        self._build_pending = True
        self.refresh()

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
        self.plotter.reset_camera_clipping_range()
        self.plotter.render()

    def refresh(self):
        if self._disposed or not self.isVisible():
            return
        self._ensure_plotter()
        settings = self.selected_settings()
        # Inclusion depends on the parameter snapshot, never on a completed
        # surface preview or volume mesh. A slow/failed preview must not block DoE.
        errors = validate_geometry(settings, include_mesh=False)
        self.add_button.setEnabled(not errors)
        settings = self._requested_settings
        if settings != self._settings:
            self._settings = replace(settings)
            self._build_pending = True
            self._ready = None
            self.plotter.clear()
            self.plotter.render()
            self.details.setText(
                f"{settings.imported_geometry_name or settings.structure_name}\n"
                f"Size {settings.size:g} mm · Cell {settings.cell_size:g} mm\n"
                f"Density {settings.solid_fraction:g}% · {settings.tpms_morphology}")
            self.status.setText("Updating geometry…")
        if self._future is not None:
            snapshot, future = self._future
            if not future.done():
                if self._progress_status is not None:
                    stage = self._progress_status()
                    if isinstance(stage, str):
                        self.status.setText(stage)
                return
            self._future = None
            if snapshot == self._settings:
                try:
                    surface = future.result()
                    if not surface.n_cells:
                        raise ValueError("Geometry is empty; adjust its parameters.")
                    # clear() removes the lights too: restore them for every build.
                    self.plotter.enable_lightkit()
                    self.plotter.set_background("#e9eef4", top="#ffffff")
                    self.plotter.add_mesh(surface, color="#699bc4", smooth_shading=True,
                                          split_sharp_edges=True, feature_angle=45,
                                          ambient=0.22, diffuse=0.78, specular=0.2,
                                          specular_power=24)
                    self.plotter.add_axes(interactive=False)
                    self._set_view("iso")
                    self._ready = snapshot
                    self.status.setText("Ready")
                except Exception as error:
                    self.status.setText(f"Preview unavailable: {error}")
                return
        if not self._build_pending:
            if errors:
                self.status.setText("\n".join(errors))
            elif self.selected_settings() != self._settings:
                self.status.setText("Dimensions changed. Click Apply to generate geometry.")
            return
        preview_errors = validate_geometry(settings, include_mesh=False)
        if preview_errors:
            self._build_pending = False
            self.status.setText("\n".join(preview_errors))
            return
        builder = self._builder_provider()
        self._build_pending = False
        if builder is None:
            self.status.setText("Geometry preview unavailable.")
            return
        self._cancel_event = getattr(builder, "cancel_event", None)
        self._progress_status = getattr(builder, "progress_status", None)
        snapshot = replace(self._settings)
        self._future = (snapshot, self._executor.submit(builder, snapshot))

    def _add(self):
        # Commit a numeric edit even if the user clicks Add before pressing Enter.
        for spin in self.values.values():
            spin.interpretText()
        settings = self.selected_settings()
        errors = validate_geometry(settings, include_mesh=False)
        self.add_button.setEnabled(not errors)
        if errors:
            self.status.setText("\n".join(errors))
            return
        self.geometry_added.emit(replace(settings))

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self.timer.stop()
        if self._cancel_event is not None:
            self._cancel_event.set()
        self._executor.shutdown(wait=False, cancel_futures=True)
        if self.plotter is not None:
            self.plotter.close()


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
            spin.valueChanged.connect(self._schedule)
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
        self._debounce = QTimer(self, singleShot=True, interval=200)
        self._debounce.timeout.connect(self._start_preview)
        self._poll_timer = QTimer(self, interval=60)
        self._poll_timer.timeout.connect(self._poll)
        self.family.currentIndexChanged.connect(self._populate)
        self.structure.currentIndexChanged.connect(self._schedule)
        self.morphology.currentIndexChanged.connect(self._schedule)
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
        self._schedule()

    def _schedule(self, *_):
        self._revision += 1
        self.confirm.setEnabled(False)
        self.status.setText("Updating geometry preview…")
        self._debounce.start()

    def _start_preview(self):
        if self._disposed:
            return
        if self._future is not None:
            self._debounce.start()
            return
        settings = self.selected_settings()
        errors = validate_geometry(settings, include_mesh=False)
        self.plotter.clear()
        self.plotter.render()
        if errors:
            self.status.setText("\n".join(errors))
            return
        self._future = (self._revision, self._executor.submit(self._builder, settings))
        self._poll_timer.start()

    def _poll(self):
        revision, future = self._future
        if not future.done():
            return
        self._future = None
        self._poll_timer.stop()
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
        self._debounce.stop()
        self._poll_timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self.plotter.close()
