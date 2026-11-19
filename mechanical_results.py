"""Mesh, fixture and solution reports for mechanical analyses."""
from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pyvista as pv

from mesh_engine import make_surface
from model import FEMesh, PLATEN_MARGIN_RATIO, Settings, loading_target_reached, write_json
from rust_mesh import tet_metrics
from structure_library import describe_structure

if TYPE_CHECKING:
    from mechanical_analysis import RunPaths

QUALITY_METRICS = ("scaled_jacobian", "max_edge_ratio", "volume")

MODEL_NOTES = {
    "units": "mm, N, MPa; work in J (N mm / 1000)",
    "geometry": "Sheet gyroid: abs(sin X cos Y + sin Y cos Z + sin Z cos X) <= t",
    "smooth_surface": "Sample-calibrated reference isosurface, capped at cube faces",
    "fe_geometry": "Voxel HEX8/HEX20 or surface-conforming TET4/TET10 of the largest connected solid",
    "removed_fragments": "Other face-connected voxel components excluded; no volume backfill; shown red in Studio",
    "fe_ties": "Equal field values resolved by stable grid index order",
    "boundary_conditions": "Bottom rigid platen fixed; top rigid platen moves only in Z",
    "material": "Selected isotropic linear, bilinear or multilinear material; see settings.json",
    "material_curve_units": "Cauchy true stress / MPa versus plastic logarithmic strain for user curves",
    "nonlinearity": "Large displacement, selected material, rigid platen contact and optional self-contact",
    "work": "External compression work; not separately computed plastic energy absorption",
    "mesh_quality": "Element shape checks do not establish geometry or solution convergence",
}

def mesh_quality(grid: pv.UnstructuredGrid) -> tuple[pv.UnstructuredGrid, dict]:
    cell_type = grid.celltypes[0]
    is_quadratic = cell_type in (pv.CellType.QUADRATIC_TETRA, pv.CellType.QUADRATIC_HEXAHEDRON)
    is_tetrahedral = cell_type in (pv.CellType.TETRA, pv.CellType.QUADRATIC_TETRA)
    linear = grid.linear_copy() if is_quadratic else grid
    if is_tetrahedral:
        measured = linear.cell_quality(["scaled_jacobian", "volume"], null_value=np.nan)
        _, measured.cell_data["max_edge_ratio"] = tet_metrics(grid)
    else:
        measured = linear.cell_quality(list(QUALITY_METRICS), null_value=np.nan)
    quality = grid.copy(deep=False) if is_quadratic else measured
    if is_quadratic:
        quality.cell_data.update(measured.cell_data)
    sj, ratio, volume = (
        np.asarray(quality.cell_data[name]) for name in QUALITY_METRICS
    )
    valid = (
        np.isfinite(sj)
        & np.isfinite(ratio)
        & np.isfinite(volume)
        & (sj > 0)
        & (ratio >= 0.9999999999)
        & (volume > 0)
    )
    quality.cell_data["quality_valid"] = valid.astype(np.uint8)
    report = {
        "nodes": grid.n_points,
        "elements": grid.n_cells,
        "invalid_or_unmeasurable": int(np.count_nonzero(~valid)),
    }
    for name in QUALITY_METRICS:
        values = np.asarray(quality.cell_data[name])
        finite = values[np.isfinite(values)]
        report[name + "_range"] = (
            [float(finite.min()), float(finite.max())] if finite.size else None
        )
    return quality, report


