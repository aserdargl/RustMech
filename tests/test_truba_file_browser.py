"""Cover the SFTP browser's project boundary and desktop entry points."""

from contextlib import contextmanager
from io import BytesIO
from pathlib import PurePosixPath
from stat import S_IFDIR, S_IFLNK, S_IFREG
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import MagicMock

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSplitter
import pytest
from paramiko import SSHException

from esen_workspace import EsenWorkspace
from truba_files import create_directory, download_file, list_directory, project_root, upload_file, upload_tree
from truba_ssh import RemoteAuth, register_session, unregister_session
from window_state import WindowStateStore


class Truba(QObject):
    connection_changed = Signal(bool)
    auth = None

    def analysis_auth(self):
        return self.auth


@pytest.fixture
def remote():
    auth = RemoteAuth("test-user", "test-password")
    client = MagicMock()
    sftp = client.open_sftp.return_value.__enter__.return_value
    sftp.normalize.side_effect = str
    register_session(auth.username, auth.password, client)
    try:
        yield auth, sftp
    finally:
        unregister_session(auth.username, client)


def test_sftp_keeps_listing_and_folder_creation_in_project(remote):
    auth, sftp = remote
    root = project_root(auth.username)
    relative = PurePosixPath("results")
    assert list_directory(auth, relative) == []
    sftp.listdir_attr.assert_called_once_with(str(root / relative))
    create_directory(auth, relative, "Yeni sonuçlar")
    sftp.mkdir.assert_called_once_with(str(root / relative / "Yeni sonuçlar"))
    for bad in ("", "..", "../outside", "nested/folder"):
        with pytest.raises(ValueError, match="single folder name"):
            create_directory(auth, relative, bad)

    sftp.normalize.side_effect = lambda path: path if path == str(root) else "/outside"
    with pytest.raises(ValueError, match="outside the RustMech project"):
        list_directory(auth, relative)
    sftp.mkdir.assert_called_once()


def test_upload_stages_data_and_never_overwrites_existing_file(remote, tmp_path):
    auth, sftp = remote
    folder = PurePosixPath("results")
    source = tmp_path / "run.slurm"
    payload = b"#!/bin/bash\n"
    source.write_bytes(payload)
    sftp.lstat.side_effect = FileNotFoundError
    sftp.stat.return_value.st_size = len(payload)

    upload_file(auth, folder, source)
    temporary, mode = sftp.open.call_args.args
    target = str(project_root(auth.username) / folder / source.name)
    assert mode == "wx"
    assert temporary.startswith(str(project_root(auth.username) / folder / ".rustmech-upload-"))
    sftp.open.return_value.write.assert_called_once_with(payload)
    sftp.rename.assert_called_once_with(temporary, target)

    # A second client makes the destination after the preflight check.
    sftp.lstat.side_effect = [FileNotFoundError, SimpleNamespace(st_mode=S_IFREG)]
    sftp.rename.side_effect = OSError("Failure")
    with pytest.raises(FileExistsError):
        upload_file(auth, folder, source)
    sftp.remove.assert_called_once_with(sftp.open.call_args.args[0])


def test_job_inputs_go_directly_into_open_folder_and_retry_safely(remote, tmp_path):
    auth, sftp = remote
    folder = PurePosixPath("project/run 1")
    target = project_root(auth.username) / folder
    (tmp_path / "data").mkdir()
    (tmp_path / "data/model.lmp").write_bytes(b"model")
    (tmp_path / "job.slurm").write_bytes(b"script")
    known = {}
    staged = {}

    def attrs(path):
        if path not in known:
            raise FileNotFoundError(path)
        contents = known[path]
        return SimpleNamespace(st_mode=S_IFDIR if contents is None else S_IFREG,
                               st_size=len(contents or b""))

    def open_file(path, mode):
        if mode == "rb":
            return BytesIO(known[path])
        staged[path] = bytearray()
        writer = MagicMock()
        writer.__enter__.return_value = writer
        writer.write.side_effect = staged[path].extend
        return writer

    sftp.lstat.side_effect = attrs
    sftp.mkdir.side_effect = lambda path: known.__setitem__(path, None)
    sftp.open.side_effect = open_file
    sftp.stat.side_effect = lambda path: SimpleNamespace(st_size=len(staged[path]))
    sftp.rename.side_effect = lambda path, final: known.__setitem__(final, bytes(staged.pop(path)))

    updates = []
    upload_tree(auth, folder, tmp_path, progress=lambda sent, total: updates.append((sent, total)))
    assert known == {
        str(target / "data"): None,
        str(target / "data/model.lmp"): b"model",
        str(target / "job.slurm"): b"script",
    }
    assert updates == [(0, 11), (5, 11), (11, 11)]
    assert [call.args[0] for call in sftp.lstat.call_args_list] == [str(target / "data"), str(target / "job.slurm")]
    assert not staged
    upload_tree(auth, folder, tmp_path)
    assert sftp.rename.call_count == 2

    (tmp_path / "extra.txt").touch()
    (tmp_path / "job.slurm").write_bytes(b"edited")
    with pytest.raises(FileExistsError, match="job.slurm"):
        upload_tree(auth, folder, tmp_path)
    assert str(target / "extra.txt") not in known
    assert known[str(target / "job.slurm")] == b"script"


