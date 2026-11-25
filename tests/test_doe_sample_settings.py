"""Named combinations retain snapshots and join the existing DoE analysis queue."""
import copy
from dataclasses import replace
from pathlib import Path
import unittest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QStyle
from studio_style import CompactComboBox
from model import Settings
from doe_sample_rules import sample_name, validate_sample_state
from tests import test_doe_window as helpers


class SampleSettingsTests(unittest.TestCase):
    setUpClass = classmethod(helpers.DoEWindowTests.setUpClass.__func__)
    setUp = helpers.DoEWindowTests.setUp
    tearDown = helpers.DoEWindowTests.tearDown
    window = helpers.DoEWindowTests.window

    def test_names_combinations_transfer_and_export(self):
        _, studio = self.window()
        page = studio.sample_settings
        self.assertEqual(studio.settings_tabs.tabText(4), 'Settings')
        for key in ('structure_name', 'cell_size', 'solid_fraction'):
            page.add_field(key)
        batch = page.batch
        editor = batch.parameter_editor
        editor.add_variable('cell_size', ['2', '3'])
        editor.add_variable('solid_fraction', ['20', '40'])
        self.assertTrue(batch.build_matrix(), batch.status.text())
        self.assertEqual(len(batch._rows), 4)
        self.assertEqual(batch._rows[0].exp_id, 'Gyroid-2-20')
        batch.transfer()
        self.assertEqual(len(studio.rows), 4)
        self.assertEqual(studio.rows[0].design_settings.cell_size, 2)
        batch.transfer()
        self.assertEqual(len(studio.rows), 8)
        self.assertEqual(len({row.exp_id for row in studio.rows}), 8)
        self.assertEqual(studio.rows[4].exp_id, 'Gyroid-2-20-02')
        path = Path(self.temp.name) / 'samples.csv'
        studio.export_csv_path(path)
        self.assertIn('Gyroid-2-20', path.read_text(encoding='utf-8-sig'))
        self.assertIsNone(studio._active_mode)

    def test_invalid_values_limit_and_family_leave_existing_rows_untouched(self):
        _, studio = self.window()
        studio.add_design(Settings())
        studio.generate_table()
        page = studio.sample_settings
        batch = page.batch
        editor = batch.parameter_editor
        editor.add_variable('cell_size', ['nan'])
        self.assertFalse(batch.build_matrix())
        self.assertFalse(batch.transfer_button.isEnabled())
        self.assertEqual(len(studio.rows), 1)
        editor.variables.item(0, 1).setText('99')
        self.assertFalse(batch.build_matrix())
        editor.variables.item(0, 1).setText('2')
        editor.add_variable('size', [str(i + 1) for i in range(20)])
        editor.add_variable('solid_fraction', [str(i + 1) for i in range(20)])
        editor.add_variable('random_seed', [str(i + 1) for i in range(20)])
        self.assertFalse(batch.build_matrix())
        self.assertIn('maximum', batch.status.text())
        self.assertEqual(len(studio.rows), 1)

    def test_centered_check_column_common_dropdowns_and_busy_guard(self):
        window, studio = self.window()
        page = studio.sample_settings
        studio.settings_tabs.setCurrentIndex(4)
        self.app.processEvents()
        table = page.available
        self.assertEqual(table.columnWidth(3), table.style().pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth) + 4)
        row = next(i for i, field in enumerate(page.sources) if field[0] == 'cell_size')
        table.setCurrentCell(row, 3)
        QTest.keyClick(table, Qt.Key.Key_Space)
        self.assertEqual(page.rule()['fields'][0]['key'], 'cell_size')
        QTest.keyClick(table, Qt.Key.Key_Space)
        self.assertEqual(page.rule()['fields'], [])
        self.assertTrue(all(isinstance(combo, CompactComboBox) for combo in page.findChildren(QComboBox)))
        studio._active_mode = 'solve'
        studio._refresh_plan()
        self.assertFalse(page.isEnabled())
        page.batch.transfer()
        self.assertEqual(studio.rows, [])
        studio._active_mode = None
        studio._refresh_plan()

    def test_plan_and_user_preferences_preserve_naming_axes_and_splitters(self):
        first, studio = self.window()
        studio.add_design(replace(Settings(), size=9, cell_size=2))
        page = studio.sample_settings
        page.load_button.click()
        page.add_field('cell_size')
        page.rename_block('Cell')
        page.labels.setChecked(True)
        page.batch.parameter_editor.add_variable('cell_size', ['2', '3'])
        studio.settings_tabs.setCurrentIndex(4)
        page.open_batch()
        self.app.processEvents()
        page.batch.splitter.setSizes([300, 180])
        self.app.processEvents()
        sizes = page.batch.splitter.sizes()
        saved = page.state()
        plan = Path(self.temp.name) / 'samples.rustmech-doe.json'
        studio.save_plan_path(plan)
        first.close()
        second, restored = self.window()
        self.assertEqual(restored.sample_settings.rule(), page.rule())
        restored.settings_tabs.setCurrentIndex(4)
        restored.sample_settings.open_batch()
        self.app.processEvents()
        self.assertEqual(restored.sample_settings.batch.splitter.sizes(), sizes)
        restored.open_plan_path(plan)
        self.assertEqual(restored.sample_settings.state(), saved)
        self.assertTrue(restored.sample_settings.batch.build_matrix())
        self.assertEqual([r.cell_size for r in restored.sample_settings.batch._rows], [2, 3])
        self.assertEqual(restored.settings_tabs.currentIndex(), 4)

    def test_regular_doe_generation_uses_naming_rule_and_legacy_plans_open(self):
        _, studio = self.window()
        studio.sample_settings.add_field('cell_size')
        studio.add_design(Settings())
        studio.generate_table()
        self.assertEqual(studio.rows[0].exp_id, '3')
        payload = studio.capture_plan()
        payload.pop('sample_settings')
        from doe_plan import decode_plan
        self.assertEqual(decode_plan(payload)['sample_settings'], {})
        malformed = copy.deepcopy(studio.capture_plan()['sample_settings'])
        malformed['combinations']['parameters']['value_columns'] = 100000
        with self.assertRaises(ValueError):
            validate_sample_state(malformed)
        self.assertEqual(sample_name({'fields': [{'key': 'x'}]}, {'x': 'bad:/name'}), 'bad__name')

    def test_family_labels_preserve_internal_identifiers(self):
        _, studio = self.window()
        page = studio.sample_settings
        editor = page.batch.parameter_editor
        row = next(i for i, field in enumerate(page.sources) if field[0] == 'structure_family')
        combo = editor.fixed.cellWidget(row, 1)
        self.assertEqual([combo.itemText(i) for i in range(combo.count())], ['TPMS', 'Truss Lattice', 'Porous'])
        self.assertEqual(page.available.item(row, 2).text(), 'TPMS')
        combo.setCurrentIndex(combo.findData('porous'))
        combo.activated.emit(combo.currentIndex())
        self.assertEqual(editor.fixed.item(row, 1).text(), 'porous')
        self.assertEqual(editor.values('structure_family'), ('porous',))
        self.assertEqual(sample_name({'fields': [{'key': 'structure_family'}]}, {'structure_family': 'tpms'}), 'TPMS')

    def test_three_columns_keep_fixed_and_variable_parameters_and_dropdowns(self):
        window, studio = self.window()
        page = studio.sample_settings
        batch = page.batch
        editor = batch.parameter_editor
        self.assertEqual(editor.splitter.count(), 3)
        self.assertEqual(editor.splitter.orientation(), Qt.Orientation.Horizontal)
        source_row = lambda key: next(i for i, field in enumerate(page.sources) if field[0] == key)
        size_row = source_row('size')
        editor.available.item(size_row, 2).setCheckState(Qt.CheckState.Checked)
        editor.fixed.item(size_row, 1).setText('12')
        editor.add_variable('cell_size', ['2', '3'])
        editor.add_variable('structure_name', ['Gyroid', 'Diamond'])
        self.assertFalse(editor.fixed.isRowHidden(size_row))
        self.assertFalse(editor.fixed.isRowHidden(source_row('cell_size')))
        self.assertEqual(editor.fixed.item(source_row('cell_size'), 2).checkState(), Qt.CheckState.Checked)
        self.assertEqual(editor.variables.rowCount(), 2)
        row = editor.variable_row('structure_name')
        combo = editor.variables.cellWidget(row, 2)
        self.assertIsInstance(combo, CompactComboBox)
        combo.setCurrentText('S. Primitive')
        combo.activated.emit(combo.currentIndex())
        self.assertEqual(editor.values('structure_name'), ('Gyroid', 'S. Primitive'))
        self.assertTrue(batch.build_matrix(), batch.status.text())
        self.assertEqual(len(batch._rows), 4)
        self.assertTrue(all(row.design_settings.size == 12 for row in batch._rows))
        editor.fixed_filter.search.setText('Cube size')
        self.assertTrue(editor.fixed.isRowHidden(source_row('cell_size')))
        self.assertEqual(editor.values('cell_size'), (2.0, 3.0))
        editor.fixed_filter.search.clear()
        studio.settings_tabs.setCurrentIndex(4)
        page.open_batch()
        window.resize(1360, 880)
        self.app.processEvents()
        editor.splitter.setSizes([270, 320, 420])
        self.app.processEvents()
        sizes = editor.splitter.sizes()
        saved = batch.state()
        batch.restore_state(saved)
        self.app.processEvents()
        restored = batch.parameter_editor
        self.assertEqual(restored.added_keys, {'size', 'cell_size', 'structure_name'})
        self.assertEqual(restored.splitter.sizes(), sizes)
        self.assertEqual(restored.values('structure_name'), ('Gyroid', 'S. Primitive'))
        restored.variables.setCurrentCell(restored.variable_row('cell_size'), 0)
        restored.to_fixed.click()
        self.assertFalse(restored.fixed.isRowHidden(source_row('cell_size')))
        self.assertEqual(restored.variable_row('cell_size'), -1)
        self.assertEqual(restored.fixed.item(source_row('cell_size'), 2).checkState(), Qt.CheckState.Unchecked)
        restored.fixed.item(source_row('cell_size'), 2).setCheckState(Qt.CheckState.Checked)
        self.assertGreaterEqual(restored.variable_row('cell_size'), 0)
        restored.fixed.setCurrentCell(size_row, 0)
        restored.remove_parameter.click()
        self.assertTrue(restored.fixed.isRowHidden(size_row))
        self.assertEqual(restored.values('size'), (Settings().size,))

    def test_legacy_parameter_selection_preserves_existing_fixed_values(self):
        from doe_sample_parameters import SampleBatchParameters
        _, studio = self.window()
        fields = studio.sample_settings.sources
        editor = SampleBatchParameters(fields, {'axes': {
            'size': {'fixed': '12', 'vary': False},
            'cell_size': {'fixed': '3', 'vary': True, 'values': ['2', '3']}}})
        self.addCleanup(editor.deleteLater)
        self.assertEqual(editor.added_keys, {'size', 'cell_size'})
        self.assertEqual(editor.values('size'), (12.0,))
        self.assertEqual(editor.values('cell_size'), (2.0, 3.0))
