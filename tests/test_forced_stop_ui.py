"""Stop actions cancel live jobs without accepting/closing the workspace."""
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from model import Settings
from nonlinear_compression import RustMechDialog


class ForcedStopUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch.object(RustMechDialog, "start_preview", lambda self: None):
            self.dialog = RustMechDialog(Settings())

    def tearDown(self):
        self.dialog.dispose()
        self.dialog.close()

    def test_single_analysis_keeps_dialog_open_and_can_be_killed(self):
        dialog = self.dialog
        dialog.preview = SimpleNamespace(mesh=object())
        accepted = QSignalSpy(dialog.accepted)
        job = MagicMock()
        with patch.object(dialog, "refresh_validity"), patch(
            "nonlinear_compression.save_settings"
        ), patch("nonlinear_compression.AnalysisProcess", return_value=job):
            dialog.apply_button.setEnabled(True)
            dialog.apply()
            self.assertEqual(accepted.count(), 0)
            self.assertTrue(dialog.force_stop_button.isEnabled())
            self.assertFalse(dialog.apply_button.isEnabled())
            dialog.force_stop_button.click()
            job.force_stop.assert_called_once()
            self.assertIsNone(dialog.analysis_job)
            self.assertFalse(dialog.analysis_poll.isActive())
            self.assertIn("cancelled", dialog.status.text())
        dialog.preview = None

    def test_batch_stop_kills_current_job_and_clears_remaining_queue(self):
        study = self.dialog.doe_studio
        study.add_design(Settings())
        study.add_blank_row()
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
