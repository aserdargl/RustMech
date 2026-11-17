"""Pipe-flow meshing, analytical references and result post-processing (no solver needed)."""
from dataclasses import replace
import csv
import json
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np

from flow_analysis import (FlowSettings, build_flow_grid, export_flow_case, write_poly_mesh,
                           _write_residual_history)
import pipe_flow_theory as theory
import pipe_mesh
from model import Settings


class CrossSectionTests(unittest.TestCase):
    def test_cell_split_matches_requested_cells_across(self):
        for cells in range(8, 65):
            core, radial = pipe_mesh.split_cells_across(cells)
            self.assertEqual(core + 2 * radial, cells if (cells - core) % 2 == 0 else cells - 1)
            self.assertGreaterEqual(core, 2)

    def test_wall_fitted_section_is_valid_and_conforming(self):
        points, quads, info = pipe_mesh.cross_section(4.0, 16)
        self.assertEqual(len(quads), info["cells_per_layer"])
        self.assertTrue(np.all(info["areas_mm2"] > 0))
        # Polygonal wall slightly under-fills the circle; refinement closes the gap.
        self.assertAlmostEqual(info["section_area_mm2"] / (math.pi * 4), 1.0, delta=.01)
        self.assertGreater(info["min_angle_deg"], 45)
        self.assertLess(info["max_angle_deg"], 140)
        radius = np.linalg.norm(points, axis=1)
        self.assertAlmostEqual(radius.max(), 2.0, places=9)
        # Interior edges are shared by exactly two quads: adjacency count of a conforming mesh.
        edges = 4 * len(quads)
        boundary_edges = 4 * (info["core_cells"])
        self.assertEqual(len(pipe_mesh.quad_adjacency(quads)), (edges - boundary_edges) // 2)

    def test_finer_mesh_is_closer_to_circle(self):
        coarse = pipe_mesh.cross_section(4.0, 8)[2]["section_area_mm2"]
        fine = pipe_mesh.cross_section(4.0, 32)[2]["section_area_mm2"]
        self.assertLess(abs(fine - math.pi * 4), abs(coarse - math.pi * 4))

    def test_wall_grading_thins_cells_towards_the_wall(self):
        uniform = pipe_mesh.cross_section(4.0, 32, 1.0)[2]
        graded = pipe_mesh.cross_section(4.0, 32, 4.0)[2]
        self.assertLess(graded["first_cell_centre_mm"], uniform["first_cell_centre_mm"])
        fractions = pipe_mesh.radial_fractions(6, 4.0)
        widths = np.diff(fractions)
        self.assertAlmostEqual(widths[0] / widths[-1], 4.0)
        self.assertAlmostEqual(fractions[-1], 1.0)

    def test_invalid_core_fraction_is_rejected(self):
        with self.assertRaises(ValueError):
            pipe_mesh.cross_section(4.0, 16, core_fraction=.9)


class PipeGridTests(unittest.TestCase):
    def test_empty_pipe_uses_own_length_and_has_positive_volumes(self):
        flow = FlowSettings(cells_across=12, diameter_mm=4, empty_pipe=True, pipe_length_mm=30)
        grid, mesh = build_flow_grid(Settings(), flow)
        volume = grid.compute_cell_sizes(length=False, area=False, volume=True).cell_data["Volume"]
        self.assertGreater(volume.min(), 0)
        self.assertEqual(mesh["specimen_end_mm"], 30)
        self.assertAlmostEqual(grid.bounds[5], (30 + flow.outlet_length_mm) * 1e-3)
        self.assertAlmostEqual(grid.volume, volume.sum())
        self.assertEqual(mesh["mesh_style"], "ogrid")
        self.assertAlmostEqual(mesh["effective_diameter_mm"], 4, delta=.04)

    def test_zero_pipe_length_falls_back_to_cube_size(self):
        _, mesh = build_flow_grid(Settings(), FlowSettings(cells_across=8, empty_pipe=True))
        self.assertEqual(mesh["specimen_end_mm"], Settings().size)

    def test_tpms_fill_keeps_a_connected_path_and_exact_pipe_wall(self):
        flow = FlowSettings(cells_across=16)
        grid, mesh = build_flow_grid(Settings(), flow)
        empty, _ = build_flow_grid(Settings(), replace(flow, empty_pipe=True, axial_aspect=1.0))
        self.assertLess(grid.n_cells, empty.n_cells)
        self.assertGreater(mesh["solid_cells"], 0)
        self.assertTrue(0 < mesh["specimen_solid_fraction"] < 1)
        self.assertEqual(grid.bounds, empty.bounds)
        layers = grid.cell_data["grid_k"]
        self.assertEqual(layers.min(), 0)
        self.assertEqual(layers.max(), mesh["layers"] - 1)

    def test_every_cell_has_six_faces_in_the_exported_polymesh(self):
        flow = FlowSettings(cells_across=8, diameter_mm=4, empty_pipe=True, pipe_length_mm=6, inlet_length_mm=4, outlet_length_mm=4)
        grid, _ = build_flow_grid(Settings(), flow)
        with tempfile.TemporaryDirectory() as directory:
            write_poly_mesh(grid, Path(directory))
            owner = [int(v) for v in (Path(directory) / "owner").read_text().split("(")[1].split(")")[0].split()]
            neighbour = [int(v) for v in (Path(directory) / "neighbour").read_text().split("(")[1].split(")")[0].split()]
            counts = np.bincount(owner + neighbour, minlength=grid.n_cells)
            self.assertTrue(np.all(counts == 6))
            boundary = (Path(directory) / "boundary").read_text()
            for name in ("inlet", "outlet", "walls"):
                self.assertIn(name, boundary)

    def test_new_settings_are_validated(self):
        base = FlowSettings()
        for change in (dict(mesh_style="polar"), dict(convection="central"), dict(wall_grading=.5),
                       dict(axial_aspect=100.0), dict(pipe_length_mm=-1.0), dict(pipe_length_mm=float("nan")),
                       dict(wall_grading=True)):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(base, **change).validate(Settings())

    def test_empty_pipe_may_exceed_the_cube(self):
        flow = FlowSettings(diameter_mm=12, cells_across=16, empty_pipe=True, pipe_length_mm=20)
        flow.validate(Settings())
        with self.assertRaises(ValueError):
            replace(flow, empty_pipe=False).validate(Settings())

    def test_old_saved_cases_still_load_into_settings(self):
        saved = {key: value for key, value in FlowSettings().__dict__.items()
                 if key not in ("mesh_style", "pipe_length_mm", "axial_aspect", "wall_grading", "convection")}
        self.assertEqual(FlowSettings(**saved), FlowSettings())

    def test_cell_estimate_is_conservative(self):
        flow = FlowSettings(cells_across=12, diameter_mm=4, empty_pipe=True, pipe_length_mm=30)
        grid, _ = build_flow_grid(Settings(), flow)
        self.assertEqual(flow.estimated_cells(Settings()), grid.n_cells)

    def test_cartesian_style_is_still_available(self):
        grid, mesh = build_flow_grid(Settings(), FlowSettings(cells_across=8, empty_pipe=True, mesh_style="cartesian"))
        self.assertGreater(grid.n_cells, 0)
        self.assertNotIn("mesh_style", mesh)


class ExportTests(unittest.TestCase):
    def test_second_order_and_first_order_schemes_are_written(self):
        for scheme, expected, absent in (("linearUpwind", "linearUpwind grad(U)", None), ("upwind", "bounded Gauss upwind", "linearUpwind")):
            with self.subTest(scheme=scheme), tempfile.TemporaryDirectory() as directory:
                flow = FlowSettings(cells_across=8, empty_pipe=True, convection=scheme)
                manifest = export_flow_case(Settings(), flow, Path(directory) / "case")
                text = (Path(directory) / "case/system/fvSchemes").read_text()
                self.assertIn(expected, text)
                if absent:
                    self.assertNotIn(absent, text)
                self.assertEqual(manifest["flow"]["convection"], scheme)
                self.assertIn("O-grid", manifest["discretization"])
                self.assertEqual(json.loads((Path(directory) / "case/flow_case.json").read_text())["mesh"]["mesh_style"], "ogrid")

    def test_residual_history_is_extracted(self):
        log = "\n".join(("Time = 1", "Solving for Ux, Initial residual = 1, Final residual = 0.1, No Iterations 2",
                         "Solving for p, Initial residual = 1, Final residual = 0.01, No Iterations 4",
                         "Time = 2", "Solving for Ux, Initial residual = 0.5, Final residual = 0.05, No Iterations 2",
                         "Solving for p, Initial residual = 3.5e-1, Final residual = 0.01, No Iterations 4"))
        with tempfile.TemporaryDirectory() as directory:
            _write_residual_history(directory, log)
            with (Path(directory) / "residuals.csv").open() as stream:
                rows = list(csv.reader(stream))
        self.assertEqual(rows[0], ["iteration", "Ux", "p"])
        self.assertEqual(rows[2], ["2.0", "0.5", "0.35"])


class TheoryTests(unittest.TestCase):
    def test_friction_factor_references(self):
        self.assertAlmostEqual(theory.darcy_friction_factor(1000), .064)
        self.assertIsNone(theory.darcy_friction_factor(3000))
        self.assertAlmostEqual(theory.darcy_friction_factor(1e5), .01799, places=4)
        self.assertAlmostEqual(theory.darcy_friction_factor(5e3), .0374, places=3)
        with self.assertRaises(ValueError):
            theory.darcy_friction_factor(0)

    def test_hagen_poiseuille_matches_laminar_friction_factor(self):
        mu, rho, u, d = .001, 1000, .05, .004
        re = theory.reynolds(rho, u, d, mu)
        gradient = theory.hagen_poiseuille_gradient(mu, u, d)
        f = gradient * 2 * d / (rho * u * u)
        self.assertAlmostEqual(f, 64 / re, places=10)

    def test_entrance_length_scaling(self):
        self.assertAlmostEqual(theory.developing_length_mm(200, 4), 40)
        self.assertGreater(theory.developing_length_mm(1e5, 4), 4 * 4.4)

    def test_gradient_fit_recovers_a_line_and_rejects_short_ranges(self):
        z = np.linspace(0, 100, 51)
        slope, r2, count = theory.fit_gradient(z, 500 - 120 * z * 1e-3, 20, 90)
        self.assertAlmostEqual(slope, -120)
        self.assertAlmostEqual(r2, 1)
        self.assertIsNone(theory.fit_gradient(z, z, 99, 100))

    def test_empty_pipe_analysis_reports_zero_error_for_exact_data(self):
        flow = FlowSettings(diameter_mm=4, inlet_speed=.05, empty_pipe=True)
        d = flow.diameter_mm * 1e-3
        gradient = theory.hagen_poiseuille_gradient(flow.dynamic_viscosity, flow.inlet_speed, d)
        z = np.linspace(-12, 100, 200)
        result = theory.analyze_empty_pipe(flow, 4.0, z, -gradient * z * 1e-3, 1.0)
        self.assertTrue(result["fully_developed_fit"])
        self.assertLess(abs(result["friction_factor_error_pct"]), 1e-6)
        self.assertAlmostEqual(result["wall_shear_stress_Pa"], gradient * d / 4)

    def test_short_pipe_is_flagged_as_not_fully_developed(self):
        flow = FlowSettings(diameter_mm=4, inlet_speed=.05, empty_pipe=True)
        z = np.linspace(0, 20, 100)
        result = theory.analyze_empty_pipe(flow, 4.0, z, -50 * z, 1.0)
        self.assertFalse(result["fully_developed_fit"])
        self.assertTrue(result["notes"])

    def test_darcy_permeability_and_forchheimer_note(self):
        flow = FlowSettings(inlet_speed=.01)
        low = theory.analyze_porous(flow, .7, 6.0, 7.0, 6.0)
        self.assertAlmostEqual(low["darcy_permeability_m2"], flow.dynamic_viscosity * .01 * .006 / 7.0)
        high = theory.analyze_porous(replace(flow, inlet_speed=1.0), .7, 6.0, .1, 6.0)
        self.assertTrue(high["notes"])
        self.assertTrue(theory.analyze_porous(flow, .7, 6.0, -1.0, 6.0)["notes"])

    def test_radial_profile_is_volume_weighted(self):
        r = np.array([.1, .2, 1.9])
        rows = theory.radial_profile(r, np.array([2.0, 4.0, 1.0]), np.array([1.0, 3.0, 1.0]), 2.0, bins=4)
        self.assertAlmostEqual(rows[0, 1], 3.5)
        self.assertEqual(len(rows), 2)

    def test_y_plus_scales_with_first_cell_height(self):
        a = theory.estimate_y_plus(1e4, 1000, 1.0, .001, .1)
        b = theory.estimate_y_plus(1e4, 1000, 1.0, .001, .2)
        self.assertAlmostEqual(b / a, 2)


def _openfoam_available():
    try:
        from flow_analysis import check_flow_runtime
        check_flow_runtime(FlowSettings())
        return True
    except Exception:
        return False


@unittest.skipUnless(_openfoam_available(), "OpenFOAM in WSL is not available")
class OpenFoamPipeValidation(unittest.TestCase):
    def test_laminar_pipe_matches_hagen_poiseuille(self):
        from flow_analysis import execute_flow_case
        flow = FlowSettings(diameter_mm=4, inlet_length_mm=12, outlet_length_mm=18, pipe_length_mm=60,
                            inlet_speed=.05, cells_across=16, iterations=2000, empty_pipe=True)
        base = Path(__file__).resolve().parent.parent / "build" / "pipe-validation-tests"
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            summary = execute_flow_case(Settings(), flow, Path(directory) / "case")
            analysis = summary["pipe_analysis"]
            self.assertEqual(summary["status"], "converged")
            self.assertTrue(analysis["fully_developed_fit"])
            self.assertLess(abs(analysis["friction_factor_error_pct"]), 3.0)
            self.assertAlmostEqual(analysis["centreline_to_mean_velocity"], 2.0, delta=.2)
            for name in ("axial_profile.csv", "radial_profile.csv", "residuals.csv"):
                self.assertTrue((Path(directory) / "case" / name).exists())


if __name__ == "__main__":
    unittest.main()
