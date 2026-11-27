"""Quasi-static compression of TPMS particles with a prototype microelastic brittle model."""

from dataclasses import asdict, dataclass, replace
from datetime import datetime
import csv
from math import ceil, nextafter
from pathlib import Path
from uuid import uuid4

import numpy as np
from scipy.optimize import minimize
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from analysis_process import report_analysis_step
from model import ROOT, is_finite_number, validate_geometry, write_json
from structure_library import TPMS_FUNCTIONS, TPMS_MORPHOLOGIES, evaluate_priority_field


POISSON_RATIO = 0.25
MAX_PAIRS = 2_000_000
PLATEN_STIFFNESS = 2.0
CONTACT_STIFFNESS = 2.0
CONTACT_DISTANCE = 0.9
CONTACT_SKIN = 0.3
MAX_DAMAGE_ROUNDS = 100
MIN_PARTICLE_INTERVALS = 12
MAX_PARTICLE_INTERVALS = 72


@dataclass(frozen=True)
class CompressionOptions:
    resolution: int = 16
    horizon_ratio: float = 3.0
    critical_stretch_percent: float = 1.5
    compression_percent: float = 12.0
    increments: int = 16


def minimum_particle_resolution(settings):
    """Return the Z-axis intervals needed for eight intervals per TPMS period."""
    size, period = settings.size, settings.cell_size
    if not all(is_finite_number(value) and value > 0 for value in (size, period)):
        return MIN_PARTICLE_INTERVALS
    return max(MIN_PARTICLE_INTERVALS, ceil(nextafter(8 * size / period, 0)))


def validate_compression(settings, options):
    if (settings.structure_family != "tpms" or settings.structure_name not in TPMS_FUNCTIONS
            or settings.tpms_morphology not in TPMS_MORPHOLOGIES or settings.test_profile != "custom"
            or settings.imported_geometry_path or settings.specimen_architecture != "solid"):
        return ["Peridynamic compression requires an analytic TPMS specimen."]
    errors = validate_geometry(settings, include_mesh=False)
    for value, bounds, label in (
        (settings.young_modulus, (0.001, 10_000_000), "Elastic modulus (MPa)"),
        (options.horizon_ratio, (2.5, 4.0), "Horizon / spacing"),
        (options.critical_stretch_percent, (0.01, 20.0), "Critical bond stretch (%)"),
        (options.compression_percent, (0.1, 35.0), "Compression (%)"),
    ):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not bounds[0] <= value <= bounds[1]):
            errors.append(f"{label} must be between {bounds[0]:g} and {bounds[1]:g}.")
    for value, bounds, label in (
        (options.resolution, (MIN_PARTICLE_INTERVALS, MAX_PARTICLE_INTERVALS), "Particles per axis"),
        (options.increments, (2, 80), "Load increments"),
    ):
        if type(value) is not int or not bounds[0] <= value <= bounds[1]:
            errors.append(f"{label} must be an integer from {bounds[0]} to {bounds[1]}.")
    if not errors and options.resolution < minimum_particle_resolution(settings):
        errors.append("Use at least eight particle intervals per TPMS period.")
    return errors


def _pairs(points, distance):
    tree = cKDTree(points)
    count = (tree.count_neighbors(tree, distance) - len(points)) // 2
    if count > MAX_PAIRS:
        raise ValueError("Too many particle pairs. Reduce the specimen grid or the horizon.")
    return tree.query_pairs(distance, output_type="ndarray").T


def _bonds(points, horizon, settings, spacing, threshold):
    left, right = _pairs(points, horizon + 0.5)
    length = np.linalg.norm(points[right] - points[left], axis=1)
    volume_fraction = np.clip(horizon + 0.5 - length, 0, 1)
    solid = volume_fraction > 0
    # Prevent interactions across pores by sampling each bond through its length.
    probes = int(np.ceil(2 * (horizon + 0.5)))
    for start in range(0, len(left), 65536):
        part = slice(start, start + 65536)
        origin = points[left[part]] * spacing
        delta = points[right[part]] * spacing - origin
        for position in np.arange(1, probes) / probes:
            sample = origin + position * delta
            solid[part] &= evaluate_priority_field(settings, *sample.T) < threshold
    return left[solid], right[solid], length[solid], volume_fraction[solid]


