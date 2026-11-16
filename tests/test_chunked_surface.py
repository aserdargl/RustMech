"""Chunk ownership, exact seam welding, periodic reuse and automatic routing."""
import unittest
from unittest.mock import patch, PropertyMock

import numpy as np

import chunked_surface as chunks
import mesh_engine as engine
from model import Settings
from periodic_sampling import BlockSampler
from structure_library import evaluate_priority_field
from surface_optimization import cap_areas


class ChunkedSurfaceTests(unittest.TestCase):
    @patch.object(Settings, 'surface_resolution', new_callable=PropertyMock, return_value=.1)
    def test_zero_isovalue_on_a_shared_plane_has_one_owner(self, _resolution):
        settings = Settings(size=4, cell_size=1, structure_family='porous', structure_name='Gaussian Random Field')
        def field(_settings, x, y, z):
            return np.broadcast_to(x - .9, np.broadcast_shapes(x.shape, y.shape, z.shape))
        with patch.object(engine, 'geometry_threshold', return_value=0), patch('periodic_sampling.evaluate_priority_field', side_effect=field):
            expected = engine.make_surface(settings, chunked=False)
            with patch.object(chunks, 'BLOCK_POINTS', 11**3):
                actual = engine.make_surface(settings, chunked=True)
        self.assert_same_solid(expected, actual, .1)
        self.assertAlmostEqual(actual.volume, .9 * 4**2, places=5)

    def test_float32_caps_remain_exact_at_real_surface_resolution(self):
        settings = Settings(size=1, cell_size=1)
        expected = engine.make_surface(settings, chunked=False)
        with patch.object(chunks, 'BLOCK_POINTS', 64**3):
            actual = engine.make_surface(settings, chunked=True)
        self.assert_same_solid(expected, actual, settings.surface_resolution)

    @patch.object(Settings, 'surface_resolution', new_callable=PropertyMock, return_value=.1)
    def test_final_geometry_still_has_an_explicit_memory_preflight(self, _resolution):
        with patch('mesh_resources.available_memory', return_value=100_000):
            with self.assertRaisesRegex(MemoryError, 'Reference surface assembly'):
                engine.make_surface(Settings(size=1, cell_size=1), chunked=True)

    @patch.object(Settings, 'surface_resolution', new_callable=PropertyMock, return_value=.1)
    def test_disk_spill_and_disabled_template_reuse_preserve_geometry(self, _resolution):
        settings = Settings(size=3, cell_size=1)
        expected = engine.make_surface(settings, chunked=False)
        with patch.object(chunks, 'BLOCK_POINTS', 20**3), patch.object(chunks, 'CACHE_BYTES', 1), patch.object(chunks, 'REUSE_TEMPLATES', False):
            actual = engine.make_surface(settings, chunked=True)
        self.assert_same_solid(expected, actual, .1)
        self.assertEqual(actual.field_data['_surface_chunks'][2], 0)

    @patch.object(Settings, 'surface_resolution', new_callable=PropertyMock, return_value=.1)
    def test_ambiguous_fast_contour_retries_with_bounded_lewiner_blocks(self, _resolution):
        settings = Settings(size=2, cell_size=1)
        from mesh_engine import _project_reference_surface
        import pyvista as pv
        calls = []
        def project(*args):
            calls.append(True)
            if len(calls) == 1:
                return pv.Plane().triangulate()
            return _project_reference_surface(*args)
        with patch.object(chunks, 'BLOCK_POINTS', 20**3), patch.object(engine, '_project_reference_surface', side_effect=project):
            actual = engine.make_surface(settings, chunked=True)
        self.assertEqual(len(calls), 2)
        self.assertTrue(actual.is_manifold)
        self.assertEqual(actual.field_data['_surface_extractor'][0], 'marching_cubes')

    def test_auto_mode_uses_grid_size_and_available_memory(self):
        with patch.object(chunks, 'available_memory', return_value=8 * 1024**3):
            self.assertFalse(chunks.should_chunk(83))
            self.assertTrue(chunks.should_chunk(323))
        with patch.object(chunks, 'available_memory', return_value=32 * 1024**2):
            self.assertTrue(chunks.should_chunk(100))

    def assert_same_solid(self, expected, actual, spacing):
        self.assertTrue(actual.is_manifold)
        self.assertEqual(actual.n_open_edges, 0)
        np.testing.assert_allclose(actual.bounds, expected.bounds, atol=1e-10)
        self.assertLess(abs(actual.volume / expected.volume - 1), .0001)
        np.testing.assert_allclose(cap_areas(actual, actual.bounds[5]), cap_areas(expected, expected.bounds[5]), rtol=1e-5, atol=1e-10)
        for a, b in ((actual, expected), (expected, actual)):
            self.assertLess(np.max(np.abs(a.compute_implicit_distance(b)['implicit_distance'])), spacing * 1e-4)
        triangles = actual.points[actual.faces.reshape(-1, 4)[:, 1:]]
        self.assertGreater(np.einsum('ij,ij->i', triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])).sum(), 0)

    @patch.object(Settings, 'surface_resolution', new_callable=PropertyMock, return_value=.1)
    def test_periodic_templates_keep_caps_and_seams_without_internal_walls(self, _resolution):
        for family, name, morphology in [('tpms', 'Gyroid', 'sheet'), ('tpms', 'Gyroid', 'network_a'),
                                         ('tpms', 'Gyroid', 'network_b'), ('tpms', 'S. Diamond', 'sheet'),
                                         ('lattice', 'BCC', 'sheet')]:
            settings = Settings(size=4, cell_size=1, structure_family=family, structure_name=name, tpms_morphology=morphology)
            with self.subTest(family=family, morphology=morphology):
                expected = engine.make_surface(settings, chunked=False)
                stages = []
                with patch.object(chunks, 'BLOCK_POINTS', 20**3):
                    actual = engine.make_surface(settings, chunked=True, on_progress=stages.append)
                self.assert_same_solid(expected, actual, .1)
                self.assertEqual(actual.field_data['_surface_mode'][0], 'chunked')
                total, extracted, reused = actual.field_data['_surface_chunks']
                self.assertGreater(reused, 0)
                self.assertEqual(total, extracted + reused)
                self.assertEqual(actual.field_data['_surface_sampling'][0], 10**3)
                self.assertTrue(any('periodic blocks reused' in stage for stage in stages))
                faces = actual.points[actual.faces.reshape(-1, 4)[:, 1:]]
                for dimension in range(3):
                    for coordinate in (1., 2., 3.):
                        # These curved solids have no planar internal walls.
                        self.assertFalse(np.any(np.all(np.isclose(faces[:, :, dimension], coordinate, rtol=0, atol=1e-12), axis=1)))

    @patch.object(Settings, 'surface_resolution', new_callable=PropertyMock, return_value=.13)
    def test_unaligned_partial_cells_and_procedural_fields_use_direct_samples(self, _resolution):
        for settings in (Settings(size=2.3, cell_size=1),
                         Settings(size=2.3, cell_size=1, structure_family='porous', structure_name='Gaussian Random Field')):
            with self.subTest(family=settings.structure_family):
                expected = engine.make_surface(settings, chunked=False)
                with patch.object(chunks, 'BLOCK_POINTS', 12**3):
                    actual = engine.make_surface(settings, chunked=True)
                self.assert_same_solid(expected, actual, .13)
                self.assertEqual(actual.field_data['_surface_chunks'][2], 0)

    def test_rectangular_block_sampler_reuses_only_exact_periods(self):
        settings = Settings(size=4, cell_size=1)
        axis = np.arange(-1, 42) * .1
        sampler = BlockSampler(settings, axis, threshold=.2)
        starts, stops = (7, 0, 11), (25, 8, 31)
        actual = sampler.sample(starts, stops)
        vectors = [axis[a:b] for a, b in zip(starts, stops)]
        expected = evaluate_priority_field(settings, vectors[0][:, None, None], vectors[1][None, :, None], vectors[2][None, None, :]) - .2
        np.testing.assert_allclose(actual, expected, atol=1e-6)
        self.assertEqual(sampler.evaluated_points, 1000)
        sampler.sample((0, 0, 0), (5, 6, 7))
        self.assertEqual(sampler.evaluated_points, 1000)

    @patch.object(Settings, 'surface_resolution', new_callable=PropertyMock, return_value=.1)
    def test_auto_routing_and_memory_preflight_preserve_requested_spacing(self, _resolution):
        settings = Settings(size=2, cell_size=1)
        with patch.object(chunks, 'AUTO_POINTS', 1000), patch.object(chunks, 'BLOCK_POINTS', 20**3):
            actual = engine.make_surface(settings)
        self.assertEqual(actual.field_data['_surface_mode'][0], 'chunked')
        with patch('mesh_resources.available_memory', return_value=1):
            with self.assertRaisesRegex(MemoryError, 'Reference surface sampling'):
                engine.make_surface(settings, chunked=True)
        self.assertEqual(settings.surface_resolution, .1)


if __name__ == '__main__':
    unittest.main()
