"""TRUBA job transfer, SLURM monitoring and recovery without stored passwords."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from urllib.parse import unquote
import uuid

import paramiko
from truba_credentials import CredentialStore, state_directory

HOST = "172.16.6.15"
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass
class RemoteAuth:
    username: str
    password: str = field(repr=False)


def write_record(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2)+"\n",encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def winscp_executable():
    found = shutil.which("WinSCP.com")
    for root in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if root and (Path(root)/"WinSCP/WinSCP.com").is_file():
            return str(Path(root)/"WinSCP/WinSCP.com")
    return found


def saved_winscp_sites():
    if os.name != "nt":
        return []
    import winreg
    sites = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Martin Prikryl\WinSCP 2\Sessions") as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                name = winreg.EnumKey(root,i)
                with winreg.OpenKey(root,name) as key:
                    try:
                        host = winreg.QueryValueEx(key,"HostName")[0]
                        username = winreg.QueryValueEx(key,"UserName")[0]
                    except FileNotFoundError:
                        continue
                if host == HOST and SAFE_NAME.fullmatch(username):
                    sites.append((unquote(name),username))
    except FileNotFoundError:
        pass
    return sites


def get_auth(saved=True):
    record = CredentialStore().load() if saved else None
    return RemoteAuth(*record) if record else None


def shell_job_settings(settings, account):
    if type(settings.cores) is not int or not 1 <= settings.cores <= 1024:
        raise ValueError("TRUBA CPU count must be an integer between 1 and 1024.")
    for value in (settings.truba_partition, account):
        if not SAFE_NAME.fullmatch(value):
            raise ValueError("TRUBA partition and account must contain letters, digits, '.', '_' or '-'.")
    if not re.fullmatch(r"\d{1,3}:\d{2}:\d{2}",settings.truba_walltime):
        raise ValueError("TRUBA wall time must use HH:MM:SS.")
    hours,minutes,seconds=map(int,settings.truba_walltime.split(':'))
    if minutes>=60 or seconds>=60 or hours+minutes+seconds == 0:
        raise ValueError("TRUBA wall time needs valid hours, minutes and seconds.")
    if not 1 <= settings.truba_memory_mb <= 4194304:
        raise ValueError("TRUBA memory must be between 1 and 4194304 MiB.")
    return f"""#!/bin/bash -l
#SBATCH --job-name=RustMech
#SBATCH --partition={settings.truba_partition}
#SBATCH --account={account}
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task={settings.cores}
#SBATCH --mem={settings.truba_memory_mb}M
#SBATCH --time={settings.truba_walltime}
#SBATCH --output=slurm.out
#SBATCH --error=slurm.err
set -euo pipefail
module load comp/python/3.12.0
export OMP_NUM_THREADS="$SLURM_CPUS_PER_TASK"
export OPENBLAS_NUM_THREADS=1
"$HOME/.local/share/RustMech/kratos-10.4.4/bin/python" kratos_driver.py
"""


MONITOR = """#!/bin/bash -l
set -euo pipefail
[ ! -e cancel.request ] || exit 130
if [ ! -s job.id ]; then
    if mkdir submission.lock 2>/dev/null; then
        job=$(sbatch --parsable --job-name="RM-${PWD##*/}" run.slurm)
        job=${job%%;*}
        [[ "$job" =~ ^[0-9]+$ ]] || exit 2
        printf '%s\\n' "$job" > job.id
    else
        for attempt in $(seq 1 30); do
            [ ! -s job.id ] || break
            sleep 1
        done
        [ -s job.id ] || { printf 'Submission status unknown; refusing to submit a duplicate job.\\n' >&2; exit 2; }
    fi
fi
job=$(cat job.id)
[[ "$job" =~ ^[0-9]+$ ]] || exit 2
if [ -e cancel.request ]; then scancel "$job"; exit 130; fi
printf 'RUSTMECH_JOB_ID=%s\\n' "$job"
previous=''
while true; do
    state=$(squeue -h -j "$job" -o '%T|%r')
    if [ -z "$state" ]; then break; fi
    if [ "$state" != "$previous" ]; then printf 'RUSTMECH_STATE=%s\\n' "$state"; previous="$state"; fi
    sleep 5
