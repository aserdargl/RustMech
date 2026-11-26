"""Shared mouse controls for the 3D preview windows."""

from time import monotonic

import numpy as np
import pyvista as pv
from pyvistaqt import QtInteractor
from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from scipy.spatial.transform import Rotation, Slerp
from vtkmodules.vtkFiltersCore import vtkQuadricClustering


CAMERA_MOTION_FACTOR = 6.0
MESH_PREVIEW_ACTOR = "mesh_preview"
ORIENTATION_RIGHT_INSET = 2
MOTION_SURFACE_THRESHOLD = 100_000
MOTION_CLUSTER_DIVISIONS = 32
WHEEL_IDLE_MS = 150


def orientation_cube_size(viewport):
    return max(1, min(96, viewport.width() - ORIENTATION_RIGHT_INSET, viewport.height() // 4))


class _PreviewInteractor(QtInteractor):
    """Release custom VTK widgets before QtInteractor finalizes its context."""

    def set_motion_meshes(self, actors):
        self._mesh_motion.set_actors(actors)

    def clear(self):
        motion = getattr(self, "_mesh_motion", None)
        if motion is not None:
            motion.layers.clear()
        super().clear()

    def event(self, event):
        if event.type() == QEvent.Type.DeferredDelete:
            # deleteLater() bypasses close(): finalize before Qt destroys HWND.
            self.close()
        return super().event(event)

    def close(self):
        if self._closed:
            return
        transition = getattr(self, "_camera_transition", None)
        if transition is not None:
            transition.stop()
        motion = getattr(self, "_mesh_motion", None)
        if motion is not None:
            motion.wheel_timer.stop()
            motion.layers.clear()
        controls = getattr(self, "_orientation_controls", None)
        if controls is not None:
            self.interactor.removeEventFilter(controls)
            controls.press = None
        marker = getattr(self, "orientation_cube", None)
        if marker is not None:
            # The cube is custom, so PyVista's renderer widget cleanup does not
            # own it. Detach while the native window and interactor still exist.
            marker.SetEnabled(0)
            marker.SetInteractor(None)
            marker.SetCurrentRenderer(None)
        super().close()


def _motion_mapper(dataset):
    if dataset.n_cells < MOTION_SURFACE_THRESHOLD:
        return None
    surface = dataset if isinstance(dataset, pv.PolyData) else dataset.extract_surface(
        algorithm="dataset_surface", pass_pointid=False, pass_cellid=False,
    )
    if surface.n_cells < MOTION_SURFACE_THRESHOLD:
        return None
    cluster = vtkQuadricClustering()
    cluster.SetInputData(surface)
    divisions = MOTION_CLUSTER_DIVISIONS
    cluster.SetNumberOfDivisions(divisions, divisions, divisions)
    cluster.UseInputPointsOn()
    cluster.Update()
    reduced = pv.wrap(cluster.GetOutput())
    if 0 < reduced.n_cells < surface.n_cells // 2:
        mapper = pv.DataSetMapper(dataset=reduced)
        mapper.scalar_visibility = False
        return mapper
    return None


class _MeshMotion(QObject):
    """Keep the exact actors and mappers ready to restore after camera movement."""

    def __init__(self, plotter):
        super().__init__(plotter.interactor)
        self.plotter = plotter
        self.layers = []
        self.active = set()
        self.wheel_timer = QTimer(self)
        self.wheel_timer.setSingleShot(True)
        self.wheel_timer.setInterval(WHEEL_IDLE_MS)
        self.wheel_timer.timeout.connect(lambda: self.finish("wheel"))

    def set_actors(self, actors):
        for actor in actors:
            mapper = actor.mapper
            moving_mapper = _motion_mapper(mapper.dataset)
            self.layers.append((actor, mapper, moving_mapper, actor.prop.show_edges))
        if self.active:
            self._apply_detail()

    def set_active(self, source, active):
        was_moving = bool(self.active)
        if active:
            self.active.add(source)
        else:
            self.active.discard(source)
        if was_moving == bool(self.active) or not self.layers:
            return False
        self._apply_detail()
        return True

    def _apply_detail(self):
        moving = bool(self.active)
        for actor, mapper, moving_mapper, show_edges in self.layers:
            if moving_mapper is not None:
                actor.mapper = moving_mapper if moving else mapper
            actor.prop.show_edges = show_edges and not moving
        if not moving and any(layer[2] is not None for layer in self.layers):
            self.plotter.reset_camera_clipping_range()

    def wheel(self):
        self.set_active("wheel", True)
        self.wheel_timer.start()

    def finish(self, source):
        if self.set_active(source, False):
            self.plotter.render()


def _camera_frame(direction, up):
    direction = np.asarray(direction, dtype=float)
    direction /= np.linalg.norm(direction)
    right = np.cross(up, direction)
    right /= np.linalg.norm(right)
    return np.column_stack((right, np.cross(direction, right), direction))


class _CameraTransition(QObject):
    """Rotate the scene camera; VTK's marker follows the same rendered frames."""

    def __init__(self, plotter):
        super().__init__(plotter.interactor)
        self.plotter = plotter
        self.motion = getattr(plotter, "_mesh_motion", None)
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)

    def stop(self):
        self.timer.stop()
        if self.motion is not None:
            self.motion.set_active("transition", False)

    def start(self, direction, up, duration_ms):
        camera = self.plotter.camera
        self.focus = np.asarray(camera.focal_point).copy()
        self.distance = camera.distance
        start = _camera_frame(np.asarray(camera.position) - self.focus, camera.up)
        end = _camera_frame(direction, up)
        self.rotation = Slerp([0, 1], Rotation.from_matrix(np.stack((start, end))))
        self.duration = max(duration_ms / 1000, 0.001)
        self.started = monotonic()
        if self.motion is not None:
            self.motion.set_active("transition", True)
        self.timer.start()

    def _tick(self):
        t = min(1.0, (monotonic() - self.started) / self.duration)
        eased = t * t * (3 - 2 * t)
        frame = self.rotation(eased).as_matrix()
        camera = self.plotter.camera
        camera.position = self.focus + frame[:, 2] * self.distance
        camera.up = frame[:, 1]
        self.plotter.reset_camera_clipping_range()
        if t >= 1:
            self.stop()
        self.plotter.render()


def align_camera(plotter, direction, *, animate=True, up=None, duration_ms=480):
    """Ease around the current focus, preserving distance and orthographic zoom."""
    direction = np.asarray(direction, dtype=float)
    direction /= np.linalg.norm(direction)
    camera = plotter.camera
    focus = np.asarray(camera.focal_point)
    distance = camera.distance
    up = up if up is not None else ((0, 1, 0) if abs(direction[2]) > 0.99 else (0, 0, 1))
    camera.parallel_projection = True
    callback = getattr(plotter, "on_orientation_selected", None)
    if callback is not None:
        callback()
    if not hasattr(plotter, "_camera_transition"):
        plotter._camera_transition = _CameraTransition(plotter)
    transition = plotter._camera_transition
    if animate:
        transition.start(direction, up, duration_ms)
    else:
        transition.stop()
        camera.position = focus + direction * distance
        camera.up = up
        camera.OrthogonalizeViewUp()
        plotter.reset_camera_clipping_range()
        plotter.render()


class _OrientationCubeControls(QObject):
    """Consume cube clicks before the viewport's trackball receives them."""

    def __init__(self, plotter, marker):
        super().__init__(plotter.interactor)
        self.plotter = plotter
        self.marker = marker
        self.press = None
        self.dragging = False

    def resize_marker(self, viewport):
        width, height = max(1, viewport.width()), max(1, viewport.height())
        size = orientation_cube_size(viewport)
        right = max(size, width - ORIENTATION_RIGHT_INSET)
        self.marker.SetViewport((right - size) / width, 1 - size / height, right / width, 1)

    def _pick_direction(self, position):
        window = self.plotter.render_window
        width, height = window.GetSize()
        control = self.plotter.interactor
        x = position.x() * width / max(control.width(), 1)
        y = height - 1 - position.y() * height / max(control.height(), 1)
        renderer = self.marker.GetRenderer()
        if not renderer.IsInViewport(int(x), int(y)):
            return None
        # AnnotatedCubeActor hides its internal geometry from cell pickers.
        # Intersect the marker camera's ray with its unit cube instead.
        ray = []
        for depth in (0, 1):
            renderer.SetDisplayPoint(x, y, depth)
            renderer.DisplayToWorld()
            world = np.asarray(renderer.GetWorldPoint())
            ray.append(world[:3] / world[3])
        origin, far = ray
        vector = far - origin
        enter, leave = 0.0, 1.0
        for axis in range(3):
            if abs(vector[axis]) < 1e-12:
                if abs(origin[axis]) > 0.5:
                    return None
                continue
            limits = sorted(((-0.5 - origin[axis]) / vector[axis],
                             (0.5 - origin[axis]) / vector[axis]))
            enter = max(enter, limits[0])
            leave = min(leave, limits[1])
        if enter > leave:
            return None
        point = origin + enter * vector
        # The cube spans [-0.5, 0.5]. Outer bands select edges/corners.
        direction = np.where(np.abs(point) >= 0.32, np.sign(point), 0)
        return direction if np.any(direction) else None

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind in (QEvent.Type.Resize, QEvent.Type.Show):
            self.resize_marker(watched)
        if kind == QEvent.Type.Wheel and event.angleDelta().y():
            self.plotter._mesh_motion.wheel()
        if kind in (QEvent.Type.MouseButtonPress, QEvent.Type.Close, QEvent.Type.Wheel):
            if hasattr(self.plotter, "_camera_transition"):
                self.plotter._camera_transition.stop()
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            direction = self._pick_direction(event.position())
            if direction is not None:
                self.press = (event.position(), direction)
                camera = self.plotter.camera
                self.drag_frame = _camera_frame(np.asarray(camera.position) - camera.focal_point, camera.up)
                self.dragging = False
                watched.setCursor(Qt.CursorShape.ClosedHandCursor)
                return True
        elif kind == QEvent.Type.MouseMove and self.press is not None:
            delta = event.position() - self.press[0]
            if self.dragging or delta.manhattanLength() > 6:
                self.dragging = True
                # Orbit relative to the orientation at press, independent of frame rate.
                turn = (Rotation.from_rotvec(-delta.x() * 0.012 * self.drag_frame[:, 1])
                        * Rotation.from_rotvec(-delta.y() * 0.012 * self.drag_frame[:, 0]))
                frame = turn.as_matrix() @ self.drag_frame
                align_camera(self.plotter, frame[:, 2], up=frame[:, 1], duration_ms=120)
            return True
        elif kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            if self.press is not None:
                position, direction = self.press
                self.press = None
                if not self.dragging and (event.position() - position).manhattanLength() <= 6:
                    align_camera(self.plotter, direction)
                self.dragging = False
                watched.unsetCursor()
                return True
        elif kind == QEvent.Type.MouseMove:
            if self._pick_direction(event.position()) is not None:
                watched.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                watched.unsetCursor()
        return super().eventFilter(watched, event)


def create_preview_viewport(
    parent, *, simplify_mesh_during_drag: bool = False
) -> QtInteractor:
    """Create a trackball viewport with gentler mouse sensitivity."""
    plotter = _PreviewInteractor(parent, lighting="light kit", auto_update=False)
    plotter.enable_trackball_style()
    style = plotter.iren.style
    style.SetMotionFactor(CAMERA_MOTION_FACTOR)

    if simplify_mesh_during_drag:
        motion = plotter._mesh_motion = _MeshMotion(plotter)
        plotter.orientation_cube = _create_orientation_cube(plotter)
        style.add_observer(
            "StartInteractionEvent", lambda *_: motion.set_active("trackball", True)
        )
        style.add_observer(
            "EndInteractionEvent", lambda *_: motion.finish("trackball")
        )
    return plotter


def _create_orientation_cube(plotter):
    """A native VTK orientation marker, independent of the bottom-left axes."""
    from vtkmodules.vtkFiltersSources import vtkSuperquadricSource
    from vtkmodules.vtkInteractionWidgets import vtkOrientationMarkerWidget
    from vtkmodules.vtkRenderingAnnotation import vtkAnnotatedCubeActor
    from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper, vtkPropAssembly

    cube = vtkAnnotatedCubeActor()
    for axis in ("X", "Y", "Z"):
        getattr(cube, f"Set{axis}PlusFaceText")(axis)
        getattr(cube, f"Set{axis}MinusFaceText")(f"−{axis}")
        for side in ("Plus", "Minus"):
            getattr(cube, f"Get{axis}{side}FaceProperty")().SetColor(0.84, 0.89, 0.96)
    cube.GetTextEdgesProperty().SetColor(0.15, 0.22, 0.34)
    cube.SetCubeVisibility(False)
    shell = vtkSuperquadricSource()
    shell.SetPhiRoundness(0.07)
    shell.SetThetaRoundness(0.07)
    shell.SetPhiResolution(64)
    shell.SetThetaResolution(64)
    shell_mapper = vtkPolyDataMapper()
    shell_mapper.SetInputConnection(shell.GetOutputPort())
    shell_actor = vtkActor()
    shell_actor.SetMapper(shell_mapper)
    shell_actor.GetProperty().SetColor(0.64, 0.71, 0.81)
    # Match the visible face/edge/corner bands to _pick_direction's 0.32 cutoff.
    points, lines = [], []
    for normal_axis in range(3):
        face_axes = [axis for axis in range(3) if axis != normal_axis]
        for side in (-1, 1):
            for fixed_axis, along_axis in (face_axes, face_axes[::-1]):
                for band in (-0.32, 0.32):
                    start, end = np.zeros(3), np.zeros(3)
                    start[normal_axis] = end[normal_axis] = side * 0.501
                    start[fixed_axis] = end[fixed_axis] = band
                    start[along_axis], end[along_axis] = -0.46, 0.46
                    index = len(points)
                    points.extend((start, end))
                    lines.extend((2, index, index + 1))
    divisions = pv.PolyData(np.asarray(points), lines=np.asarray(lines))
    mapper = vtkPolyDataMapper()
    mapper.SetInputData(divisions)
    borders = vtkActor()
    borders.SetMapper(mapper)
    borders.GetProperty().SetColor(0.18, 0.27, 0.40)
    borders.GetProperty().SetLineWidth(1.5)
    borders.GetProperty().LightingOff()
    marker = vtkPropAssembly()
    marker.AddPart(shell_actor)
    marker.AddPart(cube)
    marker.AddPart(borders)
    widget = vtkOrientationMarkerWidget()
    widget.SetOrientationMarker(marker)
    widget.SetInteractor(plotter.iren.interactor)
    widget.SetCurrentRenderer(plotter.renderer)
    plotter._orientation_controls = _OrientationCubeControls(plotter, widget)
    plotter._orientation_controls.resize_marker(plotter.interactor)
    widget.SetEnabled(1)
    widget.InteractiveOff()
    plotter.interactor.installEventFilter(plotter._orientation_controls)
    plotter.interactor.setMouseTracking(True)
    plotter.interactor.setToolTip(
        "Click a marked face, edge or corner for a smooth view change. Drag the cube to rotate the model."
    )
    return widget
