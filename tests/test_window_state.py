"""Exercise window preferences with an isolated per-test settings file."""
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog, QSplitter, QVBoxLayout, QWidget

from window_state import WindowStateStore


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


if __name__ == "__main__":
    unittest.main()
