"""Exercise the operation planner through the real workspace settings."""
import json
from pathlib import Path
import time
import unittest
from unittest.mock import Mock, patch

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
        self.assertEqual(studio.settings_tabs.tabText(studio.settings_tabs.currentIndex()), "NEW DOE")
        self.assertTrue(studio.save_plan_btn.isHidden())
        self.assertLess(studio.capture_plan()["selection"]["tab"], 5)
        return page

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
        cell = factors["cell_size"]
        cell.variable.setChecked(True)
        cell.first.setValue(2)
        cell.last.setValue(4)
        page.create()
        self.assertEqual([(r["grid"], r["settings"]["size"]) for r in page.rows],
                         [(g, s) for g in (16, 32) for s in (2, 4)])
        grid.mode.setCurrentIndex(grid.mode.findData("list"))
        self.assertFalse(page.run.isEnabled())
        grid.selection.setText("32, 17, 24")
        page.create()
        self.assertEqual([(r["grid"], r["settings"]["size"]) for r in page.rows],
                         [(g, s) for g in (32, 17, 24) for s in (2, 4)])
        cell.mode.setCurrentIndex(cell.mode.findData("list"))
        cell.selection.setText("1,25; 3,5")
        page.create()
        self.assertEqual([r["settings"]["size"] for r in page.rows], [1.25, 3.5] * 3)
        self.assertTrue(all(r["void_ratio"] == self.window.fft_void_ratio.value() for r in page.rows))
        self.assertFalse(self.window.doe_studio.rows)
        grid.selection.setText("16, 24.5")
        page.create()
        self.assertFalse(page.rows)
        self.assertFalse(page.run.isEnabled())

    def test_grouped_drafts_build_geometry_only_on_apply(self):
        page = self.page()
        cell = next(f for f in page.factors if f.path == ("settings", "cell_size"))
        row = page.factors.index(cell)
        with patch.object(self.window, "_show_doe_bounds") as preview:
            self.assertFalse(page.inputs.isRowHidden(row))
            cell.first.setValue(2)
            cell.variable.setChecked(True)
            cell.last.setValue(4)
            page.group.setCurrentIndex(page.group.findData("FFT Homogenization"))
            self.assertTrue(page.inputs.isRowHidden(row))
            preview.assert_not_called()
            page.apply_button.click()
            self.assertEqual(next(iter(preview.call_args.args[1].values())).size, 2)
            self.assertFalse(page.rows)
            page.create()
            page.preview.selectRow(1)
            page.apply_button.click()
            self.assertEqual(next(iter(preview.call_args.args[1].values())).size, 4)
            self.assertEqual(preview.call_count, 2)

    def test_two_peridynamic_runs_save_separate_results(self):
        page = self.page()
        page.operation.setCurrentIndex(page.operation.findData("peridynamic_solve"))
        fields = {f.path[-1]: f for f in page.factors}
        fields["resolution"].first.setValue(12)
        fields["cell_size"].first.setValue(6)
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

    def test_truba_uses_selected_operation_and_downloads_into_the_row(self):
        from new_doe_operations import OPERATIONS, launch, snapshot
        from remote_operations import OPERATIONS as capabilities
        from truba_remote import run_remote_operation

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
                    worker.assert_called_with(run_remote_operation, request, "context", remote_auth="auth",
                                              remote_options={"directory": directory / "truba"})
