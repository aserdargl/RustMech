from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

from studio_style import STUDIO_STYLESHEET
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QLabel, QVBoxLayout

from model import settings_from_dict
from preview_viewport import create_preview_viewport
from ui_language import translate


def preview_mesh_quality(grid, summary):
    from mechanical_results import mesh_quality

    if not grid.n_cells:
        raise ValueError("No geometry to display.")
    quality, report = mesh_quality(grid)
    jacobian = report["scaled_jacobian_range"]
    summary += "\n" + translate("Invalid or unmeasurable elements: {value}").format(
        value=f"{report['invalid_or_unmeasurable']:,}")
    if jacobian is not None:
        low, high = jacobian
        summary += " · " + translate("Scaled Jacobian: {low}–{high}").format(low=f"{low:.3f}", high=f"{high:.3f}")
    return quality, "quality", summary


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
        summary = (f"{settings.mesh_type} · {grid.n_cells:,} {translate('Elements')} "
                   f"· {grid.n_points:,} {translate('Nodes')}")
        return preview_mesh_quality(grid, summary)

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
        summary = "\n".join((summary, *map(translate, details.get("geometry_errors", ()))))
        return preview_mesh_quality(grid, summary)

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
        spacing = " × ".join(f"{step:.4g}" for step in image.spacing)
        return surface, "edges", (f"{count}³ · {solid_count:,} {translate('Solid voxels')} "
                                  f"({solid_count / solid.size:.1%})\n{translate('Voxel spacing')} · {spacing} mm")

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
        cell_counts = " × ".join(str(int(count)) for count in cells)
        detail = f"\n{translate('Background grid')} · {cell_counts} {translate('Cells')}"
        dtype = np.float64
    else:
        from peridynamic_analysis import (
            CompressionOptions, MAX_PARTICLE_INTERVALS, minimum_particle_resolution, validate_compression,
        )

        options = CompressionOptions(**request["options"])
        minimum = minimum_particle_resolution(settings)
        if minimum > MAX_PARTICLE_INTERVALS:
            raise ValueError(translate("This TPMS period requires {minimum} particles per axis. The limit is {maximum}. "
                                       "Increase the period or reduce the specimen size.").format(
                                           minimum=minimum, maximum=MAX_PARTICLE_INTERVALS))
        options = replace(options, resolution=max(options.resolution, minimum))
        errors = validate_compression(settings, options)
        if errors:
            raise ValueError("\n".join(errors))
        counts = np.maximum(1, np.rint(lengths / (settings.size / options.resolution)))
        if counts.max() > MAX_PARTICLE_INTERVALS:
            raise ValueError("Use at most 72 particle intervals along each specimen axis.")
        detail = "\n" + translate("Particles per axis (Z): {value} · Minimum: {minimum}").format(
            value=options.resolution, minimum=minimum)
        dtype = np.float32
    spacing = lengths / counts
    axes = [(np.arange(int(count)) + 0.5) * step for count, step in zip(counts, spacing)]
    field = sample_box(settings, axes, dtype=dtype, threshold=geometry_threshold(settings))
    solid = field <= 0 if op == "mpm_solve" else field < 0
    points = pv.PolyData((np.argwhere(solid) + 0.5) * spacing)
    size = " × ".join(f"{step:g}" for step in spacing)
    return points, "points", f"{points.n_points:,} {translate('Particles')} · {translate('Particle spacing')} · {size} mm{detail}"


class DoeInputPreview(QDialog):
    """A modeless preview of the current DoE settings before creating the matrix."""

    def __init__(self, parent, request, *, mesh, varying, notes=(), error=None):
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
            notes = (translate("The preview uses the first configured level for each variable."), *notes)
        if notes:
            note = QLabel("\n".join(notes))
            note.setObjectName("doePreviewNote")
            note.setTextFormat(Qt.TextFormat.PlainText)
            note.setWordWrap(True)
            layout.addWidget(note)
        viewport = QFrame()
        viewport.setObjectName("viewportContainer")
        viewport_layout = QVBoxLayout(viewport)
        viewport_layout.setContentsMargins(1, 1, 1, 1)
        from window_caption import install_window_captions

        install_window_captions(QApplication.instance()).prepare_main_window(self)
        layout.addWidget(viewport, 1)
        self.status = QLabel()
        self.status.setObjectName("doePreviewStatus")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.resize(510, 390)

        if error is not None:
            self._show_error(error)
            return

        self.plotter = create_preview_viewport(viewport)
        self.plotter.set_background("#e5edf5", top="#ffffff")
        viewport_layout.addWidget(self.plotter.interactor)
        self.status.setText(translate("Building FE mesh in background…" if mesh and request["operation"] == "mechanical"
                                      else "Building preview…"))
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
            appearance = (dict(scalars="scaled_jacobian", cmap="viridis", clim=(0, 1),
                               preference="cell", nan_color="#b91c1c",
                               scalar_bar_args={"title": translate("Scaled Jacobian"), "fmt": "%.2f",
                                                "title_font_size": 12, "label_font_size": 10})
                          if style == "quality" else dict(color="#699bc4"))
            self.plotter.add_mesh(data, style="points" if style == "points" else "surface",
                                  smooth_shading=style == "surface", show_edges=style in ("edges", "quality"),
                                  edge_color="#334155", edge_opacity=0.25, point_size=4, render=False, **appearance)
            self.plotter.add_axes(interactive=False)
            self.plotter.view_isometric(render=False)
            self.status.setText(summary)
            self.plotter.render()
        except Exception as error:
            self._show_error(str(error))

    def _show_error(self, error):
        self.status.setText(translate("Preview unavailable: {error}").format(error=translate(error)))

    def _dispose(self):
        self._cancelled.set()
        self._timer.stop()
        self._future.cancel()
        self.plotter.close()
