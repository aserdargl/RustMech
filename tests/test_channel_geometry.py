"""Channel dimensions, physical placement and shared CFD/CHT phase sampling."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

import numpy as np

from flow_analysis import FlowSettings, build_flow_grid, export_flow_case, sample_channel_solid
from thermal_analysis import ThermalSettings, build_thermal_grid, export_thermal_case
from model import Settings
from mesh_engine import geometry_threshold
from structure_library import evaluate_priority_field


class ChannelGeometryTests(unittest.TestCase):
    def setUp(self):
        self.specimen = Settings()
        self.flow = FlowSettings(channel_shape="rectangular", width_mm=4, height_mm=2,
                                 cells_across=8, inlet_length_mm=2, outlet_length_mm=2)

    def test_rectangular_empty_channel_exact_bounds_area_and_hydraulic_diameter(self):
        flow = replace(self.flow, empty_pipe=True)
        grid, meta = build_flow_grid(self.specimen, flow)
        np.testing.assert_allclose(grid.bounds, [.001,.005,.002,.004,-.002,.008], atol=1e-14)
        self.assertAlmostEqual(meta["inlet_area_m2"], 8e-6)
        self.assertAlmostEqual(flow.hydraulic_diameter_mm, 8/3)
        self.assertAlmostEqual(flow.reynolds, flow.density*flow.inlet_speed*(8/3)*.001/flow.dynamic_viscosity)
        self.assertEqual(grid.n_cells, 16*8*40)

    def test_square_and_cylinder_legacy_defaults(self):
        square, square_meta = build_flow_grid(self.specimen, replace(self.flow, width_mm=2, empty_pipe=True))
        circle, circle_meta = build_flow_grid(self.specimen, replace(self.flow, channel_shape="cylinder", diameter_mm=2, empty_pipe=True))
        self.assertGreater(square.n_cells, circle.n_cells)
        self.assertEqual(square_meta["channel_shape"], "rectangular")
        self.assertEqual(circle_meta["hydraulic_diameter_mm"], 2)
        self.assertEqual(FlowSettings(**{"diameter_mm": 3}).section_length(self.specimen), 6)

    def test_section_length_clips_without_stretching_and_extensions_remain_empty(self):
        flow = replace(self.flow, width_mm=6, height_mm=6, cells_across=12, section_length_mm=3)
        grid, meta = build_thermal_grid(self.specimen, flow)
        self.assertEqual(meta["specimen_end_mm"], 3)
        points = grid.cell_centers().points*1000
        self.assertFalse(grid.cell_data["Solid"][points[:,2]<0].any())
        self.assertFalse(grid.cell_data["Solid"][points[:,2]>3].any())
        p = points[(points[:,2]>0)&(points[:,2]<3)].copy()
        sampled, _ = sample_channel_solid(self.specimen, flow, p)
        p[:,2] += 1.5  # centred crop of the existing 6 mm cube
        expected = evaluate_priority_field(self.specimen, p[:,0],p[:,1],p[:,2])<=geometry_threshold(self.specimen)
        np.testing.assert_array_equal(sampled, expected)

    def test_translation_and_rotation_use_inverse_rigid_transform(self):
        flow = replace(self.flow, specimen_offset_x_mm=.4, specimen_offset_y_mm=-.2,
                       specimen_offset_z_mm=.1, specimen_rotation_z_deg=90)
        local = np.array([[1.2,2.4,3.1],[3.5,3.2,2.7],[20,20,20]])
        rotation = np.array([[0,-1,0],[1,0,0],[0,0,1]])
        world = (local-3) @ rotation.T + np.array([3.4,2.8,3.1])
        solid, envelope = sample_channel_solid(self.specimen, flow, world)
        np.testing.assert_array_equal(envelope, [True,True,False])
        expected = evaluate_priority_field(self.specimen, *local[:2].T)<=geometry_threshold(self.specimen)
        np.testing.assert_array_equal(solid[:2], expected)
        self.assertFalse(solid[2])

    def test_bypass_requires_explicit_selection_and_is_reported(self):
        flow = replace(self.flow, width_mm=8, height_mm=6, cells_across=12)
        with self.assertRaisesRegex(ValueError, "bypass"):
            build_flow_grid(self.specimen, flow)
        grid, meta = build_flow_grid(self.specimen, replace(flow, allow_bypass=True))
        self.assertGreater(meta["bypass_envelope_cells"], 0)
        self.assertTrue(any("Intentional bypass" in warning for warning in meta["warnings"]))
        with self.assertRaisesRegex(ValueError, "bypass"):
            build_flow_grid(self.specimen, replace(self.flow, specimen_offset_x_mm=4))

    def test_cfd_and_cht_use_identical_placed_solid_partition(self):
        flow = replace(self.flow, width_mm=4, height_mm=4, cells_across=12,
                       specimen_rotation_z_deg=30)
        grid, meta = build_flow_grid(self.specimen, flow)
        thermal, thermal_meta = build_thermal_grid(self.specimen, flow)
        self.assertEqual(meta["solid_cells"], int(thermal.cell_data["Solid"].sum()))
        self.assertEqual(grid.n_cells, int((thermal.cell_data["Solid"]==0).sum()))
        self.assertEqual(thermal_meta["channel_shape"], "rectangular")

    def test_rectangular_export_uses_same_channel_in_both_solvers(self):
        flow = replace(self.flow, empty_pipe=True)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cfd = export_flow_case(self.specimen, flow, root/"flow")
            cht = export_thermal_case(self.specimen, flow, ThermalSettings(), root/"cht")
            self.assertEqual(cfd["flow"], cht["flow"])
            self.assertEqual(cfd["mesh"]["inlet_area_m2"], cht["mesh"]["inlet_area_m2"])
            self.assertIn("rectangular", cfd["geometry"])
            self.assertIn("type patch", (root/"flow/constant/polyMesh/boundary").read_text())

    def test_bad_sections_and_mesh_budgets_rejected_before_allocation(self):
        for changes in ({"channel_shape":"triangle"},{"width_mm":float("nan")},
                        {"section_length_mm":-1},{"specimen_rotation_z_deg":float("inf")},
                        {"height_mm":100000},{"allow_bypass":1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.flow, **changes).validate(self.specimen)

    def test_lattice_and_porous_work_in_both_sections_and_preserve_seed(self):
        for family, name in (("lattice", "BCC"), ("porous", "Gaussian Random Field")):
            for shape in ("cylinder", "rectangular"):
                with self.subTest(family=family, shape=shape):
                    spec = replace(self.specimen, structure_family=family, structure_name=name, solid_fraction=15)
                    flow = replace(self.flow, channel_shape=shape, width_mm=6, height_mm=6,
                                   cells_across=16, inlet_length_mm=1, outlet_length_mm=1)
                    first, meta = build_thermal_grid(spec, flow)
                    second, _ = build_thermal_grid(spec, flow)
                    self.assertGreater(meta["solid_cells"], 0)
                    np.testing.assert_array_equal(first.cell_data["Solid"], second.cell_data["Solid"])
                    self.assertEqual(meta["isolated_cells"], 0)
        other, _ = build_thermal_grid(replace(spec, random_seed=43), flow)
        self.assertFalse(np.array_equal(second.cell_data["Solid"], other.cell_data["Solid"]))


if __name__ == "__main__":
    unittest.main()
