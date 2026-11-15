"""Geometry, conformity, independent buffers and invalidation of tetra reuse."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np
import pyvista as pv

import netgen_io
from model import Settings, TET_EDGES
from rust_mesh import quadratic_tetra_arrays, tet_metrics, triangle_normals
import tetrahedral_mesh as tetra


class TetraAccelerationTests(unittest.TestCase):
    def setUp(self):
        tetra.clear_tetrahedral_cache()
        self.settings = replace(
            Settings(),
            size=1.0,
            cell_size=0.5,
            mesh_type="TET4",
            mesh_resolution=0.5,
            local_mesh_size=0.3,
        )
        self.surface = pv.Box(bounds=(0, 1, 0, 1, 0, 1)).triangulate()

    def tearDown(self):
        tetra.clear_tetrahedral_cache()

    def test_rust_elevation_matches_netgen_element_geometry(self):
        from netgen.meshing import MeshingParameters

        boundary = tetra._clean_boundary(self.surface, 1.0, 1e-6)
        native = netgen_io.geometry_from_surface(boundary).GenerateMesh(
            mp=MeshingParameters(maxh=0.4)
        )
        linear = tetra._read_grid(native.Copy(), "TET4")
        reference = tetra._read_grid(native.Copy(), "TET10")
        corners = np.asarray(linear.cell_connectivity).reshape(-1, 4)
        points, cells = quadratic_tetra_arrays(linear.points, corners)
        expected = reference.points[
            np.asarray(reference.cell_connectivity).reshape(-1, 10)
        ]
        np.testing.assert_array_equal(points[cells], expected)
        self.assertEqual(len(points), reference.n_points)
        shared_edges = {}
        for cell in cells:
            for slot, (a, b) in enumerate(TET_EDGES, start=4):
                edge = tuple(sorted((cell[a], cell[b])))
                self.assertEqual(shared_edges.setdefault(edge, cell[slot]), cell[slot])
        self.assertEqual(len(points), linear.n_points + len(shared_edges))

    def test_rust_elevation_rejects_invalid_indices_and_coordinates(self):
        points = np.eye(4, 3)
        for cells in ([[0, 1, 2, 2]], [[0, 1, 2, -1]], [[0, 1, 2, 4]], [[0, 1, 2]]):
            with self.assertRaises(ValueError):
                quadratic_tetra_arrays(points, cells)
        points[0, 0] = np.nan
        with self.assertRaises(ValueError):
            quadratic_tetra_arrays(points, [[0, 1, 2, 3]])

    def test_facet_normals_match_vtk_double_precision_for_curved_triangles(self):
        from vtkmodules.vtkCommonDataModel import vtkTriangle

        rng = np.random.default_rng(35)
        points = rng.normal(size=(300, 3))
        faces = np.arange(300).reshape(-1, 3)
        expected = []
        for face in faces:
            normal = [0.0, 0.0, 0.0]
            vtkTriangle.ComputeNormal(*points[face], normal)
            expected.append(normal)
        np.testing.assert_array_equal(triangle_normals(points, faces), expected)
        with self.assertRaises(ValueError):
            triangle_normals(points, [[0, 1, 300]])

    def test_order_switch_reuses_linear_mesh_with_independent_buffers(self):
        with patch.object(
            tetra, "_mesh_boundary", wraps=tetra._mesh_boundary
        ) as generator:
            first = tetra.generate_tetrahedral_mesh(self.settings, self.surface)
            original = np.array(first.grid.points)
            first.grid.points[:] = -90
            first.grid.cell_connectivity[:] = 0
            quadratic = tetra.generate_tetrahedral_mesh(
                replace(self.settings, mesh_type="TET10"), self.surface
            )
            again = tetra.generate_tetrahedral_mesh(
                replace(self.settings, young_modulus=123.0), self.surface
            )
            self.assertEqual(generator.call_count, 1)
            np.testing.assert_array_equal(again.grid.points, original)
            self.assertTrue(np.all(tet_metrics(again.grid)[0] > 0))
            self.assertEqual(quadratic.grid.n_cells, again.grid.n_cells)
            self.assertEqual(quadratic.solve_errors, [])
            self.assertAlmostEqual(quadratic.fraction, 100.0, places=8)
            np.testing.assert_array_equal(
                quadratic.grid.points[quadratic.bottom_ids - 1, 2], 0.0
            )
            np.testing.assert_array_equal(
                quadratic.grid.points[quadratic.top_ids - 1, 2], 1.0
            )

    def test_cache_invalidates_for_all_sampling_inputs_and_actual_geometry(self):
        # Keep this focused on invalidation; real Netgen runs are checked above.
        grid = pv.UnstructuredGrid(
            [4, 0, 1, 2, 3],
            [pv.CellType.TETRA],
            np.array([[0.0, 0, 0], [1.0, 0, 0], [0.0, 1, 0], [0.0, 0, 1]]),
        )
        with patch.object(tetra, "_mesh_boundary", return_value=grid) as generator:
            tetra.generate_tetrahedral_mesh(self.settings, self.surface)
            for name, value in (
                ("mesh_resolution", 0.6),
                ("local_mesh_size", 0.2),
                ("cell_size", 0.75),
                ("size", 2.0),
            ):
                tetra.generate_tetrahedral_mesh(
                    replace(self.settings, **{name: value}), self.surface
                )
            changed = self.surface.copy()
            changed.points[0, 0] += 0.01
            tetra.generate_tetrahedral_mesh(self.settings, changed)
            self.assertEqual(generator.call_count, 6)
            tetra.generate_tetrahedral_mesh(self.settings, changed, use_cache=False)
            self.assertEqual(generator.call_count, 7)

    def test_cache_has_a_memory_limit_and_serializes_equivalent_calls(self):
        with patch.object(
            tetra, "_mesh_boundary", wraps=tetra._mesh_boundary
        ) as generator:
            with ThreadPoolExecutor(2) as executor:
                futures = [
                    executor.submit(
                        tetra.generate_tetrahedral_mesh,
                        replace(self.settings, mesh_type=kind),
                        self.surface.copy(),
                    )
                    for kind in ("TET4", "TET10")
                ]
                meshes = [future.result(timeout=30) for future in futures]
            self.assertEqual(generator.call_count, 1)
            self.assertEqual(meshes[0].grid.n_cells, meshes[1].grid.n_cells)
        tetra.clear_tetrahedral_cache()
        with patch.object(tetra, "_TET_CACHE_BYTES", 0):
            tetra.generate_tetrahedral_mesh(self.settings, self.surface)
            self.assertIsNone(tetra._tet_cache)

    def test_array_bridge_matches_ascii_geometry_and_refinement(self):
        from netgen.meshing import MeshingParameters

        bridge = netgen_io._array_bridge()
        if bridge is None:
            self.skipTest("Optional Netgen array bridge has not been built")
        boundary = tetra._clean_boundary(self.surface, 1.0, 1e-6)
        groups = (np.array([[0.1, 0.2, 0.3], [0.2, 0.3, 0.4]]), np.empty((0, 3)))
        parameters = MeshingParameters(maxh=0.5)
        netgen_io.restrict_h(parameters, groups, 0.2)
        direct_geometry = netgen_io.geometry_from_surface(boundary)
        direct = tetra._read_grid(direct_geometry.GenerateMesh(mp=parameters), "TET4")
        with patch.object(netgen_io, "_array_bridge", return_value=None):
            parameters = MeshingParameters(maxh=0.5)
            netgen_io.restrict_h(parameters, groups, 0.2)
            file_geometry = netgen_io.geometry_from_surface(boundary)
            from_file = tetra._read_grid(
                file_geometry.GenerateMesh(mp=parameters), "TET4"
            )
        for name in ("vertices", "triangles", "normals"):
            np.testing.assert_array_equal(
                direct_geometry._visualizationData()[name],
                file_geometry._visualizationData()[name],
            )
        np.testing.assert_array_equal(
            direct.cell_connectivity, from_file.cell_connectivity
        )
        np.testing.assert_allclose(direct.points, from_file.points, atol=1e-14, rtol=0)
        with self.assertRaises(ValueError):
            bridge.geometry_from_arrays(
                np.zeros((3, 3)),
                np.array([[0, 1, 3]], dtype=np.int64),
                np.zeros((1, 3)),
            )

    def test_bridge_compatibility_is_checked_before_loading_native_code(self):
        netgen_io._array_bridge.cache_clear()
        try:
            with patch.object(netgen_io, "version", return_value="different-version"):
                with patch.object(
                    netgen_io.importlib.util,
                    "spec_from_file_location",
                    side_effect=AssertionError("incompatible bridge was loaded"),
                ):
                    self.assertIsNone(netgen_io._array_bridge())
        finally:
            netgen_io._array_bridge.cache_clear()


if __name__ == "__main__":
    unittest.main()
