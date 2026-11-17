"""Material imports update physics inputs explicitly and retain original cards."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QWidget

from flow_workspace import FlowWorkspace
from thermal_workspace import ThermalWorkspace
from material_library import MaterialCard, save_material_card
from model import Settings


class WorkspaceMaterialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.preferences = QSettings(str(self.folder/"ui.ini"), QSettings.Format.IniFormat)
        self.solid = MaterialCard("Shared solid","solid",{"density":4430,"young_modulus":110e9,
            "poisson_ratio":.3,"conductivity":7.2,"heat_capacity":560,"thermal_expansion":9e-6})
        self.settings = replace(Settings(),material_card=self.solid.to_dict())
        self.workspaces = []

    def workspace(self, cls=ThermalWorkspace):
        workspace = cls(lambda:self.settings, preferences=self.preferences,
                        viewport_factory=lambda parent:MagicMock(interactor=QWidget(parent)))
        self.workspaces.append(workspace)
        return workspace

    def tearDown(self):
        for workspace in self.workspaces:
            workspace.dispose()
            workspace.close()
        self.temp.cleanup()

    def test_solid_card_is_shared_explicitly_and_survives_preferences(self):
        first = self.workspace()
        with patch("thermal_workspace.AnalysisProcess") as process:
            first.use_model_material()
            process.assert_not_called()
        thermal = first.collect_thermal()
        self.assertEqual((thermal.solid_density,thermal.solid_conductivity,thermal.solid_cp),(4430,7.2,560))
        self.assertEqual(thermal.solid_material_card,self.solid.to_dict())
        second = self.workspace()
        self.assertEqual(second.collect_thermal().solid_material_card,self.solid.to_dict())
        self.assertEqual(second.collect_thermal().solid_cp,560)

    def test_fluid_table_evaluated_at_cht_inlet_and_retained(self):
        workspace = self.workspace()
        path = self.folder/"water.csv"
        path.write_text("temperature_K,density,dynamic_viscosity,heat_capacity,conductivity\n293.15,998,0.001,4180,0.6\n353.15,972,0.0004,4200,0.67\n")
        workspace.thermal_controls["inlet_temperature_c"].setValue(50)
        with patch("thermal_workspace.AnalysisProcess") as process:
            workspace.load_fluid_material_path(path)
            process.assert_not_called()
        self.assertEqual(workspace.collect().density,985)
        self.assertAlmostEqual(workspace.collect().dynamic_viscosity,.0007)
        self.assertEqual(workspace.collect_thermal().water_cp,4190)
        self.assertIn("temperature_K",workspace.collect().fluid_material_card["properties"]["density"])

    def test_wrong_phase_missing_properties_and_extrapolation_leave_inputs_unchanged(self):
        workspace = self.workspace()
        path = self.folder/"solid.json"
        save_material_card(self.solid,path)
        before = workspace.collect()
        with self.assertRaisesRegex(ValueError,"fluid"):
            workspace.load_fluid_material_path(path)
        self.assertEqual(workspace.collect(),before)
        thermal = workspace.collect_thermal()
        with self.assertRaisesRegex(ValueError,"missing"):
            workspace.apply_solid_card(MaterialCard("Incomplete","solid",{"density":1000}))
        self.assertEqual(workspace.collect_thermal(),thermal)
        path.write_text("temperature_K,density,dynamic_viscosity\n300,990,.001\n310,980,.0008\n")
        flow = self.workspace(FlowWorkspace)
        # Use the same table with its explicit CSV extension.
        csv = path.with_suffix('.csv')
        csv.write_text(path.read_text())
        with self.assertRaisesRegex(ValueError,"outside"):
            flow.load_fluid_material_path(csv)

    def test_geometry_inspection_is_explicit_and_displays_flagged_cells(self):
        import pyvista as pv
        from flow_analysis import FlowSettings,export_geometry_inspection
        source = self.folder/"block.vtp"
        pv.Box(bounds=(0,6,0,6,2,4)).triangulate().save(source)
        self.settings = replace(self.settings,imported_geometry_path=str(source),imported_geometry_role="general")
        flow = self.workspace(FlowWorkspace)
        with patch("flow_workspace.AnalysisProcess") as process:
            flow.inspect_button.click()
            self.assertIs(process.call_args.args[0],export_geometry_inspection)
            self.assertFalse(flow.inspect_button.isEnabled())
            flow.cancel()
        case = self.folder/"inspection"
        export_geometry_inspection(self.settings,FlowSettings(channel_shape="rectangular",cells_across=12,
                                   inlet_length_mm=1,outlet_length_mm=1),case)
        flow.load_directory(case)
        self.assertEqual(flow.view.currentIndex(),6)
        self.assertIn("GEOMETRY INSPECTION",flow.results.text())
        calls = flow.plotter.add_mesh.call_args_list
        self.assertTrue(any(call.kwargs.get("scalars")=="GeometryIssue" for call in calls))


if __name__ == "__main__": unittest.main()
