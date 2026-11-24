"""Project opening is transactional and does not launch preview or solvers."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QSettings, QEvent
from PySide6.QtWidgets import QApplication, QWidget

from analysis_project import AnalysisProject, save_project
from flow_analysis import FlowSettings
from model import Settings
from nonlinear_compression import RustMechDialog
from solver_profiles import SolverProfileStore
from thermal_analysis import ThermalSettings


class ProjectWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.preferences = QSettings(str(Path(self.temp.name)/"ui.ini"), QSettings.Format.IniFormat)
        self.patches = [patch("window_state.install_window_state"),
            patch("nonlinear_compression.create_preview_viewport", side_effect=lambda parent, **kw: MagicMock(interactor=QWidget(parent))),
            patch.object(RustMechDialog, "start_preview", lambda self: None)]
        for item in self.patches:
            item.start()
        self.window = RustMechDialog(Settings(), solver_profile_store=SolverProfileStore(self.preferences))
        self.app.processEvents()
        self.controller = self.window.project_workspace
        self.path = Path(self.temp.name)/"test.rustmech.json"

    def tearDown(self):
        self.window.analysis_job = None
        self.window.flow_workspace.job = None
        self.window.dispose()
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def project(self):
        return AnalysisProject.capture(Settings(size=9, material_name="Titanium", young_modulus=110000,
            material_density=4.43, loading_mode="force", applied_force=125),
            FlowSettings(inlet_speed=.02,channel_shape="rectangular",width_mm=4,height_mm=2,
                         specimen_rotation_z_deg=30,allow_bypass=True,mesh_method="snappy",surface_refinement=2,boundary_layers=4),
            FlowSettings(inlet_speed=.04), ThermalSettings(solid_cp=720))

    def test_open_restores_all_workspaces_without_computation(self):
        project = self.project()
        project.active_study = "cht"
        save_project(project, self.path)
        with patch.object(self.window, "apply_geometry") as preview, patch("flow_workspace.AnalysisProcess") as flow_job, patch("thermal_workspace.AnalysisProcess") as thermal_job:
            self.controller.open_path(self.path)
            preview.assert_not_called()
            flow_job.assert_not_called()
            thermal_job.assert_not_called()
        settings = self.window.collect()
        self.assertEqual((settings.size, settings.material_name, settings.young_modulus), (9, "Titanium", 110000))
        self.assertEqual((settings.loading_mode, settings.applied_force), ("force", 125))
        self.assertEqual(self.window.flow_workspace.collect().inlet_speed, .02)
        self.assertEqual(self.window.flow_workspace.collect().channel_shape, "rectangular")
        self.assertEqual(self.window.flow_workspace.collect().specimen_rotation_z_deg, 30)
        self.assertTrue(self.window.flow_workspace.collect().allow_bypass)
        self.assertEqual(self.window.flow_workspace.collect().mesh_method,"snappy")
        self.assertEqual(self.window.flow_workspace.collect().surface_refinement,2)
        self.assertEqual(self.window.flow_workspace.collect().boundary_layers,4)
        self.assertEqual(self.window.thermal_workspace.collect().inlet_speed, .04)
        self.assertEqual(self.window.thermal_workspace.collect_thermal().solid_cp, 720)
        self.assertEqual(self.window.workspace_tabs.currentIndex(), 3)
        self.assertEqual(self.window.workspace_actions.checkedAction().data(), 3)
        self.assertIsNone(self.window.sync_window)
        self.assertEqual(self.controller.fingerprint(self.controller.capture()), self.controller.saved_snapshot)

    def test_save_roundtrip_and_stable_identity(self):
        self.window.controls["size"].setValue(8)
        self.controller.save_path(self.path)
        identifier = self.controller.project.id
        self.window.controls["size"].setValue(7)
        self.controller.open_path(self.path)
        self.assertEqual(self.window.collect().size, 8)
        self.controller.save_path(self.path)
        self.assertEqual(self.controller.project.id, identifier)

    def test_corrupt_file_does_not_mutate_workspace(self):
        before = self.window.collect()
        self.path.write_text('{"format":"rustmech-project","schema_version":99}')
        with self.assertRaises(ValueError):
            self.controller.open_path(self.path)
        self.assertEqual(self.window.collect(), before)
        self.assertIsNone(self.controller.path)

    def test_invalid_control_range_rejected_before_mutation(self):
        project = self.project()
        project.studies["flow"]["config"]["flow"]["inlet_speed"] = -1
        save_project(project, self.path)
        before = self.window.collect()
        with self.assertRaises(ValueError):
            self.controller.open_path(self.path)
        self.assertEqual(self.window.collect(), before)

    def test_active_jobs_block_open_and_save(self):
        self.window.flow_workspace.job = MagicMock()
        with self.assertRaises(RuntimeError):
            self.controller.open_path(self.path)
        with self.assertRaises(RuntimeError):
            self.controller.save_path(self.path)
        self.assertFalse(self.path.exists())

    def test_general_geometry_handoff_does_not_start_work(self):
        import pyvista as pv
        source = Path(self.temp.name)/"surface.vtp"
        pv.Box(bounds=(1,2,1,2,0,6)).triangulate().save(source)
        with patch.object(self.window, "apply_geometry") as preview, patch("flow_workspace.AnalysisProcess") as process:
            self.window._load_flow_geometry(str(source), 6, "Imported solid")
            preview.assert_not_called()
            process.assert_not_called()
        self.assertEqual(self.window.collect().imported_geometry_role, "general")
        self.assertEqual(self.window.workspace_tabs.currentIndex(), 2)


if __name__ == "__main__":
    unittest.main()
