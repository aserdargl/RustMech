"""LAMMPS input snapshots, TRUBA execution and result collection."""

import base64
from contextlib import suppress
from datetime import datetime
from filecmp import cmp
import json
import ntpath
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shlex
import shutil
import tarfile
from tempfile import TemporaryDirectory
from uuid import uuid4

import esen_restart
from truba_credentials import state_directory
from truba_jobs import SAFE_NAME, validated_remote_path, write_record
from truba_remote import RemoteContext, RemoteTransport, safe_extract
from truba_resources import BASELINES, candidates, fetch_catalog


DEFAULT_PARTITION = "barbun"
DEFAULT_MODULE = "apps/lammps/29Aug2024_stable_oneapi-2024"
MODULES = {
    DEFAULT_PARTITION: DEFAULT_MODULE,
    "hamsi": "apps/lammps/29Aug2024_update1_oneapi-2024-hamsi",
    "orfoz": "apps/lammps/29Aug2024_update1_oneapi-2024-orfoz",
}
CPU_QUEUES = tuple(name for name in BASELINES if not name.endswith("-cuda"))
LOGS = ("stdout.log", "stderr.log")
TERMINAL = frozenset({"Completed", "Failed", "Cancelled", "Interrupted"})
SLURM_TERMINAL = frozenset({
    "BOOT_FAIL", "CANCELLED", "COMPLETED", "DEADLINE", "FAILED", "NODE_FAIL",
    "OUT_OF_MEMORY", "PREEMPTED", "REVOKED", "SPECIAL_EXIT", "TIMEOUT",
})
LOG_BYTES = 16000
FILE_LIMIT = 2000
INPUT_ARCHIVE_COMPRESSION = 3
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


def is_slurm_script(name):
    return Path(name).suffix.lower() == ".slurm"


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
    save_job(directory, record)
    return directory, record


def snapshot_inputs(directory, root, entry, extra, whole_folder):
    root = root.resolve(strict=True)
    if whole_folder and directory.resolve().is_relative_to(root):
        raise ValueError("Choose a job folder outside the ESEN history directory.")
    snapshot = directory / "inputs"
    snapshot.mkdir()

    def copy(name):
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
                if is_link(source) or (folder.name.casefold() == "results"
                                       and RESULT_DIRECTORY.fullmatch(name)):
                    dirs.remove(name)
                else:
                    relative = relative_file(source.relative_to(root).as_posix())
                    (snapshot / relative).mkdir(parents=True, exist_ok=True)
            for name in files:
                source = folder / name
                if not is_link(source):
                    copy(source.relative_to(root).as_posix())
    else:
        for name in dict.fromkeys((entry, *extra) if entry else extra):
            copy(name)
    if entry and not (snapshot / relative_file(entry)).is_file():
        raise ValueError("The input script is missing from the snapshot.")
    return snapshot


def upload_snapshot(history, upload_id):
    if not re.fullmatch(r"[0-9a-f]{32}", upload_id):
        raise ValueError("Invalid ESEN folder upload.")
    return history / ".uploads" / upload_id / "inputs"


def upload_location(upload_id, username, directory=""):
    if (not SAFE_NAME.fullmatch(username) or username in (".", "..")
            or not re.fullmatch(r"[0-9a-f]{32}", upload_id)):
        raise ValueError("Invalid ESEN folder upload.")
    relative = PurePosixPath(directory)
    if relative.is_absolute() or ".." in relative.parts or "\0" in directory:
        raise ValueError("This folder is outside the RustMech project.")
    return str(PurePosixPath("/arf/scratch", username, "RustMech") / relative / "esen-folders" / upload_id)


def job_inputs(directory, record):
    if upload_id := record.get("folder_upload"):
        return upload_snapshot(directory.parent, upload_id)
    return directory / "inputs"


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
    work = "work"
    invocation = f"{launcher} -n {ranks} {target} -nonbuf -in {shlex.quote(entry)}"

    modules = f"module purge\nmodule load {shlex.quote(module)}\n" if module else ""
    directives = ""
    body = f"cd {work}\nexec {invocation}\n"
    if resume_input:
        continuation = relative_file(resume_input).as_posix()
        hours, minutes, seconds = map(int, walltime.split(":"))
        lead_time = (hours - 70) * 3600 + minutes * 60 + seconds
        directives = "#SBATCH --requeue\n#SBATCH --open-mode=append\n"
        if lead_time > 0:
            directives += f"#SBATCH --signal=B:USR1@{lead_time}\n"
        body = esen_restart.batch_body(entry, continuation, launcher, ranks, target, work)
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


