"""Offline starter cards, with property provenance and explicit approximations.

Values are SI. These are homogeneous parent materials, not effective lattice
properties. No plastic curve is inferred from tensile strength or elongation.
"""
from dataclasses import dataclass
import json
from uuid import uuid4

from PySide6.QtCore import QSettings

from material_library import MaterialCard

CUSTOM_CARDS_KEY = "materials/customCards"

# These are explicitly marked beside their values in the property table.
ASSUMED_PROPERTIES = {
    "316l": ("poisson_ratio",), "ti64": ("poisson_ratio",),
    "alsi10mg": ("young_modulus", "poisson_ratio"),
    "6061t6": ("density", "young_modulus", "poisson_ratio"),
    "s355": ("poisson_ratio",), "c110": ("poisson_ratio",),
    "pla": ("poisson_ratio",), "esun_pla_plus": ("young_modulus", "poisson_ratio"),
    "pa12": ("poisson_ratio",),
    "abs_resin": ("density", "poisson_ratio"),
}


@dataclass(frozen=True)
class CatalogMaterial:
    identifier: str
    name: str
    category: str
    condition: str
    properties: dict
    references: tuple  # (title, URL, location / property coverage)
    notes: str = ""
    assumptions: str = ""
    kind: str = "solid"
    reference_temperature_K: float = 293.15
    user_card: MaterialCard | None = None

    def card(self):
        if self.user_card is not None:
            return MaterialCard.from_dict(self.user_card.to_dict())
        source = f"{self.condition}\nReference: {self.reference_temperature_K:g} K. Constant properties.\n"
        source += "\n".join(f"{title} — {location}\n{url}" for title, url, location in self.references)
        if self.notes:
            source += "\n" + self.notes
        if self.assumptions:
            source += "\nEngineering assumptions: " + self.assumptions
        return MaterialCard(self.name, self.kind, self.properties, source=source)


def catalog_materials(settings):
    """Combine the bundled catalog with validated, per-user material cards."""
    settings.sync()
    entries = list(builtin_materials())
    prefix = f"{CUSTOM_CARDS_KEY}/"
    custom = []
    for key in settings.allKeys():
        if not key.startswith(prefix):
            continue
        try:
            card = MaterialCard.from_dict(json.loads(settings.value(key)))
        except (ValueError, TypeError):
            continue
        custom.append(CatalogMaterial(
            f"custom-{key[len(prefix):]}", card.name, "Custom material", "",
            card.properties, (), kind=card.kind, user_card=card,
        ))
    return entries + sorted(custom, key=lambda entry: entry.name.casefold())


def save_custom_material(card, settings):
    if any(entry.kind == card.kind and entry.name.casefold() == card.name.casefold()
           for entry in catalog_materials(settings)):
        raise ValueError("A material with this name already exists.")
    identifier = uuid4().hex
    key = f"{CUSTOM_CARDS_KEY}/{identifier}"
    settings.setValue(key, json.dumps(card.to_dict(), ensure_ascii=False))
    settings.sync()
    if settings.status() != QSettings.Status.NoError:
        settings.remove(key)
        raise OSError("Could not save the material library.")
    return f"custom-{identifier}"


