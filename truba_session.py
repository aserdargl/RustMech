"""Durable, per-user resource sessions; computation stays inside Slurm."""
from dataclasses import asdict
import json
from pathlib import Path
import re
import shlex
import tempfile
import uuid

from truba_credentials import state_directory
from truba_jobs import HOST, validated_remote_path, write_record


QUEUE_FUNCTION = '''rustmech_queue() {
 local queue accounting
 if queue=$(squeue -h -j "$1" -o "$2"); then
  printf '%s\\n' "$queue"
 else
  accounting=$(sacct -n -X -j "$1" --format=State -P | head -n 1)
  case "$accounting" in
   COMPLETED*|FAILED*|CANCELLED*|TIMEOUT*|NODE_FAIL*|OUT_OF_MEMORY*|PREEMPTED*|BOOT_FAIL*|DEADLINE*|REVOKED*) printf '\\n';;
   *) echo 'Resource state could not be confirmed' >&2; return 2;;
  esac
 fi
}
'''


def session_path():
    return state_directory() / 'resource_session.json'


def saved_session():
    path = session_path()
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def validate_session(record, transport):
    if (record.get('kind') != 'rustmech_session' or record.get('host') != HOST or
            record.get('username') != transport.username or
            not re.fullmatch(r'\d+', str(record.get('job_id', '')))):
        raise ValueError('Resource session does not belong to this TRUBA connection.')
    return validated_remote_path(record['remote_directory'], transport.username)


def create_session(context, auth=None):
    from truba_remote import RemoteTransport, _source_root, slurm_script
    context.validate()
    if context.session_id:
        raise ValueError('Release the existing resource session first.')
    previous = saved_session()
    if previous and previous.get('state') not in ('released', 'ended'):
        raise RuntimeError('An existing resource request is saved. Refresh or release it first.')
    transport = RemoteTransport(context, auth)
    remote = f'/arf/scratch/{transport.username}/RustMech/{uuid.uuid4().hex}'
    validated_remote_path(remote, transport.username)
    script = slurm_script(context, transport.username,
                          dict(operation='probe', source_sha256='0'*64, native_sha256='0'*64))
    script = script.split('set -euo pipefail', 1)[0].replace('RustMech-remote', 'RustMech-session')
    load = 'module load ' + shlex.quote(context.python_module) if context.python_module else ':'
    script += ('#SBATCH --no-requeue\nset -euo pipefail\nset +u\n' + load + '\nset -u\n' +
               'exec python3 session_worker.py "$PWD"\n')
    record = dict(kind='rustmech_session', host=HOST, username=transport.username,
                  remote_directory=remote, context=asdict(context), state='submitting',
                  transport='winscp' if transport.delegate.__class__.__name__ == 'WinSCPJobs' else 'ssh')
    if record['transport'] == 'winscp': record['winscp_site'] = transport.delegate.site
    write_record(session_path(), record)
    transport.command('mkdir -p -- ' + shlex.quote(remote + '/inbox'))
    with tempfile.TemporaryDirectory(prefix='rustmech-session-') as temporary:
        local = Path(temporary) / 'session.slurm'
        local.write_text(script, encoding='utf-8', newline='\n')
        transport.put(local, remote + '/session.slurm')
        transport.put(_source_root() / 'truba_session_worker.py', remote + '/session_worker.py')
    return refresh_session(record, auth, submit=True)


def refresh_session(record, auth=None, *, submit=False):
    from truba_remote import RemoteContext, RemoteTransport
    transport = RemoteTransport(RemoteContext(**record['context']), auth, record)
    remote = validated_remote_path(record['remote_directory'], transport.username)
    if record.get('host') != HOST or record.get('username') != transport.username:
        raise ValueError('Resource session account mismatch.')
    prefix = 'cd ' + shlex.quote(remote) + '\nset -euo pipefail\n' + QUEUE_FUNCTION
    if submit:
        prefix += '''if mkdir submission.lock 2>/dev/null; then
 if ! job=$(sbatch --parsable session.slurm 2>submission.err); then
  rmdir submission.lock
  printf 'SESSION_REJECTED='
  cat submission.err
  exit 0
 fi
 job=${job%%;*}
 [[ "$job" =~ ^[0-9]+$ ]] || exit 2
 printf '%s\\n' "$job" > job.id
fi
'''
    reply = transport.command(prefix + '''test -s job.id || { echo 'Submission is unconfirmed. Do not allocate again.' >&2; exit 3; }
job=$(cat job.id)
[[ "$job" =~ ^[0-9]+$ ]] || exit 2
printf 'SESSION_ID=%s\\n' "$job"
printf 'SESSION_QUEUE='
rustmech_queue "$job" '%T|%L|%r|%P|%N'
if [ -s ready ]; then printf 'SESSION_READY=%s\\n' "$(cat ready)"; fi
''')
    if 'SESSION_REJECTED=' in reply:
        record = dict(record, state='ended', ready=False,
                      reason=reply.split('SESSION_REJECTED=',1)[1].strip())
        write_record(session_path(), record)
        return record
    match = re.search(r'^SESSION_ID=(\d+)\s*$', reply, re.M)
    if not match: raise RuntimeError('Resource submission is unconfirmed; refresh the saved session.')
    if record.get('job_id') and record['job_id'] != match[1]:
        raise RuntimeError('Resource job ID changed; refusing to attach.')
    record = dict(record, job_id=match[1])
    queue = re.search(r'^SESSION_QUEUE=(.*)$', reply, re.M)
    if queue is None: raise RuntimeError('Resource state could not be read.')
    value = queue[1].strip()
    fields = value.split('|', 4)
    record.update(state=fields[0] if value else 'ended', remaining=fields[1] if len(fields)>1 else '',
                  reason=fields[2] if len(fields)>2 else '',
                  ready=fields[0]=='RUNNING' and f'SESSION_READY={match[1]}' in reply.splitlines())
    if len(fields) > 3:
        record['allocated_partition'] = fields[3]
    if len(fields) > 4:
        record['allocated_nodes'] = fields[4]
    write_record(session_path(), record)
    return record


