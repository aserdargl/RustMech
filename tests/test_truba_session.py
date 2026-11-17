"""Resource session lifecycle, shared-allocation requests and isolated cancellation."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest
import shlex
import subprocess
from unittest.mock import MagicMock, patch

from truba_remote import RemoteContext, run_remote_job
from truba_jobs import HOST, cancel_remote, write_record
from truba_session import create_session, refresh_session, release_session, session_path
from truba_session_worker import run_request
from tests.test_truba_remote import FakeTransport
from tests.test_truba_monitor import BASH


class SessionTransport(FakeTransport):
    commands = []
    queue = 'RUNNING|2-23:59:00|None'
    step = 'COMPLETED|0:0'

    def __init__(self, context, auth=None, record=None):
        existing = record if record and record.get('local_directory') and (Path(record['local_directory'])/'request.json').exists() else None
        super().__init__(context, auth, existing)

    def command(self, script):
        self.commands.append(script)
        if 'SESSION_ID=' in script:
            return 'SESSION_ID=321\nSESSION_QUEUE=' + self.queue + '\nSESSION_READY=321\n'
        if 'SESSION_RELEASED' in script: return 'SESSION_RELEASED\n'
        if 'REQUEST_STOPPED' in script: return 'REQUEST_STOPPED\n'
        if 'RUSTMECH_STEP=' in script:
            return 'RUSTMECH_STEP=' + self.step + '\nRUSTMECH_QUEUE=' + self.queue + '\n'
        if 'RUSTMECH_JOB_ID' in script:
            if 'sbatch' in script: raise AssertionError('Session analysis submitted a new allocation')
            self.server_job = '321'
            return 'RUSTMECH_JOB_ID=321\n'
        return super().command(script)


class ResourceSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        SessionTransport.commands = []
        SessionTransport.queue = 'RUNNING|2-23:59:00|None'
        SessionTransport.step = 'COMPLETED|0:0'
        self.context = RemoteContext(partition='hamsi', cores=28, walltime='72:00:00')
        self.session = dict(kind='rustmech_session', host=HOST, username='tester',
                            job_id='321', remote_directory='/arf/scratch/tester/RustMech/'+'a'*32,
                            context=asdict(self.context), state='RUNNING', ready=True, transport='ssh')
        for name in ('truba_remote.state_directory', 'truba_session.state_directory', 'truba_jobs.state_directory'):
            item = patch(name, return_value=self.root/'state')
            item.start()
            self.addCleanup(item.stop)
        item = patch('truba_remote.RemoteTransport', SessionTransport)
        item.start()
        self.addCleanup(item.stop)
        write_record(session_path(), self.session)

    def test_two_analyses_and_resume_use_same_allocation_without_sbatch(self):
        context = replace(self.context, session_id='321')
        records = []
        for index in range(2):
            result, directory = run_remote_job(dict(operation='probe'), context, directory=self.root/f'job{index}')
            record = json.loads((directory/'remote_job.json').read_text())
            records.append(record)
            self.assertEqual(record['job_id'], '321')
            self.assertEqual(record['state'], 'downloaded')
            self.assertEqual(result['slurm_job_id'], '321')
        self.assertNotEqual(records[0]['request_id'], records[1]['request_id'])
        run_remote_job({}, context, resume_record=records[0])
        self.assertFalse(any('sbatch' in script for script in SessionTransport.commands))

    def test_pending_expired_and_changed_resources_never_submit_another_allocation(self):
        for queue in ('PENDING|3-00:00:00|Resources', ''):
            SessionTransport.queue = queue
            with self.assertRaises(RuntimeError):
                run_remote_job(dict(operation='probe'), replace(self.context, session_id='321'),
                               directory=self.root/('pending' if queue else 'expired'))
        SessionTransport.queue = 'RUNNING|1:00|None'
        with self.assertRaises(RuntimeError):
            run_remote_job(dict(operation='probe'), replace(self.context, cores=32, session_id='321'),
                           directory=self.root/'mismatch')
        self.assertFalse(any('sbatch' in script for script in SessionTransport.commands))

    def test_refresh_recovers_durable_session_and_release_is_explicit(self):
        result = refresh_session(self.session)
        self.assertTrue(result['ready'])
        self.assertEqual(result['remaining'], '2-23:59:00')
        released = release_session(result)
        self.assertEqual(released['state'], 'released')
        self.assertIn('scancel 321', SessionTransport.commands[-1])

    def test_confirmed_release_clears_only_requests_owned_by_that_session(self):
        directory = self.root / 'active-request'
        child = dict(kind='rustmech_remote', host=HOST, username='tester',
                     session=self.session, state='session · running', local_directory=str(directory))
        registry = self.root / 'state/jobs'
        write_record(registry / 'active.json', child)
        write_record(directory / 'remote_job.json', child)
        write_record(registry / 'downloaded.json', dict(child, state='downloaded'))
        write_record(registry / 'other.json', dict(child, session=dict(self.session, job_id='999')))
        release_session(self.session)
        self.assertEqual(json.loads((registry / 'active.json').read_text())['state'], 'cancelled')
        self.assertEqual(json.loads((directory / 'remote_job.json').read_text())['state'], 'cancelled')
        self.assertEqual(json.loads((registry / 'downloaded.json').read_text())['state'], 'downloaded')
        self.assertEqual(json.loads((registry / 'other.json').read_text())['state'], 'session · running')

    @unittest.skipUnless(BASH and Path(BASH).is_file(), 'Bash is required')
    def test_release_shell_waits_for_terminal_state_and_preserves_record_on_failure(self):
        def shell(always_running):
            def command(transport, script):
                preamble = '''scancel() { printf '%s\\n' "$1" > cancelled_job; }
sleep() { :; }
squeue() {
 n=$(cat polls 2>/dev/null || echo 0)
 n=$((n+1)); echo "$n" > polls
 if [ "$n" -lt 2 ]; then echo COMPLETING; fi
}
'''
                if always_running:
                    preamble += 'squeue() { echo RUNNING; }\n'
                path = self.root / 'release.sh'
                path.write_text(preamble + script, encoding='utf-8', newline='\n')
                completed = subprocess.run([str(BASH), '--noprofile', '--norc', str(path)],
                                           cwd=self.root, capture_output=True, timeout=15)
                if completed.returncode:
                    raise RuntimeError(completed.stderr.decode())
                return completed.stdout.decode()
            return command
        with patch.object(SessionTransport, 'command', shell(True)):
            with self.assertRaisesRegex(RuntimeError, 'still unconfirmed'):
                release_session(self.session)
        self.assertEqual(json.loads(session_path().read_text())['state'], 'RUNNING')
        with patch.object(SessionTransport, 'command', shell(False)):
            result = release_session(self.session)
        self.assertEqual(result['state'], 'released')
        self.assertEqual((self.root / 'cancelled_job').read_text().strip(), '321')
        self.assertEqual((self.root / 'polls').read_text().strip(), '2')
        with patch.object(SessionTransport, 'command', side_effect=AssertionError('duplicate cancellation')):
            self.assertEqual(release_session(result)['state'], 'released')

    @unittest.skipUnless(BASH and Path(BASH).is_file(), 'Bash is required')
    def test_actual_submission_shell_reuses_job_and_reports_scheduler_rejection(self):
        scripts = []
        server = self.root/'server'
        server.mkdir()
        def shell(transport, script):
            scripts.append(script)
            preamble = '''sbatch() { echo submitted >> calls.txt; echo 321; }
squeue() { echo 'RUNNING|2-23:59:00|None'; }
sacct() { echo 'COMPLETED|'; }
'''
            script = script.replace(shlex.quote(self.session['remote_directory']), shlex.quote(server.as_posix()))
            path = self.root/'command.sh'
            path.write_text(preamble+script,encoding='utf-8',newline='\n')
            completed = subprocess.run([str(BASH),'--noprofile','--norc',str(path)],capture_output=True,timeout=15)
            if completed.returncode: raise RuntimeError(completed.stderr.decode())
            return completed.stdout.decode()
        with patch.object(SessionTransport,'command',shell):
            first = refresh_session(self.session,submit=True)
            second = refresh_session(first,submit=True)
        self.assertEqual(first['job_id'],second['job_id'])
        self.assertEqual((server/'calls.txt').read_text().splitlines(),['submitted'])
        # Test failure through the same actual shell, then permit a clean retry.
        (server/'job.id').unlink()
        (server/'submission.lock').rmdir()
        def rejected(transport, script):
            script = script.replace('job=$(sbatch --parsable session.slurm 2>submission.err)',
                                    'job=$(echo "Partition time limit exceeded" >submission.err; false)')
            return shell(transport,script)
        with patch.object(SessionTransport,'command',rejected):
            result = refresh_session(self.session,submit=True)
        self.assertEqual(result['state'],'ended')
        self.assertIn('Partition time limit exceeded',result['reason'])
        self.assertFalse((server/'submission.lock').exists())

    def test_create_requests_chosen_duration_and_refuses_duplicate(self):
        session_path().unlink()
        result = create_session(self.context)
        self.assertEqual(result['job_id'], '321')
        self.assertEqual(sum('sbatch --parsable session.slurm' in s for s in SessionTransport.commands), 1)
        with self.assertRaises(RuntimeError): create_session(self.context)

    def test_analysis_cancel_never_cancels_parent_allocation(self):
        directory = self.root/'analysis'
        path = directory/'remote_job.json'
        record = dict(kind='rustmech_remote', host=HOST, username='tester', transport='ssh',
                      state='running', job_id='321', local_directory=str(directory),
                      remote_directory='/arf/scratch/tester/RustMech/'+'b'*32,
                      context=asdict(replace(self.context, session_id='321')), session=self.session)
        write_record(path, record)
        with patch('truba_jobs.connection', return_value=MagicMock(username='tester')) as connection:
            cancel_remote(path)
            connection.return_value.cancel.assert_not_called()
        self.assertEqual(json.loads(path.read_text())['state'], 'cancelled')
        self.assertFalse(any('scancel 321' in s for s in SessionTransport.commands))

    def test_dispatcher_repeated_or_cancelled_requests_never_launch(self):
        directory = self.root/'request'
        directory.mkdir()
        (directory/'cancel.request').touch()
        with patch('truba_session_worker.subprocess.Popen') as launch:
            run_request(directory)
            self.assertEqual((directory/'step.status').read_text(), 'CANCELLED|0:0')
            run_request(directory)
            launch.assert_not_called()

    def test_dispatcher_launches_only_srun_step_and_records_exit(self):
        directory = self.root/'request'
        directory.mkdir()
        process = MagicMock(returncode=0)
        process.poll.return_value = 0
        with patch.dict('os.environ', SLURM_CPUS_PER_TASK='4'), patch(
                'truba_session_worker.subprocess.Popen', return_value=process) as launch:
            run_request(directory)
        self.assertEqual(launch.call_args.args[0][0], 'srun')
        self.assertIn('--cpus-per-task=4', launch.call_args.args[0])
        self.assertEqual((directory/'step.status').read_text(), 'COMPLETED|0:0')

    def test_dispatcher_cancels_only_numeric_step(self):
        directory = self.root/'request'
        directory.mkdir()
        (directory/'step.id').write_text('7')
        process = MagicMock(returncode=-9)
        polls = iter([None, 0])
        def poll():
            (directory/'cancel.request').touch()
            return next(polls)
        process.poll.side_effect = poll
        with patch.dict('os.environ', SLURM_CPUS_PER_TASK='4', SLURM_JOB_ID='321'), patch(
                'truba_session_worker.subprocess.Popen', return_value=process), patch(
                'truba_session_worker.subprocess.run') as cancel:
            run_request(directory)
        self.assertEqual(cancel.call_args.args[0], ['scancel', '321.7'])
        self.assertEqual((directory/'step.status').read_text(), 'CANCELLED|0:0')

    def test_transient_step_cancel_failure_does_not_end_session(self):
        directory = self.root/'request'
        directory.mkdir()
        (directory/'step.id').write_text('7')
        process = MagicMock(returncode=-9)
        polls = iter([None, 0])
        def poll():
            (directory/'cancel.request').touch()
            return next(polls)
        process.poll.side_effect = poll
        with patch.dict('os.environ', SLURM_CPUS_PER_TASK='4', SLURM_JOB_ID='321'), patch(
                'truba_session_worker.subprocess.Popen', return_value=process), patch(
                'truba_session_worker.subprocess.run', side_effect=subprocess.TimeoutExpired('scancel',20)):
            run_request(directory)
        self.assertIn('will retry',(directory/'slurm.err').read_text())
        self.assertEqual((directory/'step.status').read_text(),'CANCELLED|0:0')


if __name__ == '__main__':
    unittest.main()

