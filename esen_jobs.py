"""LAMMPS input snapshots, Windows execution and standalone TRUBA jobs."""

import base64
from datetime import datetime
import json
import ntpath
import os
from pathlib import Path, PureWindowsPath
import re
import shlex
import shutil
import signal
import subprocess
import tarfile
from uuid import uuid4

from truba_credentials import state_directory
from truba_jobs import SAFE_NAME, validated_remote_path, write_record
from truba_remote import RemoteContext, RemoteTransport
from truba_resources import BASELINES


MODULES = {
    "hamsi": "apps/lammps/29Aug2024_update1_oneapi-2024-hamsi",
    "orfoz": "apps/lammps/29Aug2024_update1_oneapi-2024-orfoz",
}
DEFAULT_MODULE = "apps/lammps/29Aug2024_stable_oneapi-2024"
CPU_QUEUES = tuple(name for name in BASELINES if not name.endswith("-cuda"))
LOGS = ("stdout.log", "stderr.log")
TERMINAL = frozenset({"Completed", "Failed", "Cancelled", "Interrupted"})
SLURM_TERMINAL = frozenset({
    "BOOT_FAIL", "CANCELLED", "COMPLETED", "DEADLINE", "FAILED", "NODE_FAIL",
    "OUT_OF_MEMORY", "PREEMPTED", "REVOKED", "SPECIAL_EXIT", "TIMEOUT",
})
LOG_BYTES = 16000
FILE_LIMIT = 2000


def jobs_directory():
    return state_directory().parent / "ESEN" / "jobs"


def relative_file(name):
    """Accept paths that can be transferred between Linux and Windows."""
    parts = name.split("/")
    for part in parts:
        if (part in ("", ".", "..") or part.endswith((".", " "))
                or re.search(r'[\x00-\x1f<>:"\\|?*]', part)
                or is_reserved(part)):
            raise ValueError(f"Unsupported file path: {name}")
    return Path(*parts)


def is_reserved(name):
    if hasattr(ntpath, "isreserved"):
        return ntpath.isreserved(name)
    return PureWindowsPath(name).is_reserved()


def is_link(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def save_job(directory, record, **changes):
    updated = {**record, **changes}
    write_record(directory / "job.json", updated)
    record.update(changes)


def read_job(directory):
    return json.loads((directory / "job.json").read_text(encoding="utf-8"))


def new_job(root, entry, target, ranks, *, parent):
    directory = parent / uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    record = dict(name=entry, target=target, ranks=ranks, state="Preparing",
                  created=datetime.now().astimezone().isoformat(timespec="seconds"), source=str(root))
    save_job(directory, record)
    return directory, record


def snapshot_inputs(directory, root, entry, extra, whole_folder, stop=None):
    root = root.resolve(strict=True)
    if whole_folder and directory.resolve().is_relative_to(root):
        raise ValueError("Choose a job folder outside the ESEN history directory.")
    snapshot = directory / "inputs"
    snapshot.mkdir()

    def copy(name):
        if stop is not None and stop.is_set():
            raise InterruptedError("Input preparation cancelled.")
        relative = relative_file(name)
        source = root / relative
        if not source.is_file() or not source.resolve().is_relative_to(root) or is_link(source):
            raise ValueError(f"Missing file or symbolic link: {name}")
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    if whole_folder:
        def walk_error(error):
            raise error

        for folder, dirs, files in os.walk(root, onerror=walk_error):
            folder = Path(folder)
            for name in dirs[:]:
                source = folder / name
                if is_link(source):
                    dirs.remove(name)
                else:
                    relative = relative_file(source.relative_to(root).as_posix())
                    (snapshot / relative).mkdir(parents=True, exist_ok=True)
            for name in files:
                source = folder / name
                if not is_link(source):
                    copy(source.relative_to(root).as_posix())
    else:
        for name in dict.fromkeys((entry, *extra)):
            copy(name)
    if not (snapshot / relative_file(entry)).is_file():
        raise ValueError("The input script is missing from the snapshot.")
    return snapshot


def local_command(entry, ranks, executable="lmp", mpi=""):
    lammps = shutil.which(executable)
    if not lammps:
        raise ValueError(f"LAMMPS executable not found: {executable}")
    arguments = [lammps, "-nonbuf", "-in", relative_file(entry).as_posix()]
    if ranks > 1:
        if not mpi and os.name == "nt":
            ms_mpi = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Microsoft MPI/Bin/mpiexec.exe"
            if ms_mpi.is_file():
                mpi = str(ms_mpi)
        launcher = shutil.which(mpi or "mpiexec")
        if not launcher:
            raise ValueError("MPI launcher not found. Select mpiexec for this LAMMPS build.")
        arguments = [launcher, "-n", str(ranks), *arguments]
    return arguments


def stop_process(process):
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW, timeout=5, check=False,
            )
            if completed.returncode == 0 or process.poll() is not None:
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
        process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_local(directory, record, files, command, stop):
    try:
        snapshot = snapshot_inputs(directory, *files, stop=stop)
        work = directory / "work"
        shutil.copytree(snapshot, work)
        if stop.is_set():
            raise InterruptedError("Input preparation cancelled.")
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        with (directory / LOGS[0]).open("wb") as stdout, (directory / LOGS[1]).open("wb") as stderr:
            with subprocess.Popen(command, cwd=work, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                  env={**os.environ, "OMP_NUM_THREADS": "1"}, **options) as process:
                try:
                    save_job(directory, record, state="Running", command=command)
                    while process.poll() is None:
                        if stop.wait(0.25):
                            stop_process(process)
                            process.wait()
                            save_job(directory, record, state="Cancelled")
                            return
                finally:
                    if process.poll() is None:
                        stop_process(process)
        save_job(directory, record, state="Completed" if process.returncode == 0 else "Failed",
                 detail=f"Exit code: {process.returncode}")
    except InterruptedError:
        save_job(directory, record, state="Cancelled")
    except Exception as error:
        save_job(directory, record, state="Failed", detail=str(error))


