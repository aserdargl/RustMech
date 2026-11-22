"""Geometry-only requests, lazy FE generation and topology-safe fast contours."""
from dataclasses import replace
from multiprocessing.shared_memory import SharedMemory
import os
from pathlib import Path
from threading import Event
import unittest
from unittest.mock import patch

import numpy as np
import pyvista as pv

import mesh_engine as engine
from mesh_preview_worker import PreviewWorker
from model import Settings
from preview_transport import pack_preview, unpack_preview, write_arrays


def _exit_during_preview(connection, scratch):
    connection.recv()
    connection.send(("progress", "Generating tetrahedra in Netgen…"))
    (Path(scratch) / "worker-crash.txt").write_text("Native crash report", encoding="utf-8")
    os._exit(37)


class SurfacePreviewTests(unittest.TestCase):
    def test_unexpected_worker_exit_keeps_exit_code_and_progress(self):
        progress = []
        worker = PreviewWorker()
        try:
            with patch("mesh_preview_worker._serve_preview", _exit_during_preview):
                with self.assertRaisesRegex(RuntimeError, "stopped before returning") as caught:
                    worker.build(Settings(mesh_type="TET10"), None, progress.append, Event(), include_mesh=True)
            self.assertIn("Generating tetrahedra in Netgen…", progress)
            self.assertIn("Mesh worker exit code: 37", caught.exception.__notes__[0])
            self.assertIn("Native crash report", caught.exception.__notes__)
            self.assertIsNone(worker._process)
            self.assertIsNone(worker._scratch)
        finally:
            worker.close()

    def test_worker_preserves_child_python_traceback(self):
        worker = PreviewWorker()
        try:
            settings = Settings(size=1, cell_size=1, mesh_type="TET10", local_mesh_size=.3)
            with self.assertRaises(ValueError) as caught:
                worker.build(settings, None, None, Event(), include_mesh=True)
            self.assertIn("Traceback", caught.exception.__notes__[0])
            self.assertIn("generate_volume_mesh", caught.exception.__notes__[0])
        finally:
            worker.close()

    def test_remote_geometry_request_uses_surface_operation(self):
        from truba_remote import run_remote_preview
        settings = Settings(size=1, cell_size=1)
        with patch('truba_remote.run_remote_surface', return_value=pv.Box().triangulate()) as surface, patch(
            'truba_remote.run_remote_job', side_effect=AssertionError('FE operation')
        ):
            preview = run_remote_preview(settings, 'context', 'auth', None, Event(), include_mesh=False)
        surface.assert_called_once()
        self.assertIsNone(preview.mesh)

    def test_imported_path_change_invalidates_surface_even_with_the_same_name(self):
        settings = Settings(imported_geometry_path='first.vtp', imported_geometry_name='Model')
        with patch.object(engine, 'make_surface', return_value=pv.Box().triangulate()) as make:
            preview = engine.build_preview(settings)
            engine.build_preview(replace(settings, imported_geometry_path='second.vtp'), preview)
        self.assertEqual(make.call_count, 2)

    def test_open_fast_contour_falls_back_to_closed_lewiner_surface(self):
        axis = np.linspace(-1, 1, 16)
        field = (axis[:, None, None] ** 2 + axis[None, :, None] ** 2 + axis[None, None, :] ** 2 - .5).astype(np.float32)
        original = engine._project_reference_surface
        calls = []
        def projected(vertices, faces, spacing, size):
            calls.append(True)
            if len(calls) == 1:
                return pv.Plane().triangulate()
            return original(vertices, faces, spacing, size)
        with patch.object(engine, '_project_reference_surface', side_effect=projected):
            surface = engine.extract_reference_surface(field, .1, 2)
        self.assertEqual(len(calls), 2)
        self.assertTrue(surface.is_manifold)
        self.assertEqual(surface.field_data['_surface_extractor'][0], 'marching_cubes')

    def test_geometry_request_never_enters_volume_mesher(self):
        settings = Settings(size=1, cell_size=1, mesh_type='TET10')
        with patch.object(engine, 'generate_volume_mesh', side_effect=AssertionError('volume mesh')):
            preview = engine.build_preview(settings)
        self.assertIsNone(preview.mesh)
        self.assertTrue(preview.surface.is_manifold)
        with patch.object(engine, 'make_surface', side_effect=AssertionError('surface rebuilt')):
            mesh_preview = engine.build_preview(replace(settings, mesh_type='HEX8', mesh_resolution=.1), preview, include_mesh=True)
        self.assertIs(mesh_preview.surface, preview.surface)
        self.assertGreater(mesh_preview.mesh.grid.n_cells, 0)
        # A new mesh size must not retain an incompatible cached mesh.
        changed = engine.build_preview(replace(settings, mesh_type='HEX8', mesh_resolution=.2), mesh_preview)
        self.assertIsNone(changed.mesh)

    def test_surface_only_shared_transfer_can_drop_old_mesh(self):
        settings = Settings(size=1, cell_size=1, mesh_resolution=.1)
        cached = engine.build_preview(settings, include_mesh=True)
        payload, arrays, size = pack_preview(engine.preview_key(replace(settings, mesh_resolution=.2)), cached.surface, None)
        segment = SharedMemory(create=True, size=max(1, size))
        try:
            write_arrays(segment, arrays)
            result = unpack_preview(payload, segment, cached)
        finally:
            segment.close()
            segment.unlink()
        self.assertIsNone(result.mesh)
        np.testing.assert_array_equal(result.surface.points, cached.surface.points)

    def test_persistent_worker_surface_mesh_and_invalidated_mesh(self):
        settings = Settings(size=1, cell_size=1, mesh_resolution=.1)
        worker = PreviewWorker()
        try:
            surface = worker.build(settings, None, None, Event())
            self.assertIsNone(surface.mesh)
            full = worker.build(settings, surface, None, Event(), include_mesh=True)
            self.assertIs(full.surface, surface.surface)
            self.assertGreater(full.mesh.grid.n_cells, 0)
            changed = worker.build(replace(settings, mesh_resolution=.2), full, None, Event())
            self.assertIsNone(changed.mesh)
            cached = worker.build(replace(settings, mesh_resolution=.2), changed, None, Event())
            self.assertIs(cached.surface, changed.surface)
            self.assertIsNone(cached.mesh)
        finally:
            worker.close()

    def test_fast_surface_matches_closed_reference_without_changing_sampling(self):
        for name, morphology, fraction in [('Gyroid', 'sheet', 30), ('Gyroid', 'network_a', 45), ('S. Diamond', 'sheet', 15)]:
            settings = Settings(size=1, cell_size=1, structure_name=name, tpms_morphology=morphology, solid_fraction=fraction)
            with self.subTest(name=name, morphology=morphology):
                reference = engine.make_surface(settings, surface_backend='marching_cubes')
                actual = engine.make_surface(settings)
                self.assertTrue(actual.is_manifold)
                self.assertEqual(actual.n_open_edges, 0)
                np.testing.assert_allclose(actual.bounds, reference.bounds, atol=1e-7)
                self.assertLess(abs(actual.volume / reference.volume - 1), .005)
                distances = actual.compute_implicit_distance(reference)['implicit_distance']
                self.assertLess(np.max(np.abs(distances)), settings.surface_resolution)
                # Signed triangle volume detects reversed winding, unlike .volume.
                triangles = actual.points[actual.faces.reshape(-1, 4)[:, 1:]]
                self.assertGreater(np.einsum('ij,ij->i', triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])).sum(), 0)


if __name__ == '__main__':
    unittest.main()
