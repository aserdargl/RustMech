"""Solver-independent configuration, validation and exact FE mesh data."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pyvista as pv

from structure_library import (
    ALL_STRUCTURES_BY_FAMILY,
    STRUCTURE_FAMILIES,
    TPMS_MORPHOLOGIES,
    resolve_family_and_name,
)

ROOT = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
SETTINGS_FILE = ROOT / "settings.json"
DEFAULT_CORES = 4
MIN_MESH_INPUT_MM = 0.001
TETRAHEDRAL_MESH_TYPES = ("TET10", "TET4")
MESH_TYPES = ("HEX8", "HEX20", *TETRAHEDRAL_MESH_TYPES)
# VTK ordering of midside nodes, expressed as corner pairs.
HEX_EDGES = (
    (0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
    (0, 4), (1, 5), (2, 6), (3, 7),
)
TET_EDGES = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))
MAX_SUBDIVISIONS = 150
MAX_FE_ELEMENTS = 1_100_000
MAX_SURFACE_SAMPLES = 256**3
NUMERIC_GEOMETRY_FIELDS = ("size", "cell_size", "solid_fraction")
GEOMETRY_FIELDS = (
    "test_profile", "specimen_architecture", "core_fraction",
    "imported_geometry_path",
    "imported_geometry_name",
    "mesh_resolution",
    "mesh_type",
    "tetra_strategy",
    "local_mesh_size",
    "structure_family",
    "structure_name",
    "tpms_morphology",
    "random_seed",
    *NUMERIC_GEOMETRY_FIELDS,
)
MATERIAL_MODELS = {
    "linear_elastic": "Linear elastic",
    "bilinear_isotropic": "Bilinear elastoplastic",
    "multilinear_isotropic": "True stress–strain (MISO)",
}
SOLVER_BACKENDS = {
    "ansys_mapdl": "ANSYS MAPDL",
    "openfoam": "OpenFOAM (Inactive)",
    "kratos": "Kratos Multiphysics",
}
PLATEN_MARGIN_RATIO = 0.12
SELF_CONTACT_MODES = {
    "disabled": "Disabled (no self-contact)",
    "frictional": "Frictional self-contact (Coulomb μ_self)",
    "frictionless": "Frictionless self-contact (μ_self = 0)",
    "rough": "Rough self-contact (no sliding)",
}
CONTACT_FORMULATIONS = {
    "augmented_lagrange": "Augmented Lagrangian (Default)",
    "penalty": "Pure Penalty",
}
# Automatic UI presets are saved with their effective numerical values so every
# solver and exported study uses the same explicit, reviewable configuration.
CONTACT_NUMERICAL_FIELDS = (
    "normal_contact_stiffness_factor", "penetration_tolerance_factor",
    "platen_penetration_tolerance_factor", "contact_damping_factor",
)
LOADING_MODES = {"displacement": "Displacement control", "force": "Force control"}
PLATEN_CONTACT_MODES = {
    "frictional": "Frictional (Coulomb)",
    "frictionless": "Frictionless",
    "rough": "Rough (no sliding, separation allowed)",
    "bonded": "Bonded (initial contact)",
}
ANALYSIS_FIELDS = (
    "loading_mode", "compression", "applied_force", "substeps", "minimum_substeps",
    "maximum_substeps", "cores", "solver_backend",
    "bottom_platen_contact", "top_platen_contact", "bottom_platen_friction_coeff",
    "top_platen_friction_coeff", "self_contact_mode", "self_friction_coeff",
    "contact_formulation", "normal_contact_stiffness_factor", "penetration_tolerance_factor",
    "contact_damping_factor",
    "platen_penetration_tolerance_factor",
    "automatic_contact_parameters", "automatic_cores",
)
MATERIAL_FIELDS = (
    "material_name",
    "material_model",
    "material_density",
    "young_modulus",
    "poisson_ratio",
    "yield_stress",
    "tangent_modulus",
    "hardening_curve",
)


@dataclass(frozen=True)
class Settings:
    test_profile: str = "custom"
    specimen_architecture: str = "solid"
    core_fraction: float = 100.0
    size: float = 6.0
    cell_size: float = 3.0
    solid_fraction: float = 30.0
    mesh_resolution: float = 0.2
    mesh_type: str = "HEX8"
    local_mesh_size: float = 0.050
    compression: float = 0.6
    material_density: float = 7.85
    young_modulus: float = 200000.0
    poisson_ratio: float = 0.3
    yield_stress: float = 250.0
    tangent_modulus: float = 1000.0
    cores: int = DEFAULT_CORES
    substeps: int = 40
    minimum_substeps: int | None = None
    maximum_substeps: int | None = None
    material_name: str = "Custom material"
    material_model: str = "bilinear_isotropic"
    hardening_curve: tuple[tuple[float, float], ...] = ()
    structure_family: str = "tpms"
    structure_name: str = "Gyroid"
    tpms_morphology: str = "sheet"
    random_seed: int = 42
    solver_backend: str = "ansys_mapdl"
    bottom_platen_friction_coeff: float = 0.20
    top_platen_friction_coeff: float = 0.20
    self_contact_mode: str = "frictional"
    self_friction_coeff: float = 0.15
    contact_formulation: str = "augmented_lagrange"
    normal_contact_stiffness_factor: float = 1.0
    penetration_tolerance_factor: float = 0.10
    contact_damping_factor: float = 0.01
    tetra_strategy: str = "auto"
    loading_mode: str = "displacement"
    applied_force: float = 100.0
    bottom_platen_contact: str = "frictional"
    top_platen_contact: str = "frictional"
    platen_penetration_tolerance_factor: float = 0.0001
    automatic_contact_parameters: bool | None = None
    automatic_cores: bool | None = None
    kratos_linear_solver: str = "sparse_lu"
    kratos_contact_method: str = "alm"
    kratos_max_iterations: int = 30
    kratos_relative_tolerance: float = 1e-4
    kratos_absolute_tolerance: float = 1e-9
    kratos_line_search: bool = False
    kratos_penalty_factor: float = 10000.0
    truba_transport: str = "auto"
    truba_winscp_site: str = ""
    truba_partition: str = "debug"
    truba_account: str = ""
    truba_walltime: str = "00:30:00"
    truba_memory_mb: int = 4096
    imported_geometry_path: str = ""
    imported_geometry_name: str = ""

    @property
    def substep_limits(self) -> tuple[int, int]:
        """Preserve the automatic-step bounds of files saved before explicit limits."""
        minimum = self.substeps if self.minimum_substeps is None else self.minimum_substeps
        maximum = max(self.substeps * 100, 1000) if self.maximum_substeps is None else self.maximum_substeps
        return minimum, maximum

    @property
    def surface_resolution(self) -> float:
        """Automatic reference sampling, independent of all FE mesh settings.

        Resolve each period with at least 80 intervals. Sheets and porous walls
        become thin approximately in proportion to phase fraction; strut/network
        thickness scales approximately with its square root. Resolve both solid
        and void features, including small pores at high solid fractions.
        """
        phase = min(self.solid_fraction, 100 - self.solid_fraction) / 100
        walls = self.structure_family == "porous" or (
            self.structure_family == "tpms" and self.tpms_morphology == "sheet"
        )
        feature_scale = phase if walls else np.sqrt(phase)
        return self.cell_size / max(80, 12 / feature_scale)


def validate_geometry(settings: Settings) -> list[str]:
    from standard_tests import validate_profile, profile
    errors = validate_profile(settings)
    if settings.imported_geometry_path:
        if not isinstance(settings.imported_geometry_path, str) or not Path(settings.imported_geometry_path).is_file():
            errors.append('Imported geometry file is missing. Import the geometry again.')
        if settings.mesh_type not in TETRAHEDRAL_MESH_TYPES:
            errors.append('Imported geometry requires TET4 or TET10 elements.')
        if settings.tetra_strategy == 'periodic':
            errors.append('Imported geometry requires Auto or Netgen meshing.')
        for name in ('size', 'mesh_resolution', 'local_mesh_size'):
            if not is_finite_number(getattr(settings, name)) or getattr(settings, name) <= 0:
                errors.append(f'{name} must be positive and finite.')
        if not errors and settings.local_mesh_size > settings.mesh_resolution:
            errors.append('Local mesh size must not exceed the global FE mesh size.')
        return errors
    if (
        not isinstance(settings.structure_family, str)
        or settings.structure_family not in STRUCTURE_FAMILIES
    ):
        errors.append("Unsupported structure family.")
    else:
        _, resolved_name = resolve_family_and_name(
            settings.structure_family, settings.structure_name
        )
        valid_names = ALL_STRUCTURES_BY_FAMILY[settings.structure_family]
        if (
            not isinstance(settings.structure_name, str)
            or resolved_name not in valid_names
            or (
                settings.structure_name not in valid_names
                and resolved_name != settings.structure_name
            )
        ):
            errors.append(
                f"Unsupported structure '{settings.structure_name}' for family '{settings.structure_family}'."
            )
    if (
        not isinstance(settings.tpms_morphology, str)
        or settings.tpms_morphology not in TPMS_MORPHOLOGIES
    ):
        errors.append("Unsupported structure morphology.")
    if isinstance(settings.random_seed, bool) or not isinstance(settings.random_seed, int):
        errors.append("random_seed must be an integer.")
    if settings.mesh_type not in MESH_TYPES:
        errors.append("Unsupported FE mesh type.")
    if settings.tetra_strategy not in ("auto", "netgen", "periodic"):
        errors.append("Unsupported tetrahedral mesh strategy.")
    for name in (*NUMERIC_GEOMETRY_FIELDS, "mesh_resolution", "local_mesh_size"):
        value = getattr(settings, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
            errors.append(f"{name} must be a finite number.")
        elif value <= 0:
            errors.append(f"{name} must be positive.")
    if errors:
        return errors
    if profile(settings):
        p = profile(settings)
        if settings.mesh_type in TETRAHEDRAL_MESH_TYPES:
            if settings.local_mesh_size > settings.mesh_resolution:
                errors.append('Local mesh size must not exceed the global mesh size.')
        else:
            divisions = np.ceil(np.array(p.dimensions)/settings.mesh_resolution)
            if np.min(divisions) < 4 or np.prod(divisions) > MAX_FE_ELEMENTS*4:
                errors.append('Standard specimen needs at least four elements through its thickness and a bounded mesh. Adjust global mesh size.')
        if settings.solid_fraction >= 100:
            errors.append('Pore solid fraction must be below 100%; select Solid specimen for a solid coupon.')
        return errors
    if settings.solid_fraction >= 100:
        errors.append("Solid fraction must be between 0 and 100%.")
    if settings.cell_size > settings.size:
        errors.append("Period / characteristic size must not exceed cube size.")
    if settings.mesh_type not in TETRAHEDRAL_MESH_TYPES:
        n = int(np.ceil(settings.size / settings.mesh_resolution))
        if not 4 <= n <= MAX_SUBDIVISIONS:
            errors.append(f"FE mesh requires 4..{MAX_SUBDIVISIONS} subdivisions per axis.")
        count = round(n**3 * settings.solid_fraction / 100)
        if not 1 <= count <= MAX_FE_ELEMENTS:
            errors.append(f"FE mesh requires 1..{MAX_FE_ELEMENTS:,} solid elements.")
    elif settings.local_mesh_size > settings.mesh_resolution:
        errors.append(f"Local {settings.mesh_type} size must not exceed the global FE mesh size.")
    if errors:
        return errors
    surface_n = int(np.ceil(settings.size / settings.surface_resolution))
    if (surface_n + 3) ** 3 > MAX_SURFACE_SAMPLES:
        errors.append(
            "Preserving this geometry's details exceeds the surface sampling memory limit. "
            "Increase the period / pore scale relative to the cube size, or use a less extreme "
            "solid fraction. Changing FE mesh sizes does not change this limit."
        )
    return errors


def validate_settings(settings: Settings) -> list[str]:
    errors = validate_geometry(settings) + validate_material(settings)
    for name in ("automatic_contact_parameters", "automatic_cores"):
        if getattr(settings, name) is not None and not isinstance(getattr(settings, name), bool):
            errors.append(f"{name} must be a boolean or null.")
    if settings.loading_mode not in LOADING_MODES:
        errors.append("Unsupported loading mode.")
    if not is_finite_number(settings.applied_force) or settings.applied_force <= 0:
        errors.append("Applied compression force must be positive and finite (N).")
    for side in ("bottom", "top"):
        if getattr(settings, f"{side}_platen_contact") not in PLATEN_CONTACT_MODES:
            errors.append(f"Unsupported {side} platen contact mode.")
    if (
        not isinstance(settings.solver_backend, str)
        or settings.solver_backend not in SOLVER_BACKENDS
    ):
        errors.append("Unsupported solver backend.")
    for label, coefficient in (
        ("Bottom", settings.bottom_platen_friction_coeff),
        ("Top", settings.top_platen_friction_coeff),
    ):
        if not is_finite_number(coefficient) or not 0.0 <= coefficient <= 2.0:
            errors.append(f"{label} platen friction coefficient must be between 0 and 2.")
    if (
        not isinstance(settings.self_contact_mode, str)
        or settings.self_contact_mode not in SELF_CONTACT_MODES
    ):
        errors.append("Unsupported self-contact mode.")
    if (
        not is_finite_number(settings.self_friction_coeff)
        or not 0.0 <= settings.self_friction_coeff <= 2.0
    ):
        errors.append("Internal self-friction coefficient μ_self must be between 0 and 2.")
    if (
        not isinstance(settings.contact_formulation, str)
        or settings.contact_formulation not in CONTACT_FORMULATIONS
    ):
        errors.append("Unsupported contact formulation.")
    if (
        not is_finite_number(settings.normal_contact_stiffness_factor)
        or not 0.01 <= settings.normal_contact_stiffness_factor <= 10.0
    ):
        errors.append("Normal contact stiffness factor (FKN) must be between 0.01 and 10.")
    if (
        not is_finite_number(settings.penetration_tolerance_factor)
        or not 0.001 <= settings.penetration_tolerance_factor <= 0.5
    ):
        errors.append("Penetration tolerance factor (FTOLN) must be between 0.001 and 0.5.")
    if (
        not is_finite_number(settings.contact_damping_factor)
        or not 0.0 <= settings.contact_damping_factor <= 1.0
    ):
        errors.append("Contact damping factor must be between 0 and 1.")
    if (not is_finite_number(settings.platen_penetration_tolerance_factor)
            or not 1e-6 <= settings.platen_penetration_tolerance_factor <= .5):
        errors.append("Platen penetration tolerance factor must be between 0.000001 and 0.5.")
    if not is_finite_number(settings.compression) or settings.compression <= 0:
        errors.append("Compression must be a positive finite number.")
    elif (settings.loading_mode == "displacement" and is_finite_number(settings.size)
          and settings.compression >= settings.size):
        errors.append("Compression must be smaller than cube size.")
    valid_substeps = True
    for name in ("cores", "substeps", "minimum_substeps", "maximum_substeps"):
        value = getattr(settings, name)
        if value is None and name in ("minimum_substeps", "maximum_substeps"):
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            errors.append(f"{name} must be a positive integer.")
            if name != "cores":
                valid_substeps = False
    if valid_substeps:
        minimum, maximum = settings.substep_limits
        if not minimum <= settings.substeps <= maximum:
            errors.append("Substeps must satisfy minimum ≤ initial ≤ maximum.")
    return errors


def loading_target_reached(settings, compression, force, time=1.0):
    """A partial load history must never qualify as a completed mesh level."""
    actual, target = ((force, settings.applied_force) if settings.loading_mode == "force"
                      else (compression, settings.compression))
    return bool(np.isfinite(actual) and np.isfinite(time)
                and np.isclose(time, 1.0, rtol=1e-5, atol=1e-7)
                and np.isclose(actual, target, rtol=1e-5, atol=1e-7))


def is_finite_number(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and np.isfinite(value)


def validate_material(settings: Settings) -> list[str]:
    errors = []
    if not isinstance(settings.material_name, str) or not settings.material_name.strip():
        errors.append("Enter a material name.")
    if not isinstance(settings.material_model, str) or settings.material_model not in MATERIAL_MODELS:
        return errors + ["Unsupported material model."]
    if not is_finite_number(settings.material_density) or settings.material_density <= 0:
        errors.append("Material density must be positive and finite.")
    if not is_finite_number(settings.young_modulus) or settings.young_modulus <= 0:
        errors.append("Young's modulus must be positive and finite.")
    if not is_finite_number(settings.poisson_ratio) or not -1 < settings.poisson_ratio < 0.5:
        errors.append("Poisson ratio must be between -1 and 0.5.")
    if settings.material_model == "bilinear_isotropic":
        if not is_finite_number(settings.yield_stress) or settings.yield_stress <= 0:
            errors.append("Yield stress must be positive and finite.")
        if (
            not is_finite_number(settings.tangent_modulus)
            or not is_finite_number(settings.young_modulus)
            or not 0 <= settings.tangent_modulus < settings.young_modulus
        ):
            errors.append("Tangent modulus must be >= 0 and < Young's modulus.")
    if settings.material_model == "multilinear_isotropic":
        errors.extend(validate_hardening_curve(settings.hardening_curve))
    return errors


def validate_hardening_curve(curve) -> list[str]:
    if not isinstance(curve, (tuple, list)) or len(curve) < 2:
        return ["User curve needs at least two plastic-strain / true-stress points."]
    if any(
        not isinstance(row, (tuple, list))
        or len(row) != 2
        or not all(is_finite_number(value) for value in row)
        for row in curve
    ):
        return ["Every curve row needs two finite numbers: plastic strain, true stress."]
    values = np.asarray(curve, dtype=float)
    if values[0, 0] != 0 or np.any(np.diff(values[:, 0]) <= 0) or np.any(values[:, 1] <= 0):
        return ["Curve must start at zero plastic strain, with increasing strain and positive stress."]
    if np.any(np.diff(values[:, 1]) < 0):
        return ["This isotropic hardening model requires nondecreasing stress; softening is unsupported."]
    return []


def plastic_curve_from_total_true_strain(curve, young_modulus):
    """Convert a yield-onward uniaxial true curve to the MISO input convention.

    Assumes small elastic strain: epsilon_pl = epsilon_true - sigma_true / E.
    Do not shift the curve to hide an offset yield definition or elastic rows.
    """
    if not is_finite_number(young_modulus) or young_modulus <= 0:
        raise ValueError("Young's modulus must be positive and finite.")
    if not isinstance(curve, (list, tuple)) or len(curve) < 2 or any(
        not isinstance(row, (list, tuple)) or len(row) != 2
        or not all(is_finite_number(value) for value in row) for row in curve
    ):
        raise ValueError("True curve needs at least two finite strain / stress pairs.")
    values = np.asarray(curve, dtype=float)
    if np.any(values[:, 0] < 0) or np.any(np.diff(values[:, 0]) <= 0):
        raise ValueError("Total true strain must be nonnegative and strictly increasing.")
    plastic = values[:, 0] - values[:, 1] / young_modulus
    # Only absorb floating-point roundoff at the yield point, not measured offsets.
    tolerance = 1e-12
    if abs(plastic[0]) > tolerance:
        raise ValueError(
            "Start the total true-strain curve at initial yield (strain = stress / E). "
            "Exclude elastic rows; offset yield data needs an explicit material calibration."
        )
    plastic[0] = 0.0
    points = tuple((float(strain), float(stress)) for strain, stress in zip(plastic, values[:, 1]))
    errors = validate_hardening_curve(points)
    if errors:
        raise ValueError("\n".join(errors))
    return points


def require_geometry(settings: Settings) -> None:
    errors = validate_geometry(settings)
    if errors:
        raise ValueError("\n".join(errors))


def require_settings(settings: Settings) -> None:
    errors = validate_settings(settings)
    if errors:
        raise ValueError("\n".join(errors))


@dataclass
class FEMesh:
    grid: pv.UnstructuredGrid
    spacing: float
    subdivisions: int | None
    fraction: float
    components: int
    bottom_ids: np.ndarray
    top_ids: np.ndarray
    removed_grid: pv.UnstructuredGrid
    original_components: int
    candidate_elements: int

    @property
    def solve_errors(self) -> list[str]:
        errors = []
        if self.components != 1:
            errors.append(f"FE solid has {self.components} face-connected components. Refine the mesh.")
        if not self.bottom_ids.size or not self.top_ids.size:
            errors.append("FE solid must intersect both top and bottom faces.")
        return errors


def load_settings(path: Path = SETTINGS_FILE) -> Settings:
    if not path.exists():
        return Settings()
    settings = settings_from_dict(json.loads(path.read_text(encoding="utf-8")))
    require_settings(settings)
    return settings


def settings_from_dict(saved: dict) -> Settings:
    """Decode saved settings, including the legacy platen-friction format."""
    if not isinstance(saved, dict):
        raise ValueError("settings.json must contain a JSON object.")
    values = asdict(Settings())
    values.update({key: value for key, value in saved.items() if key in values})
    legacy_mu = saved.get("platen_friction_coeff", Settings().bottom_platen_friction_coeff)
    if saved.get("platen_mode") == "frictionless_both":
        legacy_mu = 0.0
    for key in ("bottom_platen_friction_coeff", "top_platen_friction_coeff"):
        if key not in saved:
            values[key] = legacy_mu
    curve = values["hardening_curve"]
    if isinstance(curve, (tuple, list)) and all(isinstance(row, (tuple, list)) for row in curve):
        values["hardening_curve"] = tuple(tuple(row) for row in curve)
    return Settings(**values)


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def save_settings(settings: Settings, path: Path = SETTINGS_FILE) -> None:
    require_settings(settings)
    temporary = path.with_suffix(".json.tmp")
    write_json(temporary, asdict(settings))
    temporary.replace(path)


def save_material(settings: Settings, path: Path) -> None:
    errors = validate_material(settings)
    if errors:
        raise ValueError("\n".join(errors))
    material = {name: getattr(settings, name) for name in MATERIAL_FIELDS}
    write_json(
        path,
        {
            "schema_version": 1,
            "units": "mm-N-MPa",
            "material": material,
        },
    )


def load_material(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != 1
        or data.get("units") != "mm-N-MPa"
    ):
        raise ValueError("Expected a RustMech material file with mm-N-MPa units.")
    material = data.get("material")
    if not isinstance(material, dict) or not all(
        name in material
        for name in ("material_name", "material_model", "young_modulus", "poisson_ratio")
    ):
        raise ValueError("Material name, model, Young's modulus and Poisson ratio are required.")
    required = {
        "bilinear_isotropic": ("yield_stress", "tangent_modulus"),
        "multilinear_isotropic": ("hardening_curve",),
    }
    selected = material.get("material_model")
    if not isinstance(selected, str):
        raise ValueError("Material model must be a supported model name.")
    if any(name not in material for name in required.get(selected, ())):
        raise ValueError("Material file is missing parameters required by its selected model.")
    values = {name: getattr(Settings(), name) for name in MATERIAL_FIELDS}
    values.update({name: value for name, value in material.items() if name in MATERIAL_FIELDS})
    errors = validate_material(Settings(**values))
    if errors:
        raise ValueError("\n".join(errors))
    curve = values["hardening_curve"]
    if not isinstance(curve, (list, tuple)) or any(not isinstance(row, (list, tuple)) for row in curve):
        raise ValueError("Material curve must be an array of strain / stress pairs.")
    values["hardening_curve"] = tuple(tuple(row) for row in curve)
    return values