def builtin_materials():
    """Return fresh cards; caller edits cannot mutate the bundled library."""
    sm = "https://www.specialmetals.com/documents/technical-bulletins/inconel/"
    eos = "https://www.eos.info/var/assets/"
    return (
        CatalogMaterial("316l", "316L / wrought, annealed", "Stainless steel",
            "Wrought 316L / EN 1.4404, solution annealed; room temperature.",
            dict(density=8000, young_modulus=200e9, poisson_ratio=.30,
                 conductivity=15, heat_capacity=500, thermal_expansion=16e-6),
            (("Outokumpu — Supra range datasheet", "https://www.outokumpu.com/-/media/files/products/supra/outokumpu-supra-range-datasheet.pdf",
              "Supra 316L/4404 physical properties table: density, E, k, cp and mean expansion 20–100 °C."),),
            "Wrought reference; do not treat this as a calibrated LPBF card.",
            "Poisson ratio 0.30 is an isotropic engineering estimate, not a value reported by this datasheet."),
        CatalogMaterial("ti64", "Ti-6Al-4V / Grade 5, LPBF", "Titanium",
            "EOS Ti64, M 290 / 30 µm; isotropic room-temperature approximation.",
            dict(density=4410, young_modulus=110e9, poisson_ratio=.34),
            (("EOS — Titanium Ti64 material datasheet", eos + "05-datasheet-images/Assets_MDS_Metal/EOS_Titanium_Ti64/Material_DataSheet_EOS_Titanium_Ti64_EOSM290_EOSM300-4_EOSM400_EOSM400-4_en.pdf",
              "M 290 physical/mechanical properties: part density and elastic modulus."),),
            "Build direction and heat treatment affect response. Thermal properties are not supplied by this card.",
            "Poisson ratio 0.34 is an engineering estimate; isotropy is assumed."),
        CatalogMaterial("alsi10mg", "AlSi10Mg / LPBF, as manufactured", "Aluminium",
            "EOS AlSi10Mg, M 290 / 30 µm; horizontal, as manufactured.",
            dict(density=2670, young_modulus=70e9, poisson_ratio=.33, conductivity=110),
            (("EOS — Aluminium AlSi10Mg material datasheet", eos + "03_system-related-assets/material-related-contents/metal-materials-and-examples/metal-material-datasheet/aluminium/material_datasheet_eos_aluminium-alsi10mg_en_web.pdf",
              "pp. 6–8: density >= 2.67 g/cm³; horizontal as-manufactured k = 110 W/(m K)."),),
            "Density uses the published lower bound. Heat capacity is not supplied; thermal assignment requires it.",
            "E = 70 GPa and Poisson ratio 0.33 are engineering estimates, not measured values in this datasheet. Isotropy is assumed."),
        CatalogMaterial("6061t6", "Aluminium 6061-T6 / wrought", "Aluminium",
            "6061-T6 wrought/extruded material; room-temperature reference.",
            dict(density=2700, young_modulus=68.9e9, poisson_ratio=.33, conductivity=167),
            (("Hydro — Alloy 6061 datasheet", "https://www.hydro.com/globalassets/01-products--services/extruded-profiles/americas/ena-resources/alloy-data-sheets/hydro_2019_data_sheet_6061.pdf",
              "T6 / T6511 extrusion; conductivity at 25 °C = 167 W/(m K)."),
             ("NIST — Aluminium 6061-T6", "https://www.nist.gov/mml/acmd/aluminum-6061-t6-uns-aa96061",
              "Independent temperature-dependent elastic/thermal reference; not a temperature model in this card.")),
            "No heat capacity or plastic curve is supplied.",
            "Density 2700 kg/m³, E = 68.9 GPa and Poisson ratio 0.33 are nominal engineering approximations."),
        CatalogMaterial("s355", "S355 / normalized structural steel", "Structural steel",
            "Normalized S355 structural steel; room-temperature elastic reference.",
            dict(density=7850, young_modulus=210e9, poisson_ratio=.30),
            (("SSAB — S355K2+N Zero datasheet 2423", "https://www.ssab.com/-/media/files/en/zero/data_sheet_2423_ssab_s355k2n_zero_2025_01_21.pdf",
              "Grade/delivery condition; yield depends on plate thickness. No plastic model is inferred."),
             ("TU Delft TeachBooks — steel material properties", "https://teachbooks.io/vademecum/main/basis_gegevens/materiaaleigenschappen/staal.html",
              "S355 elastic reference: density 7850 kg/m³, E = 210 GPa.")),
            assumptions="Poisson ratio 0.30 is a nominal isotropic engineering approximation."),
        CatalogMaterial("c110", "Copper C11000 / Cu-ETP, soft", "Copper",
            "KEMPER Cu-ETP / CW004A / C11000, soft state, ambient temperature.",
            dict(density=8900, young_modulus=130e9, poisson_ratio=.34,
                 conductivity=385, thermal_expansion=17.7e-6),
            (("KEMPER — Cu-ETP technical properties", "https://www.kemper-group.com/en-uk/rolled-products/alloys/cu-etp/",
              "Physical properties in soft state: density, E, conductivity and expansion."),),
            "Heat capacity is not supplied by this card.",
            "Poisson ratio 0.34 is an engineering estimate, not a value reported by this source."),
        CatalogMaterial("pla", "PLA / Prusament, FDM horizontal", "Polymer",
            "Prusament PLA, FDM/FFF horizontal test specimen; TDS v1.1 (2022).",
            dict(density=1240, young_modulus=2.3e9, poisson_ratio=.35),
            (("Prusa Polymers — Prusament PLA technical datasheet", "https://prusament.com/wp-content/uploads/2022/10/PLA_Prusament_TDS_2021_10_EN.pdf",
              "pp. 2–3: density 1.24 g/cm³; horizontal printed tensile modulus 2.3 ± 0.1 GPa."),),
            "Printed response depends on orientation, porosity and print settings. No thermal data or failure model.",
            "Poisson ratio 0.35 is an engineering estimate. Isotropic approximation; filament density used for fully dense printed material."),
        CatalogMaterial("esun_pla_plus", "Esun PLA+", "Polymer",
            "eSUN PLA+ filament, FDM printed; isotropic linear-elastic reference.",
            dict(density=1240, young_modulus=3061.7e6, poisson_ratio=.35),
            (("eSUN EU — PLA+ product page", "https://eu.esun3d.com/pla-pro-product",
              "Physical and mechanical properties and recommended printing settings."),
             ("eSUN — PLA+ technical data sheet, February 2026 v1.0",
              "https://eu.esun3d.com/media/esun/catalog/certification/product/PLA/PLA_-TDS-en.pdf",
              "pp. 1–3: density 1.24 g/cm³, directional test data and 100% infill specimen settings."),
             ("Polymers — Mechanical characterization of eSUN PLA+",
              "https://www.mdpi.com/2073-4360/18/2/243",
              "Section 2.5.2: tensile Young's modulus 3061.7 MPa and assumed Poisson ratio 0.35.")),
            "eSUN reports XY/Z tensile strengths of 70.37/36.36 MPa and flexural moduli of "
            "3259.39/2873.94 MPa. Printed response depends on orientation and print settings.",
            "Young's modulus comes from independently tested eSUN PLA+ specimens and is a reference "
            "estimate for the current formulation. Poisson ratio is a modeling assumption from the same study. "
            "Nominal density is used for fully dense material."),
        CatalogMaterial("pa12", "PA12 / EOS PA 2200, SLS dry XY", "Polymer",
            "EOS PA 2200 on HELIOR; dry, X/Y direction, ISO 527.",
            dict(density=980, young_modulus=1.95e9, poisson_ratio=.39),
            (("EOS — PA 2200 process data sheet", "https://www.eos.info/polymer-solutions/data-sheets/all-processes-and-materials?id=pa-2200",
              "HELIOR: dry XY modulus 1950 MPa; part density 0.98 g/cm³; accessed 2026-11-17."),),
            "Humidity and build direction affect stiffness. This is a printed-part reference, not powder bulk density.",
            "Poisson ratio 0.39 is an engineering estimate; isotropy is assumed."),
        CatalogMaterial("in625", "Inconel 625 / annealed, wrought", "Nickel superalloy",
            "Special Metals INCONEL 625, annealed wrought reference at 21 °C.",
            dict(density=8440, young_modulus=207.5e9, poisson_ratio=.278,
                 conductivity=9.8, heat_capacity=410),
            (("Special Metals — INCONEL alloy 625 technical bulletin", sm + "inconel-alloy-625.pdf",
              "Tables 2–4, pp. 2–3: density, cp, k, annealed E and Poisson ratio at 21 °C."),),
            "Constant room-temperature values. This is not an LPBF or high-temperature constitutive model.",
            reference_temperature_K=294.15),
        CatalogMaterial("in718", "Inconel 718 / annealed and aged, wrought", "Nickel superalloy",
            "Special Metals INCONEL 718, annealed and aged wrought reference at 21 °C.",
            dict(density=.297 * 453.59237 / 2.54**3 * 1000,
                 young_modulus=199.9479615e9, poisson_ratio=.29,
                 heat_capacity=435, conductivity=11.4),
            (("Special Metals — INCONEL alloy 718 technical bulletin SMC-045", sm + "inconel-alloy-718.pdf",
              "Tables 2–3 (p. 1): aged density 0.297 lb/in³, E = 29 Msi, Poisson ratio 0.29, cp = 435 J/(kg K). Table 5 (p. 2): aged k = 79 Btu in/(ft² h °F), rounded to 11.4 W/(m K)."),),
            "Density and modulus converted to SI. Table 3 uses AMS 5596B sheet; Table 5 lists its thermal specimen heat treatment separately. No plastic curve supplied.",
            reference_temperature_K=294.15),
        CatalogMaterial("abs_resin", "ABS-like resin / Anycubic 3.0, SLA-MSLA", "Photopolymer resin",
            "Anycubic ABS-Like Resin 3.0; UV-cured 365–405 nm photopolymer; room temperature.",
            dict(density=1075, young_modulus=1.5e9, poisson_ratio=.35),
            (("Anycubic — ABS-Like Resin 3.0 technical datasheet", "https://cdn.shopify.com/s/files/1/0245/5519/2380/files/ANYCUBIC-TDS-ABS-Like_Resin_3.0_815df629-d242-4968-9f14-0defcf102280.pdf?v=1758252957",
              "Physical properties: density range 1.06–1.09 g/cm³ (curing state unspecified); UV post-cure guidance. This PDF does not report Young's modulus."),
             ("Anycubic — ABS-Like Resin 3.0 product specifications", "https://store.anycubic.com/products/abs-like-resin-3-0",
              "Young's modulus range 1300–1700 MPa; nominal midpoint 1500 MPa selected. Product identity, UV wavelength and post-curing guidance.")),
            "ABS-like is a photopolymer, not thermoplastic ABS. Curing time, temperature, colour and build orientation affect response. No thermal data or plastic curve.",
            "Density 1075 kg/m³ is the midpoint of the TDS density range, used as a cured-density estimate requiring calibration. The TDS does not identify the density specimen's curing state; this is NOT a measured cured-part density. Poisson ratio 0.35 is an engineering estimate. Isotropy is assumed."),
        CatalogMaterial("water", "Water / liquid, 20 °C, 1 atm", "Fluid",
            "Liquid water at 293.15 K and 101325 Pa; rounded constant reference values.",
            dict(density=998.2, dynamic_viscosity=1.002e-3, conductivity=.598, heat_capacity=4182),
            (("CoolProp — Water / IAPWS reference", "https://coolprop.org/fluid_properties/fluids/Water.html",
              "IAPWS equation of state and transport references; rounded room-temperature engineering values."),),
            "Single-phase, constant properties; no phase change or temperature-dependent solver model.", kind="fluid"),
        CatalogMaterial("air", "Air / dry, 20 °C, 1 atm", "Fluid",
            "Dry air at 293.15 K and 101325 Pa; rounded constant reference values.",
            dict(density=1.204, dynamic_viscosity=1.825e-5, conductivity=.02587, heat_capacity=1006),
            (("CoolProp — Air reference", "https://coolprop.org/fluid_properties/fluids/Air.html",
              "Air equation of state and transport references; rounded room-temperature engineering values."),),
            "Constant-density, low-speed approximation. Not a compressible gas or buoyancy model.", kind="fluid"),
    )
