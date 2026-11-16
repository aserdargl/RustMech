"""Small live geometry/mesh/DoE solve qualification inside one CPU allocation."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from truba_jobs import get_auth, write_record
from truba_remote import RemoteContext, run_remote_job
from truba_resources import allocate_with_limits
from truba_session import saved_session, refresh_session, release_session
from tools.verify_truba_operations import cases, verify


def main():
    auth = get_auth()
    if auth is None:
        raise RuntimeError('Saved TRUBA credentials are required.')
    root = ROOT / 'build' / ('truba-pipeline-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    root.mkdir(parents=True)
    report = dict(cases={}, passed=False)
    path = root / 'verification.json'
    print('REPORT=' + str(path), flush=True)
    session = saved_session()
    if session and session.get('state') not in ('released', 'ended'):
        session = refresh_session(session, auth)
    owned = not session or session.get('state') in ('released', 'ended')
    if owned:
        session = allocate_with_limits(RemoteContext(partition='auto', walltime='00:15:00'), auth)
    report['job_id'] = session.get('job_id')
    report['owned_session'] = owned
    write_record(path, report)
    try:
        while not session.get('ready'):
            print('SESSION=' + session.get('state', '?'), flush=True)
            if session.get('state') in ('ended', 'released'):
                raise RuntimeError('Session ended before becoming ready.')
            time.sleep(5)
            session = refresh_session(session, auth)
        context = replace(RemoteContext(**session['context']), session_id=session['job_id'])
        report['context'] = asdict(context)
        requests = {'probe': dict(operation='probe'), **cases(root)}
        for name in ('surface', 'preview', 'doe_mesh', 'doe_solve'):
            requests[name]['settings'].update(specimen_architecture='porous_all',
                size=2, cell_size=1, mesh_resolution=.25, solid_fraction=70)
        report['sample'] = requests['surface']['settings']
        for name in ('probe', 'surface', 'preview', 'doe_mesh', 'doe_solve'):
            started = time.monotonic()
            print('START=' + name, flush=True)
            try:
                result, directory = run_remote_job(requests[name], context, auth,
                    directory=root / name, on_progress=lambda stage: print(name + ': ' + stage, flush=True))
                record = json.loads((directory / 'remote_job.json').read_text(encoding='utf-8'))
                report['cases'][name] = dict(passed=verify(name, result, directory, requests[name]),
                    seconds=round(time.monotonic() - started, 3), result=result,
                    job_id=record['job_id'], remote_directory=record['remote_directory'])
            except Exception as error:
                report['cases'][name] = dict(passed=False, seconds=round(time.monotonic() - started, 3), error=str(error))
            write_record(path, report)
            print('FINISH=' + name + ' ' + str(report['cases'][name]['passed']), flush=True)
        report['passed'] = all(item['passed'] for item in report['cases'].values())
        write_record(path, report)
    finally:
        if owned:
            release_session(session, auth)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
