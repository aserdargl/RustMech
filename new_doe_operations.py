"""Operation inputs and solver dispatch for independent factorial experiments."""
from dataclasses import asdict
from itertools import product
from math import ceil, floor, prod
from pathlib import Path

import numpy as np

from model import GEOMETRY_FIELDS, settings_from_dict
from structure_library import ALL_STRUCTURES_BY_FAMILY, STRUCTURE_FAMILIES

OPERATIONS = {
    # Match the main workspace's method menu, then list the flow workspaces.
    "FEM": "mechanical",
    "FFT": "fft_solve",
    "MPM": "mpm_solve",
    "Peridynamics": "peridynamic_solve",
    "Flow (CFD)": "flow_solve",
    "Thermal (CHT)": "thermal_solve",
}
MAX_ROWS = 10000
FAMILY_PATH = ("settings", "structure_family")
TYPE_PATH = ("settings", "structure_name")
CELL_SIZE_PATH = ("settings", "cell_size")
OUTER_SIZE_PATHS = tuple(("settings", key) for key in ("size_x", "size_y", "size"))


def snapshot(host, operation):
    """Read the same committed settings the corresponding workspace submits."""
    settings = host.applied_structure_settings()
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
        keys = ("structure_family", "structure_name", "tpms_morphology", "size", "cell_size", "solid_fraction",
                "material_name", "material_card", "young_modulus", "poisson_ratio")
    elif op == "peridynamic_solve":
        keys = ("structure_family", "structure_name", "tpms_morphology", "size", "cell_size", "solid_fraction",
                "young_modulus")
    elif op == "mpm_solve":
        keys = ("structure_family", "structure_name", "tpms_morphology", "size", "cell_size", "solid_fraction")
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
        yield ("settings", key), request["settings"][key]


def structure_families(operation):
    families = {"fft_solve": ("tpms", "lattice"), "peridynamic_solve": ("tpms",), "mpm_solve": ("tpms",)}
    return families.get(operation, ALL_STRUCTURES_BY_FAMILY)


