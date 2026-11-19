"""Progress follows operation lifetimes and receives real child-process stages."""
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QWidget

from analysis_process import AnalysisProcess, report_analysis_step
from operation_progress import OperationProgress


def staged_operation(release_path):
    report_analysis_step("Exporting result fields…", 5, 6)
    while not Path(release_path).exists():
        time.sleep(.01)
    return "completed"


class OperationProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = QDialog()
        self.layout = QVBoxLayout(self.window)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(predicate())

    def test_independent_operations_follow_steps_and_completion(self):
        states = [[False, "Ready"], [False, "Ready"]]
        footers = [OperationProgress(self.window, lambda i=i: states[i]) for i in range(2)]
        for footer in footers:
            self.layout.addWidget(footer)
        states[0] = [True, "Building mesh…"]
        states[1] = [True, "Solving…"]
        self.wait_for(lambda: not footers[0].isHidden() and not footers[1].isHidden())
        self.assertFalse(footers[0].isHidden())
        self.assertEqual(footers[0].bar.maximum(), 0)
        states[0] = [False, "Cancelled"]
        states[1] = [True, "Exporting results…"]
        self.wait_for(lambda: footers[0].isHidden() and "Exporting results" in footers[1].label.text())
        self.assertTrue(footers[0].isHidden())
        self.assertFalse(footers[1].isHidden())
        self.assertIn("Exporting results", footers[1].label.text())
        states[1] = [False, "Failed"]
        self.wait_for(footers[1].isHidden)
        self.assertTrue(footers[1].isHidden())

    def test_counted_batch_then_unknown_duration_and_restarted_bar(self):
        footer = OperationProgress(self.window)
        self.layout.addWidget(footer)
        footer.update_step(True, "Experiment 2 · Solving", 1, 3)
        self.assertEqual((footer.bar.value(), footer.bar.maximum()), (1, 3))
        self.assertEqual(footer.bar.text(), "33%")
        footer.update_step(False, "Completed")
        footer.bar.hide()  # Existing mesh lifecycle hides its bar on completion.
        footer.update_step(True, "Starting analysis…")
        self.assertFalse(footer.bar.isHidden())
        self.assertEqual(footer.bar.maximum(), 0)
        self.assertFalse(footer.bar.isTextVisible())

    def test_long_step_does_not_force_window_width_and_remains_readable(self):
        footer = OperationProgress(self.window)
        self.layout.addWidget(footer)
        message = "<step> " + "Exporting geometry " * 100
        footer.update_step(True, message)
        self.assertLess(footer.minimumSizeHint().width(), 400)
        self.assertEqual(footer.label.toolTip(), message)
        self.assertEqual(footer.label.accessibleName(), message)

    def test_synchronous_failure_restores_background_step_without_changing_layout(self):
        footer = OperationProgress(self.window, lambda: (True, "Building mesh…"))
        self.layout.addWidget(footer)
        self.window.resize(600, 180)
        self.window.show()
        self.app.processEvents()
        bounds = self.window.geometry()
        with self.assertRaises(ValueError):
            with footer.step("Reading imported geometry…"):
                self.assertIn("Reading imported geometry", footer.label.text())
                raise ValueError("invalid file")
        self.assertEqual(footer.label.text(), "Building mesh…")
        self.assertEqual(self.window.geometry(), bounds)

    def test_real_analysis_process_publishes_stage_before_result(self):
        with TemporaryDirectory() as temporary:
            release = Path(temporary) / "release"
            job = AnalysisProcess(staged_operation, str(release))
            try:
                deadline = time.monotonic() + 20
                stage = None
                while time.monotonic() < deadline:
                    stage = job.progress_status()
                    if stage == "Exporting result fields…":
                        break
                    QTest.qWait(20)
                self.assertEqual(stage, "Exporting result fields…")
                self.assertEqual(job.progress_status(with_counts=True), (stage, 5, 6))
                self.assertFalse(job.done())
                release.touch()
                while not job.done() and time.monotonic() < deadline:
                    QTest.qWait(20)
                self.assertTrue(job.done())
                self.assertEqual(job.result(), "completed")
                self.assertIsNone(job.progress_status())
            finally:
                if not release.exists():
                    release.touch()
                    job.force_stop()

    def test_flow_and_thermal_show_actual_stage_then_clear_after_cancel_or_failure(self):
        from flow_workspace import FlowWorkspace
        from thermal_workspace import ThermalWorkspace
        from model import Settings
        with TemporaryDirectory() as temporary:
            preferences = QSettings(str(Path(temporary) / 'ui.ini'), QSettings.Format.IniFormat)
            for workspace_type in (FlowWorkspace, ThermalWorkspace):
                with self.subTest(workspace=workspace_type.__name__):
                    workspace = workspace_type(Settings, self.window, preferences,
                        viewport_factory=lambda parent: MagicMock(interactor=QWidget(parent)))
                    self.layout.addWidget(workspace)
                    workspace.case_directory = Path(temporary) / 'case'
                    workspace.submitted_flow = workspace.collect()
                    job = workspace.job = MagicMock()
                    job.done.return_value = False
                    job.progress_status.return_value = 'Exporting result fields…'
                    workspace.poll()
                    workspace.operation_progress.refresh()
                    self.assertEqual(workspace.operation_progress.label.text(), 'Exporting result fields…')
                    self.assertFalse(workspace.operation_progress.isHidden())
                    with patch('flow_workspace.stop_flow_case'):
                        workspace.cancel()
                    workspace.operation_progress.refresh()
                    self.assertTrue(workspace.operation_progress.isHidden())
                    job.force_stop.assert_called_once()
                    workspace.job = job
                    job.done.return_value = True
                    job.result.side_effect = RuntimeError('solver failure')
                    workspace.poll()
                    workspace.operation_progress.refresh()
                    self.assertTrue(workspace.operation_progress.isHidden())
                    self.assertIn('solver failure', workspace.status.text())
                    workspace.deleteLater()


if __name__ == "__main__":
    unittest.main()
