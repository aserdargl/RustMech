"""Compute-node dispatcher for one bounded Slurm allocation (stdlib only)."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor


def write_status(directory, value):
    temporary = directory / 'step.status.tmp'
    temporary.write_text(value, encoding='utf-8')
    temporary.replace(directory / 'step.status')


def run_request(directory, cores=None, memory_mb=None):
    if (directory / 'step.status').exists():
        return
    if (directory / 'cancel.request').exists():
        write_status(directory, 'CANCELLED|0:0')
        return
    write_status(directory, 'RUNNING')
    cores = cores or int(os.environ['SLURM_CPUS_PER_TASK'])
    command = ['srun', '--exclusive', '--exact', '--cpu-bind=cores', '--nodes=1', '--ntasks=1',
               '--cpus-per-task=' + str(cores)]
    if memory_mb is not None:
        command.append('--mem=' + str(memory_mb) + 'M')
    command += [
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


def dispatch_pending(root, active, executor, total_cores, total_memory):
    """Admit independent steps only when both CPU and RAM budgets fit."""
    root = Path(root).resolve()
    for request, (future, _, _) in list(active.items()):
        if future.done():
            try:
                future.result()
            except Exception as error:
                # A failed srun launch belongs to this request. Keep other
                # running steps and the resource dispatcher alive.
                directory = Path(json.loads(request.read_text())['directory']).resolve()
                with (directory / 'slurm.err').open('a', encoding='utf-8') as stream:
                    stream.write(f'Step execution failed: {error}\n')
                write_status(directory, 'FAILED|2:0')
            request.rename(request.with_suffix('.done'))
            del active[request]
    used_cores = sum(item[1] for item in active.values())
    used_memory = sum(item[2] for item in active.values())
    inbox = root / 'inbox'
    for request in sorted(inbox.glob('*.json'), key=lambda p: (p.stat().st_mtime_ns, p.name)):
        if request in active:
            continue
        data = json.loads(request.read_text())
        directory = Path(data['directory']).resolve()
        if (directory.parent != root.parent or request.stem != directory.name
                or not re.fullmatch(r'[a-f0-9]{32}', directory.name)):
            raise ValueError('Request outside RustMech job directory')
        cores, memory = data.get('cores', total_cores), data.get('memory_mb', total_memory)
        if (type(cores) is not int or type(memory) is not int
                or not 1 <= cores <= total_cores or not 1 <= memory <= total_memory):
            write_status(directory, 'FAILED|2:0')
            request.rename(request.with_suffix('.done'))
            continue
        if (directory / 'cancel.request').exists() or (directory / 'step.status').exists():
            run_request(directory, cores, memory)
            request.rename(request.with_suffix('.done'))
            continue
        if used_cores + cores > total_cores or used_memory + memory > total_memory:
            continue
        active[request] = (executor.submit(run_request, directory, cores, memory), cores, memory)
        used_cores += cores
        used_memory += memory


def main(root):
    root = Path(root).resolve()
    inbox = root / 'inbox'
    inbox.mkdir(exist_ok=True)
    (root / 'ready').write_text(os.environ['SLURM_JOB_ID'])
    cores = int(os.environ['SLURM_CPUS_PER_TASK'])
    memory = int(os.environ['SLURM_MEM_PER_NODE'])
    active = {}
    with ThreadPoolExecutor(max_workers=cores) as executor:
        while True:
            dispatch_pending(root, active, executor, cores, memory)
            time.sleep(.5)


if __name__ == '__main__':
    main(sys.argv[1])
