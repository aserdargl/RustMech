"""Exercise the shared picker through the application's file-selection entry points."""

import ctypes
from ctypes import wintypes
from pathlib import Path

import pytest
from PySide6.QtCore import QDir, QPoint, QSettings, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialogButtonBox, QInputDialog, QLineEdit, QMessageBox,
    QStackedWidget, QWidget,
    QStyle, QStyleOptionButton,
)

from file_dialog import QFileDialog, install_file_dialogs
from studio_style import STUDIO_STYLESHEET
from window_caption import CAPTION_PROPERTY, install_window_captions
from window_state import WindowStateStore


@pytest.fixture
def picker_app(tmp_path):
    app = QApplication.instance() or QApplication([])
    attribute = Qt.ApplicationAttribute.AA_DontUseNativeDialogs
    previous = app.testAttribute(attribute)
    install_window_captions(app)
    install_file_dialogs(app)
    store = WindowStateStore(app, QSettings(str(tmp_path / "windows.ini"), QSettings.Format.IniFormat))
    previous_store = getattr(app, "_rustmech_window_state", None)
    app._rustmech_window_state = store
    yield app
    app.removeEventFilter(store)
    store.deleteLater()
    if previous_store is None:
        del app._rustmech_window_state
    else:
        app._rustmech_window_state = previous_store
    for name in ("_rustmech_file_dialog_service", "_rustmech_caption_service"):
        service = getattr(app, name)
        app.removeEventFilter(service)
        if frame := getattr(service, "_native_frame", None):
            app.removeNativeEventFilter(frame)
        delattr(app, name)
        service.deleteLater()
    app.setAttribute(attribute, previous)