def _micromodulus(horizon):
    # Match the bulk modulus of an infinite regular lattice in uniform dilation.
    reach = int(np.ceil(horizon + 0.5))
    axis = np.arange(-reach, reach + 1)
    radii = np.linalg.norm(np.stack(np.meshgrid(axis, axis, axis), axis=-1), axis=-1)
    shell_fraction = np.clip(horizon + 0.5 - radii, 0, 1)
    bulk_modulus = 1 / (3 * (1 - 2 * POISSON_RATIO))
    return 18 * bulk_modulus / np.sum(shell_fraction * radii)


def _accumulate(gradient, left, right, vectors):
    size = len(gradient)
    for axis in range(3):
        gradient[:, axis] += (np.bincount(right, weights=vectors[:, axis], minlength=size)
                              - np.bincount(left, weights=vectors[:, axis], minlength=size))


class _Compression:
    """Energy and gradient in units of particle spacing and solid modulus."""

    def __init__(self, points, left, right, length, weight, horizon, height):
        self.points = points.copy()
        self.left, self.right, self.length = left, right, length
        self.stiffness = _micromodulus(horizon) * weight / length
        self.intact = np.ones(len(length), dtype=bool)
        self.height = height
        self.contact_origin = None

    def _potential(self, flattened, top, left, right, length, stiffness):
        points = flattened.reshape(-1, 3)
        delta = points[right] - points[left]
        distance = np.linalg.norm(delta, axis=1)
        extension = distance - length
        force = stiffness * extension
        energy = 0.5 * np.einsum("i,i->", force, extension)
        gradient = np.zeros_like(points)
        _accumulate(gradient, left, right, (force / np.maximum(distance, 1e-12))[:, None] * delta)

        if (self.contact_origin is None
                or np.max(np.linalg.norm(points - self.contact_origin, axis=1)) > CONTACT_SKIN / 2):
            self.contact_pairs = _pairs(points, CONTACT_DISTANCE + CONTACT_SKIN)
            self.contact_origin = points.copy()
        first, second = self.contact_pairs
        gap = points[second] - points[first]
        distance = np.linalg.norm(gap, axis=1)
        touching = distance < CONTACT_DISTANCE
        overlap = distance[touching] - CONTACT_DISTANCE
        contact_force = CONTACT_STIFFNESS * overlap
        energy += 0.5 * np.einsum("i,i->", contact_force, overlap)
        _accumulate(gradient, first[touching], second[touching],
                    (contact_force / np.maximum(distance[touching], 1e-12))[:, None] * gap[touching])

        bottom, upper = self.platen_overlaps(points, top)
        energy += 0.5 * PLATEN_STIFFNESS * (np.square(bottom).sum() + np.square(upper).sum())
        gradient[:, 2] += PLATEN_STIFFNESS * (upper - bottom)
        return energy, gradient.ravel()

    @staticmethod
    def platen_overlaps(points, top):
        z = points[:, 2]
        return np.maximum(0.5 - z, 0), np.maximum(z - top + 0.5, 0)

    def _relax(self, top, left, right, length, stiffness):
        for _ in range(2):
            result = minimize(self._potential, self.points.ravel(), args=(top, left, right, length, stiffness),
                              method="L-BFGS-B", jac=True,
                              options={"maxiter": 1000, "gtol": 1e-5, "ftol": 1e-13, "maxcor": 8})
            if not np.all(np.isfinite(result.x)) or not np.all(np.isfinite(result.jac)):
                raise RuntimeError("Peridynamic equilibrium returned non-finite values.")
            self.points = result.x.reshape(-1, 3)
            bottom, upper = self.platen_overlaps(self.points, top)
            reactions = PLATEN_STIFFNESS * np.array([bottom.sum(), upper.sum()])
            imbalance = abs(reactions[0] - reactions[1])
            residual = float(np.max(np.abs(result.jac)))
            nodal_load = PLATEN_STIFFNESS * max(bottom.max(), upper.max())
            if (residual <= max(2e-5, 0.005 * nodal_load)
                    and imbalance <= max(2e-4, 0.02 * reactions.max())):
                return reactions, residual, True
        return reactions, residual, False

    def advance(self, travel, previous, critical_stretch):
        top = self.height - travel
        self.points[:, 2] -= (travel - previous) * np.clip(self.points[:, 2] / (self.height - previous), 0, 1)
        first_break_force = None
        for round_number in range(MAX_DAMAGE_ROUNDS):
            subset = self.intact
            left, right = self.left[subset], self.right[subset]
            length, stiffness = self.length[subset], self.stiffness[subset]
            reactions, residual, converged = self._relax(top, left, right, length, stiffness)
            if not converged:
                return reactions, residual, first_break_force, "equilibrium"
            stretch = np.linalg.norm(self.points[right] - self.points[left], axis=1) / length - 1
            broken = stretch > critical_stretch
            if not np.any(broken):
                return reactions, residual, first_break_force, None
            if first_break_force is None:
                first_break_force = float(reactions[1])
            if round_number == MAX_DAMAGE_ROUNDS - 1:
                return reactions, residual, first_break_force, "damage"
            self.intact[np.flatnonzero(subset)[broken]] = False