def remote_script(entry, ranks, partition, account, memory, walltime, module, executable, launcher):
    if partition not in CPU_QUEUES or not SAFE_NAME.fullmatch(account) or account in (".", ".."):
        raise ValueError("Select a CPU queue and enter a valid TRUBA account.")
    RemoteContext(partition=partition, account=account, memory_mb=memory, walltime=walltime, cores=ranks).validate()
    if module and not re.fullmatch(r"[A-Za-z0-9_./+\-]+", module):
        raise ValueError("Invalid LAMMPS module name.")
    if not re.fullmatch(r"(?:/|~/)?[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*", executable):
        raise ValueError("Enter a Linux LAMMPS executable name or path without spaces.")
    target = '"$HOME"/' + shlex.quote(executable[2:]) if executable.startswith("~/") else shlex.quote(executable)
    if launcher not in ("mpirun", "srun"):
        raise ValueError("Select mpirun or srun.")
    launch = f"{launcher} -n {ranks} {target} -nonbuf -in {shlex.quote(relative_file(entry).as_posix())}"
    modules = f"module purge\nmodule load {shlex.quote(module)}\n" if module else ""
    return f"""#!/bin/bash -l
#SBATCH --partition={partition}
#SBATCH --account={account}
#SBATCH --nodes=1
#SBATCH --ntasks={ranks}
#SBATCH --cpus-per-task=1
#SBATCH --mem={memory}M
#SBATCH --time={walltime}
#SBATCH --output=stdout.log
#SBATCH --error=stderr.log
set -eo pipefail
{modules}export OMP_NUM_THREADS=1
cd work
exec {launch}
"""


def remote_location(directory, record, auth):
    if not auth or not SAFE_NAME.fullmatch(auth.username) or auth.username in (".", ".."):
        raise ValueError("Connect through RustMech's TRUBA button first.")
    if record.get("username", auth.username) != auth.username:
        raise ValueError("Connect using the TRUBA account that submitted this job.")
    path = f"/arf/scratch/{auth.username}/RustMech/{directory.name}"
    return RemoteTransport(RemoteContext(transport="ssh"), auth), validated_remote_path(path, auth.username)


def tagged(reply, key):
    match = re.search(rf"^{key}=(.*)$", reply, re.MULTILINE)
    if match is None:
        raise RuntimeError(f"Missing response from Slurm/TRUBA: {key}")
    return match[1].strip()


def submit_remote(directory, record, files, script, auth):
    try:
        transport, remote = remote_location(directory, record, auth)
        snapshot = snapshot_inputs(directory, *files)
        batch = directory / "run.slurm"
        batch.write_text(script, encoding="utf-8", newline="\n")
        archive = directory / "inputs.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(snapshot, arcname=".")
        work = shlex.quote(remote + "/work")
        transport.command(f"umask 077; mkdir -p -- {work}")
        transport.put(archive, remote + "/inputs.tar.gz")
        transport.put(batch, remote + "/run.slurm")
        transport.command(f"cd {shlex.quote(remote)} && tar -xzf inputs.tar.gz -C work && rm -- inputs.tar.gz")
        archive.unlink()
        # After this checkpoint a lost SSH reply is ambiguous. Reattach using
        # the durable job ID or unique Slurm name, without replaying sbatch.
        save_job(directory, record, state="Submitting", username=auth.username)
        name = "ESEN-" + directory.name
        reply = transport.command(f"""cd {shlex.quote(remote)} && {{
if job=$(sbatch --parsable --job-name={name} run.slurm 2>submit.err); then
    job=${{job%%;*}}
    [[ "$job" =~ ^[0-9]+$ ]] || exit 2
    printf '%s\\n' "$job" > job.id.tmp && mv -- job.id.tmp job.id || exit 3
    printf 'JOB_ID=%s\\n' "$job"
else
    printf 'JOB_ID=\\nREJECTION='; base64 -w0 submit.err; printf '\\n'
fi
}}""")
        job_id = tagged(reply, "JOB_ID")
        if not job_id:
            detail = decode(tagged(reply, "REJECTION"))
            save_job(directory, record, state="Failed", detail=detail.strip())
        elif re.fullmatch(r"[0-9]+", job_id):
            save_job(directory, record, state="Submitted", job_id=job_id)
        else:
            raise RuntimeError("Invalid Slurm job ID. Refresh to recover the submission.")
    except Exception as error:
        state = "Submitting" if record["state"] == "Submitting" else "Failed"
        save_job(directory, record, state=state, detail=str(error))