@pytest.mark.parametrize("method", ["getOpenFileName", "getOpenFileNames", "getSaveFileName", "getExistingDirectory"])
def test_picker_navigation_and_geometry(picker_app, tmp_path, method):
    nested = tmp_path / "a folder"
    nested.mkdir()
    owner = QWidget()
    owner.show()
    visits = []

    def inspect():
        dialog = picker_app.activeModalWidget()
        try:
            combo = dialog.findChild(QComboBox, "lookInCombo")
            address = combo.lineEdit()
            sidebar = dialog.findChild(QWidget, "sidebar")
            files = dialog.findChild(QStackedWidget, "stackedWidget")
            caption = dialog.property(CAPTION_PROPERTY)
            if not visits:
                dialog.resize(710, 465)
                dialog.move(90, 105)
            geometry = dialog.geometry()
            filename = dialog.findChild(QLineEdit, "fileNameEdit")
            filename.setFocus()
            QTest.keyClick(filename, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier)
            shortcut_worked = dialog.focusWidget() in (combo, address) and address.selectedText() == address.text()
            address.setText(nested.name)
            QTest.keyClick(address, Qt.Key.Key_Return)
            after_enter = Path(dialog.directory().absolutePath())
            QTest.keyClick(address, Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)
            after_back = Path(dialog.directory().absolutePath())
            QTest.keyClick(address, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
            after_forward = Path(dialog.directory().absolutePath())
            QTest.keyClick(address, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
            visits.append((geometry, sidebar.isHidden(), files.x(), caption.height(), shortcut_worked,
                           after_enter, after_back, after_forward,
                           Path(QDir.fromNativeSeparators(address.text())), dialog.isVisible()))
        finally:
            dialog.reject()

    try:
        for _ in range(2):
            QTimer.singleShot(0, inspect)
            getattr(QFileDialog, method)(owner, "Choose", str(tmp_path))
        assert visits[0] == visits[1]
        _, hidden, file_list_left, caption_height, shortcut, entered, back, forward, address, still_open = visits[0]
        assert hidden and file_list_left < 25
        assert caption_height == 25 and shortcut and still_open
        assert (entered, back, forward, address) == (nested, tmp_path, nested, tmp_path)
    finally:
        owner.close()


def test_picker_remembers_each_actions_directory_on_cancel(picker_app, tmp_path):
    nested = tmp_path / "last visited"
    nested.mkdir()
    visits = []

    def visit(*, navigate=False):
        dialog = picker_app.activeModalWidget()
        try:
            field = dialog.findChild(QLineEdit, "fileNameEdit")
            visits.append((Path(dialog.directory().absolutePath()), field.text()))
            if navigate:
                address = dialog.findChild(QComboBox, "lookInCombo").lineEdit()
                address.setText(str(nested))
                QTest.keyClick(address, Qt.Key.Key_Return)
        finally:
            dialog.reject()

    QTimer.singleShot(0, lambda: visit(navigate=True))
    QFileDialog.getSaveFileName(None, "Export", str(tmp_path / "original.csv"), "CSV (*.csv)")

    # Reload settings from disk, as on the next application launch.
    store = picker_app._rustmech_window_state
    store.settings = QSettings(store.settings.fileName(), QSettings.Format.IniFormat)
    QTimer.singleShot(0, visit)
    QFileDialog.getSaveFileName(None, "Export", str(tmp_path / "next.csv"), "CSV (*.csv)")
    QTimer.singleShot(0, visit)
    QFileDialog.getSaveFileName(None, "Other export", str(tmp_path / "other.csv"), "CSV (*.csv)")
    nested.rmdir()
    QTimer.singleShot(0, visit)
    QFileDialog.getSaveFileName(None, "Export", str(tmp_path / "fallback.csv"), "CSV (*.csv)")

    assert visits == [(tmp_path, "original.csv"), (nested, "next.csv"),
                      (tmp_path, "other.csv"), (tmp_path, "fallback.csv")]


def test_picker_appends_extension_from_selected_export_format(picker_app, tmp_path):
    def choose_format():
        dialog = picker_app.activeModalWidget()
        dropdown = dialog.findChild(QComboBox, "fileTypeCombo")
        dropdown.setFocus()
        QTest.keyClick(dropdown, Qt.Key.Key_End)
        dialog.accept()

    QTimer.singleShot(0, choose_format)
    path, selected = QFileDialog.getSaveFileName(
        None, "Export mesh", str(tmp_path / "mesh"), "ANSYS (*.cdb);;Abaqus (*.inp)",
        default_suffix="cdb",
    )
    assert Path(path) == tmp_path / "mesh.inp"
    assert selected == "Abaqus (*.inp)"


@pytest.mark.parametrize("input_kind", ["mouse", "browser_keys", "win32_mouse", "win32_caption", "win32_command"])
def test_picker_side_button_navigation(picker_app, tmp_path, input_kind):
    if input_kind.startswith("win32_") and picker_app.platformName() != "windows":
        pytest.skip("Requires the native Windows Qt backend")
    first = tmp_path / "first"
    second = first / "second"
    second.mkdir(parents=True)
    visits = []

    def inspect():
        dialog = picker_app.activeModalWidget()
        try:
            address = dialog.findChild(QComboBox, "lookInCombo").lineEdit()
            files = dialog.findChild(QAbstractItemView, "listView").viewport()
            address.setFocus()
            dialog.setDirectory(str(first))
            dialog.setDirectory(str(second))
            for back in (True, True, False, False, False):
                direction = 1 if back else 2
                if input_kind == "mouse":
                    QTest.mouseClick(files, Qt.MouseButton.BackButton if back else Qt.MouseButton.ForwardButton)
                elif input_kind == "browser_keys":
                    QTest.keyClick(address, Qt.Key.Key_Back if back else Qt.Key.Key_Forward)
                else:
                    send = ctypes.windll.user32.SendMessageW
                    send.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
                    send.restype = wintypes.LPARAM
                    hwnd = int(dialog.winId())
                    if input_kind == "win32_command":
                        send(hwnd, 0x0319, hwnd, (0x8000 | direction) << 16)  # WM_APPCOMMAND
                    elif input_kind == "win32_caption":
                        point = dialog.mapToGlobal(QPoint(50, 10))
                        coords = (point.y() & 0xFFFF) << 16 | (point.x() & 0xFFFF)
                        send(hwnd, 0x00AB, direction << 16 | 2, coords)  # WM_NCXBUTTONDOWN, HTCAPTION
                        send(hwnd, 0x00AC, direction << 16 | 2, coords)  # WM_NCXBUTTONUP
                    else:
                        point = files.mapTo(dialog, files.rect().center()) * dialog.devicePixelRatioF()
                        coords = point.y() << 16 | point.x()
                        send(hwnd, 0x020B, direction << 16 | (0x20 * direction), coords)  # WM_XBUTTONDOWN
                        send(hwnd, 0x020C, direction << 16, coords)  # WM_XBUTTONUP
                visits.append(Path(QDir.fromNativeSeparators(address.text())))
        finally:
            dialog.reject()

    QTimer.singleShot(0, inspect)
    QFileDialog.getExistingDirectory(None, "Choose", str(tmp_path))
    assert visits == [first, tmp_path, first, second, second]


@pytest.mark.parametrize("entry", [
    "getExistingDirectory", "getOpenFileName", "getOpenFileNames", "getSaveFileName", "message", "input",
])
def test_dialog_buttons_fit_large_localized_text(picker_app, tmp_path, entry):
    owner = QWidget()
    owner.setStyleSheet(STUDIO_STYLESHEET + "\nQPushButton { font-size: 26px; }")
    measured = []

    def measure():
        dialog = picker_app.activeModalWidget()
        box = dialog.findChild(QDialogButtonBox)
        try:
            for button in box.buttons():
                metrics = button.fontMetrics()
                width = metrics.size(Qt.TextFlag.TextShowMnemonic, button.text()).width()
                option = QStyleOptionButton()
                button.initStyleOption(option)
                option.state |= QStyle.StateFlag.State_HasFocus
                content = button.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, button)
                measured.append((button.size(), content.width(), content.height(), width, metrics.height()))
        finally:
            dialog.reject()

    def relabel():
        dialog = picker_app.activeModalWidget()
        box = dialog.findChild(QDialogButtonBox)
        for button in box.buttons():
            button.setText("Bu klasörü &seç" if button.property("primary") else "İptal")
        for _ in range(20):
            if all(button.width() >= button.sizeHint().width() for button in box.buttons()):
                break
            QTest.qWait(10)
        measure()

    previous_font = picker_app.font()
    picker_app.setFont(QFont("Segoe UI", 15))
    try:
        if entry == "message":
            prompt = QMessageBox(QMessageBox.Icon.Question, "Confirm", "Use this folder?",
                                 QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel, owner)
            prompt.button(QMessageBox.StandardButton.Ok).setText("Bu klasörü &seç")
            prompt.button(QMessageBox.StandardButton.Cancel).setText("İptal")
            QTimer.singleShot(0, measure)
            prompt.exec()
        elif entry == "input":
            prompt = QInputDialog(owner)
            prompt.setLabelText("Folder name")
            prompt.setOkButtonText("Klasör adını &onayla")
            prompt.setCancelButtonText("İptal")
            QTimer.singleShot(0, measure)
            prompt.exec()
        else:
            QTimer.singleShot(0, relabel)
            getattr(QFileDialog, entry)(owner, "Choose", str(tmp_path))
    finally:
        picker_app.setFont(previous_font)

    assert len(measured) == 2
    if entry.startswith("get"):
        assert measured[0][0] == measured[1][0]
    assert all(width >= text_width and height >= text_height
               for _, width, height, text_width, text_height in measured), measured