def structure_choices(operation):
    return [(f"{family.upper()} / {name}", name)
            for family in structure_families(operation)
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


def structure_levels(base, dimensions):
    settings = base["settings"]
    families = dimensions.get(FAMILY_PATH, [settings["structure_family"]])
    names = dimensions.get(TYPE_PATH, [settings["structure_name"]])
    if len(families) == 1:
        # Existing Type sweeps may span families even without a family sweep.
        return [(next((family for family, members in ALL_STRUCTURES_BY_FAMILY.items() if name in members),
                      families[0]), name) for name in names]
    pairs = []
    for family in families:
        members = ALL_STRUCTURES_BY_FAMILY[family]
        matching = [name for name in names if name in members]
        if not matching:
            if len(names) > 1:
                raise ValueError(f"Select a structure type for {STRUCTURE_FAMILIES[family]}.")
            matching = [members[0]]
        pairs.extend((family, name) for name in matching)
    return pairs


def matrix(base, dimensions):
    geometry = structure_levels(base, dimensions)
    # Keep the structure's position in the existing factorial sample order.
    paths = [path for path in dimensions if path != FAMILY_PATH]
    if TYPE_PATH not in paths:
        paths.append(TYPE_PATH)
    levels = [geometry if path == TYPE_PATH else dimensions[path] for path in paths]
    if prod(map(len, levels)) > MAX_ROWS:
        raise ValueError(f"Reduce the number of levels to at most {MAX_ROWS:,} experiments.")
    for combination in product(*levels):
        # Each row owns its input groups. Material tables inside them are
        # immutable snapshots and can be shared across a large matrix.
        request = {key: value.copy() if isinstance(value, dict) else value
                   for key, value in base.items()}
        for path, value in zip(paths, combination):
            if path == TYPE_PATH:
                request["settings"]["structure_family"], request["settings"]["structure_name"] = value
            else:
                target = request if len(path) == 1 else request[path[0]]
                target[path[-1]] = value
        yield request


def _lhs_samples(
    n_samples: int,
    ranges: list[tuple[float, float]],
    seed: int = 42,
) -> np.ndarray:
    """Generate Latin Hypercube Samples in [low, high] for each dimension."""
    rng = np.random.default_rng(int(seed))
    dims = len(ranges)
    result = np.zeros((n_samples, dims), dtype=float)
    for d, (low, high) in enumerate(ranges):
        if low == high:
            result[:, d] = low
            continue
        cut = np.linspace(0.0, 1.0, n_samples + 1)
        u = rng.uniform(low=cut[:-1], high=cut[1:], size=n_samples)
        rng.shuffle(u)
        result[:, d] = low + u * (high - low)
    return result


def _fixed_outer_fft_cell_sizes(base, dimensions, low, high):
    from damask_fft import whole_fft_periods

    x, y, z = (dimensions.get(path, [base["settings"][path[-1]]])[0] for path in OUTER_SIZE_PATHS)
    if x is None and y is None:
        return None  # Legacy requests with implicit outer sizes always sample one cell.
    lengths = [z if length is None else length for length in (x, y, z)]
    shortest = min(lengths)
    first = max(1, ceil(np.nextafter(shortest / high, -np.inf)))
    last = floor(np.nextafter(shortest / low, np.inf))
    candidates = []
    for start in range(first, last + 1, 65536):
        counts = np.arange(start, min(start + 65536, last + 1))
        # Clipping keeps an exact UI bound when dividing introduces a one-ULP difference.
        sizes = np.clip(shortest / counts, low, high)
        valid = whole_fft_periods(np.asarray(lengths)[:, None] / sizes).all(axis=0)
        candidates.append(sizes[valid][::-1])
    if not any(part.size for part in candidates):
        raise ValueError(f"No periodic FFT cell size in {low:g}–{high:g} mm fits "
                         f"outer X/Y/Z = {lengths[0]:g}/{lengths[1]:g}/{lengths[2]:g} mm.")
    return np.concatenate(candidates[::-1])


def lhs_matrix(base, dimensions, count, seed):
    """Stratify numeric inputs within each selected structure."""
    varying = {path: values for path, values in dimensions.items() if len(values) == 2
               and path not in (FAMILY_PATH, TYPE_PATH)}
    if not varying:
        yield from matrix(base, dimensions)
        return
    geometry = structure_levels(base, dimensions)
    if count * len(geometry) > MAX_ROWS:
        raise ValueError(f"Reduce the number of levels to at most {MAX_ROWS:,} experiments.")
    allowed_cells = None
    if (base["operation"] == "fft_solve" and CELL_SIZE_PATH in varying
            and not any(path in varying for path in OUTER_SIZE_PATHS)):
        allowed_cells = _fixed_outer_fft_cell_sizes(base, dimensions, *varying[CELL_SIZE_PATH])
        if allowed_cells is not None:
            varying[CELL_SIZE_PATH] = (0, len(allowed_cells) - 1)
    limits = [(low, high + 1 if type(low) is int else high) for low, high in varying.values()]
    samples = _lhs_samples(count, limits, seed)
    for family, name in geometry:
        # Use the same sampled values for each structure so results are comparable.
        levels = dimensions.copy()
        levels[FAMILY_PATH], levels[TYPE_PATH] = [family], [name]
        for sample in samples:
            for (path, (low, high)), value in zip(varying.items(), sample):
                value = min(high, floor(value)) if type(low) is int else float(value)
                if path == CELL_SIZE_PATH and allowed_cells is not None:
                    value = float(allowed_cells[value])
                levels[path] = [value]
            yield next(matrix(base, levels))


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


def matrix_request(rows):
    from remote_operations import required_capability
    operation = rows[0]["operation"]
    for row in rows:
        if row["operation"] != operation:
            raise ValueError("All experiments in the matrix must use the same operation.")
        required_capability(row)
    largest = max(rows, key=lambda row: row["settings"]["cores"])
    return dict(operation=operation, settings=largest["settings"], experiment_rows=rows)


def launch(request, directory, controller):
    from analysis_process import AnalysisProcess
    if controller is not None:
        from remote_operations import required_capability
        from truba_remote import run_remote_operation, submit_remote_analysis
        capability = required_capability(request)
        context, auth = controller.require_analysis(capability, settings_from_dict(request["settings"]), explicit_target=True)
        target = submit_remote_analysis if "experiment_index" in request else run_remote_operation
        return AnalysisProcess(target, request, context, remote_auth=auth,
                               remote_options={"directory": Path(directory) / "truba"})
    if request["operation"] == "fft_solve":
        from damask_fft import DamaskAnalysisProcess
        return DamaskAnalysisProcess(settings_from_dict(request["settings"]), request["grid"],
                                     request["void_ratio"], request["distribution"], folder=Path(directory))
    return AnalysisProcess(run_local, request, directory)
