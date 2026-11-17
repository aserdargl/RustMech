"""General imported solids are shared by flow/CHT without compression faces."""
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pyvista as pv

from flow_analysis import FlowSettings, build_flow_grid, sample_channel_solid
from thermal_analysis import build_thermal_grid
from imported_geometry import read_surface, prepare_general_surface
from mesh_engine import make_surface
from model import Settings, validate_settings


class ImportedFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/"solid.vtp"
        # A finite solid band with open flow on both sides; no picked faces.
        self.surface = pv.Box(bounds=(2,4,2,4,0,6)).triangulate().clean()
        self.surface.save(self.path)
        self.settings = Settings(imported_geometry_path=str(self.path),
            imported_geometry_role="general", mesh_type="TET4", tetra_strategy="netgen")
        self.flow = FlowSettings(channel_shape="rectangular", cells_across=12,
                                 inlet_length_mm=1, outlet_length_mm=1)

    def test_imported_solid_removed_from_cfd_and_preserved_in_cht(self):
        fluid, meta = build_flow_grid(self.settings, self.flow)
        thermal, thermal_meta = build_thermal_grid(self.settings, self.flow)
        self.assertEqual(meta["solid_cells"], 4*4*12)
        self.assertEqual(meta["solid_cells"], int(thermal.cell_data["Solid"].sum()))
        self.assertEqual(fluid.n_cells+meta["solid_cells"], thermal.n_cells)
        self.assertFalse(meta["isolated_cells"])
        self.assertTrue(any("support/loading" in message for message in validate_settings(self.settings)))
        np.testing.assert_allclose(make_surface(self.settings).bounds, self.surface.bounds)

    def test_general_preparation_preserves_volume_and_dimensions(self):
        shifted = self.surface.copy()
        shifted.points += [12,-5,8]
        prepared, size = prepare_general_surface(shifted)
        self.assertEqual(size, 6)
        self.assertAlmostEqual(abs(prepared.volume), abs(self.surface.volume))
        np.testing.assert_allclose(prepared.bounds, self.surface.bounds)

    def test_multiple_closed_components_supported_only_for_general_import(self):
        first = pv.Box(bounds=(1,2,1,2,1,2)).triangulate()
        second = pv.Box(bounds=(4,5,4,5,4,5)).triangulate()
        (first+second).save(self.path)
        with self.assertRaisesRegex(ValueError,"one connected"):
            read_surface(self.path)
        self.assertGreater(read_surface(self.path,require_single=False).n_cells, 0)
        solid, _ = sample_channel_solid(self.settings,self.flow,np.array([[1.5,1.5,1.5],[4.5,4.5,4.5],[3,3,3]]))
        np.testing.assert_array_equal(solid,[True,True,False])

    def test_changed_surface_invalidates_sampling_cache(self):
        p = np.array([[3.,3.,3.]])
        self.assertTrue(sample_channel_solid(self.settings,self.flow,p)[0][0])
        pv.Box(bounds=(0,1,0,1,0,1)).triangulate().save(self.path)
        self.assertFalse(sample_channel_solid(self.settings,self.flow,p)[0][0])

    def test_open_surfaces_and_missing_imports_are_rejected(self):
        pv.Plane().triangulate().save(self.path)
        with self.assertRaisesRegex(ValueError,"closed"):
            build_flow_grid(self.settings,self.flow)
        self.path.unlink()
        with self.assertRaisesRegex(ValueError,"missing"):
            build_flow_grid(self.settings,self.flow)


if __name__ == "__main__": unittest.main()
