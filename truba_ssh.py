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
from truba_shared_session import DesktopSession, DesktopSSHClient

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


def active_session_client(auth):
    """Return the connection panel's authenticated client without opening SSH."""
    if isinstance(auth, DesktopSession):
        return DesktopSSHClient(auth)
    with _lock:
        cached = _sessions.get(auth.username)
        if cached and cached[0] == auth.password and not cached[2]:
            transport = cached[1].get_transport()
            if transport is not None and transport.is_active() and transport.is_authenticated():
                return cached[1]
    raise ConnectionError("Connect through RustMech's TRUBA button first.")


def close_owned_sessions():
    with _lock:
        for username, (_, client, owned) in list(_sessions.items()):
            if owned:
                client.close()
                del _sessions[username]


@contextmanager
def ssh_connection(auth):
    if isinstance(auth, DesktopSession):
        yield DesktopSSHClient(auth)
        return
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


def execute_ssh_command(client, command, *, timeout=None, on_line=None):
    if isinstance(client, DesktopSSHClient):
        return client.execute(command, timeout=timeout, on_line=on_line)
    options = {} if timeout is None else {"timeout": timeout}
    _, stdout, stderr = client.exec_command(command, **options)
    if on_line is None:
        data = stdout.read()
    else:
        for line in stdout:
            on_line(line)
        data = b""
    error = stderr.read() if stderr is not None else b""
    return stdout.channel.recv_exit_status(), data, error