def make_compression_platens(
    size: float, top_displacement_z: float = 0.0, xy_bounds=None
) -> tuple[pv.PolyData, pv.PolyData]:
    """Create bottom floor platen (Z <= 0) and top compression platen (Z >= size + u_z)."""
    margin = PLATEN_MARGIN_RATIO * float(size)
    thickness = max(0.08 * float(size), 0.25)
    x_min, x_max = -margin, float(size) + margin
    y_min, y_max = -margin, float(size) + margin
    if xy_bounds is not None:
        x_min, x_max, y_min, y_max = xy_bounds
        x_min, x_max = x_min-margin, x_max+margin
        y_min, y_max = y_min-margin, y_max+margin
    bottom_platen = (
        pv.Box(bounds=(x_min, x_max, y_min, y_max, -thickness, 0.0))
        .triangulate()
        .compute_normals(auto_orient_normals=True, split_vertices=True)
    )
    z_top_base = float(size) + float(top_displacement_z)
    top_platen = (
        pv.Box(
            bounds=(
                x_min,
                x_max,
                y_min,
                y_max,
                z_top_base,
                z_top_base + thickness,
            )
        )
        .triangulate()
        .compute_normals(auto_orient_normals=True, split_vertices=True)
    )
    return bottom_platen, top_platen


def save_image(
    mesh,
    filename: Path,
    scalars: str | None = None,
    edges: bool = False,
    platens: tuple[pv.PolyData, pv.PolyData] | None = None,
) -> None:
    plotter = pv.Plotter(off_screen=True, window_size=(1200, 900))
    try:
        plotter.set_background("#f5f5f5")
        options = (
            {"scalars": scalars, "cmap": "viridis"}
            if scalars
            else {"color": "#94b9d4"}
        )
        if scalars == "scaled_jacobian":
            options.update(
                clim=(0, 1), scalar_bar_args={"title": "Scaled Jacobian"}
            )
        plotter.add_mesh(
            mesh, show_edges=edges, edge_color="#334455", **options
        )
        if platens is not None:
            bottom_platen, top_platen = platens
            plotter.add_mesh(
                bottom_platen,
                color="#475569",
                opacity=0.88,
                show_edges=True,
                edge_color="#1e293b",
            )
            plotter.add_mesh(
                top_platen,
                color="#64748b",
                opacity=0.88,
                show_edges=True,
                edge_color="#1e293b",
            )
        plotter.add_axes()
        plotter.view_isometric()
        plotter.reset_camera()
        plotter.camera.zoom(0.9)
        plotter.screenshot(str(filename))
    finally:
        plotter.close()


