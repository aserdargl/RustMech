"""Exercise the operation planner through the real workspace settings."""
import json
from pathlib import Path
import time
import unittest
from unittest.mock import MagicMock, Mock, patch
from xml.etree import ElementTree
from zipfile import ZipFile
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractSpinBox, QLineEdit, QStyle, QStyleOptionComboBox, QTableWidget, QWidget

from tests import test_remote_mode_ui as ui
from ui_language import LanguageSelector, turkish


class NewDoeTests(unittest.TestCase):
    setUpClass = classmethod(ui.RemoteModeUiTests.setUpClass.__func__)
    setUp = ui.RemoteModeUiTests.setUp
    tearDown = ui.RemoteModeUiTests.tearDown

    def page(self):
        studio = self.window.doe_studio
        self.window.doe_window.open_workspace()
        page = studio.new_doe
        studio.settings_tabs.setCurrentWidget(page)
        self.app.processEvents()
        self.assertEqual(studio.settings_tabs.tabText(studio.settings_tabs.currentIndex()), "DoE")
        self.assertFalse(studio.save_plan_btn.isVisible())
        self.assertLess(studio.capture_plan()["selection"]["tab"], 5)
        return page

    def test_new_doe_is_the_first_and_initial_page(self):
        studio = self.window.doe_studio
        self.window.doe_window.open_workspace()
        self.app.processEvents()
        tabs = studio.settings_tabs
        self.assertEqual(tabs.count(), 5)
        self.assertIs(tabs.widget(0), studio.new_doe)
        self.assertIs(tabs.currentWidget(), studio.new_doe)
        self.assertTrue(studio.new_doe.loaded)
        self.assertEqual([tabs.tabText(index) for index in range(tabs.count())],
                         ["DoE", "Mesh", "DeepLearning", "DoE2", "Settings"])
        self.assertEqual(studio.new_doe.operation.currentText(), "FFT")
        self.assertIsNone(studio.findChild(LanguageSelector))
        self.assertTrue(self.window.language_selector.isVisible())
        self.assertFalse(studio.plan_file_label.isVisible())
        self.assertFalse(studio.findChildren(QAbstractSpinBox))
        analysis = studio.new_doe.operation
        self.assertEqual([analysis.itemText(index) for index in range(analysis.count())],
                         ["FEM", "FFT", "MPM", "Peridynamics", "Flow (CFD)", "Thermal (CHT)"])
        nav = studio.planner.navigation
        nav_bottom = nav.mapToGlobal(QPoint(0, nav.height())).y()
        self.assertLessEqual(analysis.mapToGlobal(QPoint()).y() - nav_bottom, 8)
        buttons = studio.planner.navigation.layout()
        self.assertEqual(buttons.itemAt(0).widget().text(), "DoE")
        for index in range(buttons.count()):
            button = buttons.itemAt(index).widget()
            self.assertGreaterEqual(button.width(), button.fontMetrics().horizontalAdvance(button.text()) + 22)
        tabs.setCurrentIndex(3)
        self.assertTrue(studio.open_plan_btn.isVisible())
        self.assertEqual(studio.open_plan_btn.width(), 64)
        self.assertEqual(studio.save_plan_btn.width(), 64)

    def test_numeric_input_commits_on_enter_and_rejects_invalid_bounds(self):
        page = self.page()
        factor = next(factor for factor in page.factors if factor.path == ("settings", "cell_size"))
        self.assertIsInstance(factor.fixed, QLineEdit)
        factor.fixed.selectAll()
        QTest.keyClicks(factor.fixed, "3.125")
        QTest.keyClick(factor.fixed, Qt.Key.Key_Return)
        self.assertEqual(factor.levels_for_matrix(), [3.125])
        self.assertFalse(page.rows)
        factor.variable.setChecked(True)
        self.assertIsInstance(factor.first, QLineEdit)
        self.assertIsInstance(factor.last, QLineEdit)
        factor.first.clear()
        QTest.keyClick(factor.first, Qt.Key.Key_Return)
        self.assertEqual(factor.first.value(), 3.125)
        factor.last.selectAll()
        QTest.keyClicks(factor.last, "4.25")
        QTest.keyClick(factor.last, Qt.Key.Key_Return)
        self.assertEqual(factor.levels_for_matrix(), [3.125, 4.25])

    def test_drafts_survive_switching_operations_and_reopening_the_application(self):
        from model import Settings
        from nonlinear_compression import RustMechDialog
        from solver_profiles import SolverProfileStore

        page = self.page()

        def factor(page, name):
            return next(field for field in page.factors if field.path[-1] == name)

        grid = factor(page, "grid")
        grid.fixed.setValue(28)
        grid.variable.setChecked(True)
        grid.first.setValue(20)
        grid.last.setValue(32)
        grid.mode.setCurrentIndex(grid.mode.findData("list"))
        grid.selection.setText("32, 48")
        structure = factor(page, "structure_name")
        structure.family.setCurrentIndex(structure.family.findData("lattice"))
        self.assertEqual(structure.family.currentData(), "lattice")
        structure.variable.setChecked(True)
        structure.menu.actions()[-1].setChecked(True)
        expected_structure = structure.draft()
        distribution = factor(page, "distribution")
        distribution.fixed.setText("Local Ubuntu")

        cfd = page.operation.findData("flow_solve")
        page.operation.setCurrentIndex(cfd)
        factor(page, "size").fixed.setValue(9.25)
        page.operation.setCurrentIndex(page.operation.findData("fft_solve"))
        grid = factor(page, "grid")
        self.assertEqual(grid.fixed.value(), 28)
        self.assertEqual((grid.first.value(), grid.last.value()), (20, 32))
        self.assertEqual(grid.levels_for_matrix(), [32, 48])
        self.assertEqual(factor(page, "structure_name").draft(), expected_structure)
        self.assertEqual(factor(page, "distribution").fixed.text(), "Local Ubuntu")
        page.operation.setCurrentIndex(cfd)
        factor(page, "size").fixed.setText("9.75")
        self.window.close()

        with patch.object(RustMechDialog, "start_preview", lambda self: None), patch(
            "nonlinear_compression.create_preview_viewport",
            side_effect=lambda parent, **kwargs: MagicMock(interactor=QWidget(parent)),
        ):
            reopened = RustMechDialog(Settings(), solver_profile_store=SolverProfileStore(self.preferences))
        try:
            reopened.doe_window.open_workspace()
            self.app.processEvents()
            page = reopened.doe_studio.new_doe
            self.assertEqual(page.operation.currentData(), "flow_solve")
            self.assertEqual(factor(page, "size").fixed.value(), 9.75)
            self.assertFalse(page.rows)
            self.assertFalse(page.run.isEnabled())
            page.operation.setCurrentIndex(page.operation.findData("fft_solve"))
            self.assertEqual(factor(page, "grid").levels_for_matrix(), [32, 48])
        finally:
            reopened.close()
            reopened.dispose()

    def test_legacy_integer_specimen_size_exposes_three_decimal_axes(self):
        from new_doe_operations import parameters, snapshot
        base = snapshot(self.window, "flow_solve")
        base["settings"]["size"] = 8
        del base["settings"]["size_x"], base["settings"]["size_y"]
        fields = dict(parameters(base))
        for key in ("size_x", "size_y", "size"):
            value = fields["settings", key]
            self.assertIs(type(value), float)
            self.assertEqual(value, 8.0)

    def test_each_structure_family_can_sweep_each_outer_axis(self):
        page = self.page()
        for index in range(page.operation.count()):
            page.operation.setCurrentIndex(index)
            self.assertTrue(page.loaded, page.status.text())
            fields = {f.path: f for f in page.factors}
            structure = fields["settings", "structure_name"]
            axes = [fields["settings", key] for key in ("size_x", "size_y", "size")]
            lengths = (6, 9, 12)
            fields["settings", "cell_size"].fixed.setValue(3)
            if page.operation.currentData() == "peridynamic_solve":
                fields["options", "resolution"].fixed.setValue(48)
            for axis, length in zip(axes, lengths):
                axis.fixed.setValue(length)
            for family in range(structure.family.count()):
                structure.family.setCurrentIndex(family)
                for direction, axis in enumerate(axes):
                    with self.subTest(operation=page.operation.currentText(),
                                      family=structure.family.currentText(), axis=axis.label):
                        axis.variable.setChecked(True)
                        axis.first.setValue(lengths[direction])
                        axis.last.setValue(lengths[direction] + 3)
                        page.create()
                        self.assertEqual(len(page.rows), 2, page.status.text())
                        for level, row in enumerate(page.rows):
                            expected = list(lengths)
                            expected[direction] += 3 * level
                            self.assertEqual([row["settings"][f.path[-1]] for f in axes], expected)
                            self.assertEqual(row["settings"]["structure_family"], structure.family.currentData())
                        axis.variable.setChecked(False)

    def test_operation_inputs_and_fft_factorial_matrix(self):
        from new_doe_operations import snapshot
        page = self.page()
        selector = page.operation
        selector_width = selector.width()
        option = QStyleOptionComboBox()
        selector.initStyleOption(option)
        text_rect = selector.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                                    QStyle.SubControl.SC_ComboBoxEditField, selector)
        for i in range(page.operation.count()):
            page.operation.setCurrentIndex(i)
            with self.subTest(op=page.operation.currentText()):
                self.assertEqual(selector.width(), selector_width)
                for title in (selector.currentText(), turkish(selector.currentText())):
                    self.assertLessEqual(selector.fontMetrics().horizontalAdvance(title), text_rect.width())
                self.assertTrue(page.loaded, page.status.text())
                self.assertEqual(page.base, snapshot(self.window, page.operation.currentData()))
                page.create()
                self.assertEqual(len(page.rows), 1, page.status.text())
        page.operation.setCurrentIndex(page.operation.findData("fft_solve"))
        factors = {f.path[-1]: f for f in page.factors}
        grid = factors["grid"]
        grid.variable.setChecked(True)
        grid.first.setValue(16)
        grid.last.setValue(32)
        for axis in ("size_x", "size_y", "size"):
            factors[axis].fixed.setValue(12)
        cell = factors["cell_size"]
        cell.variable.setChecked(True)
        cell.first.setValue(2)
        cell.last.setValue(4)
        page.create()
        self.assertEqual([(r["grid"], r["settings"]["cell_size"]) for r in page.rows],
                         [(g, s) for g in (16, 32) for s in (2, 4)])
        grid.mode.setCurrentIndex(grid.mode.findData("list"))
        self.assertFalse(page.run.isEnabled())
        grid.selection.setText("32, 17, 24")
        page.create()
        self.assertEqual([(r["grid"], r["settings"]["cell_size"]) for r in page.rows],
                         [(g, s) for g in (32, 17, 24) for s in (2, 4)])
        cell.mode.setCurrentIndex(cell.mode.findData("list"))
        cell.selection.setText("1,5; 3,0")
        page.create()
        self.assertEqual([r["settings"]["cell_size"] for r in page.rows], [1.5, 3.0] * 3)
        self.assertTrue(all(r["void_ratio"] == self.window.fft_void_ratio.value() for r in page.rows))
        self.assertFalse(self.window.doe_studio.rows)
        previous_rows = page.rows
        grid.selection.setText("16, 24.5")
        page.apply_button.click()
        self.assertIs(page.rows, previous_rows)
        self.assertFalse(page.run.isEnabled())

    def test_fft_geometry_and_material_controls_are_local_to_doe(self):
        from material_catalog import builtin_materials
        from window_caption import CAPTION_PROPERTY

        page = self.page()
        self.assertEqual(self.window.property(CAPTION_PROPERTY).height(), 25)
        self.assertEqual(page.window().property(CAPTION_PROPERTY).height(), 25)
        fields = {factor.path[-1]: factor for factor in page.factors}
        structure = fields["structure_name"]
        starting_structure = structure.fixed.currentData()
        structure.family.setCurrentIndex(structure.family.findData("lattice"))
        self.assertNotEqual(structure.fixed.currentData(), starting_structure)
        self.assertEqual(self.window.fft_structure_combo.currentData(), starting_structure)
        structure.family.setCurrentIndex(structure.family.findData("tpms"))

        card = next(entry.card() for entry in builtin_materials() if entry.kind == "solid")
        original = self.window.fft_material_editor.material_settings()
        page.apply_fft_material(card)
        modulus = fields["young_modulus"]
        modulus.variable.setChecked(True)
        modulus.first.setValue(1000)
        modulus.last.setValue(2000)
        page.create()
        self.assertEqual([row["settings"]["young_modulus"] for row in page.rows], [1000, 2000])
        self.assertTrue(all(row["settings"]["material_name"] == card.name for row in page.rows))
        self.assertEqual(self.window.fft_material_editor.material_settings(), original)
        modulus.variable.setChecked(False)
        page.create()
        self.assertEqual(len(page.rows), 1)
        self.assertEqual(page.rows[0]["settings"]["young_modulus"], modulus.fixed.value())

        for key, length in zip(("size_x", "size_y", "size"), (4, 8, 12)):
            field = fields[key].fixed
            self.assertGreater(field.minimum(), 0)
            self.assertTrue(field.hasAcceptableInput())
            field.setValue(length)
        period = fields["cell_size"]
        period.variable.setChecked(True)
        period.first.setValue(2)
        period.last.setValue(4)
        page.create()
        self.assertEqual(
            [tuple(row["settings"][axis] for axis in ("size_x", "size_y", "size")) for row in page.rows],
            [(4, 8, 12), (4, 8, 12)],
        )

    def test_apply_replaces_the_matrix_from_the_left_column(self):
        page = self.page()
        fields = {f.path[-1]: f for f in page.factors}
        fields["cell_size"].fixed.setValue(1)
        fields["size_x"].fixed.setValue(6)
        fields["size_y"].fixed.setValue(3)
        cell = fields["size"]
        geometry = page.sections["Structure / Dimensions"]
        fft = page.sections["FFT Homogenization"]
        grid = fields["grid"]
        settings_table = fft.findChild(QTableWidget)
        self.assertIs(settings_table.cellWidget(0, 0), grid.variable)
        self.assertEqual(settings_table.item(0, 1).text(), grid.label)
        self.assertTrue(page.preview.isVisible())
        self.assertGreater(page.preview.mapTo(page, QPoint()).x(),
                           page.input_tabs.mapTo(page, QPoint()).x() + page.input_tabs.width())
        self.assertLess(page.input_tabs.width(), page.preview.width())
        self.assertEqual(page.inputs.horizontalScrollBar().maximum(), 0)
        top_row = (page.operation, page.run, page.stop, page.open)
        self.assertEqual(len({control.mapTo(page, QPoint()).y() for control in top_row}), 1)
        self.assertEqual(len({(button.width(), button.height()) for button in top_row[1:]}), 1)
        self.assertLess(page.operation.width(), 150)
        apply_right = page.apply_button.mapTo(page, page.apply_button.rect().bottomRight()).x()
        settings_right = page.input_tabs.mapTo(page, page.input_tabs.rect().bottomRight()).x()
        self.assertEqual(apply_right, settings_right)
        self.assertEqual(page.apply_button.mapTo(page, QPoint(0, page.apply_button.height())).y(),
                         page.splitter.mapTo(page, QPoint(0, page.splitter.height())).y())
        self.assertEqual(page.splitter.geometry().bottom(),
                         page.rect().bottom() - page.layout().contentsMargins().bottom())
        studio = self.window.doe_studio
        bottom_gap = studio.rect().bottom() - page.apply_button.mapTo(
            studio, page.apply_button.rect().bottomRight()).y()
        self.assertLessEqual(bottom_gap, studio.layout().contentsMargins().bottom() + 3)
        self.assertEqual(page.status.text(), "")
        self.assertTrue(cell.fixed.isVisible())
        cell.fixed.setValue(2)
        cell.variable.setChecked(True)
        cell.last.setValue(4)
        self.assertTrue(fft.content.isVisible())
        self.assertFalse(cell.fixed.isEnabled())
        self.assertTrue(page.variations.isRowHidden(fields["grid"].variation_row))
        page.input_tabs.setCurrentWidget(page.variations)
        self.assertTrue(cell.first.isVisible())
        self.assertFalse(page.variations.isRowHidden(cell.variation_row))
        page.input_tabs.setCurrentWidget(page.inputs)
        geometry.setFocus()
        QTest.keyClick(geometry, Qt.Key.Key_Space)
        self.assertFalse(cell.fixed.isVisible())
        self.assertTrue(fft.content.isVisible())
        self.assertFalse(page.rows)
        page.apply_button.click()
        self.assertEqual([row["settings"]["size"] for row in page.rows], [2, 4])
        self.assertTrue(page.run.isEnabled())
        self.app.processEvents()
        self.assertEqual(page.splitter.geometry().bottom(),
                         page.rect().bottom() - page.layout().contentsMargins().bottom())
        QTest.keyClick(geometry, Qt.Key.Key_Space)
        self.assertTrue(cell.fixed.isVisible())
        cell.variable.setChecked(False)
        cell.fixed.setValue(5)
        self.assertEqual(page.sheet.rowCount(), 2)
        self.assertFalse(page.run.isEnabled())
        self.assertEqual(page.status.text(), "")
        page.apply_button.click()
        self.assertEqual([row["settings"]["size"] for row in page.rows], [5])
        self.assertEqual(page.sheet.rowCount(), 1)
        self.assertTrue(page.run.isEnabled())

    def test_settings_splitter_restores_user_width(self):
        page = self.page()
        sizes = page.splitter.sizes()
        page.splitter.setSizes([sizes[0] + 100, sizes[1] - 100])
        adjusted = page.splitter.sizes()
        self.assertNotEqual(adjusted, sizes)
        self.store.save(page.window())
        page.splitter.setSizes(sizes)
        self.store.restore(page.window())
        self.app.processEvents()
        self.assertEqual(page.splitter.sizes(), adjusted)

    def test_two_peridynamic_runs_save_separate_results(self):
        page = self.page()
        page.operation.setCurrentIndex(page.operation.findData("peridynamic_solve"))
        fields = {f.path[-1]: f for f in page.factors}
        fields["resolution"].fixed.setValue(12)
        fields["cell_size"].fixed.setValue(6)
        increments = next(f for f in page.factors if f.path[-1] == "increments")
        increments.variable.setChecked(True)
        increments.first.setValue(2)
        increments.last.setValue(3)
        page.create()
        self.assertEqual(len(page.rows), 2, page.status.text())
        with patch("doe_studio.results_directory", return_value=Path(self.temp.name)):
            page.start()
            deadline = time.monotonic() + 90
            try:
                while page.timer.isActive() and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.05)
                self.assertFalse(page.timer.isActive(), page.status.text())
                for i in (1, 2):
                    record = json.loads((page.directory / f"row_{i:05d}" / "experiment.json").read_text())
                    self.assertEqual(record["state"], "Completed", record.get("error"))
                    self.assertEqual(record["inputs"]["options"]["increments"], i + 1)
                    self.assertTrue(list(Path(record["results"]).glob("*.csv")))
            finally:
                page.cancel()

    def test_failure_continues_and_stop_prevents_the_next_launch(self):
        page = self.page()
        grid = next(f for f in page.factors if f.path == ("grid",))
        grid.variable.setChecked(True)
        grid.mode.setCurrentIndex(grid.mode.findData("list"))
        grid.selection.setText("16, 24, 40")
        page.create()
        failed, active = Mock(), Mock()
        failed.done.return_value = True
        failed.result.side_effect = RuntimeError("Solver failed")
        with patch("doe_studio.results_directory", return_value=Path(self.temp.name)), \
                patch("new_doe_page.launch", side_effect=[failed, active]) as launch:
            page.start()
            page.advance()
            page.cancel()
        self.assertEqual(launch.call_count, 2)
        active.force_stop.assert_called_once()
        self.assertEqual(page.sheet.states, ["Failed", "Stopped", "Skipped"])
        self.assertIn("Solver failed", (page.directory / "row_00001/experiment.json").read_text())

    def select_run_target(self, page, target, batch_size=None):
        from new_doe_page import DoeRunDialog

        page.run.click()
        dialog, = (dialog for dialog in page.findChildren(DoeRunDialog) if dialog.isVisible())
        dialog.target.setCurrentIndex(dialog.target.findData(target))
        if batch_size is not None:
            dialog.batch_size.setValue(batch_size)
        return dialog

    def test_run_dialog_selects_target_for_the_matrix(self):
        page = self.page()
        page.create()
        controller = self.window.remote_controller
        with patch("doe_studio.results_directory", return_value=Path(self.temp.name)), \
                patch.object(controller, "auth", return_value=None) as auth, \
                patch("new_doe_page.launch") as launch:
            dialog = self.select_run_target(page, "truba_matrix")
            self.assertFalse(dialog.batch_size.isEnabled())
            launch.assert_not_called()
            dialog.confirm.click()
            self.assertIsNone(page.directory)
            self.assertIsNone(page.truba_jobs_window)
            self.assertFalse(page.timer.isActive())
            self.assertTrue(page.run.isEnabled())
            launch.assert_not_called()

            auth.return_value = object()
            context, credentials = controller.require_analysis("damask", explicit_target=True)
            self.assertEqual(credentials, auth.return_value)
            self.assertTrue(context.partition)
            self.select_run_target(page, "truba_matrix").confirm.click()
            self.assertIs(launch.call_args.args[2], controller)
            self.assertTrue(page.truba_jobs_window.isVisible())
            self.assertFalse(controller.requested)
            record = page.directory / "truba" / "remote_job.json"
            record.parent.mkdir()
            record.write_text(json.dumps(dict(job_id="71", state="cancelled")), encoding="utf-8")
            page.cancel()
            self.assertEqual(page.truba_jobs_window.table.item(0, 2).text(), "Cancelled")
            page.truba_jobs_window.close()

            with patch.object(controller, "requested", True):
                self.select_run_target(page, "local").confirm.click()
                self.assertIsNone(launch.call_args.args[2])
                self.assertFalse(page.truba_jobs_window.isVisible())
                page.cancel()

    def test_truba_dispatches_whole_matrix_once_and_restores_each_row_state(self):
        page = self.page()
        grid = next(f for f in page.factors if f.path == ("grid",))
        grid.variable.setChecked(True)
        grid.first.setValue(16)
        grid.last.setValue(24)
        page.create()
        root = Path(self.temp.name)
        output = root / "output"
        for i, state in enumerate(("Failed", "Completed"), 1):
            case = output / f"row_{i:05d}"
            case.mkdir(parents=True)
            (case / "experiment.json").write_text(json.dumps(dict(state=state)))
        with patch("doe_studio.results_directory", return_value=root), \
                patch.object(self.controller, "auth", return_value=object()), \
                patch("new_doe_page.launch") as launch:
            job = launch.return_value
            job.done.return_value = False
            page.start(remote=True, separate=False)
            jobs = page.truba_jobs_window
            self.assertTrue(jobs.isVisible())
            self.assertEqual(jobs.table.rowCount(), 0)
            job.remote_status.return_value = dict(job_id="71", state="RUNNING", experiment_index=2,
                                                  local_directory=str(root))
            page.advance()
            self.assertEqual(jobs.table.rowCount(), 1)
            self.assertEqual([jobs.table.item(0, col).text() for col in range(3)],
                             ["1–2", "71", "Analysis started and running"])
            self.assertIn("2/2", page.status.text())
            self.assertEqual(page.sheet.states, ["Awaiting results", "Running"])
            jobs.table.selectRow(0)
            self.assertFalse(jobs.results_button.isEnabled())
            job.done.return_value = True
            job.result.return_value = str(output)
            job.remote_status.return_value["state"] = "downloaded"
            page.advance()
            self.assertEqual(jobs.table.rowCount(), 1)
            self.assertEqual(jobs.table.item(0, 2).text(), "Analysis complete · results downloaded")
            launch.assert_called_once()
            self.assertEqual(launch.call_args.args[0]["experiment_rows"], page.rows)
            self.assertEqual(page.sheet.states, ["Failed", "Completed"])
            self.assertFalse(page.timer.isActive())
            self.assertTrue(jobs.results_button.isEnabled())
            with patch("new_doe_page.QDesktopServices.openUrl") as open_url:
                jobs.results_button.click()
            open_url.assert_called_once()
            self.assertEqual(Path(open_url.call_args.args[0].toLocalFile()), output)

    def test_separate_jobs_queue_before_completion_and_finish_out_of_order(self):
        page = self.page()
        grid = next(f for f in page.factors if f.path == ("grid",))
        grid.variable.setChecked(True)
        grid.first.setValue(16)
        grid.last.setValue(24)
        page.create()
        root = Path(self.temp.name)
        records = []
        def submit(row, case, controller):
            index = row["experiment_index"]
            self.assertIs(controller, self.controller)
            self.assertEqual(row["experiment_count"], 2)
            self.assertNotIn("experiment_rows", row)
            folder = case / "truba"
            folder.mkdir()
            record = dict(request_id=f"request-{index}", job_id=str(70 + index), state="submitted",
                          local_directory=str(folder))
            (folder / "remote_job.json").write_text(json.dumps(record))
            records.append(record)
            job = Mock()
            job.remote_status.return_value = record
            job.result.return_value = record
            job.done.return_value = True  # Slurm accepted the job; analysis is still queued.
            return job

        with patch("doe_studio.results_directory", return_value=root), \
                patch.object(self.controller, "auth", return_value=object()), \
                patch("new_doe_page.launch", side_effect=submit), \
                patch("new_doe_page.monotonic", return_value=0) as clock:
            self.select_run_target(page, "truba", 1).confirm.click()
            jobs = page.truba_jobs_window
            self.assertTrue(jobs.isVisible())
            page.advance()
            jobs.close()
            page.advance()
            self.assertEqual(len(records), 2)
            self.assertTrue(page.timer.isActive())
            self.assertFalse(jobs.isVisible())
            self.assertEqual(jobs.table.rowCount(), 2)
            self.assertEqual([jobs.table.item(row, 1).text() for row in range(2)], ["71", "72"])
            page.truba_jobs_button.click()
            self.assertTrue(jobs.isVisible())
            self.assertEqual(page.sheet.states, ["Queued", "Queued"])
            for index, state in enumerate(("RUNNING|None", "downloaded")):
                record = records[index]
                record["state"] = state
                (Path(record["local_directory"]) / "remote_job.json").write_text(json.dumps(record))
            clock.return_value = 3
            page.advance()
            self.assertEqual(page.sheet.states, ["Running", "Completed"])
            self.assertEqual(jobs.table.item(0, 2).text(), "Analysis started and running")
            self.assertEqual(jobs.table.item(1, 2).text(), "Analysis complete · results downloaded")
            records[0]["state"] = "downloaded"
            (Path(records[0]["local_directory"]) / "remote_job.json").write_text(json.dumps(records[0]))
            clock.return_value = 6
            page.advance()
            self.assertFalse(page.timer.isActive())
            for record in records:
                saved = json.loads((Path(record["local_directory"]).parent / "experiment.json").read_text())
                self.assertEqual(saved["slurm_job_id"], record["job_id"])
                self.assertEqual(saved["state"], "Completed")

    def test_truba_chains_pairs_by_default_and_includes_the_final_single_experiment(self):
        page = self.page()
        self.assertEqual(page.run_options("fft_solve")[1], 2)
        grid = next(f for f in page.factors if f.path == ("grid",))
        grid.variable.setChecked(True)
        grid.mode.setCurrentIndex(grid.mode.findData("list"))
        grid.selection.setText("16, 24, 32, 40, 48")
        page.create()
        root = Path(self.temp.name)
        records = []

        def submit(request, case, controller):
            first = request["experiment_index"]
            self.assertEqual(request["experiment_count"], 5)
            expected_predecessor = {1: None, 3: "71", 5: "73"}
            self.assertEqual(request.get("predecessor_job_id"), expected_predecessor[first])
            if first in (1, 3):
                self.assertEqual([row["grid"] for row in request["experiment_rows"]],
                                 [16, 24] if first == 1 else [32, 40])
            else:
                self.assertEqual(first, 5)
                self.assertEqual(request["grid"], 48)
                self.assertNotIn("experiment_rows", request)
            folder = case / "truba"
            folder.mkdir()
            record = dict(request_id=str(first), job_id=str(70 + first), state="submitted",
                          local_directory=str(folder))
            (folder / "remote_job.json").write_text(json.dumps(record), encoding="utf-8")
            records.append(record)
            job = Mock()
            job.remote_status.return_value = record
            job.result.return_value = record
            job.done.return_value = True
            return job

        with patch("doe_studio.results_directory", return_value=root), \
                patch.object(self.controller, "auth", return_value=object()), \
                patch("new_doe_page.launch", side_effect=submit), \
                patch("new_doe_page.monotonic", return_value=0) as clock:
            self.select_run_target(page, "truba").confirm.click()
            page.advance()
            page.advance()
            page.advance()
            self.assertEqual(len(records), 3)
            jobs = page.truba_jobs_window
            table = jobs.table
            self.assertEqual([table.item(row, 0).text() for row in range(3)], ["1–2", "3–4", "5"])
            self.assertEqual(page.sheet.states, ["Queued"] * 5)
            for record, states in zip(records[:2], (("Failed", "Completed"), ("Completed", "Completed")), strict=True):
                outputs = Path(record["local_directory"]) / "output"
                for i, state in enumerate(states, 1):
                    folder = outputs / f"row_{i:05d}"
                    folder.mkdir(parents=True)
                    (folder / "experiment.json").write_text(json.dumps(dict(state=state)), encoding="utf-8")
                    if state == "Completed":
                        value = 100 * int(record["request_id"]) + i
                        (folder / "stiffness_mpa.csv").write_text(f"MPa,XX\nXX,{value}\n", encoding="utf-8")
            for record in records:
                record["state"] = "downloaded"
                (Path(record["local_directory"]) / "remote_job.json").write_text(json.dumps(record), encoding="utf-8")
            (Path(records[-1]["local_directory"]) / "output").mkdir()
            clock.return_value = 3
            page.advance()
            self.assertEqual(page.sheet.states, ["Failed"] + ["Completed"] * 4)
            self.assertEqual(page.failures, 1)
            self.assertFalse(page.timer.isActive())
            for i, job_id in enumerate(("71", "71", "73", "73", "75"), 1):
                record = json.loads((page.directory / f"row_{i:05d}" / "experiment.json").read_text(encoding="utf-8"))
                self.assertEqual(record["slurm_job_id"], job_id)
            from new_doe_report import REPORT_NAME
            with ZipFile(page.directory / REPORT_NAME) as workbook:
                sheet = ElementTree.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
                result_rows = [[cell.findtext(".//{*}t") or cell.findtext("{*}v") for cell in row]
                               for row in sheet.findall(".//{*}row")]
            self.assertEqual(result_rows[0], ["Numune parametreleri"])
            self.assertIn(["grid", "16", "24", "32", "40", "48"], result_rows)
            starts = [result_rows.index([f"Deney {i}", f"grid = {grid}"])
                      for i, grid in enumerate((16, 24, 32, 40, 48), 1)]
            self.assertEqual(starts, sorted(starts))
            self.assertIn(["Durum", "Failed"], result_rows[starts[0]:starts[1]])
            for start, end, value in zip(starts[1:4], starts[2:], ("102", "301", "302"), strict=True):
                self.assertIn(["XX", value], result_rows[start:end])
            self.assertFalse(jobs.results_button.isEnabled())
            for row in (1, 2, 0):
                table.selectRow(row)
                output = Path(records[row]["local_directory"]) / "output"
                self.assertTrue(jobs.results_button.isEnabled())
                with patch("new_doe_page.QDesktopServices.openUrl") as open_url:
                    jobs.results_button.click()
                open_url.assert_called_once()
                self.assertEqual(Path(open_url.call_args.args[0].toLocalFile()), output)
            table.clearSelection()
            self.assertFalse(jobs.results_button.isEnabled())

    def test_truba_chain_stops_submitting_when_a_predecessor_is_unconfirmed(self):
        page = self.page()
        grid = next(f for f in page.factors if f.path == ("grid",))
        grid.variable.setChecked(True)
        grid.mode.setCurrentIndex(grid.mode.findData("list"))
        grid.selection.setText("16, 24, 32")
        page.create()
        job = Mock()
        job.remote_status.return_value = {}
        job.done.return_value = True
        job.result.side_effect = RuntimeError("Submission was not confirmed.")
        with patch("doe_studio.results_directory", return_value=Path(self.temp.name)), \
                patch.object(self.controller, "auth", return_value=object()), \
                patch("new_doe_page.launch", return_value=job) as launch:
            page.start(remote=True)
            page.advance()
            page.advance()
        launch.assert_called_once()
        self.assertEqual(page.sheet.states, ["Failed", "Failed", "Skipped"])
        self.assertFalse(page.timer.isActive())

    def test_run_settings_are_saved_per_analysis_and_restore_window_bounds(self):
        from new_doe_page import DoeRunDialog, NewDoePage

        page = self.page()
        page.preferences.setValue("doe/pairTrubaExperiments/fft_solve", False)
        with patch("new_doe_page.launch") as launch:
            page.run_settings.click()
            dialog, = page.findChildren(DoeRunDialog)
            self.assertEqual(dialog.batch_size.value(), 1)
            self.assertFalse(dialog.batch_size.isEnabled())
            self.assertFalse(page.run_settings.icon().isNull())
            self.assertEqual(dialog.rustmechCaption.height(), 25)
            dialog.target.setCurrentIndex(dialog.target.findData("truba"))
            dialog.batch_size.lineEdit().selectAll()
            QTest.keyClicks(dialog.batch_size.lineEdit(), "3")
            QTest.keyClick(dialog.batch_size.lineEdit(), Qt.Key.Key_Return)
            self.assertEqual(dialog.batch_size.value(), 3)
            self.assertTrue(dialog.isVisible())
            dialog.resize(430, 220)
            dialog.move(45, 55)
            self.app.processEvents()
            expected = dialog.geometry()
            dialog.showMaximized()
            self.app.processEvents()
            dialog.confirm.click()
            launch.assert_not_called()

        self.assertEqual(page.run_options("flow_solve"), ("local", 2))
        dialog = page.open_run_options()
        self.assertTrue(dialog.isMaximized())
        dialog.showNormal()
        self.app.processEvents()
        self.assertEqual(dialog.geometry(), expected)
        dialog.batch_size.setValue(4)
        dialog.reject()
        reopened = NewDoePage(None, page.preferences)
        try:
            self.assertEqual(reopened.run_options("fft_solve"), ("truba", 3))
        finally:
            reopened.deleteLater()

    def test_truba_jobs_window_restores_normal_bounds_and_maximized_state(self):
        from new_doe_page import DoeTrubaJobsWindow
        from window_state import show_restored_window

        owner = self.page().window()
        first = DoeTrubaJobsWindow(owner)
        show_restored_window(first)
        self.app.processEvents()
        first.resize(540, 310)
        first.move(45, 55)
        self.app.processEvents()
        expected = first.geometry()
        self.assertEqual(first.rustmechCaption.height(), 25)
        first.showMaximized()
        self.app.processEvents()
        first.close()

        reopened = DoeTrubaJobsWindow(owner)
        show_restored_window(reopened)
        self.app.processEvents()
        self.assertTrue(reopened.isMaximized())
        reopened.showNormal()
        self.app.processEvents()
        self.assertEqual(reopened.geometry(), expected)
        reopened.close()

    def test_truba_uses_selected_operation_and_downloads_into_the_row(self):
        from new_doe_operations import OPERATIONS, launch, snapshot
        from remote_operations import OPERATIONS as capabilities
        from truba_remote import run_remote_operation, submit_remote_analysis

        directory = Path(self.temp.name) / "row_00001"
        controller = Mock()
        controller.require_analysis.return_value = ("context", "auth")
        with patch("analysis_process.AnalysisProcess") as worker:
            for op in OPERATIONS.values():
                with self.subTest(operation=op):
                    request = snapshot(self.window, op)
                    if op == "mechanical":
                        request["settings"]["solver_backend"] = "kratos"
                    launch(request, directory, controller)
                    self.assertEqual(controller.require_analysis.call_args.args[0], capabilities[op])
                    self.assertTrue(controller.require_analysis.call_args.kwargs["explicit_target"])
                    worker.assert_called_with(run_remote_operation, request, "context", remote_auth="auth",
                                              remote_options={"directory": directory / "truba"})
                    row = dict(request, experiment_index=1, experiment_count=2)
                    launch(row, directory, controller)
                    worker.assert_called_with(submit_remote_analysis, row, "context", remote_auth="auth",
                                              remote_options={"directory": directory / "truba"})
