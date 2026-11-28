"""RustMech's TRUBA session and analysis file synchronization window."""

from pathlib import Path, PurePosixPath
import posixpath
from stat import S_ISDIR, S_ISLNK

from paramiko import SSHException
from PySide6.QtCore import QDir, QObject, QSize, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileSystemModel, QFrame, QGroupBox, QHeaderView, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSplitter, QSizePolicy, QStyle, QTableWidget, QTableWidgetItem,
    QToolButton, QToolTip, QTreeView, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout,
)

from file_dialog import QFileDialog, _style_dialog_buttons
from studio_style import STUDIO_STYLESHEET
from truba_credentials import state_directory
from truba_desktop import DesktopSessionMonitor, DesktopStatus
from truba_job_list import BackgroundJobSync, job_description, tracked_analysis, unfinished_records
from truba_jobs import validated_remote_path
from truba_remote import analysis_code
from truba_shared_session import DesktopSSHClient
from ui_language import translate


class TrubaSession(QObject):
    connection_changed = Signal(bool)
    status_changed = Signal(object)
    background_updated = Signal(dict, object)

    def __init__(self, parent):
        super().__init__(parent)
        self.state_dir = state_directory()
        self.status = DesktopStatus()
        self.monitor = DesktopSessionMonitor(self)
        self.monitor.updated.connect(self._status_changed)
        self.sync_worker = None
        self.submitting = False
        self.timer = QTimer(self, interval=5000)
        self.timer.timeout.connect(self.sync_background)

    @property
    def connected(self):
        return self.status.session is not None

    def analysis_auth(self):
        return self.status.session

    def start(self):
        self.monitor.start()
        self.timer.start()

    def _status_changed(self, status):
        previous = self.status
        self.status = status
        self.status_changed.emit(status)
        if (status.session, status.other_cluster) != (previous.session, previous.other_cluster):
            self.connection_changed.emit(self.connected)
            self.sync_background()

    def sync_background(self):
        auth = self.analysis_auth()
        if auth is None or self.sync_worker is not None:
            return
        records = [(path, record) for path, record in unfinished_records(self.state_dir)
                   if tracked_analysis(record) and record.get("submission_attempted")
                   and not (self.submitting and record.get("state") == "submitting")
                   and record.get("username") == auth.username]
        if records:
            self.sync_worker = BackgroundJobSync(records, auth, self)
            self.sync_worker.updated.connect(self.background_updated)
            self.sync_worker.finished.connect(self._sync_finished)
            self.sync_worker.start()

    def _sync_finished(self):
        self.sync_worker.deleteLater()
        self.sync_worker = None

    def shutdown(self):
        self.timer.stop()
        self.monitor.stop()
        if self.sync_worker is not None:
            self.sync_worker.requestInterruption()
            self.sync_worker.wait()


class RemoteFileListing(QThread):
    def __init__(self, auth, folder, parent, *, browse=False):
        super().__init__(parent)
        self.auth = auth
        self.folder = folder
        self.browse = browse
        self.files = None

    def run(self):
        try:
            if self.browse:
                path = PurePosixPath(self.folder)
                if path.root != "/" or ".." in path.parts or "\0" in self.folder:
                    return
                folder = str(path)
            else:
                folder = validated_remote_path(self.folder, self.auth.username)
            with DesktopSSHClient(self.auth).open_sftp() as sftp:
                entries = sftp.listdir_attr(folder)
                self.files = sorted(
                    ((entry.filename, self._is_directory(sftp, folder, entry)) for entry in entries
                     if entry.filename not in (".", "..") and "/" not in entry.filename),
                    key=lambda item: (not item[1], item[0].casefold()),
                )
        except (OSError, ValueError, RuntimeError, EOFError, SSHException):
            pass

    @staticmethod
    def _is_directory(sftp, folder, entry):
        mode = entry.st_mode or 0
        if S_ISLNK(mode):
            try:
                mode = sftp.stat(str(PurePosixPath(folder) / entry.filename)).st_mode or 0
            except OSError:
                return False
        return S_ISDIR(mode)


