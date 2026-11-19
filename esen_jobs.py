"""LAMMPS input snapshots, Windows execution and standalone TRUBA jobs."""

import base64
from contextlib import suppress
from datetime import datetime
from filecmp import cmp
from functools import partial
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
from tempfile import TemporaryDirectory
from uuid import NAMESPACE_URL, uuid4, uuid5

import esen_restart
from esen_windows_job import limited_process
from truba_credentials import state_directory
from truba_jobs import SAFE_NAME, validated_remote_path, write_record
from truba_remote import RemoteContext, RemoteTransport, safe_extract
from truba_resources import BASELINES, candidates, fetch_catalog


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
RESULT_DIRECTORY = re.compile(r"(?:ESEN-[0-9a-f]{32}|\.esen-export-[0-9a-f]{32}-.+)", re.IGNORECASE)


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


def is_input_script(name):
    path = Path(name)
    return path.name.lower().startswith("in.") or path.suffix.lower() == ".in"


def scan_folder_files(root):
    """List files by working directory, skipping Results and links."""
    def walk_error(error):
        raise error

    found = []
    for folder, dirs, files in os.walk(root.resolve(strict=True), onerror=walk_error):
        folder = Path(folder)
        dirs[:] = sorted((name for name in dirs if name.casefold() != "results"
                          and not is_link(folder / name)), key=str.casefold)
        for name in sorted(files, key=str.casefold):
            path = folder / name
            if path.is_file() and not is_link(path):
                found.append(path)
    return found


def save_job(directory, record, **changes):
    updated = {**record, **changes}
    write_record(directory / "job.json", updated)
    record.update(changes)


def read_job(directory):
    return json.loads((directory / "job.json").read_text(encoding="utf-8"))


def new_job(root, entry, target, ranks, *, parent, batch_folder=None):
    directory = parent / uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    record = dict(name=entry, target=target, ranks=ranks, state="Preparing",
                  created=datetime.now().astimezone().isoformat(timespec="seconds"), source=str(root))
    if batch_folder is not None:
        record["batch_folder"] = batch_folder
    if root.name == "inputs" and root.parent.parent == parent:
        # Reused inputs are stored in job history, while their results belong in the original job folder.
        with suppress(OSError, ValueError):
            previous = read_job(root.parent)
            record["results_source"] = previous.get("results_source") or previous.get("source")
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
                if is_link(source) or (folder.name.casefold() == "results" and RESULT_DIRECTORY.fullmatch(name)):
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


def run_local(directory, record, files, command, stop, *, memory_mib=0):
    try:
        snapshot = snapshot_inputs(directory, *files, stop=stop)
        work = directory / "work"
        shutil.copytree(snapshot, work)
        if stop.is_set():
            raise InterruptedError("Input preparation cancelled.")
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        env = {**os.environ, "OMP_NUM_THREADS": "1"}
        with (directory / LOGS[0]).open("wb") as stdout, (directory / LOGS[1]).open("wb") as stderr:
            if memory_mib:
                launch = limited_process(command, cwd=work, env=env, stdout=stdout, stderr=stderr,
                                         memory_mib=memory_mib)
            else:
                launch = subprocess.Popen(command, cwd=work, stdin=subprocess.DEVNULL, stdout=stdout,
                                          stderr=stderr, env=env, **options)
            with launch as process:
                cancel = process.kill if memory_mib else partial(stop_process, process)
                try:
                    save_job(directory, record, state="Running", command=command, memory_mib=memory_mib)
                    while process.poll() is None:
                        if stop.wait(0.25):
                            cancel()
                            process.wait()
                            save_job(directory, record, state="Cancelled")
                            return
                finally:
                    if process.poll() is None:
                        cancel()
                        process.wait()
        save_job(directory, record, state="Completed" if process.returncode == 0 else "Failed",
                 detail=f"Exit code: {process.returncode}")
    except InterruptedError:
        save_job(directory, record, state="Cancelled")
    except Exception as error:
        save_job(directory, record, state="Failed", detail=str(error))


