"""Geometry, units and pressure-result contracts for the independent CFD path."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from flow_analysis import FlowSettings, build_flow_grid, export_flow_case, summarize_flow_case
from model import Settings


class FlowAnalysisTests(unittest.TestCase):
    def test_remote_geometry_does_not_require_local_wsl_configuration(self):
        flow=FlowSettings(cells_across=8,empty_pipe=True,distribution="",foam_bashrc="")
        grid,_=build_flow_grid(Settings(),flow)
        self.assertGreater(grid.n_cells,0)
        with self.assertRaises(ValueError): flow.validate_local_runtime()

    def test_invalid_dimensions_and_budget_are_rejected(self):
        defaults = FlowSettings()
        for change in (dict(diameter_mm=7),dict(inlet_speed=float("nan")),dict(density=-1),
                       dict(cells_across=True),dict(iterations=0),dict(turbulence="unknown"),
                       dict(cells_across=128),dict(inlet_length_mm=.001)):
            with self.subTest(change=change),self.assertRaises(ValueError):
                replace(defaults,**change).validate(Settings())

    def test_cylinder_clipping_and_plenums_use_si_units(self):
        flow = FlowSettings(cells_across=12,diameter_mm=4,empty_pipe=True)
        grid,mesh = build_flow_grid(Settings(),flow)
        self.assertAlmostEqual(grid.bounds[4],-.012)
        self.assertAlmostEqual(grid.bounds[5],.024)
        centres = grid.cell_centers().points
        self.assertTrue(np.all((centres[:,0]-.003)**2+(centres[:,1]-.003)**2 < .002**2))
        self.assertEqual(mesh["solid_cells"],0)
        self.assertGreater(mesh["inlet_area_m2"],1e-5)

    def test_tpms_removes_solid_and_keeps_inlet_and_outlet(self):
        flow = FlowSettings(cells_across=12)
        empty,_ = build_flow_grid(Settings(),replace(flow,empty_pipe=True))
        tpms,mesh = build_flow_grid(Settings(),flow)
        self.assertLess(tpms.n_cells,empty.n_cells)
        self.assertGreater(mesh["solid_cells"],0)
        self.assertEqual(tpms.bounds,empty.bounds)
        self.assertTrue(0<mesh["specimen_solid_fraction"]<1)

    def test_export_orientation_boundary_conditions_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            flow = FlowSettings(cells_across=8,empty_pipe=True)
            manifest = export_flow_case(Settings(),flow,path)
            self.assertEqual(manifest["pressure_units"],"Pa (OpenFOAM p multiplied by density)")
            boundary = (path/"constant/polyMesh/boundary").read_text()
            self.assertIn("inlet",boundary)
            self.assertIn("outlet",boundary)
            self.assertIn("type wall",boundary)
            self.assertIn("type noSlip",(path/"0/U").read_text())
            self.assertIn("value uniform 0",(path/"0/p").read_text())
            self.assertIn("nu [0 2 -1",(path/"constant/transportProperties").read_text())
            self.assertEqual(json.loads((path/"flow_case.json").read_text())["flow"],manifest["flow"])
            with self.assertRaises(ValueError): export_flow_case(Settings(),flow,path)
            with self.assertRaisesRegex(RuntimeError,"No solved time"):
                summarize_flow_case(path)


if __name__ == "__main__":
    unittest.main()
