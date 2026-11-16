"""Workspace requests a volume mesh only for an explicit FE/analysis action."""
from concurrent.futures import Future
from threading import Event
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication, QWidget

from mesh_engine import build_preview
from model import Settings
from nonlinear_compression import RustMechDialog


class SurfacePreviewUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.patches = [
            patch('window_state.install_window_state'),
            patch('nonlinear_compression.create_preview_viewport', side_effect=lambda parent, **kw: MagicMock(interactor=QWidget(parent))),
            patch.object(RustMechDialog, 'start_preview', lambda self: None),
        ]
        for item in self.patches:
            item.start()
        self.window = RustMechDialog(Settings(size=1, cell_size=1, compression=.1, mesh_resolution=.1))
        self.settings = self.window.collect()
        self.window.active_geometry = self.settings
        self.window.preview = build_preview(self.settings)
        self.window.mesh_cancelled = Event()
        self.window.refresh_validity()

    def tearDown(self):
        self.window.future = None
        self.window.analysis_job = None
        self.window.dispose()
        self.window.close()
        self.app.processEvents()
        for item in reversed(self.patches):
            item.stop()

    def test_geometry_default_renders_without_fe_mesh_and_explicit_tab_requests_it(self):
        self.assertEqual(self.window.mode.currentIndex(), 0)
        self.window.render_preview()
        self.assertIn('has not been generated', self.window.stats.text())
        self.assertTrue(self.window.apply_button.isEnabled())
        self.window.mode.setCurrentIndex(1)
        self.assertTrue(self.window._mesh_requested)
        self.assertIsNone(self.window.preview.mesh)

    def test_doe_select_ignores_mesh_errors_and_pending_main_preview(self):
        self.window.controls['mesh_resolution'].setValue(2)
        self.window.preview = None
        self.window.refresh_validity()
        self.assertTrue(self.window.doe_studio.use_model_btn.isEnabled())
        self.assertFalse(self.window.mesh_apply_button.isEnabled())
        self.assertFalse(self.window.apply_button.isEnabled())
        with patch('nonlinear_compression.generate_volume_mesh', side_effect=AssertionError('mesh')):
            self.window._add_current_design()
        self.assertEqual(len(self.window.doe_studio._designs), 1)
        self.assertEqual(self.window.doe_studio._designs[0].mesh_resolution, 2)

    def test_analysis_waits_for_mesh_and_forwards_it_without_remeshing(self):
        with patch('nonlinear_compression.AnalysisProcess') as process, patch('nonlinear_compression.save_settings'):
            self.window.apply()
            process.assert_not_called()
            self.assertEqual(self.window._analysis_after_mesh, self.settings)
            full = build_preview(self.settings, self.window.preview, include_mesh=True)
            self.assertFalse(full.mesh.solve_errors)
            future = Future()
            future.set_result(full)
            self.window.future = future
            self.window.worker_key = full.key
            self.window.poll_preview()
            process.assert_called_once()
            self.assertIs(process.call_args.args[2], full.mesh)

    def test_editing_settings_or_cancelling_during_meshing_cancels_pending_solve(self):
        with patch('nonlinear_compression.AnalysisProcess') as process:
            self.window.apply()
            full = build_preview(self.settings, self.window.preview, include_mesh=True)
            self.window.controls['compression'].setValue(.05)
            future = Future()
            future.set_result(full)
            self.window.future = future
            self.window.worker_key = full.key
            self.window.poll_preview()
            process.assert_not_called()
            self.assertIsNone(self.window._analysis_after_mesh)
            self.window._analysis_after_mesh = self.settings
            self.window.future = Future()
            self.window.cancel_meshing()
            self.assertIsNone(self.window._analysis_after_mesh)


if __name__ == '__main__':
    unittest.main()
