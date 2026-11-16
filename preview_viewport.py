"""Shared mouse controls for the 3D preview windows."""

from pyvistaqt import QtInteractor
from PySide6.QtCore import QEvent, QObject, Qt, QTimer
import numpy as np
from time import monotonic
from scipy.spatial.transform import Rotation, Slerp


CAMERA_MOTION_FACTOR = 6.0
MESH_PREVIEW_ACTOR = "mesh_preview"


class _PreviewInteractor(QtInteractor):
    """Release custom VTK widgets before QtInteractor finalizes its context."""

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
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)

    def stop(self):
        self.timer.stop()
        actor = getattr(self, "edge_actor", None)
        if actor is not None:
            actor.prop.show_edges = self.edges_visible
            self.edge_actor = None

    def start(self, direction, up, duration_ms):
        camera = self.plotter.camera
        self.focus = np.asarray(camera.focal_point).copy()
        self.distance = camera.distance
        start = _camera_frame(np.asarray(camera.position) - self.focus, camera.up)
        end = _camera_frame(direction, up)
        self.rotation = Slerp([0, 1], Rotation.from_matrix(np.stack((start, end))))
        self.duration = max(duration_ms / 1000, 0.001)
        self.started = monotonic()
        self.edge_actor = self.plotter.renderer.actors.get(MESH_PREVIEW_ACTOR)
        if self.edge_actor is not None:
            self.edges_visible = self.edge_actor.prop.show_edges
            self.edge_actor.prop.show_edges = False
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
    transition.stop()
    if animate:
        transition.start(direction, up, duration_ms)
    else:
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
        plotter.orientation_cube = _create_orientation_cube(plotter)
        style.add_observer(
            "StartInteractionEvent", lambda *_: _show_mesh_edges(plotter, False)
        )
        style.add_observer(
            "EndInteractionEvent", lambda *_: _show_mesh_edges(plotter, True)
        )
    return plotter


def _create_orientation_cube(plotter):
    """A native VTK orientation marker, independent of the bottom-left axes."""
    from vtkmodules.vtkInteractionWidgets import vtkOrientationMarkerWidget
    from vtkmodules.vtkRenderingAnnotation import vtkAnnotatedCubeActor
    from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper, vtkPropAssembly
    import pyvista as pv

    cube = vtkAnnotatedCubeActor()
    for axis in ("X", "Y", "Z"):
        getattr(cube, f"Set{axis}PlusFaceText")(axis)
        getattr(cube, f"Set{axis}MinusFaceText")(f"−{axis}")
        for side in ("Plus", "Minus"):
            getattr(cube, f"Get{axis}{side}FaceProperty")().SetColor(0.84, 0.89, 0.96)
    cube.GetTextEdgesProperty().SetColor(0.15, 0.22, 0.34)
    cube.GetCubeProperty().SetColor(0.64, 0.71, 0.81)
    cube.GetCubeProperty().EdgeVisibilityOn()
    cube.GetCubeProperty().SetEdgeColor(0.18, 0.27, 0.40)
    cube.GetCubeProperty().SetLineWidth(1.5)
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
                    start[along_axis], end[along_axis] = -0.5, 0.5
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
    marker.AddPart(cube)
    marker.AddPart(borders)
    widget = vtkOrientationMarkerWidget()
    widget.SetOrientationMarker(marker)
    widget.SetInteractor(plotter.iren.interactor)
    widget.SetCurrentRenderer(plotter.renderer)
    widget.SetViewport(0.90, 0.84, 0.99, 0.99)
    widget.SetEnabled(1)
    widget.InteractiveOff()
    plotter._orientation_controls = _OrientationCubeControls(plotter, widget)
    plotter.interactor.installEventFilter(plotter._orientation_controls)
    plotter.interactor.setMouseTracking(True)
    plotter.interactor.setToolTip(
        "Click a marked face, edge or corner for a smooth view change. Drag the cube to rotate the model."
    )
    return widget


def _show_mesh_edges(plotter: QtInteractor, visible: bool) -> None:
    actor = plotter.renderer.actors.get(MESH_PREVIEW_ACTOR)
    if actor is None or actor.prop.show_edges == visible:
        return
    actor.prop.show_edges = visible
    if visible:
        plotter.render()
