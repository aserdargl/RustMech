"""Compare extractors on identical samples and geometry-only versus FE preview.

Run from the project root with the application Python. No solver is started.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import platform
import statistics
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pyvista as pv

import mesh_engine as engine
from model import Settings
from periodic_sampling import sample_priority_grid
from surface_optimization import cap_areas
from tetrahedral_mesh import clear_tetrahedral_cache


def timed(call, repeats):
    times = []
    for _ in range(repeats):
        start = perf_counter()
        result = call()
        times.append(perf_counter() - start)
    return result, dict(median_seconds=statistics.median(times), seconds=times)


def compare(settings, repeats):
    start = perf_counter()
    n = max(4, int(np.ceil(settings.size / settings.surface_resolution)))
    spacing = settings.size / n
    axis = np.arange(-1, n + 2, dtype=float) * spacing
    field = sample_priority_grid(settings, axis, dtype=np.float32, threshold=engine.geometry_threshold(settings))
    field[[0, -1], :, :] = 1
    field[:, [0, -1], :] = 1
    field[:, :, [0, -1]] = 1
    sampling_seconds = perf_counter() - start
    # Load native cleanup/VTK before timed repetitions; timings are warm process.
    engine.extract_reference_surface(field, spacing, settings.size)
    surfaces, timings = {}, {}
    for backend in ('marching_cubes', 'flying_edges'):
        surfaces[backend], timings[backend] = timed(
            lambda: engine.extract_reference_surface(field, spacing, settings.size, backend=backend), repeats)
    old, new = surfaces['marching_cubes'], surfaces['flying_edges']
    distances = [float(np.max(np.abs(a.compute_implicit_distance(b)['implicit_distance'])))
                 for a, b in ((new, old), (old, new))]
    old_caps, new_caps = cap_areas(old, settings.size), cap_areas(new, settings.size)
    triangles = new.points[new.faces.reshape(-1, 4)[:, 1:]]
    signed_volume = float(np.einsum('ij,ij->i', triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])).sum() / 6)
    accuracy = dict(closed_manifold=bool(new.is_manifold), open_edges=int(new.n_open_edges),
                    reference_volume=float(old.volume), fast_volume=float(new.volume), signed_volume=signed_volume,
                    relative_volume_error=abs(new.volume / old.volume - 1),
                    max_bidirectional_vertex_surface_distance_mm=max(distances),
                    bounds_max_difference_mm=float(np.max(np.abs(np.array(old.bounds)-new.bounds))),
                    caps_max_relative_error=float(np.max(np.abs(new_caps-old_caps)/np.maximum(np.abs(old_caps), 1e-12))))
    passed = (accuracy['closed_manifold'] and signed_volume > 0 and
              accuracy['relative_volume_error'] < .005 and max(distances) < spacing and
              accuracy['bounds_max_difference_mm'] < 1e-6 * settings.size and
              accuracy['caps_max_relative_error'] < .005)
    return dict(settings=asdict(settings), spacing_mm=spacing, grid_shape=list(field.shape),
                sampling_seconds=sampling_seconds, timings=timings,
                extraction_speedup=timings['marching_cubes']['median_seconds']/timings['flying_edges']['median_seconds'],
                actual_extractor=str(new.field_data['_surface_extractor'][0]),
                old_triangles=old.n_cells, new_triangles=new.n_cells, accuracy=accuracy, passed=passed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', default='surface_preview_benchmark.json')
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    cases = []
    for cells in (1, 2):
        for family, name, morphology, fraction in (
            ('tpms', 'Gyroid', 'sheet', 30),
            ('tpms', 'Gyroid', 'network_a', 45),
            ('lattice', 'BCC', 'sheet', 20),
        ):
            settings = Settings(size=cells, cell_size=1, mesh_resolution=.1,
                                structure_family=family, structure_name=name,
                                tpms_morphology=morphology, solid_fraction=fraction)
            result = compare(settings, args.repeats)
            cases.append(result)
            print(f"{cells} cells/axis {name} {morphology}: {result['extraction_speedup']:.2f}x, passed={result['passed']}", flush=True)
    flows = []
    for kind in ('HEX8', 'TET4'):
        settings = Settings(size=1, cell_size=1, mesh_type=kind, mesh_resolution=.1, local_mesh_size=.1, tetra_strategy='netgen')
        _, geometry = timed(lambda: engine.build_preview(settings), args.repeats)
        def former_preview():
            clear_tetrahedral_cache()
            surface = engine.make_surface(settings, surface_backend='marching_cubes')
            return engine.generate_volume_mesh(settings, surface)
        mesh, full = timed(former_preview, args.repeats)
        flows.append(dict(mesh_type=kind, geometry_only=geometry, former_surface_and_mesh=full,
                          preview_compute_speedup=full['median_seconds']/geometry['median_seconds'],
                          elements=mesh.grid.n_cells, solve_errors=mesh.solve_errors))
        print(f"{kind} preview computation: {flows[-1]['preview_compute_speedup']:.2f}x", flush=True)
    output = dict(python=sys.version, platform=platform.platform(), vtk=pv.vtk_version_info,
                  timing_scope='Warm process, median; identical scalar grid per extractor; FE caches cleared per former-preview repetition; no rendering or solver',
                  repeats=args.repeats, surfaces=cases, preview_flows=flows,
                  passed=all(case['passed'] for case in cases) and all(not flow['solve_errors'] for flow in flows))
    Path(args.output).write_text(json.dumps(output, indent=2) + '\n', encoding='utf-8')
    return 0 if output['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
