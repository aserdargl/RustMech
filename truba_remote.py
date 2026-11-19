"""Explicit, fail-closed TRUBA execution for complete RustMech operations.

Only settings and versioned source are uploaded. Meshes and numerical work are
created by SLURM workers; downloaded VTK/CSV data is rendered locally.
"""
from dataclasses import asdict, dataclass, replace
import base64
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import tarfile
import time
import uuid

from truba_jobs import (HOST, SAFE_NAME, SLURM_TERMINAL_STATES, SSHJobs, WinSCPJobs, connection, job_record_lock,
                        validated_remote_path, write_record, cancel_remote)
from truba_credentials import state_directory


@dataclass(frozen=True)
class RemoteContext:
    runtime_python: str = "~/.local/share/RustMech/remote-v1/bin/python"
    python_module: str = "comp/python/3.12.0"
    foam_module: str = "apps/openfoam/v2312-2025"
    partition: str = "debug"
    account: str = ""
    walltime: str = "00:30:00"
    memory_mb: int = 4096
    cores: int = 1
    gpus: int = 0
    transport: str = "auto"
    winscp_site: str = ""
    session_id: str = ""
    parallel_jobs: int = 1
    auto_renew: bool = True
    study_name: str = ""

    def validate(self):
        if type(self.parallel_jobs) is not int or not 1 <= self.parallel_jobs <= 224:
            raise ValueError("Use 1..224 parallel analyses.")
        if type(self.auto_renew) is not bool:
            raise ValueError("Invalid automatic resource renewal setting.")
        if self.session_id and not re.fullmatch(r"[0-9]+", self.session_id):
            raise ValueError("Invalid resource session ID.")
        from truba_resources import BASELINES
        if self.partition != 'auto' and not all(name in BASELINES for name in self.partition.split(',')):
            raise ValueError('Select a supported CPU queue or Auto.')
        for name in ("account",):
            value = getattr(self,name)
            if value and not SAFE_NAME.fullmatch(value): raise ValueError(f"Invalid TRUBA {name}.")
        for name in ("python_module","foam_module"):
            value = getattr(self,name)
            if value and not re.fullmatch(r"[A-Za-z0-9_./+\-]+",value): raise ValueError(f"Invalid {name}.")
        if not re.fullmatch(r"(?:~/|/)[A-Za-z0-9_./+\-]+/bin/python[0-9.]*",self.runtime_python):
            raise ValueError("Runtime Python must be an absolute or ~/ path inside a dedicated environment (…/bin/python).")
        if "/../" in self.runtime_python or "/./" in self.runtime_python:
            raise ValueError("Runtime path must not contain traversal components.")
        if self.runtime_python.rsplit("/bin/",1)[0] in ("/usr","/usr/local",""):
            raise ValueError("Use a dedicated per-user runtime, not system Python.")
        if not re.fullmatch(r"\d{1,3}:\d{2}:\d{2}",self.walltime): raise ValueError("Use HH:MM:SS for TRUBA wall time.")
        h,m,s = map(int,self.walltime.split(":"))
        if m>=60 or s>=60 or not h+m+s: raise ValueError("Invalid TRUBA wall time.")
        if type(self.cores) is not int or not 1<=self.cores<=224: raise ValueError("Use 1..224 remote CPU cores.")
        if type(self.memory_mb) is not int or not 1<=self.memory_mb<=4194304: raise ValueError("Invalid remote memory limit.")
        if type(self.gpus) is not int or not 0<=self.gpus<=8: raise ValueError('Invalid remote GPU count.')
        if self.transport not in ("auto","ssh","winscp"): raise ValueError("Invalid remote transport.")

    @property
    def truba_transport(self): return self.transport
    @property
    def truba_winscp_site(self): return self.winscp_site


def _source_root():
    import sys
    if getattr(sys,"frozen",False): return Path(sys._MEIPASS)/"remote_sources"
    return Path(__file__).resolve().parent


