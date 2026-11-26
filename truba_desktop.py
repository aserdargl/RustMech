"""Open the TRUBA desktop or explicitly ask it to connect."""

import ctypes
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from PySide6.QtNetwork import QLocalSocket

from local_kratos import system_dll_search
from truba_session_protocol import STATUS_REQUEST
from truba_shared_session import DesktopSession


SHOW_CONNECTION = "--show-connection"
CONNECT_TRUBA = "--connect-truba"
REPLY_TIMEOUT_MS = 250


def _local_app_data():
    return Path(os.environ.get("LOCALAPPDATA", Path.home()))


def _desktop_server_name():
    directory = _local_app_data() / "TRUBA"
    user_key = sha256(os.path.normcase(str(directory)).encode("utf-8")).hexdigest()
    return f"TRUBA.Desktop.{user_key}"


def _request_running_desktop(command):
    socket = QLocalSocket()
    socket.connectToServer(_desktop_server_name())
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
        socket.write(f"{command}\n".encode("ascii"))
        socket.flush()
        if not socket.canReadLine():
            socket.waitForReadyRead(REPLY_TIMEOUT_MS)
        if bytes(socket.readLine(16)) != b"ok\n":
            if command == CONNECT_TRUBA:
                raise OSError("TRUBA Desktop does not support one-click connection. Restart it with the updated version.")
            raise OSError("The running TRUBA application did not respond.")
        return True
    finally:
        socket.abort()


def open_truba_desktop():
    _request_desktop(SHOW_CONNECTION)


def connect_truba_desktop():
    _request_desktop(CONNECT_TRUBA)


def _request_desktop(command):
    if _request_running_desktop(command):
        return
    executable, arguments, directory = _desktop_launch_command(command)
    with system_dll_search():
        if sys.platform == "win32":
            # ShellExecute starts TRUBA without inheriting PyCharm's console pipes.
            try:
                os.startfile(str(executable), "open", subprocess.list2cmdline(arguments), str(directory))
            except OSError as exc:
                raise OSError("Could not start the TRUBA application.") from exc
            return
        started, _ = QProcess.startDetached(str(executable), arguments, str(directory))
    if not started:
        raise OSError("Could not start the TRUBA application.")


def _desktop_launch_command(command):
    frozen = getattr(sys, "frozen", False)
    app_directory = Path(sys.executable if frozen else __file__).resolve().parent
    configured = os.environ.get("TRUBA_DESKTOP_EXE")
    source = os.environ.get("TRUBA_DESKTOP_SOURCE")
    # Use the neighboring source checkout during development, with its own
    # Python environment. Installed RustMech uses the companion app by default.
    if source or (not frozen and not configured):
        directory = Path(source).expanduser() if source else app_directory.parent / "TRUBA"
        script = directory / "main.py"
        if source or script.is_file():
            python = directory / ".venv" / "Scripts" / "pythonw.exe"
            if not directory.is_absolute() or not script.is_file() or not python.is_file():
                raise OSError("TRUBA source needs an absolute folder containing main.py and .venv/Scripts/pythonw.exe.")
            return python, [str(script), command], directory

    candidates = (Path(configured).expanduser(),) if configured else (
        app_directory / "TRUBA.exe",
        app_directory.parent / "TRUBA" / "TRUBA.exe",
        _local_app_data() / "Programs" / "TRUBA" / "TRUBA.exe",
        *(parent / "TRUBA" / "dist" / "TRUBA.exe" for parent in app_directory.parents[:3]),
    )
    for executable in candidates:
        if executable.is_absolute() and executable.is_file():
            return executable, [command], executable.parent
    raise OSError("TRUBA.exe was not found. Set TRUBA_DESKTOP_EXE to its full path.")


@dataclass(frozen=True)
class DesktopStatus:
    session: DesktopSession | None = None
    other_cluster: bool = False


def _parse_status(data):
    try:
        status = json.loads(data)
    except ValueError:
        return DesktopStatus()
    if not isinstance(status, dict) or status.get("connected") is not True:
        return DesktopStatus()
    if status.get("cluster") != "truba":
        return DesktopStatus(other_cluster=True)
    username, port, key = (status.get(field) for field in ("username", "port", "key"))
    if (not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", username)
            or type(port) is not int or not 1 <= port <= 65535
            or not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key)):
        return DesktopStatus()
    return DesktopStatus(DesktopSession(username, port, key))


class DesktopSessionMonitor(QObject):
    updated = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.peer = None
        self.greeted = False
        self.previous = DesktopStatus()
        self.missed = 0
        self.timer = QTimer(self, interval=1000)
        self.timer.timeout.connect(self._poll)
        self.deadline = QTimer(self, interval=750, singleShot=True)
        self.deadline.timeout.connect(self._unavailable)

    def start(self):
        self.timer.start()
        self._poll()

    def stop(self):
        self.timer.stop()
        self._close_peer()

    def _poll(self):
        if self.peer is not None:
            return
        self.greeted = False
        self.peer = QLocalSocket(self)
        self.peer.setReadBufferSize(1024)
        self.peer.readyRead.connect(self._read)
        self.peer.disconnected.connect(self._unavailable)
        self.peer.errorOccurred.connect(self._unavailable)
        self.deadline.start()
        self.peer.connectToServer(_desktop_server_name())

    def _read(self):
        peer = self.peer
        if not self.greeted and peer.canReadLine():
            if not bytes(peer.readLine()).strip().isdigit():
                self._unavailable()
                return
            self.greeted = True
            peer.write(STATUS_REQUEST)
            peer.flush()
        if peer is self.peer and self.greeted and peer.canReadLine():
            self._finish(_parse_status(bytes(peer.readLine())))

    def _unavailable(self, *_):
        self._finish(DesktopStatus(), missed=True)

    def _finish(self, status, *, missed=False):
        self._close_peer()
        # A briefly busy GUI need not interrupt an already-running analysis.
        self.missed = self.missed + 1 if missed else 0
        if status != self.previous and (not missed or self.missed > 1):
            self.previous = status
            self.updated.emit(status)

    def _close_peer(self):
        self.deadline.stop()
        peer, self.peer = self.peer, None
        if peer is not None:
            peer.blockSignals(True)
            peer.abort()
            peer.deleteLater()
