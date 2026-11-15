"""Credentials protected by Windows DPAPI for the current user only."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path


def state_directory():
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "RustMech" / "TRUBA"


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data, *, decrypt=False):
    if os.name != "nt":
        raise OSError("Saving passwords requires Windows user encryption.")
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = _Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    # UI_FORBIDDEN; never LOCAL_MACHINE, which would permit other users.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel32.LocalFree(target.data)


class CredentialStore:
    def __init__(self, directory=None):
        self.path = (Path(directory) if directory is not None else state_directory()) / "credentials.dpapi"

    def load(self):
        if not self.path.exists():
            return None
        record = json.loads(_crypt(self.path.read_bytes(), decrypt=True))
        if not all(isinstance(record.get(key), str) for key in ("username", "password")):
            raise ValueError("Invalid saved credentials")
        return record["username"], record["password"]

    def save(self, username, password):
        encrypted = _crypt(json.dumps({"username": username, "password": password}).encode("utf-8"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        try:
            temporary.write_bytes(encrypted)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def forget(self):
        self.path.unlink(missing_ok=True)
