"""Stop actions cancel live jobs without accepting/closing the workspace."""
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QWidget

from model import Settings
from nonlinear_compression import RustMechDialog


class ForcedStopUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        picker = patch("nonlinear_compression.choose_analysis_results_folder", return_value=Path("Selected results"))
        self.pick_results = picker.start()
        self.addCleanup(picker.stop)
        with patch.object(RustMechDialog, "start_preview", lambda self: None), patch(
            "nonlinear_compression.create_preview_viewport",
            side_effect=lambda parent, **kwargs: MagicMock(interactor=QWidget(parent)),
        ):
            self.dialog = RustMechDialog(Settings())

    def tearDown(self):
        self.dialog.dispose()
        self.dialog.close()

    def test_single_analysis_keeps_dialog_open_and_can_be_killed(self):
        dialog = self.dialog
        dialog.preview = SimpleNamespace(mesh=object())
        accepted = QSignalSpy(dialog.accepted)
        job = MagicMock()
        job.is_remote = False
        job.done.return_value = False
        job.remote_status.return_value = None
        job.progress_status.return_value = None
        with patch.object(dialog, "refresh_validity"), patch(
            "nonlinear_compression.save_settings"
        ), patch("nonlinear_compression.AnalysisProcess", return_value=job):
            dialog.apply_button.setEnabled(True)
            dialog.apply()
            self.assertEqual(accepted.count(), 0)
            self.assertTrue(dialog.force_stop_button.isEnabled())
            self.assertFalse(dialog.apply_button.isEnabled())
            progress = dialog.local_analysis_progress
            footer = dialog.status_banner.layout()
            self.assertIs(footer.itemAt(0).widget(), dialog.result_button)
            self.assertIs(footer.itemAt(1).widget(), dialog.apply_button)
            self.assertIs(footer.itemAt(footer.count() - 2).widget(), dialog._analysis_action_buttons[0])
            self.assertEqual(footer.itemAt(footer.count() - 1).widget().objectName(), "unitBadge")
            self.assertFalse(progress.isHidden())
            self.assertEqual(progress.bar.maximum(), 0)

            job.progress_status.return_value = ("Exporting result fields…", 5, 6)
            dialog._poll_analysis()
            self.assertEqual(progress.label.text(), "Exporting result fields…")
            self.assertEqual(progress.bar.text(), "83%")
            dialog.operation_progress.refresh()
            self.assertTrue(dialog.operation_progress.isHidden())

            dialog.force_stop_button.click()
            job.force_stop.assert_called_once()
            self.assertIsNone(dialog.analysis_job)
            self.assertFalse(dialog.analysis_poll.isActive())
            self.assertIn("cancelled", dialog.status.text())
            self.assertTrue(progress.isHidden())
        dialog.preview = None

    def test_local_progress_tracks_automatic_meshing_and_excludes_remote_jobs(self):
        dialog = self.dialog
        dialog.preview = SimpleNamespace(mesh=None)
        dialog.status.setText("Building FE mesh in background…")
        with patch.object(dialog, "refresh_validity"), patch.object(dialog, "start_preview"):
            dialog.apply_button.setEnabled(True)
            dialog.apply()
        self.assertIsNotNone(dialog._analysis_after_mesh)
        dialog.future = MagicMock()
        progress = dialog.local_analysis_progress
        progress.refresh()
        self.assertFalse(progress.isHidden())
        self.assertEqual(progress.label.text(), "Building FE mesh in background…")

        dialog.future = None
        dialog._analysis_after_mesh = None
        job = dialog.analysis_job = MagicMock(is_remote=True)
        job.remote_status.return_value = {"job_id": "123", "state": "running"}
        dialog._show_analysis_progress(job)
        self.assertTrue(progress.isHidden())
        dialog.operation_progress.refresh()
        self.assertFalse(dialog.operation_progress.isHidden())
        dialog.analysis_job = None
        dialog.preview = None

    def test_cancel_folder_picker_does_not_start_meshing_or_analysis(self):
        dialog = self.dialog
        dialog.preview = SimpleNamespace(mesh=None)
        self.pick_results.return_value = None
        with patch.object(dialog, "refresh_validity"), patch.object(dialog, "start_preview") as mesh, patch(
            "nonlinear_compression.AnalysisProcess"
        ) as start:
            dialog.apply_button.setEnabled(True)
            dialog.apply()
        self.pick_results.assert_called_once_with(dialog)
        mesh.assert_not_called()
        start.assert_not_called()
        self.assertIsNone(dialog._analysis_after_mesh)
        dialog.preview = None

    def test_local_progress_hides_after_success_or_failure(self):
        dialog = self.dialog
        for failure in (None, RuntimeError("solver failure")):
            with self.subTest(failure=failure):
                job = dialog.analysis_job = MagicMock(is_remote=False)
                job.result.side_effect = failure
                job.result.return_value = "results"
                dialog.local_analysis_progress.refresh()
                self.assertFalse(dialog.local_analysis_progress.isHidden())
                with patch.object(dialog, "_show_completed_analysis") as completed:
                    dialog._poll_analysis()
                self.assertEqual(completed.called, failure is None)
                self.assertTrue(dialog.local_analysis_progress.isHidden())

    def test_batch_stop_kills_current_job_and_clears_remaining_queue(self):
        import tempfile
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with patch.dict("os.environ", RUSTMECH_ANALYSIS_QUEUE=str(Path(directory.name) / "queue.json")):
            self.dialog._ensure_analysis_queue()
        study = self.dialog.analysis_queue_widget
        study.add_snapshot(Settings())
        job = MagicMock()
        with patch("doe_studio.AnalysisProcess", return_value=job):
            study.start_batch_solve()
            self.assertIs(study._active_future, job)
            study.stop_batch_btn.click()
        job.force_stop.assert_called_once()
        self.assertIsNone(study._active_future)
        self.assertFalse(study._queue)
        self.assertIn("Cancelled", study.rows[0].status)
        self.assertIsNone(study._active_mode)


if __name__ == "__main__":
    unittest.main()
