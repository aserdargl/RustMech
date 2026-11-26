"""DoE Run denetimi: durum siniflari, kapsam, ayar dondurma, hata politikasi, zaman asimi, sureler."""
import dataclasses
import os
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

from doe_studio import (POLICY_STOP_AFTER, RESULTS_ENV, RUN_SCOPE_FAILED, RUN_SCOPE_PARTIAL,
                        STATE_CANCELLED, STATE_COMPLETED, STATE_DISCONNECTED, STATE_FAILED,
                        STATE_INTERRUPTED, STATE_INVALID, STATE_PARTIAL, STATE_PENDING,
                        STATE_RUNNING, TIMEOUT_STATUS, DoEStudioWidget, DeepLearningStudioWindow,
                        classify_status)
from model import Settings
from studio_style import STUDIO_STYLESHEET
from tests import test_doe_window as helpers


class Hanging:
    """A solve that never finishes; remembers that it was force-stopped."""

    def __init__(self):
        self.stopped = False

    def done(self):
        return False

    def force_stop(self):
        self.stopped = True


def scripted(outcomes, calls):
    """AnalysisProcess stand-in: each call consumes one outcome (dict, Exception or Hanging)."""
    def process(target, index, snapshot, *args, **kwargs):
        calls.append((index, snapshot))
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, Hanging):
            return outcome
        future = Future()
        future.set_result(dict(r_idx=index, **outcome))
        return future
    return process


