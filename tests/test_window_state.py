"""Exercise window preferences with an isolated per-test settings file."""
import ctypes
import tempfile
import unittest
from ctypes import wintypes
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QMainWindow, QMenu, QMessageBox, QSplitter, QVBoxLayout, QWidget,
)

from window_caption import CAPTION_PROPERTY, WM_NCHITTEST, WS_CAPTION, CaptionButton, MonitorInfo, install_window_captions
from window_state import WindowStateStore

DWMWA_NCRENDERING_ENABLED = 1
DWMWA_VISIBLE_FRAME_BORDER_THICKNESS = 37


class WindowStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.temp.name) / "windows.ini"), QSettings.Format.IniFormat)
        self.store = WindowStateStore(self.app, self.settings)
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.close()
        self.app.removeEventFilter(self.store)
        self.temp.cleanup()

    def window(self, name="main"):
        window = QDialog()
        window.setObjectName(name)
        layout = QVBoxLayout(window)
        splitter = QSplitter()
        splitter.addWidget(QWidget())
        splitter.addWidget(QWidget())
        layout.addWidget(splitter)
        self.windows.append(window)
        return window, splitter

    def test_recreated_window_restores_geometry_and_splitter(self):
        first, splitter = self.window()
        first.resize(640, 420)
        first.move(80, 90)
        first.show()
        self.app.processEvents()
        splitter.setSizes([170, 440])
        expected_frame = first.frameGeometry()
        expected_sizes = splitter.sizes()
        first.hide()
        second, restored = self.window()
        second.show()
        self.app.processEvents()
        self.assertEqual(second.frameGeometry(), expected_frame)
        self.assertEqual(restored.sizes(), expected_sizes)

    def test_maximized_state_keeps_normal_bounds(self):
        first, _ = self.window()
        first.resize(600, 400)
        first.show()
        self.app.processEvents()
        expected_size = first.size()
        first.showMaximized()
        self.app.processEvents()
        first.hide()
        second, _ = self.window()
        second.show()
        self.app.processEvents()
        self.assertTrue(second.isMaximized())
        second.showNormal()
        self.app.processEvents()
        self.assertEqual(second.size(), expected_size)

    def test_secondary_window_preferences_are_separate_and_visible(self):
        first, _ = self.window("secondary")
        first.resize(520, 360)
        first.show()
        first.move(-20000, -20000)
        first.hide()
        second, _ = self.window("secondary")
        second.show()
        self.app.processEvents()
        self.assertEqual(second.size().width(), 520)
        self.assertTrue(self.app.primaryScreen().availableGeometry().intersects(second.frameGeometry()))
        other, _ = self.window("main")
        self.assertIsNone(self.settings.value(f"{self.store.key(other)}/geometry"))

    def test_owner_does_not_save_or_restore_secondary_splitters(self):
        owner, own_splitter = self.window()
        own_splitter.setObjectName("settings")
        child = QDialog(owner)
        child.setObjectName("experiments")
        layout = QVBoxLayout(child)
        secondary = QSplitter(Qt.Orientation.Vertical)
        secondary.setObjectName("experimentPlan")
        secondary.addWidget(QWidget())
        secondary.addWidget(QWidget())
        layout.addWidget(secondary)
        self.windows.append(child)
        self.store.save(owner)
        key = self.store.key(owner)
        self.assertIsNone(self.settings.value(f"{key}/splitters/experimentPlan"))
        # Even old contaminated parent preferences must not reach the child.
        wrong = QSplitter(Qt.Orientation.Horizontal)
        self.settings.setValue(f"{key}/splitters/experimentPlan", wrong.saveState())
        self.store.restore(owner)
        self.assertEqual(secondary.orientation(), Qt.Orientation.Vertical)

    def test_wrong_orientation_record_cannot_rotate_settings_column(self):
        window, splitter = self.window()
        splitter.setObjectName("settings")
        wrong = QSplitter(Qt.Orientation.Vertical)
        wrong.addWidget(QWidget())
        wrong.addWidget(QWidget())
        self.settings.setValue(f"{self.store.key(window)}/splitters/settings", wrong.saveState())
        self.store.restore(window)
        self.assertEqual(splitter.orientation(), Qt.Orientation.Horizontal)

    def test_named_layout_survives_insertion_of_other_splitters(self):
        first, splitter = self.window()
        splitter.setObjectName("settings")
        first.show()
        self.app.processEvents()
        splitter.setSizes([170, 440])
        expected = splitter.sizes()
        first.hide()
        second = QDialog()
        second.setObjectName("main")
        second.resize(first.size())
        layout = QVBoxLayout(second)
        added = QSplitter(Qt.Orientation.Vertical, second)
        added.setObjectName("newPlan")
        added.hide()
        restored = QSplitter(Qt.Orientation.Horizontal)
        restored.setObjectName("settings")
        restored.addWidget(QWidget())
        restored.addWidget(QWidget())
        layout.addWidget(restored)
        self.windows.append(second)
        second.show()
        self.app.processEvents()
        self.assertEqual(restored.orientation(), Qt.Orientation.Horizontal)
        self.assertEqual(restored.sizes(), expected)

    def test_plain_tool_window_can_be_embedded_again(self):
        service_attr = "_rustmech_caption_service"
        install_window_captions(self.app)
        service = getattr(self.app, service_attr)
        self.addCleanup(delattr, self.app, service_attr)
        self.addCleanup(self.app.removeEventFilter, service)
        if service._native_frame:
            self.addCleanup(self.app.removeNativeEventFilter, service._native_frame)
        host = QWidget()
        panel = QWidget(host, Qt.WindowType.Tool)
        self.windows.extend([host, panel])
        panel.setWindowTitle("Preview")
        panel.setObjectName("tool-panel")
        margins = panel.contentsMargins()
        panel.show()
        self.assertTrue(panel.property(CAPTION_PROPERTY).isVisible())
        panel.resize(430, 270)
        panel.hide()
        self.assertTrue(self.settings.contains(f"{self.store.key(panel)}/geometry"))
        reopened = QWidget(host, Qt.WindowType.Tool)
        self.windows.append(reopened)
        reopened.setObjectName("tool-panel")
        reopened.show()
        self.assertEqual(reopened.size(), panel.size())
        panel.setParent(host, Qt.WindowType.Widget)
        host.show()
        panel.show()
        self.assertIsNone(panel.property(CAPTION_PROPERTY))
        self.assertEqual(panel.contentsMargins(), margins)

    def test_caption_preserves_window_actions_modality_and_saved_geometry(self):
        # A native viewport can create the HWND before the owner is shown.
        main = QMainWindow()
        main.setCentralWidget(QWidget())
        main.resize(640, 440)
        main.move(80, 90)
        main.ensurePolished()
        main.centralWidget().winId()
        self.windows.append(main)

        attribute = Qt.ApplicationAttribute.AA_DontUseNativeDialogs
        native_dialogs_disabled = self.app.testAttribute(attribute)
        install_window_captions(self.app)
        service = self.app._rustmech_caption_service
        self.addCleanup(self.app.setAttribute, attribute, native_dialogs_disabled)
        self.addCleanup(delattr, self.app, "_rustmech_caption_service")
        self.addCleanup(self.app.removeEventFilter, service)
        if service._native_frame is not None:
            self.addCleanup(self.app.removeNativeEventFilter, service._native_frame)
        self.addCleanup(service.deleteLater)

        def assert_integrated_frame(window):
            if service._native_frame is None:
                self.assertTrue(window.windowHandle().flags() & Qt.WindowType.FramelessWindowHint)
                return
            frame = service._native_frame
            hwnd = int(window.winId())
            self.assertEqual(frame._get_style(hwnd, -16) & WS_CAPTION, 0)
            self.assertEqual(frame._get_style(hwnd, -20) & 0x00000001, 0)
            if not window.isMinimized():
                bounds, _ = native_bounds(window)
                client_rect = wintypes.RECT()
                origin = wintypes.POINT()
                self.assertTrue(ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(client_rect)))
                self.assertTrue(frame._client_to_screen(hwnd, ctypes.byref(origin)))
                self.assertEqual((origin.x, origin.y, client_rect.right, client_rect.bottom),
                                 (bounds.left, bounds.top, bounds.right - bounds.left, bounds.bottom - bounds.top))

        def native_bounds(window):
            frame = service._native_frame
            hwnd = int(window.winId())
            bounds = wintypes.RECT()
            monitor = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
            self.assertTrue(ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(bounds)))
            self.assertTrue(frame._monitor_info(frame._monitor_from_window(hwnd, 2), ctypes.byref(monitor)))
            return bounds, monitor

        def assert_native_work_area(window):
            assert_integrated_frame(window)
            if service._native_frame is not None:
                self.assertTrue(service._native_frame._is_zoomed(int(window.winId())))
                bounds, monitor = native_bounds(window)
                self.assertEqual((bounds.left, bounds.top, bounds.right, bounds.bottom),
                                 (monitor.work.left, monitor.work.top, monitor.work.right, monitor.work.bottom))

        def assert_frame_decoration(window):
            outline = window.property(CAPTION_PROPERTY).outline
            if service._native_frame is None:
                self.assertTrue(outline.isVisible())
                image = outline.grab().toImage()
                self.assertEqual(image.pixelColor(image.width() // 2, 0).name(), "#64748b")
            else:
                self.assertIsNone(outline)
                get_attribute = ctypes.windll.dwmapi.DwmGetWindowAttribute
                get_attribute.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
                hwnd = int(window.winId())
                result = wintypes.DWORD()
                self.assertEqual(get_attribute(hwnd, DWMWA_NCRENDERING_ENABLED, ctypes.byref(result),
                                               ctypes.sizeof(result)), 0)
                self.assertEqual(result.value, 1)
                status = get_attribute(hwnd, DWMWA_VISIBLE_FRAME_BORDER_THICKNESS, ctypes.byref(result),
                                       ctypes.sizeof(result))
                if status >= 0:
                    self.assertGreater(result.value, 0)

        if service._native_frame is None:
            main.show()
        else:
            frame = service._native_frame
            with patch.object(frame, "_extend_dwm_frame", wraps=frame._extend_dwm_frame) as extend:
                main.show()
                assert_integrated_frame(main)
                self.assertEqual(extend.call_args.args[0], int(main.winId()))
        self.app.processEvents()
        assert_integrated_frame(main)
        caption = main.property(CAPTION_PROPERTY)
        buttons = caption.findChildren(CaptionButton)
        self.assertEqual([button.action for button in buttons], ["pin", "minimize", "maximize", "close"])
        self.assertEqual([button.x() for button in buttons], list(range(caption.width() - 100, caption.width(), 25)))
        self.assertTrue(all(button.width() == button.height() == 25 for button in buttons))
        self.assertGreaterEqual(main.centralWidget().y(), caption.geometry().bottom() + 1)
        assert_frame_decoration(main)
        if service._native_frame is not None:
            # VTK's native viewport likewise promotes the surrounding Qt widgets.
            main.centralWidget().winId()
            send_message = ctypes.windll.user32.SendMessageW
            send_message.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
            send_message.restype = ctypes.c_ssize_t
            hwnd = int(main.winId())
            origin = wintypes.POINT()
            self.assertTrue(service._native_frame._client_to_screen(hwnd, ctypes.byref(origin)))
            ratio = main.devicePixelRatioF()
            for point, expected_hit in (
                (QPoint(1, 1), 13),
                (caption.pos() + QPoint(80, 12), 2),
                (caption.pos() + caption.pin.geometry().center(), 1),
            ):
                x = origin.x + round(point.x() * ratio)
                y = origin.y + round(point.y() * ratio)
                self.assertEqual(send_message(hwnd, WM_NCHITTEST, 0, (x & 0xFFFF) | ((y & 0xFFFF) << 16)),
                                 expected_hit)

            # Simulate Windows calculating the client area before Qt can
            # route frame messages, even though the native styles are correct.
            main.hide()
            self.app.removeNativeEventFilter(frame)
            try:
                self.assertTrue(frame._set_position(hwnd, None, 0, 0, 0, 0, 0x0037))
            finally:
                self.app.installNativeEventFilter(frame)
            self.assertEqual(frame._get_style(hwnd, -16) & WS_CAPTION, 0)
            main.show()
            assert_integrated_frame(main)
        main.resize(660, 450)
        self.app.processEvents()
        if caption.outline is not None:
            self.assertTrue(caption.outline.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
            self.assertTrue(caption.outline.mask().contains(QPoint(main.width() - 1, main.height() - 1)))
            self.assertFalse(caption.outline.mask().contains(main.rect().center()))
        normal = main.geometry()
        buttons[1].click()
        self.app.processEvents()
        self.assertTrue(main.isMinimized())
        main.showNormal()
        self.app.processEvents()
        self.assertEqual(main.geometry(), normal)

        dialog = QDialog(main)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.addWidget(QWidget())
        self.windows.append(dialog)
        dialog.show()
        self.app.processEvents()
        assert_integrated_frame(dialog)
        dialog_caption = dialog.property(CAPTION_PROPERTY)
        dialog_buttons = [button for button in dialog_caption.findChildren(CaptionButton) if button.isVisible()]
        self.assertEqual([button.action for button in dialog_buttons], ["pin", "close"])
        assert_frame_decoration(main)
        assert_frame_decoration(dialog)
        native = service._native_frame
        if native is not None:
            owner = native._get_owner(int(dialog.winId()), -8)
            self.assertEqual(owner, int(main.winId()))
        dialog_caption.pin.click()
        if native is not None:
            self.assertEqual(native._get_owner(int(dialog.winId()), -8), 0)
            dialog_caption.pin.click()
            self.assertEqual(native._get_owner(int(dialog.winId()), -8), owner)
            dialog_caption.pin.click()
        self.assertTrue(dialog_caption.pin.isChecked())
        self.assertFalse(caption.pin.isChecked())
        self.assertEqual(dialog.windowModality(), Qt.WindowModality.WindowModal)
        dialog_buttons[-1].click()
        self.assertFalse(dialog.isVisible())

        modeless = QDialog(main)
        modeless.setObjectName("modeless")
        # A style or a child widget can polish the window before its final flags are set.
        modeless.ensurePolished()
        modeless.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint)
        modeless.resize(400, 300)
        self.windows.append(modeless)
        modeless.show()
        self.app.processEvents()
        modeless_normal = modeless.geometry()
        assert_integrated_frame(modeless)
        modeless_caption = modeless.property(CAPTION_PROPERTY)
        self.assertEqual([button.action for button in modeless_caption.findChildren(CaptionButton)
                          if button.isVisible()], ["pin", "minimize", "close"])
        minimize = modeless.findChild(CaptionButton, "WindowCaptionMinimize")
        minimize.click()
        self.app.processEvents()
        self.assertTrue(modeless.isMinimized())
        if service._native_frame is not None:
            bounds, monitor = native_bounds(modeless)
            area = monitor.monitor
            self.assertTrue(bounds.right <= area.left or bounds.left >= area.right
                            or bounds.bottom <= area.top or bounds.top >= area.bottom)
            send_message(int(modeless.winId()), 0x0112, 0xF120, 0)  # WM_SYSCOMMAND / SC_RESTORE
        else:
            modeless.showNormal()
        self.app.processEvents()
        assert_integrated_frame(modeless)
        self.assertEqual(modeless.geometry(), modeless_normal)
        self.assertEqual(modeless.windowHandle().transientParent(), main.windowHandle())
        modeless.hide()
        modeless.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint)
        modeless.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        modeless.show()
        self.app.processEvents()
        assert_integrated_frame(modeless)
        self.assertIs(modeless.property(CAPTION_PROPERTY), modeless_caption)
        visible = [button for button in modeless_caption.findChildren(CaptionButton) if button.isVisible()]
        self.assertEqual([button.action for button in visible], ["pin", "minimize", "maximize"])
        self.assertEqual([button.x() for button in visible],
                         list(range(modeless_caption.width() - 75, modeless_caption.width(), 25)))
        modeless.close()

        caption.maximize.click()
        self.app.processEvents()
        self.assertTrue(main.isMaximized())
        self.assertTrue(caption.isVisible())
        if caption.outline is not None:
            self.assertFalse(caption.outline.isVisible())
        assert_native_work_area(main)
        main.hide()
        restored = QMainWindow()
        service.prepare_main_window(restored)
        self.windows.append(restored)
        restored.show()
        self.app.processEvents()
        self.assertTrue(restored.isMaximized())
        assert_native_work_area(restored)
        restored_caption = restored.property(CAPTION_PROPERTY)
        restored_caption.maximize.click()
        self.app.processEvents()
        self.assertEqual(restored.geometry(), normal)
        assert_frame_decoration(restored)
        restored.showFullScreen()
        self.app.processEvents()
        assert_integrated_frame(restored)
        self.assertFalse(restored_caption.isVisible())
        restored.showNormal()
        self.app.processEvents()
        assert_integrated_frame(restored)
        self.assertEqual(restored.geometry(), normal)
        self.assertTrue(restored_caption.isVisible())

        picker = QFileDialog(restored)
        self.windows.append(picker)
        picker.show()
        self.app.processEvents()
        assert_integrated_frame(picker)
        self.assertTrue(picker.property(CAPTION_PROPERTY).isVisible())
        assert_frame_decoration(picker)
        warning = QMessageBox(QMessageBox.Icon.Warning, "Warning", "Application message", parent=restored)
        self.windows.append(warning)
        warning.show()
        self.app.processEvents()
        assert_integrated_frame(warning)
        self.assertTrue(warning.property(CAPTION_PROPERTY).isVisible())
        menu = QMenu(restored)
        menu.ensurePolished()
        self.assertIsNone(menu.property(CAPTION_PROPERTY))


if __name__ == "__main__":
    unittest.main()
