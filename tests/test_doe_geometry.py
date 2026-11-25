"""Geometry library preview validation and window preferences."""
from concurrent.futures import Future
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from doe_geometry import GeometryLibraryDialog
from model import Settings
from window_state import WindowStateStore


class GeometryLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_current_preview_required_and_preferences_restored(self):
        with TemporaryDirectory() as folder:
            previous = getattr(self.app, "_rustmech_window_state", None)
            if previous is not None:
                self.app.removeEventFilter(previous)
            store = WindowStateStore(self.app, QSettings(str(Path(folder) / "windows.ini"), QSettings.Format.IniFormat))
            self.app._rustmech_window_state = store
            windows = []
            try:
                with patch("preview_viewport.create_preview_viewport") as factory:
                    def viewport(_):
                        return MagicMock(interactor=QWidget())
                    factory.side_effect = viewport
                    first = GeometryLibraryDialog(Settings(), lambda _: None)
                    windows.append(first)
                    first.show()
                    self.app.processEvents()
                    self.assertIsNone(first._future)
                    self.assertFalse(first.confirm.isEnabled())
                    first.accept()
                    self.assertTrue(first.isVisible())
                    future = Future()
                    future.set_result(SimpleNamespace(n_cells=12))
                    with patch.object(first._executor, 'submit', return_value=future) as submit:
                        first.apply_button.click()
                        submit.assert_called_once_with(first._builder, first.selected_settings())
                    first._poll()
                    self.assertTrue(first.confirm.isEnabled())
                    first.plotter.enable_lightkit.assert_called_once()
                    self.assertEqual(first.plotter.add_mesh.call_args.kwargs["ambient"], 0.22)
                    self.assertEqual(first.splitter.orientation(), Qt.Orientation.Vertical)
                    self.assertTrue(first.splitter.widget(0).isAncestorOf(first.plotter.interactor))
                    self.assertTrue(first.splitter.widget(1).isAncestorOf(first.values["size"]))
                    self.assertLess(first.plotter.interactor.mapTo(first, first.plotter.interactor.rect().bottomLeft()).y(),
                                    first.values["size"].mapTo(first, first.values["size"].rect().topLeft()).y())
                    first.structure.setCurrentText("Neovius")
                    self.assertFalse(first.confirm.isEnabled())
                    self.assertIsNone(first._future)
                    # Completion from the previous choice must not enable inclusion.
                    first._future = (first._revision - 1, future)
                    first._poll()
                    self.assertFalse(first.confirm.isEnabled())
                    first._future = (first._revision, future)
                    first._poll()
                    self.assertEqual(first.selected_settings().structure_name, "Neovius")
                    first.values["size"].setFocus()
                    QTest.keyClick(first.values["size"], Qt.Key.Key_Return)
                    self.assertTrue(first.isVisible())
                    first.resize(700, 620)
                    first.move(80, 90)
                    self.app.processEvents()
                    first.splitter.setSizes([300, 220])
                    splitter_sizes = first.splitter.sizes()
                    frame = first.frameGeometry()
                    first.reject()
                    second = GeometryLibraryDialog(Settings(), lambda _: None)
                    windows.append(second)
                    second.show()
                    self.app.processEvents()
                    self.assertEqual(second.frameGeometry(), frame)
                    self.assertEqual(second.splitter.sizes(), splitter_sizes)
                    second.values["cell_size"].setValue(80)
                    second.apply_button.click()
                    self.assertFalse(second.confirm.isEnabled())
                    self.assertIn("must not exceed", second.status.text())
            finally:
                for window in windows:
                    window.reject()
                    window.deleteLater()
                self.app.removeEventFilter(store)
                if previous is not None:
                    self.app._rustmech_window_state = previous
                    self.app.installEventFilter(previous)
                else:
                    del self.app._rustmech_window_state
