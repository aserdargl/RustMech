"""Planning ignores FE settings; explicit meshing and solving still reject them."""
from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

import pyvista as pv

from doe_studio import ExperimentWorker
from mesh_engine import make_surface, generate_volume_mesh
from model import Settings, validate_geometry, validate_settings
from standard_tests import apply_profile


class DoEValidationTests(unittest.TestCase):
    def test_invalid_mesh_settings_are_deferred_to_explicit_mesh_or_solve(self):
        base = Settings(size=1, cell_size=1)
        for changes in (
            dict(mesh_resolution=2), dict(mesh_resolution=0),
            dict(mesh_resolution=float('nan')), dict(local_mesh_size=-1),
            dict(mesh_type='unsupported'), dict(tetra_strategy='unsupported'),
            dict(mesh_type='TET4', mesh_resolution=.1, local_mesh_size=.3),
        ):
            settings = replace(base, **changes)
            with self.subTest(changes=changes):
                self.assertEqual(validate_geometry(settings, include_mesh=False), [])
                self.assertTrue(validate_geometry(settings))
                self.assertTrue(validate_settings(settings))
                mesh, solve = MagicMock(), MagicMock()
                worker = ExperimentWorker(mesh, solve)
                self.assertIn('Invalid', worker._evaluate_row_mesh(0, settings)['status'])
                self.assertIn('Invalid', worker._solve_row(0, settings)['status'])
                mesh.assert_not_called()
                solve.assert_not_called()
                with self.assertRaises(ValueError):
                    generate_volume_mesh(settings)

    def test_shape_errors_remain_errors_during_planning(self):
        for changes in (dict(size=0), dict(cell_size=12), dict(solid_fraction=100),
                        dict(structure_name='unknown'), dict(random_seed=True)):
            with self.subTest(changes=changes):
                self.assertTrue(validate_geometry(replace(Settings(), **changes), include_mesh=False))

    def test_surface_preview_does_not_require_valid_fe_settings(self):
        settings = Settings(size=1, cell_size=1, mesh_resolution=0, local_mesh_size=-1, mesh_type='unsupported')
        surface = make_surface(settings)
        self.assertTrue(surface.is_manifold)
        self.assertGreater(surface.n_cells, 0)

    def test_imported_and_standard_geometries_defer_mesh_rules(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'shape.vtp'
            pv.Box(bounds=(0, 1, 0, 1, 0, 1)).triangulate().save(path)
            settings = Settings(size=1, imported_geometry_path=str(path), mesh_type='HEX8', tetra_strategy='periodic', mesh_resolution=0)
            self.assertEqual(validate_geometry(settings, include_mesh=False), [])
            self.assertTrue(validate_geometry(settings))
            self.assertGreater(make_surface(settings).n_cells, 0)
            # Remote surface-only requests also bypass supported-FE-type checks.
            import base64
            from remote_worker import run_operation
            request = dict(operation='surface', settings=asdict(settings),
                           imported_geometry_vtp=base64.b64encode(path.read_bytes()).decode('ascii'))
            output = Path(folder) / 'remote'
            output.mkdir()
            with patch('remote_worker.probe', return_value=dict(mesh=True, mesh_types=['TET4'])):
                run_operation(request, output)
            self.assertTrue((output / 'surface.vtp').is_file())
        standard = replace(apply_profile(Settings(), 'iso178_preferred'), mesh_resolution=100, tetra_strategy='periodic')
        self.assertEqual(validate_geometry(standard, include_mesh=False), [])
        self.assertTrue(validate_geometry(standard))


if __name__ == '__main__':
    unittest.main()