def source_files(root):
    files = list(root.glob("*.py"))
    for directory,patterns in (("assets/ui",("*",)),("assets/help",("*",)),
                               ("tools/truba",("*.py","*.sh")),("native/mesh_kernel",("Cargo.toml","Cargo.lock")),
                               ("native/mesh_kernel/src",("*.rs",))):
        for pattern in patterns:
            files.extend(p for p in (root/directory).rglob(pattern) if p.is_file() and "__pycache__" not in p.parts)
    return sorted(set(files))


def make_source_bundle(destination, root=None):
    root = Path(root) if root else _source_root()
    files = source_files(root)
    if not (root/"remote_worker.py").is_file():
        raise RuntimeError("Remote worker source is absent from this installation; rebuild the executable with remote sources included.")
    native = hashlib.sha256()
    contents = [(path.relative_to(root).as_posix(),path.read_bytes()) for path in files]
    for name,data in contents:
        if name.startswith("native/mesh_kernel/"):
            native.update(name.encode())
            native.update(data)
    with open(destination,"wb") as stream,gzip.GzipFile(filename="",mode="wb",fileobj=stream,mtime=0) as compressed:
        with tarfile.open(fileobj=compressed,mode="w") as archive:
            for name,data in contents:
                member = tarfile.TarInfo(name)
                member.size,member.mode = len(data),0o644
                archive.addfile(member,io.BytesIO(data))
    with open(destination,"rb") as stream: digest = hashlib.file_digest(stream,"sha256").hexdigest()
    return digest,native.hexdigest()


def safe_extract(archive_path,directory,*,validate_name=None,max_size=40*1024**3):
    directory = Path(directory).resolve()
    with tarfile.open(archive_path,"r:gz") as archive:
        members = archive.getmembers()
        total = 0
        for item in members:
            path = PurePosixPath(item.name)
            if path.is_absolute() or ".." in path.parts or "\\" in item.name or ":" in item.name or item.issym() or item.islnk() or not (item.isfile() or item.isdir()):
                raise ValueError("Invalid path or link in the remote result archive.")
            if validate_name is not None:
                validate_name(item.name)
            if not (directory/item.name).resolve().is_relative_to(directory): raise ValueError("Remote archive escaped its destination.")
            total += item.size
        if max_size is not None and total>max_size:
            raise ValueError(f"Remote result archive exceeds the {max_size/(1024**3):g} GiB extraction limit.")
        archive.extractall(directory,members=members,filter="data")


class RemoteTransport:
    def __init__(self,context,auth=None,record=None):
        self.delegate = connection(context,auth,record)
        self.username = self.delegate.username

    def command(self,script,*,timeout=60):
        if isinstance(self.delegate,SSHJobs):
            with self.delegate.connect() as client:
                _,out,err = client.exec_command("env TERM=dumb bash -lc "+shlex.quote(script),timeout=timeout)
                data = out.read().decode(errors="replace")
                error = err.read().decode(errors="replace")
                if out.channel.recv_exit_status(): raise RuntimeError("TRUBA command failed: "+error[-1500:])
                return data
        lines = []
        # WinSCP's batch parser is line-oriented; never place a multi-line bash
        # program directly in its command file.
        encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
        command = "printf %s "+shlex.quote(encoded)+" | base64 -d | bash -l"
        self.delegate.execute(["call env TERM=dumb bash -lc "+shlex.quote(command)],on_line=lines.append)
        return "\n".join(lines)

    def put(self,local,remote):
        if isinstance(self.delegate,SSHJobs):
            with self.delegate.connect() as client,client.open_sftp() as sftp: sftp.put(str(local),remote)
        else: self.delegate.execute(["put "+self.delegate.quote(local)+" "+self.delegate.quote(remote)])

    def get(self,remote,local):
        if isinstance(self.delegate,SSHJobs):
            with self.delegate.connect() as client,client.open_sftp() as sftp: sftp.get(remote,str(local))
        else: self.delegate.execute(["get "+self.delegate.quote(remote)+" "+self.delegate.quote(local)])


