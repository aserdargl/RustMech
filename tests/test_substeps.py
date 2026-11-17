"""Automatic substep limits, saved settings, solver handoff and compact UI."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication, QWidget
from model import Settings, load_settings, save_settings, settings_from_dict, validate_settings
import mapdl_backend


class SubstepSettingsTests(unittest.TestCase):
    def test_legacy_limits_and_explicit_limits(self):
        self.assertEqual(Settings().substep_limits, (40, 4000))
        old = settings_from_dict({'substeps': 80})
        self.assertEqual(old.substep_limits, (80, 8000))
        self.assertEqual(validate_settings(old), [])
        explicit = replace(old, minimum_substeps=10, maximum_substeps=2000)
        self.assertEqual(explicit.substep_limits, (10, 2000))
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            save_settings(explicit, path)
            self.assertEqual(load_settings(path), explicit)

    def test_invalid_limits_cannot_be_saved(self):
        for changes in ({'minimum_substeps': 0}, {'maximum_substeps': True},
                        {'maximum_substeps': 10}, {'minimum_substeps': 50},
                        {'minimum_substeps': '10'}, {'substeps': None}):
            with self.subTest(changes=changes):
                value = replace(Settings(), **changes)
                self.assertTrue(validate_settings(value))
                with TemporaryDirectory() as folder:
                    with self.assertRaises(ValueError):
                        save_settings(value, Path(folder) / 'invalid.json')

    def test_mapdl_uses_automatic_stepping_and_saved_limits(self):
        for initial, minimum, maximum, expected in ((40, None, None, (40, 4000, 40)),
                                                    (80, None, None, (80, 8000, 80)),
                                                    (50, 10, 2500, (50, 2500, 10))):
            with self.subTest(initial=initial, minimum=minimum):
                settings = replace(Settings(), substeps=initial, minimum_substeps=minimum,
                                   maximum_substeps=maximum, self_contact_mode='disabled',
                                   material_model='linear_elastic')
                mapdl = MagicMock()
                with patch.object(mapdl_backend, 'select_specimen_boundaries'), \
                     patch.object(mapdl_backend, 'create_platen_contacts', return_value=(1, 2)), \
                     patch.object(mapdl_backend, 'stabilize_in_plane'):
                    mapdl_backend.prepare_ansys(mapdl, settings, object())
                mapdl.autots.assert_called_once_with('ON')
                mapdl.nsubst.assert_called_once_with(*expected)


class SubstepUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import nonlinear_compression as studio
        self.plotter = MagicMock(interactor=QWidget())
        self.viewport_patch = patch.object(studio, 'create_preview_viewport', return_value=self.plotter)
        self.preview_patch = patch.object(studio.RustMechDialog, 'start_preview', new=lambda _self: None)
        self.viewport_patch.start()
        self.preview_patch.start()
        self.dialog = studio.RustMechDialog(Settings())

    def tearDown(self):
        self.dialog.dispose()
        self.dialog.deleteLater()
        self.app.processEvents()
        self.preview_patch.stop()
        self.viewport_patch.stop()

    def test_default_hides_numeric_controls_inside_advanced(self):
        dialog = self.dialog
        self.assertEqual(dialog.substeps_status.text(), 'Automatic')
        self.assertTrue(dialog.substeps_advanced.isHidden())
        self.assertEqual(dialog.substeps_mode_combo.currentText(), "Automatic")
        for name in ('substeps', 'minimum_substeps', 'maximum_substeps'):
            self.assertTrue(dialog.substeps_advanced.isAncestorOf(dialog.controls[name]))
        self.assertEqual(dialog.collect().substep_limits, (40, 4000))
        dialog.substeps_mode_combo.setCurrentIndex(1)
        self.assertFalse(dialog.substeps_advanced.isHidden())
        dialog.controls['minimum_substeps'].setValue(10)
        dialog.controls['substeps'].setValue(50)
        dialog.controls['maximum_substeps'].setValue(2000)
        dialog.substeps_mode_combo.setCurrentIndex(0)
        self.assertTrue(dialog.substeps_advanced.isHidden())
        self.assertEqual(dialog.collect().substeps, Settings().substeps)
        self.assertEqual(dialog.collect().substep_limits, Settings().substep_limits)
        dialog.substeps_mode_combo.setCurrentIndex(1)
        self.assertEqual(dialog.collect().substeps, 50)
        self.assertEqual(dialog.collect().substep_limits, (10, 2000))
        self.assertEqual(validate_settings(dialog.collect()), [])
        # Solver profiles restore custom stepping instead of silently using defaults.
        custom = replace(Settings(), substeps=60, minimum_substeps=12, maximum_substeps=2400)
        dialog._apply_solver_profile(custom)
        self.assertEqual(dialog.substeps_mode_combo.currentText(), "Advanced")
        self.assertEqual(dialog.collect().substeps, 60)
        self.assertEqual(dialog.collect().substep_limits, (12, 2400))
        dialog._apply_solver_profile(Settings())
        self.assertEqual(dialog.substeps_mode_combo.currentText(), "Automatic")
        self.assertTrue(dialog.substeps_advanced.isHidden())


if __name__ == '__main__':
    unittest.main()
