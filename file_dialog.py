"""Datenburg-style pickers and standard Qt dialog buttons."""

import os
import sys

from PySide6.QtCore import QDir, QEvent, QFileInfo, QObject, QProcess, QSettings, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog as QtFileDialog,
    QFileSystemModel, QInputDialog, QMenu, QMessageBox, QWidget,
)

from datenburg_widgets import compact_gradient_button_stylesheet, qss_asset_url


_DIALOG_BUTTON_STYLE = compact_gradient_button_stylesheet(
    "QPushButton", "padding: 0 7px; min-height: 23px; max-height: 1000px;"
) + "\nQPushButton:focus { padding: 0 6px; }"


def _style_dialog_buttons(box):
    buttons = box.buttons()
    for button in buttons:
        button.setProperty("primary", button.isDefault()
                           or box.buttonRole(button) == QDialogButtonBox.ButtonRole.AcceptRole)
    if not box.property("rustmechDialogButtonsStyled"):
        box.setProperty("rustmechDialogButtonsStyled", True)
        box.setStyleSheet(box.styleSheet() + "\n" + _DIALOG_BUTTON_STYLE)
    for button in buttons:
        button.ensurePolished()
        # A QSS min-width can take the place of Qt's content-based size hint.
        metrics = button.fontMetrics()
        text_width = metrics.size(Qt.TextFlag.TextShowMnemonic, button.text()).width()
        button.setMinimumSize(max(77, button.minimumWidth(), text_width + 18),
                              max(25, button.minimumHeight(), metrics.height() + 6))


def _picker_stylesheet():
    arrow = qss_asset_url("spin-down-blue.svg")
    return f"""
QFileDialog {{ background: #F1F5F9; }}
#lookInLabel, #fileNameLabel, #fileTypeLabel {{ color: #334155; }}
#lookInCombo, #fileTypeCombo, #fileNameEdit {{
    background: white; color: #0F172A; border: 1px solid #CBD5E1;
    border-radius: 4px; min-height: 23px; max-height: 23px; padding: 0 6px;
}}
#lookInCombo QLineEdit {{
    background: white; color: #0F172A; border: 0; padding: 0;
}}
#lookInCombo:focus, #fileTypeCombo:focus, #fileNameEdit:focus {{ border-color: #3978BE; }}
#lookInCombo::drop-down, #fileTypeCombo::drop-down {{
    subcontrol-origin: padding; subcontrol-position: top right; width: 22px;
    border-left: 1px solid #CBD5E1;
}}
#lookInCombo::down-arrow, #fileTypeCombo::down-arrow {{ image: url("{arrow}"); width: 8px; height: 8px; }}
#stackedWidget QAbstractItemView {{
    background: white; color: #0F172A; border: 1px solid #CBD5E1;
    border-radius: 4px; selection-background-color: #D6E8FA; selection-color: #172B4D;
}}
#stackedWidget QAbstractItemView::item {{ min-height: 23px; border-bottom: 1px solid transparent; }}
#stackedWidget QAbstractItemView::item:selected {{
    background: #D6E8FA; color: #172B4D; border-bottom-color: #A9C7E5;
}}
#stackedWidget QHeaderView::section {{
    background: #E8EFF7; color: #334155; padding: 3px 6px;
    border: 0; border-right: 1px solid #CBD5E1; border-bottom: 1px solid #CBD5E1;
}}
#stackedWidget QScrollBar:vertical {{ width: 4px; margin: 1px 1px 1px 0; background: transparent; }}
#stackedWidget QScrollBar:horizontal {{ height: 4px; margin: 0 1px 1px; background: transparent; }}
#stackedWidget QScrollBar::handle {{ background: #64748B; border: 0; }}
#stackedWidget QScrollBar::handle:vertical {{ min-height: 15px; }}
#stackedWidget QScrollBar::handle:horizontal {{ min-width: 15px; }}
#stackedWidget QScrollBar::add-line, #stackedWidget QScrollBar::sub-line {{ width: 0; height: 0; }}
#stackedWidget QScrollBar::add-page, #stackedWidget QScrollBar::sub-page {{ background: transparent; }}
QFileDialog QToolButton {{
    background: white; border: 1px solid #CBD5E1; border-radius: 4px;
    min-width: 23px; min-height: 23px; padding: 0;
}}
QFileDialog QToolButton:hover, QFileDialog QToolButton:focus {{ border-color: #3978BE; }}
QFileDialog QToolButton:pressed, QFileDialog QToolButton:checked {{ background: #D6E8FA; }}
QFileDialog QToolButton:disabled {{ background: #E9EDF2; }}
"""