def _shell_path(path):
    return '"$HOME"/'+shlex.quote(path[2:]) if path.startswith("~/") else shlex.quote(path)


def slurm_script(context,username,request):
    context.validate()
    if context.gpus or context.partition.endswith('-cuda'):
        raise ValueError('The current RustMech workers require a CPU queue; GPU execution is not supported.')
    from truba_resources import candidates
    partition = candidates(context.partition, context.cores, context.memory_mb, context.walltime)
    account = context.account or username
    if not SAFE_NAME.fullmatch(account): raise ValueError("Invalid TRUBA account.")
    runtime = _shell_path(context.runtime_python)
    prepare = request["operation"]=="prepare"
    prepare_damask = prepare and request.get('prepare_damask') is True
    python_load = "module load "+shlex.quote(context.python_module) if context.python_module else ":"
    needs_foam = request['operation'] in ('probe', 'prepare', 'flow_export', 'flow_solve', 'thermal_export', 'thermal_solve')
    foam_load = "module load "+shlex.quote(context.foam_module)+" >/dev/null 2>&1 || true" if context.foam_module and needs_foam else ":"
    step_cores, _ = request_resources(context, request['operation'])
    job_name = "RustMech-remote"
    if request.get("background"):
        job_name = analysis_code(request)
    return f'''#!/bin/bash -l
#SBATCH --job-name={job_name}
#SBATCH --partition={partition}
#SBATCH --account={account}
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task={context.cores}
#SBATCH --mem={context.memory_mb}M
#SBATCH --time={context.walltime}
#SBATCH --output=slurm.out
#SBATCH --error=slurm.err
set -euo pipefail
rustmech_stage() {{ printf '%s\n' "$1" > progress.txt; printf '%s\n' "$1"; }}
rustmech_stage 'Preparing request sources'
export TERM=dumb
export QT_API=pyside6 QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg
export OMP_NUM_THREADS={step_cores} RUSTMECH_THREADS={step_cores} OPENBLAS_NUM_THREADS=1
mkdir -p output engine
trap 'tar -czf result.tar.gz output' EXIT
echo "{request['source_sha256']}  engine.tar.gz" | sha256sum -c -
tar -xzf engine.tar.gz -C engine
rustmech_stage 'Loading runtime modules'
set +u
{python_load}
{foam_load}
set -u
runtime_python={runtime}
export RUSTMECH_CORE_LIBRARY="$(dirname "$(dirname "$runtime_python")")/native/librustmech_core.so"
test ! -e cancel.request
'''+('bash engine/tools/truba/prepare-remote.sh "$runtime_python" "'+request["native_sha256"]+'"\n' if prepare else '')+('''
if ! bash engine/tools/truba/prepare-damask.sh "$runtime_python"; then
    printf '%s\\n' 'DAMASK setup did not complete. See the runtime capability report and Slurm logs.' >&2
fi
''' if prepare_damask else '')+f'''
if [ ! -x "$runtime_python" ]; then
    python3 engine/remote_worker.py request.json --missing-runtime
else
    rustmech_stage 'Starting Python worker'
    "$runtime_python" engine/remote_worker.py request.json
fi
'''


def _write_paths(paths,record):
    for path in paths: write_record(path,record)


