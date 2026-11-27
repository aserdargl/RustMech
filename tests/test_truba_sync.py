from pathlib import Path
from stat import S_IFDIR, S_IFREG
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from truba_desktop import DesktopStatus
from truba_shared_session import DesktopSession
from truba_sync import TrubaSession, TrubaSyncWindow
from ui_language import LanguageManager


@pytest.fixture
def sync_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    language = LanguageManager(app, settings)
    session = TrubaSession(None)
    session.state_dir = tmp_path
    window = TrubaSyncWindow(session, language)
    window.show()
    try:
        yield window
    finally:
        window.shutdown()
        window.close()
        language.shutdown()


def wait_until(predicate):
    for _ in range(150):
        if predicate():
            return True
        QTest.qWait(10)
    return False


def test_sync_local_pickers_show_nested_folders_without_a_saved_job(sync_window, tmp_path):
    window = sync_window
    inputs, outputs = tmp_path / "source", tmp_path / "destination"
    nested = inputs / "geometry"
    nested.mkdir(parents=True)
    outputs.mkdir()
    (nested / "custom.step").touch()
    (outputs / "report.txt").touch()
    assert window.outputs.isVisible()
    assert not window.remote_button.isEnabled()
    for button, view in ((window.input_button, window.inputs), (window.remote_button, window.remote),
                         (window.output_button, window.outputs)):
        assert not button.icon().isNull()
        assert button.geometry().top() > view.geometry().bottom()
        assert button.size() == window.input_button.size()

    with patch("truba_sync.QFileDialog.getExistingDirectory", side_effect=[str(inputs), str(outputs), ""]):
        window.input_button.click()
        window.output_button.click()
        window.input_button.click()
    window.refresh_button.click()

    source = window.inputs.file_model
    destination = window.outputs.file_model
    assert Path(window.input_path.text()) == inputs
    assert Path(window.output_path.text()) == outputs
    assert wait_until(lambda: source.index(str(nested)).isValid())
    child = source.index(str(nested))
    window.inputs.expand(child)
    assert wait_until(lambda: source.index(str(nested / "custom.step")).isValid())
    assert source.index(str(nested / "custom.step")).parent() == child
    assert wait_until(lambda: destination.index(str(outputs / "report.txt")).isValid())


def test_sync_remote_picker_navigates_and_selects_a_folder_without_a_saved_job(sync_window):
    window = sync_window
    session = DesktopSession("tester", 10000, "a" * 64)
    root = "/arf/scratch/tester"
    child = root + "/project"
    with patch("truba_sync.DesktopSSHClient") as desktop:
        sftp = desktop.return_value.open_sftp.return_value.__enter__.return_value
        listings = {
            root: [SimpleNamespace(filename="project", st_mode=S_IFDIR)],
            child: [SimpleNamespace(filename="results.txt", st_mode=S_IFREG)],
        }
        sftp.listdir_attr.side_effect = listings.__getitem__
        window.session._status_changed(DesktopStatus(session))
        assert window.remote_button.isEnabled()
        assert wait_until(lambda: window.remote.worker is None)
        assert window.remote.topLevelItem(0).text(0) == "project"

        picker = window.folder_dialog
        observed = []

        def select_folder():
            if wait_until(picker.select_button.isEnabled):
                picker.view.itemActivated.emit(picker.view.topLevelItem(0), 0)
                if wait_until(picker.select_button.isEnabled):
                    picker.path.setText(child)
                    QTest.keyClick(picker.path, Qt.Key.Key_Return)
                    if picker.isVisible() and wait_until(picker.select_button.isEnabled):
                        observed.append(picker.view.topLevelItem(0).text(0))
                        picker.select_button.click()
                        return
            picker.reject()

        QTimer.singleShot(0, select_folder)
        window.remote_button.click()
        assert observed == ["results.txt"]
        assert window.remote_path.text() == child
        assert wait_until(lambda: window.remote.worker is None)
        assert window.remote.topLevelItem(0).text(0) == "results.txt"
        window.refresh_button.click()
        assert wait_until(lambda: window.remote.worker is None)
        assert window.remote_path.text() == child
