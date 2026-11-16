"""DoE sonuclari: CSV gidis-donusu, atomik yazma, oturum dosyalari, kurtarma plani."""
import os
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

from doe_plan import read_plan
from doe_studio import (INTERRUPTED_STATUS, RESULTS_ENV, DoEStudioWidget, DoEStudioWindow,
                        results_directory)
from model import Settings
from studio_style import STUDIO_STYLESHEET
from tests import test_doe_window as helpers


def pending_rows(studio):
    return [i for i, row in enumerate(studio.rows)
            if row.enabled and not row.status.lower().startswith("completed")]


class DoEResultsPersistenceTests(unittest.TestCase):
    setUpClass = classmethod(helpers.DoEWindowTests.setUpClass.__func__)
    tearDown = helpers.DoEWindowTests.tearDown
    window = helpers.DoEWindowTests.window

    def setUp(self):
        helpers.DoEWindowTests.setUp(self)
        self.results = Path(self.temp.name) / "results"
        patcher = patch.dict(os.environ, {RESULTS_ENV: str(self.results)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def one_row_studio(self, runner=None):
        if runner is None:
            _, studio = self.window()
        else:
            studio = DoEStudioWidget(Settings, lambda _: None, runner)
            window = DoEStudioWindow(studio)
            window.setStyleSheet(STUDIO_STYLESHEET)
            self.windows.append(window)
            window.open_workspace()
            self.app.processEvents()
        studio.add_design(Settings())
        studio.generate_table()
        self.assertEqual(len(studio.rows), 1)
        return studio

    @staticmethod
    def complete(row):
        row.status = "Completed"
        row.actual_density = 31.2
        row.hex8_elements = 8040
        row.max_force_n = 123.0
        row.energy_absorption_j = 4.5
        row.effective_modulus_mpa = 987.6543
        row.run_folder = "X:/run1"

    def test_csv_round_trip_keeps_results_and_completed_rows_are_skipped(self):
        studio = self.one_row_studio()
        self.complete(studio.rows[0])
        path = Path(self.temp.name) / "table.csv"
        studio.export_csv_path(path)
        studio.import_csv_path(path)
        row = studio.rows[0]
        self.assertEqual(row.status, "Completed")
        self.assertEqual((row.actual_density, row.hex8_elements), (31.2, 8040))
        self.assertIsInstance(row.hex8_elements, int)
        self.assertEqual((row.max_force_n, row.energy_absorption_j), (123.0, 4.5))
        self.assertAlmostEqual(row.effective_modulus_mpa, 987.6543)
        self.assertEqual(row.run_folder, "X:/run1")
        self.assertEqual(pending_rows(studio), [])

    def test_unfinished_row_stays_pending_after_round_trip(self):
        studio = self.one_row_studio()
        path = Path(self.temp.name) / "table.csv"
        studio.export_csv_path(path)
        studio.import_csv_path(path)
        self.assertEqual(studio.rows[0].status, "Pending")
        self.assertIsNone(studio.rows[0].max_force_n)
        self.assertEqual(pending_rows(studio), [0])

    def test_rows_exported_while_running_come_back_interrupted_and_disabled(self):
        for status in ("Checking...", "Solving (ANSYS)...", "TRUBA · solving"):
            studio = self.one_row_studio()
            studio.rows[0].status = status
            path = Path(self.temp.name) / "running.csv"
            studio.export_csv_path(path)
            studio.import_csv_path(path)
            self.assertEqual(studio.rows[0].status, INTERRUPTED_STATUS, status)
            self.assertFalse(studio.rows[0].enabled)
            self.assertEqual(pending_rows(studio), [])
            for window in self.windows:
                window.close()
            self.windows.clear()

    def test_saved_plan_with_running_row_opens_as_interrupted(self):
        studio = self.one_row_studio()
        studio.rows[0].status = "TRUBA · solving"
        plan = Path(self.temp.name) / "plan.rustmech-doe.json"
        studio.save_plan_path(plan)
        studio.open_plan_path(plan)
        self.assertEqual(studio.rows[0].status, INTERRUPTED_STATUS)
        self.assertFalse(studio.rows[0].enabled)

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

    def test_session_files_use_the_plan_name_and_never_the_master_file(self):
        studio = self.one_row_studio()
        studio._plan_path = Path("My plan.rustmech-doe.json")
        path = studio._new_dataset_path()
        self.assertEqual(path.parent, results_directory())
        self.assertTrue(path.name.startswith("My plan_"))
        self.assertEqual(path.suffix, ".csv")
        self.assertNotEqual(path.name, "doe_master_dataset.csv")

    def test_persist_progress_writes_csv_and_recovery_plan_and_reports_failures(self):
        studio = self.one_row_studio()
        self.complete(studio.rows[0])
        studio._dataset_path = studio._new_dataset_path()
        default = studio.footer_label.text()
        studio._persist_progress()
        self.assertIn("Completed", studio._dataset_path.read_text(encoding="utf-8-sig"))
        recovery = read_plan(studio._dataset_path.with_suffix(".rustmech-doe.json"))
        self.assertEqual(recovery["matrices"]["doe"][0].status, "Completed")
        self.assertEqual(recovery["matrices"]["doe"][0].run_folder, "X:/run1")
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
        dataset = studio._dataset_path
        self.assertEqual(dataset.parent, self.results)
        text = dataset.read_text(encoding="utf-8-sig")
        self.assertIn("321.0000", text)
        self.assertIn("X:/run-live", text)
        recovery = read_plan(dataset.with_suffix(".rustmech-doe.json"))
        self.assertEqual(recovery["matrices"]["doe"][0].max_force_n, 321.0)
        self.assertFalse((Path(self.temp.name) / "doe_master_dataset.csv").exists())


if __name__ == "__main__":
    unittest.main()
