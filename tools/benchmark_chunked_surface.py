"""Isolated-process comparison of automatic chunks and periodic contour reuse."""
import argparse
import gc
import json
from pathlib import Path
import statistics
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pyvista as pv
import psutil

import chunked_surface
from mesh_engine import geometry_threshold, make_surface
from model import Settings


def worker(args):
    settings = Settings(size=args.size, cell_size=1, structure_family=args.family, structure_name=args.name)
    geometry_threshold(settings)
    # Warm VTK/native imports using a small specimen, outside timed repetitions.
    make_surface(Settings(size=1, cell_size=1), chunked=False)
    chunked_surface.REUSE_TEMPLATES = args.mode != 'chunked_no_reuse'
    process = psutil.Process()
    times, peaks, deltas = [], [], []
    surface = None
    for _ in range(args.repeats):
        surface = None
        gc.collect()
        baseline = process.memory_info().rss
        peak = [baseline]
        stopped = Event()
        def sample():
            while not stopped.wait(.01):
                peak[0] = max(peak[0], process.memory_info().rss)
        thread = Thread(target=sample, daemon=True)
        thread.start()
        try:
            start = perf_counter()
            surface = make_surface(settings, chunked=False if args.mode == 'single' else None)
            times.append(perf_counter() - start)
            peak[0] = max(peak[0], process.memory_info().rss)
        finally:
            stopped.set()
            thread.join()
        peaks.append(peak[0])
        deltas.append(peak[0] - baseline)
    surface.save(args.surface)
    print(json.dumps(dict(mode=args.mode, seconds=times, median_seconds=statistics.median(times),
                          peak_rss_bytes=max(peaks), peak_increment_bytes=max(deltas),
                          triangles=surface.n_cells, points=surface.n_points, volume=surface.volume,
                          closed_manifold=surface.is_manifold,
                          surface_mode=str(surface.field_data['_surface_mode'][0]),
                          chunks=surface.field_data['_surface_chunks'].tolist() if '_surface_chunks' in surface.field_data else None,
                          sampling=surface.field_data['_surface_sampling'].tolist() if '_surface_sampling' in surface.field_data else None)))


def accuracy(reference, actual):
    distances = []
    for a, b in ((reference, actual), (actual, reference)):
        indices = np.linspace(0, a.n_points - 1, min(4096, a.n_points), dtype=int)
        distances.append(float(np.max(np.abs(pv.PolyData(a.points[indices]).compute_implicit_distance(b)['implicit_distance']))))
    # Measure caps in bounded face batches, avoiding a full triangle-coordinate copy.
    def caps(surface):
        result = np.zeros(6)
        faces = surface.faces.reshape(-1, 4)[:, 1:]
        for start in range(0, len(faces), 262144):
            points = surface.points[faces[start:start + 262144]]
            area = np.linalg.norm(np.cross(points[:, 1]-points[:, 0], points[:, 2]-points[:, 0]), axis=1) / 2
            for dimension in range(3):
                for side, coordinate in enumerate(surface.bounds[2*dimension:2*dimension+2]):
                    result[2*dimension+side] += area[np.all(points[:, :, dimension] == coordinate, axis=1)].sum()
        return result
    old_caps, new_caps = caps(reference), caps(actual)
    volume_error = abs(actual.volume / reference.volume - 1)
    cap_error = float(np.max(np.abs(new_caps-old_caps) / np.maximum(old_caps, 1e-12)))
    bounds_error = float(np.max(np.abs(np.asarray(actual.bounds)-reference.bounds)))
    return dict(relative_volume_error=volume_error, relative_cap_area_error=cap_error,
                max_sampled_bidirectional_vertex_surface_distance_mm=max(distances),
                distance_samples_per_direction=4096, bounds_difference_mm=bounds_error,
                closed_manifold=actual.is_manifold,
                passed=bool(actual.is_manifold and volume_error < .0001 and cap_error < .0001 and bounds_error < 1e-7 and max(distances) < 1e-6))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--size', type=float, default=4)
    parser.add_argument('--family', default='tpms')
    parser.add_argument('--name', default='Gyroid')
    parser.add_argument('--mode', choices=['single', 'chunked', 'chunked_no_reuse'])
    parser.add_argument('--surface')
    parser.add_argument('--output', default='chunked_surface_benchmark.json')
    parser.add_argument('--unaligned', action='store_true')
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    if args.mode:
        worker(args)
        return 0
    cases = [('tpms', 'Gyroid', args.size), ('lattice', 'BCC', args.size)]
    if args.unaligned:
        cases.append(('tpms', 'Gyroid', 3.13))
    results = []
    with TemporaryDirectory(prefix='rustmech-chunk-benchmark-') as directory:
        for family, name, size in cases:
            modes, paths = {}, {}
            for mode in ('single', 'chunked_no_reuse', 'chunked'):
                path = Path(directory) / f'{mode}.vtp'
                command = [sys.executable, __file__, '--mode', mode, '--surface', str(path), '--repeats', str(args.repeats),
                           '--family', family, '--name', name, '--size', str(size)]
                completed = subprocess.run(command, text=True, capture_output=True, check=True)
                modes[mode] = json.loads(completed.stdout.splitlines()[-1])
                paths[mode] = path
                print(f"{name} {size:g} mm {mode}: {modes[mode]['median_seconds']:.3f}s, peak RSS {modes[mode]['peak_rss_bytes']/1024**3:.2f} GiB", flush=True)
            reference, actual = pv.read(paths['single']), pv.read(paths['chunked'])
            check = accuracy(reference, actual)
            del reference, actual
            gc.collect()
            result = dict(family=family, name=name, size_mm=size, cell_mm=1, modes=modes, accuracy=check,
                          speedup=modes['single']['median_seconds']/modes['chunked']['median_seconds'],
                          periodic_template_speedup=modes['chunked_no_reuse']['median_seconds']/modes['chunked']['median_seconds'])
            results.append(result)
            Path(args.output).write_text(json.dumps(dict(repeats=args.repeats, cases=results,
                scope='Independent warm processes; no rendering/solver; sampled surface distance; RSS sampled every 10 ms',
                passed=all(row['accuracy']['passed'] for row in results)), indent=2) + '\n', encoding='utf-8')
            print(f"Accuracy passed={check['passed']}; total speedup={result['speedup']:.2f}x; template reuse={result['periodic_template_speedup']:.2f}x", flush=True)
    return 0 if all(row['accuracy']['passed'] for row in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
