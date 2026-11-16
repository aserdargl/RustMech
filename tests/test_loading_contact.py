"""Load targets, contact commands and actual Qt setup propagation."""
from dataclasses import replace
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest, QSignalSpy
from PyQt6.QtWidgets import QApplication

import mapdl_backend as backend
from mesh_convergence import MeshConvergenceDialog, response_metrics, solve_level
from model import Settings, loading_target_reached, save_settings, load_settings, validate_settings
from nonlinear_compression import RustMechDialog, export_graphs, extract_curve


class LoadContactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_persist_load_and_contact_conditions(self):
        settings = replace(Settings(), loading_mode="force", applied_force=234,
                           bottom_platen_contact="rough", top_platen_contact="bonded")
        with TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            save_settings(settings, path)
            self.assertEqual(load_settings(path), settings)
        self.assertTrue(validate_settings(replace(settings, applied_force=float("nan"))))
        self.assertTrue(validate_settings(replace(settings, contact_formulation="mpc")))

    def test_contact_types_and_real_constants(self):
        mapdl = MagicMock()
        settings = replace(Settings(), contact_damping_factor=.025)
        backend.configure_contact(mapdl, settings, 8, 5, "rough")
        mapdl.keyopt.assert_any_call(8, 12, 1)
        mapdl.keyopt.assert_any_call(8, 10, 1)
        mapdl.rmodif.assert_called_once_with(5, 31, .025)
        mapdl.r.assert_called_once_with(5, "", "", 1., .0001)
        mapdl.rmore.assert_not_called()

    def test_adaptive_stiffness_is_retained_for_penalty_and_self_contact(self):
        for formulation, pair in (("penalty", 5), ("augmented_lagrange", 3)):
            with self.subTest(formulation=formulation, pair=pair):
                mapdl = MagicMock()
                settings = replace(Settings(), contact_formulation=formulation)
                backend.configure_contact(mapdl, settings, 8, pair, "frictional")
                mapdl.keyopt.assert_any_call(8, 10, 2)
                tolerance = (settings.platen_penetration_tolerance_factor if pair == 5
                             else settings.penetration_tolerance_factor)
                mapdl.r.assert_called_once_with(pair, "", "", settings.normal_contact_stiffness_factor, tolerance)

    def test_small_displacement_and_force_control_keep_adaptive_platen_stiffness(self):
        for settings in (replace(Settings(), compression=.001, mesh_resolution=.25),
                         replace(Settings(), loading_mode="force")):
            with self.subTest(settings=settings):
                mapdl = MagicMock()
                backend.configure_contact(mapdl, settings, 8, 5, "frictionless")
                mapdl.keyopt.assert_any_call(8, 10, 2)

    def test_force_releases_only_top_uz_and_applies_total_load(self):
        mapdl = MagicMock()
        mesh = SimpleNamespace(grid=SimpleNamespace(point_data={"ansys_node_num": np.array([1, 2])}))
        settings = replace(Settings(), loading_mode="force", applied_force=345,
                           top_platen_contact="rough", bottom_platen_contact="frictionless")
        with patch.object(backend, "create_platen", side_effect=[20, 30]) as platen:
            self.assertEqual(backend.create_platen_contacts(mapdl, settings, mesh), (20, 30))
        mapdl.ddele.assert_called_once_with(30, "UZ")
        mapdl.f.assert_called_once_with(30, "FZ", -345)
        mapdl.keyopt.assert_any_call(8, 12, 1)
        self.assertEqual(platen.call_args_list[0].args[-2], 0.)

    def test_force_curve_uses_load_and_independently_checks_bottom_balance(self):
        reader = MagicMock(nsets=2, time_values=np.array([.5, 1.]))
        reader.result_dof.return_value = ["UX", "UY", "UZ"]
        reader.nodal_reaction_forces.side_effect = [
            (np.array([50.]), np.array([20]), np.array([3])),
            (np.array([100.]), np.array([20]), np.array([3])),
        ]
        result = backend.MapdlResults(reader, (20, 30), settings=replace(Settings(), loading_mode="force"))
        with patch.object(result, "top_platen_displacement", side_effect=[-.001, -.002]):
            history, report = extract_curve(result)
        np.testing.assert_allclose(history[:, 2], [0, 50, 100])
        self.assertFalse(report["force_balance_warning"])
        with TemporaryDirectory() as folder:
            graphs = export_graphs(history, result.settings, Path(folder), images=False)
        self.assertTrue(graphs["completed"])
        self.assertAlmostEqual(graphs["reached_compression_mm"], .002)
        self.assertFalse(loading_target_reached(result.settings, .002, 100, .9))

    def test_main_setup_below_mesh_supplies_convergence_and_enter_only_commits(self):
        with patch("window_state.install_window_state"), patch.object(RustMechDialog, "start_preview", lambda self: None):
            dialog = RustMechDialog(Settings())
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.dispose)
        self.addCleanup(dialog.close)
        dialog.controls["compression"].setValue(.3)
        dialog.controls["bottom_platen_friction_coeff"].setValue(.45)
        combo = dialog.platen_contact_combos["top"]
        combo.setCurrentIndex(combo.findData("rough"))
        # Form controls live in each SidebarGroupBox's content widget.
        mesh_group = dialog.controls["mesh_resolution"].parentWidget().parentWidget()
        loading_group = dialog.loading_mode_combo.parentWidget().parentWidget()
        contact_group = combo.parentWidget().parentWidget()
        fields = mesh_group.parentWidget().layout()
        self.assertIs(loading_group.parentWidget(), mesh_group.parentWidget())
        self.assertIs(contact_group.parentWidget(), mesh_group.parentWidget())
        self.assertEqual(fields.indexOf(loading_group), fields.indexOf(mesh_group) + 1)
        self.assertEqual(fields.indexOf(contact_group), fields.indexOf(dialog.material_editor) + 1)
        self.assertEqual(dialog.setup_tabs.currentIndex(), 0)
        self.assertFalse(dialog.setup_tabs.tabBar().isVisible())
        dialog.show()
        clicks = QSignalSpy(dialog.apply_button.clicked)
        spin = dialog.controls["compression"]
        spin.setFocus()
        spin.lineEdit().selectAll()
        QTest.keyClicks(spin.lineEdit(), "0.42")
        QTest.keyClick(spin.lineEdit(), Qt.Key.Key_Return)
        self.app.processEvents()
        self.assertAlmostEqual(spin.value(), .42)
        self.assertEqual(len(clicks), 0)
        self.assertFalse(dialog.truba_dialog.isVisible())
        selected = dialog.collect()
        with patch("mesh_convergence.MeshConvergenceDialog") as factory:
            factory.return_value.selected_settings = None
            dialog.open_mesh_convergence()
            self.assertEqual(factory.call_args.args[0], selected)
        study_dialog = MeshConvergenceDialog(selected, MagicMock(), MagicMock())
        study = study_dialog.study
        self.addCleanup(study_dialog.deleteLater)
        self.addCleanup(study.dispose)
        level = study.level_settings(.1)
        self.assertEqual(level.compression, .42)
        self.assertEqual(level.bottom_platen_friction_coeff, .45)
        self.assertEqual(level.top_platen_contact, "rough")
        self.assertFalse(hasattr(study, "analysis_setup"))
        force_dialog = MeshConvergenceDialog(replace(selected, loading_mode="force"), MagicMock(), MagicMock())
        self.addCleanup(force_dialog.deleteLater)
        self.addCleanup(force_dialog.study.dispose)
        self.assertEqual(response_metrics(force_dialog.study.settings)[0][1], "reached_compression_mm")
        study = force_dialog.study
        self.assertEqual(study.results.horizontalHeaderItem(3).text(), "Displ. (mm)")

    def test_force_study_rejects_partial_target(self):
        settings = replace(Settings(), loading_mode="force")
        with TemporaryDirectory() as folder:
            graphs = Path(folder) / "04_Graphs"
            graphs.mkdir()
            (graphs / "force_displacement.csv").write_text(
                "Compression_mm,Compression_Force_N\n0,0\n.001,50\n")
            solver = lambda *_: {"status": "completed", "run_folder": folder,
                                 "graphs": {"reached_compression_mm": .001}}
            mesh = lambda *_: SimpleNamespace(grid=SimpleNamespace(n_cells=10), solve_errors=[])
            level = solve_level(settings, mesh, solver, selected=[0])
            self.assertEqual(level.status, "Target force not verified")

    def test_automatic_defaults_preserve_physics_and_restore_custom_values(self):
        with patch("window_state.install_window_state"), patch.object(RustMechDialog, "start_preview", lambda self: None):
            dialog = RustMechDialog(Settings())
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.dispose)
        self.assertTrue(dialog.automatic_contact.isChecked())
        self.assertTrue(dialog.automatic_cores.isChecked())
        self.assertFalse(dialog.controls["cores"].isEnabled())
        self.assertFalse(dialog._contact_form.isRowVisible(dialog.controls["normal_contact_stiffness_factor"]))
        dialog.controls["bottom_platen_friction_coeff"].setValue(.45)
        dialog.automatic_contact.setChecked(False)
        dialog.controls["normal_contact_stiffness_factor"].setValue(.75)
        dialog.controls["contact_damping_factor"].setValue(.025)
        dialog.automatic_contact.setChecked(True)
        self.assertEqual(dialog.collect().normal_contact_stiffness_factor, 1.)
        self.assertEqual(dialog.collect().contact_damping_factor, .01)
        self.assertEqual(dialog.collect().bottom_platen_friction_coeff, .45)
        self.assertTrue(dialog.collect().automatic_contact_parameters)
        dialog.automatic_contact.setChecked(False)
        self.assertEqual(dialog.collect().normal_contact_stiffness_factor, .75)
        self.assertEqual(dialog.collect().contact_damping_factor, .025)
        dialog.automatic_cores.setChecked(False)
        dialog.controls["cores"].setValue(2)
        dialog.automatic_cores.setChecked(True)
        self.assertEqual(dialog.collect().cores, 4)
        dialog.automatic_cores.setChecked(False)
        self.assertEqual(dialog.collect().cores, 2)
        with TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            save_settings(dialog.collect(), path)
            saved = load_settings(path)
            self.assertFalse(saved.automatic_contact_parameters)
            self.assertFalse(saved.automatic_cores)

    def test_existing_custom_numeric_values_open_in_manual_mode(self):
        settings = replace(Settings(), cores=2, normal_contact_stiffness_factor=.75)
        with patch("window_state.install_window_state"), patch.object(RustMechDialog, "start_preview", lambda self: None):
            dialog = RustMechDialog(settings)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.dispose)
        self.assertFalse(dialog.automatic_contact.isChecked())
        self.assertFalse(dialog.automatic_cores.isChecked())
        self.assertEqual(dialog.collect().normal_contact_stiffness_factor, .75)
        self.assertEqual(dialog.collect().cores, 2)


if __name__ == "__main__":
    unittest.main()
