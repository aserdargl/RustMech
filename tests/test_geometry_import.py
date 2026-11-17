"""Exercise imported surfaces, compression orientation and real tetra meshing."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

import numpy as np
import pyvista as pv

from imported_geometry import read_surface, plane_faces, align_compression_surface
from mesh_engine import build_preview, make_surface, preview_key
from model import Settings, save_settings, load_settings, validate_geometry


def face_at(surface, axis, value):
    centers = surface.cell_centers().points
    return int(np.flatnonzero(np.isclose(centers[:, axis], value))[0])


class GeometryImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.surface = pv.Box(bounds=(10, 14, -5, -3, 2, 5)).triangulate().clean()
        self.bottom = face_at(self.surface, 0, 10)
        self.top = face_at(self.surface, 0, 14)

    def imported(self):
        surface, height = align_compression_surface(self.surface, self.bottom, self.top)
        path = Path(self.temp.name) / 'aligned.vtp'
        surface.save(path)
        return replace(Settings(), imported_geometry_path=str(path), imported_geometry_name='part.stl',
                       size=height, mesh_type='TET4', tetra_strategy='netgen',
                       mesh_resolution=1., local_mesh_size=.8)

    def test_opposite_faces_rotate_without_scaling(self):
        aligned, height = align_compression_surface(self.surface, self.bottom, self.top)
        self.assertEqual(height, 4)
        np.testing.assert_allclose(aligned.bounds, (0, 3, 0, 2, 0, 4))
        self.assertAlmostEqual(aligned.volume, self.surface.volume)
        self.assertEqual(len(plane_faces(self.surface, self.bottom)[0]), 2)
        self.assertEqual(set(aligned.cell_data['compression_face']), {0, 1, 2})

    def test_invalid_face_pairs_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'different'):
            align_compression_surface(self.surface, self.bottom, self.bottom)
        with self.assertRaisesRegex(ValueError, 'parallel'):
            align_compression_surface(self.surface, self.bottom, face_at(self.surface, 1, -5))

    def test_open_surface_is_rejected(self):
        path = Path(self.temp.name) / 'open.stl'
        pv.Plane().triangulate().save(path)
        with self.assertRaisesRegex(ValueError, 'closed'):
            read_surface(path)

    def test_settings_roundtrip_and_validation(self):
        settings = self.imported()
        self.assertFalse(validate_geometry(settings))
        path = Path(self.temp.name) / 'settings.json'
        save_settings(settings, path)
        self.assertEqual(load_settings(path), settings)
        self.assertTrue(validate_geometry(replace(settings, mesh_type='HEX8')))
        self.assertTrue(validate_geometry(replace(settings, imported_geometry_path='absent.vtp')))
        from flow_analysis import FlowSettings
        with self.assertRaisesRegex(ValueError, 'mechanical compression'):
            FlowSettings().validate(settings)

    def test_real_tetra_mesh_and_cache_switch(self):
        settings = self.imported()
        preview = build_preview(settings, include_mesh=True)
        self.assertGreater(preview.mesh.grid.n_cells, 0)
        self.assertFalse(preview.mesh.solve_errors)
        self.assertAlmostEqual(preview.mesh.fraction, 100, places=4)
        ids = preview.mesh.grid.point_data['ansys_node_num']
        for nodes, z in ((preview.mesh.bottom_ids, 0), (preview.mesh.top_ids, 4)):
            np.testing.assert_allclose(preview.mesh.grid.points[np.isin(ids, nodes), 2], z, atol=1e-6)
        self.assertIs(build_preview(settings, preview).mesh, preview.mesh)
        other = replace(settings, imported_geometry_path='different.vtp')
        self.assertNotEqual(preview_key(settings), preview_key(other))
        np.testing.assert_allclose(make_surface(settings).bounds, preview.surface.bounds)

    def test_remote_worker_receives_imported_surface(self):
        import base64
        from dataclasses import asdict
        from remote_worker import run_operation
        from truba_remote import load_remote_preview
        settings = self.imported()
        encoded = base64.b64encode(Path(settings.imported_geometry_path).read_bytes()).decode('ascii')
        request = dict(operation='preview', settings=asdict(replace(settings, imported_geometry_path='missing-on-cluster.vtp')),
                       imported_geometry_vtp=encoded)
        directory = Path(self.temp.name) / 'remote'
        directory.mkdir()
        with patch('remote_worker.probe', return_value=dict(mesh=True, mesh_types=['TET4'])):
            run_operation(request, directory)
        preview = load_remote_preview(directory / 'preview')
        self.assertFalse(preview.mesh.solve_errors)
        self.assertAlmostEqual(preview.mesh.fraction, 100, places=4)

    def test_remote_imported_mesh_reuse_across_job_directories_and_changed_geometry(self):
        import base64
        from dataclasses import asdict
        from remote_worker import run_operation
        from truba_remote import load_remote_preview
        settings = replace(self.imported(), solver_backend='kratos', cores=1, self_contact_mode='disabled')
        raw = Path(settings.imported_geometry_path).read_bytes()
        values = dict(settings=asdict(settings), imported_geometry_vtp=base64.b64encode(raw).decode('ascii'))
        first = Path(self.temp.name) / 'first' / 'output'
        second = Path(self.temp.name) / 'second' / 'output'
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        with patch('remote_worker.probe', return_value=dict(mesh=True, kratos=True, mesh_types=['TET4'])):
            run_operation(dict(operation='preview', **values), first)
            local_preview = load_remote_preview(first / 'preview', settings=settings)
            self.assertEqual(local_preview.key, preview_key(settings))
            request = dict(operation='mechanical', mesh_source=str((first / 'preview').resolve()), **values)
            with patch('truba_jobs.validated_remote_path'), patch('remote_worker._batch', return_value=dict(run_folder='result')) as solve, patch('mesh_engine.generate_volume_mesh') as remesh:
                run_operation(request, second)
                remesh.assert_not_called()
                np.testing.assert_array_equal(solve.call_args.args[1].grid.points, local_preview.mesh.grid.points)
                np.testing.assert_array_equal(solve.call_args.args[1].grid.cell_connectivity, local_preview.mesh.grid.cell_connectivity)
                changed = pv.read(settings.imported_geometry_path)
                changed.points[:, 0] *= .9
                changed.save(settings.imported_geometry_path)
                request['imported_geometry_vtp'] = base64.b64encode(Path(settings.imported_geometry_path).read_bytes()).decode('ascii')
                solve.reset_mock()
                with self.assertRaisesRegex(ValueError, 'geometry changed'):
                    run_operation(request, second)
                solve.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'geometry changed'):
            load_remote_preview(first / 'preview', settings=settings)


if __name__ == '__main__':
    unittest.main()
