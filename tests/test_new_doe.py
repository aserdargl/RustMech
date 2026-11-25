"""Exercise the operation planner through the real workspace settings."""
import json
from pathlib import Path
import time
import unittest
from unittest.mock import Mock, patch
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTableWidget

from tests import test_remote_mode_ui as ui


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
        self.assertTrue(studio.save_plan_btn.isHidden())
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
        self.assertTrue(studio.planner.language_selector.isVisible())
        buttons = studio.planner.navigation.layout()
        self.assertEqual(buttons.itemAt(2).widget().text(), "DoE")
        for index in range(2, buttons.count()):
            button = buttons.itemAt(index).widget()
            self.assertGreaterEqual(button.width(), button.fontMetrics().horizontalAdvance(button.text()) + 22)
        tabs.setCurrentIndex(3)
        self.assertTrue(studio.open_plan_btn.isVisible())
        self.assertEqual(studio.open_plan_btn.width(), 64)
        self.assertEqual(studio.save_plan_btn.width(), 64)

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
        for i in range(page.operation.count()):
            page.operation.setCurrentIndex(i)
            with self.subTest(op=page.operation.currentText()):
                self.assertTrue(page.loaded, page.status.text())
                self.assertEqual(page.base, snapshot(self.window, page.operation.currentData()))
                page.create()
                self.assertEqual(len(page.rows), 1, page.status.text())
        page.operation.setCurrentIndex(0)
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
        grid.selection.setText("16, 24.5")
        page.create()
        self.assertFalse(page.rows)
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
            self.assertEqual(field.specialValueText(), "")
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

    def test_grouped_drafts_build_geometry_only_on_apply(self):
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
        with patch.object(self.window, "_show_doe_bounds") as preview:
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
            preview.assert_not_called()
            page.apply_button.click()
            self.assertEqual(next(iter(preview.call_args.args[1].values())).dimensions, (6, 3, 2))
            self.assertFalse(page.rows)
            page.create()
            QTest.keyClick(geometry, Qt.Key.Key_Space)
            self.assertTrue(cell.fixed.isVisible())
            self.assertEqual((cell.first.value(), cell.last.value()), (2, 4))
            self.assertEqual(len(page.rows), 2)
            page.preview.selectRow(1)
            page.apply_button.click()
            self.assertEqual(next(iter(preview.call_args.args[1].values())).dimensions, (6, 3, 4))
            self.assertEqual(preview.call_count, 2)

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

    def test_run_menu_selects_target_for_the_matrix(self):
        page = self.page()
        page.create()
        local, remote, separate = page.run.menu().actions()
        controller = self.window.remote_controller
        with patch("doe_studio.results_directory", return_value=Path(self.temp.name)), \
                patch.object(controller, "auth", return_value=None) as auth, \
                patch("new_doe_page.launch") as launch:
            remote.trigger()
            self.assertIsNone(page.directory)
            self.assertFalse(page.timer.isActive())
            self.assertTrue(page.run.isEnabled())
            launch.assert_not_called()

            auth.return_value = object()
            context, credentials = controller.require_analysis("damask", explicit_target=True)
            self.assertEqual(credentials, auth.return_value)
            self.assertTrue(context.partition)
            remote.trigger()
            self.assertIs(launch.call_args.args[2], controller)
            self.assertFalse(controller.requested)
            page.cancel()

            with patch.object(controller, "requested", True):
                local.trigger()
                self.assertIsNone(launch.call_args.args[2])
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
            page.start(remote=True)
            job.remote_status.return_value = dict(job_id="71", state="RUNNING", experiment_index=2)
            page.advance()
            self.assertIn("2/2", page.status.text())
            self.assertEqual(page.sheet.states, ["Awaiting results", "Running"])
            job.done.return_value = True
            job.result.return_value = str(output)
            page.advance()
            launch.assert_called_once()
            self.assertEqual(launch.call_args.args[0]["experiment_rows"], page.rows)
            self.assertEqual(page.sheet.states, ["Failed", "Completed"])
            self.assertFalse(page.timer.isActive())

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
            page.run.menu().actions()[2].trigger()
            page.advance()
            page.advance()
            self.assertEqual(len(records), 2)
            self.assertTrue(page.timer.isActive())
            self.assertEqual(page.sheet.states, ["Queued", "Queued"])
            for index, state in enumerate(("RUNNING|None", "downloaded")):
                record = records[index]
                record["state"] = state
                (Path(record["local_directory"]) / "remote_job.json").write_text(json.dumps(record))
            clock.return_value = 3
            page.advance()
            self.assertEqual(page.sheet.states, ["Running", "Completed"])
            records[0]["state"] = "downloaded"
            (Path(records[0]["local_directory"]) / "remote_job.json").write_text(json.dumps(records[0]))
            clock.return_value = 6
            page.advance()
            self.assertFalse(page.timer.isActive())
            for record in records:
                saved = json.loads((Path(record["local_directory"]).parent / "experiment.json").read_text())
                self.assertEqual(saved["slurm_job_id"], record["job_id"])
                self.assertEqual(saved["state"], "Completed")

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