def full_node_resources(partition, catalog):
    limit = catalog.get(partition)
    if not limit or not limit.available:
        raise ValueError("The selected TRUBA queue is unavailable.")
    ranks = limit.max_cpus
    memory = limit.memory_ceiling(ranks)
    hours, remainder = divmod(limit.max_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    walltime = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    candidates(partition, ranks, memory, walltime, catalog)
    return ranks, memory, walltime


def submit_full_node(directory, record, files, settings, auth):
    try:
        catalog = fetch_catalog(RemoteContext(transport="ssh"), auth, live_node_capacity=True)
        ranks, memory, walltime = full_node_resources(settings["partition"], catalog)
        script = remote_script(files[1], ranks, memory=memory, walltime=walltime, **settings)
        changes = dict(ranks=ranks, memory_mib=memory, partition=settings["partition"])
        if settings.get("resume_input"):
            changes["resume_input"] = settings["resume_input"]
        save_job(directory, record, **changes)
    except Exception as error:
        save_job(directory, record, state="Failed", detail=str(error))
        return
    submit_remote(directory, record, files, script, auth)


def remote_location(directory, record, auth):
    if not auth or not SAFE_NAME.fullmatch(auth.username) or auth.username in (".", ".."):
        raise ValueError("Connect through RustMech's TRUBA button first.")
    if record.get("username", auth.username) != auth.username:
        raise ValueError("Connect using the TRUBA account that submitted this job.")
    transport = RemoteTransport(RemoteContext(transport="ssh"), auth)
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
        batch = directory / "run.slurm"
        work = shlex.quote(remote + "/work")
        snapshot = snapshot_inputs(directory, *files)
        if record.get("resume_input") and not (snapshot / relative_file(record["resume_input"])).is_file():
            raise ValueError("Include the LAMMPS restart input in the job files.")
        batch.write_text(script, encoding="utf-8", newline="\n")
        archive = directory / "inputs.tar.gz"
        with tarfile.open(archive, "w:gz", compresslevel=INPUT_ARCHIVE_COMPRESSION) as bundle:
            bundle.add(snapshot, arcname=".")
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


def refresh_remote(directory, record, auth):
    transport, remote = remote_location(directory, record, auth)
    if record.get("resume_input"):
        return refresh_restartable(directory, record, transport, remote)
    job_id = record.get("job_id") or recover_id(transport, remote, directory)
    if not job_id:
        save_job(directory, record, detail="Slurm has not confirmed this submission. Refresh to check again.")
        return
    if not re.fullmatch(r"[0-9]+", job_id):
        raise ValueError("Invalid Slurm job ID.")
    save_job(directory, record, job_id=job_id)
    refresh_status(directory, record, transport, f"cd {shlex.quote(remote)} || exit\njob={job_id}\n")


def refresh_restartable(directory, record, transport, remote):
    name = "ESEN-" + directory.name
    setup = esen_restart.control_script(remote, name, r'''
job=$(cat job.id 2>/dev/null || true)
ids=$(active_jobs)
if ! grep -Fxq -- "$job" <<< "$ids" && [[ -n "$ids" ]]; then
    job=$(sort -nr <<< "$ids" | sed -n '1p')
fi
[[ -z "$job" || "$job" =~ ^[0-9]+$ ]] || exit 2
for flag in completed cancelled; do
    if [[ -e "$flag" ]]; then printf '%s=1\n' "$flag"; else printf '%s=0\n' "$flag"; fi
done
checkpoints=$(find checkpoints -type f -name 'step.*' -size +0c -print -quit 2>/dev/null || true)
printf 'HAS_CHECKPOINT=%s\n' "${checkpoints:+1}"
''')
    refresh_status(directory, record, transport, setup, managed=True)


def slurm_queue_seconds(value):
    match = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d{2})", value)
    if match is None:
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def script_step(output):
    steps = re.findall(r"^ESEN_STEP=[^\S\r\n]*([^\r\n]*)", output, re.MULTILINE)
    return next((step.strip()[:160] for step in reversed(steps) if step.strip()), "")


