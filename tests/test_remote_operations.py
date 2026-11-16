"""Unsupported remote requests fail before connecting or generating a mesh."""
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest
import json
import os
from unittest.mock import patch, PropertyMock

from model import Settings
from remote_operations import required_capability
from remote_worker import run_operation
from truba_remote import RemoteContext, run_remote_job


class RemoteOperationTests(unittest.TestCase):
    @patch.object(Settings, 'surface_resolution', new_callable=PropertyMock, return_value=.2)
    def test_remote_operations_generate_whole_surfaces_and_restore_local_policy(self, _resolution):
        import mesh_engine
        from remote_operations import OPERATIONS
        settings = Settings(size=1, cell_size=1, solver_backend='kratos')

        def generate(request, output):
            return mesh_engine.make_surface(settings)

        with patch('chunked_surface.should_chunk', return_value=True), patch(
                'chunked_surface.build_chunked_surface', side_effect=AssertionError('Unexpected chunking')), patch(
                'remote_worker._run_operation', side_effect=generate):
            for operation in OPERATIONS.keys() - {'probe', 'prepare'}:
                with self.subTest(operation=operation):
                    result = run_operation(dict(operation=operation, settings=asdict(settings)), Path('.'))
                    self.assertEqual(result.field_data['_surface_mode'][0], 'single')
            # The compute-node override must not leak into local geometry work.
            with self.assertRaisesRegex(AssertionError, 'Unexpected chunking'):
                mesh_engine.make_surface(settings)

    def test_remote_surface_memory_failure_does_not_fall_back_to_chunks(self):
        import mesh_engine
        settings = Settings(size=1, cell_size=1)
        with patch('chunked_surface.should_chunk', return_value=True), patch(
                'chunked_surface.build_chunked_surface') as chunks, patch(
                'mesh_resources.available_memory', return_value=1), patch(
                'remote_worker._run_operation', side_effect=lambda *_: mesh_engine.make_surface(settings)):
            with self.assertRaises(MemoryError):
                run_operation(dict(operation='surface', settings=asdict(settings)), Path('.'))
            chunks.assert_not_called()
        self.assertFalse(mesh_engine._single_surface.get())

    def test_surface_and_hex_mesh_do_not_probe_unrelated_solvers(self):
        from remote_worker import probe
        with tempfile.TemporaryDirectory() as directory:
            library = Path(directory) / 'librustmech_core.so'
            library.with_suffix('.json').write_text(json.dumps(dict(native_sha256='test')))
            for operation, mesh_type, needs_tetra in (('surface', 'TET4', False),
                    ('doe_mesh', 'HEX8', False), ('preview', 'TET4', True)):
                with self.subTest(operation=operation, mesh_type=mesh_type), patch.dict(
                        os.environ, RUSTMECH_CORE_LIBRARY=str(library)), patch(
                        'rust_mesh._kernel'), patch('remote_worker.subprocess.run') as launch, patch(
                        'remote_worker.importlib.import_module') as imports:
                    result = probe(dict(operation=operation, settings=dict(mesh_type=mesh_type), native_sha256='test'))
                    self.assertTrue(result['mesh'], result)
                    names = [call.args[0] for call in imports.call_args_list]
                    self.assertEqual('netgen.meshing' in names, needs_tetra)
                    self.assertFalse(any(name.startswith('Kratos') for name in names))
                    launch.assert_not_called()

    def test_geometry_is_available_independently_of_mechanical_license(self):
        for operation in ('preview', 'surface', 'doe_mesh', 'flow_export', 'thermal_export'):
            self.assertEqual(required_capability(dict(operation=operation, settings=asdict(Settings()))), 'mesh')
        self.assertEqual(required_capability(dict(operation='flow_solve')), 'flow')
        self.assertEqual(required_capability(dict(operation='thermal_solve')), 'thermal')

    def test_unsupported_operations_and_ansys_never_connect_or_compute(self):
        requests = [dict(operation='unknown'), dict(operation='ansys_mapdl')]
        for operation in ('mechanical', 'convergence_level', 'doe_solve'):
            for settings in (Settings(), replace(Settings(), solver_backend='kratos', test_profile='astm_e8_subsize'),
                             replace(Settings(), solver_backend='kratos', test_profile='iso178_preferred')):
                requests.append(dict(operation=operation, settings=asdict(settings)))
        with tempfile.TemporaryDirectory() as directory:
            for request in requests:
                with self.subTest(request=request), patch('truba_remote.RemoteTransport') as transport, patch('remote_worker.probe') as probe:
                    with self.assertRaises(ValueError):
                        run_remote_job(request, RemoteContext(), directory=Path(directory) / 'job')
                    transport.assert_not_called()
                    self.assertFalse((Path(directory) / 'job').exists())
                    with self.assertRaises(ValueError):
                        run_operation(request, Path(directory))
                    probe.assert_not_called()

    def test_kratos_adapter_limits_are_checked_before_meshing(self):
        request = dict(operation='mechanical', settings=asdict(replace(Settings(), solver_backend='kratos', mesh_type='HEX20')))
        with patch('remote_worker._stage'), patch('remote_worker.probe', return_value=dict(kratos=True, mesh_types=['HEX20'])), patch('mesh_engine.generate_volume_mesh') as mesh:
            with self.assertRaisesRegex(ValueError, 'HEX8, TET4'):
                run_operation(request, Path('.'))
            mesh.assert_not_called()


if __name__ == '__main__':
    unittest.main()