def _folder_button(icon, label, action):
    button = QToolButton()
    button.setIcon(button.style().standardIcon(icon))
    button.setIconSize(QSize(16, 16))
    button.setFixedWidth(30)
    button.setToolTip(label)
    button.setAccessibleName(label)
    button.clicked.connect(action)
    return button


class LocalFolderView(QTreeView):
    def __init__(self, title):
        super().__init__(headerHidden=True)
        self.setProperty("preserveItemText", True)
        self.setAccessibleName(title)
        self.file_model = QFileSystemModel(self)
        self.file_model.setReadOnly(True)
        self.file_model.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot | QDir.Filter.Hidden)
        self.activated.connect(self._open_file)

    def show_folder(self, folder):
        if not folder or not Path(folder).is_dir():
            self.setModel(None)
            return
        if self.model() is None:
            self.setModel(self.file_model)
            for column in range(1, self.file_model.columnCount()):
                self.hideColumn(column)
        self.setRootIndex(self.file_model.setRootPath(folder))

    def _open_file(self, index):
        info = self.file_model.fileInfo(index)
        if info.isFile():
            QDesktopServices.openUrl(QUrl.fromLocalFile(info.absoluteFilePath()))


class RemoteFolderView(QTreeWidget):
    folder_activated = Signal(str)
    folder_loaded = Signal(bool)

    def __init__(self):
        super().__init__(headerHidden=True, rootIsDecorated=False)
        self.setProperty("preserveItemText", True)
        self.setAccessibleName("TRUBA files")
        self.request = None
        self.worker = None
        self.status = ""
        self.itemActivated.connect(self._activate_item)

    def show_folder(self, auth, folder, *, browse=False, reload=False):
        request = (auth, folder, browse) if auth and folder else None
        if request == self.request and not reload:
            return
        self.request = request
        self.folder_loaded.emit(False)
        if request:
            self._show_status("Loading TRUBA files…")
        elif auth:
            self._show_status("Enter a TRUBA folder path.")
        else:
            self._show_status("Connect in TRUBA.")
        self._start_listing()

    def _show_status(self, status):
        self.clear()
        self.status = status
        item = QTreeWidgetItem([translate(status)])
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.addTopLevelItem(item)

    def retranslate_status(self):
        if self.status:
            self.topLevelItem(0).setText(0, translate(self.status))

    def _start_listing(self):
        if self.request is None or self.worker is not None:
            return
        auth, folder, browse = self.request
        self.worker = RemoteFileListing(auth, folder, self, browse=browse)
        self.worker.finished.connect(self._listing_finished)
        self.worker.start()

    def _listing_finished(self):
        worker, self.worker = self.worker, None
        if (worker.auth, worker.folder, worker.browse) != self.request:
            self._start_listing()
        elif worker.files is None:
            self._show_status("TRUBA files could not be listed.")
        else:
            if worker.files:
                self.clear()
                self.status = ""
                style = self.style()
                folder_icon = style.standardIcon(QStyle.StandardPixmap.SP_DirIcon)
                file_icon = style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)
                items = []
                for name, directory in worker.files:
                    item = QTreeWidgetItem([name])
                    item.setIcon(0, folder_icon if directory else file_icon)
                    if directory:
                        item.setData(0, Qt.ItemDataRole.UserRole, str(PurePosixPath(worker.folder) / name))
                    items.append(item)
                self.addTopLevelItems(items)
            else:
                self._show_status("Folder is empty.")
            self.folder_loaded.emit(True)
        worker.deleteLater()

    def _activate_item(self, item, _column):
        folder = item.data(0, Qt.ItemDataRole.UserRole)
        if folder:
            self.folder_activated.emit(folder)

    def shutdown(self):
        if self.worker is not None:
            self.worker.finished.disconnect(self._listing_finished)
            self.worker.wait()


class TrubaFolderDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("trubaFolderDialog")
        self.setWindowTitle("Choose TRUBA folder")
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint)
        self.resize(520, 380)
        self.auth = None
        self.folder = ""
        layout = QVBoxLayout(self)
        address = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setAccessibleName("TRUBA folder path")
        self.path.returnPressed.connect(lambda: self._browse(self.path.text()))
        address.addWidget(self.path)
        self.up_button = _folder_button(
            QStyle.StandardPixmap.SP_FileDialogToParent, "Parent folder",
            lambda: self._browse(str(PurePosixPath(self.folder).parent)),
        )
        address.addWidget(self.up_button)
        layout.addLayout(address)
        self.view = RemoteFolderView()
        self.view.folder_activated.connect(self._browse)
        layout.addWidget(self.view)

        self.actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.select_button = self.actions.addButton("Select folder", QDialogButtonBox.ButtonRole.AcceptRole)
        self.view.folder_loaded.connect(
            lambda loaded: self.select_button.setEnabled(loaded and self.path.text() == self.folder),
        )
        self.path.textEdited.connect(lambda: self.select_button.setEnabled(False))
        self.actions.accepted.connect(self.accept)
        self.actions.rejected.connect(self.reject)
        for button in self.actions.buttons():
            button.setAutoDefault(False)
        layout.addWidget(self.actions)

    def _browse(self, folder):
        self.folder = folder
        self.path.setText(folder)
        parent = str(PurePosixPath(folder).parent)
        self.up_button.setEnabled(parent.startswith("/") and parent != folder)
        self.view.show_folder(self.auth, folder, browse=True, reload=True)

    def choose(self, auth, folder):
        self.auth = auth
        self._browse(folder)
        return self.folder if self.exec() == QDialog.DialogCode.Accepted else ""


