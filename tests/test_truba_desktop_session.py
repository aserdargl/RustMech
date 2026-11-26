"""The desktop connection supplies an SSH handle with no stored credentials."""

import subprocess
import sys
import time
from pathlib import Path
from stat import S_IFDIR, S_IFREG
from textwrap import dedent
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PySide6.QtTest import QSignalSpy
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from truba_sync import RemoteFileListing, TrubaSession
from truba_desktop import CONNECT_TRUBA, SHOW_CONNECTION, DesktopStatus, connect_truba_desktop, open_truba_desktop
from truba_jobs import SSHJobs, connection
from truba_remote import RemoteContext
from truba_shared_session import DesktopSession, DesktopSSHClient


@pytest.mark.parametrize(("launch", "command"), (
    (open_truba_desktop, SHOW_CONNECTION),
    (connect_truba_desktop, CONNECT_TRUBA),
))
@pytest.mark.parametrize("running", [False, True])
def test_desktop_requests_keep_show_and_connect_separate(tmp_path, monkeypatch, launch, command, running):
    executable = tmp_path / "TRUBA.exe"
    executable.touch()
    monkeypatch.setenv("TRUBA_DESKTOP_EXE", str(executable))
    monkeypatch.delenv("TRUBA_DESKTOP_SOURCE", raising=False)
    with patch("truba_desktop._request_running_desktop", return_value=running) as send, \
            patch("truba_desktop.subprocess.Popen") as spawn, \
            patch("truba_desktop.QProcess.startDetached", return_value=(True, 123)) as start:
        launch()
    send.assert_called_once_with(command)
    if running:
        spawn.assert_not_called()
        start.assert_not_called()
    elif sys.platform == "win32":
        spawn.assert_called_once_with(
            [str(executable), command], cwd=tmp_path, creationflags=subprocess.CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        start.assert_called_once_with(str(executable), [command], str(tmp_path))


@pytest.mark.parametrize("explicit_source", [False, True])
def test_desktop_starts_truba_source_with_its_own_python(tmp_path, monkeypatch, explicit_source):
    rustmech = tmp_path / "RustMech"
    source = tmp_path / ("truba project" if explicit_source else "TRUBA")
    python = source / ".venv" / "Scripts" / "pythonw.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    script = source / "main.py"
    script.touch()
    (rustmech / "TRUBA.exe").parent.mkdir()
    (rustmech / "TRUBA.exe").touch()
    monkeypatch.setattr("truba_desktop.__file__", str(rustmech / "truba_desktop.py"))
    monkeypatch.delenv("TRUBA_DESKTOP_EXE", raising=False)
    monkeypatch.delenv("TRUBA_DESKTOP_SOURCE", raising=False)
    if explicit_source:
        monkeypatch.setenv("TRUBA_DESKTOP_SOURCE", str(source))
    with patch("truba_desktop._request_running_desktop", return_value=False), \
            patch("truba_desktop.subprocess.Popen") as spawn, \
            patch("truba_desktop.QProcess.startDetached", return_value=(True, 123)) as start:
        connect_truba_desktop()
    if sys.platform == "win32":
        spawn.assert_called_once_with(
            [str(python), str(script), CONNECT_TRUBA], cwd=source, creationflags=subprocess.CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        start.assert_called_once_with(str(python), [str(script), CONNECT_TRUBA], str(source))


@pytest.mark.skipif(sys.platform != "win32", reason="requires a Windows detached process")
def test_truba_can_stay_open_after_rustmech_console_closes(tmp_path):
    (tmp_path / "companion.py").write_text(dedent("""\
        import ctypes
        from pathlib import Path
        import time

        Path("has-console").write_text(str(bool(ctypes.windll.kernel32.GetConsoleWindow())))
        Path("ready").touch()
        try:
            deadline = time.monotonic() + 20
            while not Path("release").exists() and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            Path("stopped").touch()
    """), encoding="utf-8")
    launch = dedent("""\
        from pathlib import Path
        import sys
        from unittest.mock import patch
        from truba_desktop import open_truba_desktop

        folder = Path(sys.argv[1])
        python = Path(sys.executable)
        command = (python, [str(folder / "companion.py")], folder)
        with patch("truba_desktop._request_running_desktop", return_value=False), \\
                patch("truba_desktop._desktop_launch_command", return_value=command):
            open_truba_desktop()
        print("RUSTMECH_EXITING")
    """)
    try:
        result = subprocess.run(
            [sys.executable, "-c", launch, str(tmp_path)],
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10,
            creationflags=subprocess.DETACHED_PROCESS,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "RUSTMECH_EXITING"
        deadline = time.monotonic() + 5
        while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (tmp_path / "ready").exists()
        assert (tmp_path / "has-console").read_text() == "False"
        assert not (tmp_path / "stopped").exists()
    finally:
        (tmp_path / "release").touch()


def test_rustmech_uses_only_the_advertised_arf_session(tmp_path):
    app = QApplication.instance() or QApplication([])
    owner = QObject()
    with patch("truba_sync.state_directory", return_value=tmp_path):
        client = TrubaSession(owner)
    try:
        assert client.analysis_auth() is None
        changed = QSignalSpy(client.connection_changed)
        session = DesktopSession("tester", 10000, "a" * 64)
        client._status_changed(DesktopStatus(session))
        assert client.connected and client.analysis_auth() is session
        client._status_changed(DesktopStatus(other_cluster=True))
        assert not client.connected and client.analysis_auth() is None
        assert [changed.at(i) for i in range(changed.count())] == [[True], [False]]
    finally:
        client.shutdown()
        app.processEvents()


def test_ssh_delegate_uses_desktop_session_even_for_old_winscp_records():
    session = DesktopSession("tester", 10000, "a" * 64)
    with patch("truba_ssh.paramiko.SSHClient") as direct, patch("truba_jobs.get_auth") as saved:
        delegate = connection(RemoteContext(transport="auto"), session, {"transport": "winscp"})
        assert isinstance(delegate, SSHJobs)
        with delegate.connect() as client:
            assert isinstance(client, DesktopSSHClient)
            assert client.session is session
        with pytest.raises(ConnectionError, match="TRUBA ARF"):
            connection(RemoteContext(transport="auto"))
        direct.assert_not_called()
        saved.assert_not_called()


def test_sync_lists_only_the_selected_accounts_remote_job_directory():
    session = DesktopSession("tester", 10000, "a" * 64)
    folder = "/arf/scratch/tester/RustMech/" + "b" * 32
    with patch("truba_sync.DesktopSSHClient") as desktop:
        sftp = desktop.return_value.open_sftp.return_value.__enter__.return_value
        sftp.listdir_attr.return_value = [
            SimpleNamespace(filename="request.json", st_mode=S_IFREG),
            SimpleNamespace(filename="output", st_mode=S_IFDIR),
        ]
        worker = RemoteFileListing(session, folder, None)
        worker.run()
        desktop.assert_called_once_with(session)
        sftp.listdir_attr.assert_called_once_with(folder)
        assert worker.files == [("output", True), ("request.json", False)]

        invalid = RemoteFileListing(session, folder.replace("/tester/", "/other/"), None)
        invalid.run()
        assert invalid.files is None
        sftp.listdir_attr.assert_called_once()