def refresh_status(directory, record, transport, setup, *, managed=False):
    status_query = r"""
if [[ -n "$job" ]]; then
    export TZ=UTC SLURM_TIME_FORMAT='%Y-%m-%dT%H:%M:%S'
    queue=$(squeue -h -j "$job" -o '%T|%M|%S|%L|%C|%D|%P|%r') || queue=''
    if ! accounting=$(sacct -n -X -j "$job" --format=State,ExitCode,ElapsedRaw,Start,AllocCPUS,AllocNodes,Partition -P); then
        [[ -n "$queue" ]] || exit 1
        accounting=''
    fi
else
    queue= accounting=
fi
"""
    continuation = esen_restart.RESUME_TIMEOUT if managed else ""
    reply = transport.command(setup + status_query + continuation + f"""
printf 'QUEUE=%s\\nACCT=%s\\n' "$queue" "$accounting"
for log in {' '.join(LOGS)}; do
    printf '%s=' "$log"
    if [ -f "$log" ] && [ ! -L "$log" ]; then tail -c {LOG_BYTES} -- "$log" | base64 -w0; fi
    printf '\\n'
done
""")
    changes = {}
    auto_resumed = False
    if managed:
        job_id = tagged(reply, "JOB_ID")
        if not job_id:
            save_job(directory, record, detail="Slurm has not confirmed this submission. Refresh to check again.")
            return
        changes.update(job_id=job_id, has_checkpoint=tagged(reply, "HAS_CHECKPOINT") == "1",
                       cancel_requested=tagged(reply, "cancelled") == "1")
        auto_resumed = tagged(reply, "AUTO_RESUMED") == "1"
        if job_id != record.get("job_id"):
            changes.update(elapsed_seconds=None, script_step="", slurm={})
    queue = tagged(reply, "QUEUE")
    accounting = tagged(reply, "ACCT")
    accounting_state, code, elapsed, start, cpus, nodes, partition, *_ = accounting.split("|") + [""] * 6
    status = accounting_state.split(" ", 1)[0].rstrip("+")
    slurm = changes.get("slurm", record.get("slurm", {})).copy()
    queue_state, queue_time, queue_start, remaining, queue_cpus, queue_nodes, queue_partition, reason, *_ = (
        queue.split("|", 7) + [""] * 7
    )
    if queue:
        fields = dict(cpus=queue_cpus, nodes=queue_nodes, partition=queue_partition)
        if queue_state not in {"PENDING", "CONFIGURING"}:
            fields.update(start=queue_start, remaining=remaining)
    else:
        fields = dict(start=start, cpus=cpus, nodes=nodes, partition=partition)
    slurm.update({field: value for field, value in fields.items() if value and value not in {"N/A", "Unknown"}})
    if slurm:
        changes["slurm"] = slurm
    if elapsed.isascii() and elapsed.isdecimal():
        changes["elapsed_seconds"] = int(elapsed)
    if queue_state == "PENDING":
        changes["elapsed_seconds"] = 0
    elif (seconds := slurm_queue_seconds(queue_time)) is not None:
        changes["elapsed_seconds"] = seconds
    for name in LOGS:
        data = base64.b64decode(tagged(reply, re.escape(name)), validate=True)
        (directory / name).write_bytes(data)
        if name == LOGS[0] and not auto_resumed and (step := script_step(data.decode("utf-8", errors="replace"))):
            changes["script_step"] = step
    cancelling = changes.get("cancel_requested", record.get("cancel_requested"))
    if managed and tagged(reply, "completed") == "1":
        changes.update(state="Completed", detail="")
    elif auto_resumed:
        changes.update(state="Submitted", detail="Slurm time limit reached. Continuation submitted.")
    elif status in SLURM_TERMINAL and not queue:
        state = {"COMPLETED": "Completed" if code == "0:0" else "Failed", "CANCELLED": "Cancelled"}.get(status, "Failed")
        if managed and cancelling:
            state = "Cancelled"
        elif managed and state == "Completed":
            state = "Interrupted"
        changes.update(state=state, detail=f"{accounting_state}|{code}")
    elif queue or record["state"] not in TERMINAL:
        state = record["state"]
        if cancelling:
            state = "Cancellation requested"
        elif queue:
            state = "Running" if queue_state in {"RUNNING", "COMPLETING"} else "Queued"
        detail = "|".join((queue_state, reason)) if queue else "Waiting for Slurm accounting…"
        changes.update(state=state, detail=detail)
    save_job(directory, record, **changes)


def remote_files(directory, record, auth):
    transport, remote = remote_location(directory, record, auth)
    outputs = remote_output_scan(record)
    reply = transport.command(f"""cd {shlex.quote(remote)} || exit
set -eo pipefail
printf 'FILES='
{outputs} | sort -z | sed -z -n '1,{FILE_LIMIT + 1}p' | base64 -w0
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


def _quoted_upload_inputs(record):
    remote = upload_location(record["folder_upload"], record["username"], record.get("upload_directory", ""))
    return shlex.quote(remote + "/inputs")


def remote_output_scan(record, *, archive=False):
    """List changed files from a Slurm work copy and all files for legacy jobs."""
    find = "find -P work -type f " + ("-print0" if archive else "-printf '%P\\0'")
    grouped = record.get("group_entries")
    if record.get("direct_upload") or not (
        record.get("folder_upload") and (grouped or is_slurm_script(record["name"]))
    ):
        return find
    source = _quoted_upload_inputs(record)
    output = "$file" if archive else "${file#work/}"
    original = "${file#work/*/}" if grouped else "${file#work/}"
    return f"""find -P work -type f -print0 | while IFS= read -r -d '' file; do
    original={source}/"{original}"
    if [ ! -f "$original" ] || ! cmp -s -- "$file" "$original"; then
        printf '%s\\0' "{output}"
    fi
done"""


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
    if not source and "truba_input_folder" in record:
        source = directory
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
    outputs = remote_output_scan(record, archive=True)
    # Select regular files explicitly so links and solver control files are excluded.
    script = f"""cd {shlex.quote(remote)} || exit
set -eo pipefail
umask 077
{{
    {outputs}
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
        inputs = job_inputs(directory, record)
        for folder, _, files in os.walk(output / "work", topdown=False):
            folder = Path(folder)
            for name in files:
                result = folder / name
                original = inputs / result.relative_to(output / "work")
                if original.is_file() and cmp(original, result, shallow=False):
                    result.unlink()
            if not any(folder.iterdir()):
                folder.rmdir()
        output.rename(destination)