class FileDialogControls(QObject):
    """Extend the Qt picker while keeping its selection and accept logic."""

    def __init__(self, dialog, address):
        super().__init__(dialog)
        self.dialog = dialog
        sidebar = dialog.findChild(QWidget, "sidebar")
        if sidebar is not None:
            sidebar.hide()

        address.setEditable(True)
        address.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.editor = address.lineEdit()
        self.editor.setReadOnly(False)
        self.editor.setCompleter(None)
        dialog.directoryEntered.connect(self.sync_address)
        self.sync_address()

        back = dialog.findChild(QAbstractButton, "backButton")
        forward = dialog.findChild(QAbstractButton, "forwardButton")
        self.mouse_navigation = {Qt.MouseButton.BackButton: back, Qt.MouseButton.ForwardButton: forward}
        self.keyboard_navigation = {
            Qt.Key.Key_Left: back, Qt.Key.Key_Right: forward,
            Qt.Key.Key_Up: dialog.findChild(QAbstractButton, "toParentButton"),
        }
        for widget in (dialog, *dialog.findChildren(QWidget)):
            widget.installEventFilter(self)
        for name in ("listView", "treeView"):
            view = dialog.findChild(QAbstractItemView, name)
            if view is not None:
                view.customContextMenuRequested.connect(lambda point, view=view: self.extend_menu(view, point))

        dialog.setStyleSheet(_picker_stylesheet())
        _style_dialog_buttons(dialog.findChild(QDialogButtonBox, "buttonBox"))

    def sync_address(self, _path=""):
        self.editor.setText(QDir.toNativeSeparators(self.dialog.directory().absolutePath()))
        self.editor.setCursorPosition(0)

    def focus_address(self):
        self.editor.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.editor.selectAll()

    def navigate_to_address(self):
        path = QDir.fromNativeSeparators(self.editor.text().strip().strip('"'))
        if path:
            target = QDir(self.dialog.directory().absoluteFilePath(path))
            if target.exists():
                self.dialog.setDirectory(target.absolutePath())
                self.sync_address()
        self.editor.selectAll()

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            if watched is self.editor and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if kind == QEvent.Type.KeyPress:
                    self.navigate_to_address()
                event.accept()
                return True
            if (event.key(), event.modifiers()) in (
                (Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier),
                (Qt.Key.Key_D, Qt.KeyboardModifier.AltModifier),
            ):
                if kind == QEvent.Type.KeyPress:
                    self.focus_address()
                event.accept()
                return True
            button = (self.keyboard_navigation.get(event.key())
                      if event.modifiers() == Qt.KeyboardModifier.AltModifier else None)
        elif kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            button = self.mouse_navigation.get(event.button())
        else:
            return False
        if button is None:
            return False
        if kind in (QEvent.Type.MouseButtonRelease, QEvent.Type.KeyPress):
            button.click()
        event.accept()
        return True

    def extend_menu(self, view, point):
        index = view.indexAt(point)
        path = index.data(QFileSystemModel.Roles.FilePathRole)
        if not path or not QFileInfo(path).isFile():
            return
        # Qt's own connection creates the standard menu before this slot runs.
        for menu in view.findChildren(QMenu, options=Qt.FindChildOption.FindDirectChildrenOnly):
            if menu.isVisible() and menu.actions():
                first = menu.actions()[0]
                open_action = menu.addAction(self.tr("Open"), lambda: self.open_file(path, reveal=False))
                location_action = menu.addAction(self.tr("Open file location"), lambda: self.open_file(path, reveal=True))
                menu.insertAction(first, open_action)
                menu.insertAction(first, location_action)
                menu.insertSeparator(first)
                break

    def open_file(self, path, *, reveal):
        info = QFileInfo(path)
        if info.isFile():
            if reveal and sys.platform == "win32":
                explorer = QProcess()
                explorer.setProgram(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe"))
                explorer.setNativeArguments(f'/select,"{QDir.toNativeSeparators(path)}"')
                opened, _ = explorer.startDetached()
            else:
                url = QUrl.fromLocalFile(info.absolutePath() if reveal else path)
                opened = QDesktopServices.openUrl(url)
            if opened:
                return
        QMessageBox.warning(self.dialog, self.tr("File"), self.tr("Could not open %1.").replace("%1", path))


class FileDialogService(QObject):
    def eventFilter(self, widget, event):
        if event.type() not in (QEvent.Type.Polish, QEvent.Type.Show):
            return False
        if isinstance(widget, (QInputDialog, QMessageBox)):
            buttons = widget.findChild(QDialogButtonBox)
            if buttons is not None:
                _style_dialog_buttons(buttons)
            if isinstance(widget.parentWidget(), QFileDialog) and not widget.styleSheet():
                widget.setStyleSheet("QDialog { background: #F1F5F9; }")
        return False


class QFileDialog(QtFileDialog):
    """Own the widget explicitly so native window controls attach before exec."""

    @classmethod
    def _choose(cls, parent, title, directory, filters, mode, *, save=False):
        app = QApplication.instance()
        store = getattr(app, "_rustmech_window_state", None)
        settings = store.settings if store is not None else QSettings("RustMech", "RustMech")
        owner = type(parent).__name__ if parent is not None else "default"
        action = "save" if save else "open"
        key = f"fileDialogs/{owner}/{action}/{mode.name}/{title or 'default'}/directory"
        remembered = settings.value(key, "", type=str)
        folder_mode = mode == cls.FileMode.Directory
        if (remembered and QDir(remembered).exists()
                and not (folder_mode and directory and QFileInfo(directory).isDir())):
            suggestion = QFileInfo(directory)
            if save and directory and not suggestion.isDir():
                directory = QDir(remembered).filePath(suggestion.fileName())
            else:
                directory = remembered

        dialog = cls(parent, title, directory)
        dialog.setOption(cls.Option.DontUseNativeDialog)
        dialog.setAcceptMode(cls.AcceptMode.AcceptSave if save else cls.AcceptMode.AcceptOpen)
        dialog.setFileMode(mode)
        if filters:
            # Directory mode resets the filter selector to "Directories".
            dialog.setNameFilter(filters)
        address = dialog.findChild(QComboBox, "lookInCombo")
        dialog._file_controls = FileDialogControls(dialog, address)
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            files = dialog.selectedFiles() if accepted else []
            visited = dialog.directory().absolutePath()
            if files:
                visited = files[0] if folder_mode else QFileInfo(files[0]).absolutePath()
            if QDir(visited).exists():
                settings.setValue(key, visited)
                settings.sync()
            if accepted:
                return files, dialog.selectedNameFilter()
            return [], ""
        finally:
            dialog.deleteLater()

    @classmethod
    def getOpenFileName(cls, parent=None, caption="", dir="", filter=""):
        files, selected_filter = cls._choose(parent, caption, dir, filter, cls.FileMode.ExistingFile)
        return (files[0] if files else ""), selected_filter

    @classmethod
    def getOpenFileNames(cls, parent=None, caption="", dir="", filter=""):
        return cls._choose(parent, caption, dir, filter, cls.FileMode.ExistingFiles)

    @classmethod
    def getSaveFileName(cls, parent=None, caption="", dir="", filter=""):
        files, selected_filter = cls._choose(parent, caption, dir, filter, cls.FileMode.AnyFile, save=True)
        return (files[0] if files else ""), selected_filter

    @classmethod
    def getExistingDirectory(cls, parent=None, caption="", dir="", *, filter=""):
        folders, _ = cls._choose(parent, caption, dir, filter, cls.FileMode.Directory)
        return folders[0] if folders else ""


def install_file_dialogs(app):
    if not hasattr(app, "_rustmech_file_dialog_service"):
        app._rustmech_file_dialog_service = FileDialogService(app)
        app.installEventFilter(app._rustmech_file_dialog_service)
