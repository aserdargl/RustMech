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
        settings = Settings()
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


if __name__ == "__main__": unittest.main()