@pytest.mark.parametrize("available_channels", [1, 3])
def test_job_upload_uses_bounded_channels_and_falls_back(remote, tmp_path, monkeypatch, available_channels):
    auth, _ = remote
    channels = []
    files = {f"part{index}.lmp": b"data" * index for index in range(1, 7)}
    for name, content in files.items():
        (tmp_path / name).write_bytes(content)

    @contextmanager
    def channel(_auth):
        if len(channels) == available_channels:
            raise SSHException("Channel limit")
        sftp = MagicMock()
        sftp.normalize.side_effect = str
        sftp.lstat.side_effect = FileNotFoundError
        channels.append(sftp)
        yield sftp

    first_write = Barrier(available_channels)
    used_channels = set()
    uploaded = {}

    def publish(sftp, source, target, *, progress):
        if sftp not in used_channels:
            used_channels.add(sftp)
            first_write.wait(timeout=5)
        uploaded[PurePosixPath(target).name] = source.read_bytes()
        progress(source.stat().st_size)

    monkeypatch.setattr("truba_files._sftp_channel", channel)
    monkeypatch.setattr("truba_files._publish_file", publish)
    updates = []
    upload_tree(auth, PurePosixPath(), tmp_path, progress=lambda sent, total: updates.append((sent, total)))
    total = sum(map(len, files.values()))
    assert len(used_channels) == available_channels
    assert uploaded == files
    assert updates[0] == (0, total)
    assert updates[-1] == (total, total)
    assert updates == sorted(updates)


def test_download_replaces_local_file_only_after_complete_transfer(remote, tmp_path):
    auth, sftp = remote
    target = tmp_path / "output.txt"
    target.write_bytes(b"previous contents")
    sftp.lstat.return_value.st_mode = S_IFREG

    def interrupted(_source, temporary, **_):
        temporary.write(b"partial contents")
        raise EOFError

    sftp.getfo.side_effect = interrupted
    with pytest.raises(EOFError):
        download_file(auth, PurePosixPath(), target.name, target)
    assert target.read_bytes() == b"previous contents"
    assert list(tmp_path.iterdir()) == [target]

    sftp.getfo.side_effect = lambda _source, temporary, **_: temporary.write(b"new contents")
    download_file(auth, PurePosixPath(), target.name, target)
    assert target.read_bytes() == b"new contents"
    assert sftp.getfo.call_args.args[0] == str(project_root(auth.username) / target.name)

    sftp.lstat.return_value.st_mode = S_IFLNK
    with pytest.raises(ValueError, match="regular file"):
        download_file(auth, PurePosixPath(), target.name, target)
    assert sftp.getfo.call_count == 2


