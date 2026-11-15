"""Geometry, interfaces, quality, strategy selection and FE order regression."""

from dataclasses import replace
import unittest
from unittest.mock import patch
import numpy as np
import pyvista as pv

import mesh_engine
import tetrahedral_mesh as tetra
from model import Settings, settings_from_dict, validate_geometry
from periodic_tetra import PeriodicUnavailable, _cell, check_periodic_faces, face_components, generate_periodic, period_count
from surface_optimization import cap_areas, simplify_boundary, spaced_indices
from rust_mesh import tet_metrics


class TPMSOptimizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = Settings(size=6, cell_size=3, mesh_type='TET4', local_mesh_size=.1)
        cls.reference = mesh_engine.make_surface(cls.settings)

    def tearDown(self):
        tetra.clear_tetrahedral_cache()

    def test_reduced_surface_preserves_reference_caps_topology_and_volume(self):
        original = self.reference.points.copy()
        reduced = simplify_boundary(self.reference, self.settings)
        self.assertLess(reduced.n_cells, self.reference.n_cells / 2)
        self.assertTrue(reduced.is_manifold)
        self.assertLess(abs(reduced.volume / self.reference.volume - 1), .005)
        np.testing.assert_allclose(cap_areas(reduced, 6), cap_areas(self.reference, 6), rtol=.005)
        self.assertTrue(np.all(reduced.points >= 0))
        self.assertTrue(np.all(reduced.points <= 6))
        np.testing.assert_array_equal(self.reference.points, original)

    def test_spacing_keeps_geometric_coverage_and_empty_groups(self):
        rng = np.random.default_rng(5)
        points = rng.random((1000, 3))
        indices = spaced_indices(points, .1)
        self.assertLess(len(indices), len(points))
        self.assertTrue(np.all(np.diff(indices) > 0))
        keys = np.floor(points / .1).astype(int)
        self.assertEqual(set(map(tuple, keys)), set(map(tuple, keys[indices])))
        self.assertEqual(len(spaced_indices(np.empty((0, 3)), .1)), 0)

    def test_contact_plane_sampling_keeps_narrow_strips_and_refines_long_rims(self):
        settings = Settings(size=1, cell_size=.5, local_mesh_size=.1)
        strip = pv.Box(bounds=(.45, .46, 0, 1, 0, 1)).triangulate()
        points = tetra._platen_samples(strip, settings)
        self.assertTrue(len(points))
        self.assertTrue(np.all((points[:, 2] == 0) | (points[:, 2] == 1)))
        self.assertTrue(np.any((points[:, 1] > .3) & (points[:, 1] < .7)))
        self.assertTrue(np.all((points[:, 0] >= .45 - 1e-7) & (points[:, 0] <= .46 + 1e-7)))
        with self.assertRaisesRegex(ValueError, 'sampling limit'):
            tetra._platen_samples(strip, replace(settings, local_mesh_size=.00001))

    def test_tiled_gyroid_has_no_internal_caps_and_one_solid_component(self):
        settings = self.settings
        threshold = mesh_engine.geometry_threshold(settings)
        grid = generate_periodic(settings, self.reference, threshold)
        self.assertLessEqual(grid.n_cells, 1_100_000)
        self.assertLess(abs(grid.volume / self.reference.volume - 1), .015)
        self.assertTrue(np.all(tet_metrics(grid)[0] > 0))
        self.assertEqual(len(np.unique(face_components(grid))), 1)
        surface = grid.extract_surface(algorithm='dataset_surface')
        self.assertTrue(surface.is_manifold)
        faces = surface.faces.reshape(-1, 4)[:, 1:]
        for axis in range(3):
            self.assertFalse(np.any(np.all(np.isclose(surface.points[faces, axis], 3), axis=1)))
        quality = grid.cell_quality('scaled_jacobian').cell_data['scaled_jacobian']
        self.assertGreater(quality.min(), 1e-4)
        self.assertGreater(np.quantile(quality, .01), .005)

    def test_cell_faces_match_and_a_missing_triangle_is_rejected(self):
        cell = _cell(self.settings, mesh_engine.geometry_threshold(self.settings), None)
        surface = cell.extract_surface(algorithm='dataset_surface')
        check_periodic_faces(surface, 3)
        faces = surface.faces.reshape(-1, 4)
        cap = np.flatnonzero(np.all(surface.points[faces[:, 1:], 0] == 0, axis=1))[0]
        damaged = pv.PolyData(surface.points, np.delete(faces, cap, axis=0).ravel())
        with self.assertRaises(PeriodicUnavailable):
            check_periodic_faces(damaged, 3)

    def test_nonperiodic_geometry_and_excessive_refinement_are_rejected_early(self):
        for settings in (replace(self.settings, size=5),
                         replace(self.settings, structure_family='porous'),
                         replace(self.settings, size=3)):
            with self.assertRaises(PeriodicUnavailable):
                period_count(settings)
        with self.assertRaisesRegex(PeriodicUnavailable, 'budget'):
            _cell(replace(self.settings, local_mesh_size=.01), 0, None)

    def test_order_switch_uses_one_periodic_linear_mesh_and_shared_midpoints(self):
        settings = replace(self.settings, tetra_strategy='periodic')
        with patch.object(tetra, 'generate_periodic', wraps=tetra.generate_periodic) as generate:
            linear = mesh_engine.generate_volume_mesh(settings, self.reference)
            quadratic = mesh_engine.generate_volume_mesh(replace(settings, mesh_type='TET10'), self.reference)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(linear.grid.n_cells, quadratic.grid.n_cells)
        self.assertEqual(quadratic.solve_errors, [])
        cells = quadratic.grid.cell_connectivity.reshape(-1, 10)
        edges = np.sort(cells[:, :2], axis=1)
        _, inverse = np.unique(edges, axis=0, return_inverse=True)
        order = np.argsort(inverse)
        same = np.diff(inverse[order]) == 0
        self.assertTrue(np.all(np.diff(cells[order, 4])[same] == 0))

    def test_custom_or_modified_surface_is_never_replaced_by_a_tpms(self):
        settings = replace(self.settings, tetra_strategy='periodic')
        custom = pv.Box(bounds=(0, 6, 0, 6, 0, 6)).triangulate()
        with self.assertRaisesRegex(ValueError, 'unchanged generated'):
            mesh_engine.generate_volume_mesh(settings, custom)
        changed = self.reference.copy()
        changed.points[0, 0] += .001
        with self.assertRaisesRegex(ValueError, 'unchanged generated'):
            mesh_engine.generate_volume_mesh(settings, changed)

    def test_settings_compatibility_and_preview_keys(self):
        self.assertEqual(Settings().local_mesh_size, .05)
        self.assertEqual(settings_from_dict({'local_mesh_size': .01}).local_mesh_size, .01)
        self.assertEqual(settings_from_dict({}).tetra_strategy, 'auto')
        self.assertTrue(validate_geometry(replace(self.settings, tetra_strategy='invalid')))
        first = mesh_engine.preview_key(self.settings)
        other = mesh_engine.preview_key(replace(self.settings, tetra_strategy='netgen'))
        self.assertEqual(first[:8], other[:8])
        self.assertNotEqual(first, other)


if __name__ == '__main__':
    unittest.main()
