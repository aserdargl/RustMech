"""The supported compute-node operations, shared by submission and execution.

Geometry work is independent of the selected mechanical solver. Only mechanics
requires the qualified Kratos adapter; MAPDL is deliberately excluded.
"""

OPERATIONS = {
    "probe": "mesh", "prepare": "mesh",
    "preview": "mesh", "surface": "mesh", "doe_mesh": "mesh",
    "mechanical": "kratos", "convergence_level": "kratos", "doe_solve": "kratos",
    "fft_solve": "damask", "peridynamic_solve": "peridynamics",
    "flow_export": "mesh", "flow_solve": "flow",
    "thermal_export": "mesh", "thermal_solve": "thermal",
    "mpm_solve": "mpm",
    "learning_train": "learning",
}

ANALYSIS_OPERATIONS = frozenset({
    "learning_train",
    "mpm_solve",
    "mechanical", "fft_solve", "peridynamic_solve", "flow_solve", "thermal_solve", "doe_solve", "convergence_level",
})


def required_capability(request):
    operation = request.get("operation")
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise ValueError(f"Unsupported TRUBA operation: {operation!r}.")
    capability = OPERATIONS[operation]
    if capability == "kratos":
        settings = request.get("settings", {})
        if settings.get("solver_backend") != "kratos":
            raise ValueError("TRUBA mechanical analyses require Kratos. ANSYS MAPDL is excluded.")
        if settings.get("test_profile") in ("astm_e8_subsize", "iso178_preferred"):
            raise ValueError("Tension and three-point flexure currently require ANSYS MAPDL; their TRUBA solve adapters are not implemented. Geometry and mesh previews remain available.")
    return capability