def test_workspace_browses_creates_and_restores_three_columns(remote, tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    preferences = QSettings(str(tmp_path / "layout.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr("esen_workspace.QSettings", lambda *_: preferences)
    store = WindowStateStore(app, preferences)
    truba = Truba()
    auth, sftp = remote
    root = project_root(auth.username)
    folder = SimpleNamespace(filename="results", st_mode=S_IFDIR, st_size=0)
    file = SimpleNamespace(filename="readme.txt", st_mode=S_IFREG, st_size=1024)
    sftp.listdir_attr.side_effect = lambda path: [file, folder] if path == str(root) else []

    workspace = EsenWorkspace(truba, history=tmp_path)
    reopened = None
    try:
        workspace.show()
        app.processEvents()
        browser = workspace.truba_browser
        split = workspace.findChild(QSplitter, "esenThreeColumns")
        assert split.count() == 3
        assert split.widget(1).isAncestorOf(workspace.preview)
        assert split.widget(2).isAncestorOf(browser)
        assert not browser.refresh.isEnabled()
        assert not browser.upload.isEnabled()
        assert not browser.download.isEnabled()
        sftp.listdir_attr.assert_not_called()

        def finish_request():
            browser.task[0].result(timeout=5)
            browser.check_task()

        truba.auth = auth
        truba.connection_changed.emit(True)
        finish_request()
        assert browser.address.text() == str(root)
        assert browser.files.topLevelItem(0).text(0) == "results"
        assert browser.files.topLevelItem(1).text(0) == "readme.txt"
        browser.group_files.setChecked(True)
        folders, other = (browser.files.topLevelItem(index) for index in range(2))
        assert folders.child(0).text(0) == "results"
        assert other.child(0).text(0) == "readme.txt"
        browser.files.setCurrentItem(other.child(0))
        assert browser.download.isEnabled()
        browser.filter.setText("RESULT")
        assert other.isHidden() and not folders.isHidden()
        assert not browser.download.isEnabled()
        browser.filter.clear()
        browser.group_files.setChecked(False)
        assert not browser.up.isEnabled()
        assert browser.upload.isEnabled()
        assert not browser.download.isEnabled()
        browser.files.setCurrentItem(browser.files.topLevelItem(0))
        assert not browser.download.isEnabled()
        QTest.keyClick(browser.files, Qt.Key.Key_Return)
        finish_request()
        assert browser.address.text() == str(root / "results")

        browser.folder_name.setText("Yeni klasör")
        QTest.keyClick(browser.folder_name, Qt.Key.Key_Return)
        finish_request()
        finish_request()
        sftp.mkdir.assert_called_once_with(str(root / "results" / "Yeni klasör"))
        assert workspace.isVisible()
        assert not workspace.local_task and not workspace.remote_task

        source = tmp_path / "local.txt"
        source.write_bytes(b"local file")
        monkeypatch.setattr("truba_file_browser.QFileDialog.getOpenFileName",
                            lambda *_: (str(source), ""))
        sftp.lstat.side_effect = FileNotFoundError
        sftp.stat.return_value.st_size = source.stat().st_size
        uploaded = SimpleNamespace(filename=source.name, st_mode=S_IFREG, st_size=source.stat().st_size)
        sftp.listdir_attr.side_effect = lambda path: [file, folder] if path == str(root) else [uploaded]
        browser.upload.click()
        assert not browser.up.isEnabled()
        assert not browser.upload.isEnabled()
        finish_request()
        finish_request()
        assert sftp.rename.call_args.args[1] == str(root / "results" / source.name)
        assert browser.status.text() == "File uploaded to TRUBA."

        browser.files.setCurrentItem(browser.files.topLevelItem(0))
        assert browser.download.isEnabled()
        destination = tmp_path / "downloaded.txt"
        monkeypatch.setattr("truba_file_browser.QFileDialog.getSaveFileName",
                            lambda *_: (str(destination), ""))
        sftp.lstat.side_effect = None
        sftp.lstat.return_value.st_mode = S_IFREG
        sftp.getfo.side_effect = lambda _source, temporary, **_: temporary.write(b"remote file")
        browser.download.click()
        finish_request()
        assert sftp.getfo.call_args.args[0] == str(root / "results" / source.name)
        assert destination.read_bytes() == b"remote file"
        assert browser.status.text() == "File downloaded from TRUBA."

        browser.up.click()
        finish_request()
        assert browser.address.text() == str(root)
        truba.auth = None
        truba.connection_changed.emit(False)
        assert browser.files.topLevelItemCount() == 0
        assert not browser.folder_name.isEnabled()

        split.setSizes([343, 327, 263])
        app.processEvents()
        sizes = split.sizes()
        workspace.close()
        reopened = EsenWorkspace(truba, history=tmp_path)
        reopened.show()
        app.processEvents()
        assert reopened.size() == workspace.size()
        assert reopened.findChild(QSplitter, "esenThreeColumns").sizes() == sizes
    finally:
        if reopened is not None:
            reopened.dispose()
        workspace.dispose()
        app.removeEventFilter(store)
        store.deleteLater()
