"""RustMech's TRUBA session and analysis file synchronization window."""

from pathlib import Path
from stat import S_ISDIR

from paramiko import SSHException
from PySide6.QtCore import QDir, QObject, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QFileSystemModel, QGroupBox, QHeaderView, QHBoxLayout, QLabel, QPushButton, QSplitter,
    QSizePolicy, QTableWidget, QTableWidgetItem, QTreeView, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout,
)

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
        self.status = status
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
    def __init__(self, auth, folder, parent):
        super().__init__(parent)
        self.auth = auth
        self.folder = folder
        self.files = None

    def run(self):
        try:
            folder = validated_remote_path(self.folder, self.auth.username)
            with DesktopSSHClient(self.auth).open_sftp() as sftp:
                entries = sftp.listdir_attr(folder)
            self.files = sorted((entry.filename, S_ISDIR(entry.st_mode)) for entry in entries)
        except (OSError, ValueError, RuntimeError, EOFError, SSHException):
            pass


class TrubaSyncWindow(QDialog):
    def __init__(self, session, language_manager, parent=None):
        super().__init__(parent)
        self.session = session
        self.language_manager = language_manager
        self.records = []
        self.selected = None
        self.remote_request = None
        self.remote_worker = None
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
        self.inputs = QTreeWidget(headerHidden=True, rootIsDecorated=False)
        self.remote = QTreeWidget(headerHidden=True, rootIsDecorated=False)
        self.outputs = QTreeView(headerHidden=True)
        self.output_model = QFileSystemModel(self)
        self.output_model.setReadOnly(True)
        self.output_model.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot)
        self.outputs.setModel(self.output_model)
        for column in range(1, self.output_model.columnCount()):
            self.outputs.hideColumn(column)
        self.inputs.itemActivated.connect(self._open_input)
        self.outputs.activated.connect(self._open_output)
        self.input_path = self._add_column(columns, "Local inputs", self.inputs)
        self.remote_path = self._add_column(columns, "TRUBA files", self.remote)
        self.output_path = self._add_column(columns, "Local results", self.outputs)
        columns.setSizes([275, 370, 275])

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setObjectName("syncFilesAndJobs")
        splitter.setChildrenCollapsible(False)
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
        self.job_table.currentCellChanged.connect(self._select)
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
    def _add_column(splitter, title, view):
        group = QGroupBox(title)
        layout = QVBoxLayout(group)
        path = QLabel()
        path.setObjectName("mutedHintLabel")
        path.setTextFormat(Qt.TextFormat.PlainText)
        path.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(path)
        view.setAccessibleName(title)
        layout.addWidget(view)
        splitter.addWidget(group)
        return path

    def _language_changed(self, *_):
        self.language_manager.localize_tree(self)
        self.job_table.setHorizontalHeaderLabels([translate(title) for title in
                                                  ("Analysis code", "Slurm job ID", "Analysis", "Status")])
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
        current = self.selected
        self.records = records
        self.job_table.blockSignals(True)
        try:
            self.job_table.setRowCount(len(records))
            for row, (path, record) in enumerate(records):
                if path == self.selected:
                    current = row
                values = (analysis_code(record) or "—", str(record.get("job_id") or "—"),
                          job_description(record), str(record.get("state") or "—"))
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    self.job_table.setItem(row, column, item)
            row = current if isinstance(current, int) else (0 if records else -1)
            self.job_table.setCurrentCell(row, 0)
        finally:
            self.job_table.blockSignals(False)
        self._select(row)
        self._update_hint()

    @staticmethod
    def _modified(path):
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return 0

    def _select(self, row, *_):
        selection = self.records[row] if 0 <= row < len(self.records) else (None, {})
        self.selected, record = selection
        local = record.get("local_directory", "")
        self._show_local_files(local)
        folder = record.get("remote_directory")
        folder = folder if isinstance(folder, str) else ""
        self.remote_path.setText(folder)
        self.remote_path.setToolTip(folder)
        auth = self.session.analysis_auth()
        request = (auth, folder) if auth and folder and auth.username == record.get("username") else None
        if request != self.remote_request or (request is None and self.remote.topLevelItemCount() == 0):
            self.remote_request = request
            self.remote.clear()
            self._load_remote_files()

    def _show_local_files(self, directory):
        self.inputs.clear()
        self.input_path.setText(directory)
        self.input_path.setToolTip(directory)
        self.output_path.clear()
        self.output_path.setToolTip("")
        self.outputs.hide()
        if not directory:
            return
        root = Path(directory)
        try:
            entries = sorted(root.iterdir())
        except OSError:
            entries = []
        for path in entries:
            if path.is_file() and (path.suffix == ".slurm" or path.name in {
                "request.json", "engine.tar.gz", "input.json", "mesh_input.npz",
                "model.mdpa", "ProjectParameters.json", "materials.json", "run.slurm",
            }):
                item = QTreeWidgetItem([path.name])
                item.setData(0, Qt.ItemDataRole.UserRole, str(path))
                self.inputs.addTopLevelItem(item)
        output = next((path for path in (root / "output", root / "results") if path.is_dir()), None)
        if output is not None:
            self.output_path.setText(str(output))
            self.output_path.setToolTip(str(output))
            self.outputs.setRootIndex(self.output_model.setRootPath(str(output)))
            self.outputs.show()

    def _load_remote_files(self):
        if self.remote_request is None:
            if self.selected is not None and self.remote.topLevelItemCount() == 0:
                item = QTreeWidgetItem([translate("Connect in TRUBA.")])
                item.setToolTip(0, translate("Connect with this job's account in TRUBA."))
                self.remote.addTopLevelItem(item)
            return
        if self.remote_worker is not None:
            return
        worker = RemoteFileListing(*self.remote_request, self)
        self.remote_worker = worker
        worker.finished.connect(self._remote_finished)
        worker.start()

    def _remote_finished(self):
        worker, self.remote_worker = self.remote_worker, None
        if (worker.auth, worker.folder) != self.remote_request:
            self._load_remote_files()
        else:
            self.remote.clear()
            if worker.files is None:
                self.remote.addTopLevelItem(QTreeWidgetItem([translate("TRUBA files could not be listed.")]))
            else:
                for name, directory in worker.files:
                    self.remote.addTopLevelItem(QTreeWidgetItem([name + "/" if directory else name]))
        worker.deleteLater()

    def _refresh_files(self):
        self.refresh()
        if self.remote_worker is None:
            self.remote_request = None
            self.remote.clear()
            self._select(self.job_table.currentRow())
        self.session.sync_background()

    def _connection_changed(self, *_):
        if self.isVisible():
            self._select(self.job_table.currentRow())

    @staticmethod
    def _open_input(item, _column):
        QDesktopServices.openUrl(QUrl.fromLocalFile(item.data(0, Qt.ItemDataRole.UserRole)))

    def _open_output(self, index):
        path = self.output_model.fileInfo(index)
        if path.isFile():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path.absoluteFilePath()))

    def shutdown(self):
        self.timer.stop()
        if self.remote_worker is not None:
            self.remote_worker.finished.disconnect(self._remote_finished)
            self.remote_worker.wait()
