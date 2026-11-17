"""Common SI cards, physical validation and legacy mechanical unit conversion."""
from pathlib import Path
import tempfile
import unittest

from material_library import MaterialCard, read_material_card, save_material_card
from model import Settings, save_material


class MaterialLibraryTests(unittest.TestCase):
    def card(self):
        return MaterialCard("Example solid","solid",{"density":7850,"young_modulus":200e9,
            "poisson_ratio":.3,"conductivity":16,"heat_capacity":500,"thermal_expansion":1.2e-5})

    def test_si_mechanical_conversion_and_roundtrip(self):
        card = self.card()
        values = card.mechanical_values()
        self.assertEqual(values["young_modulus"], 200000)
        self.assertEqual(values["material_density"], 7.85)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"card.json"
            save_material_card(card,path)
            self.assertEqual(read_material_card(path).to_dict(), card.to_dict())

    def test_legacy_materials_migrate_without_inventing_thermal_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"old.json"
            original = Settings(young_modulus=110000,material_density=4.43)
            save_material(original,path)
            card = read_material_card(path)
            self.assertEqual(card.properties["young_modulus"],110e9)
            self.assertEqual(card.mechanical_values()["material_density"],4.43)
            with self.assertRaisesRegex(ValueError,"conductivity"):
                card.evaluate(293.15,("conductivity",))

    def test_table_interpolates_without_extrapolation_and_preserves_source(self):
        card = MaterialCard("Fluid","fluid",{"density":{"temperature_K":[293.15,353.15],"values":[998,972]},
                            "dynamic_viscosity":.001},source="measured.csv")
        self.assertEqual(card.evaluate(323.15)["density"],985)
        self.assertEqual(card.to_dict()["source"],"measured.csv")
        with self.assertRaisesRegex(ValueError,"outside"):
            card.evaluate(273.15)
        self.assertEqual(MaterialCard.from_dict(card.to_dict()).properties,card.properties)

    def test_csv_headers_units_and_monotonic_temperature(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"fluid.csv"
            path.write_text("temperature_K,density,dynamic_viscosity\n293.15,998,0.001\n353.15,972,0.0004\n")
            card = read_material_card(path,kind="fluid")
            self.assertEqual(card.kind,"fluid")
            self.assertAlmostEqual(card.evaluate(323.15)["dynamic_viscosity"],.0007)
            path.write_text("temperature_K,density\n300,1\n300,2\n")
            with self.assertRaisesRegex(ValueError,"increasing"):
                read_material_card(path)

    def test_bad_properties_units_and_wrong_phase_rejected(self):
        for properties in ({"density":0},{"poisson_ratio":.5},{"density":float("nan")},
                           {"unknown":1},{"heat_capacity":True}):
            with self.subTest(properties=properties),self.assertRaises(ValueError):
                MaterialCard("bad","solid",properties)
        data = self.card().to_dict()
        data["units"] = "mm-N-MPa"
        with self.assertRaises(ValueError):
            MaterialCard.from_dict(data)
        with self.assertRaisesRegex(ValueError,"solid"):
            MaterialCard("Water","fluid",{"density":998}).mechanical_values()

    def test_bilinear_and_multilinear_stresses_are_converted_to_mpa(self):
        props = {**self.card().properties,"yield_stress":250e6,"tangent_modulus":1e9}
        card = MaterialCard("Steel","solid",props,constitutive_law="bilinear_isotropic")
        self.assertEqual(card.mechanical_values()["yield_stress"],250)
        self.assertEqual(card.mechanical_values()["tangent_modulus"],1000)
        card = MaterialCard("Steel","solid",props,constitutive_law="multilinear_isotropic",
                            hardening_curve=[(0,250e6),(.1,350e6)])
        self.assertEqual(card.mechanical_values()["hardening_curve"],((0,250),(.1,350)))


if __name__ == "__main__": unittest.main()