def decode(data):
    return base64.b64decode(data, validate=True).decode("utf-8", errors="replace")


def recover_id(transport, remote, directory):
    name = "ESEN-" + directory.name
    reply = transport.command(f"""cd {shlex.quote(remote)} && {{
if [ -s job.id ]; then
    printf 'JOB=%s\\n' "$(cat job.id)"
else
    ids=$(squeue -h -u "$(id -un)" -n {name} -o %A) || exit
    if [ -z "$ids" ]; then
        ids=$(sacct -n -X --name={name} -S 1970-01-01 --format=JobIDRaw -P) || exit
    fi
    printf 'JOB=%s\\n' "$(printf '%s\\n' "$ids" | sort -u | paste -sd, -)"
fi
}}""")
    job_id = tagged(reply, "JOB")
    if job_id and not re.fullmatch(r"[0-9]+", job_id):
        raise RuntimeError("Could not uniquely identify this submission in Slurm.")
    return job_id


def refresh_remote(directory, record, auth, *, cancel=False):
    transport, remote = remote_location(directory, record, auth)
    job_id = record.get("job_id") or recover_id(transport, remote, directory)
    if not job_id:
        save_job(directory, record, detail="Slurm has not confirmed this submission. Refresh to check again.")
        return
    if not re.fullmatch(r"[0-9]+", job_id):
        raise ValueError("Invalid Slurm job ID.")
    save_job(directory, record, job_id=job_id)
    if cancel:
        transport.command(f"scancel {job_id}")
        save_job(directory, record, cancel_requested=True)
    reply = transport.command(f"""cd {shlex.quote(remote)} || exit
queue=$(squeue -h -j {job_id} -o '%T|%r') || exit
accounting=$(sacct -n -X -j {job_id} --format=State,ExitCode -P) || exit
printf 'QUEUE=%s\\nACCT=%s\\n' "$queue" "$accounting"
for log in {' '.join(LOGS)}; do
    printf '%s=' "$log"
    if [ -f "$log" ] && [ ! -L "$log" ]; then tail -c {LOG_BYTES} -- "$log" | base64 -w0; fi
    printf '\\n'
done
""")
    queue = tagged(reply, "QUEUE")
    accounting = tagged(reply, "ACCT")
    status, _, code = accounting.rstrip("|").partition("|")
    status = status.split(" ", 1)[0].rstrip("+")
    for name in LOGS:
        (directory / name).write_bytes(base64.b64decode(tagged(reply, re.escape(name)), validate=True))
    if status in SLURM_TERMINAL and not queue:
        state = {"COMPLETED": "Completed" if code == "0:0" else "Failed", "CANCELLED": "Cancelled"}.get(status, "Failed")
        save_job(directory, record, state=state, detail=accounting.rstrip("|"))
    else:
        state = "Cancellation requested" if record.get("cancel_requested") else "Running" if queue.startswith("RUNNING|") else "Queued"
        save_job(directory, record, state=state, detail=queue or "Waiting for Slurm accounting…")


def remote_files(directory, record, auth):
    transport, remote = remote_location(directory, record, auth)
    reply = transport.command(f"""cd {shlex.quote(remote)} || exit
set -eo pipefail
printf 'FILES='
find -P work -type f -printf '%P\\0' | sort -z | sed -z -n '1,{FILE_LIMIT + 1}p' | base64 -w0
printf '\\n'
for log in {' '.join(LOGS)}; do
    if [ -f "$log" ] && [ ! -L "$log" ]; then printf 'LOG=%s\\n' "$log"; fi
done
""")
    names = decode(tagged(reply, "FILES")).split("\0")[:-1]
    # Separate job logs from work files with identical basenames.
    paths = ["work/" + name for name in names[:FILE_LIMIT]]
    paths[:0] = re.findall(r"^LOG=(stdout\.log|stderr\.log)$", reply, re.MULTILINE)
    portable = []
    omitted = len(names) > FILE_LIMIT
    for name in paths:
        try:
            relative_file(name)
        except ValueError:
            omitted = True
        else:
            portable.append(name)
    save_job(directory, record, files=portable, files_omitted=omitted)


def download_file(directory, record, name, auth):
    if name not in record.get("files", []):
        raise ValueError("Refresh the result list before downloading this file.")
    relative = relative_file(name)
    transport, remote = remote_location(directory, record, auth)
    source = remote + "/" + name
    quoted = shlex.quote(source)
    transport.command(f'test -f {quoted} && test "$(realpath -- {quoted})" = {quoted}')
    destination = directory / "downloads" / relative
    if not destination.resolve().is_relative_to(directory.resolve()):
        raise ValueError("Invalid local result path.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{uuid4().hex}.part")
    try:
        transport.get(source, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def log_tail(path):
    try:
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - LOG_BYTES))
            return stream.read(LOG_BYTES).decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""
