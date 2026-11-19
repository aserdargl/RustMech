"""Cover the SFTP browser's project boundary and desktop entry points."""

from pathlib import PurePosixPath
from stat import S_IFDIR, S_IFREG
from types import SimpleNamespace
from unittest.mock import MagicMock

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSplitter
import pytest

from esen_workspace import EsenWorkspace
from truba_files import create_directory, list_directory, project_root
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
        assert not browser.up.isEnabled()
        browser.files.setCurrentItem(browser.files.topLevelItem(0))
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
