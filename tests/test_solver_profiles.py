"""Solver-specific drafts, sidebar behavior and safe separation from execution."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QWidget

from model import Settings, load_settings, save_settings
from nonlinear_compression import RustMechDialog
from solver_api import KRATOS_CAPABILITIES
from solver_profiles import SolverProfileStore


class SolverProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SolverProfileStore(QSettings(str(Path(self.temp.name)/"profiles.ini"), QSettings.Format.IniFormat))

    def dialog(self, settings=Settings()):
        with patch("window_state.install_window_state"), patch("nonlinear_compression.create_preview_viewport", return_value=MagicMock(interactor=QWidget())), patch.object(RustMechDialog, "start_preview", lambda _: None):
            dialog = RustMechDialog(settings, solver_profile_store=self.store)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.dispose)
        return dialog

    def select(self, dialog, solver):
        dialog.solver_combo.setCurrentIndex(dialog.solver_combo.findData(solver))
        self.app.processEvents()

    def test_switch_restores_ansys_draft_and_kratos_specific_controls(self):
        initial = Settings(mesh_type="TET10", cores=3, automatic_cores=False,
                           automatic_contact_parameters=False, normal_contact_stiffness_factor=2.3,
                           top_platen_contact="rough", compression=.4)
        dialog = self.dialog(initial)
        ansys = dialog.collect()
        self.select(dialog, "kratos")
        self.assertFalse(dialog.kratos_panel.isHidden())
        self.assertFalse(dialog._contact_form.isRowVisible(dialog.automatic_contact))
        self.assertFalse(dialog._contact_form.isRowVisible(dialog.controls["normal_contact_stiffness_factor"]))
        self.assertEqual(dialog.collect().mesh_type, "HEX8")
        self.assertEqual(dialog.collect().top_platen_contact, "frictional")
        dialog.kratos_panel.fields["kratos_max_iterations"].setValue(63)
        dialog.controls["compression"].setValue(.7)
        self.select(dialog, "ansys_mapdl")
        self.assertTrue(dialog.kratos_panel.isHidden())
        self.assertEqual(dialog.collect(), ansys)
        self.assertTrue(dialog._contact_form.isRowVisible(dialog.controls["normal_contact_stiffness_factor"]))
        self.select(dialog, "kratos")
        self.assertEqual(dialog.collect().kratos_max_iterations, 63)
        self.assertAlmostEqual(dialog.collect().compression, .7)

    def test_restart_restores_selected_solver_and_both_profiles(self):
        dialog = self.dialog()
        dialog.controls["compression"].setValue(.41)
        self.select(dialog, "kratos")
        dialog.kratos_panel.fields["kratos_relative_tolerance"].setValue(.002)
        dialog.controls["compression"].setValue(.8)
        dialog.dispose()
        settings = self.store.restore(Settings())
        self.assertEqual(settings.solver_backend, "kratos")
        restarted = self.dialog(settings)
        self.assertAlmostEqual(restarted.collect().kratos_relative_tolerance, .002)
        self.assertAlmostEqual(restarted.collect().compression, .8)
        self.select(restarted, "ansys_mapdl")
        self.assertAlmostEqual(restarted.collect().compression, .41)

    def test_close_restores_unsolved_workspace_and_fft_inputs(self):
        dialog = self.dialog()
        dialog.controls["size"].setValue(13.5)
        dialog.controls["cell_size"].setValue(4.75)
        dialog.controls["solid_fraction"].setValue(42)
        dialog.controls["mesh_resolution"].setValue(.4)
        dialog.controls["local_mesh_size"].setValue(.35)
        dialog.controls["mesh_resolution"].setValue(.2)
        dialog.stl_resolution_input.setText("0,07")
        dialog.material_editor.controls["young_modulus"].setValue(180000)
        dialog.fft_grid.setValue(40)
        dialog.fft_void_ratio.setValue(.02)
        dialog.fft_distribution.setText("Ubuntu-RustMech")
        dialog.reject()

        self.store = SolverProfileStore(QSettings(self.store.settings.fileName(), QSettings.Format.IniFormat))
        restarted = self.dialog(self.store.restore_workspace(Path(self.temp.name) / "settings.json"))
        actual = restarted.collect()
        self.assertEqual((actual.size, actual.cell_size, actual.solid_fraction), (13.5, 4.75, 42))
        self.assertEqual((actual.mesh_resolution, actual.local_mesh_size), (.2, .2))
        self.assertAlmostEqual(actual.stl_resolution, .07)
        self.assertEqual(actual.young_modulus, 180000)
        self.assertEqual(restarted.fft_grid.value(), 40)
        self.assertAlmostEqual(restarted.fft_void_ratio.value(), .02)
        self.assertEqual(restarted.fft_distribution.text(), "Ubuntu-RustMech")
        self.assertIsNone(restarted.analysis_job)
        self.assertFalse(restarted.remote_controller.requested)

    def test_workspace_migrates_legacy_settings_and_handles_missing_geometry(self):
        path = Path(self.temp.name) / "settings.json"
        legacy = Settings(size=17, cell_size=5)
        save_settings(legacy, path)
        self.assertEqual(self.store.restore_workspace(path), legacy)

        self.store.save_workspace(replace(legacy, size=23,
            imported_geometry_path=str(path.with_name("removed.stl")), imported_geometry_name="Gone"))
        restored = self.store.restore_workspace(path)
        self.assertEqual(restored.size, 23)
        self.assertEqual(restored.imported_geometry_path, "")
        self.assertEqual(restored.imported_geometry_name, "")

    def test_cube_toggle_applies_and_restores_independent_dimensions(self):
        dialog = self.dialog(Settings(size=4, cell_size=2, stl_resolution=.1))
        x, y, z = (dialog.controls[name] for name in ("size_x", "size_y", "size"))
        self.assertTrue(dialog.cube_button.isChecked())
        self.assertTrue(x.isEnabled())
        self.assertFalse(y.isEnabled())
        self.assertFalse(z.isEnabled())
        self.assertTrue(dialog._geometry_form.isRowVisible(x.parentWidget()))
        self.assertFalse(dialog._geometry_form.isRowVisible(y.parentWidget()))
        self.assertFalse(dialog._geometry_form.isRowVisible(z.parentWidget()))
        x.setValue(5)
        self.assertEqual(dialog.collect().dimensions, (5, 5, 5))

        dialog.cube_button.click()
        self.assertTrue(y.isEnabled())
        self.assertTrue(z.isEnabled())
        self.assertTrue(dialog._geometry_form.isRowVisible(y.parentWidget()))
        self.assertTrue(dialog._geometry_form.isRowVisible(z.parentWidget()))
        y.setValue(3)
        z.setValue(4)
        self.assertEqual(dialog.preview_settings().dimensions, (4, 4, 4))
        dialog.structure_apply_button.click()
        self.assertEqual(dialog.preview_settings().dimensions, (5, 3, 4))
        dialog.dispose()

        restored = self.dialog(self.store.restore_workspace())
        self.assertFalse(restored.cube_button.isChecked())
        self.assertEqual(restored.collect().dimensions, (5, 3, 4))
        self.assertEqual(restored.preview_settings().dimensions, (5, 3, 4))
        restored.cube_button.click()
        self.assertEqual(restored.collect().dimensions, (5, 5, 5))
        self.assertFalse(restored.controls["size"].isEnabled())
        self.assertFalse(restored._geometry_form.isRowVisible(restored.controls["size"].parentWidget()))

    def test_kratos_enter_only_confirms_and_settings_roundtrip(self):
        dialog = self.dialog()
        self.select(dialog, "kratos")
        clicks = QSignalSpy(dialog.apply_button.clicked)
        control = dialog.kratos_panel.fields["kratos_max_iterations"]
        control.setFocus()
        control.lineEdit().selectAll()
        QTest.keyClicks(control.lineEdit(), "52")
        QTest.keyClick(control.lineEdit(), Qt.Key.Key_Return)
        self.assertEqual(control.value(), 52)
        self.assertEqual(clicks.count(), 0)
        self.assertIsNone(dialog.sync_window)
        tolerance = dialog.kratos_panel.fields["kratos_absolute_tolerance"]
        tolerance.setFocus()
        tolerance.lineEdit().selectAll()
        QTest.keyClicks(tolerance.lineEdit(), "2e-8")
        QTest.keyClick(tolerance.lineEdit(), Qt.Key.Key_Return)
        self.assertAlmostEqual(tolerance.value(), 2e-8, places=12)
        self.assertEqual(tolerance.text(), "2e-08")
        path = Path(self.temp.name)/"settings.json"
        save_settings(dialog.collect(), path)
        self.assertEqual(load_settings(path), dialog.collect())
        self.assertTrue(KRATOS_CAPABILITIES.implemented)
        dialog.mesh_type_combo.setCurrentIndex(dialog.mesh_type_combo.findData("TET10"))
        settings = dialog.collect()
        self.assertEqual(settings.kratos_contact_method, "penalty")
        self.assertEqual(KRATOS_CAPABILITIES.validate(settings), [])

    def test_self_contact_selects_penalty_without_starting_and_uses_fixed_increments(self):
        dialog=self.dialog()
        ansys=dialog.collect()
        self.select(dialog,'kratos')
        clicks=QSignalSpy(dialog.apply_button.clicked)
        dialog.self_contact_combo.setCurrentIndex(dialog.self_contact_combo.findData('frictional'))
        self.assertEqual(dialog.collect().kratos_contact_method,'penalty')
        self.assertEqual(dialog.substeps_status.text(),'Fixed increments')
        self.assertIsNone(dialog.collect().maximum_substeps)
        self.assertFalse(dialog.substeps_advanced.layout().isRowVisible(dialog.controls['maximum_substeps']))
        penalty=dialog.kratos_panel.fields['kratos_penalty_factor']
        penalty.lineEdit().selectAll();QTest.keyClicks(penalty.lineEdit(),'25000')
        QTest.keyClick(penalty.lineEdit(),Qt.Key.Key_Return)
        self.assertEqual(penalty.value(),25000.)
        self.assertEqual(clicks.count(),0)
        self.assertIsNone(dialog.sync_window)
        self.select(dialog,'ansys_mapdl')
        self.assertEqual(dialog.collect(),ansys)


if __name__ == "__main__":
    unittest.main()
