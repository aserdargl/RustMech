"""Limited-data estimates must stay explicit, unit-safe and clearly approximate."""
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from material_editor import MaterialEditor
from model import Settings
from tangent_modulus_help import TangentModulusHelpDialog, estimate_tangent_modulus
from window_state import WindowStateStore


class TangentHelpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_datasheet_example_and_invalid_data(self):
        self.assertAlmostEqual(estimate_tangent_modulus(200000, 250, 400, 20), 754.7169811320755)
        self.assertEqual(estimate_tangent_modulus(200000, 250, 250, 20), 0)
        for values in ((0, 250, 400, 20), (200000, 250, 200, 20),
                       (200000, 250, 400, 0.1), (200000, 250, 400, 0.15),
                       (float('nan'), 250, 400, 20), (200000, 250, 400, float('inf'))):
            with self.subTest(values=values), self.assertRaises(ValueError):
                estimate_tangent_modulus(*values)

    def test_help_only_applies_on_click_and_preserves_selected_units(self):
        editor = MaterialEditor(Settings(material_model="linear_elastic"))
        editor.stress_unit.setCurrentText("GPa")
        original = editor.material_settings()
        editor.show_tangent_help()
        dialog = editor.tangent_help
        try:
            self.app.processEvents()
            self.assertEqual(editor.material_settings(), original)
            self.assertFalse(dialog.use_button.isEnabled())
            self.assertIn("engineering secant proxy", dialog.guide.toPlainText())
            self.assertIn("754.717", dialog.guide.toPlainText())
            dialog.inputs["uts"].setValue(0.4)
            elongation = dialog.inputs["elongation"]
            elongation.setFocus()
            elongation.lineEdit().selectAll()
            QTest.keyClicks(elongation.lineEdit(), "20")
            selected = QSignalSpy(dialog.estimate_selected)
            for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                QTest.keyClick(elongation.lineEdit(), key)
                self.app.processEvents()
                self.assertTrue(dialog.isVisible())
                self.assertEqual(selected.count(), 0)
                self.assertEqual(editor.material_settings(), original)
            self.assertTrue(dialog.use_button.isEnabled())
            QTest.mouseClick(dialog.use_button, Qt.MouseButton.LeftButton)
            actual = editor.material_settings()
            self.assertEqual(actual.material_model, "bilinear_isotropic")
            self.assertAlmostEqual(actual.tangent_modulus, 754.7169811320755, places=6)
            self.assertEqual(actual.young_modulus, 200000)
            self.assertEqual(actual.yield_stress, 250)
            self.assertTrue(editor.edit_button.isChecked())
            self.assertEqual(selected.count(), 1)
        finally:
            dialog.close()
            editor.close()

    def test_helper_restores_geometry_maximized_state_and_normal_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WindowStateStore(self.app, QSettings(
                str(Path(directory) / "help.ini"), QSettings.Format.IniFormat))
            windows = []
            try:
                first = TangentModulusHelpDialog(200000, 250)
                windows.append(first)
                first.show()
                self.app.processEvents()
                first.resize(620, 540)
                area = self.app.primaryScreen().availableGeometry()
                first.move(area.left() + 80, area.top() + 80)
                self.app.processEvents()
                bounds = first.geometry()
                first.close()
                second = TangentModulusHelpDialog(200000, 250)
                windows.append(second)
                second.show()
                self.app.processEvents()
                self.assertEqual(second.geometry(), bounds)
                second.showMaximized()
                self.app.processEvents()
                second.close()
                third = TangentModulusHelpDialog(200000, 250)
                windows.append(third)
                third.show()
                self.app.processEvents()
                self.assertTrue(third.isMaximized())
                third.showNormal()
                self.app.processEvents()
                self.assertEqual(third.size(), bounds.size())
                self.assertTrue(self.app.primaryScreen().availableGeometry().intersects(third.frameGeometry()))
            finally:
                for window in windows:
                    window.close()
                self.app.removeEventFilter(store)


if __name__ == "__main__":
    unittest.main()
