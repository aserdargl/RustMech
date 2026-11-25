from concurrent.futures import ThreadPoolExecutor

from studio_style import CompactComboBox, CONTROL_HEIGHT, STUDIO_STYLESHEET
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout,
)

from model import Settings, validate_geometry
from preview_viewport import create_preview_viewport


class DoEBoundaryPreview(QDialog):
    """Compare the reference and factor endpoints without modifying the active model."""

    def __init__(self, factor_label: str, cases: dict[str, Settings], surface_builder, parent, *, detach_requested=None):
        super().__init__(parent)
        from window_state import install_window_state
        install_window_state(QApplication.instance())
        self.setWindowTitle(f"DoE preview · {factor_label}")
        self.setStyleSheet(STUDIO_STYLESHEET)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.resize(840, 660)
        self.setMinimumSize(540, 440)
        self._cases = list(cases.values())
        extent = max((case.size for case in self._cases if not validate_geometry(case, include_mesh=False)), default=1.0)
        self._comparison_bounds = (0.0, extent) * 3
        self._surface_builder = surface_builder
        self._detach_requested = detach_requested
        self._cache = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="doe-preview")
        self._job = None
        self._disposed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        heading = QLabel("Geometry preview")
        heading.setObjectName("doePreviewTitle")
        layout.addWidget(heading)
        subtitle = QLabel(f"DoE · {factor_label}" + (
            " · Compare reference and range limits at the same scale."
            if len(cases) > 1 else " · Inspect the selected experiment geometry."
        ))
        subtitle.setTextFormat(Qt.TextFormat.PlainText)
        subtitle.setWordWrap(True)
        subtitle.setObjectName("doePreviewHint")
        layout.addWidget(subtitle)

        toolbar = QFrame()
        toolbar.setObjectName("viewportToolbar")
        tools = QHBoxLayout(toolbar)
        tools.setContentsMargins(8, 6, 8, 6)
        tools.setSpacing(6)
        case_label = QLabel("Case")
        self.case_selector = CompactComboBox()
        self.case_selector.addItems(list(cases))
        self.case_selector.setAccessibleName("DoE preview case")
        case_label.setBuddy(self.case_selector)
        tools.addWidget(case_label)
        tools.addWidget(self.case_selector)
        button_width = max(
            48, max(self.fontMetrics().horizontalAdvance(text)
                    for text in ("3D", "Top", "Front", "Close")) + 20,
        )
        for text, view, tooltip in (
            ("3D", "iso", "Reset to the isometric comparison view"),
            ("Top", "top", "View from above (XY)"),
            ("Front", "front", "View from the front (XZ)"),
        ):
            button = QPushButton(text)
            button.setAutoDefault(False)
            button.setFixedSize(button_width, CONTROL_HEIGHT)
            button.setToolTip(tooltip)
            button.clicked.connect(lambda _checked=False, view=view: self._set_view(view))
            tools.addWidget(button)
        tools.addStretch(1)
        layout.addWidget(toolbar)

        self.details = QLabel()
        self.details.setObjectName("doePreviewDetails")
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.details.setWordWrap(True)
        layout.addWidget(self.details)

        viewport = QFrame()
        viewport.setObjectName("viewportContainer")
        viewport_layout = QVBoxLayout(viewport)
        viewport_layout.setContentsMargins(1, 1, 1, 1)
        self.plotter = create_preview_viewport(self)
        self.plotter.set_background("#e5edf5", top="#ffffff")
        viewport_layout.addWidget(self.plotter.interactor)
        layout.addWidget(viewport, 1)

        footer = QHBoxLayout()
        self.status = QLabel()
        self.status.setObjectName("doePreviewStatus")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        for button in buttons.buttons():
            button.setFixedSize(button_width, CONTROL_HEIGHT)
            button.setAutoDefault(False)
            button.setDefault(False)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        layout.addLayout(footer)
        hint = QLabel("Drag to rotate · Scroll to zoom · Middle-drag to pan")
        hint.setObjectName("doePreviewHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        from operation_progress import OperationProgress
        self.operation_progress = OperationProgress(self, provider=lambda: (
            self._job is not None, self.status.text(),
        ))
        layout.addWidget(self.operation_progress)

        self._timer = QTimer(self, interval=60)
        self._timer.timeout.connect(self._poll)
        self.case_selector.currentIndexChanged.connect(self._show_case)
        QTimer.singleShot(0, self._show_case)

    def _show_case(self, *_) -> None:
        if self._disposed:
            return
        index = self.case_selector.currentIndex()
        if index < 0:
            return
        settings = self._cases[index]
        self.case_selector.setToolTip(self.case_selector.currentText())
        name = settings.imported_geometry_name or settings.structure_name
        dimensions = f"Size {settings.size:g} mm"
        if not settings.imported_geometry_path and settings.specimen_architecture != "solid":
            dimensions += f"   ·   Cell {settings.cell_size:g} mm   ·   Density {settings.solid_fraction:g}%"
        self.details.setText(f"{name}   ·   {dimensions}")
        self.plotter.clear()
        errors = validate_geometry(settings, include_mesh=False)
        if errors:
            self.status.setText("\n".join(errors))
        elif index in self._cache:
            self._render(self._cache[index])
        else:
            self.status.setText("Building smooth geometry…")
            if self._job is None:
                self._job = (index, self._executor.submit(self._surface_builder, settings))
                self._timer.start()
        self.plotter.render()

    def _poll(self) -> None:
        index, future = self._job
        if not future.done():
            return
        self._job = None
        self._timer.stop()
        try:
            self._cache[index] = future.result()
        except Exception as error:
            self._cache[index] = str(error)
        self._show_case()

    def _render(self, surface) -> None:
        if isinstance(surface, str):
            self.status.setText(f"Preview unavailable. {surface}")
            return
        self.status.setText("Smooth geometry · Ready")
        # clear() removes the renderer lights along with the previous case.
        self.plotter.enable_lightkit()
        self.plotter.add_mesh(surface, color="#699bc4", smooth_shading=True,
                              ambient=0.18, diffuse=0.8,
                              specular=0.25, specular_power=24)
        self.plotter.add_axes(interactive=False)
        self._set_view("iso")

    def _set_view(self, view) -> None:
        if self._disposed:
            return
        self.plotter.enable_parallel_projection()
        if view == "top":
            self.plotter.view_xy(render=False, bounds=self._comparison_bounds)
        elif view == "front":
            self.plotter.view_xz(render=False, bounds=self._comparison_bounds)
        else:
            self.plotter.view_isometric(render=False, bounds=self._comparison_bounds)
        self.plotter.render()

    def done(self, result):
        self._dispose()
        super().done(result)

    def closeEvent(self, event):
        self._dispose()
        super().closeEvent(event)

    def detach_remote_job(self):
        if self._detach_requested is not None and self._job is not None:
            self._detach_requested.set()
            self._timer.stop()
            return self._job[1]
        return None

    def _dispose(self, *_) -> None:
        if self._disposed:
            return
        self._disposed = True
        self.detach_remote_job()
        self._timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self.plotter.close()
