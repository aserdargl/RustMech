from concurrent.futures import ThreadPoolExecutor

from studio_style import CompactComboBox
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from model import Settings, validate_geometry
from preview_viewport import create_preview_viewport


class DoEBoundaryPreview(QDialog):
    """Compare the reference and factor endpoints without modifying the active model."""

    def __init__(self, factor_label: str, cases: dict[str, Settings], surface_builder, parent):
        super().__init__(parent)
        self.setWindowTitle(f"DoE preview · {factor_label}")
        self.resize(720, 560)
        self._cases = list(cases.values())
        extent = max((case.size for case in self._cases if not validate_geometry(case)), default=1.0)
        self._comparison_bounds = (0.0, extent) * 3
        self._surface_builder = surface_builder
        self._cache = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="doe-preview")
        self._job = None
        self._disposed = False

        layout = QVBoxLayout(self)
        self.case_selector = CompactComboBox()
        self.case_selector.addItems(list(cases))
        self.case_selector.setAccessibleName("DoE preview case")
        layout.addWidget(self.case_selector)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.plotter = create_preview_viewport(self)
        layout.addWidget(self.plotter.interactor, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._timer = QTimer(self, interval=60)
        self._timer.timeout.connect(self._poll)
        self.case_selector.currentIndexChanged.connect(self._show_case)
        self.finished.connect(self._dispose)
        QTimer.singleShot(0, self._show_case)

    def _show_case(self, *_) -> None:
        if self._disposed:
            return
        index = self.case_selector.currentIndex()
        settings = self._cases[index]
        self.plotter.clear()
        errors = validate_geometry(settings)
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
        self.status.setText("Smooth geometry · Rotate to inspect")
        self.plotter.set_background("#eef2f6", top="#ffffff")
        self.plotter.add_mesh(surface, color="#4ea8de", smooth_shading=True, specular=0.2)
        self.plotter.add_axes(interactive=False)
        self.plotter.view_isometric(render=False, bounds=self._comparison_bounds)

    def _dispose(self, *_) -> None:
        self._disposed = True
        self._timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self.plotter.close()
