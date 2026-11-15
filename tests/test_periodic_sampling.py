"""Repeated field work, procedural fallback and conforming lattice meshes."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np

from model import Settings
import mesh_engine
import periodic_sampling as sampling
import tetrahedral_mesh as tetra
from periodic_tetra import PeriodicUnavailable, face_components
from rust_mesh import tet_metrics
from structure_library import evaluate_priority_field, TPMS_ITEMS, LATTICE_ITEMS, POROUS_ITEMS


class PeriodicSamplingTests(unittest.TestCase):
    def test_every_analytic_structure_and_morphology_matches_direct_sampling(self):
        # Include ghost points and a partial final period.
        axis = np.arange(-1, 22) * .25
        for family, names in (('tpms', TPMS_ITEMS), ('lattice', LATTICE_ITEMS)):
            for name in names:
                for morphology in ('sheet', 'network_a', 'network_b'):
                    settings = Settings(size=5, cell_size=3, structure_family=family,
                                        structure_name=name, tpms_morphology=morphology)
                    with self.subTest(family=family, name=name, morphology=morphology):
                        expected = evaluate_priority_field(settings, axis[:, None, None],
                                                           axis[None, :, None], axis[None, None, :])
                        with patch.object(sampling, 'evaluate_priority_field', wraps=evaluate_priority_field) as evaluate:
                            actual = sampling.sample_priority_grid(settings, axis)
                        np.testing.assert_allclose(actual, expected, rtol=1e-11, atol=1e-12)
                        sampled = sum(np.prod(np.broadcast_shapes(*(a.shape for a in call.args[1:])))
                                      for call in evaluate.call_args_list)
                        self.assertEqual(sampled, 12 ** 3)

    def test_unaligned_grid_keeps_original_samples_and_bounds_calls(self):
        settings = Settings(cell_size=3)
        axis = np.linspace(0, 6, 26)
        expected = evaluate_priority_field(settings, axis[:, None, None], axis[None, :, None], axis[None, None, :])
        with patch.object(sampling, 'SAMPLE_BUDGET', 100), patch.object(
                sampling, 'evaluate_priority_field', wraps=evaluate_priority_field) as evaluate:
            actual = sampling.sample_priority_grid(settings, axis, dtype=np.float32, threshold=.2)
        np.testing.assert_array_equal(actual, (expected - .2).astype(np.float32))
        for call in evaluate.call_args_list:
            self.assertLessEqual(np.prod(np.broadcast_shapes(*(a.shape for a in call.args[1:]))), 100)

    def test_procedural_structures_are_not_replaced_with_identical_cells(self):
        for name in POROUS_ITEMS:
            settings = Settings(structure_family='porous', structure_name=name)
            self.assertFalse(sampling.has_repeating_cell(settings))
        settings = Settings(structure_family='porous', structure_name='Voronoi Cell')
        axis = (np.arange(20) + .5) * .3
        expected = evaluate_priority_field(settings, axis[:, None, None], axis[None, :, None], axis[None, None, :])
        actual = sampling.sample_priority_grid(settings, axis)
        np.testing.assert_array_equal(actual, expected)

    def test_lattice_tiling_preserves_volume_interfaces_and_positive_elements(self):
        settings = Settings(structure_family='lattice', structure_name='BCC',
                            mesh_type='TET4', local_mesh_size=.125, tetra_strategy='periodic')
        reference = mesh_engine.make_surface(settings)
        tetra.clear_tetrahedral_cache()
        self.addCleanup(tetra.clear_tetrahedral_cache)
        with patch.object(tetra, 'generate_periodic', wraps=tetra.generate_periodic) as generate:
            linear = mesh_engine.generate_volume_mesh(settings, reference)
            quadratic = mesh_engine.generate_volume_mesh(replace(settings, mesh_type='TET10'), reference)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(linear.solve_errors, [])
        self.assertEqual(quadratic.solve_errors, [])
        self.assertEqual(linear.grid.n_cells, quadratic.grid.n_cells)
        self.assertEqual(linear.grid.field_data['meshing_method'][0], 'Periodic LATTICE cells')
        grid = linear.grid
        self.assertLess(abs(grid.volume / reference.volume - 1), .015)
        self.assertTrue(np.all(tet_metrics(grid)[0] > 0))
        self.assertEqual(len(np.unique(face_components(grid))), 1)
        boundary = grid.extract_surface(algorithm='dataset_surface')
        self.assertTrue(boundary.is_manifold)
        faces = boundary.faces.reshape(-1, 4)[:, 1:]
        for axis in range(3):
            self.assertFalse(np.any(np.all(np.isclose(boundary.points[faces, axis], 3), axis=1)))

    def test_auto_retains_netgen_fallback_when_periodic_quality_is_unavailable(self):
        import pyvista as pv
        settings = Settings(size=1, cell_size=.5, mesh_type='TET4', local_mesh_size=.2,
                            structure_family='lattice', structure_name='BCC')
        reference = pv.Box(bounds=(0, 1, 0, 1, 0, 1)).triangulate()
        tetra.clear_tetrahedral_cache()
        self.addCleanup(tetra.clear_tetrahedral_cache)
        # Generate the ordinary fallback once, then check routing without rerunning Netgen.
        ordinary = tetra.generate_tetrahedral_mesh(replace(settings, tetra_strategy='netgen'), reference)
        with patch.object(tetra, 'generate_periodic', side_effect=PeriodicUnavailable('quality')), patch.object(
                tetra, '_mesh_boundary', return_value=ordinary.grid) as netgen:
            mesh = tetra.generate_tetrahedral_mesh(settings, reference, periodic_threshold=.3)
        self.assertEqual(netgen.call_count, 1)
        self.assertEqual(mesh.grid.field_data['meshing_method'][0], 'Netgen')

    def test_hex_orders_keep_direct_sampling_solid_mask(self):
        for family, name in (('tpms', 'Gyroid'), ('lattice', 'BCC')):
            for order in ('HEX8', 'HEX20'):
                settings = Settings(structure_family=family, structure_name=name, mesh_type=order)
                axis = (np.arange(30) + .5) * .2
                values = evaluate_priority_field(settings, axis[:, None, None], axis[None, :, None], axis[None, None, :])
                expected = values < mesh_engine.geometry_threshold(settings)
                mesh = mesh_engine.generate_volume_mesh(settings)
                actual = np.zeros(expected.shape, dtype=bool)
                for grid in (mesh.grid, mesh.removed_grid):
                    if grid.n_cells:
                        ijk = np.floor(grid.cell_centers().points / .2).astype(int)
                        actual[tuple(ijk.T)] = True
                np.testing.assert_array_equal(actual, expected)


if __name__ == '__main__':
    unittest.main()
