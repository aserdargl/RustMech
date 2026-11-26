"""Independent DoE workspace behavior and per-user window restoration."""
import tempfile
import copy
import json
from dataclasses import replace
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QScrollArea, QWidget

from doe_studio import DoEStudioWidget, DoEStudioWindow
from model import Settings
from studio_style import STUDIO_STYLESHEET
from window_state import WindowStateStore


class DoEWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = getattr(self.app, "_rustmech_window_state", None)
        if self.previous is not None:
            self.app.removeEventFilter(self.previous)
        self.store = WindowStateStore(self.app, QSettings(
            str(Path(self.temp.name) / "windows.ini"), QSettings.Format.IniFormat))
        self.app._rustmech_window_state = self.store
        self.windows = []
        viewport = patch('preview_viewport.create_preview_viewport',
                         side_effect=lambda *_, **kw: MagicMock(interactor=QWidget()))
        viewport.start()
        self.addCleanup(viewport.stop)
        builder = patch.object(DoEStudioWidget, '_geometry_surface_builder', lambda self: None)
        builder.start()
        self.addCleanup(builder.stop)

    def tearDown(self):
        for window in self.windows:
            window.close()
            window.centralWidget().dispose()
        self.app.removeEventFilter(self.store)
        if self.previous is not None:
            self.app._rustmech_window_state = self.previous
            self.app.installEventFilter(self.previous)
        else:
            del self.app._rustmech_window_state
        self.temp.cleanup()

    def window(self, *, preferences=None):
        studio = DoEStudioWidget(Settings, lambda _: None, preferences=preferences)
        window = DoEStudioWindow(studio)
        window.setStyleSheet(STUDIO_STYLESHEET)
        self.windows.append(window)
        window.open_workspace()
        self.app.processEvents()
        return window, studio

    def test_normal_window_reopens_without_losing_plan_or_triggering_actions(self):
        window, studio = self.window()
        self.assertFalse(window.isMaximized())
        self.assertFalse(window.isFullScreen())
        self.assertFalse(window.isModal())
        studio.add_design(Settings())
        studio.generate_table()
        self.assertEqual(len(studio.rows), 1)
        buttons = studio.findChildren(QPushButton)
        spies = [QSignalSpy(button.clicked) for button in buttons]
        studio.settings_tabs.setCurrentIndex(1)
        field = studio.common_strain_spin
        field.setFocus()
        field.selectAll()
        QTest.keyClicks(field, "12.5")
        QTest.keyClick(field, Qt.Key.Key_Return)
        self.assertEqual(field.value(), 12.5)
        self.assertTrue(all(spy.count() == 0 for spy in spies))
        window.close()
        window.open_workspace()
        self.assertEqual(len(studio.rows), 1)
        self.assertEqual(len(studio._designs), 1)
        self.assertTrue(window.isVisible())

    def test_classic_inputs_restore_without_restoring_generated_experiments(self):
        first, studio = self.window(preferences=self.store.settings)
        studio.add_design(replace(Settings(), cell_size=3.5, stl_resolution=0.075))
        studio.settings_tabs.setCurrentIndex(1)
        studio.sampling_combo.setCurrentIndex(studio.sampling_combo.findData("lhs"))
        studio.lhs_count_spin.setValue(14)
        studio.sampling_seed.setValue(23)
        enabled, low, high, levels = studio.factor_controls["cell_size"]
        enabled.setChecked(True)
        low.setValue(2.5)
        high.setValue(4.5)
        levels.setValue(4)
        studio.generate_table()
        self.assertTrue(studio.rows)
        first.close()

        _, restored = self.window(preferences=self.store.settings)
        self.assertEqual(restored._designs[0].cell_size, 3.5)
        self.assertEqual(restored._designs[0].stl_resolution, 0.075)
        self.assertEqual(restored.sampling_combo.currentData(), "lhs")
        self.assertEqual((restored.lhs_count_spin.value(), restored.sampling_seed.value()), (14, 23))
        enabled, low, high, levels = restored.factor_controls["cell_size"]
        self.assertTrue(enabled.isChecked())
        self.assertEqual((low.value(), high.value(), levels.value()), (2.5, 4.5, 4))
        self.assertFalse(restored.rows)
        self.assertIs(restored.settings_tabs.currentWidget(), restored.new_doe)

    def test_geometry_splitters_and_maximized_normal_bounds_restore(self):
        first, studio = self.window()
        first.resize(1060, 720)
        first.move(90, 80)
        self.app.processEvents()
        studio.vertical_splitter.setSizes([280, 340])
        studio.new_doe.splitter.setSizes([360, 660])
        self.app.processEvents()
        frame = first.frameGeometry()
        vertical = studio.vertical_splitter.sizes()
        horizontal = studio.new_doe.splitter.sizes()
        first.close()
        second, restored = self.window()
        self.assertEqual(second.frameGeometry(), frame)
        self.assertEqual(restored.vertical_splitter.sizes(), vertical)
        self.assertEqual(restored.new_doe.splitter.sizes(), horizontal)
        normal_size = second.size()
        second.showMaximized()
        self.app.processEvents()
        second.close()
        third, _ = self.window()
        self.assertTrue(third.isMaximized())
        third.showNormal()
        self.app.processEvents()
        self.assertEqual(third.size(), normal_size)

    def test_compact_window_wraps_actions_without_clipping(self):
        window, studio = self.window()
        window.resize(720, 600)
        for _ in range(5):
            self.app.processEvents()
        for tab in range(studio.settings_tabs.count()):
            studio.settings_tabs.setCurrentIndex(tab)
            self.app.processEvents()
            for button in studio.findChildren(QPushButton):
                if button.isVisible():
                    with self.subTest(tab=tab, action=button.text()):
                        ancestor = button.parentWidget()
                        while ancestor is not None:
                            if isinstance(ancestor, QScrollArea):
                                ancestor.ensureWidgetVisible(button)
                                self.app.processEvents()
                            ancestor = ancestor.parentWidget()
                        self.assertEqual(button.visibleRegion().boundingRect(), button.rect())

    def test_geometry_actions_and_matrix_tabs_fit_their_content(self):
        _, studio = self.window()
        studio.settings_tabs.setCurrentIndex(1)
        self.app.processEvents()
        navigation = studio.planner.navigation.layout()
        buttons = [navigation.itemAt(index).widget() for index in range(navigation.count())]
        self.assertEqual([button.text() for button in buttons],
                         [studio.settings_tabs.tabText(index)
                          for index in range(studio.settings_tabs.count())])
        open_plan, save_plan = studio.open_plan_btn, studio.save_plan_btn
        self.assertEqual(open_plan.size(), save_plan.size())
        self.assertLess(save_plan.width(), buttons[0].width())
        self.assertEqual(save_plan.x() - open_plan.geometry().right() - 1,
                         studio.planner.plan_footer.layout().spacing())
        self.assertEqual(len({button.width() for button in buttons}), 1)
        self.assertTrue(studio.settings_tabs.tabBar().isHidden())
        open_plan.setFocus()
        QTest.keyClick(open_plan, Qt.Key.Key_Tab)
        self.assertTrue(save_plan.hasFocus())
        self.assertEqual(studio.settings_tabs.tabText(0), "DoE")
        self.assertEqual(studio.settings_tabs.tabText(3), "DoE2")
        self.assertFalse(studio.table.isVisible())
        self.assertEqual(studio.vertical_splitter.handleWidth(), 3)
        self.assertFalse(studio.matrix_tabs.expanding())
        buttons[3].click()
        self.app.processEvents()
        self.assertEqual(studio.settings_tabs.currentIndex(), 3)
        self.assertTrue(buttons[3].isChecked())
        footer_corner = save_plan.mapTo(studio, save_plan.rect().bottomRight())
        margins = studio.layout().contentsMargins()
        self.assertEqual(footer_corner.x(), studio.rect().right() - margins.right())
        self.assertEqual(footer_corner.y(), studio.rect().bottom() - margins.bottom())
        matrix = studio.matrix_navigation.layout()
        experiments, learning = [matrix.itemAt(index).widget() for index in range(matrix.count())]
        self.assertLess(experiments.width() + learning.width(), studio.width() // 2)
        self.assertEqual(experiments.width(), learning.width())
        learning.click()
        self.assertEqual(studio.matrix_tabs.currentIndex(), 1)
        studio.matrix_tabs.setCurrentIndex(0)
        self.assertTrue(experiments.isChecked())
        studio.table.setFocus()
        QTest.keyClick(studio.table, Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(studio.settings_tabs.currentIndex(), 4)
        self.assertTrue(buttons[4].isChecked())
        for buttons in studio._action_groups:
            self.assertEqual(len({button.size().width() for button in buttons}), 1)
            for button in buttons:
                self.assertNotIn("…", button.text())
                self.assertNotIn("...", button.text())
                self.assertGreaterEqual(button.width(), button.fontMetrics().horizontalAdvance(button.text()) + 24)

    def test_doe_button_opens_settings_and_model_selection_stays_inside(self):
        from nonlinear_compression import RustMechDialog
        from solver_profiles import SolverProfileStore
        with patch.object(RustMechDialog, "start_preview", lambda self: None):
            dialog = RustMechDialog(Settings(), solver_profile_store=SolverProfileStore(self.store.settings))
        try:
            dialog.show()
            self.app.processEvents()
            self.assertEqual(dialog.doe_button.text(), "DoE")
            navigation = dialog.doe_button.parentWidget().layout()
            self.assertEqual(navigation.indexOf(dialog.esen_button),
                             navigation.indexOf(dialog.doe_button) + 1)
            self.assertFalse(dialog.doe_button.autoDefault())
            dialog.doe_button.click()
            self.assertTrue(dialog.doe_window.isVisible())
            self.assertIs(dialog.doe_studio.window(), dialog.doe_window)
            self.assertEqual(dialog.workspace_tabs.currentIndex(), 0)
            self.assertEqual(len(dialog.doe_studio._designs), 0)
            self.assertIs(dialog.doe_studio.use_model_btn.window(), dialog.doe_window)
            with patch.object(dialog, "refresh_validity"):
                dialog.doe_studio.use_model_btn.setEnabled(True)
                dialog.doe_studio.use_model_btn.click()
            self.assertEqual(len(dialog.doe_studio._designs), 1)
            dialog.doe_window.close()
            dialog.workspace_actions.actions()[1].trigger()
            self.app.processEvents()
            self.assertTrue(dialog.doe_window.isVisible())
            self.assertEqual(dialog.workspace_tabs.currentIndex(), 0)
            self.assertEqual(dialog.workspace_button.text(), "Mechanic")
        finally:
            dialog.close()
            dialog.dispose()

    def test_saved_designs_preserve_dimensions_and_defer_mesh_settings(self):
        _, studio = self.window()
        studio.add_design(replace(Settings(), size=20))
        for size in (8, 10):
            studio.add_design(replace(Settings(), size=size, cell_size=2, solid_fraction=35))
        self.assertEqual([design.size for design in studio._designs], [20, 8, 10])
        studio._designs[1] = replace(studio._designs[1], mesh_resolution=0)
        with patch('doe_studio.QMessageBox.warning') as warning:
            studio.generate_table()
            studio.generate_ml_table()
            warning.assert_not_called()
        for rows in studio._matrix_rows.values():
            self.assertEqual([row.size for row in rows], [20, 8, 10])
            self.assertEqual(rows[1].cell_size, 2)
            self.assertEqual(rows[1].solid_fraction, 35)

    def test_each_geometry_keeps_mesh_and_variables_in_generated_rows(self):
        _, studio = self.window()
        studio.add_design(Settings())
        studio.design_mesh_type.setCurrentText("TET10")
        studio.design_value_controls["mesh_resolution"].setValue(.4)
        studio.design_value_controls["local_mesh_size"].setValue(.08)
        enabled, low, high, count = studio.factor_controls["solid_fraction"]
        low.setValue(20)
        high.setValue(40)
        count.setValue(3)
        enabled.setChecked(True)
        studio.add_design(replace(Settings(), structure_name="Neovius"))
        self.assertFalse(enabled.isChecked())
        self.assertEqual(studio.design_mesh_type.currentText(), "HEX8")
        cell_enabled, cell_low, cell_high, cell_count = studio.factor_controls["cell_size"]
        cell_low.setValue(2)
        cell_high.setValue(3)
        cell_count.setValue(2)
        cell_enabled.setChecked(True)
        studio.design_list.setCurrentRow(0)
        self.assertTrue(enabled.isChecked())
        self.assertFalse(cell_enabled.isChecked())
        self.assertEqual(studio.design_mesh_type.currentText(), "TET10")
        studio.generate_table()
        self.assertEqual(len(studio.rows), 5)
        first = [row.to_settings(Settings()) for row in studio.rows[:3]]
        self.assertEqual([settings.solid_fraction for settings in first], [20, 30, 40])
        self.assertTrue(all(settings.mesh_type == "TET10" and settings.local_mesh_size == .08
                            and settings.mesh_resolution == .4 for settings in first))
        self.assertEqual([row.cell_size for row in studio.rows[3:]], [2, 3])

    def test_ml_matrix_is_separate_repeatable_and_exports_snapshot_and_splits(self):
        _, studio = self.window()
        studio.add_design(Settings())
        studio.factor_controls["solid_fraction"][0].setChecked(True)
        studio.generate_table()
        doe_rows = studio.rows
        studio.ml_count_spin.setValue(12)
        studio.generate_ml_table()
        ml_rows = studio.rows
        self.assertEqual(len(ml_rows), 12)
        self.assertEqual(studio.table.horizontalHeaderItem(22).text(), "Dataset split")
        self.assertEqual(studio.table.item(0, 22).text(), ml_rows[0].dataset_columns()[1])
        self.assertTrue(all(row.exp_id.startswith("ML-") for row in ml_rows))
        self.assertTrue(all(15 < row.solid_fraction < 45 for row in ml_rows))
        values = [row.solid_fraction for row in ml_rows]
        with patch("doe_studio.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            studio.generate_ml_table()
        self.assertEqual(values, [row.solid_fraction for row in studio.rows])
        studio.matrix_tabs.setCurrentIndex(0)
        self.assertIs(studio.rows, doe_rows)
        self.assertEqual(studio.table.columnCount(), 22)
        studio.matrix_tabs.setCurrentIndex(1)
        path = Path(self.temp.name) / "ml.csv"
        studio.export_csv_path(path)
        import csv
        with path.open(encoding="utf-8-sig", newline="") as stream:
            exported = list(csv.reader(stream))
        self.assertIn("Design settings (JSON)", exported[0])
        self.assertEqual(len(exported), 13)
        self.assertTrue(all(row.dataset_columns()[1] in ("train", "validation", "test") for row in studio.rows))
        studio._active_mode = "check"
        studio.matrix_tabs.setCurrentIndex(0)
        self.assertEqual(studio.matrix_tabs.currentIndex(), 1)
        self.assertEqual([row.solid_fraction for row in studio.rows], values)
        studio._active_mode = None

    def test_invalid_ranges_do_not_replace_existing_matrix(self):
        _, studio = self.window()
        studio.add_design(Settings())
        studio.generate_table()
        original = studio.rows
        enabled, low, high, _ = studio.factor_controls["cell_size"]
        low.setValue(5)
        high.setValue(8)
        enabled.setChecked(True)
        with patch("doe_studio.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), patch("doe_studio.QMessageBox.warning") as warning:
            studio.generate_table()
        warning.assert_called_once()
        self.assertIs(studio.rows, original)

    def test_saved_structure_groups_can_sweep_size_independently(self):
        _, studio = self.window()
        for name in ("Gyroid", "Neovius"):
            studio.add_design(replace(Settings(), structure_name=name, size=6))
            enabled, low, high, levels = studio.factor_controls["size"]
            self.assertFalse(enabled.isChecked())
            enabled.setChecked(True)
            low.setValue(6)
            high.setValue(7)
            levels.setValue(2)
            enabled, low, high, levels = studio.factor_controls["solid_fraction"]
            low.setValue(30)
            high.setValue(40)
            levels.setValue(2)
            enabled.setChecked(True)
        self.assertEqual([design.size for design in studio._designs], [6, 6])
        studio.generate_table()
        self.assertEqual(len(studio.rows), 8)
        self.assertEqual({(row.structure_name, row.size, row.solid_fraction) for row in studio.rows},
                         {(name, size, density) for name in ("Gyroid", "Neovius")
                          for size in (6, 7) for density in (30, 40)})
        studio.design_list.setCurrentRow(0)
        size_enabled = studio.factor_controls["size"][0]
        self.assertTrue(size_enabled.isChecked())
        size_enabled.setChecked(False)
        self.assertNotIn("size", studio._design_factors[0])
        self.assertIn("size", studio._design_factors[1])

    def test_saved_designs_exclude_duplicates_and_unchecked_designs(self):
        _, studio = self.window()
        for name in ("Gyroid", "Gyroid", "S. Diamond"):
            studio.add_design(replace(Settings(), structure_name=name))
        self.assertEqual([s.structure_name for s in studio._designs],
                         ["Gyroid", "S. Diamond"])
        studio.factor_controls["cell_size"][0].setChecked(True)
        studio.generate_table()
        self.assertEqual(len(studio.rows), 6)
        self.assertEqual([r.structure_name for r in studio.rows], ["Gyroid"] + ["S. Diamond"] * 5)
        self.assertEqual([r.cell_size for r in studio.rows[1:]], [2, 2.5, 3, 3.5, 4])
        for index in (0, 1):
            studio.design_list.item(index).setCheckState(Qt.CheckState.Unchecked)
        self.assertFalse(studio.generate_ml_btn.isEnabled())

    def test_entered_and_selected_dimensions_reach_generated_settings(self):
        _, studio = self.window()
        for size in (8, 12):
            studio.add_design(replace(Settings(), size=size))
        self.assertEqual([s.size for s in studio._designs], [8, 12])
        studio.design_list.setCurrentRow(0)
        studio.design_value_controls["size"].setValue(9)
        self.assertEqual([s.size for s in studio._designs], [9, 12])
        studio.generate_table()
        self.assertEqual([r.to_settings(Settings()).size for r in studio.rows], [9, 12])
        self.assertEqual(studio.settings_tabs.currentIndex(), 3)

    def test_group_bounds_two_variables_and_unique_experiments(self):
        _, studio = self.window()
        for cell, density in ((2, 20), (4, 40)):
            studio.add_design(replace(Settings(), cell_size=cell, solid_fraction=density))
        for field, limits in (('cell_size', (2, 4)), ('solid_fraction', (20, 40))):
            enabled, low, high, levels = studio.factor_controls[field]
            enabled.setChecked(True)
            levels.setValue(3)
            low.setValue(limits[0])
            high.setValue(limits[1])
        studio.design_list.setCurrentRow(0)
        self.assertEqual(set(studio._active_factors()), {'cell_size', 'solid_fraction'})
        # Reference dimensions remain editable while the field varies in experiments.
        self.assertTrue(studio.design_value_controls['solid_fraction'].isEnabled())
        studio.generate_table()
        self.assertEqual(len(studio.rows), 9)
        self.assertEqual({(r.cell_size, r.solid_fraction) for r in studio.rows},
                         {(c, d) for c in (2, 3, 4) for d in (20, 30, 40)})

    def test_saved_designs_keep_factors_separate_between_geometry_groups(self):
        _, studio = self.window()
        studio.add_design(Settings())
        enabled, low, high, _ = studio.factor_controls['solid_fraction']
        enabled.setChecked(True)
        low.setValue(20)
        high.setValue(50)
        studio.add_design(replace(Settings(), structure_name='Neovius'))
        self.assertFalse(enabled.isChecked())
        studio.design_list.setCurrentRow(0)
        self.assertEqual((low.value(), high.value()), (20, 50))

    def test_plan_file_roundtrip_restores_designs_variables_both_matrices_and_draft(self):
        _, studio = self.window()
        studio.add_design(Settings(hardening_curve=((0., 250.), (.1, 300.))))
        enabled, low, high, count = studio.factor_controls['solid_fraction']
        enabled.setChecked(True)
        low.setValue(20)
        high.setValue(40)
        count.setValue(3)
        studio.sampling_combo.setCurrentIndex(studio.sampling_combo.findData('lhs'))
        studio.lhs_count_spin.setValue(4)
        studio.sampling_seed.setValue(123)
        studio.ml_count_spin.setValue(5)
        studio.ml_seed_spin.setValue(321)
        studio.common_strain_check.setChecked(False)
        studio.common_strain_spin.setValue(15)
        studio.add_design(replace(Settings(), structure_name='Neovius'))
        studio.design_list.item(1).setCheckState(Qt.CheckState.Unchecked)
        studio.design_list.setCurrentRow(0)
        studio.generate_table()
        studio._rows[0].max_force_n = 123.4
        studio._rows[0].status = 'Completed'
        studio._rows[0].run_folder = 'D:/results/experiment-1'
        studio.generate_ml_table()
        studio.settings_tabs.setCurrentIndex(0)
        studio.planner.saved_draft = replace(studio.planner.saved_draft, size=9, solid_fraction=42)
        path = Path(self.temp.name) / 'plan.rustmech-doe.json'
        studio.save_plan_path(path)
        expected = studio.capture_plan()
        self.assertEqual(expected['draft']['solid_fraction'], 42)
        previous_plan = json.loads(path.read_text(encoding='utf-8'))
        previous_plan['selection']['tab'] = 0
        for rows in previous_plan['matrices'].values():
            for row in rows:
                row['folder_job'] = None
        path.write_text(json.dumps(previous_plan), encoding='utf-8')
        _, restored = self.window()
        restored.open_plan_path(path)
        self.app.processEvents()
        self.assertEqual(restored.settings_tabs.currentIndex(), 3)
        self.assertEqual(restored.capture_plan(), expected)
        self.assertEqual(restored._plan_path, path.resolve())
        self.assertEqual(restored.plan_file_label.text(), path.name)
        self.assertEqual(len(restored._matrix_rows[0]), 4)
        self.assertEqual(len(restored._matrix_rows[1]), 5)
        self.assertIsNone(restored._active_future)

    def test_invalid_plan_keeps_current_workspace_and_failed_save_keeps_existing_file(self):
        _, studio = self.window()
        studio.add_design(Settings())
        studio.generate_table()
        original = studio.capture_plan()
        corruptions = (
            lambda data: data.update(version=999),
            lambda data: data['options'].update(lhs_count='bad'),
            lambda data: data['designs'][0].update(factors={'cell_size': [2, 4, 1000]}),
            lambda data: data['matrices']['doe'][0].update(size='bad'),
            lambda data: data['draft'].update(tpms_morphology='unknown'),
        )
        path = Path(self.temp.name) / 'invalid.json'
        for corrupt in corruptions:
            payload = copy.deepcopy(original)
            corrupt(payload)
            path.write_text(json.dumps(payload), encoding='utf-8')
            with self.assertRaises(ValueError):
                studio.open_plan_path(path)
            self.assertEqual(studio.capture_plan(), original)
        path.write_text('keep the previous file', encoding='utf-8')
        with patch('doe_plan.QSaveFile') as target:
            target.return_value.open.return_value = True
            target.return_value.write.return_value = 0
            with self.assertRaises(OSError):
                studio.save_plan_path(path)
            target.return_value.cancelWriting.assert_called_once()
            target.return_value.commit.assert_not_called()
        self.assertEqual(path.read_text(encoding='utf-8'), 'keep the previous file')
        self.assertIsNone(studio._plan_path)

    def test_save_open_buttons_dialogs_cancel_and_running_batch(self):
        _, studio = self.window()
        studio.add_design(Settings())
        destination = Path(self.temp.name) / 'saved-plan'
        with patch('doe_studio.QFileDialog.getSaveFileName', return_value=(str(destination), '')):
            studio.save_plan_btn.click()
        path = destination.with_name(destination.name + '.rustmech-doe.json')
        self.assertTrue(path.is_file())
        studio.design_value_controls['solid_fraction'].setValue(45)
        changed = studio.capture_plan()
        with patch('doe_studio.QFileDialog.getOpenFileName', return_value=(str(path), '')), \
                patch('doe_studio.QMessageBox.question', return_value=QMessageBox.StandardButton.Cancel):
            studio.open_plan_btn.click()
        self.assertEqual(studio.capture_plan(), changed)
        with patch('doe_studio.QFileDialog.getOpenFileName', return_value=(str(path), '')), \
                patch('doe_studio.QMessageBox.question', return_value=QMessageBox.StandardButton.Discard):
            studio.open_plan_btn.click()
        self.assertEqual(studio._designs[0].solid_fraction, 30)
        studio._active_mode = 'solve'
        studio._refresh_plan()
        self.assertFalse(studio.save_plan_btn.isEnabled())
        self.assertFalse(studio.open_plan_btn.isEnabled())
        with self.assertRaises(RuntimeError):
            studio.open_plan_path(path)
        with self.assertRaises(RuntimeError):
            studio.save_plan_path(path)
        studio._active_mode = None

    def test_disabled_factors_keep_reference_values(self):
        _, studio = self.window()
        studio.add_design(Settings())
        enabled, low, high, count = studio.factor_controls["solid_fraction"]
        enabled.setChecked(True)
        self.assertTrue(low.isEnabled())
        enabled.setChecked(False)
        self.assertFalse(low.isEnabled())
        self.assertEqual(len(studio._designs), 1)
        self.assertEqual(studio.rows, [])
        studio.generate_table()
        self.assertEqual([r.solid_fraction for r in studio.rows], [30])


if __name__ == "__main__":
    unittest.main()

