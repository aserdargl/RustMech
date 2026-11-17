"""Versioned project snapshots shared by the existing analysis workspaces.

This is a storage contract, not a solver capability declaration. Draft studies
can be saved before their geometry, mesh and physical inputs are solve-ready.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields
import json
import math
import os
from pathlib import Path
import tempfile
from uuid import uuid4

from flow_analysis import FlowSettings
from thermal_analysis import ThermalSettings
from model import GEOMETRY_FIELDS, MATERIAL_FIELDS, Settings, settings_from_dict

FORMAT = "rustmech-project"
SCHEMA_VERSION = 1
PHYSICS = ("mechanical", "flow", "thermal", "cht")
UNITS = {"geometry": "mm", "mechanical": "mm-N-MPa", "thermal": "SI; temperature inputs degC", "flow": "SI"}


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    return value


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Project contains a non-finite number.")
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _finite(item)


def decode_settings(cls, values):
    """Reject mistyped values instead of letting Qt silently coerce them."""
    values = _object(values, cls.__name__)
    defaults = asdict(cls())
    if set(values) - set(defaults):
        raise ValueError(f"Unknown {cls.__name__} fields: {sorted(set(values)-set(defaults))}")
    for key, value in values.items():
        example = defaults[key]
        if example is None:
            valid = value is None or (type(value) in (int, float) and math.isfinite(value))
            if key.endswith("material_card"):
                valid = value is None or isinstance(value, dict)
            if key in ("automatic_contact_parameters", "automatic_cores"):
                valid = value is None or type(value) is bool
            if key in ("minimum_substeps", "maximum_substeps"):
                valid = value is None or type(value) is int
        elif type(example) is bool:
            valid = type(value) is bool
        elif type(example) is int:
            valid = type(value) is int
        elif type(example) is float:
            valid = type(value) in (int, float) and math.isfinite(value)
        elif isinstance(example, str):
            valid = isinstance(value, str)
        else:
            valid = isinstance(value, (list, tuple))
        if not valid:
            raise ValueError(f"Invalid {cls.__name__}.{key} value.")
    _finite(values)
    return settings_from_dict(values) if cls is Settings else cls(**values)


@dataclass
class AnalysisProject:
    name: str = "Untitled project"
    id: str = field(default_factory=lambda: uuid4().hex)
    geometries: dict = field(default_factory=dict)
    materials: dict = field(default_factory=dict)
    regions: dict = field(default_factory=dict)
    studies: dict = field(default_factory=dict)
    active_study: str = "mechanical"

    @classmethod
    def capture(cls, settings, flow=None, cht_flow=None, thermal=None, previous=None):
        """Update one shared specimen and preserve project/result identities."""
        project = deepcopy(previous) if previous is not None else cls()
        flow, cht_flow = flow or FlowSettings(), cht_flow or FlowSettings()
        thermal = thermal or ThermalSettings()
        geometry = deepcopy({key: getattr(settings, key) for key in GEOMETRY_FIELDS})
        project.geometries["specimen"] = {"name": settings.imported_geometry_name or settings.structure_name,
                                          "units": "mm", "settings": geometry}
        project.materials.update({
            "mechanical-solid": {"kind": "solid", "units": "mm-N-MPa",
                                 "properties": deepcopy({key: getattr(settings, key) for key in MATERIAL_FIELDS})},
            "thermal-solid": {"kind": "solid", "units": "SI",
                              "properties": {key: getattr(thermal, key) for key in
                                             ("solid_density", "solid_cp", "solid_conductivity")}},
        })
        for physics, fluid in (("flow", flow), ("cht", cht_flow)):
            project.materials[f"{physics}-fluid"] = {"kind": "fluid", "units": "SI",
                "card": deepcopy(fluid.fluid_material_card),
                "properties": {"density": fluid.density, "dynamic_viscosity": fluid.dynamic_viscosity}}
        project.materials["cht-fluid"]["properties"].update(water_cp=thermal.water_cp,
                                                            water_conductivity=thermal.water_conductivity)
        project.materials["thermal-solid"]["card"] = deepcopy(thermal.solid_material_card)
        project.regions.update({"solid": {"geometry": "specimen", "phase": "solid"},
                                "fluid": {"geometry": "specimen", "phase": "fluid-complement"}})
        configs = {"mechanical": {"settings": asdict(settings)}, "flow": {"flow": asdict(flow)},
                   "cht": {"flow": asdict(cht_flow), "thermal": asdict(thermal)}}
        assignments = {"mechanical": {"solid": "mechanical-solid"}, "flow": {"fluid": "flow-fluid"},
                       "cht": {"solid": "thermal-solid", "fluid": "cht-fluid"}}
        for kind, config in configs.items():
            old = project.studies.get(kind, {})
            project.studies[kind] = {"physics": kind, "geometry": "specimen", "config": config,
                                     "materials": assignments[kind], "boundaries": old.get("boundaries", {}),
                                     "results": old.get("results", [])}
        return project

    def workspace_settings(self):
        mechanical = self.studies.get("mechanical")
        if mechanical is None:
            raise ValueError("Project has no mechanical workspace draft.")
        values = dict(mechanical["config"]["settings"])
        values.update(self.geometries[mechanical["geometry"]]["settings"])
        values.update(self.materials[mechanical["materials"]["solid"]]["properties"])
        settings = decode_settings(Settings, values)
        decoded = {}
        for kind in ("flow", "cht"):
            study = self.studies[kind]
            fluid = self.materials[study["materials"]["fluid"]]
            values = dict(study["config"]["flow"])
            values.update({key:fluid["properties"][key] for key in ("density","dynamic_viscosity")})
            values["fluid_material_card"] = fluid.get("card", values.get("fluid_material_card"))
            decoded[kind] = decode_settings(FlowSettings, values)
        study = self.studies["cht"]
        solid = self.materials[study["materials"]["solid"]]
        thermal = dict(study["config"]["thermal"])
        thermal.update({key:solid["properties"][key] for key in ("solid_density","solid_cp","solid_conductivity")})
        thermal.update({key:self.materials[study["materials"]["fluid"]]["properties"][key]
                        for key in ("water_cp","water_conductivity")})
        thermal["solid_material_card"] = solid.get("card",thermal.get("solid_material_card"))
        return settings, decoded["flow"], decoded["cht"], decode_settings(ThermalSettings, thermal)

    def to_dict(self):
        return {"format": FORMAT, "schema_version": SCHEMA_VERSION, "units": dict(UNITS), **asdict(self)}

    @classmethod
    def from_dict(cls, data):
        data = _object(data, "Project")
        _finite(data)
        if data.get("format") != FORMAT:
            return cls.from_legacy(data)
        if type(data.get("schema_version")) is not int or data["schema_version"] != SCHEMA_VERSION:
            raise ValueError("Unsupported RustMech project version.")
        if data.get("units") != UNITS:
            raise ValueError("Unsupported project units.")
        keys = {item.name for item in fields(cls)}
        if not keys.issubset(data):
            raise ValueError("Incomplete project file.")
        project = cls(**{key: deepcopy(data[key]) for key in keys})
        if not all(isinstance(v, str) and v.strip() for v in (project.id, project.name, project.active_study)):
            raise ValueError("Invalid project identity.")
        for key in ("geometries", "materials", "regions", "studies"):
            _object(getattr(project, key), key)
        for identifier, geometry in project.geometries.items():
            _object(geometry, f"Geometry {identifier}")
            if geometry.get("units") != "mm":
                raise ValueError("Geometry must use mm.")
            decode_settings(Settings, _object(geometry.get("settings"), "Geometry settings"))
        for identifier, material in project.materials.items():
            _object(material, f"Material {identifier}")
            if material.get("kind") not in ("solid", "fluid") or material.get("units") not in ("SI", "mm-N-MPa"):
                raise ValueError("Invalid material kind or units.")
            _object(material.get("properties"), "Material properties")
        for region in project.regions.values():
            _object(region, "Region")
            if region.get("geometry") not in project.geometries or region.get("phase") not in ("solid", "fluid-complement"):
                raise ValueError("Invalid region geometry or phase.")
        for study in project.studies.values():
            _object(study, "Study")
            if study.get("physics") not in PHYSICS or study.get("geometry") not in project.geometries:
                raise ValueError("Invalid study physics or geometry reference.")
            for region, material in _object(study.get("materials"), "Material assignments").items():
                if region not in project.regions or material not in project.materials:
                    raise ValueError("Invalid study region/material reference.")
                phase = "fluid" if project.regions[region]["phase"] == "fluid-complement" else "solid"
                if project.materials[material]["kind"] != phase:
                    raise ValueError("Material phase does not match the assigned region.")
            _object(study.get("boundaries"), "Boundary selections")
            _object(study.get("config"), "Study configuration")
            if not isinstance(study.get("results"), list):
                raise ValueError("Study results must be a list.")
            for result in study["results"]:
                if not isinstance(result, dict) or not all(isinstance(result.get(k), str) for k in ("id", "path")):
                    raise ValueError("Invalid result reference.")
        if project.active_study not in project.studies:
            raise ValueError("Active study is missing.")
        try:
            project.workspace_settings()
        except (KeyError, TypeError) as error:
            raise ValueError("Incomplete workspace configuration.") from error
        return project

    @classmethod
    def from_legacy(cls, data):
        """Existing settings and exported CFD/CHT cases remain usable."""
        if "specimen" in data and "flow" in data:
            settings = decode_settings(Settings, data["specimen"])
            flow = decode_settings(FlowSettings, data["flow"])
            if "thermal" in data:
                project = cls.capture(settings, cht_flow=flow,
                                      thermal=decode_settings(ThermalSettings, data["thermal"]))
                project.active_study = "cht"
            else:
                project = cls.capture(settings, flow=flow)
                project.active_study = "flow"
            return project
        if "size" in data and ("structure_name" in data or "young_modulus" in data):
            # Legacy platen keys are handled by the existing settings decoder.
            known = {key: value for key, value in data.items() if key in asdict(Settings())}
            settings = decode_settings(Settings, {**asdict(settings_from_dict(data)), **known})
            return cls.capture(settings)
        raise ValueError("Expected a RustMech project, settings file or flow/thermal case.")


def save_project(project, path):
    path = Path(path)
    data = project.to_dict()
    AnalysisProject.from_dict(data)
    serialized = json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".rustmech-project-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_project(path):
    path = Path(path).resolve()
    project = AnalysisProject.from_dict(json.loads(path.read_text(encoding="utf-8-sig")))
    for geometry in project.geometries.values():
        name = geometry["settings"].get("imported_geometry_path")
        if name and not Path(name).is_absolute():
            geometry["settings"]["imported_geometry_path"] = str((path.parent/name).resolve())
    for study in project.studies.values():
        for result in study["results"]:
            if result["path"] and not Path(result["path"]).is_absolute():
                result["path"] = str((path.parent/result["path"]).resolve())
    return project