def remote_script(entry, ranks, partition, account, memory, walltime, module, executable, launcher,
                  *, resume_input=""):
    if partition not in CPU_QUEUES or not SAFE_NAME.fullmatch(account) or account in (".", ".."):
        raise ValueError("Select a CPU queue and enter a valid TRUBA account.")
    RemoteContext(partition=partition, account=account, walltime=walltime).validate()
    if type(ranks) is not int or ranks < 1 or type(memory) is not int or memory < 1:
        raise ValueError("TRUBA requires positive CPU and RAM allocations.")
    if module and not re.fullmatch(r"[A-Za-z0-9_./+\-]+", module):
        raise ValueError("Invalid LAMMPS module name.")
    if not re.fullmatch(r"(?:/|~/)?[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*", executable):
        raise ValueError("Enter a Linux LAMMPS executable name or path without spaces.")
    target = '"$HOME"/' + shlex.quote(executable[2:]) if executable.startswith("~/") else shlex.quote(executable)
    if launcher not in ("mpirun", "srun"):
        raise ValueError("Select mpirun or srun.")
    entry = relative_file(entry).as_posix()
    launch = f"{launcher} -n {ranks} {target} -nonbuf -in {shlex.quote(entry)}"
    modules = f"module purge\nmodule load {shlex.quote(module)}\n" if module else ""
    directives = ""
    body = f"cd work\nexec {launch}\n"
    if resume_input:
        continuation = relative_file(resume_input).as_posix()
        hours, minutes, seconds = map(int, walltime.split(":"))
        lead_time = (hours - 70) * 3600 + minutes * 60 + seconds
        directives = "#SBATCH --requeue\n#SBATCH --open-mode=append\n"
        if lead_time > 0:
            directives += f"#SBATCH --signal=B:USR1@{lead_time}\n"
        body = esen_restart.batch_body(entry, continuation, launcher, ranks, target)
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
{directives}set -eo pipefail
{modules}export OMP_NUM_THREADS=1
{body}"""


def full_node_resources(partition, walltime, catalog):
    limit = catalog.get(partition)
    if not limit or not limit.available:
        raise ValueError("The selected TRUBA queue is unavailable.")
    ranks = limit.max_cpus
    memory = limit.memory_ceiling(ranks)
    candidates(partition, ranks, memory, walltime, catalog)
    return ranks, memory


def submit_full_node(directory, record, files, settings, auth):
    try:
        catalog = fetch_catalog(RemoteContext(transport="ssh"), auth, live_node_capacity=True)
        ranks, memory = full_node_resources(settings["partition"], settings["walltime"], catalog)
        script = remote_script(files[1], ranks, memory=memory, **settings)
        changes = dict(ranks=ranks, memory_mib=memory)
        if settings.get("resume_input"):
            changes["resume_input"] = settings["resume_input"]
        save_job(directory, record, **changes)
    except Exception as error:
        save_job(directory, record, state="Failed", detail=str(error))
        return
    submit_remote(directory, record, files, script, auth)


def remote_run_directory(parent, path, auth):
    path = path.strip().rstrip("/")
    scratch = f"/arf/scratch/{auth.username}"
    if (not SAFE_NAME.fullmatch(auth.username) or auth.username in (".", "..")
            or not path.startswith(scratch + "/")):
        raise ValueError("Choose a TRUBA run folder under your scratch directory.")
    relative_file(path[len(scratch) + 1:])
    return parent / uuid5(NAMESPACE_URL, f"truba:{path}").hex, path


def attach_remote_run(directory, path, auth):
    name = " · ".join(path.split("/")[-3:])
    record = dict(name=name, target="remote", ranks=None, state="Waiting for results",
                  created=datetime.now().astimezone().isoformat(timespec="seconds"),
                  username=auth.username, remote_run=path)
    remote_files(directory, record, auth)


def attached_folder_check(remote, username):
    scratch = shlex.quote(f"/arf/scratch/{username}")
    return f"""scratch=$(realpath -- {scratch}) || exit
