"""Compute-node dispatcher for one bounded Slurm allocation (stdlib only)."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


def write_status(directory, value):
    temporary = directory / 'step.status.tmp'
    temporary.write_text(value, encoding='utf-8')
    temporary.replace(directory / 'step.status')


def run_request(directory):
    if (directory / 'step.status').exists():
        return
    if (directory / 'cancel.request').exists():
        write_status(directory, 'CANCELLED|0:0')
        return
    write_status(directory, 'RUNNING')
    # The batch dispatcher consumes no srun step. Each request owns the allotted
    # cores and memory, and cancellation addresses only this numeric job step.
    command = ['srun', '--exclusive', '--nodes=1', '--ntasks=1',
               '--cpus-per-task=' + os.environ['SLURM_CPUS_PER_TASK'],
               'bash', '-c', 'printf "%s\\n" "$SLURM_STEP_ID" > step.id; '
               'test ! -e cancel.request || exit 130; exec bash -l run.slurm']
    with (directory / 'slurm.out').open('wb') as out, (directory / 'slurm.err').open('wb') as err:
        process = subprocess.Popen(command, cwd=directory, stdout=out, stderr=err)
        cancelled = False
        last_cancel = -10.0
        while process.poll() is None:
            if (directory / 'cancel.request').exists() and time.monotonic() - last_cancel >= 5:
                cancelled = True
                last_cancel = time.monotonic()
                step = directory / 'step.id'
                try:
                    if step.exists() and re.fullmatch(r'\d+', step.read_text().strip()):
                        subprocess.run(['scancel', os.environ['SLURM_JOB_ID'] + '.' + step.read_text().strip()],
                                       check=False, timeout=20)
                    else:
                        process.terminate()
                except (OSError, subprocess.SubprocessError) as error:
                    # A failed cancellation must not tear down the allocation.
                    # Retry and acknowledge only once the step actually exits.
                    err.write(f'Step cancellation will retry: {error}\n'.encode('utf-8'))
                    err.flush()
            time.sleep(.5)
    code = process.returncode
    write_status(directory, 'CANCELLED|0:0' if cancelled else
                 ('COMPLETED|0:0' if code == 0 else f'FAILED|{code}:0'))


def main(root):
    root = Path(root).resolve()
    inbox = root / 'inbox'
    inbox.mkdir(exist_ok=True)
    (root / 'ready').write_text(os.environ['SLURM_JOB_ID'])
    while True:
        for request in sorted(inbox.glob('*.json'), key=lambda p: (p.stat().st_mtime_ns, p.name)):
            try:
                directory = Path(json.loads(request.read_text())['directory']).resolve()
                if directory.parent != root.parent or not re.fullmatch(r'[a-f0-9]{32}', directory.name):
                    raise ValueError('Request outside RustMech job directory')
                run_request(directory)
                request.rename(request.with_suffix('.done'))
            except Exception as error:
                # A dispatcher error ends the allocation; clients then fail
                # closed rather than waiting forever on a lost request.
                print(f'Dispatch failed: {error}', file=sys.stderr, flush=True)
                raise
        time.sleep(.5)


if __name__ == '__main__':
    main(sys.argv[1])