def export_geometry(
    settings: Settings, mesh: FEMesh, paths: RunPaths, images: bool = True
) -> dict:
    surface = make_surface(settings)
    if surface.n_open_edges or not surface.is_manifold:
        raise ValueError(
            "Smooth surface is not closed; adjust surface resolution."
        )
    geometry = paths.human / "01_Geometry"
    target = paths.human / "02_MeshQuality"
    surface.save(geometry / "gyroid.stl")
    from standard_tests import fixture_surfaces, profile_report
    bottom_platen, top_platen = (fixture_surfaces(settings) if settings.test_profile != 'custom' else
        make_compression_platens(settings.size, 0.0, surface.bounds[:4] if settings.imported_geometry_path else None))
    bottom_platen.save(geometry / "bottom_platen.stl")
    top_platen.save(geometry / "top_platen.stl")
    quality, report = mesh_quality(mesh.grid)
    report.update(
        {
            "structure_family": settings.structure_family,
            "structure_name": settings.structure_name,
            "tpms_morphology": settings.tpms_morphology,
            "structure_description": describe_structure(settings),
            "requested_spacing_mm": settings.mesh_resolution,
            "mesh_type": settings.mesh_type,
            "local_target_spacing_mm": settings.local_mesh_size if mesh.subdivisions is None else None,
            "actual_spacing_mm": mesh.spacing if mesh.subdivisions is not None else None,
            "subdivisions": mesh.subdivisions,
            "actual_solid_fraction_percent": mesh.fraction,
            "face_connected_components": mesh.components,
            "original_face_connected_components": mesh.original_components,
            "candidate_elements": mesh.candidate_elements,
            "removed_elements": mesh.removed_grid.n_cells,
            "removed_volume_mm3": float(mesh.removed_grid.volume) if mesh.removed_grid.n_cells else 0.,
            "solve_errors": mesh.solve_errors,
            "smooth_volume_fraction_percent": 100
            * surface.volume
            / (np.prod(np.asarray(surface.bounds).reshape(3, 2)[:, 1] - np.asarray(surface.bounds).reshape(3, 2)[:, 0]) if settings.imported_geometry_path or settings.test_profile != 'custom' else settings.size**3),
        }
    )
    quality.save(target / "mesh.vtu")
    if mesh.removed_grid.n_cells:
        mesh.removed_grid.save(target / "removed_fragments.vtu")
    write_json(target / "quality_summary.json", report)
    with (target / "quality_report.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.writer(stream)
        writer.writerow(["Element", *QUALITY_METRICS, "Valid"])
        writer.writerows(
            zip(
                quality.cell_data["ansys_elem_num"],
                *(quality.cell_data[name] for name in QUALITY_METRICS),
                quality.cell_data["quality_valid"],
            )
        )
    write_json(paths.human / "settings.json", asdict(settings))
    notes = {**MODEL_NOTES, "geometry": describe_structure(settings), 'test_profile': profile_report(settings)}
    if settings.test_profile != 'custom':
        notes.update(work='External actuator work; not separately computed plastic dissipation',
                     boundary_conditions='See test_profile for generated specimen and fixtures')
        write_json(geometry / 'test_profile.json', profile_report(settings))
    write_json(paths.human / "model_notes.json", notes)
    if images:
        save_image(
            surface,
            geometry / "geometry.png",
            platens=(bottom_platen, top_platen),
        )
        save_image(
            quality.extract_surface(algorithm="dataset_surface"),
            target / "mesh_quality.png",
            "scaled_jacobian",
            edges=True,
            platens=(bottom_platen, top_platen),
        )
    if report["invalid_or_unmeasurable"]:
        raise ValueError("Invalid FE cell quality; see quality_report.csv.")
    return report


def extract_curve(result) -> tuple[np.ndarray, dict]:
    times = np.asarray(result.time_values, dtype=float)
    if (
        result.nsets < 1
        or len(times) != result.nsets
        or not np.all(np.isfinite(times))
    ):
        raise ValueError("No valid saved solution history.")
    from standard_tests import load_sign
    settings=getattr(result,'settings',None)
    sign=load_sign(settings) if isinstance(settings,Settings) else -1.
    rows = [[0.0, 0.0, 0.0, 0.0, 0.0]]
    for index, time in enumerate(times):
        top_uz = result.top_platen_displacement(index)
        bottom, top = result.platen_reactions(index)
        rows.append([float(time), sign*top_uz, sign*top, bottom, top + bottom])
    history = np.asarray(rows)
    if np.any(np.diff(history[:, 0]) <= 0) or np.any(
        np.diff(history[:, 1]) <= 0
    ):
        raise ValueError(
            "Saved actuator travel history is not strictly increasing."
        )
    if np.any(history[:, 2] < -1e-6):
        raise ValueError(
            "Reaction sign is inconsistent with the selected loading direction."
        )
    scale = np.maximum(
        np.maximum(np.abs(history[:, 2]), np.abs(history[:, 3])), 1e-9
    )
    mismatch = np.abs(history[:, 4]) / scale
    report = {
        "maximum_relative_force_imbalance": float(mismatch.max()),
        "force_source": getattr(result, "force_source", "negative UZ reaction at the top platen pilot node"),
        "force_balance_warning": bool(
            np.any((mismatch > 0.01) & (scale > 1e-5))
        ),
    }
    return history, report


def _lateral_poisson_at_step(
    u: np.ndarray, pts: np.ndarray, top_uz: float
) -> tuple[float, float, float]:
    """Compute effective lateral Poisson ratios (nu_xz, nu_yz, nu_avg) from nodal displacements."""
    z_coords = pts[:, 2]
    z_min, z_max = float(z_coords.min()), float(z_coords.max())
    height = max(z_max - z_min, 1e-9)
    axial_comp_strain = -top_uz / height
    if abs(axial_comp_strain) < 1e-12:
        return 0.0, 0.0, 0.0

    # Evaluate lateral strain in the interior of the specimen away from the platen interfaces
    free_mask = z_coords > (z_min + 0.15 * height)
    if np.count_nonzero(free_mask) < 8:
        free_mask = np.ones(len(z_coords), dtype=bool)

    ratios = []
    for axis_idx in (0, 1):
        coord = pts[free_mask, axis_idx]
        disp = u[free_mask, axis_idx]
        c_centered = coord - float(np.mean(coord))
        denom = float(np.dot(c_centered, c_centered))
        if denom > 1e-12:
            lat_strain = float(np.dot(c_centered, disp - float(np.mean(disp))) / denom)
            ratios.append(lat_strain / axial_comp_strain)
        else:
            ratios.append(0.0)
    nu_xz, nu_yz = ratios[0], ratios[1]
    return float(nu_xz), float(nu_yz), float(0.5 * (nu_xz + nu_yz))


def export_field_results(
    result, mesh: FEMesh, folder: Path, images: bool = True
) -> dict:
    index = result.nsets - 1
    ids = mesh.grid.point_data["ansys_node_num"]
    u = result.displacement(index, ids)
    stress = result.equivalent_stress(index, ids)
    pts = np.asarray(mesh.grid.points, dtype=float)
    top_uz = result.top_platen_displacement(index)
    u_elastic = result.displacement(0, ids) if index else u
    nu_xz_el, nu_yz_el, nu_eff_el = _lateral_poisson_at_step(
        u_elastic, pts, result.top_platen_displacement(0)
    )
    nu_xz_fin, nu_yz_fin, nu_eff_fin = _lateral_poisson_at_step(u, pts, top_uz)

    grid = mesh.grid.copy(deep=True)
    grid.point_data["Displacement_mm"] = u
    grid.point_data["Displacement_Magnitude_mm"] = np.linalg.norm(u, axis=1)
    grid.point_data["Von_Mises_MPa"] = stress
    if getattr(result,'has_self_contact',False):
        grid.point_data['Self_Contact_Pressure_MPa']=result.contact_pressure(index,ids)
        grid.point_data['Self_Contact_Shear_MPa']=result.contact_pressure(index,ids,tangential=True)
    grid.save(folder / "results_undeformed.vtu")
    deformed = grid.copy(deep=True)
    deformed.points += u
    deformed.save(folder / "deformed_shape.vtu")
    cube_size = float(mesh.grid.bounds[5])
    settings=getattr(result,'settings',None)
    from standard_tests import fixture_surfaces, kind
    standard=isinstance(settings,Settings) and settings.test_profile != 'custom'
    bottom_platen, top_platen_def = (fixture_surfaces(settings,top_uz) if standard else
        make_compression_platens(cube_size, top_uz, mesh.grid.bounds[:4]))
    if standard and kind(settings) != 'compression':
        nu_xz_el=nu_yz_el=nu_eff_el=nu_eff_fin=None
    bottom_platen.save(folder / "bottom_platen.stl")
    top_platen_def.save(folder / "top_platen_deformed.stl")
    if images:
        surface = deformed.extract_surface(algorithm="dataset_surface")
        save_image(
            surface,
            folder / "displacement.png",
            "Displacement_Magnitude_mm",
            platens=(bottom_platen, top_platen_def),
        )
        save_image(
            surface,
            folder / "von_mises.png",
            "Von_Mises_MPa",
            platens=(bottom_platen, top_platen_def),
        )
    report = {
        "result_index": index,
        "saved_time": float(result.time_values[index]),
        "maximum_displacement_mm": float(np.linalg.norm(u, axis=1).max()),
        "maximum_von_mises_MPa": float(stress.max()),
        "effective_poisson_ratio": nu_eff_el,
        "effective_poisson_ratio_xz": nu_xz_el,
        "effective_poisson_ratio_yz": nu_yz_el,
        "final_poisson_ratio": nu_eff_fin,
        "deformation_scale": 1.0,
        "stress_definition": result.stress_definition,
    }
    if getattr(result,'has_self_contact',False):
        report['self_contact']=result.steps[index]['self_contact']
    write_json(folder / "summary.json", report)
    return report


def export_graphs(
    history: np.ndarray,
    settings: Settings,
    folder: Path,
    mesh: FEMesh | None = None,
    images: bool = True,
    result=None,
) -> dict:
    from standard_tests import kind, export_standard_graphs
    if kind(settings) != 'compression':
        return export_standard_graphs(history,settings,folder,mesh,images,result)
    x, force = history[:, 1], history[:, 2]
    work = np.r_[
        0.0, np.cumsum((force[1:] + force[:-1]) * np.diff(x) / 2000)
    ]
    area_mm2 = float(settings.size**2)
    nominal_vol_mm3 = float(settings.size**3)
    if settings.imported_geometry_path:
        bounds = (mesh.grid.bounds if mesh is not None else make_surface(settings).bounds)
        area_mm2 = float((bounds[1]-bounds[0]) * (bounds[3]-bounds[2]))
        nominal_vol_mm3 = area_mm2 * settings.size
    rel_density = (
        float(mesh.fraction) / 100.0
        if mesh is not None
        else float(settings.solid_fraction) / 100.0
    )
    solid_vol_mm3 = nominal_vol_mm3 * max(rel_density, 1e-6)
    # 1 g/cm^3 = 1e-3 g/mm^3
    mass_g = float(getattr(settings, "material_density", 7.85)) * solid_vol_mm3 * 1e-3
    ea_j = float(work[-1])
    sea_j_per_g = ea_j / max(mass_g, 1e-9)
    vol_ea_j_per_cm3 = ea_j / max(nominal_vol_mm3 * 1e-3, 1e-9)

    strain = x / max(float(settings.size), 1e-9)
    nominal_stress_mpa = force / max(area_mm2, 1e-9)
    if len(strain) >= 2 and (strain[1] - strain[0]) > 1e-12:
        effective_modulus_mpa = float(
            (nominal_stress_mpa[1] - nominal_stress_mpa[0]) / (strain[1] - strain[0])
        )
    else:
        effective_modulus_mpa = 0.0

    with (folder / "force_displacement.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "Time",
                "Compression_mm",
                "Compression_Force_N",
                "Bottom_RFZ_N",
                "Force_Imbalance_N",
                "External_Work_J",
            ]
        )
        writer.writerows(np.column_stack((history, work)))

    # Save standardized 100-point stress-strain curve for Deep Learning
    if len(strain) >= 2 and strain[-1] > 0:
        std_strain = np.linspace(0.0, float(strain[-1]), 100)
        std_stress = np.interp(std_strain, strain, nominal_stress_mpa)
        with (folder / "standardized_stress_strain.csv").open(
            "w", newline="", encoding="utf-8"
        ) as stream:
            writer = csv.writer(stream)
            writer.writerow(["Compressive_Strain", "Nominal_Stress_MPa"])
            writer.writerows(np.column_stack((std_strain, std_stress)))

    if images:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        for y, label, filename in (
            (force, "Compression force (N)", "force_displacement.png"),
            (work, "External compression work (J)", "compression_work.png"),
        ):
            figure, axis = plt.subplots(figsize=(8, 5))
            axis.plot(x, y, linewidth=2, color="#247fa6")
            axis.set(xlabel="Compression (mm)", ylabel=label)
            axis.grid(alpha=0.25)
            figure.tight_layout()
            figure.savefig(folder / filename, dpi=200)
            plt.close(figure)
    return {
        "maximum_force_N": float(force.max()),
        "external_compression_work_J": ea_j,
        "energy_absorption_J": ea_j,
        "specific_energy_absorption_J_g": float(sea_j_per_g),
        "volumetric_energy_absorption_J_cm3": float(vol_ea_j_per_cm3),
        "specimen_mass_g": float(mass_g),
        "effective_young_modulus_MPa": float(effective_modulus_mpa),
        "reached_compression_mm": float(x[-1]),
        "final_force_N": float(force[-1]),
        "loading_mode": settings.loading_mode,
        "completed": loading_target_reached(settings, x[-1], force[-1], history[-1, 0]),
    }
