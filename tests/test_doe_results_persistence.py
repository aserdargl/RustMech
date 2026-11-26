"""Analysis queue result persistence and atomic export."""
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

from analysis_queue import AnalysisQueueWidget
from model import Settings
from tests import test_doe_window as helpers


class DoEResultsPersistenceTests(unittest.TestCase):
    setUpClass = classmethod(helpers.DoEWindowTests.setUpClass.__func__)
    tearDown = helpers.DoEWindowTests.tearDown
    def setUp(self):
        helpers.DoEWindowTests.setUp(self)
        self.results = Path(self.temp.name)

    def one_row_studio(self, runner=None):
        queue = AnalysisQueueWidget(Settings, lambda _: None, runner,
                                    queue_path=self.results / "queue.json")
        self.addCleanup(queue.dispose)
        queue.add_snapshot(Settings())
        return queue

    @staticmethod
    def complete(row):
        row.status = "Completed"
        row.actual_density = 31.2
        row.hex8_elements = 8040
        row.max_force_n = 123.0
        row.energy_absorption_j = 4.5
        row.effective_modulus_mpa = 987.6543
        row.run_folder = "X:/run1"

    def test_queue_round_trip_keeps_results_and_completed_rows_are_skipped(self):
        studio = self.one_row_studio()
        self.complete(studio.rows[0])
        studio.save_queue()
        studio._rows = studio.store.load()
        row = studio.rows[0]
        self.assertEqual(row.status, "Completed")
        self.assertEqual((row.actual_density, row.hex8_elements), (31.2, 8040))
        self.assertIsInstance(row.hex8_elements, int)
        self.assertEqual((row.max_force_n, row.energy_absorption_j), (123.0, 4.5))
        self.assertAlmostEqual(row.effective_modulus_mpa, 987.6543)
        self.assertEqual(row.run_folder, "X:/run1")
        self.assertEqual(studio.rows_to_run(), [])

    def test_running_row_restores_as_interrupted(self):
        studio = self.one_row_studio()
        for status in ("Checking...", "Solving (ANSYS)...", "TRUBA · solving"):
            studio.rows[0].status = status
            studio.save_queue()
            row = studio.store.load()[0]
            self.assertTrue(row.status.startswith("Interrupted"), status)
            self.assertFalse(row.enabled)

    def test_failed_export_keeps_the_previous_file_and_leaves_no_temporary(self):
        studio = self.one_row_studio()
        path = Path(self.temp.name) / "table.csv"
        studio.export_csv_path(path)
        original = path.read_bytes()

        def broken(target):
            Path(target).write_text("half written", encoding="utf-8")
            raise OSError("disk full")

        with patch.object(studio, "_write_csv", broken):
            with self.assertRaisesRegex(OSError, "disk full"):
                studio.export_csv_path(path)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(sorted(p.name for p in path.parent.glob("table.csv*")), ["table.csv"])

    def test_persist_progress_writes_csv_and_reports_failures(self):
        studio = self.one_row_studio()
        self.complete(studio.rows[0])
        studio._results_path = studio._new_results_path()
        default = studio.footer_label.text()
        studio._persist_progress()
        self.assertIn("Completed", studio._results_path.read_text(encoding="utf-8-sig"))
        self.assertEqual(studio.footer_label.text(), default)
        with patch.object(studio, "export_csv_path", side_effect=OSError("disk full")):
            studio._persist_progress()
        self.assertIn("disk full", studio.footer_label.text())
        self.assertIn("disk full", studio.footer_label.toolTip())
        studio._persist_progress()
        self.assertEqual(studio.footer_label.text(), default)

    def test_each_solved_row_is_persisted_while_the_queue_runs(self):
        studio = self.one_row_studio(runner=lambda settings, mesh: {})

        def process(target, index, snapshot, **kwargs):
            future = Future()
            future.set_result(dict(r_idx=index, status="Completed", max_force_n=321.0,
                                   run_folder="X:/run-live"))
            return future

        with patch("doe_studio.AnalysisProcess", side_effect=process):
            studio.start_batch_solve()
            studio._poll_worker()
        self.assertIsNone(studio._active_mode)
        results = studio._results_path
        self.assertEqual(results.parent, self.results)
        text = results.read_text(encoding="utf-8-sig")
        self.assertIn("321.0000", text)
        self.assertIn("X:/run-live", text)
        self.assertEqual(studio.store.load()[0].max_force_n, 321.0)


if __name__ == "__main__":
    unittest.main()
