import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

import kratos_flow
from flow_analysis import FlowSettings, build_flow_grid, export_flow_case
from model import Settings

SMALL = dict(diameter_mm=4, inlet_length_mm=4, outlet_length_mm=4, pipe_length_mm=12, cells_across=8, empty_pipe=True,
             inlet_speed=.05, iterations=60, axial_aspect=3.0)


def small_grid(**changes):
    flow = FlowSettings(**{**SMALL, **changes})
    grid, metadata = build_flow_grid(Settings(), flow)
    return flow, grid, metadata


def tet_volumes(tets, points):
    a, b, c, d = (points[tets[:, i]] for i in range(4))
    return np.einsum("ij,ij->i", np.cross(b - a, c - a), d - a) / 6


class TetrahedralSplitTests(unittest.TestCase):
    def test_split_is_positive_and_volume_preserving(self):
        _, grid, _ = small_grid()
        conn = grid.cells.reshape(-1, 9)[:, 1:]
        tets = kratos_flow.hex_to_tets(conn, np.asarray(grid.points))
        self.assertEqual(len(tets), 6 * len(conn))
        volumes = tet_volumes(tets, np.asarray(grid.points))
        self.assertTrue((volumes > 0).all())
        hex_volume = float(grid.compute_cell_sizes(length=False, area=False, volume=True).cell_data["Volume"].sum())
        self.assertAlmostEqual(float(volumes.sum()) / hex_volume, 1.0, places=9)

    def test_split_is_conforming_for_the_tpms_clipped_mesh(self):
        _, grid, _ = small_grid(empty_pipe=False, diameter_mm=6, cells_across=16, pipe_length_mm=0.0, axial_aspect=1.0,
                                inlet_length_mm=3, outlet_length_mm=3)
        points = np.asarray(grid.points)
        tets = kratos_flow.hex_to_tets(grid.cells.reshape(-1, 9)[:, 1:], points)
        faces = np.sort(np.concatenate((tets[:, (1, 2, 3)], tets[:, (0, 3, 2)], tets[:, (0, 1, 3)], tets[:, (0, 2, 1)])), axis=1)
        _, counts = np.unique(faces, axis=0, return_counts=True)
        self.assertTrue(set(counts) <= {1, 2}, "a triangle is shared by more than two tetrahedra")
        boundary = kratos_flow.boundary_faces(tets)
        p = points[boundary]
        area = .5 * np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1).sum()
        surface = grid.extract_surface(algorithm="dataset_surface")
        self.assertAlmostEqual(float(area) / float(surface.area), 1.0, places=6)

    def test_boundary_faces_are_outward_oriented(self):
        _, grid, _ = small_grid()
        points = np.asarray(grid.points)
        tets = kratos_flow.hex_to_tets(grid.cells.reshape(-1, 9)[:, 1:], points)
        groups = kratos_flow.classify_faces(kratos_flow.boundary_faces(tets), points)
        inlet = points[groups["Inlet3D_inlet"]]
        normals = np.cross(inlet[:, 1] - inlet[:, 0], inlet[:, 2] - inlet[:, 0])
        self.assertTrue((normals[:, 2] < 0).all(), "inlet normals must point out of the domain (-z)")
        outlet = points[groups["Outlet3D_outlet"]]
        self.assertTrue((np.cross(outlet[:, 1] - outlet[:, 0], outlet[:, 2] - outlet[:, 0])[:, 2] > 0).all())
        self.assertEqual(sum(len(v) for v in groups.values()), len(kratos_flow.boundary_faces(tets)))

    def test_inlet_scale_restores_nominal_flow(self):
        _, grid, _ = small_grid()
        points = np.asarray(grid.points)
        tets = kratos_flow.hex_to_tets(grid.cells.reshape(-1, 9)[:, 1:], points)
        groups = kratos_flow.classify_faces(kratos_flow.boundary_faces(tets), points)
        scale = kratos_flow.inlet_flux_scale(points, groups)
        self.assertGreater(scale, 1.0)
        self.assertLess(scale, 1.6)


