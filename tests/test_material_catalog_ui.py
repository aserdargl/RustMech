"""Library selection, provenance, units and per-user window/choice persistence."""
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QEvent, QObject, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from material_catalog import builtin_materials
from material_editor import MaterialEditor
from material_library import MaterialCard, read_material_card
from material_library_dialog import MaterialLibraryDialog
from model import Settings, save_material
from window_state import WindowStateStore


class MaterialCatalogTests(unittest.TestCase):
    def test_all_cards_are_valid_sourced_and_independent(self):
        entries = builtin_materials()
        self.assertEqual(len(entries), 13)
        self.assertEqual(len({entry.identifier for entry in entries}), len(entries))
        for entry in entries:
            with self.subTest(entry=entry.identifier):
                card = entry.card()
                self.assertEqual(MaterialCard.from_dict(card.to_dict()).to_dict(), card.to_dict())
                self.assertTrue(entry.references)
                for _, url, _ in entry.references:
                    self.assertTrue(url.startswith("https://"))
                    self.assertIn(url, card.source)
                self.assertIn(entry.condition, card.source)
                if entry.kind == "solid":
                    self.assertEqual(card.mechanical_values()["material_model"], "linear_elastic")
                    self.assertFalse(card.hardening_curve)
                else:
                    card.evaluate(293.15, ("density", "dynamic_viscosity", "heat_capacity", "conductivity"))
        entries[0].properties["density"] = 1
        self.assertEqual(builtin_materials()[0].properties["density"], 8000)

    def test_resin_is_named_and_density_curing_state_is_not_invented(self):
        resin = next(e for e in builtin_materials() if e.identifier == "abs_resin")
        self.assertIn("Anycubic", resin.name)
        self.assertIn("engineering estimate", resin.assumptions)
        self.assertIn("NOT a measured cured-part density", resin.assumptions)
        self.assertNotIn("yield_stress", resin.properties)


class MaterialCatalogUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.temp.name) / "library.ini"), QSettings.Format.IniFormat)
        self.windows = []
        self.store = WindowStateStore(self.app, self.settings)

    def tearDown(self):
        for window in self.windows:
            window.close()
        self.app.removeEventFilter(self.store)
        self.temp.cleanup()

    def editor(self, settings=None):
        editor = MaterialEditor(settings or Settings(), library_settings=self.settings)
        self.windows.append(editor)
        return editor

    def dialog(self, **kwargs):
        dialog = MaterialLibraryDialog(library_settings=self.settings, **kwargs)
        self.windows.append(dialog)
        return dialog

    def test_add_saves_custom_solid_and_reopens_it_for_selection(self):
        editor = self.editor()
        before = editor.material_settings()
        editor.library_button.click()
        dialog = editor.library_dialog
        dialog.search.setText("Our alloy")
        self.assertIsNone(dialog.selected_entry())
        self.assertLess(dialog.material_list.geometry().bottom(), dialog.new_button.geometry().top())
        dialog.new_button.click()
        draft = dialog.draft
        draft.name.setText("Our alloy")
        draft.fields["density"].setValue(7900)
        draft.fields["young_modulus"].setValue(204000)
        draft.fields["poisson_ratio"].setValue(.3)
        draft.optional["conductivity"].setChecked(True)
        draft.fields["conductivity"].setValue(16)
        QTest.keyClick(draft.name, Qt.Key.Key_Return)
        self.assertIsNone(dialog.selected_entry())
        dialog.save_button.click()
        self.assertTrue(dialog.isVisible())
        self.assertEqual(editor.material_settings(), before)
        saved = dialog.selected_entry()
        self.assertEqual(saved.name, "Our alloy")
        self.assertEqual(saved.card().properties, {
            "density": 7900, "young_modulus": 204e9, "poisson_ratio": .3, "conductivity": 16,
        })
        dialog.close_button.click()
        self.settings = QSettings(self.settings.fileName(), QSettings.Format.IniFormat)
        thermal = self.dialog(required=("density", "conductivity", "heat_capacity"))
        thermal.search.setText("Our alloy")
        self.assertEqual(thermal.selected_entry().identifier, saved.identifier)
        self.assertFalse(thermal.add_button.isEnabled())
        reopened = self.editor()
        reopened.library_button.click()
        reopened.library_dialog.search.setText("Our alloy")
        reopened.library_dialog.add_button.click()
        self.assertEqual(reopened.material_settings().material_card, saved.card().to_dict())
        self.assertGreater(self.editor().material_selector.findData(saved.identifier), 0)

    def test_add_fluid_validates_and_cancel_discards_the_draft(self):
        dialog = self.dialog(kind="fluid")
        dialog.show()
        dialog.new_button.click()
        draft = dialog.draft
        draft.name.setText("My fluid")
        dialog.save_button.click()
        self.assertIn("density", dialog.status.text())
        draft.fields["density"].setValue(1000)
        draft.fields["dynamic_viscosity"].setValue(.001)
        dialog.cancel_button.click()
        self.assertEqual(len(self.dialog(kind="fluid").entries), 2)
        dialog.new_button.click()
        self.assertEqual(draft.name.text(), "")
        draft.name.setText("My fluid")
        draft.fields["density"].setValue(1000)
        draft.fields["dynamic_viscosity"].setValue(.001)
        dialog.save_button.click()
        self.assertEqual(dialog.selected_entry().card().properties,
                         {"density": 1000, "dynamic_viscosity": .001})
        dialog.new_button.click()
        draft.name.setText("my fluid")
        draft.fields["density"].setValue(999)
        draft.fields["dynamic_viscosity"].setValue(.001)
        dialog.save_button.click()
        self.assertIn("already exists", dialog.status.text())
        self.assertEqual(len(self.dialog(kind="fluid").entries), 3)

    def test_constructing_material_panels_shows_no_standalone_window(self):
        shown = []

        class ShowObserver(QObject):
            def eventFilter(self, widget, event):
                if event.type() == QEvent.Type.Show and isinstance(widget, QWidget) and widget.isWindow():
                    shown.append(widget.objectName())
                return False

        observer = ShowObserver()
        self.app.installEventFilter(observer)
        self.addCleanup(self.app.removeEventFilter, observer)
        for elastic_only in (False, True):
            editor = MaterialEditor(Settings(), elastic_only=elastic_only, library_settings=self.settings)
            self.windows.append(editor)
            self.assertFalse(editor.library_button.isHidden())
            self.assertEqual(editor.save_button.isHidden(), elastic_only)
        self.assertEqual(shown, [])

    def test_browsing_enter_cancel_do_not_change_editor_add_selects_and_deduplicates(self):
        editor = self.editor()
        before = editor.material_settings()
        editor.library_button.click()
        dialog = editor.library_dialog
        self.app.processEvents()
        dialog.search.setText("Inconel 625")
        self.assertEqual(editor.material_settings(), before)
        self.assertEqual(dialog.selected_entry().identifier, "in625")
        self.assertIn("specialmetals.com", dialog.sources.toHtml())
        QTest.keyClick(dialog.search, Qt.Key.Key_Return)
        self.assertTrue(dialog.isVisible())
        self.assertEqual(editor.material_settings(), before)
        dialog.close_button.click()
        self.assertEqual(editor.material_settings(), before)
        editor.library_button.click()
        dialog.add_button.click()
        self.assertEqual(editor.material_selector.count(), 2)
        self.assertEqual(editor.material_selector.currentData(), "in625")
        actual = editor.material_settings()
        self.assertAlmostEqual(actual.young_modulus, 207500)
        self.assertAlmostEqual(actual.material_density, 8.44)
        self.assertEqual(actual.material_model, "linear_elastic")
        editor.add_library_material(next(e for e in builtin_materials() if e.identifier == "in625").card())
        self.assertEqual(editor.material_selector.count(), 2)

    def test_switch_preserves_custom_edits_unit_and_added_choices_survive_new_editor(self):
        editor = self.editor()
        editor.edit_button.setChecked(True)
        editor.name.setText("My measured alloy")
        editor.controls["young_modulus"].setValue(123456)
        original = editor.material_settings()
        editor.stress_unit.setCurrentText("GPa")
        entry = next(e for e in builtin_materials() if e.identifier == "in718")
        editor.add_library_material(entry.card())
        self.assertAlmostEqual(editor.controls["young_modulus"].value(), 199.9479615)
        editor.edit_button.setChecked(True)
        editor.controls["young_modulus"].setValue(190)
        editor.material_selector.setCurrentIndex(0)
        self.assertEqual(editor.material_settings(), original)
        self.assertTrue(editor.edit_button.isChecked())
        editor.material_selector.setCurrentIndex(1)
        self.assertEqual(editor.material_settings().young_modulus, 190000)
        self.assertEqual(editor.material_settings().material_card["properties"]["young_modulus"], 190e9)
        self.assertIn("User-modified", editor.material_settings().material_card["source"])
        reopened = self.editor(editor.material_settings())
        self.assertGreaterEqual(reopened.material_selector.findData("in718"), 1)
        self.assertEqual(reopened.material_selector.currentData(), "in718")
        self.assertEqual(reopened.material_settings().young_modulus, 190000)

    def test_mechanical_edits_preserve_thermal_data_and_unedited_temperature_tables(self):
        card = MaterialCard("Measured steel", "solid", {
            "density": {"temperature_K": [293.15, 353.15], "values": [8000, 7800]},
            "young_modulus": 200e9, "poisson_ratio": .3,
            "conductivity": 15, "heat_capacity": 500}, source="Measured laboratory data")
        editor = self.editor(Settings(**card.mechanical_values()))
        self.assertEqual(editor.material_settings().material_card, card.to_dict())
        editor.edit_button.setChecked(True)
        editor.controls["material_density"].setValue(7.9)
        edited = editor.material_settings().material_card
        self.assertEqual(edited["properties"]["density"], 7900)
        self.assertEqual(edited["properties"]["heat_capacity"], 500)
        self.assertEqual(edited["properties"]["conductivity"], 15)
        self.assertIn("User-modified", edited["source"])

    def test_save_load_and_project_replacement_keep_sources_and_drop_stale_selection(self):
        editor = self.editor()
        editor.add_library_material(next(e for e in builtin_materials() if e.identifier == "abs_resin").card())
        before = editor.material_settings()
        path = Path(self.temp.name) / "resin.json"
        save_material(before, path)
        self.assertIn("ANYCUBIC-TDS", read_material_card(path).source)
        editor.load_path(path)
        self.assertEqual(editor.material_settings(), before)
        self.assertEqual(editor.material_selector.currentData(), "current")
        editor.set_current_material(asdict(Settings(material_name="Project material", young_modulus=55000)))
        self.assertEqual(editor.material_settings().material_name, "Project material")
        self.assertIsNone(editor.material_settings().material_card)

    def test_search_empty_state_sources_and_missing_thermal_data(self):
        dialog = self.dialog(required=("density", "heat_capacity", "conductivity"))
        dialog.search.setText("Anycubic")
        self.assertFalse(dialog.add_button.isEnabled())
        self.assertIn("not supplied", dialog.status.text())
        text = dialog.details.toPlainText()
        self.assertIn("Engineering assumptions", text)
        self.assertIn("estimate", text)
        self.assertIn("Datasheets and references", dialog.sources.toPlainText())
        self.assertIn("ANYCUBIC-TDS", dialog.sources.toHtml())
        dialog.search.setText("no such material")
        self.assertIsNone(dialog.selected_entry())
        self.assertFalse(dialog.add_button.isEnabled())
        self.assertFalse(dialog.details.toPlainText())
        dialog.search.clear()
        self.assertTrue(dialog.add_button.isEnabled())
        fluid = self.dialog(kind="fluid", required=("density", "dynamic_viscosity"))
        self.assertEqual(len(fluid.entries), 2)
        self.assertTrue(all(entry.kind == "fluid" for entry in fluid.entries))

    def test_geometry_splitter_maximized_normal_bounds_and_offscreen_restore(self):
        first = self.dialog()
        first.resize(820, 560)
        first.move(70, 80)
        first.show()
        self.app.processEvents()
        first.splitter.setSizes([240, 550])
        expected_frame, expected_sizes = first.frameGeometry(), first.splitter.sizes()
        first.details_splitter.setSizes([300, 180])
        expected_details_sizes = first.details_splitter.sizes()
        first.close()
        second = self.dialog()
        second.show()
        self.app.processEvents()
        self.assertEqual(second.frameGeometry(), expected_frame)
        self.assertEqual(second.splitter.sizes(), expected_sizes)
        self.assertEqual(second.details_splitter.sizes(), expected_details_sizes)
        normal = second.size()
        second.showMaximized()
        self.app.processEvents()
        second.close()
        third = self.dialog()
        third.show()
        self.app.processEvents()
        self.assertTrue(third.isMaximized())
        third.showNormal()
        self.app.processEvents()
        self.assertEqual(third.size(), normal)
        third.move(-20000, -20000)
        third.close()
        fourth = self.dialog()
        fourth.show()
        self.app.processEvents()
        self.assertTrue(self.app.primaryScreen().availableGeometry().intersects(fourth.frameGeometry()))

    def test_flow_and_thermal_add_apply_only_to_the_requested_phase_without_solving(self):
        from thermal_workspace import ThermalWorkspace
        workspace = ThermalWorkspace(lambda: Settings(), preferences=self.settings,
            viewport_factory=lambda parent: MagicMock(interactor=QWidget(parent)))
        self.windows.append(workspace)
        try:
            with patch("thermal_workspace.AnalysisProcess") as process:
                workspace.fluid_library_button.click()
                fluid = workspace.fluid_library_dialog
                fluid.search.setText("Water")
                fluid.add_button.click()
                self.assertAlmostEqual(workspace.collect().density, 998.2)
                self.assertEqual(workspace.collect_thermal().water_cp, 4182)
                self.assertIn("coolprop.org", workspace.collect().fluid_material_card["source"])
                before = workspace.collect()
                workspace.solid_library_button.click()
                solid = workspace.solid_library_dialog
                solid.search.setText("Inconel 625")
                solid.add_button.click()
                self.assertEqual(workspace.collect(), before)
                self.assertEqual(workspace.collect_thermal().solid_cp, 410)
                self.assertAlmostEqual(workspace.collect_thermal().solid_conductivity, 9.8)
                process.assert_not_called()
                self.assertIsNone(workspace.job)
        finally:
            workspace.dispose()


if __name__ == "__main__":
    unittest.main()