def request_resources(context, operation):
    slots = min(context.parallel_jobs, context.cores, context.memory_mb) if operation in ('doe_mesh', 'doe_solve') else 1
    return max(1, context.cores // slots), max(1, context.memory_mb // slots)


def analysis_code(record):
    request_id = str(record.get("request_id", ""))
    return "RM-" + request_id[:12].upper() if re.fullmatch(r"[a-f0-9]{32}", request_id) else ""


def _rebase_paths(directory,result):
    """Relocate the known reporting paths; never reuse a remote absolute path locally."""
    remote_output = result.get("remote_output","")
    def transform(value):
        if isinstance(value,dict): return {key:transform(v) for key,v in value.items()}
        if isinstance(value,list): return [transform(v) for v in value]
        if isinstance(value,str) and remote_output and (value==remote_output or value.startswith(remote_output+"/")):
            return str(Path(directory)/"output"/value[len(remote_output):].lstrip("/"))
        return value
    result = transform(result)
    for path in (Path(directory)/"output").rglob("summary.json"):
        write_record(path,transform(json.loads(path.read_text())))
    return result


def run_remote_job(request,context,auth=None,marker=None,*,directory=None,resume_record=None,on_progress=None,cancelled=None,
                   submit_only=False, poll_once=False, detach_requested=None):
    from remote_operations import required_capability
    study_name = context.study_name
    if not resume_record:
        required_capability(request)
    context.validate()
    if context.session_id and not resume_record and request.get('operation') in ('doe_mesh', 'doe_solve'):
        from truba_handover import context_for_request
        context = context_for_request(context, auth, cancelled=cancelled, marker=marker, on_progress=on_progress)
    transport = RemoteTransport(context,auth,resume_record)
    if resume_record:
        record = dict(resume_record)
        if record.get("kind")!="rustmech_remote" or record.get("host")!=HOST or record.get("username")!=transport.username:
            raise ValueError("Remote job record does not belong to this TRUBA connection.")
        directory = Path(record["local_directory"])
    else:
        from model import ROOT
        directory = Path(directory) if directory else ROOT/"results"/("TRUBA_"+uuid.uuid4().hex)
        directory.mkdir(parents=True,exist_ok=False)
        source_hash,native_hash = make_source_bundle(directory/"engine.tar.gz")
        geometry_path = request.get('settings', {}).get('imported_geometry_path')
        if geometry_path:
            import base64
            request = {**request, 'imported_geometry_vtp': base64.b64encode(Path(geometry_path).read_bytes()).decode('ascii')}
        step_cores, step_memory = request_resources(context, request['operation'])
        execution_context = dict(asdict(context), cores=step_cores, memory_mb=step_memory)
        request = {**request,"schema_version":1,"request_id":uuid.uuid4().hex,"source_sha256":source_hash,"native_sha256":native_hash,"remote_context":execution_context}
        if submit_only:
            request["background"] = True
        write_record(directory/"request.json",request)
        record = dict(schema_version=1,kind="rustmech_remote",host=HOST,username=transport.username,
                      transport="winscp" if isinstance(transport.delegate,WinSCPJobs) else "ssh",
                      remote_directory=f"/arf/scratch/{transport.username}/RustMech/{uuid.uuid4().hex}",
                      local_directory=str(directory.resolve()),state="preparing",context=asdict(context),
                      operation=request["operation"],request_id=request["request_id"],source_sha256=source_hash)
        if study_name and request["operation"] not in ("probe", "prepare", "preview", "surface"):
            record["study_name"] = study_name
        if submit_only:
            record["background"] = True
            settings = request.get("settings", {})
            record["geometry_name"] = settings.get("imported_geometry_name") or settings.get("structure_name", "")
        if isinstance(transport.delegate,WinSCPJobs): record["winscp_site"] = transport.delegate.site
    remote = validated_remote_path(record["remote_directory"],transport.username)
    registry = state_directory()/"jobs"/(PurePosixPath(remote).name+".json")
    paths = [directory/"remote_job.json",registry]+([Path(marker)] if marker else [])
    if not poll_once:
        _write_paths(paths,record)
    if context.session_id and not record.get("session"):
        from truba_session import saved_session, validate_session, refresh_session
        session = saved_session()
        if not session or session.get("job_id") != context.session_id:
            raise RuntimeError("The selected resource session is no longer saved. Refresh the session.")
        validate_session(session, transport)
        session = refresh_session(session, auth)
        if not session.get("ready"):
            raise RuntimeError("Resource session is not ready or has expired. No new allocation was submitted.")
        for key in ("runtime_python", "python_module", "foam_module", "partition", "account", "cores", "memory_mb"):
            if getattr(context, key) != session["context"][key]:
                raise RuntimeError("Configuration differs from the resource session. Release it before changing resources.")
        record["session"] = session
        _write_paths(paths,record)
    def is_cancelled():
        saved = json.loads(paths[0].read_text()) if paths[0].exists() else {}
        return (saved.get('state') == 'cancelled'
                or (cancelled is not None and cancelled.is_set())
                or (marker is not None and Path(marker).with_suffix(".cancel").exists()))

    def is_detaching():
        return ((detach_requested is not None and detach_requested.is_set())
                or (marker is not None and Path(marker).with_suffix('.detach').exists()))

    def update(stage):
        with job_record_lock:
            saved = json.loads(paths[0].read_text()) if paths[0].exists() else {}
            if saved.get('state') == 'cancelled':
                from concurrent.futures import CancelledError
                raise CancelledError
            record["state"] = stage
            _write_paths(paths,record)
        if on_progress: on_progress("TRUBA · "+stage)
    try:
        if not poll_once and (not resume_record or not record.get("job_id")):
            if resume_record: request = json.loads((directory/"request.json").read_text())
            (directory/"run.slurm").write_text(slurm_script(context,transport.username,request),encoding="utf-8",newline="\n")
            transport.command("mkdir -p -- "+shlex.quote(remote))
            for name in ("engine.tar.gz","request.json","run.slurm"): transport.put(directory/name,remote+"/"+name)
        if is_cancelled():
            if poll_once:
                return None
            cancel_remote(paths[0],auth)
            from concurrent.futures import CancelledError
            raise CancelledError
        script = "cd "+shlex.quote(remote)+''' && test ! -e cancel.request && {
if [ ! -s job.id ]; then
 if mkdir submission.lock 2>/dev/null; then
  job=$(sbatch --parsable run.slurm); job=${job%%;*}
  [[ "$job" =~ ^[0-9]+$ ]] || exit 2
  printf '%s\n' "$job" > job.id
 else
  for attempt in $(seq 1 30); do [ ! -s job.id ] || break; sleep 1; done
  test -s job.id || exit 3
 fi
fi
printf 'RUSTMECH_JOB_ID=%s\n' "$(cat job.id)"
}'''
        if record.get("session"):
            from truba_session import validate_session
            session_remote = validate_session(record["session"], transport)
            job_id = record["session"]["job_id"]
            operation = record.get('operation', '')
            step_cores, step_memory = request_resources(context, operation)
            payload = json.dumps(dict(directory=remote, cores=step_cores, memory_mb=step_memory))
            inbox = session_remote+"/inbox/"+PurePosixPath(remote).name+".json"
            script = ("cd "+shlex.quote(remote)+"\nset -euo pipefail\n"+
                      "test ! -e cancel.request\n"+
                      "if [ ! -s job.id ]; then\n"+
                      " printf '%s\\n' "+shlex.quote(payload)+" > "+shlex.quote(inbox+".tmp")+"\n"+
                      " mv -- "+shlex.quote(inbox+".tmp")+" "+shlex.quote(inbox)+"\n"+
                      " printf '%s\\n' "+job_id+" > job.id\nfi\n"+
                      "printf 'RUSTMECH_JOB_ID=%s\\n' \"$(cat job.id)\"\n")
        if not poll_once and (submit_only or marker is not None or detach_requested is not None):
            record["submission_attempted"] = True
            update("submitting")
            script = "trap '' HUP\n" + script
        if not poll_once or not record.get("job_id"):
            if poll_once:
                # The upload finished but the client may have lost the sbatch reply.
                # Reconnect only to an existing remote ID.
                script = "cd " + shlex.quote(remote) + """ && {
if [ -s job.id ]; then printf 'RUSTMECH_JOB_ID=%s\n' "$(cat job.id)"; fi
}"""
            reply = transport.command(script)
            match = re.search(r"^RUSTMECH_JOB_ID=([0-9]+)\s*$",reply,re.MULTILINE)
            if poll_once and not match:
                return None
            if not match: raise RuntimeError("Submission was not confirmed. Resume this job record; do not submit a duplicate.")
            if record.get("job_id") and record["job_id"]!=match.group(1): raise RuntimeError("The resumed SLURM job ID changed; refusing to attach another job.")
            record["job_id"] = match.group(1)
            update("submitted")
        if not re.fullmatch(r"[0-9]+", str(record.get("job_id", ""))):
            raise ValueError("Invalid SLURM job ID.")
        if submit_only:
            return record
        next_renewal_check = 0.0
        while True:
            if is_cancelled():
                if poll_once:
                    return None
                cancel_remote(paths[0],auth)
                from concurrent.futures import CancelledError
                raise CancelledError
            if is_detaching():
                record['detached'] = True
                update(record['state'])
                return None, directory
            if (context.auto_renew and record.get('session') and record.get('operation') in ('doe_mesh', 'doe_solve')
                    and time.monotonic() >= next_renewal_check):
                from truba_handover import prepare_if_needed
                next_renewal_check = time.monotonic() + 60
                try:
                    prepare_if_needed(record['session'], auth)
                except (OSError, RuntimeError, ValueError) as error:
                    # A renewal error must never interrupt a healthy running solver.
                    if on_progress: on_progress('TRUBA · resource renewal: ' + str(error))
            if record.get("session"):
                from truba_session import QUEUE_FUNCTION
                reply = transport.command("cd "+shlex.quote(remote)+
                    "\nset -e\n"+QUEUE_FUNCTION+
                    f"printf 'RUSTMECH_STEP='; cat step.status 2>/dev/null || true\nprintf '\\nRUSTMECH_QUEUE='; rustmech_queue {record['job_id']} '%T|%r'\nprintf '\\nRUSTMECH_PROGRESS='; head -c 500 progress.txt 2>/dev/null || true")
                step = re.search(r"^RUSTMECH_STEP=(.*)$",reply,re.MULTILINE)
                if step and step[1].startswith(("COMPLETED|","FAILED|","CANCELLED|")):
                    record["step_status"] = step[1].strip()
                    break
            else:
                from truba_session import QUEUE_FUNCTION
                reply = transport.command(QUEUE_FUNCTION +
                    f"printf 'RUSTMECH_QUEUE='; rustmech_queue {record['job_id']} '%T|%r'")
            state = re.search(r"^RUSTMECH_QUEUE=(.*)$",reply,re.MULTILINE)
            if state is None: raise RuntimeError("Could not read the SLURM queue state; resume the job record.")
            value = state.group(1).strip()
            queue_state = value.partition("|")[0].rstrip("+")
            if not value or (poll_once and queue_state in SLURM_TERMINAL_STATES):
                if record.get("session"):
                    if poll_once:
                        update('FAILED|Resource session ended before this analysis completed')
                        return None
                    raise RuntimeError("Resource session ended before this analysis completed. Results and job record remain available.")
                break
            if record.get("session"):
                value = "session · "+("running" if step and step[1]=="RUNNING" else "waiting for previous analysis")
                progress = re.search(r'^RUSTMECH_PROGRESS=(.*)$', reply, re.MULTILINE)
                if step and step[1] == 'RUNNING' and progress and progress[1].strip():
                    value += ' · ' + progress[1].strip()
            if value!=record["state"]: update(value)
            if poll_once:
                return None
            if cancelled is not None: cancelled.wait(2)
            else: time.sleep(2)
        accounting = ""
        for _ in range(0 if record.get("session") else 1 if poll_once else 15):
            reply = transport.command(f"printf 'RUSTMECH_ACCOUNTING='; sacct -n -X -j {record['job_id']} --format=State,ExitCode -P | head -n 1")
            match = re.search(r"^RUSTMECH_ACCOUNTING=(.*)$",reply,re.MULTILINE)
            accounting = match.group(1).strip().rstrip("|") if match else ""
            if accounting and not accounting.startswith(("PENDING","RUNNING","COMPLETING")): break
            if not poll_once: time.sleep(1)
        if record.get("session"): accounting = record["step_status"]
        if poll_once:
            terminal_state = accounting.partition("|")[0].split()
            if not terminal_state or terminal_state[0].rstrip("+") not in SLURM_TERMINAL_STATES:
                return None
            if accounting != "COMPLETED|0:0":
                for name in ("slurm.out", "slurm.err"):
                    try: transport.get(remote+"/"+name, directory/name)
                    except (OSError, RuntimeError): pass
                record["slurm_status"] = accounting
                update(accounting)
                return None
        record["slurm_status"] = accounting
        update("downloading")
        for name in ("slurm.out","slurm.err"):
            try: transport.get(remote+"/"+name,directory/name)
            except (OSError,RuntimeError): pass
        try: transport.get(remote+"/result.tar.gz",directory/"result.tar.gz")
        except (OSError,RuntimeError) as error:
            raise RuntimeError(f"Remote results are unavailable ({accounting or 'SLURM accounting not confirmed'}). See {directory/'slurm.out'} and the saved job record.") from error
        reply = transport.command("printf 'RUSTMECH_RESULT_SHA='; sha256sum "+shlex.quote(remote+"/result.tar.gz"))
        match = re.search(r"RUSTMECH_RESULT_SHA=([a-f0-9]{64})",reply)
        with (directory/"result.tar.gz").open("rb") as stream: actual = hashlib.file_digest(stream,"sha256").hexdigest()
        if not match or match.group(1)!=actual: raise RuntimeError("Remote result checksum verification failed; retry retrieval.")
        safe_extract(directory/"result.tar.gz",directory)
        if not (directory/"output/result.json").exists():
            raise RuntimeError(f"Remote worker did not produce a result manifest ({accounting or 'unknown state'}). See {directory/'slurm.out'} and {directory/'slurm.err'}.")
        result = json.loads((directory/"output/result.json").read_text())
        if result.get("request_id")!=record["request_id"] or result.get("source_sha256")!=record["source_sha256"] or str(result.get("slurm_job_id"))!=record["job_id"]:
            raise RuntimeError("Remote results do not match this request, source version or SLURM job.")
        if accounting!="COMPLETED|0:0": raise RuntimeError(f"SLURM did not confirm successful completion ({accounting or 'unknown'}). See {directory/'slurm.err'}")
        if result.get("error"): raise RuntimeError(result["error"])
        result = _rebase_paths(directory,result)
        update("downloaded")
        return result,directory
    except BaseException:
        saved = json.loads(paths[0].read_text()) if paths[0].exists() else {}
        if is_detaching() and record.get('submission_attempted') and not submit_only:
            record['detached'] = True
        if saved.get("state")!="cancelled": update("interrupted; resume using this job record")
        raise


def load_remote_preview(directory, *, settings=None):
    from mesh_engine import Preview,preview_key
    from model import FEMesh,settings_from_dict
    import numpy as np
    import pyvista as pv
    directory = Path(directory)
    metadata = json.loads((directory/"mesh.json").read_text())
    saved_settings = settings_from_dict(metadata["settings"])
    if settings is not None:
        from dataclasses import replace
        comparable = saved_settings
        if saved_settings.imported_geometry_path or settings.imported_geometry_path:
            if not saved_settings.imported_geometry_path or not settings.imported_geometry_path:
                raise ValueError("Preview geometry does not match the requested mechanical mesh.")
            digest = hashlib.sha256(Path(settings.imported_geometry_path).read_bytes()).hexdigest()
            if digest != metadata.get("geometry_sha256"):
                raise ValueError("Imported geometry changed or the remote preview lacks its checksum. Build the mesh on TRUBA again.")
            comparable = replace(saved_settings, imported_geometry_path=settings.imported_geometry_path)
        if preview_key(comparable) != preview_key(settings):
            raise ValueError("Preview geometry does not match the requested mechanical mesh.")
    with np.load(directory/"contacts.npz",allow_pickle=False) as contacts:
        mesh = FEMesh(grid=pv.read(directory/"mesh.vtu"),removed_grid=pv.read(directory/"removed.vtu"),
                      bottom_ids=contacts["bottom"],top_ids=contacts["top"],**metadata["mesh"])
    preview = Preview(pv.read(directory/"surface.vtp"),mesh,preview_key(settings if settings is not None else saved_settings))
    preview.remote_source = metadata["remote_source"]
    return preview


def run_remote_preview(settings,context,auth,on_progress,cancelled,*,include_mesh=True,detach_requested=None):
    if not include_mesh:
        from mesh_engine import Preview, preview_key
        grid = run_remote_surface(settings,context,auth,cancelled,detach_requested=detach_requested)
        return Preview(grid,None,preview_key(settings)) if grid is not None else None
    result,directory = run_remote_job(dict(operation="preview",settings=asdict(settings)),context,auth,on_progress=on_progress,cancelled=cancelled,detach_requested=detach_requested)
    if result is None:
        return None
    preview = load_remote_preview(directory/"output/preview",settings=settings)
    return preview


def run_remote_operation(request,context,remote_auth=None,job_marker=None):
    result,directory = run_remote_job(request,context,remote_auth,job_marker)
    return str(directory/"output") if result is not None else None
run_remote_operation.remote_job_target = True


def submit_remote_analysis(request, context, remote_auth=None, job_marker=None):
    # Interactive mechanics and FFT keep their own independent Slurm allocation.
    return run_remote_job(request, replace(context, session_id=""), remote_auth, job_marker, submit_only=True)
submit_remote_analysis.remote_job_target = True
submit_remote_analysis.remote_submit_target = True


def run_remote_mechanics(settings,context,mesh_source="",remote_auth=None,job_marker=None):
    request = dict(operation="mechanical", settings=asdict(settings), mesh_source=mesh_source)
    return submit_remote_analysis(request, context, remote_auth, job_marker)
run_remote_mechanics.remote_job_target = True
run_remote_mechanics.remote_submit_target = True


def sync_background_analysis(record, auth):
    return run_remote_job({}, RemoteContext(**record["context"]), auth, resume_record=record, poll_once=True)


def run_remote_level(settings,context,max_elements,selected,remote_auth=None,job_marker=None):
    result,_ = run_remote_job(dict(operation="convergence_level",settings=asdict(settings),max_elements=max_elements,selected=selected),context,remote_auth,job_marker)
    if result is None:
        return None
    from convergence_execution import Level
    from model import settings_from_dict
    value = result["level"]
    value["settings"] = settings_from_dict(value["settings"])
    return Level(**value)
run_remote_level.remote_job_target = True


def run_remote_doe(index,settings,context,solve,remote_auth=None,job_marker=None):
    from dataclasses import replace
    cores, _ = request_resources(context, 'doe_solve' if solve else 'doe_mesh')
    settings = replace(settings, cores=min(settings.cores, cores), automatic_cores=False)
    result,_ = run_remote_job(dict(operation="doe_solve" if solve else "doe_mesh",settings=asdict(settings),row=index),context,remote_auth,job_marker)
    return result["row"] if result is not None else None
run_remote_doe.remote_job_target = True


def resume_remote_job(record_path,auth=None,marker=None):
    record = json.loads(Path(record_path).read_text())
    return run_remote_job({},RemoteContext(**record["context"]),auth,marker,resume_record=record)


def retrieve_remote_operation(record_path,remote_auth=None,job_marker=None):
    result,directory = resume_remote_job(record_path,remote_auth,job_marker)
    if result is None:
        return None
    return dict(remote_retrieval=True,result=result,directory=str(directory))
retrieve_remote_operation.remote_job_target = True


def run_remote_surface(settings,context,auth,cancelled,on_progress=None,*,detach_requested=None):
    result,directory = run_remote_job(dict(operation="surface",settings=asdict(settings)),context,auth,cancelled=cancelled,on_progress=on_progress,detach_requested=detach_requested)
    if result is None:
        return None
    import pyvista as pv
    return pv.read(directory/"output/surface.vtp")