done
mkdir -p results
accounting=''
for attempt in $(seq 1 15); do
    accounting=$(sacct -n -X -j "$job" --format=State,ExitCode -P | head -n 1)
    case "$accounting" in ''|PENDING*|RUNNING*|COMPLETING*) sleep 1;; *) break;; esac
done
printf '%s\\n' "$accounting" > results/slurm_status.txt
if [ ! -f results/manifest.json ]; then
    mkdir -p results
    printf '{"schema_version":1,"complete":false,"steps":[],"error":"SLURM job ended before producing results; see slurm.out and slurm.err"}\\n' > results/manifest.json
fi
"""


class WinSCPJobs:
    def __init__(self, site=""):
        executable = winscp_executable()
        if not executable:
            raise RuntimeError("WinSCP is not installed. Connect through the RustMech TRUBA panel instead.")
        sites = saved_winscp_sites()
        selected = [item for item in sites if item[0] == site] if site else sites
        if len(selected) != 1:
            raise RuntimeError("Select one saved TRUBA WinSCP site in the Kratos panel.")
        self.site,self.username = selected[0]
        self.executable = executable

    def execute(self, lines, on_line=None, cancel_path=None):
        def quoted(value):
            if any(c in str(value) for c in ('\r','\n')):
                raise ValueError("Invalid newline in WinSCP argument")
            return '"'+str(value).replace('"','""')+'"'
        with tempfile.TemporaryDirectory(prefix="rustmech-winscp-") as temporary:
            script = Path(temporary)/"commands.txt"
            script.write_text("option batch abort\nopen "+quoted(self.site)+" -timeout=30\n"+"\n".join(lines)+"\nexit\n",encoding="utf-8-sig")
            process = subprocess.Popen([self.executable,"/nointeractiveinput",f"/script={script}"],
                                       stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding="utf-8",errors="replace",
                                       creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            tail = []
            try:
                for line in process.stdout:
                    line=line.rstrip()
                    tail.append(line)
                    tail=tail[-12:]
                    if on_line:
                        on_line(line)
                code=process.wait()
                if code:
                    # WinSCP masks saved passwords; never log the open command or URL.
                    safe=[line for line in tail if 'sftp://' not in line and not line.startswith('open ')]
                    raise RuntimeError("TRUBA transfer/SSH operation failed. "+"\n".join(safe))
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)

    @staticmethod
    def quote(value):
        if any(c in str(value) for c in ('\r','\n','"')):
            raise ValueError("Invalid WinSCP path")
        return '"'+str(value)+'"'

    def run(self, local, remote, record, record_paths, resume=False):
        local=Path(local)
        results=local/"results"
        results.mkdir(exist_ok=True)
        def received(line):
            if line.startswith("RUSTMECH_JOB_ID="):
                value=line.split("=",1)[1]
                if not value.isdigit():
                    raise RuntimeError("Invalid SLURM job ID")
                record["job_id"]=value
            elif line.startswith("RUSTMECH_STATE="):
                record["state"]=line.split("=",1)[1]
            else:
                return
            for path in record_paths:
                write_record(path,record)
        lines=[]
        if not resume or not record.get("job_id"):
            lines.append("call mkdir -p -- "+shlex.quote(remote+"/staging"))
            for name in ("mesh_input.npz","model.mdpa","input.json","ProjectParameters.json","materials.json","kratos_driver.py","kratos_self_contact.py","run.slurm","monitor.sh"):
                if name=='kratos_self_contact.py' and not (local/name).exists():continue
                lines.append("put "+self.quote(local/name)+" "+self.quote(remote+"/staging/"+name))
        lines += ["call bash -lc "+shlex.quote("cd "+shlex.quote(remote)+" && { if [ ! -s job.id ] && [ ! -d submission.lock ]; then cp staging/* .; fi; bash monitor.sh; }"),
                  "get "+self.quote(remote+"/results/*")+" "+self.quote(str(results)+os.sep),
                  "get -filemask=slurm.* "+self.quote(remote+"/*")+" "+self.quote(str(local)+os.sep)]
        self.execute(lines,received)

    def cancel(self, record):
        command = cancellation_script(record, self.username)
        self.execute(["call bash -lc "+shlex.quote(command)])


class SSHJobs:
    def __init__(self,auth):
        if not SAFE_NAME.fullmatch(auth.username):
            raise ValueError("Invalid TRUBA username")
        self.auth=auth
        self.username=auth.username

    def connect(self):
        from truba_ssh import ssh_connection
        return ssh_connection(self.auth)

    def run(self,local,remote,record,record_paths,resume=False):
        local=Path(local)
        with self.connect() as client:
            with client.open_sftp() as sftp:
                if not resume or not record.get("job_id"):
                    _,out,err=client.exec_command("mkdir -p -- "+shlex.quote(remote+"/staging"))
                    if out.channel.recv_exit_status():
                        raise RuntimeError("Could not create the TRUBA job directory")
                    for name in ("mesh_input.npz","model.mdpa","input.json","ProjectParameters.json","materials.json","kratos_driver.py","kratos_self_contact.py","run.slurm","monitor.sh"):
                        if name=='kratos_self_contact.py' and not (local/name).exists():continue
                        sftp.put(str(local/name),remote+"/staging/"+name)
                _,out,err=client.exec_command("bash -lc "+shlex.quote("cd "+shlex.quote(remote)+" && { if [ ! -s job.id ] && [ ! -d submission.lock ]; then cp staging/* .; fi; bash monitor.sh; }"))
                for line in out:
                    if line.startswith("RUSTMECH_JOB_ID="):
                        value=line.strip().split("=",1)[1]
                        if not value.isdigit():
                            raise RuntimeError("Invalid SLURM job ID")
                        record["job_id"]=value
                    elif line.startswith("RUSTMECH_STATE="):
                        record["state"]=line.strip().split("=",1)[1]
                    else:
                        continue
                    for path in record_paths:
                        write_record(path,record)
                if out.channel.recv_exit_status():
                    raise RuntimeError("TRUBA job submission or monitoring failed; the job record can be resumed.")
                results=local/"results"
                results.mkdir(exist_ok=True)
                for name in sftp.listdir(remote+"/results"):
                    if re.fullmatch(r"step_\d{6}\.npz|manifest\.json|solver_error\.txt|slurm_status\.txt",name):
                        sftp.get(remote+"/results/"+name,str(results/name))
                for name in ("slurm.out","slurm.err"):
                    try:sftp.get(remote+"/"+name,str(local/name))
                    except FileNotFoundError:pass

    def cancel(self,record):
        with self.connect() as client:
            command = cancellation_script(record, self.username)
            _,out,err=client.exec_command("bash -lc "+shlex.quote(command))
            if out.channel.recv_exit_status():
                raise RuntimeError("TRUBA cancellation failed; reconnect and retry using the job record.")


def cancellation_script(record, username):
    """Accept an already-ended job, but never mistake scancel acceptance for exit."""
    from truba_session import QUEUE_FUNCTION
    remote = validated_remote_path(record['remote_directory'], username)
    job = str(record.get('job_id') or '')
    if job and (not job.isascii() or not job.isdigit()):
        raise ValueError('Invalid SLURM job ID')
    return 'set -euo pipefail\n' + QUEUE_FUNCTION + 'mkdir -p -- ' + shlex.quote(remote) + '\ncd ' + shlex.quote(remote) + '\n' + '''
touch cancel.request
# An in-flight sbatch must publish its ID before cancellation can be confirmed.
for attempt in {1..30}; do
 [ ! -d submission.lock ] || [ -s job.id ] || { sleep 1; continue; }
 break
done
job=''' + shlex.quote(job) + '''
if [ -s job.id ]; then
 remote_job=$(cat job.id)
 case "$remote_job" in *[!0-9]*|'') exit 2;; esac
 [ -z "$job" ] || [ "$job" = "$remote_job" ] || exit 2
 job=$remote_job
fi
if [ -z "$job" ]; then
 [ ! -d submission.lock ] || exit 3
 exit 0
fi
scancel "$job" || true
for attempt in {1..40}; do
 state=$(rustmech_queue "$job" '%T')
 case "$state" in
  ''|COMPLETED*|CANCELLED*|FAILED*|TIMEOUT*|NODE_FAIL*|OUT_OF_MEMORY*|PREEMPTED*|BOOT_FAIL*|DEADLINE*|REVOKED*) exit 0;;
 esac
 sleep .25
done
echo 'Cancellation not confirmed; reconnect and retry.' >&2
exit 3
'''


def validated_remote_path(path,username):
    prefix=f"/arf/scratch/{username}/RustMech/"
    if not path.startswith(prefix) or not re.fullmatch(r"[a-f0-9]{32}",path[len(prefix):]):
        raise ValueError("The remote job path is outside this user's RustMech directory")
    return path


def connection(settings, auth=None, record=None):
    mode=record.get("transport") if record else settings.truba_transport
    if mode in ("auto","ssh"):
        auth=auth or get_auth()
        if auth:
            return SSHJobs(auth)
        if mode == "ssh":
            raise RuntimeError("Connect through the RustMech TRUBA panel before starting analysis.")
    if mode in ("auto","winscp"):
        return WinSCPJobs(record.get("winscp_site","") if record else settings.truba_winscp_site)
    raise ValueError("Unsupported TRUBA transport")


def run_remote(settings,local,auth=None,marker=None,resume=False):
    local=Path(local)
    saved=local/"remote_job.json"
    record=json.loads(saved.read_text()) if resume else None
    if record and record.get("host") != HOST:
        raise ValueError("Unknown TRUBA host in job record")
    transport=connection(settings,auth,record)
    if record and record["username"] != transport.username:
        raise ValueError("Reconnect using the TRUBA account that owns this job")
    if not record:
        record={"schema_version":1,"host":HOST,"username":transport.username,
                "transport":"winscp" if isinstance(transport,WinSCPJobs) else "ssh",
                "remote_directory":f"/arf/scratch/{transport.username}/RustMech/{uuid.uuid4().hex}",
                "local_directory":str(local.resolve()),"state":"preparing"}
        if isinstance(transport,WinSCPJobs):record["winscp_site"]=transport.site
        (local/"run.slurm").write_text(shell_job_settings(settings,settings.truba_account or transport.username),encoding="utf-8",newline="\n")
        (local/"monitor.sh").write_text(MONITOR,encoding="utf-8",newline="\n")
    remote=validated_remote_path(record["remote_directory"],transport.username)
    registry=state_directory()/"jobs"/(PurePosixPath(remote).name+".json")
    paths=[saved,registry]+([Path(marker)] if marker else [])
    for path in paths:write_record(path,record)
    transport.run(local,remote,record,paths,resume=resume)
    record["state"]="downloaded"
    for path in paths:write_record(path,record)
    return record


def cancel_remote(record_path,auth=None):
    path=Path(record_path)
    if not path.exists():return
    record=json.loads(path.read_text())
    if record.get("state") in ("downloaded","cancelled"):return
    if record.get("host") != HOST:raise ValueError("Unknown TRUBA host in job record")
    transport=connection(None,auth,record)
    if record["username"] != transport.username:raise ValueError("TRUBA account mismatch")
    if record.get("session"):
        from truba_session import cancel_session_request
        cancel_session_request(record,auth)
    else:
        transport.cancel(record)
    record["state"]="cancelled"
    write_record(path,record)
    write_record(Path(record["local_directory"])/"remote_job.json",record)
    write_record(state_directory()/"jobs"/(PurePosixPath(record["remote_directory"]).name+".json"),record)
