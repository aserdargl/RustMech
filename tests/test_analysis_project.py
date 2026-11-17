"""Projects preserve shared references, legacy inputs and atomic file writes."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from analysis_project import AnalysisProject, load_project, save_project
from flow_analysis import FlowSettings
from model import Settings
from thermal_analysis import ThermalSettings


class AnalysisProjectTests(unittest.TestCase):
    def setUp(self):
        self.project = AnalysisProject.capture(
            Settings(material_name="Ti alloy", young_modulus=110000, material_density=4.43),
            FlowSettings(inlet_speed=.12), FlowSettings(inlet_speed=.03),
            ThermalSettings(solid_conductivity=7.2))

    def test_roundtrip_shared_geometry_units_and_independent_physics_inputs(self):
        self.project.active_study = "cht"
        self.project.studies["cht"]["boundaries"] = {"heater": ["face-12"]}
        self.project.studies["flow"]["results"] = [{"id": "run-1", "path": "results/run1"}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"design.rustmech.json"
            save_project(self.project, path)
            loaded = load_project(path)
            settings, flow, cht, thermal = loaded.workspace_settings()
            self.assertEqual(settings.material_name, "Ti alloy")
            self.assertEqual(settings.material_density, 4.43)
            self.assertEqual((flow.inlet_speed, cht.inlet_speed), (.12, .03))
            self.assertEqual(thermal.solid_conductivity, 7.2)
            self.assertEqual(loaded.active_study, "cht")
            self.assertEqual(loaded.studies["cht"]["boundaries"], {"heater": ["face-12"]})
            self.assertTrue(Path(loaded.studies["flow"]["results"][0]["path"]).is_absolute())
            self.assertEqual({v["geometry"] for v in loaded.studies.values()}, {"specimen"})

    def test_capture_preserves_identity_and_results_without_aliasing(self):
        self.project.studies["mechanical"]["results"] = [{"id": "r1", "path": "/result"}]
        updated = AnalysisProject.capture(Settings(size=9), previous=self.project)
        self.assertEqual(updated.id, self.project.id)
        self.assertEqual(updated.studies["mechanical"]["results"][0]["id"], "r1")
        self.assertEqual(self.project.geometries["specimen"]["settings"]["size"], 6)
        card = {"properties":{"density":4430}}
        captured = AnalysisProject.capture(Settings(material_card=card))
        card["properties"]["density"] = 9999
        self.assertEqual(captured.workspace_settings()[0].material_card["properties"]["density"],4430)

    def test_shared_geometry_overrides_per_study_legacy_snapshot(self):
        self.project.geometries["specimen"]["settings"]["size"] = 10
        self.assertEqual(self.project.workspace_settings()[0].size, 10)

    def test_assigned_materials_supply_workspace_properties(self):
        self.project.materials["flow-fluid"]["properties"]["density"] = 950
        self.project.materials["thermal-solid"]["properties"]["solid_cp"] = 777
        values = self.project.workspace_settings()
        self.assertEqual(values[1].density, 950)
        self.assertEqual(values[3].solid_cp, 777)

    def test_legacy_settings_and_cases_migrate_without_solving(self):
        legacy = {**asdict(Settings()), "platen_friction_coeff": .17}
        legacy.pop("bottom_platen_friction_coeff")
        legacy.pop("top_platen_friction_coeff")
        self.assertEqual(AnalysisProject.from_dict(legacy).workspace_settings()[0].bottom_platen_friction_coeff, .17)
        case = {"specimen": asdict(Settings()), "flow": asdict(FlowSettings(inlet_speed=.2))}
        project = AnalysisProject.from_dict(case)
        self.assertEqual(project.active_study, "flow")
        case["thermal"] = asdict(ThermalSettings(solid_cp=700))
        project = AnalysisProject.from_dict(case)
        self.assertEqual(project.active_study, "cht")
        self.assertEqual(project.workspace_settings()[3].solid_cp, 700)

    def test_corrupt_versions_types_units_and_references_rejected(self):
        original = self.project.to_dict()
        changes = [lambda d: d.update(schema_version=99), lambda d: d.update(schema_version=True),
                   lambda d: d["units"].update(geometry="m"),
                   lambda d: d["studies"]["flow"].update(geometry="missing"),
                   lambda d: d["studies"]["flow"]["materials"].update(fluid="missing"),
                   lambda d: d["studies"]["flow"]["config"]["flow"].update(inlet_speed="fast"),
                   lambda d: d["studies"]["flow"]["config"]["flow"].update(density=float("nan")),
                   lambda d: d["studies"]["flow"].update(results=[{"path": "a"}]),
                   lambda d: d.update(active_study="missing")]
        for change in changes:
            with self.subTest(change=change):
                data = deepcopy(original)
                change(data)
                with self.assertRaises(ValueError):
                    AnalysisProject.from_dict(data)

    def test_failed_replace_keeps_original_file_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"project.json"
            path.write_text("original", encoding="utf-8")
            with patch("analysis_project.os.replace", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    save_project(self.project, path)
            self.assertEqual(path.read_text(), "original")
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_relative_import_resolved_against_project_directory(self):
        self.project.geometries["specimen"]["settings"]["imported_geometry_path"] = "part.stl"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"project.json"
            save_project(self.project, path)
            self.assertEqual(load_project(path).workspace_settings()[0].imported_geometry_path,
                             str((Path(folder)/"part.stl").resolve()))


if __name__ == "__main__":
    unittest.main()
