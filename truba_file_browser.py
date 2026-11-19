"""Compact TRUBA project browser for the ESEN workspace."""

import errno
from pathlib import Path, PurePosixPath
import re
from stat import S_ISDIR, S_ISLNK, S_ISREG

from PySide6.QtCore import QLocale, Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QHeaderView, QHBoxLayout, QLabel, QLineEdit, QStyle,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from file_dialog import QFileDialog
from esen_jobs import RUN_LIST, is_input_script, is_slurm_script
from studio_style import ActionFlowLayout, CONTROL_HEIGHT
import truba_files
from ui_language import translate


BUSY_MESSAGES = {
    "list": "Loading TRUBA files…",
    "mkdir": "Creating folder…",
    "upload": "Uploading file to TRUBA…",
    "download": "Downloading file from TRUBA…",
}
FAILURE_MESSAGES = {
    "list": "Could not load TRUBA files.",
    "mkdir": "Could not create the folder. Check if the name already exists.",
    "upload": "Could not upload the file to TRUBA.",
    "download": "Could not download the file from TRUBA.",
}


class TrubaFileBrowser(QWidget):
    def __init__(self, truba, executor, command, preferences):
        super().__init__()
        self.truba = truba
        self.executor = executor
        self.preferences = preferences
        self.auth = None
        self.directory = PurePosixPath()
        self._initial_directory = PurePosixPath()
        self.task = None
        self.loaded = False
        self._uploaded_directory = None
        self._slurm_directory = None
        self.entries = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.address = QLineEdit()
        self.address.setReadOnly(True)
        self.address.setFixedHeight(CONTROL_HEIGHT)
        self.address.setAccessibleName("TRUBA folder path")
        layout.addWidget(self.address)

        self.up = command("Up", self._up)
        self.open_folder = command("Open", self._open_selected_folder, hint="Open selected TRUBA folder")
        self.refresh = command("Refresh", self._refresh)
        self.slurm_inputs = command("Slurm inputs", self._open_slurm_inputs, hint="Open synchronized Slurm inputs on TRUBA")
        self.navigation_buttons = self.up, self.open_folder, self.refresh, self.slurm_inputs
        actions = QHBoxLayout()
        actions.setSpacing(4)
        for button in self.navigation_buttons:
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)

        filters = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setObjectName("esenFileFilter")
        self.filter.setFixedHeight(CONTROL_HEIGHT)
        self.filter.setClearButtonEnabled(True)
        self.filter.setPlaceholderText("Filter by name or extension")
        self.filter.setAccessibleName("Filter TRUBA files")
        self.filter.textChanged.connect(self._filter_entries)
        filters.addWidget(self.filter, 1)
        self.group_files = QCheckBox("Group by type")
        self.group_files.toggled.connect(self._render_entries)
        filters.addWidget(self.group_files)
        layout.addLayout(filters)

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
        self.files.itemSelectionChanged.connect(self._update_controls)
        layout.addWidget(self.files, 1)

        self.upload = command("Upload", self._upload_file, hint="Upload file to the open TRUBA folder")
        self.download = command("Download", self._download_file, hint="Download selected TRUBA file")
        transfers = ActionFlowLayout(equal_width=True)
        transfers.addWidget(self.upload)
        transfers.addWidget(self.download)
        layout.addLayout(transfers)

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
        if self.auth is not None and self.task is None:
            self._uploaded_directory = None
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
        self._initial_directory = PurePosixPath()
        if auth is not None:
            saved = PurePosixPath(self.preferences.value(self._directory_key(), "", type=str))
            if not saved.is_absolute() and ".." not in saved.parts and "\0" not in str(saved):
                self._initial_directory = saved
        self.loaded = False
        self._uploaded_directory = None
        self._show_entries([])
        self.filter.clear()
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

    def _directory_key(self):
        account = self.auth.username.encode("utf-8").hex()
        return f"esen/trubaFolders/{account}"

    @property
    def restoring_directory(self):
        return not self.loaded and bool(self._initial_directory.parts)

    def _update_controls(self, *_):
        ready = self.auth is not None and self.task is None
        can_change = ready and self.loaded
        can_write = can_change and not self.is_slurm_snapshot_open()
        self.refresh.setEnabled(ready)
        self.up.setEnabled(ready and self.directory != PurePosixPath())
        self.open_folder.setEnabled(can_change and self._selected_item(S_ISDIR) is not None)
        self.slurm_inputs.setEnabled(ready and self._slurm_directory is not None
                                     and self._slurm_directory[0] == self.auth.username)
        self.folder_name.setEnabled(can_write)
        self.create.setEnabled(can_write and bool(self.folder_name.text().strip()))
        self.upload.setEnabled(can_write)
        self.download.setEnabled(can_change and self._selected_item(S_ISREG) is not None)

    def _submit(self, kind, operation, directory, *args, success_message=""):
        if self.auth is None or self.task is not None:
            return
        future = self.executor.submit(operation, self.auth, directory, *args)
        self.task = (future, directory, kind, success_message)
        self.status.setText(BUSY_MESSAGES[kind])
        self._update_controls()

    def _refresh(self, *, success_message=""):
        directory = self._initial_directory if self.restoring_directory else self.directory
        self._submit("list", truba_files.list_directory, directory, success_message=success_message)

    def open_directory(self, auth):
        if self.auth == auth and self.directory.parts:
            return self.directory.as_posix()
        return ""

    def selected_file(self, auth):
        if auth is not None and self.auth == auth and self.loaded:
            if item := self._selected_item(S_ISREG):
                return self.directory, item.text(0)
        return None

    def is_slurm_snapshot_open(self):
        parts = self.directory.parts
        return any(parent == "esen-folders" and re.fullmatch(r"[0-9a-f]{32}", name)
                   for parent, name in zip(parts, parts[1:]))

    def refresh_after_upload(self, auth, directory):
        if self.auth == auth:
            self._uploaded_directory = PurePosixPath(directory)
            self._refresh_after_upload_if_ready()

    def _refresh_after_upload_if_ready(self):
        if self.task is not None or not self.isVisible() or self._uploaded_directory is None:
            return
        directory, self._uploaded_directory = self._uploaded_directory, None
        if self.directory.is_relative_to(directory):
            self._refresh()

    def _up(self):
        self._submit("list", truba_files.list_directory, self.directory.parent)

    def _open_folder(self, item, _column):
        if S_ISDIR(item.data(0, Qt.ItemDataRole.UserRole)):
            self._submit("list", truba_files.list_directory, self.directory / item.text(0))

    def _open_selected_folder(self):
        if item := self._selected_item(S_ISDIR):
            self._open_folder(item, 0)

    def set_slurm_directory(self, username, path):
        directory = PurePosixPath(path).relative_to(truba_files.project_root(username))
        self._slurm_directory = username, directory
        self._update_controls()

    def _open_slurm_inputs(self):
        if self.slurm_inputs.isEnabled():
            self._submit("list", truba_files.list_directory, self._slurm_directory[1])

    def _create_folder(self):
        if not self.create.isEnabled():
            return
        name = self.folder_name.text().strip()
        try:
            truba_files.validate_folder_name(name)
        except ValueError as error:
            self.status.setText(str(error))
            return
        self._submit("mkdir", truba_files.create_directory, self.directory, name)

    def _selected_item(self, matches_mode):
        items = self.files.selectedItems()
        if items and matches_mode(items[0].data(0, Qt.ItemDataRole.UserRole)):
            return items[0]
        return None

    def _upload_file(self):
        if not self.upload.isEnabled():
            return
        auth, directory = self.auth, self.directory
        source, _ = QFileDialog.getOpenFileName(self, translate("Upload file to TRUBA"), str(Path.home()))
        if source and self.truba.analysis_auth() is auth and self.directory == directory:
            self._submit("upload", truba_files.upload_file, directory, Path(source))

    def _download_file(self):
        if not self.download.isEnabled():
            return
        auth, directory = self.auth, self.directory
        name = self._selected_item(S_ISREG).text(0)
        # A valid Linux filename can contain characters that a Windows picker
        # interprets as a drive, directory separator or wildcard.
        suggestion = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(" .") or "download"
        target, _ = QFileDialog.getSaveFileName(
            self, translate("Download file from TRUBA"), str(Path.home() / suggestion),
        )
        if target and self.truba.analysis_auth() is auth and self.directory == directory:
            self._submit("download", truba_files.download_file, directory, name, Path(target))

    def check_task(self):
        task = self.task
        if task is None or not task[0].done():
            return
        self._sync_connection()
        if self.task is not task:
            return
        future, directory, kind, success_message = task
        self.task = None
        try:
            entries = future.result()
        except ConnectionError:
            self.status.setText("Connect using the main TRUBA button.")
        except Exception as error:
            code = getattr(error, "errno", None)
            unavailable = code in (errno.ENOENT, errno.ENOTDIR, errno.EACCES, errno.EPERM) or isinstance(error, ValueError)
            if kind == "list" and self.restoring_directory and directory == self._initial_directory and unavailable:
                self._submit("list", truba_files.list_directory, PurePosixPath(),
                             success_message="The last TRUBA folder is unavailable. Opened the project folder.")
                return
            if isinstance(error, ValueError):
                message = str(error)
            elif kind == "upload" and isinstance(error, FileExistsError):
                message = "A file or folder with that name already exists on TRUBA."
            elif kind in ("list", "mkdir") and code == errno.ENOENT:
                message = "TRUBA folder was not found."
            elif kind in ("list", "mkdir") and code in (errno.EACCES, errno.EPERM):
                message = "Permission denied on TRUBA."
            else:
                message = FAILURE_MESSAGES[kind]
            self.status.setText(message)
        else:
            if kind == "mkdir":
                self.folder_name.clear()
                self._uploaded_directory = None
                self._refresh()
                return
            if kind == "upload":
                self._uploaded_directory = None
                self._refresh(success_message="File uploaded to TRUBA.")
                return
            if kind == "download":
                self.status.setText("File downloaded from TRUBA.")
            else:
                same_folder = self.directory == directory
                previous = self.directory if self.loaded else self._initial_directory
                if directory != previous:
                    self.preferences.setValue(self._directory_key(), directory.as_posix())
                    self.preferences.sync()
                self.directory = directory
                self.loaded = True
                self._update_address()
                self._show_entries(entries, preserve_selection=same_folder)
                if self.is_slurm_snapshot_open() and entries:
                    self.status.setText("Synchronized inputs are read-only.")
                else:
                    self.status.setText(success_message or ("" if entries else "This folder is empty."))
        self._update_controls()
        self._refresh_after_upload_if_ready()

    def _show_entries(self, entries, *, preserve_selection=False):
        self.entries = entries
        self._render_entries(preserve_selection=preserve_selection, select_run_list=True)

    def _render_entries(self, *_, preserve_selection=True, select_run_list=False):
        current = self.files.selectedItems() if preserve_selection else []
        selected_name = current[0].text(0) if current else None
        selected_row = None
        run_list_row = None
        self.files.clear()
        folder_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        file_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        link_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileLinkIcon)
        locale = QLocale()
        groups = {}
        grouped = self.group_files.isChecked()
        self.files.setRootIsDecorated(grouped)
        for entry in self.entries:
            mode = entry.st_mode or 0
            folder = S_ISDIR(mode)
            size = "" if folder or entry.st_size is None else locale.formattedDataSize(entry.st_size)
            row = QTreeWidgetItem([entry.filename, size])
            row.setData(0, Qt.ItemDataRole.UserRole, mode)
            row.setToolTip(0, entry.filename)
            if entry.filename == selected_name:
                selected_row = row
            if S_ISREG(mode) and entry.filename == RUN_LIST:
                run_list_row = row
            row.setIcon(0, folder_icon if folder else link_icon if S_ISLNK(mode) else file_icon)
            if grouped:
                if folder:
                    category = "Folders"
                elif S_ISREG(mode) and (row is run_list_row or is_slurm_script(entry.filename)
                                       or is_input_script(entry.filename)):
                    category = "Scripts"
                else:
                    category = "Other files"
                if category not in groups:
                    header = QTreeWidgetItem([translate(category)])
                    header.setData(0, Qt.ItemDataRole.UserRole, 0)
                    header.setFlags(header.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                    groups[category] = header
                groups[category].addChild(row)
            else:
                self.files.addTopLevelItem(row)
        for category in ("Folders", "Scripts", "Other files"):
            if category in groups:
                self.files.addTopLevelItem(groups[category])
                groups[category].setExpanded(True)
        self._filter_entries()
        if select_run_list and selected_row is None:
            selected_row = run_list_row
        if selected_row is not None and not selected_row.isHidden():
            self.files.setCurrentItem(selected_row)

    def _filter_entries(self, *_):
        query = self.filter.text().strip().casefold()
        for index in range(self.files.topLevelItemCount()):
            row = self.files.topLevelItem(index)
            if row.childCount():
                for child in range(row.childCount()):
                    self._filter_row(row.child(child), query)
                row.setHidden(all(row.child(child).isHidden() for child in range(row.childCount())))
                if query and not row.isHidden():
                    row.setExpanded(True)
            else:
                self._filter_row(row, query)

    @staticmethod
    def _filter_row(row, query):
        hidden = query not in row.text(0).casefold()
        if hidden:
            row.setSelected(False)
        row.setHidden(hidden)