class DoERunControlTests(unittest.TestCase):
    setUpClass = classmethod(helpers.DoEWindowTests.setUpClass.__func__)
    tearDown = helpers.DoEWindowTests.tearDown

    def setUp(self):
        helpers.DoEWindowTests.setUp(self)
        patcher = patch.dict(os.environ, {RESULTS_ENV: str(Path(self.temp.name) / "results")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def studio(self, rows=3, provider=Settings):
        studio = DoEStudioWidget(provider, lambda _: None, lambda settings, mesh: {})
        window = DeepLearningStudioWindow(studio)
        window.setStyleSheet(STUDIO_STYLESHEET)
        self.windows.append(window)
        window.open_workspace()
        self.app.processEvents()
        studio.add_design(Settings())
        studio.generate_ml_table()
        first = studio._rows[0]
        for number in range(2, rows + 1):
            studio._rows.append(dataclasses.replace(first, exp_id=f"EXP-{number:04d}"))
        studio._refresh_table_view()
        self.assertEqual(len(studio._rows), rows)
        return studio

    def run_queue(self, studio, outcomes):
        calls = []
        with patch("doe_studio.AnalysisProcess", side_effect=scripted(outcomes, calls)):
            studio.start_batch_solve()
            for _ in range(60):
                if studio._active_mode is None:
                    break
                studio._poll_worker()
                time.sleep(0.002)
        self.assertIsNone(studio._active_mode, "queue did not finish")
        return calls

    def statuses(self, studio):
        return [row.status for row in studio._rows]

    def test_status_text_maps_to_one_state(self):
        cases = {
            "Pending": STATE_PENDING, "": STATE_PENDING, "Ready - 8040 elements": STATE_PENDING,
            "Checking...": STATE_RUNNING, "Solving (ANSYS)...": STATE_RUNNING,
            "TRUBA · solving": STATE_RUNNING, "Completed": STATE_COMPLETED,
            "completed (3 warnings)": STATE_COMPLETED, "partial_solution": STATE_PARTIAL,
            "Failed: boom": STATE_FAILED, "Failed to start: x": STATE_FAILED,
            TIMEOUT_STATUS: STATE_FAILED, "Invalid: size": STATE_INVALID,
            "Mesh Disconnected": STATE_DISCONNECTED, "Cancelled (Forced Stop)": STATE_CANCELLED,
            "Interrupted - check solver/job before retry": STATE_INTERRUPTED,
        }
        for text, state in cases.items():
            self.assertEqual(classify_status(text), state, text)

    def test_run_scope_selects_rows_and_never_reruns_completed(self):
        studio = self.studio(rows=5)
        for row, status in zip(studio._rows, ("Completed", "Failed: x", "partial_solution",
                                              "Mesh Disconnected", "Pending")):
            row.status = status
        studio._rows[4].enabled = False
        self.assertEqual(studio.rows_to_run(), [1, 2, 3])
        self.assertEqual(studio.rows_to_run(RUN_SCOPE_FAILED), [1, 3])
        self.assertEqual(studio.rows_to_run(RUN_SCOPE_PARTIAL), [2])

    def test_settings_are_frozen_for_the_whole_run(self):
        live = [Settings(cores=2)]
        studio = self.studio(provider=lambda: live[0])
        studio._begin_run()
        live[0] = Settings(cores=16)
        self.assertEqual(studio._run_base().cores, 2)
        studio._frozen_base = None
        self.assertEqual(studio._run_base().cores, 16)

    def test_run_releases_the_frozen_settings_when_it_ends(self):
        studio = self.studio(rows=1)
        self.run_queue(studio, [dict(status="Completed")])
        self.assertIsNone(studio._frozen_base)

    def test_start_failure_no_longer_drops_the_rest_of_the_queue(self):
        studio = self.studio(rows=3)
        self.run_queue(studio, [RuntimeError("no license"), dict(status="Completed"),
                                dict(status="Completed")])
        self.assertTrue(studio.rows[0].status.startswith("Failed to start"))
        self.assertEqual(self.statuses(studio)[1:], ["Completed", "Completed"])

    def test_stop_after_policy_halts_the_run_and_leaves_the_rest_pending(self):
        studio = self.studio(rows=4)
        studio.failure_policy, studio.max_failures = POLICY_STOP_AFTER, 2
        calls = self.run_queue(studio, [dict(status="Failed: a"), dict(status="Failed: b"),
                                        dict(status="Completed"), dict(status="Completed")])
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.statuses(studio)[2:], ["Pending", "Pending"])
        self.assertIn("failure policy", studio.footer_label.text())

    def test_continue_policy_runs_every_row_despite_failures(self):
        studio = self.studio(rows=3)
        calls = self.run_queue(studio, [dict(status="Failed: a"), dict(status="partial_solution"),
                                        dict(status="Completed")])
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.statuses(studio), ["Failed: a", "partial_solution", "Completed"])

    def test_failed_rows_are_retried_up_to_the_limit(self):
        studio = self.studio(rows=1)
        studio.max_retries = 2
        calls = self.run_queue(studio, [dict(status="Failed: a"), dict(status="Failed: b"),
                                        dict(status="Completed")])
        self.assertEqual(len(calls), 3)
        self.assertEqual((studio.rows[0].status, studio.rows[0].attempts), ("Completed", 2))

    def test_retries_stop_at_the_limit_and_the_row_stays_failed(self):
        studio = self.studio(rows=1)
        studio.max_retries = 1
        calls = self.run_queue(studio, [dict(status="Failed: a"), dict(status="Failed: b")])
        self.assertEqual(len(calls), 2)
        self.assertEqual(studio.rows[0].status, "Failed: b")

    def test_row_timeout_stops_the_stuck_solve_and_continues(self):
        studio = self.studio(rows=2)
        studio.row_timeout_s = 0.02
        stuck = Hanging()
        self.run_queue(studio, [stuck, dict(status="Completed")])
        self.assertTrue(stuck.stopped)
        self.assertEqual(self.statuses(studio), [TIMEOUT_STATUS, "Completed"])

    def test_no_timeout_by_default(self):
        studio = self.studio(rows=1)
        self.assertEqual(studio.row_timeout_s, 0)
        calls = []
        stuck = Hanging()
        with patch("doe_studio.AnalysisProcess", side_effect=scripted([stuck], calls)):
            studio.start_batch_solve()
            time.sleep(0.05)
            studio._poll_worker()
        self.assertFalse(stuck.stopped)
        self.assertEqual(studio.rows[0].status, "Solving (ANSYS)...")
        studio._active_future = None
        studio._queue.clear()
        studio._launch_next_in_queue()

    def test_invalid_rows_are_marked_before_the_run_and_the_rest_still_solve(self):
        studio = self.studio(rows=3)
        studio._rows[1].design_settings = dataclasses.replace(Settings(), applied_force=-5.0)
        calls = self.run_queue(studio, [dict(status="Completed"), dict(status="Completed")])
        self.assertEqual(len(calls), 2)
        self.assertEqual(classify_status(studio.rows[1].status), STATE_INVALID)
        self.assertEqual((studio.rows[0].status, studio.rows[2].status), ("Completed", "Completed"))

    def test_timings_are_recorded_exported_and_restored(self):
        studio = self.studio(rows=1)
        self.run_queue(studio, [dict(status="Completed")])
        row = studio.rows[0]
        self.assertTrue(row.started_at)
        self.assertGreaterEqual(row.duration_s, 0)
        path = Path(self.temp.name) / "timed.csv"
        studio.export_csv_path(path)
        studio.import_csv_path(path)
        again = studio.rows[0]
        self.assertEqual(again.started_at, row.started_at)
        self.assertAlmostEqual(again.duration_s, row.duration_s, places=3)

    def test_saved_plan_keeps_timings_and_attempts(self):
        studio = self.studio(rows=1)
        row = studio.rows[0]
        row.started_at, row.duration_s, row.attempts = "2026-11-16T22:00:00", 12.5, 2
        plan = Path(self.temp.name) / "timed.rustmech-doe.json"
        studio.save_plan_path(plan)
        studio.open_plan_path(plan)
        again = studio.rows[0]
        self.assertEqual((again.started_at, again.duration_s, again.attempts),
                         ("2026-11-16T22:00:00", 12.5, 2))

    def test_eta_uses_the_average_completed_duration(self):
        studio = self.studio(rows=1)
        self.assertEqual(studio._eta_text(), "")
        studio._durations = [60.0, 120.0]
        studio._queue = [0, 0, 0, 0]
        self.assertEqual(studio._eta_text(), "ETA ~6m 00s")
        studio._queue = []


if __name__ == "__main__":
    unittest.main()
