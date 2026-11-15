"""Transient CHT contracts: SI/Kelvin conversion, geometry, phases and model bounds."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

import numpy as np

from flow_analysis import FlowSettings
from model import Settings
from thermal_analysis import ThermalSettings,build_thermal_grid,export_thermal_case,_scalar_values


class ThermalAnalysisTests(unittest.TestCase):
    def test_invalid_physics_and_output_budgets_are_rejected(self):
        for change in (dict(inlet_temperature_c=110),dict(initial_solid_c=float("nan")),
                       dict(solid_cp=0),dict(water_conductivity=-1),dict(time_step_s=1),
                       dict(output_interval_s=.001),dict(outer_correctors=True)):
            with self.subTest(change=change),self.assertRaises(ValueError):
                replace(ThermalSettings(),**change).validate(Settings(),FlowSettings())
        with self.assertRaisesRegex(ValueError,"laminar"):
            ThermalSettings().validate(Settings(),FlowSettings(turbulence="kOmegaSST"))

    def test_water_and_solid_partition_the_same_conformal_cylinder(self):
        grid,meta = build_thermal_grid(Settings(),FlowSettings(cells_across=12))
        solid = grid.cell_data["Solid"].astype(bool)
        self.assertTrue(solid.any())
        self.assertTrue((~solid).any())
        self.assertEqual(int(solid.sum()),meta["solid_cells"])
        self.assertEqual(meta["total_cells"],meta["fluid_cells"]+meta["solid_cells"])
        self.assertTrue(np.all((grid.cell_centers().points[solid,2]>0)&(grid.cell_centers().points[solid,2]<.006)))

    def test_export_kelvin_and_conservative_coupled_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = export_thermal_case(Settings(),FlowSettings(cells_across=12),ThermalSettings(),directory)
            water = (directory/"0/water/T").read_text()
            solid = (directory/"0/tpms/T").read_text()
            self.assertIn("353.15",water)
            self.assertIn("293.15",solid)
            self.assertIn("water_to_tpms",water)
            self.assertIn("tpms_to_water",solid)
            self.assertIn("fluidThermo",water)
            self.assertIn("solidThermo",solid)
            self.assertIn("cellZonesOnly",(directory/"Allrun").read_text())
            self.assertIn("chtMultiRegionFoam",(directory/"Allrun").read_text())
            self.assertEqual(manifest["regions"],["water","tpms"])
            with self.assertRaises(ValueError):
                export_thermal_case(Settings(),FlowSettings(cells_across=12),ThermalSettings(),directory)

    def test_scalar_reader_preserves_temperature_and_low_pressure_precision(self):
        self.assertEqual(_scalar_values("internalField uniform 293.15;","internalField",3).tolist(),[293.15]*3)
        values = _scalar_values("internalField nonuniform List<scalar> 2 ( 100000.0001 100000.0002 );","internalField",2)
        self.assertAlmostEqual(values[1]-values[0],.0001,places=10)
        with self.assertRaises(RuntimeError):
            _scalar_values("internalField nonuniform List<scalar> 3 ( 1 2 );","internalField",3)


if __name__ == "__main__": unittest.main()
