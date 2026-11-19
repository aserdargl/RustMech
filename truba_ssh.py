"""Process-local SSH reuse; never repeat authentication for each queue poll.

The connection panel owns registered clients. Standalone tools own their pooled
clients. No credentials or session handles are written to disk by this module.
"""
import atexit
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import RLock

import paramiko
from truba_credentials import state_directory

HOST = "172.16.6.15"


@dataclass
class RemoteAuth:
    username: str
    password: str = field(repr=False)


_lock = RLock()
_sessions = {}


def register_session(username, password, client):
    with _lock:
        previous = _sessions.get(username)
        if previous and previous[1] is not client and previous[2]:
            previous[1].close()
        _sessions[username] = (password, client, False)


def unregister_session(username, client):
    with _lock:
        previous = _sessions.get(username)
        if previous and previous[1] is client:
            del _sessions[username]


def close_owned_sessions():
    with _lock:
        for username, (_, client, owned) in list(_sessions.items()):
            if owned:
                client.close()
                del _sessions[username]


@contextmanager
def ssh_connection(auth):
    # Serialize authentication, while allowing independent SSH channels for
    # cancellation during a large SFTP download. Never replay failed exchanges.
    with _lock:
        cached = _sessions.get(auth.username)
        transport = cached[1].get_transport() if cached else None
        if not (cached and cached[0] == auth.password and transport is not None
                and transport.is_active() and transport.is_authenticated()):
            if cached:
                if cached[2]:
                    cached[1].close()
                del _sessions[auth.username]
            client = paramiko.SSHClient()
            known = state_directory() / 'known_hosts'
            if known.exists():
                client.load_host_keys(str(known))
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            try:
                client.connect(HOST, username=auth.username, password=auth.password,
                    allow_agent=False, look_for_keys=False, timeout=15, banner_timeout=30, auth_timeout=30)
                client.get_transport().set_keepalive(15)
            except BaseException:
                client.close()
                raise
            cached = (auth.password, client, True)
            _sessions[auth.username] = cached
    try:
        yield cached[1]
    except (OSError, EOFError, paramiko.SSHException) as error:
        transport = cached[1].get_transport()
        broken = (isinstance(error, (EOFError, ConnectionError, paramiko.SSHException))
                  or transport is None or not transport.is_active())
        if broken:
            with _lock:
                if _sessions.get(auth.username) is cached:
                    del _sessions[auth.username]
                if cached[2]:
                    cached[1].close()
        raise


atexit.register(close_owned_sessions)
