"""Exercise native mesh creation and solver export beyond former fixed caps."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pyvista as pv

from mesh_engine import generate_volume_mesh
from mesh_resources import require_memory
from model import Settings, validate_geometry
from periodic_tetra import face_components
from rust_mesh import build_hex_grid, quadratic_tetra_arrays, select_voxels, tet_metrics, write_apdl


class LargeMeshTests(unittest.TestCase):
    def test_large_settings_preserve_requested_dimensions_and_mesh(self):
        for order in ("HEX8", "HEX20"):
            settings = Settings(size=30, cell_size=3, mesh_resolution=.1, mesh_type=order)
            self.assertEqual(validate_geometry(settings), [])
            self.assertEqual(settings.size, 30)
            self.assertEqual(settings.mesh_resolution, .1)
        self.assertTrue(validate_geometry(replace(Settings(), mesh_resolution=3)))

    def test_long_axes_and_both_orders_keep_contact_ids_and_shared_nodes(self):
        values = np.zeros((170, 2, 4))
        retained, removed, components = select_voxels(values, values.size)
        self.assertEqual(components, 1)
        self.assertFalse(removed.any())
        for quadratic in (False, True):
            grid, bottom, top = build_hex_grid(retained, .125, quadratic=quadratic, return_boundaries=True)
            self.assertEqual(grid.n_cells, values.size)
            np.testing.assert_allclose(grid.bounds, (0, 21.25, 0, .25, 0, .5))
            ids = grid.point_data['ansys_node_num']
            np.testing.assert_array_equal(bottom, ids[grid.points[:, 2] == 0])
            np.testing.assert_array_equal(top, ids[grid.points[:, 2] == .5])
            self.assertAlmostEqual(grid.linear_copy().volume, values.size * .125**3)

    def test_hex_model_above_two_million_elements_exports_without_coarsening(self):
        # 160 divisions exceeds the former axis cap; 60% exceeds both the old
        # 1.1M mesh cap and the separate 2M native APDL export cap.
        settings = Settings(size=6, cell_size=3, solid_fraction=60, mesh_resolution=.0375)
        mesh = generate_volume_mesh(settings)
        self.assertEqual(mesh.subdivisions, 160)
        self.assertEqual(mesh.spacing, .0375)
        self.assertGreater(mesh.grid.n_cells, 2_000_000)
        np.testing.assert_allclose(mesh.grid.bounds, (0, 6, 0, 6, 0, 6))
        cells = mesh.grid.cell_connectivity.reshape(-1, 8)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'large.inp'
            write_apdl(path, mesh.grid.points, mesh.grid.point_data['ansys_node_num'],
                       cells, mesh.grid.cell_data['ansys_elem_num'], 185)
            nodes = elements = 0
            with path.open() as stream:
                for line in stream:
                    nodes += line.startswith('N,')
                    elements += line.startswith('EN,')
            self.assertEqual(nodes, mesh.grid.n_points)
            self.assertEqual(elements, mesh.grid.n_cells)

    def test_tetrahedral_assembly_and_metrics_above_old_caps(self):
        points = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 1.]])
        # Repeated cells isolate buffer-size handling and shared midpoint IDs;
        # this fixture tests the native arrays, not a physical FE specimen.
        corners = np.tile(np.array([[0, 1, 2, 3]], np.int64), (2_000_001, 1))
        output, cells = quadratic_tetra_arrays(points, corners)
        self.assertEqual(len(output), 10)
        self.assertEqual(cells.shape, (2_000_001, 10))
        np.testing.assert_array_equal(cells[0], cells[-1])
        grid = pv.UnstructuredGrid()
        grid.points = output
        grid.SetCells(int(pv.CellType.QUADRATIC_TETRA), pv.CellArray.from_regular_cells(cells))
        determinants, ratios = tet_metrics(grid)
        self.assertTrue(np.all(determinants == 1))
        self.assertTrue(np.all(np.isfinite(ratios)))

    def test_face_adjacency_works_above_old_packed_key_budget(self):
        # Sparse point IDs previously overflowed/rejected n**3 face encoding.
        points = np.zeros((2_100_001, 3))
        cells = np.array([[0, 1, 2, 3], [2, 1, 0, 2_100_000]])
        grid = pv.UnstructuredGrid()
        grid.points = points
        grid.SetCells(int(pv.CellType.TETRA), pv.CellArray.from_regular_cells(cells))
        np.testing.assert_array_equal(face_components(grid), [0, 0])

    def test_memory_preflight_uses_available_ram_instead_of_element_count(self):
        with patch('mesh_resources.available_memory', return_value=1024):
            require_memory(1024, 'test')
            with self.assertRaisesRegex(MemoryError, 'have not been changed'):
                require_memory(1025, 'test')
        with patch('mesh_resources.available_memory', return_value=None):
            require_memory(10**15, 'test')

    def test_convergence_has_no_implicit_element_cap_but_honors_user_budget(self):
        from mesh_convergence import solve_level
        mesh = SimpleNamespace(grid=SimpleNamespace(n_cells=2_500_000), solve_errors=[])
        solver = Mock(return_value={'status': 'completed'})
        solve_level(Settings(), lambda _: mesh, solver)
        solver.assert_called_once()
        solver.reset_mock()
        level = solve_level(Settings(), lambda _: mesh, solver, max_elements=1_000_000)
        solver.assert_not_called()
        self.assertIn('Element limit exceeded', level.status)


if __name__ == '__main__':
    unittest.main()
