"""Discover and use the authenticated SSH session owned by TRUBA Desktop."""

from dataclasses import dataclass, field
import json
import select
import socket
import struct

import paramiko

from truba_session_protocol import read_frame, write_frame


DISCONNECTED_MESSAGE = "Connect to TRUBA ARF in the TRUBA application first."


@dataclass(frozen=True)
class DesktopSession:
    username: str
    port: int
    key: str = field(repr=False)


class _SftpSocket:
    # Paramiko treats raw socket.socket specially and bypasses its recv timeout.
    def __init__(self, peer):
        self.peer = peer

    def __getattr__(self, name):
        return getattr(self.peer, name)

    def get_name(self):
        return "TRUBA Desktop"

    def recv_ready(self):
        return bool(select.select([self.peer], [], [], 0)[0])


class DesktopSSHClient:
    def __init__(self, session):
        self.session = session

    def _connect(self, operation, **parameters):
        try:
            peer = socket.create_connection(("127.0.0.1", self.session.port), timeout=12)
        except OSError as error:
            raise ConnectionError(DISCONNECTED_MESSAGE) from error
        try:
            request = {"operation": operation, "key": self.session.key, **parameters}
            write_frame(peer, json.dumps(request, ensure_ascii=False).encode("utf-8"))
        except BaseException:
            peer.close()
            raise
        return peer

    @staticmethod
    def _error(frame):
        return ConnectionError(frame[1:].decode("utf-8", "replace") if frame.startswith(b"!") else
                               "Invalid response from the TRUBA desktop session.")

    def open_sftp(self):
        peer = self._connect("sftp")
        try:
            peer.settimeout(25)
            reply = read_frame(peer)
            if reply != b"+":
                raise self._error(reply)
            peer.settimeout(30)
            return paramiko.SFTPClient(_SftpSocket(peer))
        except BaseException:
            peer.close()
            raise

    def execute(self, command, *, timeout=None, on_line=None):
        output, error, pending = bytearray(), bytearray(), bytearray()
        with self._connect("command", command=command, timeout=timeout) as peer:
            peer.settimeout(None if timeout is None else timeout + 12)
            while True:
                frame = read_frame(peer)
                kind, chunk = frame[:1], frame[1:]
                if kind == b"o":
                    if on_line is None:
                        output.extend(chunk)
                    else:
                        complete = (pending + chunk).split(b"\n")
                        pending = bytearray(complete.pop())
                        for line in complete:
                            on_line((line + b"\n").decode("utf-8", "replace"))
                elif kind == b"e":
                    error.extend(chunk)
                elif kind == b"x" and len(chunk) == 4:
                    if pending:
                        on_line(pending.decode("utf-8", "replace"))
                    return struct.unpack("!I", chunk)[0], bytes(output), bytes(error)
                else:
                    raise self._error(frame)
