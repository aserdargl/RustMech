"""Native geometry selection, explicit actions and window restoration."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pyvista as pv
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from geometry_import_dialog import GeometryImportDialog
from nonlinear_compression import RustMechDialog
from window_state import WindowStateStore
from tests.test_geometry_import import face_at
from tests import test_geometry_import as geometry_tests


class GeometryImportUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_selection_and_window_preferences(self):
        with TemporaryDirectory() as folder:
            previous = getattr(self.app, '_rustmech_window_state', None)
            if previous is not None:
                self.app.removeEventFilter(previous)
            store = WindowStateStore(self.app, QSettings(str(Path(folder)/'windows.ini'), QSettings.Format.IniFormat))
            self.app._rustmech_window_state = store
            windows = []
            try:
                surface = pv.Box(bounds=(0, 2, 0, 3, 0, 4)).triangulate().clean()
                first = GeometryImportDialog(surface, 'test.stl')
                windows.append(first)
                first.show()
                first.resize(740, 560)
                first.move(80, 90)
                self.app.processEvents()
                self.assertFalse(first.confirm.isEnabled())
                first.select_cell(face_at(surface, 2, 0))
                first.select_cell(face_at(surface, 2, 4))
                self.assertTrue(first.confirm.isEnabled())
                QTest.keyClick(first, Qt.Key.Key_Return)
                self.assertTrue(first.isVisible())
                self.assertEqual(first.result(), 0)
                first.set_role(1)
                first.select_cell(face_at(surface, 0, 0))
                self.assertFalse(first.confirm.isEnabled())
                first.select_cell(face_at(surface, 2, 4))
                screenshot = Path(__file__).resolve().parents[1] / 'design' / 'geometry-import.png'
                first.grab().save(str(screenshot))
                expected = first.frameGeometry()
                first.hide()
                second = GeometryImportDialog(surface, 'test.stl')
                windows.append(second)
                second.show()
                self.app.processEvents()
                self.assertEqual(second.frameGeometry(), expected)
                second.showMaximized()
                self.app.processEvents()
                second.hide()
                third = GeometryImportDialog(surface, 'test.stl')
                windows.append(third)
                third.show()
                self.app.processEvents()
                self.assertTrue(third.isMaximized())
                third.showNormal()
                self.app.processEvents()
                self.assertEqual(third.size(), first.size())
            finally:
                for window in windows:
                    window.close()
                    window.plotter.close()
                self.app.removeEventFilter(store)
                if previous is not None:
                    self.app._rustmech_window_state = previous
                    self.app.installEventFilter(previous)
                else:
                    del self.app._rustmech_window_state

    def test_import_reaches_analysis_settings_and_can_be_cleared(self):
        fixture = geometry_tests.GeometryImportTests()
        fixture.setUp()
        try:
            settings = fixture.imported()
            result = (settings.imported_geometry_path, settings.size, settings.imported_geometry_name)
            with patch.object(RustMechDialog, 'start_preview', lambda self: None):
                dialog = RustMechDialog(settings)
                try:
                    self.assertEqual(dialog.collect().imported_geometry_path, result[0])
                    self.assertFalse(dialog.controls['size'].isEnabled())
                    self.assertEqual(dialog.family_combo.currentData(), 'imported')
                    self.assertTrue(dialog.family_combo.isEnabled())
                    import_index = dialog.family_combo.findData('imported')
                    with patch('geometry_import_dialog.choose_geometry', return_value=result) as choose:
                        dialog.family_combo.activated.emit(import_index)
                        choose.assert_called_once_with(dialog)
                    self.assertEqual(dialog.preview_settings().imported_geometry_path, result[0])
                    self.assertEqual(dialog.collect().size, settings.size)
                    dialog.family_combo.setCurrentIndex(dialog.family_combo.findData('lattice'))
                    self.assertFalse(dialog.collect().imported_geometry_path)
                    self.assertEqual(dialog.collect().structure_family, 'lattice')
                    self.assertEqual(dialog.preview_settings().imported_geometry_path, result[0])
                    dialog.structure_apply_button.click()
                    self.assertFalse(dialog.preview_settings().imported_geometry_path)
                    with patch('geometry_import_dialog.choose_geometry', return_value=None):
                        dialog.family_combo.setCurrentIndex(import_index)
                        dialog.family_combo.activated.emit(import_index)
                    self.assertEqual(dialog.family_combo.currentData(), 'lattice')
                    self.assertFalse(dialog.collect().imported_geometry_path)
                    with patch('geometry_import_dialog.choose_geometry', return_value=result):
                        dialog.family_combo.setCurrentIndex(import_index)
                        dialog.family_combo.activated.emit(import_index)
                    self.assertEqual(dialog.family_combo.currentData(), 'imported')
                    self.assertEqual(dialog.collect().structure_family, 'lattice')
                    self.assertFalse(dialog.preview_settings().imported_geometry_path)
                    dialog.clear_imported_geometry()
                    self.assertFalse(dialog.collect().imported_geometry_path)
                    self.assertTrue(dialog.controls['size'].isEnabled())
                    self.assertEqual(dialog.family_combo.currentData(), 'lattice')
                finally:
                    dialog.dispose()
                    dialog.close()
        finally:
            fixture.doCleanups()
