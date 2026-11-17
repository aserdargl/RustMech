"""Fitted-mesh geometry contracts, budgets and explicit unsupported CHT scope."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import re
from unittest.mock import patch

import numpy as np
import pyvista as pv

from flow_analysis import FlowSettings
from thermal_analysis import ThermalSettings
from model import Settings
from fitted_mesh import block_mesh_dictionary,snappy_dictionary,layer_report,verify_surface_refinement,placed_insert_surface


class FittedMeshTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(size=2,cell_size=1,compression=.1)
        self.flow = FlowSettings(mesh_method="snappy",diameter_mm=2,width_mm=2,height_mm=2,
                                 cells_across=8,inlet_length_mm=1,outlet_length_mm=1)

    def test_rectangle_and_cylinder_preserve_patch_names_and_si_units(self):
        rectangle = block_mesh_dictionary(self.settings,replace(self.flow,channel_shape="rectangular"))
        cylinder = block_mesh_dictionary(self.settings,self.flow)
        section = rectangle.split("vertices (",1)[1].split(");",1)[0]
        vertices = np.asarray([[float(value) for value in match.split()] for match in re.findall(r"\(([^()]+)\)",section)])
        np.testing.assert_allclose(vertices.min(axis=0),[0,0,-.001])
        np.testing.assert_allclose(vertices.max(axis=0),[.002,.002,.003])
        for text in (rectangle,cylinder):
            for patch_name in ("inlet {","outlet {","walls {"):
                self.assertIn(patch_name,text)
            self.assertIn("convertToMeters 1",text)
        self.assertEqual(cylinder.count("hex "),5)
        self.assertEqual(cylinder.count("arc "),8)
        self.assertEqual(rectangle.count("hex "),1)

    def test_snappy_respects_requested_refinement_layers_and_budget(self):
        flow = replace(self.flow,surface_refinement=2,boundary_layers=4,max_fitted_cells=120000)
        text = snappy_dictionary(self.settings,flow)
        self.assertIn("level (2 2)",text)
        self.assertIn("nSurfaceLayers 4",text)
        self.assertIn("maxGlobalCells 120000",text)
        self.assertIn("patchInfo { type wall; }",text)
        empty = snappy_dictionary(self.settings,replace(flow,empty_pipe=True,boundary_layers=0))
        self.assertIn("castellatedMesh false",empty)
        self.assertIn("addLayers false",empty)

    def test_layer_coverage_reports_partial_extrusion_and_actual_patch_layers(self):
        log = "Extruding 10 out of 20 faces (50%).\nwalls 16 3 2.25 6.87e-05 39.3\ninsert 4 3 0 0 0\n"
        report = layer_report(log)
        self.assertEqual(report["face_coverage_fraction"],.5)
        self.assertEqual(report["patches"]["insert"]["average_layers"],0)
        self.assertEqual(report["patches"]["walls"]["average_layers"],2.25)

    def test_unmet_refinement_or_missing_insert_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)/"constant/polyMesh"
            folder.mkdir(parents=True)
            (folder/"boundary").write_text("insert { nFaces 2; startFace 1; }")
            (folder/"owner").write_text("FoamFile {} 3 (0 0 1)")
            (folder/"cellLevel").write_text("FoamFile {} 2 (1 2)")
            self.assertEqual(verify_surface_refinement(directory,1),1)
            with self.assertRaisesRegex(RuntimeError,"below requested"):
                verify_surface_refinement(directory,2)
            (folder/"boundary").write_text("walls { nFaces 3; startFace 0; }")
            with self.assertRaisesRegex(RuntimeError,"lost the insert"):
                verify_surface_refinement(directory,1)

    def test_axial_clipping_keeps_same_finite_specimen_without_stretching(self):
        source = pv.Box(bounds=(0,2,0,2,0,2)).triangulate()
        with patch("mesh_engine.make_surface",return_value=source):
            result = placed_insert_surface(self.settings,replace(self.flow,section_length_mm=1))
        np.testing.assert_allclose(result.bounds,[0,.002,0,.002,0,.001],atol=1e-10)
        self.assertAlmostEqual(abs(result.volume),4e-9,places=14)
        np.testing.assert_allclose(source.bounds,[0,2,0,2,0,2])

    def test_old_settings_keep_cartesian_and_unqualified_cht_does_not_fall_back(self):
        self.assertEqual(FlowSettings(**{"diameter_mm":3}).mesh_method,"cartesian")
        with self.assertRaisesRegex(ValueError,"Flow only"):
            ThermalSettings().validate(self.settings,self.flow)
        for changes in ({"mesh_method":"unknown"},{"surface_refinement":True},
                        {"boundary_layers":-1},{"max_fitted_cells":0}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                replace(self.flow,**changes).validate(self.settings)


if __name__ == "__main__": unittest.main()
