"""LAMMPS input snapshots, Windows execution and standalone TRUBA jobs."""

import base64
from contextlib import suppress
from datetime import datetime
from filecmp import cmp
from functools import partial
import hashlib
import json
import ntpath
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shlex
import shutil
import signal
import subprocess
import tarfile
from tempfile import TemporaryDirectory
from time import monotonic, time
from typing import NamedTuple
from uuid import NAMESPACE_URL, uuid4, uuid5

import esen_restart
from esen_windows_job import limited_process
from truba_credentials import state_directory
from truba_files import project_root, read_regular_file, require_regular_files, upload_tree
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
RUN_LIST = "Run.esen"
RUN_LIST_LIMIT = 64 * 1024
INPUT_ARCHIVE_COMPRESSION = 3
RESULT_DIRECTORY = re.compile(r"(?:ESEN-[0-9a-f]{32}|\.esen-export-[0-9a-f]{32}-.+)", re.IGNORECASE)


class FolderUpload(NamedTuple):
    upload_id: str
    username: str
    directory: str = ""
    direct: bool = False

    def path(self):
        if self.direct:
            return str(project_root(self.username) / self.directory)
        return upload_location(self.upload_id, self.username, self.directory) + "/inputs"


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


def is_slurm_script(name):
    return Path(name).suffix.lower() == ".slurm"


def run_list_entries(contents):
    """Read the ordered, relative Slurm paths from an ESEN run list."""
    entries = []
    seen = set()
    for line in contents.decode("utf-8-sig").splitlines():
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        path = relative_file(name).as_posix()
        if not is_slurm_script(path):
            raise ValueError(f"Expected a relative Slurm script path: {name}")
        if path.casefold() in seen:
            raise ValueError(f"Duplicate Slurm script in run list: {name}")
        seen.add(path.casefold())
        entries.append(PurePosixPath(path))
    if not entries:
        raise ValueError("The ESEN run list is empty.")
    return entries


def read_truba_run_list(auth, folder):
    contents = read_regular_file(auth, folder, RUN_LIST, limit=RUN_LIST_LIMIT)
    entries = run_list_entries(contents)
    require_regular_files(auth, folder, entries)
    return entries


def read_slurm_script(path):
    try:
        script = path.read_text(encoding="utf-8-sig")
    except UnicodeError as error:
        raise ValueError(f"Save the Slurm script as UTF-8: {path.name}") from error
    if not script.startswith("#!") or "\x00" in script:
        raise ValueError(f"Start the Slurm script with an interpreter line: {path.name}")
    return script


def scan_folder_files(root, *, include_results=False, directories=None):
    """List regular files as paths relative to the selected folder."""
    def walk_error(error):
        raise error

    root = root.resolve(strict=True)
    found = []
    for folder, dirs, files in os.walk(root, onerror=walk_error):
        folder = Path(folder)
        dirs[:] = sorted((name for name in dirs if (include_results or name.casefold() != "results")
                          and not is_link(folder / name)), key=str.casefold)
        if directories is not None:
            directories.extend((folder / name).relative_to(root).as_posix() for name in dirs)
        for name in sorted(files, key=str.casefold):
            path = folder / name
            if path.is_file() and not is_link(path):
                found.append(path.relative_to(root).as_posix())
    return found


def scan_folder_tree(root, *, include_results=False):
    directories = []
    files = scan_folder_files(root, include_results=include_results, directories=directories)
    modified = {name: file_modified_time(root / name) for name in (*directories, *files)}
    return directories, files, modified


def file_modified_time(path):
    with suppress(OSError):
        return int(path.stat().st_mtime)
    return None


def save_job(directory, record, **changes):
    updated = {**record, **changes}
    write_record(directory / "job.json", updated)
    record.update(changes)


def read_job(directory):
    return json.loads((directory / "job.json").read_text(encoding="utf-8"))


def new_job(root, entry, target, ranks, *, parent, batch_folder=None, folder_upload=None, work_subdir=None,
            truba_input_folder=None):
    directory = parent / uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    record = dict(name=entry, target=target, ranks=ranks, state="Preparing",
                  created=datetime.now().astimezone().isoformat(timespec="seconds"), source=str(root) if root else "")
    if truba_input_folder is not None:
        record["truba_input_folder"] = truba_input_folder
    if batch_folder is not None:
        record["batch_folder"] = batch_folder
    if folder_upload is not None:
        upload = FolderUpload(*folder_upload)
        subdir = relative_file(entry).parent
        if work_subdir is None:
            work_subdir = subdir.as_posix() if subdir.parts else ""
        record.update(folder_upload=upload.upload_id, username=upload.username, work_subdir=work_subdir)
        if upload.directory:
            record["upload_directory"] = upload.directory
        if upload.direct:
            record["direct_upload"] = True
    if root and root.name == "inputs" and root.parent.parent == parent:
        # Reused inputs are stored in job history, while their results belong in the original job folder.
        with suppress(OSError, ValueError):
            previous = read_job(root.parent)
            record["results_source"] = previous.get("results_source") or previous.get("source")
    save_job(directory, record)
    return directory, record


