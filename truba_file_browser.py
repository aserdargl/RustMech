"""Compact TRUBA project browser for the ESEN workspace."""

import errno
from pathlib import PurePosixPath
from stat import S_ISDIR, S_ISLNK

from PySide6.QtCore import QLocale, Qt
from PySide6.QtWidgets import (
    QApplication, QHeaderView, QHBoxLayout, QLabel, QLineEdit, QStyle,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from studio_style import ActionFlowLayout, CONTROL_HEIGHT
import truba_files
from ui_language import translate


class TrubaFileBrowser(QWidget):
    def __init__(self, truba, executor, command):
        super().__init__()
        self.truba = truba
        self.executor = executor
        self.auth = None
        self.directory = PurePosixPath()
        self.task = None
        self.loaded = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.address = QLineEdit()
        self.address.setReadOnly(True)
        self.address.setFixedHeight(CONTROL_HEIGHT)
        self.address.setAccessibleName("TRUBA folder path")
        layout.addWidget(self.address)

        self.up = command("Up", self._up)
        self.refresh = command("Refresh", self._refresh)
        actions = ActionFlowLayout(equal_width=True)
        actions.addWidget(self.up)
        actions.addWidget(self.refresh)
        layout.addLayout(actions)

        self.files = QTreeWidget()
        self.files.setAccessibleName("TRUBA files")
        self.files.setProperty("preserveItemText", True)
        self.files.setRootIsDecorated(False)
        self.files.setUniformRowHeights(True)
        self.files.setColumnCount(2)
        self.files.header().setStretchLastSection(False)
        self.files.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.files.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.files.itemActivated.connect(self._open_folder)
        layout.addWidget(self.files, 1)

        self.folder_name = QLineEdit()
        self.folder_name.setFixedHeight(CONTROL_HEIGHT)
        self.folder_name.setPlaceholderText("Folder name")
        self.folder_name.setAccessibleName("Folder name")
        self.folder_name.returnPressed.connect(self._create_folder)
        self.folder_name.textChanged.connect(self._update_controls)
        self.create = command("New folder", self._create_folder)
        row = QHBoxLayout()
        row.addWidget(self.folder_name, 1)
        row.addWidget(self.create)
        layout.addLayout(row)

        self.status = QLabel("Connect using the main TRUBA button.")
        self.status.setObjectName("mutedHintLabel")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._set_headers()
        language = getattr(QApplication.instance(), "_rustmech_language", None)
        if language is not None:
            language.changed.connect(self._set_headers)
        self.truba.connection_changed.connect(self._sync_connection)
        self._update_controls()

    def _set_headers(self, *_):
        self.files.setHeaderLabels([translate("Name"), translate("Size")])

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_connection()
        if self.auth is not None and not self.loaded and self.task is None:
            self._refresh()

    def _sync_connection(self, *_):
        auth = self.truba.analysis_auth()
        if auth == self.auth:
            return
        if self.task is not None:
            self.task[0].cancel()
            self.task = None
        self.auth = auth
        self.directory = PurePosixPath()
        self.loaded = False
        self.files.clear()
        self.folder_name.clear()
        self._update_address()
        self.status.setText("Connect using the main TRUBA button." if auth is None else "")
        self._update_controls()
        if auth is not None and self.isVisible():
            self._refresh()

    def _update_address(self):
        path = str(truba_files.project_root(self.auth.username) / self.directory) if self.auth else ""
        self.address.setText(path)
        self.address.setToolTip(path)
        self.address.setCursorPosition(0)

    def _update_controls(self, *_):
        ready = self.auth is not None and self.task is None
        can_create = ready and self.loaded
        self.refresh.setEnabled(ready)
        self.up.setEnabled(ready and self.directory != PurePosixPath())
        self.folder_name.setEnabled(can_create)
        self.create.setEnabled(can_create and bool(self.folder_name.text().strip()))

    def _submit(self, operation, directory, name=None):
        if self.auth is None or self.task is not None:
            return
        args = () if name is None else (name,)
        future = self.executor.submit(operation, self.auth, directory, *args)
        self.task = (future, directory, name)
        self.status.setText("Loading TRUBA files…" if name is None else "Creating folder…")
        self._update_controls()

    def _refresh(self):
        self._submit(truba_files.list_directory, self.directory)

    def _up(self):
        self._submit(truba_files.list_directory, self.directory.parent)

    def _open_folder(self, item, _column):
        if item.data(0, Qt.ItemDataRole.UserRole):
            self._submit(truba_files.list_directory, self.directory / item.text(0))

    def _create_folder(self):
        if not self.create.isEnabled():
            return
        name = self.folder_name.text().strip()
        try:
            truba_files.validate_folder_name(name)
        except ValueError as error:
            self.status.setText(str(error))
            return
        self._submit(truba_files.create_directory, self.directory, name)

    def check_task(self):
        task = self.task
        if task is None or not task[0].done():
            return
        self._sync_connection()
        if self.task is not task:
            return
        future, directory, name = task
        self.task = None
        try:
            entries = future.result()
        except ValueError as error:
            self.status.setText(str(error))
        except ConnectionError:
            self.status.setText("Connect using the main TRUBA button.")
        except Exception as error:
            code = getattr(error, "errno", None)
            if code == errno.ENOENT:
                message = "TRUBA folder was not found."
            elif code in (errno.EACCES, errno.EPERM):
                message = "Permission denied on TRUBA."
            else:
                message = ("Could not load TRUBA files." if name is None
                           else "Could not create the folder. Check if the name already exists.")
            self.status.setText(message)
        else:
            if name is not None:
                self.folder_name.clear()
                self._refresh()
                return
            self.directory = directory
            self.loaded = True
            self._update_address()
            self._show_entries(entries)
            self.status.setText("" if entries else "This folder is empty.")
        self._update_controls()

    def _show_entries(self, entries):
        self.files.clear()
        folder_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        file_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        link_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileLinkIcon)
        locale = QLocale()
        rows = []
        for entry in entries:
            mode = entry.st_mode or 0
            folder = S_ISDIR(mode)
            size = "" if folder or entry.st_size is None else locale.formattedDataSize(entry.st_size)
            row = QTreeWidgetItem([entry.filename, size])
            row.setData(0, Qt.ItemDataRole.UserRole, folder)
            row.setToolTip(0, entry.filename)
            row.setIcon(0, folder_icon if folder else link_icon if S_ISLNK(mode) else file_icon)
            rows.append(row)
        self.files.addTopLevelItems(rows)
