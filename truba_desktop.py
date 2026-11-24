"""Open the independent TRUBA desktop app without starting a cluster connection."""

import ctypes
from hashlib import sha256
import os
from pathlib import Path
import sys

from PySide6.QtCore import QProcess
from PySide6.QtNetwork import QLocalSocket

from local_kratos import system_dll_search


SHOW_CONNECTION = "--show-connection"
REPLY_TIMEOUT_MS = 250


def _local_app_data():
    return Path(os.environ.get("LOCALAPPDATA", Path.home()))


def _show_running_desktop():
    directory = _local_app_data() / "TRUBA"
    user_key = sha256(os.path.normcase(str(directory)).encode("utf-8")).hexdigest()
    socket = QLocalSocket()
    socket.connectToServer(f"TRUBA.Desktop.{user_key}")
    try:
        if not socket.waitForConnected(REPLY_TIMEOUT_MS):
            if socket.error() in (QLocalSocket.LocalSocketError.ServerNotFoundError,
                                  QLocalSocket.LocalSocketError.ConnectionRefusedError):
                return False
            raise OSError("Could not reach the running TRUBA application.")
        if not socket.canReadLine():
            socket.waitForReadyRead(REPLY_TIMEOUT_MS)
        process_id = bytes(socket.readLine(16)).strip()
        if not process_id.isdigit() or not 0 < int(process_id) < 0xFFFFFFFF:
            raise OSError("The running TRUBA application did not respond.")
        if sys.platform == "win32":
            # The foreground RustMech window can grant this instance permission to activate.
            ctypes.windll.user32.AllowSetForegroundWindow(int(process_id))
        socket.write(f"{SHOW_CONNECTION}\n".encode("ascii"))
        socket.flush()
        if not socket.canReadLine():
            socket.waitForReadyRead(REPLY_TIMEOUT_MS)
        if bytes(socket.readLine(16)) != b"ok\n":
            raise OSError("The running TRUBA application did not respond.")
        return True
    finally:
        socket.abort()


def open_truba_desktop():
    if _show_running_desktop():
        return
    app_directory = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    configured = os.environ.get("TRUBA_DESKTOP_EXE")
    candidates = (Path(configured).expanduser(),) if configured else (
        app_directory / "TRUBA.exe",
        app_directory.parent / "TRUBA" / "TRUBA.exe",
        _local_app_data() / "Programs" / "TRUBA" / "TRUBA.exe",
        *(parent / "TRUBA" / "dist" / "TRUBA.exe" for parent in app_directory.parents[:3]),
    )
    for executable in candidates:
        if not executable.is_absolute() or not executable.is_file():
            continue
        with system_dll_search():
            started, _ = QProcess.startDetached(str(executable), [SHOW_CONNECTION], str(executable.parent))
        if not started:
            raise OSError("Could not start the TRUBA application.")
        return
    raise OSError("TRUBA.exe was not found. Set TRUBA_DESKTOP_EXE to its full path.")
