from concurrent.futures import ThreadPoolExecutor
from threading import Event

from studio_style import STUDIO_STYLESHEET
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QLabel, QVBoxLayout

from model import settings_from_dict
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