def _save_frame(folder, step, points, reference, damage, layer, spacing):
    from pyvista import PolyData

    frame = PolyData((points * spacing).astype(np.float32))
    frame["Damage"] = damage.astype(np.float32)
    frame["Displacement_mm"] = ((points - reference) * spacing).astype(np.float32)
    frame["Reference_position_mm"] = (reference * spacing).astype(np.float32)
    frame["TPMS_layer"] = layer + 1
    frame.save(folder / f"step_{step:03}.vtp", binary=True)


def _write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run_compression(settings, options, folder=None, *, on_progress=report_analysis_step):
    """Compute displacements, compressive reactions and the irreversible bond damage."""
    from mesh_engine import geometry_threshold
    from periodic_sampling import sample_box

    errors = validate_compression(settings, options)
    if errors:
        raise ValueError("\n".join(errors))
    settings = replace(settings, poisson_ratio=POISSON_RATIO, material_model="linear_elastic", hardening_curve=())
    n = options.resolution
    spacing = settings.size / n
    lengths = np.asarray(settings.dimensions)
    counts = np.maximum(1, np.rint(lengths / spacing).astype(int))
    if counts.max() > MAX_PARTICLE_INTERVALS:
        raise ValueError("Use at most 72 particle intervals along each specimen axis.")
    steps = lengths / counts
    axes = [(np.arange(count) + 0.5) * step for count, step in zip(counts, steps)]
    on_progress("Sampling TPMS particles…", 0, options.increments)
    threshold = geometry_threshold(settings)
    grid = sample_box(settings, axes, dtype=np.float32, threshold=threshold) < 0
    points = (np.argwhere(grid) + 0.5) * (steps / spacing)
    on_progress("Building peridynamic bonds…", 0, options.increments)
    left, right, length, weight = _bonds(points, options.horizon_ratio, settings, spacing, threshold)
    if not len(left):
        raise ValueError("The sampled walls have no peridynamic bonds. Refine the particle grid.")
    connectivity = coo_matrix((np.ones(len(left), dtype=bool), (left, right)), shape=(len(points), len(points)))
    _, components = connected_components(connectivity, directed=False)
    if not np.intersect1d(components[points[:, 2] == 0.5], components[points[:, 2] == n - 0.5]).size:
        raise ValueError("No bonded material path between the platens. Refine the particle grid or change the geometry.")

    solver = _Compression(points, left, right, length, weight, options.horizon_ratio, n)
    initial_family = np.bincount(left, weight, len(points)) + np.bincount(right, weight, len(points))
    layer = np.floor(points[:, 2] * spacing / settings.cell_size).astype(np.int32)
    layers = np.unique(layer)
    layer_indices = [np.flatnonzero(layer == value) for value in layers]
    force_scale = settings.young_modulus * steps[0] * steps[1]
    if folder is None:
        run_id = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:8]}"
        folder = ROOT / "HumanResults" / f"Peridynamics_{run_id}"
    folder = Path(folder).resolve()
    frames = folder / "frames"
    frames.mkdir(parents=True)
    rows, layer_rows = [], []
    first_failure = None
    previous = 0.0
    stop_reason = None
    for step, travel in enumerate(np.linspace(0, n * options.compression_percent / 100, options.increments + 1)):
        if step:
            on_progress(f"Solving compression increment {step}/{options.increments}…", step - 1, options.increments)
            reactions, residual, failure_force, stop_reason = solver.advance(
                travel, previous, options.critical_stretch_percent / 100)
            if first_failure is None and failure_force is not None:
                first_failure = dict(step=step, displacement_mm=float(travel * spacing),
                                     force_n=failure_force * force_scale)
        else:
            reactions, residual = np.zeros(2), 0.0
        previous = travel
        broken = ~solver.intact
        lost_family = (np.bincount(left[broken], weight[broken], len(points))
                       + np.bincount(right[broken], weight[broken], len(points)))
        damage = np.divide(lost_family, initial_family, out=np.zeros_like(initial_family), where=initial_family > 0)
        damage = np.clip(damage, 0, 1)
        layer_means = [float(damage[indices].mean()) for indices in layer_indices]
        highest_damage_index = int(np.argmax(layer_means))
        row = dict(step=step, displacement_mm=float(travel * spacing),
                   bottom_force_n=float(reactions[0] * force_scale), top_force_n=float(reactions[1] * force_scale),
                   mean_damage=float(damage.mean()), max_damage=float(damage.max()),
                   broken_bonds=int(broken.sum()), max_residual_n=residual * force_scale,
                   highest_damage_layer=int(layers[highest_damage_index] + 1) if broken.any() else None,
                   highest_layer_mean_damage=layer_means[highest_damage_index],
                   converged=stop_reason is None)
        rows.append(row)
        for numbered_layer, indices, mean in zip(layers, layer_indices, layer_means):
            layer_rows.append(dict(step=step, displacement_mm=row["displacement_mm"], layer=int(numbered_layer + 1),
                                   z_min_mm=float(numbered_layer * settings.cell_size),
                                   z_max_mm=min(settings.size, float((numbered_layer + 1) * settings.cell_size)),
                                   mean_damage=mean, max_damage=float(damage[indices].max()),
                                   particles=len(indices)))
        _save_frame(frames, step, solver.points, points, damage, layer, spacing)
        if stop_reason is not None:
            break

    _write_csv(folder / "force_displacement.csv", rows)
    _write_csv(folder / "layer_damage.csv", layer_rows)
    verified = [row for row in rows if row["converged"]]
    write_json(folder / "status.json", dict(
        schema=1, state="complete" if stop_reason is None else "incomplete",
        method="quasi_static_pmb_v1", stop_reason=stop_reason, settings=asdict(settings), options=asdict(options),
        poisson_ratio=POISSON_RATIO, spacing_mm=spacing, horizon_mm=options.horizon_ratio * spacing,
        contact_distance_mm=CONTACT_DISTANCE * spacing,
        platen_penalty_n_per_mm=PLATEN_STIFFNESS * settings.young_modulus * spacing,
        self_contact_penalty_n_per_mm=CONTACT_STIFFNESS * settings.young_modulus * spacing,
        particles=len(points), bonds=len(left), sampled_solid_fraction=float(grid.mean()),
        isolated_particles=int(np.count_nonzero(initial_family == 0)), first_bond_break=first_failure,
        peak_force_n=max(row["top_force_n"] for row in verified), frames=rows,
    ))
    on_progress("Peridynamic results saved.", rows[-1]["step"], options.increments)
    return str(folder)


def run_local_compression(settings, options, folder=None):
    return dict(operation="peridynamic_solve", run_folder=run_compression(settings, options, folder))
