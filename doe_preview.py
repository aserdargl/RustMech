from concurrent.futures import ThreadPoolExecutor
from threading import Event

from studio_style import CompactComboBox, CONTROL_HEIGHT, STUDIO_STYLESHEET
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout,
)

from model import Settings, settings_from_dict, validate_geometry
from preview_viewport import create_preview_viewport
from ui_language import translate


def build_input_preview(request, mesh, cancelled):
    """Return the preview data, render style and measurements for one input case."""
    settings = settings_from_dict(request["settings"])
    op = request["operation"]
    if not mesh or op == "mechanical":
        from mesh_preview_worker import run_mesh_preview

        preview = run_mesh_preview(settings, None, None, cancelled, include_mesh=mesh)
        if not mesh:
            from standard_tests import profile

            standard = profile(settings)
            name = standard.label if standard else settings.imported_geometry_name or settings.structure_name
            dimensions = standard.dimensions if standard else settings.dimensions
            size = " × ".join(f"{value:g}" for value in dimensions)
            return preview.surface, "surface", f"{name} · {size} mm"
        grid = preview.mesh.grid
        return grid, "edges", (f"{settings.mesh_type} · {grid.n_cells:,} "
                               f"{translate('Elements')} · {grid.n_points:,} {translate('Nodes')}")

    if op in ("flow_solve", "thermal_solve"):
        from flow_analysis import FlowSettings, build_flow_grid

        flow = FlowSettings(**request["flow"])
        if op == "thermal_solve":
            from thermal_analysis import build_thermal_grid

            if flow.mesh_method != "cartesian":
                raise ValueError("Fitted meshing is currently qualified for Flow only. Select Cartesian for CHT.")
            grid, details = build_thermal_grid(settings, flow)
        else:
            grid, details = build_flow_grid(settings, flow, inspection=flow.mesh_method == "snappy")
        grid.points *= 1000  # The flow grid uses metres. Display it in millimetres.
        label = translate("Cartesian precheck" if flow.mesh_method == "snappy" else "Cartesian grid")
        summary = f"{label} · {details['nominal_cell_mm']:g} mm · {grid.n_cells:,} {translate('Cells')}"
        if flow.mesh_method == "snappy":
            refined_size = details["nominal_cell_mm"] / 2**flow.surface_refinement
            summary += (f" · {translate('Surface refinement')} {flow.surface_refinement} ({refined_size:g} mm)"
                        f" · {translate('Boundary layers')} {flow.boundary_layers}")
        return grid, "edges", summary

    import numpy as np
    import pyvista as pv

    if op == "fft_solve":
        from damask_fft import fft_dimensions, sample_periodic_cell
        from mesh_resources import require_memory

        count = request["grid"]
        solid = sample_periodic_cell(settings, count)
        solid_count = int(np.count_nonzero(solid))
        require_memory(solid.size * 16 + solid_count * 384, "DAMASK voxel preview")
        image = pv.ImageData(dimensions=(count + 1,) * 3,
                             spacing=tuple(length / count for length in fft_dimensions(settings)))
        image.cell_data["solid"] = solid.ravel(order="F").astype(np.uint8)
        surface = image.threshold(0.5, scalars="solid").extract_surface(
            pass_pointid=False, pass_cellid=False, algorithm="dataset_surface")
        return surface, "edges", f"{count}³ · {solid_count:,} {translate('Solid voxels')}"

    from mesh_engine import geometry_threshold
    from periodic_sampling import sample_box

    lengths = np.asarray(settings.dimensions)
    if op == "mpm_solve":
        from mpm_analysis import MPMSettings

        mpm = MPMSettings(**request["mpm"])
        mpm.validate(settings)
        cells = np.maximum(2, np.ceil(lengths / mpm.grid_mm))
        counts = cells * mpm.points_per_axis
        if cells.max() > 60 or counts.max() > 100:
            raise ValueError("MPM grid is too fine for this version. Increase the spacing or reduce points per axis.")
        dtype = np.float64
    else:
        from peridynamic_analysis import CompressionOptions, validate_compression

        options = CompressionOptions(**request["options"])
        errors = validate_compression(settings, options)
        if errors:
            raise ValueError("\n".join(errors))
        counts = np.maximum(1, np.rint(lengths / (settings.size / options.resolution)))
        if counts.max() > 72:
            raise ValueError("Use at most 72 particle intervals along each specimen axis.")
        dtype = np.float32
    spacing = lengths / counts
    axes = [(np.arange(int(count)) + 0.5) * step for count, step in zip(counts, spacing)]
    field = sample_box(settings, axes, dtype=dtype, threshold=geometry_threshold(settings))
    solid = field <= 0 if op == "mpm_solve" else field < 0
    points = pv.PolyData((np.argwhere(solid) + 0.5) * spacing)
    size = " × ".join(f"{step:g}" for step in spacing)
    return points, "points", f"{points.n_points:,} {translate('Particles')} · {size} mm"


class DoeInputPreview(QDialog):
    """A modeless preview of the current DoE settings before creating the matrix."""

    def __init__(self, parent, request, *, mesh, varying):
        super().__init__(parent)
        self.setObjectName("mesh" if mesh else "structure")
        self.setWindowTitle(translate("Mesh preview" if mesh else "Structure preview"))
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet(STUDIO_STYLESHEET)
        self.setMinimumSize(300, 240)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(7)
        if varying:
            note = QLabel("The preview uses the first configured level for each variable.")
            note.setWordWrap(True)
            layout.addWidget(note)
        viewport = QFrame()
        viewport.setObjectName("viewportContainer")
        viewport_layout = QVBoxLayout(viewport)
        viewport_layout.setContentsMargins(1, 1, 1, 1)
        from window_caption import install_window_captions

        install_window_captions(QApplication.instance()).prepare_main_window(self)
        self.plotter = create_preview_viewport(viewport)
        self.plotter.set_background("#e5edf5", top="#ffffff")
        viewport_layout.addWidget(self.plotter.interactor)
        layout.addWidget(viewport, 1)
        self.status = QLabel("Building FE mesh in background…" if mesh and request["operation"] == "mechanical"
                             else "Building preview…")
        self.status.setObjectName("doePreviewStatus")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.resize(510, 390)

        self._cancelled = Event()
        self.destroyed.connect(self._cancelled.set)
        self.finished.connect(self._dispose)
        self._timer = QTimer(self, interval=60)
        self._timer.timeout.connect(self._poll)
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="doe-input-preview")
        self._future = executor.submit(build_input_preview, request, mesh, self._cancelled)
        executor.shutdown(wait=False)
        self._timer.start()

    def _poll(self):
        if not self._future.done():
            return
        self._timer.stop()
        try:
            data, style, summary = self._future.result()
            if data.n_points == 0:
                raise ValueError(translate("No geometry to display."))
            self.plotter.add_mesh(data, color="#699bc4", style="points" if style == "points" else "surface",
                                  smooth_shading=style == "surface", show_edges=style == "edges",
                                  edge_color="#334155", point_size=4)
            self.plotter.add_axes(interactive=False)
            self.plotter.view_isometric()
            self.status.setText(summary)
        except Exception as error:
            self.status.setText(translate("Preview unavailable: {error}").format(error=str(error)))

    def _dispose(self):
        self._cancelled.set()
        self._timer.stop()
        self._future.cancel()
        self.plotter.close()


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
        extent = max((max(case.dimensions) for case in self._cases if not validate_geometry(case, include_mesh=False)), default=1.0)
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
        dimensions = " × ".join(f"{v:g}" for v in settings.dimensions) + " mm (X × Y × Z)"
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
