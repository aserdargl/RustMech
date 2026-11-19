"""Common solver boundary for solid meshes and normalized static results.

Adapters accept mm/N/MPa, a supported material law and prescribed-Z displacement or force.
They return global XYZ displacement, signed platen forces and nodal von Mises
stress in the requested node order. No engine-specific reader reaches the UI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np
from model import FEMesh, MESH_TYPES, Settings


KRATOS_QUADRATIC_MESH_TYPES = ("HEX20", "TET10")


def kratos_quadratic_platen_errors(settings: Settings) -> list[str]:
    from standard_tests import kind

    if settings.mesh_type not in KRATOS_QUADRATIC_MESH_TYPES or kind(settings) != "compression":
        return []
    errors = []
    if settings.kratos_contact_method != "penalty":
        errors.append("Kratos HEX20 and TET10 platen contact requires penalty mortar.")
    if settings.loading_mode != "displacement":
        errors.append("Kratos HEX20 and TET10 compression requires displacement control.")
    return errors


@dataclass(frozen=True)
class SolverCapabilities:
    identifier: str
    display_name: str
    default_cores: int
    max_cores: int
    core_limit_description: str
    supported_material_models: tuple[str, ...] = (
        "linear_elastic",
        "bilinear_isotropic",
        "multilinear_isotropic",
    )
    implemented: bool = True
    unavailable_reason: str = ""
    ui_hint: str = ""
    supported_mesh_types: tuple[str, ...] = ("HEX8", "HEX20", "TET4", "TET10")

    def validate(self, settings: Settings) -> list[str]:
        errors = []
        from standard_tests import kind
        if kind(settings) != 'compression' and self.identifier not in ('ansys_mapdl','kratos'):
            errors.append('Standard mechanical test fixtures require ANSYS MAPDL or Kratos.')
        if not self.implemented:
            errors.append(
                self.unavailable_reason
                or f"{self.display_name} solver backend is not active yet."
            )
        if isinstance(settings.cores, bool) or not isinstance(settings.cores, int):
            return errors + ["CPU cores must be an integer."]
        if not 1 <= settings.cores <= self.max_cores:
            errors.append(
                f"{self.display_name} allows 1–{self.max_cores} CPU cores. "
                + self.core_limit_description
            )
        if settings.material_model not in self.supported_material_models:
            errors.append(
                f"{self.display_name} does not support material model {settings.material_model}."
            )
        if settings.mesh_type not in self.supported_mesh_types:
            errors.append(f"{self.display_name} configuration supports: {', '.join(self.supported_mesh_types)}.")
        if self.identifier == "kratos":
            errors.extend(kratos_quadratic_platen_errors(settings))
            if kind(settings)=='flexure' and settings.mesh_type!='HEX8':
                errors.append('Kratos three-point flexure requires HEX8 with aligned support/loading lines.')
            if not isinstance(settings.kratos_line_search, bool):
                errors.append("Kratos line search must be a boolean.")
            elif settings.kratos_line_search:
                errors.append("Line search is not qualified with the current Kratos contact strategy. Use Newton-Raphson.")
            if settings.kratos_linear_solver not in ("sparse_lu", "amgcl", "skyline_lu_factorization"):
                errors.append("Select a supported Kratos linear solver.")
            if settings.kratos_linear_solver == "amgcl" and settings.kratos_contact_method == "alm":
                errors.append("Use Sparse LU for ALM contact, or choose penalty contact with AMGCL.")
            if settings.self_contact_mode not in ('disabled','frictionless','frictional'):
                errors.append('Select disabled, frictionless or Coulomb self-contact.')
            elif settings.self_contact_mode!='disabled' and settings.kratos_contact_method!='penalty':
                errors.append('Kratos self-contact requires penalty mortar.')
            if settings.self_contact_mode!='disabled' and settings.mesh_type!='HEX8':
                errors.append('Kratos self-contact requires HEX8.')
            if (isinstance(settings.kratos_penalty_factor,bool) or not isinstance(settings.kratos_penalty_factor,(int,float))
                    or not np.isfinite(settings.kratos_penalty_factor) or not 1<=settings.kratos_penalty_factor<=1e8):
                errors.append('Kratos penalty factor must be between 1 and 100000000.')
            try:
                from truba_jobs import shell_job_settings
                shell_job_settings(settings,settings.truba_account or "account")
            except (TypeError,ValueError) as error:
                errors.append(str(error))
            if settings.kratos_contact_method not in ("alm", "penalty"):
                errors.append("Select a supported Kratos contact method.")
            if (isinstance(settings.kratos_max_iterations, bool)
                    or not isinstance(settings.kratos_max_iterations, int)
                    or not 1 <= settings.kratos_max_iterations <= 1000):
                errors.append("Kratos Newton iterations must be between 1 and 1000.")
            for name in ("kratos_relative_tolerance", "kratos_absolute_tolerance"):
                value = getattr(settings, name)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or not 0 < value <= 1:
                    errors.append(f"{name} must be positive and at most 1.")
            if kind(settings)=='tension':
                if settings.bottom_platen_contact!='bonded' or settings.top_platen_contact!='bonded':
                    errors.append('The tensile profile requires bonded end grips.')
            elif (settings.bottom_platen_contact not in ("frictional", "frictionless")
                    or settings.top_platen_contact not in ("frictional", "frictionless")
                    or settings.self_contact_mode == "rough"):
                errors.append("Kratos contact configuration currently supports frictional or frictionless contact.")
        return errors


MAPDL_CAPABILITIES = SolverCapabilities(
    identifier="ansys_mapdl",
    display_name="ANSYS MAPDL",
    default_cores=4,
    max_cores=4,
    core_limit_description="Your configured ANSYS license limit is 4 cores.",
    implemented=True,
    ui_hint="Active · HEX8 / HEX20 / TET4 / TET10 · PyMAPDL SMP",
)

OPENFOAM_CAPABILITIES = SolverCapabilities(
    identifier="openfoam",
    display_name="OpenFOAM",
    default_cores=4,
    max_cores=64,
    core_limit_description="OpenFOAM MPI domain decomposition cores.",
    implemented=False,
    unavailable_reason=(
        "OpenFOAM (solid4Foam) backend slot is prepared for future integration "
        "and is currently disabled. Select ANSYS MAPDL to run analysis."
    ),
    ui_hint="Placeholder (Inactive) · solid4Foam / polyMesh HEX8 slot",
)

KRATOS_CAPABILITIES = SolverCapabilities(
    identifier="kratos",
    display_name="Kratos Multiphysics",
    default_cores=8,
    max_cores=224,
    core_limit_description="Kratos StructuralMechanicsApplication parallel threads.",
    implemented=True,
    unavailable_reason=(
        ""
    ),
    ui_hint="TRUBA · nonlinear static · HEX8 / HEX20 / TET4 / TET10 · OpenMP",
    supported_mesh_types=MESH_TYPES,
)

SOLVER_REGISTRY: dict[str, SolverCapabilities] = {
    MAPDL_CAPABILITIES.identifier: MAPDL_CAPABILITIES,
    OPENFOAM_CAPABILITIES.identifier: OPENFOAM_CAPABILITIES,
    KRATOS_CAPABILITIES.identifier: KRATOS_CAPABILITIES,
}


def get_solver_capabilities(identifier: str) -> SolverCapabilities:
    return SOLVER_REGISTRY.get(identifier, MAPDL_CAPABILITIES)


def create_solver_backend(identifier: str = "ansys_mapdl", remote_auth=None, job_marker=None) -> SolverBackend:
    if identifier == "ansys_mapdl":
        from mapdl_backend import create_mapdl_backend

        return create_mapdl_backend()
    if identifier == "openfoam":
        from openfoam_backend import create_openfoam_backend

        return create_openfoam_backend()
    if identifier == "kratos":
        from kratos_backend import create_kratos_backend

        return create_kratos_backend(remote_auth,job_marker)
    raise ValueError(f"Unsupported solver backend: {identifier}")


@dataclass(frozen=True)
class AnalysisRequest:
    settings: Settings
    mesh: FEMesh
    run_directory: Path


class SolutionReader(Protocol):
    """Lazy result access: loading all substeps into memory is unnecessary."""

    @property
    def nsets(self) -> int: ...

    @property
    def time_values(self) -> np.ndarray: ...

    @property
    def stress_definition(self) -> str: ...

    def displacement(self, index: int, target_ids: np.ndarray) -> np.ndarray: ...

    def equivalent_stress(self, index: int, target_ids: np.ndarray) -> np.ndarray: ...

    def top_platen_displacement(self, index: int) -> float: ...

    def platen_reactions(self, index: int) -> tuple[float, float]:
        """Bottom RFZ, top RFZ (or prescribed FZ for a force-driven pilot)."""
        ...


@dataclass
class SolverExecution:
    results: SolutionReader | None = None
    error: str | None = None
    solve_attempted: bool = False
    diagnostics: dict = field(default_factory=dict)


class SolverBackend(Protocol):
    """Implement this boundary to add another real solver adapter."""

    @property
    def capabilities(self) -> SolverCapabilities: ...

    def solve(self, request: AnalysisRequest) -> SolverExecution: ...


def map_node_values(source_ids, values, target_ids) -> np.ndarray:
    from rust_mesh import map_indices
    source = np.asarray(source_ids, dtype=np.int64).ravel()
    target = np.asarray(target_ids, dtype=np.int64).ravel()
    values = np.asarray(values)
    if values.ndim == 0 or values.shape[0] != len(source):
        raise ValueError("Invalid or duplicate result node IDs.")
    mapped = values[map_indices(source, target)]
    if not np.all(np.isfinite(mapped)):
        raise ValueError("Mapped nodal results contain non-finite values.")
    return mapped
