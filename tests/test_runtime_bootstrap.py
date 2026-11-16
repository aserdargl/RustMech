"""Automatic runtime setup reuses the allocation and never loops on failures."""
from concurrent.futures import CancelledError
from threading import Event
import unittest
from unittest.mock import patch

from remote_controller import ensure_remote_runtime
from truba_remote import RemoteContext


class RuntimeBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.context = RemoteContext(session_id='321')
        self.cancelled = Event()
        self.healthy = (dict(capabilities=dict(mesh=True, netgen=True, kratos=True)), 'verified')

    def run_setup(self):
        return ensure_remote_runtime(self.context, {'username': 'tester'}, cancelled=self.cancelled)

    def test_healthy_runtime_is_reused_without_installation(self):
        with patch('remote_controller.run_remote_job', return_value=self.healthy) as run:
            self.assertEqual(self.run_setup(), self.healthy)
        self.assertEqual([call.args[0]['operation'] for call in run.call_args_list], ['probe'])

    def test_missing_components_trigger_one_prepare_in_same_allocation(self):
        for missing in ('mesh', 'netgen', 'kratos'):
            with self.subTest(missing=missing):
                capabilities = dict(mesh=True, netgen=True, kratos=True)
                capabilities[missing] = False
                with patch('remote_controller.run_remote_job', side_effect=[
                        (dict(capabilities=capabilities), 'probe'), self.healthy]) as run:
                    self.assertEqual(self.run_setup(), self.healthy)
                self.assertEqual([call.args[0]['operation'] for call in run.call_args_list], ['probe', 'prepare'])
                for call in run.call_args_list:
                    self.assertEqual(call.args[1].session_id, '321')
                    self.assertIs(call.kwargs['cancelled'], self.cancelled)

    def test_probe_failure_does_not_blindly_install(self):
        with patch('remote_controller.run_remote_job', side_effect=RuntimeError('SSH failed')) as run:
            with self.assertRaisesRegex(RuntimeError, 'SSH failed'):
                self.run_setup()
        self.assertEqual(run.call_count, 1)

    def test_failed_install_is_reported_without_retry_loop(self):
        with patch('remote_controller.run_remote_job', side_effect=[
                (dict(capabilities=dict(mesh=False)), 'probe'), RuntimeError('Download failed')]) as run:
            with self.assertRaisesRegex(RuntimeError, 'Download failed'):
                self.run_setup()
        self.assertEqual(run.call_count, 2)

    def test_custom_runtime_is_not_modified(self):
        from dataclasses import replace
        self.context = replace(self.context, runtime_python='/opt/custom/bin/python')
        result = (dict(capabilities=dict(mesh=False)), 'probe')
        with patch('remote_controller.run_remote_job', return_value=result) as run:
            self.assertEqual(self.run_setup(), result)
        self.assertEqual(run.call_count, 1)

    def test_cancel_after_probe_prevents_install(self):
        self.cancelled.set()
        with patch('remote_controller.run_remote_job', return_value=(dict(capabilities={}), 'probe')) as run:
            with self.assertRaises(CancelledError):
                self.run_setup()
        self.assertEqual(run.call_count, 1)
