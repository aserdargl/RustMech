"""Exact geometry, connectivity, stable selection and native-boundary checks."""

import gc
import os
import pickle
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pyvista as pv

import nonlinear_compression as studio
from benchmark_mesh import (
    reference_field,
    reference_grid,
    reference_select,
    reference_volume_mesh,
)
from benchmark_tetra import reference_thin_walls
from mapdl_backend import write_apdl_mesh
from model import HEX_EDGES, Settings
from rust_mesh import _kernel, build_hex_grid, select_voxels
from structure_library import TPMS_ITEMS, evaluate_priority_field
from tetrahedral_mesh import (
    _clean_boundary,
    _thin_wall_samples,
    generate_tetrahedral_mesh,
)


class RustMeshTests(unittest.TestCase):
    def assert_grid_equal(self, actual, expected):
        np.testing.assert_array_equal(actual.points, expected.points)
        np.testing.assert_array_equal(actual.cells, expected.cells)
        np.testing.assert_array_equal(actual.celltypes, expected.celltypes)
        self.assertEqual(set(actual.point_data), set(expected.point_data))
        self.assertEqual(set(actual.cell_data), set(expected.cell_data))
        for name in expected.point_data:
            np.testing.assert_array_equal(
                actual.point_data[name], expected.point_data[name]
            )
        for name in expected.cell_data:
            np.testing.assert_array_equal(
                actual.cell_data[name], expected.cell_data[name]
            )

    def test_selection_matches_stable_sort_for_ties_and_random_fields(self):
        rng = np.random.default_rng(829)
        for shape in [(1, 1, 4), (2, 3, 7), (8, 8, 8), (7, 11, 5)]:
            arrays = [
                rng.standard_normal(shape),
                rng.integers(-2, 3, shape),
                np.zeros(shape),
            ]
            for values in arrays:
                for count in [
                    1,
                    max(1, values.size // 3),
                    values.size - 1,
                    values.size,
                ]:
                    with self.subTest(shape=shape, dtype=values.dtype, count=count):
                        actual = select_voxels(values, count)
                        expected = reference_select(values, count)
                        np.testing.assert_array_equal(actual[0], expected[0])
                        np.testing.assert_array_equal(actual[1], expected[1])
                        self.assertEqual(actual[2], expected[2])

    def test_diagonal_components_and_equal_component_sizes(self):
        values = np.ones((2, 2, 2))
        values[0, 0, 1] = values[0, 1, 0] = values[1, 1, 1] = 0
        retained, removed, components = select_voxels(values, 3)
        self.assertEqual(components, 3)
        self.assertEqual(np.argwhere(retained).tolist(), [[0, 0, 1]])
        self.assertEqual(int(removed.sum()), 2)

    def test_noncontiguous_and_unaligned_input(self):
        values = np.arange(60.0).reshape(3, 4, 5).transpose(2, 0, 1)[::-1]
        retained, removed, components = select_voxels(values, 17)
        expected = reference_select(values, 17)
        np.testing.assert_array_equal(retained, expected[0])
        np.testing.assert_array_equal(removed, expected[1])
        self.assertEqual(components, expected[2])
        storage = bytearray(8 * values.size + 1)
        unaligned = np.ndarray(values.shape, dtype=np.float64, buffer=storage, offset=1)
        unaligned[:] = values
        np.testing.assert_array_equal(select_voxels(unaligned, 17)[0], expected[0])
        self.assert_grid_equal(
            build_hex_grid(retained.T, 0.125), reference_grid(retained.T, 0.125)
        )

    def test_grids_match_numpy_with_shared_nodes_for_both_orders(self):
        rng = np.random.default_rng(838)
        for shape in [(1, 1, 1), (2, 3, 4), (9, 8, 7)]:
            for solid in [
                np.zeros(shape, bool),
                np.ones(shape, bool),
                rng.random(shape) < 0.35,
            ]:
                for quadratic in [False, True]:
                    with self.subTest(
                        shape=shape, quadratic=quadratic, count=solid.sum()
                    ):
                        self.assert_grid_equal(
                            build_hex_grid(solid, 0.137, quadratic=quadratic),
                            reference_grid(solid, 0.137, quadratic=quadratic),
                        )

    def test_quadratic_midpoints_and_positive_jacobians(self):
        grid = build_hex_grid(np.ones((2, 3, 4), bool), 0.2, quadratic=True)
        cells = grid.cell_connectivity.reshape(-1, 20)
        for index, (a, b) in enumerate(HEX_EDGES, start=8):
            np.testing.assert_allclose(
                grid.points[cells[:, index]],
                (grid.points[cells[:, a]] + grid.points[cells[:, b]]) / 2,
                rtol=0,
                atol=1e-15,
            )
        quality = grid.linear_copy().cell_quality(["scaled_jacobian", "volume"])
        self.assertTrue(np.all(quality.cell_data["scaled_jacobian"] > 0.999999))
        self.assertTrue(np.all(quality.cell_data["volume"] > 0))

    def test_tpms_sparse_axes_match_original_dense_evaluation(self):
        axis = np.linspace(-0.17, 6.2, 19)
        for name in TPMS_ITEMS:
            for morphology in ["sheet", "network_a", "network_b"]:
                settings = replace(
                    Settings(), structure_name=name, tpms_morphology=morphology
                )
                for coords in [
                    (axis[:, None, None], axis[None, :, None], axis[None, None, :]),
                    (axis, axis[::-1], 0.5 * axis),
                ]:
                    with self.subTest(name=name, morphology=morphology):
                        np.testing.assert_array_equal(
                            evaluate_priority_field(settings, *coords),
                            reference_field(settings, *coords),
                        )

    def test_full_mesh_and_contact_nodes_for_different_structure_families(self):
        for family, name in [
            ("tpms", "Gyroid"),
            ("lattice", "BCC"),
            ("porous", "Voronoi Cell"),
        ]:
            for mesh_type in ["HEX8", "HEX20"]:
                settings = replace(
                    Settings(),
                    mesh_resolution=0.3,
                    structure_family=family,
                    structure_name=name,
                    mesh_type=mesh_type,
                )
                with self.subTest(family=family, mesh_type=mesh_type):
                    actual, expected = (
                        studio.generate_volume_mesh(settings),
                        reference_volume_mesh(settings),
                    )
                    self.assert_grid_equal(actual.grid, expected.grid)
                    self.assert_grid_equal(actual.removed_grid, expected.removed_grid)
                    np.testing.assert_array_equal(
                        actual.bottom_ids, expected.bottom_ids
                    )
                    np.testing.assert_array_equal(actual.top_ids, expected.top_ids)
                    self.assertEqual(
                        actual.original_components, expected.original_components
                    )
                    self.assertEqual(actual.fraction, expected.fraction)
                    self.assertEqual(
                        actual.candidate_elements, expected.candidate_elements
                    )
                    self.assertEqual(actual.solve_errors, expected.solve_errors)

    def test_apdl_exports_are_identical(self):
        with tempfile.TemporaryDirectory() as folder:
            actual_path, expected_path = (
                Path(folder) / "rust.inp",
                Path(folder) / "reference.inp",
            )
            for mesh_type in ["HEX8", "HEX20"]:
                settings = replace(Settings(), mesh_resolution=0.5, mesh_type=mesh_type)
                write_apdl_mesh(studio.generate_volume_mesh(settings), actual_path)
                write_apdl_mesh(reference_volume_mesh(settings), expected_path)
                self.assertEqual(actual_path.read_bytes(), expected_path.read_bytes())

    def test_result_arrays_survive_native_handle_and_pickle(self):
        grid = build_hex_grid(np.ones((3, 4, 5), bool), 0.125, quadratic=True)
        gc.collect()
        restored = pickle.loads(pickle.dumps(grid))
        del grid
        gc.collect()
        expected = reference_grid(np.ones((3, 4, 5), bool), 0.125, quadratic=True)
        self.assert_grid_equal(restored, expected)
        self.assert_grid_equal(
            restored.extract_surface(
                algorithm="dataset_surface"
            ).cast_to_unstructured_grid(),
            expected.extract_surface(
                algorithm="dataset_surface"
            ).cast_to_unstructured_grid(),
        )

    def test_independent_native_calls_from_threads(self):
        def generate(seed):
            values = np.random.default_rng(seed).standard_normal((12, 11, 10))
            mask, _, _ = select_voxels(values, 400)
            return build_hex_grid(mask, 0.1, quadratic=True), reference_grid(
                reference_select(values, 400)[0], 0.1, quadratic=True
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            for actual, expected in pool.map(generate, range(12)):
                self.assert_grid_equal(actual, expected)

    def test_invalid_inputs_raise_python_errors(self):
        for values in [np.zeros((2, 2)), np.zeros((0, 2, 2))]:
            with self.assertRaises(ValueError):
                select_voxels(values, 1)
            with self.assertRaises(ValueError):
                build_hex_grid(values, 0.1)
        for value in [np.nan, np.inf, -np.inf]:
            with self.assertRaisesRegex(ValueError, "finite"):
                select_voxels(np.full((2, 2, 2), value), 1)
        for count in [-1, 9, 1.5, True]:
            with self.assertRaises(ValueError):
                select_voxels(np.zeros((2, 2, 2)), count)
        for spacing in [0, -1, np.nan, np.inf]:
            with self.assertRaises(ValueError):
                build_hex_grid(np.ones((2, 2, 2)), spacing)
        retained, removed, components = select_voxels(np.zeros((2, 2, 2)), 0)
        self.assertFalse(retained.any() or removed.any() or components)

    def test_missing_kernel_reports_build_instruction(self):
        _kernel.cache_clear()
        try:
            with patch.dict(
                os.environ, {"RUSTMECH_CORE_LIBRARY": "missing-mesh-library.dll"}
            ):
                with self.assertRaisesRegex(RuntimeError, "Build-MeshKernel"):
                    build_hex_grid(np.ones((2, 2, 2)), 0.1)
        finally:
            _kernel.cache_clear()

    def test_tetrahedral_dispatch_does_not_require_rust_hex_kernel(self):
        import mesh_engine
        surface = pv.Sphere()
        for mesh_type in ["TET4", "TET10"]:
            settings = replace(Settings(), mesh_type=mesh_type)
            with patch.object(
                mesh_engine, "generate_tetrahedral_mesh", return_value="tetra"
            ) as mesher:
                with patch.object(
                    mesh_engine,
                    "select_voxels",
                    side_effect=AssertionError("unexpected HEX kernel call"),
                ):
                    self.assertEqual(
                        studio.generate_volume_mesh(settings, surface), "tetra"
                    )
            mesher.assert_called_once_with(settings, surface, on_progress=None, periodic_threshold=None)

    def test_thin_wall_samples_match_original_rays_exactly(self):
        for surface in [
            pv.Box(bounds=(0, 1, 0, 1, 0, 1)).triangulate(),
            pv.Sphere(
                radius=0.4,
                center=(0.5, 0.5, 0.5),
                theta_resolution=12,
                phi_resolution=12,
            ),
        ]:
            boundary = _clean_boundary(surface, 1.0, 1e-6)
            np.testing.assert_array_equal(
                np.asarray(_thin_wall_samples(boundary, 0.4, 0.1)),
                np.asarray(reference_thin_walls(boundary, 0.4, 0.1)),
            )

    def test_netgen_linear_and_quadratic_meshes(self):
        try:
            import netgen.meshing  # noqa: F401
        except ImportError:
            self.skipTest("Optional netgen-mesher package is not installed")
        surface = pv.Box(bounds=(0, 1, 0, 1, 0, 1)).triangulate()
        for mesh_type, cell_type in [
            ("TET4", pv.CellType.TETRA),
            ("TET10", pv.CellType.QUADRATIC_TETRA),
        ]:
            settings = replace(
                Settings(),
                size=1.0,
                cell_size=0.5,
                mesh_type=mesh_type,
                mesh_resolution=0.5,
                local_mesh_size=0.4,
            )
            mesh = generate_tetrahedral_mesh(settings, surface)
            self.assertTrue(np.all(mesh.grid.celltypes == cell_type))
            self.assertEqual(mesh.solve_errors, [])
            self.assertAlmostEqual(mesh.fraction, 100, places=8)


if __name__ == "__main__":
    unittest.main()
