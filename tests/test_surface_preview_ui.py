"""Workspace requests a volume mesh only for an explicit FE/analysis action."""
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from material_catalog import builtin_materials
from mesh_engine import build_preview
from model import Settings
from nonlinear_compression import RustMechDialog, _build_fft_preview
from preview_viewport import MESH_PREVIEW_ACTOR
from solver_profiles import SolverProfileStore


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

    def test_geometry_button_hides_excluded_mesh_fragments(self):
        preview = build_preview(self.settings, self.window.preview, include_mesh=True)
        fragment = preview.mesh.grid.extract_cells([0])
        mesh = replace(preview.mesh, removed_grid=fragment)
        preview.mesh = mesh
        self.window.preview = preview
        buttons = self.window.display_mode_buttons
        plotter = self.window.plotter
        for mode in (1, 0, 2, 0):
            with self.subTest(mode=mode):
                plotter.reset_mock()
                buttons.button(mode).click()
                plotter.clear.assert_called_once()
                layers = {
                    call.kwargs.get("name"): call
                    for call in plotter.add_mesh.call_args_list
                }
                if mode == 0:
                    self.assertNotIn(MESH_PREVIEW_ACTOR, layers)
                    self.assertNotIn("excluded_fragments", layers)
                    self.assertIs(layers[None].args[0], preview.surface)
                    self.assertEqual(layers[None].kwargs["opacity"], 1)
                    self.assertTrue(self.window.fragment_status.isHidden())
                    self.assertNotIn("RED = removed fragments", self.window.stats.text())
                else:
                    self.assertIn(MESH_PREVIEW_ACTOR, layers)
                    self.assertIs(layers["excluded_fragments"].args[0], fragment)
                    self.assertFalse(self.window.fragment_status.isHidden())
                self.assertIs(self.window.preview.mesh, mesh)

    def test_fft_preview_uses_solver_voxel_coordinates(self):
        solid = np.zeros((8, 8, 8), dtype=bool)
        solid[2, 4, 6] = True
        with patch('nonlinear_compression.sample_periodic_cell', return_value=solid):
            outline, surface, count = _build_fft_preview(self.settings, 8)
        self.assertEqual(count, 1)
        self.assertEqual(surface.n_cells, 6)
        np.testing.assert_allclose(surface.bounds, np.array([2, 3, 4, 5, 6, 7]) / 8)
        np.testing.assert_allclose(outline.bounds, [0, 1] * 3)

    def test_fft_preview_replaces_stale_grid_and_restores_fe_view(self):
        window = self.window
        plotter = window.plotter
        first, second = Future(), Future()
        with patch.object(window._fft_preview_executor, 'submit', side_effect=[first, second]) as submit:
            window._select_settings_method(window._fft_method_index)
            self.assertTrue(window.display_mode_group.isHidden())
            self.assertTrue(window.show_platens_cb.isHidden())
            window.fft_grid.setValue(8)
            window._poll_fft_preview()
            window.fft_grid.setValue(16)
            first.set_result(_build_fft_preview(self.settings, 8))
            window._poll_fft_preview()
            self.assertIsNone(window._fft_preview_result)
            _, settings, grid = submit.call_args.args
            self.assertEqual(settings, window.collect())
            self.assertEqual(grid, 16)
            second.set_result(_build_fft_preview(settings, grid))
            window._poll_fft_preview()
            self.assertIn('16 × 16 × 16', window.fft_preview_summary.text())
            self.assertIn('0.0625 mm', window.fft_preview_summary.text())
            self.assertTrue(any(call.kwargs.get('name') == 'fft_solid_voxels'
                                for call in plotter.add_mesh.call_args_list))
            window.fft_void_ratio.setValue(0.05)
            window._poll_fft_preview()
            self.assertIn('5%', window.fft_preview_summary.text())
            self.assertEqual(submit.call_count, 2)

        plotter.reset_mock()
        window._select_settings_method(0)
        self.assertTrue(window.fft_preview_summary.isHidden())
        self.assertFalse(window.display_mode_group.isHidden())
        self.assertIs(plotter.add_mesh.call_args_list[0].args[0], window.preview.surface)

    def test_fft_material_is_independent_and_used_by_summary_and_solver(self):
        window = self.window
        finite_element = window.material_editor.material_settings()
        window._select_settings_method(window._fft_method_index)
        editor = window.fft_material_editor
        entry = next(entry for entry in builtin_materials() if entry.identifier == 'ti64')
        # Keep the library's shared shortlist untouched by the test.
        with patch.object(editor, 'library_settings'):
            editor.add_library_material(entry.card())
        self.assertEqual(editor.controls['young_modulus'].value(), 110000)
        editor.edit_button.click()
        modulus = editor.controls['young_modulus'].lineEdit()
        window.fft_void_ratio.setValue(0.01)
        window.fft_distribution.setText('Ubuntu')
        window._fft_preview_result = _build_fft_preview(self.settings, window.fft_grid.value())

        with patch('nonlinear_compression.DamaskAnalysisProcess') as process:
            modulus.selectAll()
            QTest.keyClicks(modulus, '125000')
            QTest.keyClick(modulus, Qt.Key.Key_Return)
            process.assert_not_called()
            self.assertIn('1250 MPa', window.fft_preview_summary.text())
            editor.controls['poisson_ratio'].setValue(0.31)
            window.apply()
            assigned = process.call_args.args[0]
            self.assertEqual(assigned.material_name, entry.name)
            self.assertEqual(assigned.material_model, 'linear_elastic')
            self.assertEqual((assigned.young_modulus, assigned.poisson_ratio), (125000, 0.31))
        self.assertEqual(window.material_editor.material_settings(), finite_element)

    def test_fft_material_draft_is_restored_separately_from_finite_element(self):
        with TemporaryDirectory() as directory:
            path = str(Path(directory) / 'preferences.ini')
            store = SolverProfileStore(QSettings(path, QSettings.Format.IniFormat))
            first = RustMechDialog(Settings(material_name='Initial material'), solver_profile_store=store)
            try:
                editor = first.fft_material_editor
                self.assertEqual(editor.name.text(), 'Initial material')
                editor.edit_button.click()
                editor.name.setText('FFT alloy')
                editor.controls['young_modulus'].setValue(123456.789)
                editor.controls['poisson_ratio'].setValue(0.321)
            finally:
                first.close()
            second = RustMechDialog(Settings(material_name='A different FE material'), solver_profile_store=store)
            try:
                material = second.fft_material_editor.material_settings()
                self.assertEqual(material.material_name, 'FFT alloy')
                self.assertEqual((material.young_modulus, material.poisson_ratio), (123456.789, 0.321))
                self.assertEqual(second.material_editor.name.text(), 'A different FE material')
            finally:
                second.close()

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
