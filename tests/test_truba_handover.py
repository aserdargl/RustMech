"""Durable replacement allocation, request routing and complete resource release."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from truba_jobs import HOST, write_record
from truba_remote import RemoteContext, request_resources, slurm_script
from truba_session import refresh_session, release_session, saved_session, session_path
from truba_handover import (context_for_request, prepare_if_needed, remaining_seconds,
                            renewal_due, successor_path)
from tests.test_truba_session import SessionTransport


class HandoverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.context = RemoteContext(partition='hamsi', cores=56, memory_mb=128000,
                                     walltime='72:00:00', parallel_jobs=4)
        self.session = dict(kind='rustmech_session', host=HOST, username='tester', job_id='321',
            remote_directory='/arf/scratch/tester/RustMech/'+'a'*32, context=asdict(self.context),
            state='RUNNING', ready=True, transport='ssh', remaining='00:45:00')
        self.successor = dict(self.session, job_id='654', renewal_parent_id='321',
                              remote_directory='/arf/scratch/tester/RustMech/'+'b'*32,
                              remaining='2-23:59:00')
        for name in ('truba_session.state_directory', 'truba_jobs.state_directory', 'truba_remote.state_directory'):
            item = patch(name, return_value=self.root / 'state')
            item.start()
            self.addCleanup(item.stop)
        write_record(session_path(), self.session)

    def test_time_formats_and_short_allocations_do_not_renew_immediately(self):
        self.assertEqual(remaining_seconds('2-23:59:00'), 259140)
        self.assertEqual(remaining_seconds('59:30'), 3570)
        self.assertIsNone(remaining_seconds('UNLIMITED'))
        self.assertTrue(renewal_due(self.session))
        self.assertFalse(renewal_due(dict(self.session, remaining='02:00:00')))
        self.assertFalse(renewal_due(dict(self.session, remaining='00:25:00',
                                         context=asdict(replace(self.context, walltime='00:30:00')))))
        self.assertFalse(renewal_due(dict(self.session, context=asdict(replace(self.context, auto_renew=False)))))
        self.assertFalse(renewal_due(dict(self.session, state='ended', terminal_state='CANCELLED', renewal_due_seen=True)))
        self.assertTrue(renewal_due(dict(self.session, state='ended', terminal_state='TIMEOUT', renewal_due_seen=True)))

    def test_multiple_monitors_prepare_only_one_durable_replacement(self):
        def refresh(record, *args, **kwargs):
            return record
        def create(*args, **kwargs):
            write_record(successor_path(), self.successor)
            return self.successor
        with patch('truba_handover.refresh_session', side_effect=refresh), patch(
                'truba_handover.create_session', side_effect=create) as submit:
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(lambda _: prepare_if_needed(self.session), range(4)))
        self.assertEqual(submit.call_count, 1)
        self.assertEqual([item['job_id'] for item in results], ['654'] * 4)
        self.assertEqual(saved_session()['job_id'], '321')

    def test_ready_successor_promotes_and_old_context_routes_without_rerunning_results(self):
        write_record(successor_path(), self.successor)
        with patch('truba_handover.refresh_session', side_effect=lambda record, *a, **kw: record):
            context = context_for_request(replace(self.context, session_id='321'))
            self.assertEqual(context.session_id, '654')
            self.assertEqual(saved_session()['previous_sessions'][0]['job_id'], '321')
            again = context_for_request(replace(self.context, session_id='321'))
            self.assertEqual(again.session_id, '654')
        self.assertFalse(successor_path().exists())
        self.assertEqual(saved_session()['state'], 'RUNNING')

    def test_pending_successor_wait_can_be_cancelled_without_cancelling_old_analysis(self):
        pending = dict(self.successor, state='PENDING', ready=False)
        write_record(successor_path(), pending)
        cancelled = Event()
        with patch('truba_handover.refresh_session', side_effect=lambda record, *a, **kw: record):
            from concurrent.futures import CancelledError
            with self.assertRaises(CancelledError):
                context_for_request(replace(self.context, session_id='321'), cancelled=cancelled,
                                    on_progress=lambda _: cancelled.set())
        self.assertEqual(saved_session()['job_id'], '321')
        self.assertEqual(json.loads(successor_path().read_text())['job_id'], '654')

    def test_refresh_of_old_record_never_overwrites_promoted_session(self):
        current = dict(self.successor, previous_sessions=[self.session])
        write_record(session_path(), current)
        def command(transport, script):
            return 'SESSION_ID=654\nSESSION_QUEUE=RUNNING|2-23:59:00|None\nSESSION_READY=654\n'
        with patch('truba_remote.RemoteTransport', SessionTransport), patch.object(SessionTransport, 'command', command):
            self.assertEqual(refresh_session(self.session)['job_id'], '654')
        self.assertEqual(saved_session()['job_id'], '654')

    def test_release_cancels_prepared_replacement_and_parent(self):
        write_record(successor_path(), self.successor)
        SessionTransport.commands = []
        with patch('truba_remote.RemoteTransport', SessionTransport):
            result = release_session(self.session)
        self.assertEqual(result['state'], 'released')
        commands = '\n'.join(SessionTransport.commands)
        self.assertIn('scancel 654', commands)
        self.assertIn('scancel 321', commands)
        self.assertFalse(successor_path().exists())
        self.assertEqual(saved_session()['state'], 'released')

    def test_batch_steps_divide_cpu_ram_and_thread_environment(self):
        self.assertEqual(request_resources(self.context, 'doe_solve'), (14, 32000))
        self.assertEqual(request_resources(self.context, 'probe'), (56, 128000))
        script = slurm_script(self.context, 'tester', dict(operation='doe_solve', source_sha256='0'*64, native_sha256='0'*64))
        self.assertIn('OMP_NUM_THREADS=14 RUSTMECH_THREADS=14', script)
        self.assertIn('#SBATCH --cpus-per-task=56', script)

    def test_release_from_old_ui_record_also_releases_promoted_and_draining_resources(self):
        write_record(session_path(), dict(self.successor, previous_sessions=[self.session]))
        SessionTransport.commands = []
        with patch('truba_remote.RemoteTransport', SessionTransport):
            result = release_session(self.session)
        self.assertEqual(result['job_id'], '654')
        self.assertEqual(result['state'], 'released')
        commands = '\n'.join(SessionTransport.commands)
        self.assertIn('scancel 321', commands)
        self.assertIn('scancel 654', commands)
        self.assertEqual(saved_session()['job_id'], '654')

    def test_releasing_unrelated_record_never_overwrites_current_session(self):
        with self.assertRaisesRegex(RuntimeError, 'different resource'):
            release_session(dict(self.session, job_id='999'))
        self.assertEqual(saved_session()['job_id'], '321')
        self.assertFalse(saved_session().get('renewal_disabled', False))

    def test_submission_payload_and_worker_context_use_the_same_resource_share(self):
        from truba_remote import run_remote_job
        SessionTransport.queue = 'RUNNING|2-23:59:00|None'
        SessionTransport.step = 'COMPLETED|0:0'
        SessionTransport.commands = []
        with patch('truba_remote.RemoteTransport', SessionTransport):
            _, directory = run_remote_job(dict(operation='doe_mesh'), replace(self.context, session_id='321'),
                                          directory=self.root / 'analysis')
        request = json.loads((directory / 'request.json').read_text())
        self.assertEqual(request['remote_context']['cores'], 14)
        self.assertEqual(request['remote_context']['memory_mb'], 32000)
        payload = next(script for script in SessionTransport.commands if 'RUSTMECH_JOB_ID=' in script)
        self.assertIn('"cores": 14', payload)
        self.assertIn('"memory_mb": 32000', payload)
        self.assertFalse(any('sbatch' in script for script in SessionTransport.commands))


if __name__ == '__main__':
    unittest.main()
