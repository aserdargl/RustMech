import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import grid_convergence as gc
from flow_analysis import FlowSettings
from model import Settings


class GciMathTests(unittest.TestCase):
    def test_celik_2008_worked_example(self):
        # Celik et al. (2008), Table 1: N = 18000, 8000, 4500 in 2-D, so h ~ N^-1/2.
        result = gc.gci_three_grids((1.0, 1.5, 2.0), (6.063, 5.972, 5.863), "u")
        self.assertEqual(result.status, "ok")
        self.assertAlmostEqual(result.order, 1.53, delta=.03)
        self.assertAlmostEqual(result.extrapolated, 6.1685, delta=.01)
        self.assertAlmostEqual(result.approx_relative_error, .015, delta=.001)
        self.assertAlmostEqual(result.gci_fine, .0214, delta=.002)
        self.assertTrue(result.monotonic)

    def test_exact_second_order_series_recovers_order_and_limit(self):
        h = (1.0, 1.5, 2.25)
        values = tuple(100 + .3 * x ** 2 for x in h)
        result = gc.gci_three_grids(h, values)
        self.assertAlmostEqual(result.order, 2.0, places=6)
        self.assertAlmostEqual(result.extrapolated, 100.0, places=6)
        self.assertAlmostEqual(result.asymptotic_ratio, 1.0, delta=.01)
        self.assertEqual(result.status, "ok")

    def test_oscillatory_series_is_flagged(self):
        result = gc.gci_three_grids((1, 1.5, 2.25), (5.0, 5.2, 5.1))
        self.assertEqual(result.status, "oscillatory")
        self.assertFalse(result.monotonic)

    def test_diverging_series_is_flagged(self):
        result = gc.gci_three_grids((1, 1.5, 2.25), (5.0, 5.4, 5.5))
        self.assertEqual(result.status, "diverging")

    def test_identical_solutions_are_reported_as_independent(self):
        result = gc.gci_three_grids((1, 1.5, 2.25), (5.0, 5.0, 5.0))
        self.assertEqual(result.status, "unresolved")
        self.assertEqual(result.gci_fine, 0.0)

    def test_two_grid_uses_assumed_order_and_safety_factor_three(self):
        result = gc.gci_two_grids((1.0, 2.0), (10.3, 11.0), order=2.0)
        self.assertEqual(result.status, "two-grid")
        self.assertAlmostEqual(result.gci_fine, 3 * abs((10.3 - 11.0) / 10.3) / 3, places=9)
        self.assertTrue(math.isnan(result.asymptotic_ratio))

    def test_inputs_are_validated(self):
        with self.assertRaises(ValueError):
            gc.gci_three_grids((1, 1, 2), (1, 2, 3))
        with self.assertRaises(ValueError):
            gc.gci_three_grids((1, 2), (1, 2, 3))
        with self.assertRaises(ValueError):
            gc.representative_cell_size(0, 10)

    def test_small_refinement_ratio_adds_a_note(self):
        result = gc.gci_three_grids((1.0, 1.1, 1.21), (5.0, 5.05, 5.1005))
        self.assertTrue(any("Refinement ratio" in note for note in result.notes))

    def test_result_serialises_without_nan(self):
        data = gc.gci_two_grids((1.0, 2.0), (10.3, 11.0), 2).as_dict()
        self.assertIsNone(data["asymptotic_ratio"])
        self.assertIsInstance(data["h"], list)

    def test_refined_cells_series(self):
        self.assertEqual(gc.refined_cells(24, 1.5, 3), [24, 16, 10])
        with self.assertRaises(ValueError):
            gc.refined_cells(8, 1.5, 3)
        with self.assertRaises(ValueError):
            gc.refined_cells(24, 1.5, 1)


class GciRunnerTests(unittest.TestCase):
    @staticmethod
    def fake_execute(specimen, flow, folder):
        n = flow.cells_across
        return {"status": "converged", "pressure_drop_specimen_Pa": 5.0 + 3.0 / n ** 2,
                "pipe_analysis": {"friction_factor_cfd": 0.3 + 1.0 / n ** 2},
                "mesh": {"fluid_cells": 100 * n ** 3}}

    def test_runner_recovers_second_order_convergence(self):
        flow = FlowSettings(cells_across=24, empty_pipe=True, pipe_length_mm=60)
        with tempfile.TemporaryDirectory() as directory:
            messages = []
            result = gc.run_flow_gci(Settings(), flow, directory, execute=self.fake_execute, progress=messages.append)
            self.assertAlmostEqual(result.order, 2.0, delta=1e-6)
            self.assertAlmostEqual(result.extrapolated, 5.0, delta=1e-6)
            self.assertEqual(len(messages), 3)
            self.assertTrue((Path(directory) / "gci.json").exists())
            rows = (Path(directory) / "gci.csv").read_text().splitlines()
            self.assertEqual(len(rows), 4)
            self.assertIn("GCI fine", gc.describe(result))

    def test_runner_supports_other_quantities_and_reports_unavailable_ones(self):
        flow = FlowSettings(cells_across=24, empty_pipe=True, pipe_length_mm=60)
        with tempfile.TemporaryDirectory() as directory:
            result = gc.run_flow_gci(Settings(), flow, directory, quantity="friction_factor", execute=self.fake_execute)
            self.assertAlmostEqual(result.extrapolated, 0.3, delta=1e-6)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError) as caught:
                gc.run_flow_gci(Settings(), flow, directory, quantity="darcy_permeability_m2", execute=self.fake_execute)
            self.assertIn("GCI level 1", str(caught.exception))
        with self.assertRaises(ValueError):
            gc.extract_quantity({}, "nonsense")

    def test_runner_names_the_failed_level(self):
        def fail(specimen, flow, folder):
            if flow.cells_across < 24:
                raise ValueError("boom")
            return self.fake_execute(specimen, flow, folder)
        flow = FlowSettings(cells_across=24, empty_pipe=True, pipe_length_mm=60)
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(RuntimeError, r"level 2 .*boom"):
            gc.run_flow_gci(Settings(), flow, directory, execute=fail)

    def test_non_converged_level_marks_result_provisional(self):
        def partial(specimen, flow, folder):
            summary = self.fake_execute(specimen, flow, folder)
            if flow.cells_across == 10:
                summary["status"] = "not_converged"
            return summary
        flow = FlowSettings(cells_across=24, empty_pipe=True, pipe_length_mm=60)
        with tempfile.TemporaryDirectory() as directory:
            result = gc.run_flow_gci(Settings(), flow, directory, execute=partial)
            self.assertTrue(any("provisional" in note for note in result.notes))

    def test_flow_settings_replace_keeps_solver_choice(self):
        flow = FlowSettings(cells_across=24, solver="kratos")
        self.assertEqual(replace(flow, cells_across=16).solver, "kratos")


if __name__ == "__main__":
    unittest.main()
