"""Compare repeated and direct field evaluation on identical sample grids.

Run from the project root using the application's Python environment.
"""

import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

import mesh_engine
from model import Settings
from periodic_sampling import sample_priority_grid
from structure_library import evaluate_priority_field


def direct(settings, axis, *, dtype=np.float64, threshold=0.0):
    field = np.empty((len(axis),) * 3, dtype=np.float64)
    slab = max(1, 2_000_000 // len(axis) ** 2)
    for start in range(0, len(axis), slab):
        field[start:start + slab] = evaluate_priority_field(
            settings, axis[start:start + slab, None, None],
            axis[None, :, None], axis[None, None, :],
        ) - threshold
    return field.astype(dtype, copy=False)


def measure(action):
    times = []
    for _ in range(3):
        start = perf_counter()
        result = action()
        times.append(perf_counter() - start)
        del result
    return median(times)


def main():
    results = []
    for family, name in (('tpms', 'Gyroid'), ('lattice', 'BCC'), ('lattice', 'Diamond Truss')):
        settings = Settings(size=12, cell_size=3, structure_family=family, structure_name=name)
        axis = (np.arange(160) + .5) * .075
        expected, actual = direct(settings, axis), sample_priority_grid(settings, axis)
        error = float(np.max(np.abs(expected - actual)))
        np.testing.assert_allclose(actual, expected, atol=1e-12, rtol=1e-11)
        del expected, actual
        before = measure(lambda: direct(settings, axis))
        after = measure(lambda: sample_priority_grid(settings, axis))
        results.append(dict(stage='field_sampling_4x4x4', structure=name,
                            before_seconds=before, after_seconds=after,
                            speedup=before / after, max_absolute_error=error,
                            evaluated_points_before=160 ** 3, evaluated_points_after=40 ** 3))
    for family, name in (('tpms', 'Gyroid'), ('lattice', 'BCC')):
        settings = Settings(structure_family=family, structure_name=name)
        mesh_engine.geometry_threshold(settings)  # Exclude the unchanged threshold calculation.
        with patch.object(mesh_engine, 'sample_priority_grid', direct):
            baseline = mesh_engine.make_surface(settings)
            before = measure(lambda: mesh_engine.make_surface(settings))
        optimized = mesh_engine.make_surface(settings)
        after = measure(lambda: mesh_engine.make_surface(settings))
        results.append(dict(stage='reference_surface_2x2x2', structure=name,
                            before_seconds=before, after_seconds=after, speedup=before / after,
                            relative_volume_change=abs(optimized.volume / baseline.volume - 1),
                            closed_before=baseline.is_manifold, closed_after=optimized.is_manifold))
    destination = Path('benchmarks/periodic_sampling.json')
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
