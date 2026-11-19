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
from truba_remote import RemoteContext, run_remote_job, slurm_script, submit_remote_analysis


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
        self.assertEqual(required_capability(dict(operation='fft_solve', settings=asdict(Settings()))), 'damask')

    def test_fft_job_uses_the_shared_durable_submission(self):
        request = dict(operation='fft_solve', settings=asdict(Settings()), grid=128, void_ratio=.05)
        context = RemoteContext(session_id='123')
        with patch('truba_remote.run_remote_job') as submit:
            self.assertIs(submit_remote_analysis(request, context, 'auth', 'marker'), submit.return_value)
        submit.assert_called_once_with(request, replace(context, session_id=''), 'auth', 'marker', submit_only=True)

    def test_fft_worker_exports_small_results_without_finite_element_meshing(self):
        settings = Settings(mesh_type='TET10')
        request = dict(operation='fft_solve', settings=asdict(settings), grid=128, void_ratio=.05)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'output'
            output.mkdir()

            def solve(_, grid, ratio, distribution, case, *, on_progress):
                self.assertEqual((grid, ratio, distribution), (128, .05, None))
                on_progress('DAMASK progress')
                for name in ('stiffness_mpa.csv', 'status.json', 'xx_plus.hdf5', 'cell.vti'):
                    (case / name).touch()

            with patch('remote_worker.probe', return_value=dict(damask=True)), patch(
                    'remote_worker.Path.cwd', return_value=root), patch('remote_worker._stage') as stage, patch(
                    'damask_fft.run_periodic_fft', side_effect=solve), patch('mesh_engine.generate_volume_mesh') as mesh:
                result = run_operation(request, output)
            stage.assert_any_call('DAMASK progress')
            mesh.assert_not_called()
            self.assertEqual(Path(result['run_folder']), (output / 'fft').resolve())
            self.assertEqual({path.name for path in (output / 'fft').iterdir()}, {'stiffness_mpa.csv', 'status.json'})

    def test_fft_runtime_preparation_is_scoped_to_fft_setup(self):
        request = dict(operation='prepare', source_sha256='checksum', native_sha256='native')
        self.assertNotIn('prepare-damask.sh', slurm_script(RemoteContext(), 'tester', request))
        request['prepare_damask'] = True
        self.assertIn('prepare-damask.sh', slurm_script(RemoteContext(), 'tester', request))

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
        request = dict(operation='mechanical', settings=asdict(replace(Settings(), solver_backend='kratos', mesh_type='HEX20',
                       self_contact_mode='disabled', kratos_contact_method='penalty', loading_mode='force')))
        with patch('remote_worker._stage'), patch('remote_worker.probe', return_value=dict(kratos=True, mesh_types=['HEX20'])), patch('mesh_engine.generate_volume_mesh') as mesh:
            with self.assertRaisesRegex(ValueError, 'requires displacement control'):
                run_operation(request, Path('.'))
            mesh.assert_not_called()


if __name__ == '__main__':
    unittest.main()
