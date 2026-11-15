"""Unsupported remote requests fail before connecting or generating a mesh."""
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from model import Settings
from remote_operations import required_capability
from remote_worker import run_operation
from truba_remote import RemoteContext, run_remote_job


class RemoteOperationTests(unittest.TestCase):
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
        with patch('remote_worker.probe', return_value=dict(kratos=True, mesh_types=['HEX20'])), patch('mesh_engine.generate_volume_mesh') as mesh:
            with self.assertRaisesRegex(ValueError, 'HEX8, TET4'):
                run_operation(request, Path('.'))
            mesh.assert_not_called()


if __name__ == '__main__':
    unittest.main()
