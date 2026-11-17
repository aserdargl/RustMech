"""Saved unfinished TRUBA requests, visible even while computation is local."""
import json
import paramiko

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (QGroupBox, QHeaderView, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from studio_style import CONTROL_HEIGHT
from truba_jobs import HOST, cancel_remote


OPERATIONS = {
    'probe': 'Runtime capability check', 'prepare': 'Remote runtime setup',
    'preview': 'Mesh preview', 'surface': 'Surface generation',
    'mechanical': 'Mechanical analysis', 'convergence_level': 'Mesh convergence',
    'doe_mesh': 'DOE mesh generation', 'doe_solve': 'DOE analysis',
    'flow_export': 'Flow export', 'flow_solve': 'Flow analysis',
    'thermal_export': 'Thermal export', 'thermal_solve': 'Thermal analysis',
}
TERMINAL = {'DOWNLOADED', 'CANCELLED', 'COMPLETED', 'FAILED', 'TIMEOUT',
            'NODE_FAIL', 'OUT_OF_MEMORY', 'PREEMPTED', 'BOOT_FAIL',
            'DEADLINE', 'REVOKED', 'RELEASED', 'ENDED'}


def unfinished_records(directory):
    paths = sorted((directory / 'jobs').glob('*.json'))
    paths.append(directory / 'resource_session.json')
    result = []
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(record, dict) or record.get('host') != HOST:
                continue
            state = str(record.get('state', 'unknown')).upper().split('|')[0].split()[0]
            if state.rstrip('+') not in TERMINAL:
                result.append((path, record))
        except (OSError, ValueError, IndexError):
            continue
    return result


def job_description(record):
    if record.get('kind') == 'rustmech_session':
        context = record.get('context', {})
        return f"Resource session · {context.get('cores', '?')} CPUs · {context.get('memory_mb', '?')} MiB"
    label = OPERATIONS.get(record.get('operation'), record.get('operation') or 'Mechanical analysis (Kratos)')
    # Older job records store identifying details only in the original request.
    from pathlib import Path
    try:
        request = json.loads((Path(record['local_directory']) / 'request.json').read_text(encoding='utf-8'))
        settings = request.get('settings', {})
        geometry = settings.get('imported_geometry_name') or settings.get('structure_name')
        if geometry:
            label += ' · ' + str(geometry)
        if request.get('row') is not None:
            label += f" · DOE row {request['row']}"
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return label


class CancelJobWorker(QThread):
    def __init__(self, path, auth, parent):
        super().__init__(parent)
        self.path, self.auth = path, auth
        self.failed = False
        self.failure_message = ''

    def run(self):
        try:
            cancel_remote(self.path, self.auth)
        except Exception as error:
            self.failed = True
            if isinstance(error, paramiko.AuthenticationException):
                self.failure_message = 'Cancellation failed: authentication rejected. Reconnect with valid credentials and retry.'
            elif isinstance(error, (OSError, EOFError, paramiko.SSHException)):
                self.failure_message = 'Cancellation failed: connection lost. Check VPN, reconnect and retry.'
            else:
                self.failure_message = 'Cancellation not confirmed. The saved job is kept; reconnect and retry.'
        finally:
            self.auth = None


class TrubaJobList(QGroupBox):
    release_requested = Signal()

    def __init__(self, dialog):
        super().__init__('Pending / active remote jobs', dialog)
        self.dialog = dialog
        self.workers = {}
        self._snapshot = None
        self._session_releasing = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 6)
        self.hint = QLabel('Saved job states · Connect to cancel a job.')
        self.hint.setObjectName('mutedHintLabel')
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Job / purpose', 'Saved state', ''])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 94)
        self.table.setMinimumHeight(80)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.refresh)
        dialog.connection_changed.connect(lambda *_: self.refresh())
        self.timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        records = unfinished_records(self.dialog.state_dir)
        auth = self.cancellation_auth()
        snapshot = (records, auth.username if auth else None, tuple(self.workers), self._session_releasing)
        if snapshot == self._snapshot:
            return
        self._snapshot = snapshot
        self.table.setRowCount(len(records))
        for row, (path, record) in enumerate(records):
            identity = str(record.get('job_id') or record.get('request_id') or path.stem)
            title = f"{identity} · {job_description(record)}"
            item = QTableWidgetItem(title)
            item.setToolTip(f"{title}\n{record.get('username', '')}\n{path}")
            self.table.setItem(row, 0, item)
            self.table.setItem(row, 1, QTableWidgetItem(str(record.get('state', 'unknown'))))
            cancelling = path in self.workers or (record.get('kind') == 'rustmech_session' and self._session_releasing)
            button = QPushButton('Cancelling…' if cancelling else 'Cancel')
            button.setObjectName('cancelBtn')
            button.setAutoDefault(False)
            button.setDefault(False)
            button.setFixedSize(88, CONTROL_HEIGHT)
            button.setEnabled(auth is not None and auth.username == record.get('username') and not cancelling)
            if auth is None or auth.username != record.get('username'):
                button.setToolTip('Connect with the account that owns this job, or save its credentials to cancel.')
            button.clicked.connect(lambda checked=False, p=path, r=record: self.cancel(p, r))
            self.table.setCellWidget(row, 2, button)
            self.table.setRowHeight(row, CONTROL_HEIGHT + 6)
        if not records:
            self.hint.setText('No pending remote jobs in saved records.')
        elif not self.workers and not self._session_releasing:
            self.hint.setText('Saved job states · Cancel uses the connected or saved owning account.')

    def cancellation_auth(self):
        auth = self.dialog.analysis_auth()
        if auth is not None:
            return auth
        # Merely reading credentials does not connect. Only an explicit Cancel
        # opens a channel, so a lost panel connection cannot lock out recovery.
        try:
            saved = self.dialog.credentials.load()
        except (OSError, ValueError):
            return None
        if saved:
            from truba_jobs import RemoteAuth
            return RemoteAuth(*saved)
        return None

    def set_session_release_state(self, active, message):
        self._session_releasing = active
        self.refresh()
        self.hint.setText(message)

    def cancel(self, path, record):
        auth = self.cancellation_auth()
        if auth is None or auth.username != record.get('username') or path in self.workers:
            return
        if record.get('kind') == 'rustmech_session':
            self.release_requested.emit()
            return
        worker = CancelJobWorker(path, auth, self)
        self.workers[path] = worker
        self.hint.setText('Cancelling remote job; waiting for confirmation…')
        worker.finished.connect(lambda: self._cancel_finished(path, worker))
        worker.start()
        self.refresh()

    def _cancel_finished(self, path, worker):
        self.workers.pop(path, None)
        self.refresh()
        self.hint.setText(worker.failure_message if worker.failed else 'Remote job stopped; removed from pending jobs.')
        worker.deleteLater()

    def shutdown(self):
        self.timer.stop()
        for worker in tuple(self.workers.values()):
            worker.wait()
