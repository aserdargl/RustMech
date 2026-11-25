"""Operation inputs and solver dispatch for independent factorial experiments."""
from dataclasses import asdict
from itertools import product
from math import prod
from pathlib import Path

from model import GEOMETRY_FIELDS, settings_from_dict
from structure_library import ALL_STRUCTURES_BY_FAMILY

OPERATIONS = {
    "FFT": "fft_solve", "FEM": "mechanical", "Flow (CFD)": "flow_solve",
    "Thermal (CHT)": "thermal_solve", "Peridynamics": "peridynamic_solve", "MPM": "mpm_solve",
}
MAX_ROWS = 10000


def snapshot(host, operation):
    """Read the same committed settings the corresponding workspace submits."""
    settings = host.collect()
    request = {"operation": operation}
    if operation == "fft_solve":
        settings = host._fft_settings(settings)
        request.update(grid=host.fft_grid.value(), void_ratio=host.fft_void_ratio.value(),
                       distribution=host.fft_distribution.text())
    elif operation == "peridynamic_solve":
        settings, options = host.peridynamic_settings.inputs()
        request["options"] = asdict(options)
    elif operation in ("flow_solve", "thermal_solve"):
        workspace = host.flow_workspace if operation == "flow_solve" else host.thermal_workspace
        request["flow"] = asdict(workspace.collect())
        if operation == "thermal_solve":
            request["thermal"] = asdict(workspace.collect_thermal())
    elif operation == "mpm_solve":
        request["mpm"] = asdict(host.mpm_workspace.collect())
    request["settings"] = asdict(settings)
    if not settings.imported_geometry_path and settings.test_profile == "custom":
        request["settings"].update(size_x=settings.dimensions[0], size_y=settings.dimensions[1])
    return request


def parameters(request):
    op = request["operation"]
    keys = [key for key in GEOMETRY_FIELDS if key not in
            ("mesh_resolution", "local_mesh_size", "mesh_type", "tetra_strategy")]
    if op == "mechanical":
        keys = [key for key in request["settings"] if not key.startswith("truba_")]
    elif op == "fft_solve":
        keys = ("structure_name", "tpms_morphology", "size", "cell_size", "solid_fraction",
                "material_name", "material_card", "young_modulus", "poisson_ratio")
    elif op == "peridynamic_solve":
        keys = ("structure_name", "tpms_morphology", "size", "cell_size", "solid_fraction", "young_modulus")
    elif op == "mpm_solve":
        keys = ("structure_name", "tpms_morphology", "size", "cell_size", "solid_fraction")
    for key, value in request.items():
        if key in ("settings", "operation"):
            continue
        if isinstance(value, dict):
            for name, field in value.items():
                yield (key, name), field
        else:
            yield (key,), value
    for key in keys:
        if key in ("size_x", "size_y"):
            continue
        if key == "size":
            geometry = request["settings"]
            if not geometry.get("imported_geometry_path") and geometry.get("test_profile", "custom") == "custom":
                for axis in ("size_x", "size_y", "size"):
                    length = geometry.get(axis)
                    yield ("settings", axis), float(geometry["size"] if length is None else length)
                continue
        if key != "structure_family":  # The structure selector includes its family.
            yield ("settings", key), request["settings"][key]


def structure_choices(operation):
    families = {"fft_solve": ("tpms", "lattice"), "peridynamic_solve": ("tpms",), "mpm_solve": ("tpms",)}
    return [(f"{family.upper()} / {name}", name)
            for family in families.get(operation, ALL_STRUCTURES_BY_FAMILY)
            for name in ALL_STRUCTURES_BY_FAMILY[family]]


def explicit_numbers(text, number_type, minimum, maximum):
    # A semicolon also allows decimal commas, e.g. "0,5; 1,25".
    tokens = text.replace(",", ".").split(";") if ";" in text else text.split(",")
    try:
        values = [number_type(token.strip()) for token in tokens]
    except ValueError:
        raise ValueError("Enter whole numbers." if number_type is int else "Enter numeric values.") from None
    if any(not minimum <= value <= maximum for value in values):
        raise ValueError(f"Enter values between {minimum:g} and {maximum:g}.")
    if len(set(values)) != len(values):
        raise ValueError("Each level must be distinct.")
    return values


