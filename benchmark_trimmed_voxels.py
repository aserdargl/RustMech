"""Compare Cartesian HEX8, trimmed TET4 and Netgen TET4 without Qt."""

import argparse
from dataclasses import dataclass
from functools import partial
import json
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Callable

import numpy as np
import pyvista as pv

from mesh_engine import contour_arrays, gyroid_field
from model import Settings
from netgen_kernel import kernel_path
from periodic_tetra import face_components
from rust_mesh import build_hex_grid, select_voxels
from tetrahedral_mesh import generate_tetrahedral_mesh
from trimmed_voxel_mesh import build_trimmed_tet4


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    field: Callable[[np.ndarray], np.ndarray]
    reference: pv.PolyData
    sample_surface: pv.PolyData
    volume: float
    sphere_center: np.ndarray | None = None
    sphere_radius: float | None = None


def make_cases():
    height, slope_x, slope_y = 0.53, 0.23, 0.17

    def plane_field(points):
        x, y, z = points.T
        return z - height - slope_x * (x - 0.5) - slope_y * (y - 0.5)

    plane = pv.Box(bounds=(0, 1, 0, 1, 0, 1)).triangulate()
    points = np.asarray(plane.points, dtype=float).copy()
    on_top = points[:, 2] == 1
    points[on_top, 2] -= plane_field(points[on_top])
    plane.points = points

    center = np.array([0.48, 0.51, 0.52])
    radius = 0.36

    def sphere_field(points):
        return np.linalg.norm(points - center, axis=1) - radius

    sphere = pv.Icosphere(radius=radius, center=center, nsub=4)
    return (
        BenchmarkCase("inclined_plane", plane_field, plane, plane.subdivide(5), height),
        BenchmarkCase("sphere", sphere_field, sphere, sphere, 4 * np.pi * radius**3 / 3,
                      center, radius),
    )


def make_gyroid_case():
    """One 1 mm period, with independently sampled sheet boundary and cube caps."""
    def field(points):
        return np.abs(gyroid_field(*points.T, 1.0)) - 0.45

    spacing = 1 / 96
    axis = np.arange(-1, 98) * spacing
    values = np.abs(gyroid_field(axis[:, None, None], axis[None, :, None], axis, 1.0)) - 0.45
    for dimension in range(3):
        np.moveaxis(values, dimension, 0)[[0, -1]] = 1
    vertices, faces = contour_arrays(values, spacing)
    vertices = np.clip(vertices - spacing, 0, 1)
    reference = pv.PolyData.from_regular_faces(vertices, faces).clean(
        tolerance=1e-7, absolute=True, polys_to_lines=False, lines_to_points=False,
    )
    return BenchmarkCase("thin_gyroid", field, reference, reference, reference.volume)


def thin_wall_probes(field):
    """Sample centers and quarter points of complete sheet crossings ≤ 0.125 mm."""
    x = np.linspace(0, 1, 513)
    yz = (np.arange(20) + 0.5) / 20
    probes = []
    for y in yz:
        for z in yz:
            line = np.column_stack((x, np.full_like(x, y), np.full_like(x, z)))
            values = field(line)
            crossings = np.flatnonzero((values[:-1] < 0) != (values[1:] < 0))
            roots = x[crossings] - ((x[1] - x[0]) * values[crossings] /
                                     (values[crossings + 1] - values[crossings]))
            for start, end in zip(roots[:-1], roots[1:]):
                if end - start <= 0.125 and field(np.array([[(start + end) / 2, y, z]]))[0] < 0:
                    probes.append([(start + t * (end - start), y, z) for t in (0.25, 0.5, 0.75)])
    return np.array(probes)


def build_center_sampled_hex8(field, subdivisions):
    """Use the production HEX8 selection and assembly with an analytic field."""
    spacing = 1 / subdivisions
    axis = (np.arange(subdivisions) + 0.5) * spacing
    points = np.stack(np.meshgrid(axis, axis, axis, indexing="ij"), axis=-1)
    values = field(points.reshape(-1, 3)).reshape((subdivisions,) * 3)
    retained, removed, components = select_voxels(values, np.count_nonzero(values < 0))
    build_hex_grid(removed, spacing)
    return build_hex_grid(retained, spacing, return_boundaries=True)[0], components


def build_netgen_tet4(surface, spacing):
    settings = Settings(size=1.0, mesh_type="TET4", mesh_resolution=spacing,
                        local_mesh_size=spacing, stl_resolution=1 / 128,
                        tetra_strategy="netgen")
    return generate_tetrahedral_mesh(settings, surface, use_cache=False).grid


def surface_samples(surface):
    points = np.asarray(surface.points, dtype=float)
    a, b, c = points[surface.regular_faces].transpose(1, 0, 2)
    return np.vstack((points, (a + b) / 2, (b + c) / 2, (c + a) / 2, (a + b + c) / 3))


def point_distances(points, surface):
    _, closest = surface.find_closest_cell(points, return_closest_point=True)
    return np.linalg.norm(points - closest, axis=1)


def surface_errors(skin, case):
    samples = surface_samples(skin)
    reference_points = surface_samples(case.sample_surface)
    if case.sphere_center is None:
        forward = point_distances(samples, case.reference)
    else:
        forward = np.abs(case.field(samples))
        radial = reference_points - case.sphere_center
        reference_points = (case.sphere_center +
                            radial * (case.sphere_radius / np.linalg.norm(radial, axis=1))[:, None])
    reverse = point_distances(reference_points, skin)
    return float(forward.max()), float(reverse.max())


