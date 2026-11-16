"""Independent DoE workspace behavior and per-user window restoration."""
import tempfile
import copy
import json
from dataclasses import replace
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton, QScrollArea, QWidget

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

    def window(self):
        studio = DoEStudioWidget(Settings, lambda _: None)
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
        spin = studio.common_strain_spin
        spin.setFocus()
        spin.lineEdit().selectAll()
        QTest.keyClicks(spin.lineEdit(), "12.5")
        QTest.keyClick(spin.lineEdit(), Qt.Key.Key_Return)
        self.assertEqual(spin.value(), 12.5)
        self.assertTrue(all(len(spy) == 0 for spy in spies))
        window.close()
        window.open_workspace()
        self.assertEqual(len(studio.rows), 1)
        self.assertEqual(len(studio._designs), 1)
        self.assertTrue(window.isVisible())

    def test_geometry_splitters_and_maximized_normal_bounds_restore(self):
        first, studio = self.window()
        first.resize(1060, 720)
        first.move(90, 80)
        self.app.processEvents()
        studio.vertical_splitter.setSizes([280, 340])
        studio.plan_splitter.setSizes([360, 660])
        self.app.processEvents()
        frame = first.frameGeometry()
        vertical = studio.vertical_splitter.sizes()
        horizontal = studio.plan_splitter.sizes()
        first.close()
        second, restored = self.window()
        self.assertEqual(second.frameGeometry(), frame)
        self.assertEqual(restored.vertical_splitter.sizes(), vertical)
        self.assertEqual(restored.plan_splitter.sizes(), horizontal)
        normal_size = second.size()
        second.showMaximized()
        self.app.processEvents()
        second.close()
        third, _ = self.window()
        self.assertTrue(third.isMaximized())
        third.showNormal()
        self.app.processEvents()
        self.assertEqual(third.size(), normal_size)

    def test_new_geometry_contains_preview_then_parameters_without_companion(self):
        window, studio = self.window()
        panel = studio.planner.geometry_panel
        self.assertFalse(hasattr(window, 'geometry_window'))
        self.assertFalse(hasattr(studio, 'geometry_toggle'))
        self.assertIs(panel.window(), window)
        self.assertTrue(panel.splitter.widget(0).isAncestorOf(panel.preview_host))
        self.assertTrue(panel.splitter.widget(1).isAncestorOf(panel.values['size']))
        self.assertEqual({b.text() for b in panel.findChildren(QPushButton)}, {'Apply', 'Add'})
        self.assertNotIn('Use current model', [b.text() for b in studio.findChildren(QPushButton)])

    def test_embedded_preview_splitter_restores_without_separate_window(self):
        first, studio = self.window()
        first.resize(1180, 850)
        self.app.processEvents()
        panel = studio.planner.geometry_panel
        panel.splitter.setSizes([300, 280])
        self.app.processEvents()
        sizes = panel.splitter.sizes()
        first.close()
        second, restored = self.window()
        self.assertEqual(restored.planner.geometry_panel.splitter.sizes(), sizes)
        self.assertFalse(hasattr(second, 'geometry_window'))

    def test_preview_apply_uses_draft_without_starting_analysis_or_adding(self):
        from concurrent.futures import Future
        _, studio = self.window()
        panel = studio.planner.geometry_panel
        panel.timer.stop()
        panel.refresh()
        with patch.object(studio, '_geometry_surface_builder', return_value=None):
            studio.planner.values['solid_fraction'].setValue(35)
            self.assertEqual(studio._designs, [])
            panel._apply()
            self.assertEqual(panel._requested_settings.solid_fraction, 35)
            self.assertEqual(studio._designs, [])
        pending = Future()
        panel._future = (panel.selected_settings(), pending)
        studio.planner.values['size'].lineEdit().setText('8')
        panel.add_button.click()
        self.assertEqual(studio._designs[0].size, 8)
        self.assertEqual(studio._designs[0].solid_fraction, 35)
        self.assertIsNone(studio._active_future)
        pending.cancel()

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
        self.assertEqual(studio.settings_tabs.tabText(0), "Design experiments")
        self.assertEqual(studio.settings_tabs.tabText(3), "Experiment matrix")
        self.assertEqual(studio.plan_splitter.count(), 3)
        self.assertEqual(studio.add_structure_btn.text(), "Add")
        self.assertFalse(studio.table.isVisible())
        self.assertEqual(studio.plan_splitter.handleWidth(), 3)
        self.assertEqual(studio.vertical_splitter.handleWidth(), 3)
        self.assertFalse(studio.matrix_tabs.expanding())
        studio.settings_tabs.setCurrentIndex(3)
        self.app.processEvents()
        self.assertLess(studio.matrix_tabs.width(), studio.width() // 2)
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
            self.assertEqual(navigation.indexOf(dialog.doe_button),
                             navigation.indexOf(dialog.workspace_button) + 1)
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

    def test_embedded_add_preserves_dimensions_and_defers_mesh_settings(self):
        _, studio = self.window()
        panel = studio.planner.geometry_panel
        panel.timer.stop()
        studio.add_design(replace(Settings(), size=20))
        panel.values['size'].lineEdit().setText('8')
        panel.values['cell_size'].lineEdit().setText('2')
        panel.values['solid_fraction'].lineEdit().setText('35')
        panel.add_button.click()
        panel.values['size'].setValue(10)
        panel.add_button.click()
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
        studio._remove_design()
        self.assertEqual(len(studio._design_factors), 1)
        self.assertTrue(studio.factor_controls["cell_size"][0].isChecked())

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

    def test_structure_choices_common_size_and_optional_size_sweep(self):
        _, studio = self.window()
        studio.common_size_spin.setValue(6)
        for name in ("Gyroid", "Neovius"):
            studio.structure_family_combo.setCurrentIndex(studio.structure_family_combo.findData("tpms"))
            studio.structure_type_combo.setCurrentText(name)
            studio.add_structure_btn.click()
            self.assertFalse(studio.size_variable_btn.isChecked())
            studio.size_variable_btn.click()
            _, low, high, levels = studio.factor_controls["size"]
            low.setValue(6)
            high.setValue(7)
            levels.setValue(2)
            enabled, low, high, levels = studio.factor_controls["solid_fraction"]
            low.setValue(30)
            high.setValue(40)
            levels.setValue(2)
            enabled.setChecked(True)
        self.assertEqual([design.size for design in studio._designs], [6, 6])
        self.assertIn("2 × 2 = 4", studio.sampling_details.text())
        studio.generate_table()
        self.assertEqual(len(studio.rows), 8)
        self.assertEqual({(row.structure_name, row.size, row.solid_fraction) for row in studio.rows},
                         {(name, size, density) for name in ("Gyroid", "Neovius")
                          for size in (6, 7) for density in (30, 40)})
        studio.design_list.setCurrentRow(0)
        self.assertTrue(studio.size_variable_btn.isChecked())
        studio.size_variable_btn.click()
        self.assertNotIn("size", studio._design_factors[0])
        self.assertIn("size", studio._design_factors[1])

    def test_doe_structure_picker_selects_duplicate_and_excludes_inactive_structures(self):
        _, studio = self.window()
        self.assertEqual(studio.settings_tabs.tabText(0), "Design experiments")
        studio.structure_type_combo.setCurrentText("Gyroid")
        studio.add_structure_btn.click()
        studio.add_structure_btn.click()
        studio.structure_type_combo.setCurrentText("S. Diamond")
        studio.add_structure_btn.click()
        self.assertEqual([s.structure_name for s in studio._designs],
                         ["Gyroid", "S. Diamond"])
        studio.factor_controls["cell_size"][0].setChecked(True)
        studio.generate_table()
        self.assertEqual(len(studio.rows), 6)
        self.assertEqual([r.structure_name for r in studio.rows], ["Gyroid"] + ["S. Diamond"] * 5)
        self.assertEqual([r.cell_size for r in studio.rows[1:]], [2, 2.5, 3, 3.5, 4])
        for index in (0, 1):
            studio.design_list.item(index).setCheckState(Qt.CheckState.Unchecked)
        self.assertFalse(studio.generate_btn.isEnabled())
        self.assertFalse(studio.generate_ml_btn.isEnabled())

    def test_entered_and_selected_dimensions_reach_generated_settings(self):
        _, studio = self.window()
        planner = studio.planner
        planner.values["size"].setValue(8)
        planner.add_draft()
        planner.values["size"].setValue(12)
        planner.add_draft()
        self.assertEqual([s.size for s in studio._designs], [8, 12])
        studio.design_list.setCurrentRow(0)
        studio.design_value_controls["size"].setValue(9)
        self.assertEqual([s.size for s in studio._designs], [9, 12])
        studio.generate_table()
        self.assertEqual([r.to_settings(Settings()).size for r in studio.rows], [9, 12])
        self.assertEqual(studio.settings_tabs.currentIndex(), 3)

    def test_group_bounds_two_variables_and_unique_experiments(self):
        _, studio = self.window()
        planner = studio.planner
        for cell, density in ((2, 20), (4, 40)):
            planner.values['cell_size'].setValue(cell)
            planner.values['solid_fraction'].setValue(density)
            planner.add_draft()
        self.assertEqual(planner.tree.topLevelItemCount(), 1)
        self.assertEqual(planner.tree.topLevelItem(0).childCount(), 2)
        for field in ('cell_size', 'solid_fraction'):
            enabled, low, high, levels = studio.factor_controls[field]
            enabled.setChecked(True)
            levels.setValue(3)
            for bound, index in (('Min', 0), ('Max', 1)):
                combo = planner.bound_sources[field, bound]
                combo.setCurrentIndex(combo.findData(index))
            self.assertEqual((low.value(), high.value()),
                             (2, 4) if field == 'cell_size' else (20, 40))
        studio.design_list.setCurrentRow(0)
        self.assertEqual(set(studio._active_factors()), {'cell_size', 'solid_fraction'})
        # Reference dimensions remain editable while the field varies in experiments.
        self.assertTrue(studio.design_value_controls['solid_fraction'].isEnabled())
        studio.generate_table()
        self.assertEqual(len(studio.rows), 9)
        self.assertEqual({(r.cell_size, r.solid_fraction) for r in studio.rows},
                         {(c, d) for c in (2, 3, 4) for d in (20, 30, 40)})

    def test_draft_enter_validation_manual_bounds_and_group_isolation(self):
        _, studio = self.window()
        planner = studio.planner
        spin = planner.values['solid_fraction']
        spin.setFocus()
        spin.lineEdit().selectAll()
        QTest.keyClicks(spin.lineEdit(), '25')
        QTest.keyClick(spin.lineEdit(), Qt.Key.Key_Return)
        self.assertEqual(spin.value(), 25)
        self.assertEqual(studio._designs, [])
        studio.add_structure_btn.click()
        enabled, low, high, count = studio.factor_controls['solid_fraction']
        enabled.setChecked(True)
        low.setValue(20)
        high.setValue(50)
        planner.structure.setCurrentText('Neovius')
        planner.add_draft()
        self.assertFalse(enabled.isChecked())
        self.assertEqual(planner.tree.topLevelItemCount(), 2)
        studio.design_list.setCurrentRow(0)
        self.assertEqual((low.value(), high.value()), (20, 50))
        low.setValue(60)
        self.assertFalse(studio.generate_btn.isEnabled())
        high.setValue(70)
        self.assertTrue(studio.generate_btn.isEnabled())
        planner.values['cell_size'].setValue(80)
        self.assertFalse(studio.add_structure_btn.isEnabled())
        self.assertIsNone(studio._active_future)

    def test_morphology_groups_and_unchecked_design_survive_editing(self):
        _, studio = self.window()
        planner = studio.planner
        planner.add_draft()
        planner.morphology.setCurrentIndex(planner.morphology.findData('network_a'))
        planner.add_draft()
        self.assertEqual(planner.tree.topLevelItemCount(), 2)
        planner._tree_items[0].setCheckState(0, Qt.CheckState.Unchecked)
        studio.design_value_controls['cell_size'].setValue(2)
        self.assertEqual(planner._tree_items[0].checkState(0), Qt.CheckState.Unchecked)
        planner.tree.setCurrentItem(planner._tree_items[0])
        self.assertEqual(studio._selected_design, 0)
        planner.family.setCurrentIndex(planner.family.findData('porous'))
        self.assertTrue(planner.morphology.isEnabled())
        self.assertEqual(planner.draft_settings().tpms_morphology, 'network_a')

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
        studio.planner.values['size'].setValue(9)
        studio.planner.values['solid_fraction'].lineEdit().setText('42')
        path = Path(self.temp.name) / 'plan.rustmech-doe.json'
        studio.save_plan_path(path)
        expected = studio.capture_plan()
        self.assertEqual(expected['draft']['solid_fraction'], 42)
        _, restored = self.window()
        submit = MagicMock(side_effect=AssertionError('opening a plan must not compute'))
        with patch.object(restored.planner.geometry_panel._executor, 'submit', submit):
            restored.open_plan_path(path)
            restored.planner.geometry_panel.refresh()
            self.app.processEvents()
        self.assertEqual(restored.capture_plan(), expected)
        self.assertEqual(restored._plan_path, path.resolve())
        self.assertEqual(restored.plan_file_label.text(), path.name)
        self.assertEqual(len(restored._matrix_rows[0]), 4)
        self.assertEqual(len(restored._matrix_rows[1]), 5)
        self.assertIsNone(restored._active_future)
        submit.assert_not_called()

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

    def test_disabled_factors_keep_reference_values_and_enter_does_not_add(self):
        _, studio = self.window()
        studio.add_structure_btn.click()
        enabled, low, high, count = studio.factor_controls["solid_fraction"]
        enabled.setChecked(True)
        self.assertTrue(low.isEnabled())
        enabled.setChecked(False)
        self.assertFalse(low.isEnabled())
        studio.settings_tabs.setCurrentIndex(0)
        studio.planner.values['size'].setFocus()
        QTest.keyClick(studio.planner.values['size'], Qt.Key.Key_Return)
        self.assertEqual(len(studio._designs), 1)
        self.assertEqual(studio.rows, [])
        studio.generate_table()
        self.assertEqual([r.solid_fraction for r in studio.rows], [30])


if __name__ == "__main__":
    unittest.main()