class TrubaSyncWindow(QDialog):
    def __init__(self, session, language_manager, parent=None):
        super().__init__(parent)
        self.session = session
        self.language_manager = language_manager
        self.records = []
        self.selected = None
        self.input_folder = ""
        self.output_folder = ""
        self.remote_folder = None
        self.folder_dialog = TrubaFolderDialog(self)
        self.setObjectName("trubaSyncWindow")
        self.setWindowTitle("SYNC")
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint)
        self.setStyleSheet(STUDIO_STYLESHEET)
        self.resize(950, 525)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(7)

        columns = QSplitter(Qt.Orientation.Horizontal)
        columns.setObjectName("syncFileColumns")
        columns.setChildrenCollapsible(False)
        columns.setHandleWidth(7)
        self.inputs = LocalFolderView("Local inputs")
        self.outputs = LocalFolderView("Local outputs")
        self.remote = RemoteFolderView()
        self.remote.folder_activated.connect(self._set_remote_folder)
        self.input_path, self.input_button = self._add_column(
            columns, "Local inputs", self.inputs, "Open local inputs folder",
            lambda: self._open_local_folder(self.input_path),
            ("Choose local inputs folder",
             lambda: self._choose_local_folder("input_folder", self.input_path, "Choose local inputs folder")),
        )
        self.remote_path, self.remote_button = self._add_column(
            columns, "TRUBA files", self.remote, "Choose TRUBA folder", self._choose_remote_folder,
        )
        self.output_path, self.output_button = self._add_column(
            columns, "Local outputs", self.outputs, "Open local outputs folder",
            lambda: self._open_local_folder(self.output_path),
            ("Choose local outputs folder",
             lambda: self._choose_local_folder("output_folder", self.output_path, "Choose local outputs folder")),
        )
        self.input_path.returnPressed.connect(
            lambda: self._apply_local_path("input_folder", self.input_path),
        )
        self.remote_path.returnPressed.connect(self._apply_remote_path)
        self.output_path.returnPressed.connect(
            lambda: self._apply_local_path("output_folder", self.output_path),
        )
        columns.setSizes([275, 370, 275])

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setObjectName("syncFilesAndJobs")
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(7)
        splitter.addWidget(columns)
        jobs = QGroupBox("RustMech analyses")
        jobs_layout = QVBoxLayout(jobs)
        self.job_table = QTableWidget(0, 4)
        self.job_table.verticalHeader().hide()
        self.job_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.job_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.job_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        header = self.job_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.job_table.currentCellChanged.connect(self._job_changed)
        jobs_layout.addWidget(self.job_table)
        splitter.addWidget(jobs)
        splitter.setSizes([290, 170])
        layout.addWidget(splitter)

        actions = QHBoxLayout()
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setObjectName("mutedHintLabel")
        actions.addWidget(self.hint, 1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("secondaryActionBtn")
        self.refresh_button.setAutoDefault(False)
        self.refresh_button.clicked.connect(self._refresh_files)
        actions.addWidget(self.refresh_button)
        layout.addLayout(actions)

        self.timer = QTimer(self, interval=1500)
        self.timer.timeout.connect(self.refresh)
        session.connection_changed.connect(self._connection_changed)
        language_manager.changed.connect(self._language_changed)
        self._language_changed()

    @staticmethod
    def _add_column(splitter, title, view, button_label, open_folder, chooser=None):
        card = QFrame()
        card.setObjectName("syncFileCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(7, 7, 7, 7)
        layout.setSpacing(5)
        heading = QLabel(title)
        heading.setObjectName("syncRemoteHeading" if chooser is None else "syncLocalHeading")
        heading.setFixedHeight(25)
        layout.addWidget(heading)
        path = QLineEdit()
        path.setAccessibleName(title)
        path.setFixedHeight(25)
        path.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        button = _folder_button(QStyle.StandardPixmap.SP_DirOpenIcon, button_label, open_folder)
        button.setIcon(QIcon(str(Path(__file__).resolve().parent / "assets" / "ui" / "folder.svg")))
        button.setFixedSize(25, 25)
        button.setObjectName("syncAddressButton")
        address = QHBoxLayout()
        address.setSpacing(3)
        address.addWidget(path, 1)
        if chooser:
            label, choose_folder = chooser
            choose = QToolButton(text="…")
            choose.setFixedSize(button.size())
            choose.setObjectName("syncAddressButton")
            choose.setAccessibleName(label)
            choose.setToolTip(label)
            choose.clicked.connect(choose_folder)
            address.addWidget(choose)
        address.addWidget(button)
        layout.addLayout(address)
        layout.addWidget(view)
        splitter.addWidget(card)
        return path, button

    def _language_changed(self, *_):
        self.language_manager.localize_tree(self)
        self.job_table.setHorizontalHeaderLabels([translate(title) for title in
                                                  ("Analysis code", "Slurm job ID", "Analysis", "Status")])
        self.remote.retranslate_status()
        self.folder_dialog.view.retranslate_status()
        _style_dialog_buttons(self.folder_dialog.actions)
        for path in (self.input_path, self.remote_path, self.output_path):
            path.setAccessibleDescription(translate("Enter a folder path. Use .. for the parent folder."))
        self._update_remote_button(self.session.analysis_auth())
        self._update_hint()

    def _update_hint(self):
        self.hint.setText(translate("Track jobs in TRUBA WORKS." if self.records else "No saved TRUBA jobs."))

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()
        self._refresh_files()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def refresh(self):
        records = unfinished_records(self.session.state_dir, include_background_history=True,
                                     include_analysis_history=True)
        records = [(path, record) for path, record in records
                   if isinstance(record.get("local_directory"), str) and record["local_directory"]
                   and record.get("kind") != "rustmech_session"]
        records.sort(key=lambda item: self._modified(item[0]), reverse=True)
        if records == self.records:
            return
        current = next((row for row, (path, _) in enumerate(records) if path == self.selected),
                       0 if records else -1)
        self.records = records
        self.job_table.blockSignals(True)
        try:
            self.job_table.setRowCount(len(records))
            for row, (path, record) in enumerate(records):
                values = (analysis_code(record) or "—", str(record.get("job_id") or "—"),
                          job_description(record), str(record.get("state") or "—"))
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    self.job_table.setItem(row, column, item)
            self.job_table.setCurrentCell(current, 0)
        finally:
            self.job_table.blockSignals(False)
        self._select(current)
        self._update_hint()

    @staticmethod
    def _modified(path):
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return 0

    def _job_changed(self, row, *_):
        if 0 <= row < len(self.records) and self.records[row][0] != self.selected:
            self.input_folder = self.output_folder = ""
            self.remote_folder = None
        self._select(row)

    def _select(self, row, *, reload=False):
        selection = self.records[row] if 0 <= row < len(self.records) else (None, {})
        self.selected, record = selection
        self._show_local_files(record.get("local_directory", ""))
        auth = self.session.analysis_auth()
        self._update_remote_button(auth)
        folder = record.get("remote_directory")
        folder = folder if isinstance(folder, str) else ""
        browse = False
        if auth and self.remote_folder and auth.username == self.remote_folder[0]:
            _, folder = self.remote_folder
            browse = True
        elif auth and not folder:
            folder = f"/arf/scratch/{auth.username}"
            browse = True
        elif auth and auth.username != record.get("username"):
            auth = None
        self._show_path(self.remote_path, folder)
        self.remote.show_folder(auth, folder, browse=browse, reload=reload)

    def _show_local_files(self, directory):
        output = self.output_folder
        if not output and directory:
            root = Path(directory)
            output = next((str(path) for path in (root / "output", root / "results") if path.is_dir()), "")
        for path, view, folder in ((self.input_path, self.inputs, self.input_folder or directory),
                                   (self.output_path, self.outputs, output)):
            self._show_path(path, folder)
            view.show_folder(folder)

    @staticmethod
    def _show_path(path, folder):
        if path.toolTip() != folder or not path.isModified():
            if path.text() != folder:
                path.setText(folder)
            path.setToolTip(folder)

    def _choose_local_folder(self, attribute, path, title):
        chosen = QFileDialog.getExistingDirectory(self, title, path.toolTip() or self.input_path.toolTip())
        if chosen:
            setattr(self, attribute, chosen)
            path.setModified(False)
            self._select(self.job_table.currentRow())

    @staticmethod
    def _open_local_folder(path):
        folder = path.toolTip()
        if folder and QDesktopServices.openUrl(QUrl.fromLocalFile(folder)):
            return
        QToolTip.showText(path.mapToGlobal(path.rect().bottomLeft()),
                          translate("Enter an existing local folder path."), path)

    def _apply_local_path(self, attribute, path):
        try:
            if not path.text():
                raise ValueError
            folder = Path(path.text()).expanduser()
            if not folder.is_absolute() and not path.toolTip():
                raise ValueError
            folder = (Path(path.toolTip()) / folder).resolve(strict=True)
            if not folder.is_dir():
                raise ValueError
        except (OSError, RuntimeError, ValueError):
            QToolTip.showText(path.mapToGlobal(path.rect().bottomLeft()),
                              translate("Enter an existing local folder path."), path)
            return
        setattr(self, attribute, str(folder))
        path.setModified(False)
        self._select(self.job_table.currentRow())

    def _update_remote_button(self, auth):
        self.remote_button.setEnabled(auth is not None)
        self.remote_path.setReadOnly(auth is None)
        self.remote_button.setToolTip(translate("Choose TRUBA folder" if auth else "Connect in TRUBA."))

    def _apply_remote_path(self):
        auth = self.session.analysis_auth()
        text = self.remote_path.text()
        if auth is None or not text or "\0" in text:
            return
        folder = PurePosixPath(text)
        if not folder.is_absolute():
            base = self.remote.request[1] if self.remote.request else f"/arf/scratch/{auth.username}"
            folder = PurePosixPath(base) / folder
        self._set_remote_folder(posixpath.normpath(str(folder)))

    def _choose_remote_folder(self):
        auth = self.session.analysis_auth()
        if auth is None:
            return
        start = self.remote.request[1] if self.remote.request else f"/arf/scratch/{auth.username}"
        folder = self.folder_dialog.choose(auth, start)
        if folder and self.session.analysis_auth() == auth:
            self._set_remote_folder(folder)

    def _set_remote_folder(self, folder):
        auth = self.session.analysis_auth()
        if auth is not None:
            self.remote_folder = (auth.username, folder)
            self.remote_path.setModified(False)
            self._select(self.job_table.currentRow())

    def _refresh_files(self):
        self.refresh()
        self._select(self.job_table.currentRow(), reload=True)
        self.session.sync_background()

    def _connection_changed(self, *_):
        if self.folder_dialog.isVisible():
            self.folder_dialog.reject()
            self.folder_dialog.view.show_folder(None, "")
        if self.isVisible():
            self._select(self.job_table.currentRow())

    def shutdown(self):
        self.timer.stop()
        self.folder_dialog.reject()
        self.folder_dialog.view.shutdown()
        self.remote.shutdown()