def snapshot_inputs(directory, root, entry, extra, whole_folder, stop=None, *, include_results=False):
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
                if is_link(source) or (not include_results and folder.name.casefold() == "results"
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
    return str(project_root(username) / relative / "esen-folders" / upload_id)


def _check_upload_directory(transport, username, directory, remote):
    if not directory:
        return
    root = project_root(username)
    target = shlex.quote(str(root / directory))
    suffix = shlex.quote("/" + PurePosixPath(directory).as_posix())
    store = shlex.quote(str(PurePosixPath(remote).parent))
    snapshot = shlex.quote(remote)
    transport.command(f"""set -e
root=$(realpath -e -- {shlex.quote(str(root))})
cd -P -- {target}
test "$PWD" = "$root"{suffix}
test ! -L {store}
test ! -L {snapshot}
test ! -L {snapshot}/inputs
""")


def upload_folder(history, root, auth, *, files=None, directory="", include_results=True, progress=None):
    """Copy inputs to the open TRUBA folder and retain a local submission snapshot."""
    if files is None and history.resolve().is_relative_to(root.resolve()):
        raise ValueError("Choose a job folder outside the ESEN history directory.")
    upload_id = uuid4().hex
    destination = upload_snapshot(history, upload_id).parent
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent) as temporary:
        staging = Path(temporary)
        snapshot = snapshot_inputs(staging, root, None, files or (), files is None, include_results=include_results)
        if progress is None:
            upload_tree(auth, PurePosixPath(directory), snapshot)
        else:
            upload_tree(auth, PurePosixPath(directory), snapshot, progress=progress)
        staging.rename(destination)
    return upload_id


def sync_slurm_folder(history, root, auth, selected, *, files=None, directory="", progress=None):
    """Publish one content-identified snapshot for the selected Slurm jobs."""
    if files is None and history.resolve().is_relative_to(root.resolve()):
        raise ValueError("Choose a job folder outside the ESEN history directory.")
    uploads = history / ".uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    project = f"{auth.username}\0{os.path.normcase(str(root.resolve()))}"
    if directory:
        project += f"\0{directory}"
    project_key = hashlib.sha256(project.encode()).hexdigest()
    latest = uploads / f"slurm-{project_key}.json"
    previous_id = None
    with suppress(OSError, ValueError, KeyError):
        previous_id = json.loads(latest.read_text("utf-8"))["upload_id"]
    with TemporaryDirectory(dir=uploads) as temporary:
        staging = Path(temporary)
        snapshot = snapshot_inputs(staging, root, None, files or (), files is None)
        for name in selected:
            read_slurm_script(snapshot / relative_file(name))

        manifest = {}
        for path in sorted(snapshot.rglob("*")):
            digest = None
            if path.is_file():
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
            manifest[path.relative_to(snapshot).as_posix()] = digest
        upload_id = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:32]
        destination = upload_snapshot(history, upload_id)
        remote = upload_location(upload_id, auth.username, directory)
        published = shlex.quote(remote + "/inputs")
        transport = RemoteTransport(RemoteContext(transport="ssh"), auth)
        _check_upload_directory(transport, auth.username, directory, remote)
        if transport.command(f"if test -d {published}; then printf 'READY=1\\n'; fi").strip() != "READY=1":
            previous = {}
            previous_root = ""
            if previous_id and previous_id != upload_id:
                previous_snapshot = upload_snapshot(history, previous_id)
                with suppress(OSError, ValueError):
                    previous = json.loads((previous_snapshot.parent / "manifest.json").read_text("utf-8"))
                previous_root = shlex.quote(upload_location(previous_id, auth.username, directory) + "/inputs")
                if previous and transport.command(
                    f"if test -d {previous_root}; then printf 'READY=1\\n'; fi"
                ).strip() != "READY=1":
                    previous = {}

            unchanged = [name for name, digest in manifest.items()
                         if digest is not None and previous.get(name) == digest]
            reused = set(unchanged)
            incoming = remote + "/.incoming-" + uuid4().hex
            quoted = shlex.quote(incoming)
            archive = staging / "inputs.tar.gz"
            copy_list = staging / "copy.list"
            copy_list.write_bytes(b"".join(name.encode("utf-8") + b"\0" for name in unchanged))
            with tarfile.open(archive, "w:gz", compresslevel=INPUT_ARCHIVE_COMPRESSION) as bundle:
                bundle.add(copy_list, arcname="copy.list")
                for name in manifest:
                    if name not in reused:
                        bundle.add(snapshot / relative_file(name), arcname="inputs/" + name, recursive=False)
            transport.command(f"umask 077; mkdir -p -- {quoted}/inputs")
            try:
                if progress is None:
                    transport.put(archive, incoming + "/inputs.tar.gz")
                else:
                    progress(0, archive.stat().st_size)
                    transport.put(archive, incoming + "/inputs.tar.gz", callback=progress)
                copy = ""
                if unchanged:
                    copy = f"""cd {previous_root}
while IFS= read -r -d '' name; do
    cp --reflink=auto --parents -- "$name" {quoted}/inputs
done < {quoted}/copy.list
"""
                transport.command(f"""set -e
tar -xzf {quoted}/inputs.tar.gz -C {quoted}
{copy}mv -T -- {quoted}/inputs {published} || test -d {published}
""", timeout=1800)
            finally:
                with suppress(Exception):
                    transport.command(f"rm -rf -- {quoted}")
        destination.parent.mkdir(exist_ok=True)
        if not destination.is_dir():
            try:
                snapshot.rename(destination)
            except OSError:
                if not destination.is_dir():
                    raise
        write_record(destination.parent / "manifest.json", manifest)
        write_record(latest, {"upload_id": upload_id})
    return upload_id


