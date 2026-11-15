"""OpenFOAM (solid4Foam) solver backend placeholder.

Prepared architecture slot for exporting exact voxel HEX8 grids to OpenFOAM
polyMesh format (points, faces, owner, neighbour, boundary patches for bottom
fixed and top prescribed-Z displacement), writing mechanicalProperties /
solidProperties dictionaries, and adapting field histories back to
SolutionReader. Currently inactive by design.
"""
from __future__ import annotations

from pathlib import Path

from model import FEMesh, Settings
from solver_api import (
    OPENFOAM_CAPABILITIES,
    AnalysisRequest,
    SolverCapabilities,
    SolverExecution,
)


def write_openfoam_case_skeleton(
    mesh: FEMesh, settings: Settings, case_directory: Path
) -> None:
    """Placeholder hook for writing constant/polyMesh, 0/D, and system/controlDict."""
    raise NotImplementedError(OPENFOAM_CAPABILITIES.unavailable_reason)


class OpenFoamBackend:
    """Placeholder OpenFOAM adapter conforming to SolverBackend."""

    def __init__(self, capabilities: SolverCapabilities = OPENFOAM_CAPABILITIES):
        self.capabilities = capabilities

    def solve(self, request: AnalysisRequest) -> SolverExecution:
        return SolverExecution(
            results=None,
            error=self.capabilities.unavailable_reason,
            solve_attempted=False,
            diagnostics={
                "solver": self.capabilities.identifier,
                "status": "placeholder_inactive",
            },
        )


def create_openfoam_backend() -> OpenFoamBackend:
    return OpenFoamBackend()
