"""Exercise the actual shell monitor with a fake scheduler, including recovery."""
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from truba_jobs import MONITOR

BASH=Path(r'C:\Program Files\Git\bin\bash.exe') if os.name=='nt' else shutil.which('bash')


@unittest.skipUnless(BASH and Path(BASH).is_file(),'Bash is required')
class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'monitor.sh').write_text(MONITOR,encoding='utf-8',newline='\n')
        (self.root/'harness.sh').write_text('''
sbatch() { printf 'submitted\\n' >> calls.txt; sleep .2; echo 123; }
squeue() { :; }
sacct() { echo 'COMPLETED|0:0|'; }
scancel() { printf 'cancelled %s\\n' "$1" >> calls.txt; }
source ./monitor.sh
''',encoding='utf-8',newline='\n')

    def start(self):
        return subprocess.Popen([str(BASH),'--noprofile','--norc','harness.sh'],cwd=self.root,
            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

    def test_concurrent_monitors_submit_once_and_resume_same_job(self):
        first=self.start();second=self.start()
        for process in (first,second):
            output=process.communicate(timeout=15)[0]
            self.assertEqual(process.returncode,0,output)
            self.assertIn(b'RUSTMECH_JOB_ID=123',output)
        resumed=self.start();output=resumed.communicate(timeout=15)[0]
        self.assertEqual(resumed.returncode,0,output)
        self.assertEqual((self.root/'calls.txt').read_text().splitlines(),['submitted'])
        self.assertEqual((self.root/'results/slurm_status.txt').read_text().strip(),'COMPLETED|0:0|')

    def test_cancel_before_submission_never_creates_job(self):
        (self.root/'cancel.request').touch()
        process=self.start();process.communicate(timeout=15)
        self.assertEqual(process.returncode,130)
        self.assertFalse((self.root/'calls.txt').exists())

    def test_corrupt_job_id_is_rejected_without_resubmission(self):
        (self.root/'job.id').write_text('123;echo unsafe')
        process=self.start();process.communicate(timeout=15)
        self.assertEqual(process.returncode,2)
        self.assertFalse((self.root/'calls.txt').exists())


if __name__=='__main__':unittest.main()
