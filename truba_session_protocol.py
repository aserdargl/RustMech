"""Framing for the local TRUBA desktop session protocol (version 1)."""

import struct


STATUS_REQUEST = b"--session-v1\n"
FRAME_LIMIT = 256 * 1024


def _read_exact(peer, size):
    data = bytearray()
    while len(data) < size:
        chunk = peer.recv(size - len(data))
        if not chunk:
            raise ConnectionError("The TRUBA desktop session channel closed.")
        data.extend(chunk)
    return bytes(data)


def read_frame(peer):
    (size,) = struct.unpack("!I", _read_exact(peer, 4))
    if size > FRAME_LIMIT:
        raise ValueError("TRUBA session message is too large.")
    return _read_exact(peer, size)


def write_frame(peer, data):
    if len(data) > FRAME_LIMIT:
        raise ValueError("TRUBA session message is too large.")
    peer.sendall(struct.pack("!I", len(data)) + data)