class KratosExportTests(unittest.TestCase):
    def test_export_writes_a_consistent_kratos_case(self):
        flow = FlowSettings(**SMALL, solver="kratos")
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory) / "case"
            manifest = export_flow_case(Settings(), flow, case)
            for name in ("flow.mdpa", "ProjectParameters.json", "FluidMaterials.json", "run_kratos.py", "Allrun", "fluid_mesh.vtu", "flow_case.json"):
                self.assertTrue((case / name).exists(), name)
            self.assertFalse((case / "constant").exists(), "OpenFOAM files must not be written for Kratos")
            parameters = json.loads((case / "ProjectParameters.json").read_text())
            solver = parameters["solver_settings"]
            self.assertEqual(solver["time_scheme"], "steady")
            self.assertEqual(solver["domain_size"], 3)
            inlet = parameters["processes"]["boundary_conditions_process_list"][0]["Parameters"]["value"][2]
            self.assertAlmostEqual(inlet, flow.inlet_speed * manifest["kratos"]["inlet_velocity_scale"])
            materials = json.loads((case / "FluidMaterials.json").read_text())
            self.assertEqual(materials["properties"][0]["Material"]["Variables"]["DENSITY"], flow.density)
            text = (case / "flow.mdpa").read_text()
            for token in ("Begin Nodes", "Begin Elements Element3D4N", "Begin Conditions SurfaceCondition3D3N",
                          "Begin SubModelPart Inlet3D_inlet", "Begin SubModelPart NoSlip3D_walls", "Begin SubModelPart Outlet3D_outlet"):
                self.assertIn(token, text)
            self.assertEqual(text.count("\r"), 0)
            saved = json.loads((case / "flow_case.json").read_text())
            self.assertEqual(saved["flow"]["solver"], "kratos")
            self.assertEqual(FlowSettings(**saved["flow"]).solver, "kratos")
            self.assertIn("python3 run_kratos.py", (case / "Allrun").read_text())

    def test_openfoam_remains_the_default_solver(self):
        self.assertEqual(FlowSettings().solver, "openfoam")
        with tempfile.TemporaryDirectory() as directory:
            export_flow_case(Settings(), FlowSettings(**SMALL), Path(directory) / "case")
            self.assertTrue((Path(directory) / "case" / "constant" / "polyMesh" / "points").exists())

    def test_kratos_validation_rules(self):
        base = FlowSettings(**SMALL, solver="kratos")
        base.validate(Settings())
        with self.assertRaisesRegex(ValueError, "laminar"):
            replace(base, turbulence="kOmegaSST").validate(Settings())
        with self.assertRaisesRegex(ValueError, "Python"):
            replace(base, kratos_python="python3; rm -rf /").validate(Settings())
        with self.assertRaisesRegex(ValueError, "solver"):
            replace(base, solver="fluent").validate(Settings())
        replace(base, foam_bashrc="not-absolute").validate_local_runtime()
        with self.assertRaises(ValueError):
            replace(FlowSettings(**SMALL), foam_bashrc="x").validate_local_runtime()

    def test_residual_history_is_parsed(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = kratos_flow.write_kratos_residuals(directory, "STEP 2 dU=1.0e-03 dP=2.0e-02\nnoise\nSTEP 3 dU=1.0e-05 dP=2.0e-03\n")
            self.assertEqual(len(rows), 2)
            lines = (Path(directory) / "residuals.csv").read_text().splitlines()
            self.assertEqual(lines[0], "iteration,dU,dP")
            self.assertEqual(len(lines), 3)

    def test_results_are_mapped_onto_hexahedra(self):
        flow, grid, _ = small_grid()
        points = np.asarray(grid.points)
        with tempfile.TemporaryDirectory() as directory:
            velocity = np.zeros((grid.n_points, 3))
            velocity[:, 2] = .05
            pressure = 10.0 - points[:, 2] * 100
            np.savez(Path(directory) / "kratos_result.npz", velocity=velocity, pressure=pressure,
                     node_ids=np.arange(1, grid.n_points + 1), steps=5, converged=True)
            fields = kratos_flow.read_kratos_fields(directory, grid, flow)
            self.assertEqual(fields["velocity"].shape, (grid.n_cells, 3))
            self.assertAlmostEqual(fields["q_in"], fields["q_out"], places=12)
            self.assertAlmostEqual(fields["p_in"] - fields["p_out"], 100 * (points[:, 2].max() - points[:, 2].min()), places=9)
            self.assertTrue(fields["converged"])
            with self.assertRaises(RuntimeError):
                np.savez(Path(directory) / "kratos_result.npz", velocity=velocity[:5], pressure=pressure[:5], steps=1, converged=True)
                kratos_flow.read_kratos_fields(directory, grid, flow)


def _kratos_available():
    try:
        from flow_analysis import check_flow_runtime
        check_flow_runtime(FlowSettings(solver="kratos"))
        return True
    except Exception:
        return False


@unittest.skipUnless(_kratos_available(), "Kratos FluidDynamicsApplication in WSL is not available")
class KratosPipeValidation(unittest.TestCase):
    def test_laminar_pipe_matches_hagen_poiseuille(self):
        from flow_analysis import execute_flow_case
        flow = FlowSettings(diameter_mm=4, inlet_length_mm=12, outlet_length_mm=18, pipe_length_mm=60, inlet_speed=.05,
                            cells_across=12, iterations=100, empty_pipe=True, solver="kratos")
        base = Path(__file__).resolve().parent.parent / "build" / "kratos-validation-tests"
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            summary = execute_flow_case(Settings(), flow, Path(directory) / "case")
            analysis = summary["pipe_analysis"]
            self.assertEqual(summary["solver"], "kratos")
            self.assertEqual(summary["status"], "converged")
            self.assertTrue(analysis["fully_developed_fit"])
            self.assertLess(abs(analysis["friction_factor_error_pct"]), 4.0)
            self.assertAlmostEqual(analysis["centreline_to_mean_velocity"], 2.0, delta=.4)
            for name in ("axial_profile.csv", "radial_profile.csv", "residuals.csv", "flow_result.vtu", "flow_summary.json"):
                self.assertTrue((Path(directory) / "case" / name).exists(), name)


if __name__ == "__main__":
    unittest.main()