def job_inputs(directory, record):
    if upload_id := record.get("folder_upload"):
        return upload_snapshot(directory.parent, upload_id)
    return directory / "inputs"


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
    started = None
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
                started = monotonic()
                cancel = process.kill if memory_mib else partial(stop_process, process)
                try:
                    save_job(directory, record, state="Running", command=command, memory_mib=memory_mib,
                             started_at=time())
                    while process.poll() is None:
                        if stop.wait(0.25):
                            cancel()
                            process.wait()
                            save_job(directory, record, state="Cancelled",
                                     elapsed_seconds=int(monotonic() - started))
                            return
                finally:
                    if process.poll() is None:
                        cancel()
                        process.wait()
        save_job(directory, record, state="Completed" if process.returncode == 0 else "Failed",
                 detail=f"Exit code: {process.returncode}", elapsed_seconds=int(monotonic() - started))
    except InterruptedError:
        save_job(directory, record, state="Cancelled")
    except Exception as error:
        timing = {} if started is None else {"elapsed_seconds": int(monotonic() - started)}
        save_job(directory, record, state="Failed", detail=str(error), **timing)


def remote_script(entry, ranks, partition, account, memory, walltime, module, executable, launcher,
                  *, resume_input="", work_subdir=""):
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
    subdir = relative_file(work_subdir) if work_subdir else Path()
    work = (Path("work") / subdir).as_posix()
    launch = f"{launcher} -n {ranks} {target} -nonbuf -in {shlex.quote(entry)}"
    modules = f"module purge\nmodule load {shlex.quote(module)}\n" if module else ""
    directives = ""
    body = f"cd {shlex.quote(work)}\nexec {launch}\n"
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
        if record.get("folder_upload") and is_slurm_script(record["name"]):
            script = read_slurm_script(job_inputs(directory, record) / relative_file(record["name"]))
            submit_remote(directory, record, files, script, auth)
            return
        if "truba_input_folder" in record and is_slurm_script(record["name"]):
            submit_remote(directory, record, files, None, auth)
            return
        catalog = fetch_catalog(RemoteContext(transport="ssh"), auth, live_node_capacity=True)
        ranks, memory, walltime = full_node_resources(settings["partition"], catalog)
        script = remote_script(files[1], ranks, memory=memory, walltime=walltime, **settings,
                               work_subdir=record.get("work_subdir", ""))
        changes = dict(ranks=ranks, memory_mib=memory, partition=settings["partition"])
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


