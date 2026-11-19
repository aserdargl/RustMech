"""Password-authenticated TRUBA sessions without Qt or solver dependencies."""

import base64
import hashlib
from pathlib import Path
import socket
from threading import Event

import paramiko

from truba_ssh import HOST, RemoteAuth, register_session, unregister_session


class HostKeyDeclined(paramiko.SSHException):
    """The user did not approve the server identity."""


class TrubaSession:
    """Run one password SSH session, reconnecting until stopped or refused.

    Run on a worker thread when embedding in an event loop. Callbacks run on
    that worker thread. The host key callback can call decide_host_key directly
    or forward the fingerprint to another thread for a decision. The failure
    callback receives the exception class name.
    Create a new session for each explicit connection attempt.
    """

    def __init__(self, username, password, directory, *, on_state, on_host_key, on_failure):
        self.username = username
        self._password = password
        self.directory = Path(directory)
        self._on_state = on_state
        self._on_host_key = on_host_key
        self._on_failure = on_failure
        self._stop_requested = Event()
        self._host_decided = Event()
        self._host_accepted = False
        self._client = None
        self._auth = None

    @property
    def auth(self):
        """Credentials for analysis while this session is authenticated."""
        return None if self._stop_requested.is_set() else self._auth

    def decide_host_key(self, accepted):
        self._host_accepted = accepted
        self._host_decided.set()

    def missing_host_key(self, client, hostname, key):
        fingerprint = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        self._host_decided.clear()
        self._host_accepted = False
        if self._stop_requested.is_set():
            raise HostKeyDeclined("Host identity was not accepted")
        self._on_state("host_key_confirmation")
        self._on_host_key(f"{hostname}\n{key.get_name()}\nSHA256:{fingerprint}")
        self._host_decided.wait()
        if not self._host_accepted or self._stop_requested.is_set():
            raise HostKeyDeclined("Host identity was not accepted")
        client.get_host_keys().add(hostname, key.get_name(), key)
        client.save_host_keys(str(self.directory / "known_hosts"))
        self._on_state("authenticating")

    def stop(self):
        self._stop_requested.set()
        self._auth = None
        self._host_decided.set()
        client = self._client
        if client is not None:
            client.close()

    def run(self):
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            while not self._stop_requested.is_set():
                self._on_state("connecting")
                client = self._client = paramiko.SSHClient()
                try:
                    known_hosts = self.directory / "known_hosts"
                    if known_hosts.exists():
                        client.load_host_keys(str(known_hosts))
                    client.set_missing_host_key_policy(self)
                    # Authenticate only with the credentials supplied by the caller.
                    client.connect(HOST, username=self.username, password=self._password,
                                   allow_agent=False, look_for_keys=False,
                                   timeout=15, banner_timeout=30, auth_timeout=30)
                    if self._stop_requested.is_set():
                        break
                    transport = client.get_transport()
                    if transport is None or not transport.is_authenticated():
                        raise paramiko.AuthenticationException()
                    transport.set_keepalive(15)
                    register_session(self.username, self._password, client)
                    self._auth = RemoteAuth(self.username, self._password)
                    self._on_state("connected")
                    while not self._stop_requested.wait(1):
                        if not transport.is_active() or not transport.is_authenticated():
                            break
                except paramiko.BadAuthenticationType:
                    self._on_state("password_auth_unavailable")
                    break
                except paramiko.AuthenticationException:
                    self._on_state("authentication_failed")
                    break
                except paramiko.BadHostKeyException:
                    self._on_state("host_key_changed")
                    break
                except HostKeyDeclined:
                    self._on_state("host_key_declined")
                    break
                except (paramiko.SSHException, EOFError) as exc:
                    self._on_state("ssh_failed")
                    # Report the category only: exception text can contain server
                    # content or authentication data.
                    self._on_failure(type(exc).__name__)
                    break
                except socket.timeout:
                    self._on_state("connection_timeout")
                    break
                except OSError:
                    self._on_state("waiting_for_network")
                    if self._stop_requested.wait(10):
                        break
                    continue
                finally:
                    self._auth = None
                    unregister_session(self.username, client)
                    client.close()
                    self._client = None
                if self._stop_requested.is_set():
                    break
                self._on_state("reconnecting")
                if self._stop_requested.wait(10):
                    break
        except (OSError, ValueError):
            self._on_state("local_error")
        finally:
            self._password = ""
