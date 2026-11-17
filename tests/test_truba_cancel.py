"""Run the cancellation shell against scheduler responses, without network access."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from truba_jobs import cancellation_script

BASH = Path(r'C:\Program Files\Git\bin\bash.exe') if os.name == 'nt' else shutil.which('bash')


@unittest.skipUnless(BASH and Path(BASH).is_file(), 'Bash is required')
class CancellationTests(unittest.TestCase):
    def run_script(self, scheduler, *, job_id='123', remote_id='123', lock=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote = '/arf/scratch/tester/RustMech/' + 'a' * 32
            script = cancellation_script(dict(remote_directory=remote, job_id=job_id), 'tester')
            script = script.replace(remote, '.')
            if remote_id:
                (root / 'job.id').write_text(remote_id)
            if lock:
                (root / 'submission.lock').mkdir()
            (root / 'cancel.sh').write_text(scheduler + '\n' + script, encoding='utf-8', newline='\n')
            result = subprocess.run([str(BASH), '--noprofile', '--norc', 'cancel.sh'], cwd=root,
                                    capture_output=True, timeout=10)
            called = (root / 'cancelled').read_text().strip() if (root / 'cancelled').exists() else ''
            return result, called

    def test_expired_job_is_confirmed_even_when_scancel_rejects_id(self):
        result, _ = self.run_script('''scancel() { return 1; }
squeue() { return 1; }
sacct() { echo 'CANCELLED by 1000|'; }
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_recorded_id_used_when_remote_id_file_missing(self):
        result, called = self.run_script('''scancel() { echo "$1" > cancelled; }
squeue() { :; }
''', remote_id='')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(called, '123')

    def test_active_job_and_failed_scheduler_query_never_report_success(self):
        for queue in ("squeue() { echo RUNNING; }", "squeue() { return 1; }; sacct() { return 1; }"):
            with self.subTest(queue=queue):
                result, _ = self.run_script('scancel() { return 1; }; sleep() { :; }\n' + queue)
                self.assertNotEqual(result.returncode, 0)

    def test_unconfirmed_submission_and_mismatched_id_are_retryable(self):
        for options in (dict(job_id='', remote_id='', lock=True), dict(remote_id='456')):
            result, called = self.run_script('sleep() { :; }', **options)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(called, '')


if __name__ == '__main__':
    unittest.main()