base=$(realpath -- {shlex.quote(remote)}) || exit
case "$base" in "$scratch"/*) ;; *) exit 1 ;; esac
test -d "$base" || exit
cd -- "$base" || exit
"""


def remote_location(directory, record, auth):
    if not auth or not SAFE_NAME.fullmatch(auth.username) or auth.username in (".", ".."):
        raise ValueError("Connect through RustMech's TRUBA button first.")
    if record.get("username", auth.username) != auth.username:
        raise ValueError("Connect using the TRUBA account that submitted this job.")
    transport = RemoteTransport(RemoteContext(transport="ssh"), auth)
    if record.get("remote_run"):
        expected, path = remote_run_directory(directory.parent, record["remote_run"], auth)
        if expected != directory:
            raise ValueError("Invalid attached TRUBA run record.")
        return transport, path
    path = f"/arf/scratch/{auth.username}/RustMech/{directory.name}"
    return transport, validated_remote_path(path, auth.username)


def tagged(reply, key):
    match = re.search(rf"^{key}=(.*)$", reply, re.MULTILINE)
    if match is None:
        raise RuntimeError(f"Missing response from Slurm/TRUBA: {key}")
    return match[1].strip()


def submit_remote(directory, record, files, script, auth):
    try:
        transport, remote = remote_location(directory, record, auth)
        snapshot = snapshot_inputs(directory, *files)
        if record.get("resume_input") and not (snapshot / relative_file(record["resume_input"])).is_file():
            raise ValueError("Include the LAMMPS restart input in the job files.")
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
    if record.get("remote_run"):
        if cancel:
            raise ValueError("Cancel externally submitted jobs in Slurm.")
        return remote_files(directory, record, auth)
    transport, remote = remote_location(directory, record, auth)
    if record.get("resume_input"):
        return refresh_restartable(directory, record, transport, remote, cancel=cancel)
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
    refresh_status(directory, record, transport, f"cd {shlex.quote(remote)} || exit\njob={job_id}\n")


def refresh_restartable(directory, record, transport, remote, *, cancel):
    name = "ESEN-" + directory.name
    if cancel:
        transport.command(esen_restart.control_script(remote, name, esen_restart.CANCEL))
    setup = esen_restart.control_script(remote, name, r'''
job=$(cat job.id 2>/dev/null || true)
ids=$(active_jobs)
if ! grep -Fxq -- "$job" <<< "$ids" && [[ -n "$ids" ]]; then
    job=$(sort -nr <<< "$ids" | sed -n '1p')
fi
[[ -z "$job" || "$job" =~ ^[0-9]+$ ]] || exit 2
printf 'JOB_ID=%s\n' "$job"
for flag in completed cancelled; do
    if [[ -e "$flag" ]]; then printf '%s=1\n' "$flag"; else printf '%s=0\n' "$flag"; fi
done
checkpoints=$(find checkpoints -type f -name 'step.*' -size +0c -print -quit 2>/dev/null || true)
printf 'HAS_CHECKPOINT=%s\n' "${checkpoints:+1}"
exec 9>&-
''')
    refresh_status(directory, record, transport, setup, managed=True)


def resume_remote(directory, record, auth):
    if not record.get("resume_input") or record.get("remote_run"):
        raise ValueError("This job has no configured LAMMPS restart input.")
    transport, remote = remote_location(directory, record, auth)
    reply = transport.command(esen_restart.control_script(remote, "ESEN-" + directory.name, esen_restart.RESUME))
    job_id = tagged(reply, "JOB_ID")
    if not re.fullmatch(r"[0-9]+", job_id):
        raise RuntimeError("Invalid Slurm job ID. Refresh to recover the submission.")
    save_job(directory, record, state="Submitted", job_id=job_id, detail="", cancel_requested=False)


def refresh_status(directory, record, transport, setup, *, managed=False):
    queue_error = "queue=''" if managed else "exit"
    reply = transport.command(setup + f"""
if [[ -n "$job" ]]; then
    queue=$(squeue -h -j "$job" -o '%T|%r') || {queue_error}
    accounting=$(sacct -n -X -j "$job" --format=State,ExitCode -P) || exit
else
    queue= accounting=
fi
printf 'QUEUE=%s\\nACCT=%s\\n' "$queue" "$accounting"
for log in {' '.join(LOGS)}; do
    printf '%s=' "$log"
    if [ -f "$log" ] && [ ! -L "$log" ]; then tail -c {LOG_BYTES} -- "$log" | base64 -w0; fi
    printf '\\n'
done
""")
    if managed:
        job_id = tagged(reply, "JOB_ID")
        if not job_id:
            save_job(directory, record, detail="Slurm has not confirmed this submission. Refresh to check again.")
            return
        save_job(directory, record, job_id=job_id, has_checkpoint=tagged(reply, "HAS_CHECKPOINT") == "1",
                 cancel_requested=tagged(reply, "cancelled") == "1")
    queue = tagged(reply, "QUEUE")
    accounting = tagged(reply, "ACCT")
    status, _, code = accounting.rstrip("|").partition("|")
    status = status.split(" ", 1)[0].rstrip("+")
    for name in LOGS:
        (directory / name).write_bytes(base64.b64decode(tagged(reply, re.escape(name)), validate=True))
    if managed and tagged(reply, "completed") == "1":
        save_job(directory, record, state="Completed", detail="")
    elif status in SLURM_TERMINAL and not queue:
        state = {"COMPLETED": "Completed" if code == "0:0" else "Failed", "CANCELLED": "Cancelled"}.get(status, "Failed")
        if managed and record.get("cancel_requested"):
            state = "Cancelled"
        elif managed and state == "Completed":
            state = "Interrupted"
        save_job(directory, record, state=state, detail=accounting.rstrip("|"))
    else:
        state = "Cancellation requested" if record.get("cancel_requested") else "Running" if queue.startswith("RUNNING|") else "Queued"
        save_job(directory, record, state=state, detail=queue or "Waiting for Slurm accounting…")


def remote_files(directory, record, auth):
    transport, remote = remote_location(directory, record, auth)
    if record.get("remote_run"):
        reply = transport.command(attached_folder_check(remote, auth.username) + f"""
set -eo pipefail
printf 'FILES='
if [ -d results ] && [ ! -L results ]; then
    find -P results -type f -print0 | sort -z | sed -z -n '1,{FILE_LIMIT + 1}p' | base64 -w0
fi
printf '\n'
""")
        names = decode(tagged(reply, "FILES")).split("\0")[:-1]
        save_file_list(directory, record, names[:FILE_LIMIT], len(names) > FILE_LIMIT,
                       state="Results available" if names else "Waiting for results")
        return
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
    save_file_list(directory, record, paths, len(names) > FILE_LIMIT)


def save_file_list(directory, record, paths, omitted, **changes):
    portable = []
    for name in paths:
        try:
            relative_file(name)
        except ValueError:
            omitted = True
        else:
            portable.append(name)
    save_job(directory, record, files=portable, files_omitted=omitted, **changes)


def result_directory(directory, record):
    source = record.get("results_source") or record.get("source")
    if record["target"] == "remote" and not record.get("remote_run") and source:
        return Path(source) / "Results" / f"ESEN-{directory.name}"
    return None


def export_remote_results(directory, record, auth):
    """Publish complete job outputs beside the original inputs in one rename."""
    destination = result_directory(directory, record)
    if destination is None or record["state"] != "Completed":
        return
    root = destination.parent
    if is_link(root) or is_link(destination):
        raise ValueError("The Results folder must not be a symbolic link or junction.")
    if destination.is_dir():
        return
    if destination.exists():
        raise ValueError(f"A file occupies the result folder path: {destination}")
    root.mkdir(exist_ok=True)
    transport, remote = remote_location(directory, record, auth)
    archive = f".esen-results-{uuid4().hex}.tar.gz"
    # Select regular files explicitly so links and solver control files are excluded.
    script = f"""cd {shlex.quote(remote)} || exit
set -eo pipefail
umask 077
{{
    find -P work -type f -print0
    for log in {' '.join(LOGS)}; do
        if [ -f "$log" ] && [ ! -L "$log" ]; then printf '%s\\0' "$log"; fi
    done
}} | tar -czf {archive} --hard-dereference --null --verbatim-files-from -T -
"""
    with TemporaryDirectory(prefix=f".esen-export-{directory.name}-", dir=root) as temporary:
        staging = Path(temporary)
        local_archive = staging / "results.tar.gz"
        try:
            transport.command(script, timeout=1800)
            transport.get(f"{remote}/{archive}", local_archive)
        finally:
            # A dropped connection should not mask the transfer error.
            with suppress(Exception):
                transport.command(f"cd {shlex.quote(remote)} && rm -f -- {archive}")
        output = staging / "output"
        output.mkdir()
        names = set()

        def validate_name(name):
            relative = relative_file(name)
            if name not in LOGS and (len(relative.parts) < 2 or relative.parts[0] != "work"):
                raise ValueError(f"Unexpected file path in TRUBA results: {name}")
            key = name.casefold()
            if key in names:
                raise ValueError(f"Conflicting file path in TRUBA results: {name}")
            names.add(key)

        safe_extract(local_archive, output, validate_name=validate_name, max_size=None)
        for folder, _, files in os.walk(output / "work", topdown=False):
            folder = Path(folder)
            for name in files:
                result = folder / name
                original = directory / "inputs" / result.relative_to(output / "work")
                if original.is_file() and cmp(original, result, shallow=False):
                    result.unlink()
            if not any(folder.iterdir()):
                folder.rmdir()
        output.rename(destination)


def download_file(directory, record, name, auth):
    if name not in record.get("files", []):
        raise ValueError("Refresh the result list before downloading this file.")
    relative = relative_file(name)
    transport, remote = remote_location(directory, record, auth)
    source = remote + "/" + name
    if record.get("remote_run") and relative.parts[0] != "results":
        raise ValueError("Choose a published result.")
    check = (attached_folder_check(remote, auth.username) if record.get("remote_run")
             else f"base=$(realpath -- {shlex.quote(remote)}) || exit\n")
    transport.command(check + f'''file="$base"/{shlex.quote(name)}
test -f "$file" && test "$(realpath -- "$file")" = "$file"''')
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
