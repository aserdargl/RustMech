"""Blocked paths can be inspected without preparing runnable solver files."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pyvista as pv

from flow_analysis import FlowSettings, build_flow_grid, export_geometry_inspection
from model import Settings


class GeometryInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        surface = self.root/"blocking-solid.vtp"
        pv.Box(bounds=(0,6,0,6,2,4)).triangulate().save(surface)
        self.settings = Settings(imported_geometry_path=str(surface),imported_geometry_role="general")
        self.flow = FlowSettings(channel_shape="rectangular",cells_across=12,
                                 inlet_length_mm=1,outlet_length_mm=1)

    def test_blocked_channel_retains_all_components_and_3d_markers(self):
        with self.assertRaisesRegex(ValueError,"No face-connected"):
            build_flow_grid(self.settings,self.flow)
        with patch("flow_analysis._wsl",side_effect=AssertionError("solver launched")):
            report = export_geometry_inspection(self.settings,self.flow,self.root/"inspection")
        mesh = report["mesh"]
        self.assertFalse(mesh["geometry_valid_for_flow"])
        self.assertFalse(mesh["geometry_valid_for_cht"])
        self.assertEqual(mesh["isolated_fluid_components"],2)
        self.assertEqual(len(mesh["isolated_component_markers"]),2)
        self.assertTrue(all(len(marker["centre_mm"])==3 for marker in mesh["isolated_component_markers"]))
        grid = pv.read(self.root/"inspection/fluid_mesh.vtu")
        self.assertTrue(np.all(grid.cell_data["GeometryIssue"]==2))
        self.assertFalse((self.root/"inspection/Allrun").exists())
        self.assertFalse((self.root/"inspection/constant/polyMesh").exists())

    def test_disallowed_bypass_can_be_seen_without_changing_permission(self):
        settings = self.settings
        flow = replace(self.flow,width_mm=8,height_mm=6)
        report = export_geometry_inspection(settings,flow,self.root/"bypass")
        self.assertFalse(report["flow"]["allow_bypass"])
        self.assertFalse(report["mesh"]["geometry_valid_for_flow"])
        self.assertGreater(report["mesh"]["bypass_envelope_cells"],0)
        grid = pv.read(self.root/"bypass/fluid_mesh.vtu")
        self.assertIn(1,np.unique(grid.cell_data["GeometryIssue"]))

    def test_inspection_does_not_overwrite_existing_case(self):
        directory = self.root/"case"
        directory.mkdir()
        sentinel = directory/"data.txt"
        sentinel.write_text("keep")
        with self.assertRaisesRegex(ValueError,"empty"):
            export_geometry_inspection(self.settings,self.flow,directory)
        self.assertEqual(sentinel.read_text(),"keep")

    def test_remote_inspection_needs_only_mesh_capability_and_never_runs_solver(self):
        import base64
        from dataclasses import asdict
        from remote_worker import run_operation
        remote = self.root/"remote"
        remote.mkdir()
        output = self.root/"output"
        output.mkdir()
        request = {"operation":"flow_export","inspection":True,"settings":asdict(self.settings),
                   "flow":asdict(self.flow),"imported_geometry_vtp":base64.b64encode(Path(self.settings.imported_geometry_path).read_bytes()).decode("ascii")}
        capabilities = {"mesh":True,"mesh_types":["TET4"],"flow":False,"thermal":False}
        with patch("remote_worker.probe",return_value=capabilities),patch("remote_worker.Path.cwd",return_value=remote),patch("remote_worker.subprocess.run",side_effect=AssertionError("solver launched")):
            result = run_operation(request,output)
        self.assertEqual(result["case"],".")
        self.assertTrue((output/"fluid_mesh.vtu").exists())
        self.assertTrue((output/"flow_case.json").exists())
        self.assertTrue((output/"surface_audit.vtp").exists())

    def test_intersecting_surface_is_inspectable_but_cannot_be_solved(self):
        a = pv.Box(bounds=(1,3,1,3,1,3)).triangulate()
        b = pv.Box(bounds=(2,4,2,4,2,4)).triangulate()
        path = self.root/"overlap.vtp"
        (a+b).save(path)
        settings = replace(self.settings,imported_geometry_path=str(path))
        with self.assertRaisesRegex(ValueError,"intersecting triangle"):
            build_flow_grid(settings,self.flow)
        report = export_geometry_inspection(settings,self.flow,self.root/"overlap-inspection")
        self.assertFalse(report["mesh"]["geometry_valid_for_flow"])
        self.assertEqual(report["mesh"]["surface_audit"]["intersection"]["status"],"invalid")
        self.assertTrue((self.root/"overlap-inspection/surface_audit.vtp").exists())

    def test_surface_defect_markers_follow_specimen_placement(self):
        from flow_analysis import specimen_transform
        flow = replace(self.flow,specimen_offset_x_mm=.4,specimen_rotation_z_deg=90)
        report = export_geometry_inspection(self.settings,flow,self.root/"rotated")
        surface = pv.read(self.root/"rotated/surface_audit.vtp")
        original = pv.read(self.settings.imported_geometry_path)
        rotation,centre = specimen_transform(self.settings,flow)
        from scipy.spatial import cKDTree
        expected = (original.points-3)@rotation.T+centre
        self.assertLess(cKDTree(expected).query(surface.points)[0].max(),1e-6)
        self.assertLess(cKDTree(surface.points).query(expected)[0].max(),1e-6)
        self.assertIn("surface_audit",report["mesh"])


if __name__ == "__main__": unittest.main()