def save_released_session(record):
    """Keep requests belonging to the confirmed stopped allocation out of active jobs."""
    record = dict(record, state='released', ready=False)
    write_record(session_path(), record)
    terminal = {'downloaded', 'cancelled', 'completed', 'failed', 'timeout',
                'node_fail', 'out_of_memory', 'preempted', 'boot_fail',
                'deadline', 'revoked', 'released', 'ended'}
    for path in (state_directory() / 'jobs').glob('*.json'):
        try:
            request = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if not isinstance(request, dict):
            continue
        parent = request.get('session')
        state = str(request.get('state', '')).lower().split('|')[0].split()
        if (not isinstance(parent, dict) or not record.get('job_id')
                or request.get('host') != HOST
                or request.get('username') != record.get('username')
                or parent.get('job_id') != record.get('job_id')
                or state and state[0].rstrip('+') in terminal):
            continue
        request = dict(request, state='cancelled')
        local = request.get('local_directory')
        if local and (Path(local) / 'remote_job.json').is_file():
            write_record(Path(local) / 'remote_job.json', request)
        write_record(path, request)
    return record


def release_session(record, auth=None):
    from truba_remote import RemoteContext, RemoteTransport
    if record.get('state') == 'released':
        return record
    if record.get('state') == 'ended':
        return save_released_session(record)
    if not record.get('job_id'):
        record = refresh_session(record, auth)
        if record.get('state') == 'ended':
            return save_released_session(record)
    transport = RemoteTransport(RemoteContext(**record['context']), auth, record)
    validate_session(record, transport)
    # scancel acceptance is not proof the allocation has stopped. Check Slurm
    # before reporting release or allowing the application to close.
    reply = transport.command('set -euo pipefail\n' + QUEUE_FUNCTION + f'''
job={record['job_id']}
scancel {record['job_id']} || {{
 state=$(rustmech_queue "$job" '%T')
 case "$state" in
  ''|COMPLETED*|CANCELLED*|FAILED*|TIMEOUT*|NODE_FAIL*|OUT_OF_MEMORY*|PREEMPTED*|BOOT_FAIL*|DEADLINE*|REVOKED*) printf 'SESSION_RELEASED\\n'; exit 0;;
  *) exit 2;;
 esac
}}
for attempt in {{1..20}}; do
 state=$(rustmech_queue "$job" '%T')
 case "$state" in
  ''|COMPLETED*|CANCELLED*|FAILED*|TIMEOUT*|NODE_FAIL*|OUT_OF_MEMORY*|PREEMPTED*|BOOT_FAIL*|DEADLINE*|REVOKED*) printf 'SESSION_RELEASED\\n'; exit 0;;
 esac
 sleep .25
done
echo 'Resource release is still unconfirmed; reconnect and retry.' >&2
exit 3
''')
    if 'SESSION_RELEASED' not in reply.splitlines(): raise RuntimeError('Resource release was not confirmed.')
    return save_released_session(record)


def cancel_session_request(record, auth=None):
    """Cancel this request, never the allocation."""
    from truba_remote import RemoteContext, RemoteTransport
    transport = RemoteTransport(RemoteContext(**record['context']), auth, record)
    remote = validated_remote_path(record['remote_directory'], transport.username)
    if record['username'] != transport.username: raise ValueError('TRUBA account mismatch')
    validate_session(record['session'], transport)
    reply = transport.command('set -euo pipefail\n' + QUEUE_FUNCTION +
                              'mkdir -p -- ' + shlex.quote(remote) + '\ncd ' + shlex.quote(remote) + '''
touch cancel.request
# The dispatcher publishes RUNNING before launching any srun. Without that
# status there can be no active solver; a later launch sees cancel.request.
if [ ! -e step.status ]; then echo REQUEST_STOPPED; exit 0; fi
for attempt in $(seq 1 40); do
 # A stopped parent cannot acknowledge a leftover RUNNING step.status file.
 # Confirm its queue state instead of waiting forever on the dead dispatcher.
 parent_state=$(rustmech_queue ''' + record['session']['job_id'] + ''' '%T')
 case "$parent_state" in
  ''|COMPLETED*|CANCELLED*|FAILED*|TIMEOUT*|NODE_FAIL*|OUT_OF_MEMORY*|PREEMPTED*|BOOT_FAIL*|DEADLINE*|REVOKED*) echo REQUEST_STOPPED; exit 0;;
 esac
 if [ -s step.status ]; then
  case "$(cat step.status)" in CANCELLED*|COMPLETED*|FAILED*) echo REQUEST_STOPPED; exit 0;; esac
 fi
 sleep 1
done
echo 'Request cancellation not yet confirmed' >&2
exit 2
''')
    if 'REQUEST_STOPPED' not in reply.splitlines():
        raise RuntimeError('Request cancellation not confirmed; keep the saved job record.')

