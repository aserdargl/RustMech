"""Exercise convergence decisions and live Qt updates without a production solver."""
import csv
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from functools import partial
import time
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtTest import QTest
from studio_style import NUMERIC_FIELD_WIDTH
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QComboBox, QDialog

from mesh_convergence import (
    Level, METRICS, MeshConvergenceDialog, MeshConvergenceWidget,
    converged_level, differences, next_mesh_size, pair_passes, solve_level,
)
from model import Settings
from truba_remote import RemoteContext, request_resources


SETTINGS = Settings(size=1, cell_size=1, compression=0.1, mesh_resolution=0.2, self_contact_mode="disabled")


def process_mesh(settings):
    return SimpleNamespace(grid=SimpleNamespace(n_cells=round(1 / settings.mesh_resolution**3)),
                           solve_errors=[])


def process_solver(folder, settings, mesh):
    return {"status": "completed", "run_folder": folder,
            "graphs": {key: 100 for _, key in METRICS}}


def blocking_mesh(marker, settings):
    Path(marker).write_text("meshing")
    time.sleep(120)
    return process_mesh(settings)


def blocking_solver(marker, settings, mesh):
    Path(marker).write_text("solving")
    time.sleep(120)


def completed(size, value, elements, **kwargs):
    return Level(replace(SETTINGS, mesh_resolution=size), elements=elements,
                 metrics=[value] * 3, status="completed", **kwargs)


class ConvergenceDecisionTests(unittest.TestCase):
    def test_requires_two_successive_pairs(self):
        levels = [completed(.2, 100, 100), completed(.15, 101, 200), completed(.1, 102, 300)]
        self.assertIsNone(converged_level(levels[:2], 2, [0, 1, 2], [0] * 3))
        self.assertIs(converged_level(levels, 2, [0, 1, 2], [0] * 3), levels[-1])
        levels[0].metrics = [80] * 3
        self.assertIsNone(converged_level(levels, 2, [0, 1, 2], [0] * 3))

    def test_selected_metrics_and_absolute_tolerance(self):
        a, b = completed(.2, 100, 100), completed(.1, 101, 200)
        b.metrics[1] = 200
        self.assertTrue(pair_passes(a, b, 2, [0], [0] * 3))
        self.assertFalse(pair_passes(a, b, 2, [0, 1], [0] * 3))
        a.metrics, b.metrics = [1e-12] * 3, [0] * 3
        self.assertTrue(math.isinf(differences(a, b)[0]))
        self.assertTrue(pair_passes(a, b, 2, [0], [1e-9] * 3))

    def test_identical_or_decreasing_element_counts_are_not_convergence(self):
        a, b = completed(.2, 100, 100), completed(.1, 100, 100)
        self.assertFalse(pair_passes(a, b, 2, [0], [0] * 3))
        b.elements = 99
        self.assertFalse(pair_passes(a, b, 2, [0], [0] * 3))

    def test_nearly_identical_sizes_are_not_convergence(self):
        self.assertFalse(pair_passes(completed(.2, 100, 100), completed(.199, 100, 200),
                                     2, [0], [0] * 3))

    def test_large_jump_inserts_geometric_midpoint_without_repeating_it(self):
        levels = [completed(.2, 100, 100), completed(.2 / 1.3, 150, 300)]
        size, kind = next_mesh_size(levels, 2, [0], 1.3)
        self.assertEqual(kind, "Intermediate")
        self.assertAlmostEqual(size, math.sqrt(.2 * (.2 / 1.3)))
        levels.append(completed(size, 120, 200))
        next_size, kind = next_mesh_size(levels, 2, [0], 1.3)
        self.assertEqual(kind, "Refinement")
        self.assertAlmostEqual(next_size, .2 / 1.3**2)

    def test_failed_mesh_is_not_revisited_as_an_intermediate_level(self):
        levels = [completed(.2, 100, 100)]
        midpoint = math.sqrt(.2 * .1)
        levels.append(Level(replace(SETTINGS, mesh_resolution=midpoint), status="failed"))
        levels.append(completed(.1, 150, 300))
        next_size, kind = next_mesh_size(levels, 2, [0], 1.3)
        self.assertEqual(kind, "Refinement")
        self.assertAlmostEqual(next_size, .1 / 1.3)

    def test_intermediate_execution_order_does_not_change_comparison_order(self):
        coarse, medium, fine = completed(.2, 100, 100), completed(.15, 101, 200), completed(.1, 102, 300)
        self.assertIs(converged_level([coarse, fine, medium], 2, [0], [0] * 3), fine)

    def test_failed_and_nonfinite_results_do_not_confirm_convergence(self):
        levels = [completed(.2, 100, 100), completed(.15, 101, 200), completed(.1, 102, 300)]
        levels[-1].status = "failed"
        self.assertIsNone(converged_level(levels, 2, [0], [0] * 3))
        levels[-1].status = "completed"
        levels[-1].metrics[0] = float("nan")
        self.assertIsNone(converged_level(levels, 2, [0], [0] * 3))


class LiveConvergenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.folder = Path(self.temp.name)
        window_settings = QSettings(str(self.folder / "window.ini"), QSettings.Format.IniFormat)
        preferences_patch = patch("mesh_convergence.QSettings", return_value=window_settings)
        preferences_patch.start()
        self.addCleanup(preferences_patch.stop)
        from window_state import WindowStateStore
        self.window_store = WindowStateStore(self.app, window_settings)
        graphs = self.folder / "04_Graphs"
        graphs.mkdir()
        (graphs / "force_displacement.csv").write_text(
            "Compression_mm,Compression_Force_N\n0,0\n0.1,100\n", encoding="utf-8")
        # Decision/UI tests use in-process callbacks; process isolation is tested separately.
        self.executor = ThreadPoolExecutor(max_workers=1)
        process_patch = patch("mesh_convergence.AnalysisProcess", side_effect=self.executor.submit)
        process_patch.start()
        self.addCleanup(process_patch.stop)
        self.widgets = []
        self.calls = []
        self.release = Event()
        self.entered = Event()

    def tearDown(self):
        self.release.set()
        for widget in self.widgets:
            if widget.future is not None:
                widget.future.result(timeout=5)
            widget.dispose()
            widget.deleteLater()
        self.executor.shutdown(wait=True)
        self.app.processEvents()
        self.app.removeEventFilter(self.window_store)
        self.temp.cleanup()

    def mesh(self, settings):
        return SimpleNamespace(grid=SimpleNamespace(n_cells=round(1 / settings.mesh_resolution**3)),
                               solve_errors=[])

    def solver(self, settings, mesh):
        self.calls.append(settings.mesh_resolution)
        value = 100 * (1 + settings.mesh_resolution**2)
        return {"status": "completed", "run_folder": str(self.folder),
                "graphs": {key: value for _, key in METRICS}}

    def widget(self, mesh=None, solver=None, settings=SETTINGS):
        widget = MeshConvergenceWidget(settings, mesh or self.mesh, solver or self.solver)
        self.widgets.append(widget)
        return widget

    def finish(self, widget):
        for _ in range(40):
            if widget.future is None:
                return
            widget.future.result(timeout=5)
            widget._poll()
        self.fail("Study did not finish within the test's bounded attempts")

    def test_automatic_default_is_minimal_and_stops_at_tolerance(self):
        widget = self.widget()
        self.assertTrue(widget.automatic.isChecked())
        self.assertTrue(widget.advanced.isHidden())
        widget.start()
        self.assertEqual(widget.results.rowCount(), 1)
        self.finish(widget)
        self.assertEqual(len(self.calls), 3)
        self.assertIn("Converged", widget.status.text())
        self.assertTrue(widget.apply_button.isEnabled())
        self.assertTrue(all(v.mesh_seconds is not None and v.solve_seconds is not None for v in widget.rows))
        self.assertIn("#2", widget.results.item(2, 6).text())

    def test_mesh_time_and_elements_are_visible_while_solver_is_running(self):
        def blocked_solver(settings, mesh):
            self.entered.set()
            self.release.wait(5)
            return self.solver(settings, mesh)
        widget = self.widget(solver=blocked_solver)
        widget.start()
        self.assertTrue(self.entered.wait(5))
        widget._poll()
        self.assertEqual(widget.results.item(0, 7).text(), "Solving")
        self.assertIn("125 elements", widget.results.item(0, 0).text())
        self.assertNotEqual(widget.results.item(0, 1).text(), "—")
        self.assertEqual(widget.results.item(0, 3).text(), "—")
        widget.request_stop()
        self.release.set()
        self.finish(widget)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("Stopped", widget.status.text())

    def test_stop_during_meshing_skips_solver(self):
        def blocked_mesh(settings):
            self.entered.set()
            self.release.wait(5)
            return self.mesh(settings)
        widget = self.widget(mesh=blocked_mesh)
        widget.start()
        self.assertTrue(self.entered.wait(5))
        widget.request_stop()
        self.release.set()
        self.finish(widget)
        self.assertEqual(self.calls, [])
        self.assertEqual(widget.rows[0].status, "Stopped after meshing")

    def test_forced_stop_cancels_current_level_and_manual_queue_then_allows_restart(self):
        widget = self.widget()
        widget.automatic.setChecked(False)
        widget.manual.setText("0.2, 0.15, 0.1")
        job = MagicMock()
        job.done.return_value = False
        self.assertFalse(widget.force_stop_button.isEnabled())
        with patch("mesh_convergence.AnalysisProcess", return_value=job):
            widget.start()
            self.assertTrue(widget.force_stop_button.isEnabled())
            widget.force_stop_button.click()
        job.force_stop.assert_called_once()
        self.assertIsNone(widget.future)
        self.assertIsNone(widget.updates)
        self.assertFalse(widget.timer.isActive())
        self.assertEqual(widget.manual_pending, [])
        self.assertEqual(widget.rows[0].status, "Cancelled (Forced Stop)")
        self.assertTrue(widget.run_button.isEnabled())
        self.assertTrue(widget.export_button.isEnabled())
        self.assertFalse(widget.apply_button.isEnabled())
        self.assertFalse(widget.force_stop_button.isEnabled())
        widget.start()
        self.finish(widget)
        self.assertIn("Converged", widget.status.text())

    def test_real_process_forced_stop_during_mesh_and_solve_and_restart(self):
        from analysis_process import AnalysisProcess
        for phase in ("mesh", "solve"):
            with self.subTest(phase=phase):
                marker = str(self.folder / f"{phase}.marker")
                widget = self.widget(
                    mesh=partial(blocking_mesh, marker) if phase == "mesh" else process_mesh,
                    solver=partial(blocking_solver, marker))
                with patch("mesh_convergence.AnalysisProcess", AnalysisProcess):
                    widget.start()
                    job = widget.future
                    try:
                        deadline = time.monotonic() + 25
                        while not Path(marker).exists() and time.monotonic() < deadline:
                            QTest.qWait(20)
                        self.assertTrue(Path(marker).exists())
                        widget._poll()
                        widget.force_stop_button.click()
                        self.assertIsNone(widget.future)
                        self.assertTrue(job._process._closed)
                        self.assertEqual(widget.rows[-1].status, "Cancelled (Forced Stop)")
                        self.assertEqual(len(widget.rows), 1)
                        self.assertFalse(widget.timer.isActive())
                    finally:
                        if widget.future is not None:
                            widget.force_stop()
                    widget.mesh_generator = process_mesh
                    widget.solver = partial(process_solver, str(self.folder))
                    widget.start()
                    deadline = time.monotonic() + 30
                    while widget.future is not None and time.monotonic() < deadline:
                        QTest.qWait(20)
                    self.assertIsNone(widget.future)
                    self.assertIn("Converged", widget.status.text())

    def test_failed_forced_stop_retains_job_for_retry(self):
        widget = self.widget()
        job = MagicMock()
        job.force_stop.side_effect = [RuntimeError("still running"), None]
        with patch("mesh_convergence.AnalysisProcess", return_value=job):
            widget.start()
        widget.force_stop_button.click()
        self.assertIs(widget.future, job)
        self.assertTrue(widget.force_stop_button.isEnabled())
        self.assertIn("Forced Stop failed", widget.status.text())
        widget.force_stop_button.click()
        self.assertIsNone(widget.future)

    def test_process_start_failure_restores_controls(self):
        widget = self.widget()
        with patch("mesh_convergence.AnalysisProcess", side_effect=RuntimeError("cannot spawn")):
            widget.start()
        self.assertIsNone(widget.future)
        self.assertFalse(widget.timer.isActive())
        self.assertTrue(widget.run_button.isEnabled())
        self.assertFalse(widget.force_stop_button.isEnabled())
        self.assertIn("cannot spawn", widget.status.text())

    def test_process_exit_during_analysis_retries_with_a_finer_mesh(self):
        widget = self.widget()
        process = MagicMock()
        process.result.side_effect = RuntimeError("Analysis process exited (1).")
        with patch("mesh_convergence.AnalysisProcess", return_value=process):
            widget.start()
        widget._poll()
        self.finish(widget)
        self.assertIn("Analysis process exited", widget.rows[0].status)
        self.assertAlmostEqual(self.calls[0], .2 / widget.factor.value())
        self.assertIn("Converged", widget.status.text())

    def test_attempt_limit_and_intermediate_values(self):
        def unstable(settings, mesh):
            result = self.solver(settings, mesh)
            result["graphs"] = {key: 1 / settings.mesh_resolution for _, key in METRICS}
            return result
        widget = self.widget(solver=unstable)
        widget.max_levels.setValue(4)
        widget.start()
        self.finish(widget)
        self.assertEqual(len(widget.rows), 4)
        self.assertEqual(widget.rows[2].kind, "Intermediate")
        self.assertGreater(widget.rows[2].settings.mesh_resolution, widget.rows[1].settings.mesh_resolution)
        self.assertEqual(widget.rows[1].reference, 2)
        self.assertIn("Attempt limit", widget.status.text())
        self.assertFalse(widget.apply_button.isEnabled())

    def test_minimum_size_limit(self):
        widget = self.widget()
        widget.minimum.setValue(.19)
        widget.start()
        self.finish(widget)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("Minimum mesh size", widget.status.text())

    def test_element_limit_prevents_solve(self):
        widget = self.widget()
        widget.max_elements.setValue(100)
        widget.start()
        self.finish(widget)
        self.assertEqual(self.calls, [])
        self.assertIn("Element limit exceeded", widget.status.text())

    def test_time_budget_prevents_next_level(self):
        widget = self.widget()
        widget.start()
        widget.future.result(timeout=5)
        widget.started -= 10000
        widget._poll()
        self.assertEqual(len(self.calls), 1)
        self.assertIn("Time budget", widget.status.text())

    def test_manual_list_stops_early_on_convergence(self):
        widget = self.widget()
        widget.mesh_type_combo.setCurrentIndex(widget.mesh_type_combo.findData("HEX20"))
        widget.automatic.setChecked(False)
        widget.manual.setText("0.2, 0.15, 0.1, 0.08")
        widget.start()
        self.finish(widget)
        self.assertEqual(self.calls, [.2, .15, .1])
        self.assertEqual([row.settings.mesh_type for row in widget.rows], ["HEX20"] * 3)
        self.assertIn("Converged", widget.status.text())

    def test_tetra_local_sizes_scale_without_changing_design(self):
        widget = self.widget(settings=replace(SETTINGS, local_mesh_size=.05))
        self.assertEqual(widget.mesh_type_combo.currentData(), "HEX8")
        for mesh_type, enabled in (("HEX8", False), ("TET4", True), ("HEX20", False), ("TET10", True)):
            widget.mesh_type_combo.setCurrentIndex(widget.mesh_type_combo.findData(mesh_type))
            self.assertEqual(widget.local_size.isEnabled(), enabled)
            self.assertAlmostEqual(widget.local_size.value(), .05)
        level = widget.level_settings(.1)
        self.assertEqual(level.mesh_type, "TET10")
        self.assertEqual(level.local_mesh_size, .025)
        self.assertEqual(level.compression, SETTINGS.compression)
        self.assertEqual(level.material_name, SETTINGS.material_name)
        widget.start()
        self.assertFalse(widget.mesh_type_combo.isEnabled())
        self.assertFalse(widget.local_size.isEnabled())
        self.finish(widget)
        self.assertEqual([row.settings.mesh_type for row in widget.rows], ["TET10"] * 3)
        self.assertTrue(widget.mesh_type_combo.isEnabled())
        self.assertTrue(widget.local_size.isEnabled())

    def test_tetra_local_size_is_capped_when_opening_or_changing_mesh_settings(self):
        for mesh_type in ("TET4", "TET10"):
            with self.subTest(mesh_type=mesh_type):
                widget = self.widget(settings=replace(SETTINGS, mesh_type=mesh_type, local_mesh_size=.3))
                self.assertEqual(widget.local_size.value(), .2)
                widget.start_size.setValue(.04)
                self.assertEqual(widget.local_size.value(), .04)
                widget.local_size.setValue(.05)
                self.assertEqual(widget.local_size.value(), .04)
                level = widget.level_settings(.013)
                self.assertEqual(level.local_mesh_size, level.mesh_resolution)
                widget.mesh_type_combo.setCurrentIndex(widget.mesh_type_combo.findData("HEX8"))
                widget.start_size.setValue(.03)
                self.assertEqual(widget.local_size.value(), .04)
                widget.mesh_type_combo.setCurrentIndex(widget.mesh_type_combo.findData(mesh_type))
                self.assertEqual(widget.local_size.value(), .03)
                widget.start_size.setValue(.05)
                self.assertEqual(widget.local_size.maximum(), .05)
                self.assertEqual(widget.local_size.value(), .03)

    def test_mesh_types_follow_ready_truba_runtime(self):
        widget = self.widget()
        widget.remote_controller = MagicMock(requested=True, ready=True,
                                             capabilities={"mesh_types": ["TET4"]})
        widget._solver_changed()
        self.assertEqual(widget.mesh_type_combo.currentData(), "TET4")
        self.assertFalse(widget.mesh_type_combo.model().item(widget.mesh_type_combo.findData("HEX8")).isEnabled())
        with patch("mesh_convergence.AnalysisProcess") as process:
            widget.remote_controller.require_convergence.return_value = (RemoteContext(session_id="321"), object())
            widget.start()
            self.assertEqual(process.call_args.args[1].mesh_type, "TET4")
            widget.force_stop()

    def test_truba_retries_a_terminal_failure_and_stops_if_job_state_is_unknown(self):
        for accounting in ("", "FAILED|1:0"):
            with self.subTest(accounting=accounting):
                widget = self.widget()
                widget.remote_controller = MagicMock(requested=True, ready=True,
                                                     capabilities={"mesh_types": ["TET4"]})
                widget.remote_controller.require_convergence.return_value = (RemoteContext(session_id="321"), object())
                widget._solver_changed()
                process = MagicMock()
                process.result.side_effect = RuntimeError("Remote results are unavailable")
                process.remote_status.return_value = {"job_id": "1234", "slurm_status": accounting}
                with patch("mesh_convergence.TRUBA_BATCH_SIZE", 1), \
                        patch("mesh_convergence.AnalysisProcess", return_value=process) as launch:
                    widget.start()
                    widget._poll()
                    self.assertEqual(launch.call_count, 2 if accounting else 1)
                    if accounting:
                        self.assertAlmostEqual(launch.call_args.args[1].mesh_resolution,
                                               .2 / widget.factor.value())
                        widget.force_stop()
                    else:
                        self.assertIn("Remote results are unavailable", widget.status.text())

    def truba_study(self, cores=20, slots=5):
        widget = self.widget()
        widget.remote_controller = MagicMock(requested=True, ready=True,
                                             capabilities={"mesh_types": ["HEX8"]})
        context = RemoteContext(cores=cores, memory_mb=10000, parallel_jobs=slots)
        widget.remote_controller.require_convergence.return_value = (context, object())
        widget._solver_changed()
        return widget

    def test_truba_preflight_error_is_visible_in_results(self):
        widget = self.truba_study()
        widget.remote_controller.require_convergence.side_effect = RuntimeError("Queue unavailable")
        with patch("mesh_convergence.AnalysisProcess") as submit:
            widget.start()
        submit.assert_not_called()
        self.assertEqual(widget.results.rowCount(), 1)
        self.assertIn("Queue unavailable", widget.results.item(0, 7).text())
        self.assertTrue(widget.run_button.isEnabled())

    def test_truba_runs_five_levels_and_uses_results_for_the_next_batch(self):
        widget = self.truba_study()
        widget.max_levels.setValue(8)
        jobs = []

        def launch(_target, settings, context, *_args, **_kwargs):
            job = MagicMock()
            job.done.return_value = False
            size = settings.mesh_resolution
            job.result.return_value = completed(size, 1 / size, round(1 / size**3))
            jobs.append(job)
            return job

        with patch("mesh_convergence.AnalysisProcess", side_effect=launch) as submit:
            widget.start()
            self.assertEqual(len(jobs), 5)
            self.assertTrue(widget.time_budget_field.isHidden())
            widget.started -= 60 * widget.max_minutes.value() + 1
            self.assertFalse(submit.call_args.args[2].session_id)
            self.assertEqual(request_resources(submit.call_args.args[2], "convergence_level"), (20, 10000))
            initial_sizes = [row.settings.mesh_resolution for row in widget.rows]
            for job in reversed(jobs[1:]):
                job.done.return_value = True
                widget._poll()
            self.assertEqual(len(jobs), 5)
            jobs[0].done.return_value = True
            widget._poll()
            self.assertFalse(widget.stop_requested.is_set())
            self.assertEqual(len(jobs), 8)
            self.assertEqual(request_resources(submit.call_args.args[2], "convergence_level"), (20, 10000))
            self.assertEqual(widget.rows[5].kind, "Intermediate")
            self.assertAlmostEqual(widget.rows[5].settings.mesh_resolution,
                                   math.sqrt(initial_sizes[-2] * initial_sizes[-1]))
            self.assertEqual([row.status for row in widget.rows[:5]], ["completed"] * 5)
            widget.force_stop()
        for job in jobs[5:]:
            job.force_stop.assert_called_once()

    def test_truba_manual_batch_sends_independent_jobs_and_detaches_all_jobs(self):
        widget = self.truba_study(cores=20, slots=2)
        widget.automatic.setChecked(False)
        widget.manual.setText(".2, .16, .13, .1, .08")
        jobs = [MagicMock() for _ in range(2)]
        with patch("mesh_convergence.AnalysisProcess", side_effect=jobs) as submit:
            widget.start()
        self.assertEqual([call.args[1].mesh_resolution for call in submit.call_args_list], [.2, .16])
        self.assertTrue(all(not call.args[2].session_id for call in submit.call_args_list))
        self.assertEqual(widget.detach_remote_jobs(), jobs)
        self.assertIsNone(widget.future)
        self.assertFalse(widget.timer.isActive())
        for job in jobs:
            job.force_stop.assert_not_called()

    def test_truba_resources_are_automatic_and_wall_time_is_persisted(self):
        widget = self.truba_study()
        self.assertFalse(widget.truba_resource_info.isHidden())
        self.assertEqual(widget.level_settings(.2).cores, 16)
        self.assertEqual(widget.truba_wall_minutes.value(), 30)
        QSettings(str(self.folder / "window.ini"), QSettings.Format.IniFormat).setValue(
            "meshConvergence/trubaCoresPerAnalysis", 8)
        widget.truba_wall_minutes.setValue(75)
        reopened = self.truba_study()
        self.assertEqual(reopened.truba_wall_minutes.value(), 75)
        self.assertEqual(reopened.level_settings(.2).cores, 16)
        self.assertFalse(reopened.level_settings(.2).automatic_cores)
        settings = reopened.level_settings(.2)
        reopened._require_remote_submission(settings, 1)
        reopened.remote_controller.require_convergence.assert_called_once_with(
            settings, max_jobs=1, walltime="01:15:00")
        reopened.remote_controller.requested = False
        reopened._solver_changed()
        self.assertTrue(reopened.truba_resource_info.isHidden())
        self.assertTrue(reopened.truba_wall_field.isHidden())
        self.assertEqual(reopened.level_settings(.2).cores, SETTINGS.cores)

    def test_truba_four_consecutive_failures_prevent_another_batch(self):
        widget = self.truba_study()

        def failed_job(_target, settings, *_args, **_kwargs):
            job = MagicMock()
            job.remote_status.return_value = {}
            job.result.return_value = Level(settings, status="failed")
            return job

        with patch("mesh_convergence.AnalysisProcess", side_effect=failed_job) as submit:
            widget.start()
            widget._poll()
            self.assertEqual(submit.call_count, 5)
        self.assertIn("after 3 failed retries", widget.status.text())
        self.assertIsNone(widget.future)

    def test_truba_pair_collects_each_level_once_and_preserves_success_after_oom(self):
        from truba_remote import SlurmOutOfMemory, run_remote_level_pair

        widget = self.truba_study(cores=56, slots=2)
        job = MagicMock()
        job.remote_status.return_value = {'job_id': '321', 'level_resources': [
            {'cores': 28, 'memory_mb': 5000}, {'cores': 28, 'memory_mb': 5000}],
            'level_peak_ram_mib': [3072, None]}
        job.result.return_value = [completed(.2, 10, 100), SlurmOutOfMemory(5000, 'step.err')]
        with patch('mesh_convergence.AnalysisProcess', return_value=job) as submit:
            widget.start()
            self.assertIs(submit.call_args.args[0], run_remote_level_pair)
            widget._poll()
        submit.assert_called_once()
        job.result.assert_called_once()
        self.assertEqual([row.status for row in widget.rows], ['completed', 'Insufficient RAM'])
        self.assertEqual(widget.remote_records[1]['failure_memory_mb'], 5000)
        self.assertTrue(all(widget.results.item(i, 8).text() == '321' for i in range(2)))
        self.assertEqual(widget.results.item(0, 13).text(), '3.00')
        self.assertEqual(widget.results.item(1, 13).text(), '—')

    def test_truba_memory_failure_stops_refinement_and_retries_exact_mesh_only_with_more_ram(self):
        from truba_remote import SlurmOutOfMemory

        widget = self.truba_study(slots=2)
        first, oom, retried = MagicMock(), MagicMock(), MagicMock()
        first.result.return_value = completed(.2, 10, 100)
        oom.result.side_effect = SlurmOutOfMemory(10000, self.folder / "slurm.err")
        oom.remote_status.return_value = {"job_id": "412", "slurm_status": "FAILED|1:0"}
        with patch("mesh_convergence.AnalysisProcess", side_effect=[first, oom, retried]) as submit:
            widget.start()
            widget._poll()
            self.assertEqual(submit.call_count, 2)
            self.assertEqual(widget.results.item(1, 7).text(), "Insufficient RAM")
            self.assertEqual(widget.results.item(1, 10).text(), "OUT_OF_MEMORY")
            self.assertIn("Further refinement stopped", widget.status.text())
            widget._retry_memory_failure(1)
            self.assertEqual(submit.call_count, 2)
            self.assertIn("Increase the TRUBA RAM", widget.status.text())

            widget.remote_controller.require_convergence.return_value = (
                RemoteContext(cores=40, memory_mb=262144, parallel_jobs=1), object())
            widget._retry_memory_failure(1)
            self.assertEqual(submit.call_count, 3)
            settings, context = submit.call_args.args[1:3]
            self.assertEqual(settings.mesh_resolution, widget.rows[1].settings.mesh_resolution)
            self.assertEqual(settings.cores, 40)
            self.assertEqual(context.memory_mb, 262144)
            widget.remote_controller.require_convergence.assert_called_with(
                replace(settings, cores=16), max_jobs=1, walltime="00:30:00")
            retried.result.return_value = completed(settings.mesh_resolution, 15, 150)
            widget._poll()
            self.assertEqual(submit.call_count, 3)
            self.assertIsNone(widget.future)
            self.assertEqual(widget.status.text(), "Single mesh completed.")

    def test_single_truba_mesh_uses_starting_size_and_stops_after_one_result(self):
        widget = self.truba_study()
        widget.single_truba_mesh.setChecked(True)
        job = MagicMock()
        job.result.return_value = completed(.2, 10, 100)
        with patch("mesh_convergence.AnalysisProcess", return_value=job) as submit:
            widget.start()
            widget._poll()
        submit.assert_called_once()
        widget.remote_controller.require_convergence.assert_called_with(
            replace(submit.call_args.args[1], cores=16), max_jobs=1, walltime="00:30:00")
        self.assertEqual(submit.call_args.args[1].cores, 20)
        self.assertEqual(submit.call_args.args[1].mesh_resolution, .2)
        self.assertIsNone(widget.future)

    def test_loading_and_contacts_are_frozen_for_every_level(self):
        received = []
        def solver(settings, mesh):
            received.append(settings)
            return self.solver(settings, mesh)
        widget = self.widget(solver=solver, settings=replace(
            SETTINGS, bottom_platen_friction_coeff=.45,
            top_platen_friction_coeff=.25, contact_damping_factor=.03))
        self.assertFalse(hasattr(widget, "analysis_setup"))
        widget.start()
        self.finish(widget)
        self.assertEqual(len(received), 3)
        for settings in received:
            self.assertEqual(settings.compression, .1)
            self.assertEqual(settings.bottom_platen_friction_coeff, .45)
            self.assertEqual(settings.top_platen_friction_coeff, .25)
            self.assertEqual(settings.contact_damping_factor, .03)

    def test_failed_solver_and_incomplete_strain_stop_after_three_finer_retries(self):
        for status in ("failed", "completed"):
            with self.subTest(status=status):
                def bad_solver(settings, mesh):
                    return {"status": status, "run_folder": "", "graphs": {}}
                widget = self.widget(solver=bad_solver)
                widget.start()
                self.finish(widget)
                self.assertEqual(len(widget.rows), 4)
                for index, level in enumerate(widget.rows):
                    self.assertAlmostEqual(level.settings.mesh_resolution, .2 / widget.factor.value()**index)
                self.assertEqual(widget.rows[-1].kind, "Retry 3/3")
                self.assertIsNone(widget.recommended)
                self.assertIn("after 3 failed retries", widget.status.text())

    def test_success_resets_the_retry_count(self):
        outcomes = iter([False] * 3 + [True] + [False] * 3 + [True, True])

        def intermittent_mesh(settings):
            if not next(outcomes):
                raise RuntimeError("mesh unavailable")
            return self.mesh(settings)

        widget = self.widget(mesh=intermittent_mesh)
        widget.start()
        self.finish(widget)
        self.assertEqual(len(widget.rows), 9)
        self.assertEqual([level.kind for level in widget.rows if level.kind == "Retry 3/3"],
                         ["Retry 3/3", "Retry 3/3"])
        self.assertIn("Converged", widget.status.text())

    def test_failed_manual_level_uses_the_next_configured_size(self):
        def first_mesh_fails(settings):
            if settings.mesh_resolution == .2:
                raise RuntimeError("mesh unavailable")
            return self.mesh(settings)

        widget = self.widget(mesh=first_mesh_fails)
        widget.automatic.setChecked(False)
        widget.manual.setText("0.2, 0.15, 0.1, 0.08")
        widget.start()
        self.finish(widget)
        self.assertEqual(self.calls, [.15, .1, .08])
        self.assertIn("Converged", widget.status.text())

    def test_worker_records_meshing_failure_and_missing_metrics(self):
        def broken_mesh(settings):
            raise RuntimeError("mesh unavailable")
        level = solve_level(SETTINGS, broken_mesh, self.solver)
        self.assertIn("mesh unavailable", level.status)
        self.assertIsNotNone(level.mesh_seconds)
        self.assertEqual(self.calls, [])
        def missing_metrics(settings, mesh):
            return {"status": "completed", "run_folder": str(self.folder), "graphs": {}}
        level = solve_level(SETTINGS, self.mesh, missing_metrics)
        self.assertIn("Missing or non-finite", level.status)

    def test_unselected_missing_metric_does_not_block_convergence(self):
        def force_only(settings, mesh):
            result = self.solver(settings, mesh)
            result["graphs"] = {METRICS[0][1]: result["graphs"][METRICS[0][1]]}
            return result
        widget = self.widget(solver=force_only)
        for check in widget.metric_checks[1:]:
            check.setChecked(False)
        widget.start()
        self.finish(widget)
        self.assertIn("Converged", widget.status.text())
        self.assertIn("Modulus: —", widget.results.item(2, 6).toolTip())

    def test_closing_running_dialog_requests_stop_before_disposal(self):
        def blocked_solver(settings, mesh):
            self.entered.set()
            self.release.wait(5)
            return self.solver(settings, mesh)
        dialog = MeshConvergenceDialog(SETTINGS, self.mesh, blocked_solver)
        self.widgets.append(dialog.study)
        dialog.show()
        dialog.study.start()
        self.assertTrue(self.entered.wait(5))
        dialog.reject()
        self.assertTrue(dialog.isVisible())
        self.assertTrue(dialog.study.stop_requested.is_set())
        self.release.set()
        self.finish(dialog.study)
        dialog.reject()
        self.assertFalse(dialog.isVisible())
        dialog.deleteLater()

    def test_main_screen_reuses_modeless_study_and_applies_selected_mesh(self):
        from nonlinear_compression import RustMechDialog
        from studio_style import CompactDoubleSpinBox
        owner = QDialog()
        self.addCleanup(owner.deleteLater)
        owner._mesh_convergence_dialog = None
        owner._finish_mesh_convergence = partial(RustMechDialog._finish_mesh_convergence, owner)
        owner.geometry_changed = MagicMock()
        owner.loading_mode_combo = QComboBox()
        owner.solver_combo = self.widget().solver_combo
        owner.mesh_type_combo = self.widget().mesh_type_combo
        owner.self_contact_combo = QComboBox()
        owner.contact_formulation_combo = QComboBox()
        owner.platen_contact_combos = {}
        owner.collect = MagicMock(return_value=SETTINGS)
        owner.controls = {key: CompactDoubleSpinBox() for key in ("mesh_resolution", "local_mesh_size")}
        for control in owner.controls.values():
            control.setRange(.1, 10)
        owner.show()
        RustMechDialog.open_mesh_convergence(owner)
        dialog = owner._mesh_convergence_dialog
        self.addCleanup(dialog.deleteLater)
        self.assertIs(dialog.parentWidget(), owner)
        self.assertIs(dialog.windowHandle().transientParent(), owner.windowHandle())
        self.assertFalse(dialog.isModal())
        self.assertIsNone(self.app.activeModalWidget())
        self.assertFalse(dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        dialog.showMaximized()
        dialog.showMinimized()
        RustMechDialog.open_mesh_convergence(owner)
        self.assertIs(owner._mesh_convergence_dialog, dialog)
        self.assertFalse(dialog.isMinimized())
        self.assertTrue(dialog.isMaximized())
        owner.collect.assert_called_once()
        self.assertAlmostEqual(owner.controls["mesh_resolution"].value(), .1)
        dialog.selected_settings = replace(SETTINGS, mesh_type="TET4", mesh_resolution=.08, local_mesh_size=.02)
        with patch.object(dialog.study, "dispose", wraps=dialog.study.dispose) as dispose:
            dialog.accept()
            dispose.assert_called_once()
        self.assertIsNone(owner._mesh_convergence_dialog)
        self.assertAlmostEqual(owner.controls["mesh_resolution"].value(), .08)
        self.assertAlmostEqual(owner.controls["local_mesh_size"].value(), .02)
        self.assertEqual(owner.mesh_type_combo.currentData(), "TET4")
        owner.geometry_changed.assert_called_once()

    def test_restart_clears_previous_results(self):
        widget = self.widget()
        for _ in range(2):
            widget.start()
            self.finish(widget)
            self.assertEqual(len(widget.rows), 3)
            self.assertEqual(widget.results.rowCount(), 3)

    def test_toolbar_and_bottom_mesh_settings_are_compact(self):
        widget = self.widget()
        widget.resize(1080, 430)
        widget.show()
        self.app.processEvents()
        self.assertEqual(widget.run_button.text(), "Run")
        self.assertLess(widget.toolbar.y(), widget.results.y())
        for control in (widget.solver_combo, widget.export_button, widget.apply_button,
                        widget.stop_button, widget.force_stop_button, widget.run_button):
            self.assertIs(control.parentWidget(), widget.toolbar)
        self.assertIs(widget.tolerance.parentWidget().parentWidget(), widget.toolbar)
        layout = widget.layout()
        self.assertIs(layout.itemAt(layout.count() - 1).widget(), widget.mesh_settings)
        fields = widget.mesh_settings.layout()
        for index, (text, control) in enumerate((
                ("Element", widget.mesh_type_combo), ("Mesh Size", widget.start_size),
                ("Local Size", widget.local_size), ("Decimal", widget.mesh_precision))):
            label = fields.itemAt(2 * index).widget()
            self.assertEqual(label.text(), text)
            self.assertIs(label.buddy(), control)
            self.assertTrue(control.isVisible())
        self.assertEqual(fields.itemAt(0).widget().x(), fields.contentsMargins().left())
        self.assertEqual(widget.mesh_settings.width(), widget.mesh_settings.sizeHint().width())
        self.assertEqual(widget.mesh_settings.y() - widget.results_panel.geometry().bottom() - 1,
                         layout.spacing())
        buttons = (widget.export_button, widget.apply_button, widget.stop_button,
                   widget.force_stop_button, widget.run_button)
        self.assertEqual(len({(button.width(), button.height()) for button in buttons}), 1)
        self.assertFalse(hasattr(widget, "advanced_button"))
        self.assertEqual(widget.start_size.width(), NUMERIC_FIELD_WIDTH)
        self.assertEqual(widget.tolerance.width(), NUMERIC_FIELD_WIDTH - 2)
        self.assertEqual(widget.solver_combo.width(), 138)
        self.assertGreater(widget.run_button.x(), widget.stop_button.x())
        self.assertGreater(widget.run_button.x(), widget.apply_button.x())

    def test_mesh_display_decimals_are_shared_and_preserve_level_values(self):
        settings = replace(SETTINGS, mesh_type="TET4", mesh_resolution=.234567, local_mesh_size=.034567)
        widget = self.widget(settings=settings)
        self.assertEqual(widget.mesh_precision.value(), 3)
        self.assertEqual(widget.start_size.text().replace(",", "."), "0.235")
        self.assertEqual(widget.local_size.text().replace(",", "."), "0.035")
        widget.mesh_precision.setValue(6)
        self.assertEqual(widget.start_size.text().replace(",", "."), "0.234567")
        self.assertEqual(widget.local_size.text().replace(",", "."), "0.034567")
        level = widget.level_settings(widget.start_size.value())
        self.assertEqual(level.mesh_resolution, settings.mesh_resolution)
        self.assertEqual(level.local_mesh_size, settings.local_mesh_size)
        self.assertEqual(self.widget(settings=settings).mesh_precision.value(), 6)

    def test_results_stay_single_line_with_full_comparison_in_tooltip(self):
        widget = self.widget()
        widget.start()
        self.finish(widget)
        for col in range(widget.results.columnCount()):
            self.assertNotIn("\n", widget.results.horizontalHeaderItem(col).text())
            self.assertNotIn("\n", widget.results.item(2, col).text())
        self.assertIn("Force:", widget.results.item(2, 6).toolTip())
        self.assertIn("Modulus:", widget.results.item(2, 6).toolTip())
        self.assertIn("SEA:", widget.results.item(2, 6).toolTip())
        self.assertEqual(widget.results.rowHeight(2), 25)

    def test_narrow_window_scrolls_toolbar_without_widening_window(self):
        widget = self.widget()
        widget.resize(650, 350)
        widget.show()
        self.app.processEvents()
        self.assertEqual(widget.width(), 650)
        self.assertGreater(widget.toolbar_scroll.horizontalScrollBar().maximum(), 0)
        widget.resize(widget.toolbar.minimumSizeHint().width() + 50, 350)
        self.app.processEvents()
        self.assertEqual(widget.toolbar_scroll.horizontalScrollBar().maximum(), 0)
        self.assertEqual(widget.toolbar_scroll.height(), widget.toolbar.sizeHint().height())

    def test_unavailable_solver_cannot_launch_a_study(self):
        widget = self.widget()
        for solver_id in ("openfoam",):
            widget.solver_combo.setCurrentIndex(widget.solver_combo.findData(solver_id))
            self.assertFalse(widget.run_button.isEnabled())
            self.assertIn("disabled", widget.solver_combo.toolTip())
            with patch("mesh_convergence.QMessageBox.warning") as warning:
                widget.start()
            warning.assert_called_once()
            self.assertIsNone(widget.future)
            self.assertEqual(widget.rows, [])

    def test_selected_solver_reaches_every_level_and_is_locked_during_run(self):
        from solver_api import SOLVER_REGISTRY
        received = []
        def solver(settings, mesh):
            received.append(settings.solver_backend)
            return self.solver(settings, mesh)
        with patch.dict(SOLVER_REGISTRY, {"kratos": replace(SOLVER_REGISTRY["kratos"], implemented=True)}):
            widget = self.widget(solver=solver)
            widget.solver_combo.setCurrentIndex(widget.solver_combo.findData("kratos"))
            widget.start()
            self.assertFalse(widget.solver_combo.isEnabled())
            self.finish(widget)
            self.assertEqual(received, ["kratos"] * 3)
            self.assertTrue(widget.solver_combo.isEnabled())

    def test_dialog_restores_window_size_and_position(self):
        first = MeshConvergenceDialog(SETTINGS, self.mesh, self.solver)
        self.addCleanup(first.deleteLater)
        self.widgets.append(first.study)
        first.show()
        first.resize(min(1150, self.app.primaryScreen().availableGeometry().width() - 50), 540)
        first.move(35, 45)
        self.app.processEvents()
        saved_size, saved_position = first.size(), first.pos()
        first.reject()
        second = MeshConvergenceDialog(SETTINGS, self.mesh, self.solver)
        self.addCleanup(second.deleteLater)
        self.widgets.append(second.study)
        second.show()
        self.app.processEvents()
        self.assertEqual(second.size(), saved_size)
        self.assertEqual(second.pos(), saved_position)
        second.reject()

    def test_dialog_restores_maximized_state_and_normal_bounds(self):
        first = MeshConvergenceDialog(SETTINGS, self.mesh, self.solver)
        self.addCleanup(first.deleteLater)
        self.widgets.append(first.study)
        first.resize(650, 350)
        first.show()
        self.app.processEvents()
        normal_size = first.size()
        first.showMaximized()
        self.app.processEvents()
        first.reject()
        second = MeshConvergenceDialog(SETTINGS, self.mesh, self.solver)
        self.addCleanup(second.deleteLater)
        self.widgets.append(second.study)
        second.show()
        self.app.processEvents()
        self.assertTrue(second.isMaximized())
        second.showNormal()
        self.app.processEvents()
        self.assertEqual(second.size(), normal_size)
        second.reject()

    def test_export_contains_timings_and_reference(self):
        widget = self.widget()
        widget.start()
        self.finish(widget)
        destination = self.folder / "study.csv"
        with patch("mesh_convergence.QFileDialog.getSaveFileName", return_value=(str(destination), "")):
            widget.export()
        with destination.open(encoding="utf-8-sig", newline="") as stream:
            records = list(csv.DictReader(stream))
        self.assertEqual(len(records), 3)
        self.assertTrue(records[-1]["Mesh (s)"])
        self.assertEqual(records[-1]["Reference level"], "2")

    def test_apply_returns_verified_mesh_and_closes_dialog(self):
        dialog = MeshConvergenceDialog(SETTINGS, self.mesh, self.solver)
        self.widgets.append(dialog.study)
        dialog.study.start()
        self.finish(dialog.study)
        dialog.study.apply_mesh()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.selected_settings, dialog.study.recommended.settings)
        dialog.deleteLater()

    def test_real_timer_delivers_updates_on_event_loop(self):
        widget = self.widget()
        widget.start()
        for _ in range(200):
            if widget.future is None:
                break
            QTest.qWait(10)
        self.assertIsNone(widget.future)
        self.assertIn("Converged", widget.status.text())


if __name__ == "__main__":
    unittest.main()