def matrix(base, dimensions):
    if prod(len(values) for values in dimensions.values()) > MAX_ROWS:
        raise ValueError(f"Reduce the number of levels to at most {MAX_ROWS:,} experiments.")
    for combination in product(*dimensions.values()):
        # Each row owns its input groups. Material tables inside them are
        # immutable snapshots and can be shared across a large matrix.
        request = {key: value.copy() if isinstance(value, dict) else value
                   for key, value in base.items()}
        for path, value in zip(dimensions, combination):
            target = request if len(path) == 1 else request[path[0]]
            target[path[-1]] = value
        settings = request["settings"]
        for family, names in ALL_STRUCTURES_BY_FAMILY.items():
            if settings["structure_name"] in names:
                settings["structure_family"] = family
                break
        yield request


def validate(request):
    from model import require_settings
    op = request["operation"]
    settings = settings_from_dict(request["settings"])
    if op == "mechanical":
        require_settings(settings)
    elif op == "fft_solve":
        from damask_fft import MIN_GRID, validate_periodic_fft
        errors = validate_periodic_fft(settings)
        if request["grid"] < MIN_GRID or not 0 < request["void_ratio"] < 1:
            errors.append("Invalid FFT grid or void stiffness ratio.")
        if errors:
            raise ValueError("\n".join(errors))
    elif op == "peridynamic_solve":
        from peridynamic_analysis import CompressionOptions, validate_compression
        errors = validate_compression(settings, CompressionOptions(**request["options"]))
        if errors:
            raise ValueError("\n".join(errors))
    elif op == "mpm_solve":
        from mpm_analysis import MPMSettings
        MPMSettings(**request["mpm"]).validate(settings)
    else:
        from flow_analysis import FlowSettings
        flow = FlowSettings(**request["flow"])
        if op == "thermal_solve":
            from thermal_analysis import ThermalSettings
            ThermalSettings(**request["thermal"]).validate(settings, flow)
        else:
            flow.validate(settings)


def run_local(request, directory):
    settings = settings_from_dict(request["settings"])
    op = request["operation"]
    if op == "mechanical":
        from mechanical_analysis import run_batch_experiment
        from mesh_engine import generate_volume_mesh
        return run_batch_experiment(settings, generate_volume_mesh(settings),
                                    output_directory=directory)["run_folder"]
    if op == "peridynamic_solve":
        from peridynamic_analysis import CompressionOptions, run_compression
        return run_compression(settings, CompressionOptions(**request["options"]), directory)
    if op == "mpm_solve":
        from mpm_analysis import MPMSettings, run_local_mpm
        return run_local_mpm(settings, MPMSettings(**request["mpm"]), directory)
    from flow_analysis import FlowSettings, execute_flow_case
    flow = FlowSettings(**request["flow"])
    if op == "thermal_solve":
        from thermal_analysis import ThermalSettings, execute_thermal_case
        execute_thermal_case(settings, flow, ThermalSettings(**request["thermal"]), directory)
    else:
        execute_flow_case(settings, flow, directory)
    return str(directory)


def launch(request, directory, controller):
    from analysis_process import AnalysisProcess
    if controller is not None:
        from remote_operations import required_capability
        from truba_remote import run_remote_operation
        capability = required_capability(request)
        context, auth = controller.require_analysis(capability, settings_from_dict(request["settings"]))
        return AnalysisProcess(run_remote_operation, request, context, remote_auth=auth,
                               remote_options={"directory": Path(directory) / "truba"})
    if request["operation"] == "fft_solve":
        from damask_fft import DamaskAnalysisProcess
        return DamaskAnalysisProcess(settings_from_dict(request["settings"]), request["grid"],
                                     request["void_ratio"], request["distribution"], folder=Path(directory))
    return AnalysisProcess(run_local, request, directory)
