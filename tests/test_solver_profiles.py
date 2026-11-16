"""Solver-specific drafts, sidebar behavior and safe separation from execution."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QWidget

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
        self.assertEqual(len(clicks), 0)
        self.assertFalse(dialog.truba_dialog.isVisible())
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
        errors = replace(KRATOS_CAPABILITIES, implemented=True).validate(replace(dialog.collect(), mesh_type="TET10"))
        self.assertTrue(any("HEX8" in error for error in errors))

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
        self.assertEqual(len(clicks),0)
        self.assertFalse(dialog.truba_dialog.isVisible())
        self.select(dialog,'ansys_mapdl')
        self.assertEqual(dialog.collect(),ansys)


if __name__ == "__main__":
    unittest.main()