def stage_truba_inputs(transport, remote, username, relative, entry, resume_input):
    """Copy the selected TRUBA folder into this job's isolated work directory."""
    folder = PurePosixPath(relative)
    if folder.is_absolute() or ".." in folder.parts:
        raise ValueError("This folder is outside the RustMech project.")
    if folder.parts:
        relative_file(folder.as_posix())
    root = shlex.quote(str(project_root(username)))
    suffix = shlex.quote("/" + folder.as_posix()) if folder.parts else "''"
    target = shlex.quote(remote + "/work")
    checks = []
    for name in filter(None, (entry, resume_input)):
        path = shlex.quote(relative_file(name).as_posix())
        checks.extend((f"test -f {path}", f"test ! -L {path}"))
    file_checks = "\n".join(checks)
    job_dirs = "" if folder.parts else " -o -regex '\\./[a-f0-9]{32}'"
    transport.command(f"""set -eo pipefail
umask 077
root=$(realpath -e -- {root})
cd -P -- "$root"{suffix}
test "$PWD" = "$root"{suffix}
{file_checks}
mkdir -p -- {target}
find -P . -regextype posix-extended -mindepth 1 \\
    \\( -type d \\( -name esen-folders{job_dirs} \\) -prune \\) -o \\
    \\( -type f ! -name '.rustmech-*.part' -o -type d \\) -print0 |
    tar --null --no-recursion -T - -cf - | tar -xf - -C {target}
cd -- {target}
{file_checks}
""", timeout=1800)


def submit_remote(directory, record, files, script, auth):
    try:
        transport, remote = remote_location(directory, record, auth)
        upload_id = record.get("folder_upload")
        truba_input = record.get("truba_input_folder")
        batch = directory / "run.slurm"
        work = shlex.quote(remote + "/work")
        work_subdir = record.get("work_subdir", "")
        if truba_input is not None:
            stage_truba_inputs(transport, remote, auth.username, truba_input, files[1], record.get("resume_input"))
            if script is None:
                transport.get(remote + "/work/" + relative_file(files[1]).as_posix(), batch)
                script = read_slurm_script(batch)
        else:
            snapshot = job_inputs(directory, record) if upload_id else snapshot_inputs(directory, *files)
            input_dir = snapshot / relative_file(work_subdir) if work_subdir else snapshot
            if not (input_dir / relative_file(files[1])).is_file():
                raise ValueError("The selected input script is missing.")
            if record.get("resume_input") and not (input_dir / relative_file(record["resume_input"])).is_file():
                raise ValueError("Include the LAMMPS restart input in the job files.")
        batch.write_text(script, encoding="utf-8", newline="\n")
        if truba_input is not None:
            transport.put(batch, remote + "/run.slurm")
        elif upload_id and not record.get("direct_upload"):
            source = _quoted_upload_inputs(record)
            transport.command(f"umask 077; mkdir -p -- {shlex.quote(remote)} && "
                              f"cp -a --reflink=auto -- {source} {work}", timeout=1800)
            transport.put(batch, remote + "/run.slurm")
        else:
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
        options = ""
        if (upload_id or truba_input is not None) and is_slurm_script(record["name"]):
            working = (remote + "/work/" + record.get("work_subdir", "")).rstrip("/")
            options = (f"--chdir={shlex.quote(working)} "
                       f"--output={shlex.quote(remote + '/stdout.log')} "
                       f"--error={shlex.quote(remote + '/stderr.log')} ")
            # sbatch sets SLURM_SUBMIT_DIR from the caller's cwd.
            batch_path = shlex.quote(remote + "/run.slurm")
            submit_from = f"cd {shlex.quote(working)} && "
        else:
            batch_path = "run.slurm"
            submit_from = ""
        rejection = shlex.quote(remote + "/submit.err") if submit_from else "submit.err"
        reply = transport.command(f"""cd {shlex.quote(remote)} && {{
if job=$({submit_from}sbatch --parsable --job-name={name} {options}{batch_path} 2>{rejection}); then
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
for flag in completed cancelled; do
    if [[ -e "$flag" ]]; then printf '%s=1\n' "$flag"; else printf '%s=0\n' "$flag"; fi
done
checkpoints=$(find checkpoints -type f -name 'step.*' -size +0c -print -quit 2>/dev/null || true)
printf 'HAS_CHECKPOINT=%s\n' "${checkpoints:+1}"
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
    save_job(directory, record, state="Submitted", job_id=job_id, detail="", cancel_requested=False,
             elapsed_seconds=None, script_step="", slurm={})


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
    if record.get("direct_upload") or not (record.get("folder_upload") and is_slurm_script(record["name"])):
        return find
    source = _quoted_upload_inputs(record)
    output = "$file" if archive else "${file#work/}"
    return f"""find -P work -type f -print0 | while IFS= read -r -d '' file; do
    original={source}/"${{file#work/}}"
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
