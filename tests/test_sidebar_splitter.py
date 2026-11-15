"""Verify settings collapse, width recovery, and preferences across launches."""
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QToolButton, QVBoxLayout, QWidget

from sidebar_splitter import SidebarSplitter
from window_state import WindowStateStore


class SidebarSplitterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_click_drag_and_relaunch_restore_expanded_width(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WindowStateStore(self.app, QSettings(
                str(Path(directory) / "windows.ini"), QSettings.Format.IniFormat))
            windows = []

            def create():
                window = QDialog()
                window.resize(900, 600)
                layout = QVBoxLayout(window)
                button = QToolButton()
                button.setCheckable(True)
                layout.addWidget(button)
                splitter = SidebarSplitter()
                sidebar = QWidget()
                sidebar.setMinimumWidth(230)
                splitter.addWidget(sidebar)
                splitter.addWidget(QWidget())
                splitter.setCollapsible(0, True)
                splitter.bind_toggle(button)
                layout.addWidget(splitter)
                windows.append(window)
                window.show()
                self.app.processEvents()
                return window, splitter, button

            try:
                first, splitter, button = create()
                splitter.setSizes([370, 500])
                self.assertTrue(button.isChecked())
                self.assertEqual(button.arrowType(), Qt.ArrowType.LeftArrow)
                width = splitter.sizes()[0]
                total = sum(splitter.sizes())
                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                self.assertEqual(splitter.sizes(), [0, total])
                self.assertFalse(button.isChecked())
                first.hide()
                second, restored, toggle = create()
                self.assertEqual(restored.sizes()[0], 0)
                self.assertEqual(toggle.arrowType(), Qt.ArrowType.RightArrow)
                QTest.mouseClick(toggle, Qt.MouseButton.LeftButton)
                self.assertAlmostEqual(restored.sizes()[0], width, delta=1)
                self.assertTrue(toggle.isChecked())
                # Drag all the way left, then reopen using the visible toggle.
                handle = restored.handle(1)
                QTest.mousePress(handle, Qt.MouseButton.LeftButton)
                restored.moveSplitter(0, 1)
                QTest.mouseRelease(handle, Qt.MouseButton.LeftButton)
                self.assertEqual(restored.sizes()[0], 0)
                self.assertFalse(toggle.isChecked())
                QTest.mouseClick(toggle, Qt.MouseButton.LeftButton)
                self.assertAlmostEqual(restored.sizes()[0], width, delta=1)
            finally:
                for window in windows:
                    window.close()
                self.app.removeEventFilter(store)


if __name__ == "__main__":
    unittest.main()
