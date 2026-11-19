"""Material display units must preserve the solver's MPa values."""
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QStyle, QStyleOptionButton, QToolButton
from material_editor import MaterialEditor
from model import Settings, save_material, plastic_curve_from_total_true_strain


class MaterialUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = Settings(hardening_curve=((0.0, 250.0), (0.1, 350.0)))
        self.editor = MaterialEditor(self.settings)

    def tearDown(self):
        self.editor.close()

    def test_heading_and_visible_content(self):
        self.assertEqual(self.editor.title(), "Material")
        self.assertFalse(self.editor.content.isHidden())
        self.assertFalse(self.editor.findChildren(QToolButton))

    def test_material_actions_have_label_padding_and_equal_sizes(self):
        from studio_style import STUDIO_STYLESHEET

        for elastic_only in (False, True):
            with self.subTest(elastic_only=elastic_only):
                editor = MaterialEditor(Settings(material_model="multilinear_isotropic"),
                                        elastic_only=elastic_only)
                try:
                    editor.setStyleSheet(STUDIO_STYLESHEET)
                    editor.library_button.setText("Kütüphane")
                    editor.load_button.setText("Yükle")
                    editor.edit_button.setText("Düzenle")
                    editor.resize(245, 650)
                    editor.show()
                    self.app.processEvents()
                    editor.library_button.setFocus()
                    toolbar = [button for button in editor.findChildren(QPushButton)
                               if button.isVisible() and button not in editor.curve_buttons]
                    self.assertEqual(len({(button.width(), button.height()) for button in toolbar}), 1)
                    for button in toolbar:
                        option = QStyleOptionButton()
                        button.initStyleOption(option)
                        content = button.style().subElementRect(
                            QStyle.SubElement.SE_PushButtonContents, option, button)
                        self.assertGreaterEqual(content.left(), 8)
                        self.assertGreaterEqual(content.width(), button.fontMetrics().horizontalAdvance(button.text()))
                    if not elastic_only:
                        self.assertEqual(len({button.width() for button in editor.curve_buttons}), 1)
                finally:
                    editor.close()

    def test_title_click_toggles_and_preserves_material(self):
        from studio_style import STUDIO_STYLESHEET
        self.editor.setStyleSheet(STUDIO_STYLESHEET)
        self.editor.resize(340, 500)
        self.editor.show()
        self.app.processEvents()
        original = self.editor.material_settings()
        position = self.editor.header_rect().center()
        QTest.mouseClick(self.editor, Qt.MouseButton.LeftButton, pos=position)
        self.assertTrue(self.editor.content.isHidden())
        self.assertEqual(self.editor.title(), "Material")
        QTest.mouseClick(self.editor, Qt.MouseButton.LeftButton, pos=position)
        self.assertFalse(self.editor.content.isHidden())
        self.assertEqual(self.editor.material_settings(), original)
        QTest.keyClick(self.editor, Qt.Key.Key_Space)
        self.assertTrue(self.editor.content.isHidden())
        QTest.keyClick(self.editor, Qt.Key.Key_Return)
        self.assertFalse(self.editor.content.isHidden())

    def test_body_click_does_not_collapse(self):
        self.editor.show()
        self.app.processEvents()
        QTest.mouseClick(self.editor.name, Qt.MouseButton.LeftButton)
        self.assertFalse(self.editor.content.isHidden())

    def test_all_units_round_trip_properties_and_curve(self):
        for unit in ("GPa", "Pa", "kPa", "psi", "ksi", "MPa"):
            with self.subTest(unit=unit):
                self.editor.stress_unit.setCurrentText(unit)
                actual = self.editor.material_settings()
                for key in self.editor.controls:
                    self.assertAlmostEqual(getattr(actual, key), getattr(self.settings, key), delta=1e-7)
                for actual_point, expected in zip(actual.hardening_curve, self.settings.hardening_curve):
                    self.assertAlmostEqual(actual_point[0], expected[0])
                    self.assertAlmostEqual(actual_point[1], expected[1], places=7)
                self.assertIn(unit, self.editor.table.horizontalHeaderItem(1).text())

    def test_edit_load_save_and_csv_in_selected_unit(self):
        self.editor.stress_unit.setCurrentText("GPa")
        self.editor.controls["young_modulus"].setValue(210)
        self.assertEqual(self.editor.numeric_values()["young_modulus"], 210000)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "material.json"
            save_material(self.editor.material_settings(), path)
            self.editor.load_path(path)
            self.assertEqual(self.editor.controls["young_modulus"].value(), 210)
            csv = Path(folder) / "curve.csv"
            csv.write_text("plastic_strain,stress\n0,0.25\n0.1,0.35\n", encoding="utf-8")
            self.editor.import_curve_path(csv)
            self.assertEqual(self.editor.extra_values()["hardening_curve"], ((0, 250), (0.1, 350)))
            self.assertEqual(self.editor.table.item(1, 1).text(), "0.35")

    def test_total_true_csv_converts_and_persists_plastic_curve(self):
        self.editor.stress_unit.setCurrentText("GPa")
        with tempfile.TemporaryDirectory() as folder:
            csv = Path(folder) / "true.csv"
            csv.write_text("true_strain,true_stress_GPa\n0.00125,0.25\n0.10175,0.35\n", encoding="utf-8")
            self.editor.import_curve_path(csv)
            actual = self.editor.material_settings()
            self.assertEqual(actual.material_model, "multilinear_isotropic")
            self.assertEqual(actual.hardening_curve[0], (0, 250))
            self.assertAlmostEqual(actual.hardening_curve[1][0], 0.1)
            self.assertEqual(actual.hardening_curve[1][1], 350)
            path = Path(folder) / "material.json"
            save_material(actual, path)
            self.editor.load_path(path)
            self.assertEqual(self.editor.material_settings(), actual)

    def test_invalid_csv_preserves_material(self):
        original = self.editor.material_settings()
        for data in (
            "engineering_strain,engineering_stress\n0,250\n0.1,350\n",
            "true_strain,true_stress\n0,0\n0.00125,250\n0.10175,350\n",
            "true_strain,true_stress\n0.00325,250\n0.10175,350\n",
            "true_strain,true_stress\n0.00125,250\n0.0015,350\n",
            "true_strain,true_stress_GPa\n0.00125,250\n0.10175,350\n",
        ):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as folder:
                csv = Path(folder) / "bad.csv"
                csv.write_text(data, encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.editor.import_curve_path(csv)
                self.assertEqual(self.editor.material_settings(), original)

    def test_total_true_conversion_rejects_nonphysical_data(self):
        for curve, modulus in (
            (((0.00125, 250), (float("nan"), 350)), 200000),
            (((0.00125, 250), (0.10175, 350)), 0),
            (((0.00125, 250), (0.101, 200)), 200000),
            (((0.00125, 250), (0.00125, 250)), 200000),
        ):
            with self.subTest(curve=curve, modulus=modulus), self.assertRaises(ValueError):
                plastic_curve_from_total_true_strain(curve, modulus)


if __name__ == "__main__":
    unittest.main()
