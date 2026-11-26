"""Exercise cube picking through Qt and verify the resulting camera direction."""

import unittest
from itertools import product
from unittest.mock import Mock, patch

import numpy as np
import pyvista as pv
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame, QScrollArea, QToolButton, QWidget

from preview_viewport import align_camera, create_preview_viewport


class OrientationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.parent = QWidget()
        self.plotter = create_preview_viewport(self.parent, show_orientation_cube=True)
        self.plotter.resize(900, 700)
        self.plotter.add_mesh(pv.Box(bounds=(1, 3, 2, 5, -1, 1)))
        self.plotter.show()
        self.parent.show()
        self.plotter.reset_camera()
        QTest.qWait(100)

    def tearDown(self):
        self.plotter.close()
        self.parent.close()

    def cube_position(self, point):
        renderer = self.plotter.orientation_cube.GetRenderer()
        renderer.SetWorldPoint(*point, 1)
        renderer.WorldToDisplay()
        x, y, _ = renderer.GetDisplayPoint()
        width, height = self.plotter.render_window.GetSize()
        widget = self.plotter.interactor
        return QPoint(round(x * widget.width() / width),
                      round((height - 1 - y) * widget.height() / height))

    def wait_for_camera_transition(self, timeout_ms=5000):
        """Animasyon suresine (480 ms) sabit pay birakmak yerine bitisini bekler."""
        QTest.qWait(50)
        waited = 50
        while self.plotter._camera_transition.timer.isActive() and waited < timeout_ms:
            QTest.qWait(25)
            waited += 25
        self.app.processEvents()

    def test_cube_faces_edges_and_corners(self):
        for signs in product((1, -1), repeat=3):
            for point, expected in (
                ((0.5, 0, 0), (1, 0, 0)),
                ((0, 0.5, 0), (0, 1, 0)),
                ((0, 0, 0.5), (0, 0, 1)),
                ((0.5, 0.43, 0), (1, 1, 0)),
                ((0.5, 0.43, 0.43), (1, 1, 1)),
            ):
                with self.subTest(signs=signs, point=point):
                    align_camera(self.plotter, signs, animate=False)
                    self.plotter.camera.parallel_projection = False
                    self.plotter.render()
                    self.app.processEvents()
                    focus = self.plotter.camera.focal_point
                    scale = self.plotter.camera.parallel_scale
                    position = self.cube_position(tuple(np.asarray(signs) * point))
                    QTest.mouseClick(self.plotter.interactor, Qt.MouseButton.LeftButton,
                                     pos=position)
                    self.wait_for_camera_transition()
                    camera = self.plotter.camera
                    direction = np.asarray(camera.position) - camera.focal_point
                    direction /= np.linalg.norm(direction)
                    expected = np.asarray(expected) * signs
                    expected = expected / np.linalg.norm(expected)
                    np.testing.assert_allclose(direction, expected, atol=1e-8)
                    self.assertTrue(camera.parallel_projection)
                    np.testing.assert_allclose(camera.focal_point, focus)
                    self.assertAlmostEqual(camera.parallel_scale, scale)

    def test_camera_and_marker_move_through_intermediate_orientations(self):
        align_camera(self.plotter, (1, 1, 1), animate=False)
        start = np.array(self.plotter.camera.position)
        distance = self.plotter.camera.distance
        align_camera(self.plotter, (-1, -1, -1))
        np.testing.assert_allclose(self.plotter.camera.position, start)
        QTest.qWait(180)
        self.assertGreater(np.linalg.norm(np.array(self.plotter.camera.position) - start), 0.01)
        self.assertTrue(self.plotter._camera_transition.timer.isActive())
        self.assertAlmostEqual(self.plotter.camera.distance, distance)
        marker_camera = self.plotter.orientation_cube.GetRenderer().GetActiveCamera()
        np.testing.assert_allclose(marker_camera.GetDirectionOfProjection(),
                                   self.plotter.camera.direction, atol=1e-8)
        QTest.qWait(400)
        self.assertFalse(self.plotter._camera_transition.timer.isActive())
        direction = np.array(self.plotter.camera.position) - self.plotter.camera.focal_point
        np.testing.assert_allclose(direction / np.linalg.norm(direction),
                                   -np.ones(3) / np.sqrt(3), atol=1e-8)

    def test_dragging_cube_rotates_scene_and_preserves_focus_and_zoom(self):
        align_camera(self.plotter, (1, 1, 1), animate=False)
        camera = self.plotter.camera
        start = np.array(camera.position)
        focus, scale, distance = camera.focal_point, camera.parallel_scale, camera.distance
        position = self.cube_position((0.5, 0, 0))
        QTest.mousePress(self.plotter.interactor, Qt.MouseButton.LeftButton, pos=position)
        QTest.mouseMove(self.plotter.interactor, position + QPoint(35, 20), delay=20)
        QTest.mouseRelease(self.plotter.interactor, Qt.MouseButton.LeftButton,
                           pos=position + QPoint(35, 20))
        QTest.qWait(200)
        self.assertGreater(np.linalg.norm(np.array(camera.position) - start), 0.01)
        np.testing.assert_allclose(camera.focal_point, focus)
        self.assertAlmostEqual(camera.parallel_scale, scale)
        self.assertAlmostEqual(camera.distance, distance)

    def test_dense_mesh_keeps_its_mapper_and_edges_in_every_camera_frame(self):
        self.plotter.clear()
        grid = pv.ImageData(dimensions=(250, 250, 3)).cast_to_unstructured_grid()
        mesh = self.plotter.add_mesh(grid, show_edges=True, color="#60a5fa")
        overlay = self.plotter.add_mesh(pv.Sphere(), opacity=0.35, show_edges=False)
        self.plotter.reset_camera()
        viewport = self.plotter.interactor
        position = QPoint(160, 180)
        full_detail = (mesh.mapper, True, overlay.mapper, False)
        frames = []

        def record_frame(*_):
            frames.append((mesh.mapper, mesh.prop.show_edges, overlay.mapper, overlay.prop.show_edges))

        self.plotter.render_window.AddObserver("StartEvent", record_frame)
        for button in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            with self.subTest(button=button):
                QTest.mousePress(viewport, button, pos=position)
                QTest.mouseMove(viewport, position + QPoint(20, 10))
                QTest.qWait(50)
                QTest.mouseRelease(viewport, button, pos=position)

        distance = self.plotter.camera.distance
        wheel = QWheelEvent(QPointF(position), QPointF(viewport.mapToGlobal(position)),
                            QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(viewport, wheel)
        self.assertNotEqual(self.plotter.camera.distance, distance)
        QTest.qWait(50)

        align_camera(self.plotter, (1, 0, 0))
        self.wait_for_camera_transition()
        self.assertGreaterEqual(len(frames), 4)
        self.assertTrue(all(frame == full_detail for frame in frames))
        self.assertIs(mesh.mapper.dataset, grid)

    def test_trackball_applies_all_moves_before_drawing_one_frame(self):
        frames = []
        self.plotter.render_window.AddObserver(
            "StartEvent", lambda *_: frames.append(self.plotter.camera.position),
        )
        interactor = self.plotter.iren.interactor
        start = self.plotter.camera.position
        interactor.SetEventPosition(160, 180)
        interactor.LeftButtonPressEvent()
        frames.clear()
        for offset in range(1, 31):
            interactor.SetEventPosition(160 + offset, 180)
            interactor.MouseMoveEvent()
        interactor.LeftButtonReleaseEvent()
        end = self.plotter.camera.position
        self.assertNotEqual(end, start)
        self.assertEqual(frames, [])
        QTest.qWait(50)
        self.assertEqual(frames, [end])

    def test_view_menu_projection_and_presets(self):
        from model import Settings
        from nonlinear_compression import RustMechDialog

        with patch.object(RustMechDialog, "start_preview", lambda self: None):
            dialog = RustMechDialog(Settings())
            try:
                dialog.show()
                self.app.processEvents()
                toolbar = dialog.findChild(QFrame, "cameraToolbar")
                self.assertIsNotNone(toolbar)
                self.assertTrue(toolbar.isVisible())
                self.assertTrue(toolbar.isEnabled())
                buttons = toolbar.findChildren(QToolButton)
                names = {button.accessibleName() for button in buttons}
                self.assertLessEqual({"Iso", "XY", "−XY", "XZ", "−XZ", "YZ", "−YZ",
                                      "Orthographic", "Perspective", "Reset view"}, names)
                self.assertTrue(all(button.isEnabled() for button in buttons))
                self.assertTrue(dialog.orthographic_button.isChecked())
                self.assertTrue(dialog.plotter.camera.parallel_projection)
                for preset in ("xy", "-xy", "xz", "-xz", "yz", "-yz", "iso"):
                    dialog.perspective_button.trigger()
                    self.assertFalse(dialog.plotter.camera.parallel_projection)
                    dialog._set_camera_preset(preset)
                    self.assertTrue(dialog.plotter.camera.parallel_projection)
                    self.assertTrue(dialog.orthographic_button.isChecked())
                    self.assertFalse(dialog.perspective_button.isChecked())
                dialog.perspective_button.trigger()
                align_camera(dialog.plotter, (1, -1, 1))
                self.assertTrue(dialog.orthographic_button.isChecked())
                self.assertFalse(dialog.perspective_button.isChecked())
                dialog.perspective_button.trigger()
                dialog.orthographic_button.trigger()
                self.assertTrue(dialog.plotter.camera.parallel_projection)
                dialog.perspective_button.trigger()
                dialog.reset_view()
                self.assertTrue(dialog.plotter.camera.parallel_projection)
                self.assertTrue(dialog.orthographic_button.isChecked())
            finally:
                dialog.close()

    def test_display_mode_buttons_share_the_right_edge_below_the_camera_controls(self):
        from model import Settings
        from nonlinear_compression import RustMechDialog

        start_preview = Mock()
        with patch.object(RustMechDialog, "start_preview", lambda self: start_preview()):
            dialog = RustMechDialog(Settings())
            try:
                dialog.show()
                viewport = dialog.plotter.interactor
                toolbar = dialog.camera_toolbar
                camera_group = toolbar.findChild(QScrollArea, "cameraViewGroup")
                group = toolbar.display_modes
                for width, height, scrolls in ((1440, 900, False), (900, 600, True), (1440, 900, False)):
                    dialog.resize(width, height)
                    QTest.qWait(100)
                    marker = dialog.plotter.orientation_cube.GetViewport()
                    self.assertAlmostEqual((1 - marker[2]) * viewport.width(), 2)
                    cube_size = min(96, viewport.width() - 2, viewport.height() // 4)
                    self.assertAlmostEqual((marker[2] - marker[0]) * viewport.width(), cube_size)
                    self.assertAlmostEqual((1 - marker[1]) * viewport.height(), cube_size)
                    self.assertLessEqual((1 - marker[1]) * viewport.height(), toolbar.y())
                    self.assertEqual(viewport.width() - toolbar.geometry().right() - 1, 2)
                    self.assertTrue(viewport.rect().contains(toolbar.geometry()))
                    self.assertEqual(camera_group.verticalScrollBar().maximum() > 0, scrolls)
                    for frame in (camera_group, group):
                        self.assertEqual(frame.geometry().center().x(), toolbar.rect().center().x())
                        for button in frame.findChildren(QToolButton):
                            x = button.mapTo(frame, QPoint()).x()
                            self.assertEqual(x, frame.width() - x - button.width())

                command_bar = dialog.findChild(QWidget, "workspaceCommandBar")
                for button in (dialog.project_button, dialog.doe_button, dialog.truba_button, dialog.about_button):
                    self.assertIs(button.parentWidget(), command_bar)
                    self.assertEqual(button.geometry().center().y(), dialog.truba_button.geometry().center().y())
                modes = dialog.display_mode_buttons
                self.assertTrue(group.isVisible())
                self.assertGreater(group.y(), camera_group.geometry().bottom())
                self.assertEqual(modes.checkedId(), 0)
                start_preview.reset_mock()
                for index in (1, 2, 0):
                    button = modes.button(index)
                    self.assertIs(button.parentWidget(), group)
                    self.assertEqual(button.toolButtonStyle(), Qt.ToolButtonStyle.ToolButtonIconOnly)
                    self.assertFalse(button.icon().isNull())
                    self.assertTrue(button.accessibleName())
                    self.assertTrue(button.toolTip())
                    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                    self.assertEqual(dialog.mode.currentIndex(), index)
                    self.assertEqual(modes.checkedId(), index)
                self.assertEqual(start_preview.call_count, 2)
                dialog.mode.setCurrentIndex(2)
                self.assertEqual(modes.checkedId(), 2)
                modes.button(2).setFocus()
                QTest.keyClick(modes.button(2), Qt.Key.Key_Up)
                self.assertEqual(dialog.mode.currentIndex(), 1)
            finally:
                dialog.close()


if __name__ == "__main__":
    unittest.main()
