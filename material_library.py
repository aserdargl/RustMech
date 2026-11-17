"""Shared SI solid/fluid cards with explicit, non-extrapolating temperature tables."""
from copy import deepcopy
import csv
import json
import math
from pathlib import Path

import numpy as np

PROPERTY_UNITS = {"density":"kg/m3", "young_modulus":"Pa", "poisson_ratio":"1",
    "yield_stress":"Pa", "tangent_modulus":"Pa", "conductivity":"W/(m K)",
    "heat_capacity":"J/(kg K)", "thermal_expansion":"1/K", "dynamic_viscosity":"Pa s"}


class MaterialCard:
    def __init__(self, name, kind, properties, *, source="", constitutive_law="linear_elastic", hardening_curve=()):
        if not isinstance(name, str) or not name.strip() or kind not in ("solid", "fluid"):
            raise ValueError("Material needs a name and a solid/fluid kind.")
        if not isinstance(properties, dict) or not properties:
            raise ValueError("Material properties must be a nonempty object.")
        self.name, self.kind, self.source = name.strip(), kind, str(source)
        self.properties = deepcopy(properties)
        from model import MATERIAL_MODELS
        if constitutive_law not in MATERIAL_MODELS:
            raise ValueError("Unsupported constitutive law.")
        self.constitutive_law = constitutive_law
        if not isinstance(hardening_curve, (list,tuple)) or any(not isinstance(row,(list,tuple)) or len(row)!=2 for row in hardening_curve):
            raise ValueError("Hardening curve must contain strain/stress pairs in SI.")
        self.hardening_curve = tuple(tuple(row) for row in hardening_curve)
        if self.hardening_curve:
            from model import validate_hardening_curve
            errors = validate_hardening_curve(self.hardening_curve)
            if errors:
                raise ValueError("\n".join(errors))
        for key, value in self.properties.items():
            if key not in PROPERTY_UNITS:
                raise ValueError(f"Unknown material property: {key}")
            if isinstance(value, dict):
                temperatures, values = value.get("temperature_K"), value.get("values")
                if not isinstance(temperatures, list) or not isinstance(values, list) or len(values)!=len(temperatures) or len(values)<2:
                    raise ValueError(f"{key}: provide at least two temperature/value pairs.")
                if any(type(t) not in (int,float) or not math.isfinite(t) or t<=0 for t in temperatures):
                    raise ValueError("Temperature table must use positive, finite Kelvin values.")
                if any(b<=a for a,b in zip(temperatures, temperatures[1:])):
                    raise ValueError("Temperature table must be strictly increasing.")
                for number in values:
                    self._check(key, number)
            else:
                self._check(key, value)

    @staticmethod
    def _check(key, value):
        if type(value) not in (int,float) or not math.isfinite(value):
            raise ValueError(f"{key} must be a finite number.")
        if key == "poisson_ratio":
            valid = -1 < value < .5
        elif key in ("thermal_expansion",):
            valid = True
        elif key == "tangent_modulus":
            valid = value >= 0
        else:
            valid = value > 0
        if not valid:
            raise ValueError(f"Invalid {key} value.")

    def evaluate(self, temperature_K, required=()):
        if type(temperature_K) not in (int,float) or not math.isfinite(temperature_K) or temperature_K<=0:
            raise ValueError("Reference temperature must be positive Kelvin.")
        missing = set(required)-self.properties.keys()
        if missing:
            raise ValueError("Material is missing: " + ", ".join(sorted(missing)))
        values = {}
        for key, value in self.properties.items():
            if isinstance(value, dict):
                temperatures = value["temperature_K"]
                if not temperatures[0] <= temperature_K <= temperatures[-1]:
                    raise ValueError(f"{key}: reference temperature {temperature_K:g} K is outside the material table.")
                values[key] = float(np.interp(temperature_K, temperatures, value["values"]))
            else:
                values[key] = float(value)
        return values

    def to_dict(self):
        return {"format":"rustmech-material", "schema_version":2, "units":"SI", "name":self.name,
                "kind":self.kind, "properties":deepcopy(self.properties), "source":self.source,
                "constitutive_law":self.constitutive_law, "hardening_curve":[list(row) for row in self.hardening_curve]}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or data.get("format")!="rustmech-material" or type(data.get("schema_version")) is not int or data["schema_version"]!=2 or data.get("units")!="SI":
            raise ValueError("Expected a version 2 RustMech SI material card.")
        return cls(data.get("name"), data.get("kind"), data.get("properties"), source=data.get("source", ""),
                   constitutive_law=data.get("constitutive_law", "linear_elastic"), hardening_curve=data.get("hardening_curve", ()))

    def mechanical_values(self, temperature_K=293.15):
        if self.kind != "solid":
            raise ValueError("Mechanical analysis requires a solid material.")
        from model import Settings, validate_material
        values = self.evaluate(temperature_K, ("density", "young_modulus", "poisson_ratio"))
        converted = {"material_name":self.name, "material_model":self.constitutive_law,
                     "material_density":values["density"]/1000, "young_modulus":values["young_modulus"]/1e6,
                     "poisson_ratio":values["poisson_ratio"], "material_card":self.to_dict(),
                     "hardening_curve":tuple((strain,stress/1e6) for strain,stress in self.hardening_curve)}
        if self.constitutive_law == "bilinear_isotropic":
            if not {"yield_stress","tangent_modulus"}.issubset(values):
                raise ValueError("Bilinear material requires yield stress and tangent modulus.")
            converted.update(yield_stress=values["yield_stress"]/1e6, tangent_modulus=values["tangent_modulus"]/1e6)
        errors = validate_material(Settings(**converted))
        if errors:
            raise ValueError("\n".join(errors))
        return converted


def read_material_card(path, *, kind="solid"):
    path = Path(path)
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            columns = reader.fieldnames or []
            if "temperature_K" not in columns or len(columns)<2 or set(columns)-{"temperature_K",*PROPERTY_UNITS}:
                raise ValueError("CSV headers: temperature_K followed by SI material property names.")
            rows = list(reader)
        if not rows:
            raise ValueError("Material table is empty.")
        try:
            properties = {key:{"temperature_K":[float(row["temperature_K"]) for row in rows],
                               "values":[float(row[key]) for row in rows]} for key in columns if key!="temperature_K"}
        except (TypeError, ValueError, KeyError) as error:
            raise ValueError("Material CSV contains an incomplete or nonnumeric row.") from error
        return MaterialCard(path.stem, kind, properties, source=path.name)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data,dict) and data.get("units")=="mm-N-MPa":
        from model import load_material
        values = load_material(path)
        if values.get("material_card"):
            return MaterialCard.from_dict(values["material_card"])
        props = {"density":values["material_density"]*1000,"young_modulus":values["young_modulus"]*1e6,
                 "poisson_ratio":values["poisson_ratio"]}
        if values["material_model"] == "bilinear_isotropic":
            props.update(yield_stress=values["yield_stress"]*1e6,tangent_modulus=values["tangent_modulus"]*1e6)
        return MaterialCard(values["material_name"],"solid",props,source=path.name,
                            constitutive_law=values["material_model"],
                            hardening_curve=[(strain,stress*1e6) for strain,stress in values["hardening_curve"]])
    return MaterialCard.from_dict(data)


def save_material_card(card, path):
    # Keep material cards independent of project documents.
    import os
    import tempfile
    path = Path(path)
    MaterialCard.from_dict(card.to_dict())
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=path.parent,delete=False,suffix=".tmp") as stream:
            temporary = Path(stream.name)
            json.dump(card.to_dict(),stream,ensure_ascii=False,allow_nan=False,indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
