"""Prepare one replacement allocation and hand off new requests at job boundaries.

Completed analyses are never rerun. Running solver processes stay on their
original allocation; this is deliberately not solver checkpoint/restart.
"""
from concurrent.futures import CancelledError
from dataclasses import replace
import json
import re
from threading import RLock
import time
from pathlib import Path

from truba_jobs import HOST, write_record
from truba_session import (create_session, refresh_session, release_session,
                           saved_session, session_path)

LOCK = RLock()


def successor_path():
    return session_path().with_name('resource_successor.json')


def remaining_seconds(value):
    match = re.fullmatch(r'(?:(\d+)-)?(\d+):(\d{2})(?::(\d{2}))?', value or '')
    if not match:
        return None
    days, first, second, third = match.groups()
    if third is None:
        if days:
            return None
        return int(first) * 60 + int(second)
    return int(days or 0) * 86400 + int(first) * 3600 + int(second) * 60 + int(third)


def renewal_due(record):
    if not record.get('context', {}).get('auto_renew', True):
        return False
    remaining = remaining_seconds(record.get('remaining'))
    duration = remaining_seconds(record['context']['walltime'])
    return (record.get('state') == 'ended' and record.get('renewal_due_seen', False)
            and record.get('terminal_state', 'TIMEOUT').startswith('TIMEOUT') or
            record.get('state') == 'RUNNING' and remaining is not None and duration is not None
            and remaining <= min(3600, max(1, duration // 10)))


def _belongs(current, record):
    return (current and current.get('username') == record.get('username')
            and current.get('job_id') == record.get('job_id'))


def prepare_if_needed(record, auth=None):
    """Durably save before submission; repeat calls recover the same request."""
    from truba_remote import RemoteContext
    with LOCK:
        current = saved_session()
        if not _belongs(current, record) or current.get('state') == 'released' or current.get('renewal_disabled'):
            return None
        current = refresh_session(current, auth)
        if current.get('renewal_disabled') or current.get('state') == 'released':
            return None
        if not renewal_due(current):
            return None
        path = successor_path()
        if path.exists():
            successor = json.loads(path.read_text(encoding='utf-8'))
            if successor.get('renewal_parent_id') != current['job_id']:
                raise RuntimeError('A replacement for another session is saved; release resources before retrying.')
            if successor.get('state') in ('released', 'ended'):
                raise RuntimeError('Replacement allocation ended or was rejected: ' + successor.get('reason', 'refresh or release resources'))
            return refresh_session(successor, auth, submit=not successor.get('job_id'), save_path=path)
        context = replace(RemoteContext(**current['context']), session_id='')
        return create_session(context, auth, successor_of=current)


def context_for_request(context, auth=None, *, cancelled=None, marker=None, on_progress=None):
    """Wait for a ready replacement before submitting another batch row."""
    from truba_remote import RemoteContext
    while True:
        if ((cancelled is not None and cancelled.is_set()) or
                (marker is not None and Path(marker).with_suffix('.cancel').exists())):
            raise CancelledError
        with LOCK:
            current = saved_session()
            valid_ids = {str((current or {}).get('job_id', ''))}
            valid_ids.update(str(item['job_id']) for item in (current or {}).get('previous_sessions', []))
            if not current or context.session_id not in valid_ids:
                raise RuntimeError('Resource session changed. Refresh before submitting new analyses.')
            if current.get('state') == 'released' or current.get('renewal_disabled'):
                raise RuntimeError('Resource session is being released.')
            current = refresh_session(current, auth)
            successor = prepare_if_needed(current, auth)
            if successor is None:
                return replace(RemoteContext(**current['context']), session_id=current['job_id'])
            if successor.get('ready') and successor.get('state') == 'RUNNING':
                previous = list(current.get('previous_sessions', []))
                previous.append({key: value for key, value in current.items() if key != 'previous_sessions'})
                successor = dict(successor, previous_sessions=previous)
                write_record(session_path(), successor)
                successor_path().unlink(missing_ok=True)
                return replace(RemoteContext(**successor['context']), session_id=successor['job_id'])
        if on_progress:
            on_progress('TRUBA · waiting for replacement resource allocation; completed results are saved')
        if cancelled is not None:
            cancelled.wait(2)
        else:
            time.sleep(2)


def release_related(record, auth=None):
    """Explicit release/close cancels every prepared or draining allocation."""
    with LOCK:
        if record.get('host') != HOST:
            raise ValueError('Unknown TRUBA host in resource session.')
        current = saved_session()
        if current:
            if not (_belongs(current, record) or any(_belongs(item, record) for item in current.get('previous_sessions', []))):
                raise RuntimeError('A different resource session is saved. Refresh before releasing resources.')
            record = current
        record = dict(record, renewal_disabled=True)
        write_record(session_path(), record)
        path = successor_path()
        if path.exists():
            successor = json.loads(path.read_text(encoding='utf-8'))
            if successor.get('renewal_parent_id') == record.get('job_id'):
                release_session(successor, auth, _related=False)
                path.unlink()
        previous = []
        for item in record.get('previous_sessions', []):
            previous.append(release_session(item, auth, _related=False))
        if previous:
            record = dict(record, previous_sessions=previous)
            write_record(session_path(), record)
        return record