def compare_case(case, subdivisions, repeats, *, quality_study=False, wall_probes=None):
    spacing = 1 / subdivisions
    methods = (
        ("HEX8", partial(build_center_sampled_hex8, case.field, subdivisions)),
        ("Trimmed TET4", partial(build_trimmed_tet4, case.field, subdivisions)),
        ("Netgen TET4", partial(build_netgen_tet4, case.reference, spacing)),
    )
    if quality_study:
        methods = (
            ("Unsnapped", partial(build_trimmed_tet4, case.field, subdivisions, snap_boundary=False)),
            ("Snapped", partial(build_trimmed_tet4, case.field, subdivisions)),
        )
    for name, build in methods:
        timings = []
        for repeat in range(repeats):
            start = perf_counter()
            result = build()
            timings.append(perf_counter() - start)
            if repeat + 1 < repeats:
                del result
        if name == "HEX8":
            grid, components = result
        else:
            grid = result
            components = len(np.unique(face_components(grid)))
        skin = grid.extract_surface(algorithm="dataset_surface").triangulate()
        forward, reverse = surface_errors(skin, case)
        volume = float(grid.volume)
        jacobian = np.asarray(grid.cell_quality("scaled_jacobian").cell_data["scaled_jacobian"])
        if len(jacobian) == 0 or not np.all(np.isfinite(jacobian) & (jacobian > 0)):
            raise ValueError(f"{name} produced invalid elements for {case.name} at n={subdivisions}.")
        row = {
            "case": case.name,
            "method": name,
            "subdivisions": subdivisions,
            "nominal_size": spacing,
            "nodes": grid.n_points,
            "elements": grid.n_cells,
            "volume": volume,
            "reference_volume": case.volume,
            "volume_error_pct": abs(volume / case.volume - 1) * 100,
            "mesh_to_reference_max": forward,
            "reference_to_mesh_max": reverse,
            "surface_error_max": max(forward, reverse),
            "scaled_jacobian_min": float(jacobian.min()),
            "scaled_jacobian_p01": float(np.percentile(jacobian, 1)),
            "face_components": components,
            "open_or_nonmanifold_skin_edges": skin.n_open_edges,
            "mesh_storage_kib": grid.GetActualMemorySize(),
            "median_meshing_seconds": median(timings),
        }
        if quality_study:
            snap = grid.field_data.get("snap_fraction")
            row["snap_fraction"] = float(snap[0]) if snap is not None else 0.0
            row["skin_euler_characteristic"] = skin.n_points - skin.n_cells // 2 if not skin.n_open_edges else None
        if wall_probes is not None:
            inside = grid.find_containing_cell(wall_probes.reshape(-1, 3)).reshape(-1, 3) >= 0
            row.update(wall_probe_count=len(wall_probes),
                       wall_centers_found=int(inside[:, 1].sum()),
                       wall_sections_found=int(inside.all(axis=1).sum()))
        yield row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--levels", type=int, nargs=3, default=(8, 16, 32),
                        metavar=("COARSE", "MEDIUM", "FINE"))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, help="Also write all measurements to JSON.")
    parser.add_argument("--quality-study", action="store_true",
                        help="Compare unsnapped and snapped TET4, including thin Gyroid wall probes.")
    args = parser.parse_args()
    coarse, medium, fine = args.levels
    if not 0 < coarse < medium < fine or args.repeats < 1:
        parser.error("Use three positive, increasing levels and at least one repeat.")

    netgen = None
    if not args.quality_study:
        netgen = "RustMech optimized kernel" if kernel_path() is not None else "netgen-mesher Python package"
        print(f"Netgen: {netgen}")
    print(f"Timed repeats: {args.repeats}")
    print(f"{'Geometry':15} {'Grid':>4} {'Mesher':13} {'Elements':>9} {'Vol err %':>10} "
          f"{'Surface':>10} {'Min SJ':>9} {'P01 SJ':>9} {'KiB':>7} {'Seconds':>9} "
          f"{'Parts':>5} {'Edges':>5} {'Wall sections':>13}")
    rows = []
    cases = make_cases()
    gyroid = make_gyroid_case() if args.quality_study else None
    if gyroid is not None:
        cases += (gyroid,)
    for case in cases:
        probes = thin_wall_probes(case.field) if case is gyroid else None
        for level in args.levels:
            for row in compare_case(case, level, args.repeats, quality_study=args.quality_study,
                                    wall_probes=probes):
                rows.append(row)
                walls = f"{row['wall_sections_found']}/{len(probes)}" if probes is not None else ""
                print(f"{case.name:15} {level:4} {row['method']:13} {row['elements']:9,d} "
                      f"{row['volume_error_pct']:10.5f} {row['surface_error_max']:10.6f} "
                      f"{row['scaled_jacobian_min']:9.2g} {row['scaled_jacobian_p01']:9.2g} "
                      f"{row['mesh_storage_kib']:7,d} "
                      f"{row['median_meshing_seconds']:9.4f} {row['face_components']:5} "
                      f"{row['open_or_nonmanifold_skin_edges']:5} {walls:>13}", flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        report = {"netgen_backend": netgen, "pyvista_version": pv.__version__,
                  "vtk_version": ".".join(map(str, pv.vtk_version_info)),
                  "length_unit": "mm", "repeats": args.repeats, "results": rows}
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
