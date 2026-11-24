"""Saved TRUBA requests and automatic tracking of background analyses."""
import json
import paramiko

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (QDialog, QGroupBox, QHeaderView, QLabel, QPushButton, QStyle,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from studio_style import CONTROL_HEIGHT, STUDIO_STYLESHEET
from truba_jobs import HOST, SLURM_TERMINAL_STATES, cancel_remote
from truba_remote import analysis_code, sync_background_analysis
from window_state import show_restored_window


OPERATIONS = {
    'probe': 'Runtime capability check', 'prepare': 'Remote runtime setup',
    'preview': 'Mesh preview', 'surface': 'Surface generation',
    'mechanical': 'Mechanical analysis', 'convergence_level': 'Mesh convergence',
    'fft_solve': 'FFT Homogenization',
    'peridynamic_solve': 'Peridynamic TPMS compression',
    'doe_mesh': 'DOE mesh generation', 'doe_solve': 'DOE analysis',
    'flow_export': 'Flow export', 'flow_solve': 'Flow analysis',
    'thermal_export': 'Thermal export', 'thermal_solve': 'Thermal analysis',
    'mpm_solve': 'MPM compression',
}
TERMINAL = SLURM_TERMINAL_STATES | {'DOWNLOADED', 'RELEASED', 'ENDED'}
MONITORED_ANALYSES = {'mechanical', 'fft_solve', 'peridynamic_solve', 'flow_solve', 'thermal_solve', 'mpm_solve',
                      'convergence_level', 'doe_solve'}


def terminal_record(record):
    state = str(record.get('state', '')).upper().split('|')[0].split()
    return bool(state and state[0].rstrip('+') in TERMINAL)


def tracked_analysis(record):
    return bool(record.get('background') or record.get('detached'))


def unfinished_records(directory, *, include_background_history=False, include_analysis_history=False):
    paths = sorted((directory / 'jobs').glob('*.json'))
    paths.append(directory / 'resource_session.json')
    result = []
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(record, dict) or record.get('host') != HOST:
                continue
            if (not terminal_record(record) or (include_background_history and tracked_analysis(record))
                    or (include_analysis_history and record.get('operation') in MONITORED_ANALYSES)):
                result.append((path, record))
        except (OSError, ValueError, IndexError):
            continue
    return result


def job_description(record):
    if record.get('kind') == 'rustmech_session':
        context = record.get('context', {})
        return f"Resource session · {context.get('cores', '?')} CPUs · {context.get('memory_mb', '?')} MiB"
    label = OPERATIONS.get(record.get('operation'), record.get('operation') or 'Mechanical analysis (Kratos)')
    if 'geometry_name' in record:
        geometry = record['geometry_name']
        return f'{label} · {geometry}' if geometry else label
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


def background_status(record):
    stage = record.get('state', '')
    state = stage.partition('|')[0].upper().rstrip('+')
    return {
        'PREPARING': 'Uploading analysis inputs…',
        'SUBMITTING': 'Submitting analysis to TRUBA…',
        'SUBMITTED': 'Analysis submitted · waiting for Slurm',
        'PENDING': 'Analysis queued',
        'CONFIGURING': 'Slurm is preparing resources…',
        'RUNNING': 'Analysis started and running',
        'COMPLETING': 'Analysis is finishing…',
        'DOWNLOADING': 'Downloading results…',
        'DOWNLOADED': 'Analysis complete · results downloaded',
    }.get(state, stage or 'Preparing analysis…')


def analysis_state(record):
    state = str(record.get('state', '')).upper().replace('|', ' ').split()
    code = state[0].rstrip('+') if state else ''
    if code in {'RUNNING', 'COMPLETING', 'DOWNLOADING'} or state[:3] == ['SESSION', '·', 'RUNNING']:
        return 'running'
    if code in {'COMPLETED', 'DOWNLOADED', 'CANCELLED'}:
        return 'idle'
    if code in SLURM_TERMINAL_STATES or code in {'INTERRUPTED', 'ERROR'}:
        return 'error'
    return 'pending'


class TrubaJobMonitor(QDialog):
    STATES = {
        'idle': ('No active analyses', '#DBEAFE', '#2563EB'),
        'pending': ('TRUBA analyses pending', '#FEF08A', '#A16207'),
        'running': ('TRUBA analyses running', '#BBF7D0', '#15803D'),
        'error': ('TRUBA analysis failed', '#FECACA', '#B91C1C'),
    }

    def __init__(self, parent, job_list):
        super().__init__(parent)
        self.setObjectName('trubaJobMonitor')
        self.setWindowTitle('TRUBA analyses')
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint)
        self.setStyleSheet(STUDIO_STYLESHEET + '\nQDialog#trubaJobMonitor { background: #F5F5F3; }')
        self.resize(720, 350)
        self.job_list = job_list
        self.records = ()
        self.submission_error = ''
        self.button = QPushButton(parent)
        self.button.setObjectName('trubaJobsBtn')
        self.button.setFixedSize(CONTROL_HEIGHT, CONTROL_HEIGHT)
        self.button.setAutoDefault(False)
        self.button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self.button.clicked.connect(self.open_window)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(7)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['Analysis code', 'Slurm job ID', 'Analysis', 'Status'])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for column in (0, 1):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        for column in (2, 3):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setObjectName('mutedHintLabel')
        layout.addWidget(self.hint)
        job_list.analyses_changed.connect(self.refresh)
        self.refresh(job_list.analysis_records)

    def open_window(self):
        self.job_list.refresh()
        show_restored_window(self)

    def start_submission(self):
        self.submission_error = ''
        self.refresh(self.records)
        self.open_window()

    def submission_failed(self, message):
        self.submission_error = str(message)
        self.refresh(self.records)

    def refresh(self, records):
        self.records = records
        states = [analysis_state(record) for _, record in records]
        state = ('running' if 'running' in states else 'pending' if 'pending' in states
                 else 'error' if self.submission_error or states[:1] == ['error'] else 'idle')
        label, background, border = self.STATES[state]
        if self.button.property('jobState') != state:
            self.button.setProperty('jobState', state)
            self.button.setStyleSheet(
                f'QPushButton {{ background: {background}; border: 1px solid {border}; border-radius: 4px; padding: 0; }}'
                f'QPushButton:hover, QPushButton:focus {{ border: 2px solid {border}; }}'
                f'QPushButton:pressed {{ background: {border}; }}'
            )
        self.button.setToolTip(f'TRUBA analyses · {label}')
        self.button.setAccessibleName(self.button.toolTip())
        self.hint.setText(self.submission_error or (label if not records else ''))
        self.hint.setVisible(bool(self.hint.text()))
        self.table.setRowCount(len(records))
        for row, (path, record) in enumerate(records):
            description = job_description(record)
            if record.get('study_name'):
                description = f"{record['study_name']} · {description}"
            status = str(record.get('state') or 'Preparing analysis…')
            for column, value in enumerate((analysis_code(record) or '—', str(record.get('job_id') or '—'),
                                            description, status)):
                item = QTableWidgetItem(value)
                item.setToolTip(f"{value}\n{record.get('remote_directory', '')}\n"
                                f"{record.get('local_directory') or path}")
                self.table.setItem(row, column, item)
            self.table.setRowHeight(row, CONTROL_HEIGHT + 6)


class BackgroundJobSync(QThread):
    updated = Signal(dict, object)

    def __init__(self, records, auth, parent):
        super().__init__(parent)
        self.records, self.auth = records, auth

    def run(self):
        try:
            for path, record in self.records:
                if self.isInterruptionRequested():
                    break
                try:
                    result = sync_background_analysis(record, self.auth)
                except Exception:
                    result = None  # Leave the durable record eligible on the next poll.
                try:
                    current = json.loads(path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    continue
                if current != record or result is not None:
                    self.updated.emit(current, result)
        finally:
            self.auth = None


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
    background_updated = Signal(dict, object)
    analyses_changed = Signal(object)

    def __init__(self, dialog):
        super().__init__('TRUBA jobs', dialog)
        self.dialog = dialog
        self.workers = {}
        self._snapshot = None
        self._session_releasing = False
        self.submitting = False
        self.analysis_records = ()
        self.sync_worker = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 6)
        self.hint = QLabel('Saved job states · Connect to cancel a job.')
        self.hint.setObjectName('mutedHintLabel')
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Analysis code / purpose', 'Saved state', ''])
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
        self.sync_timer = QTimer(self, interval=5000)
        self.sync_timer.timeout.connect(self.sync_background)
        dialog.connection_changed.connect(self.sync_background)
        self.sync_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        records = unfinished_records(self.dialog.state_dir, include_background_history=True,
                                     include_analysis_history=True)
        auth = self.cancellation_auth()
        snapshot = (records, auth.username if auth else None, tuple(self.workers), self._session_releasing)
        if snapshot == self._snapshot:
            return
        self._snapshot = snapshot
        analyses = [(path, record) for path, record in records if record.get('operation') in MONITORED_ANALYSES]
        self.analysis_records = sorted(analyses, key=lambda item: self._record_modified(item[0]), reverse=True)
        self.analyses_changed.emit(self.analysis_records)
        records = [(path, record) for path, record in records
                   if not terminal_record(record) or tracked_analysis(record)]
        self.table.setRowCount(len(records))
        for row, (path, record) in enumerate(records):
            code = analysis_code(record)
            identity = code or str(record.get('job_id') or record.get('request_id') or path.stem)
            purpose = job_description(record)
            if record.get('study_name'):
                purpose = f"{record['study_name']} · {purpose}"
            title = f"{identity} · {purpose}"
            item = QTableWidgetItem(title)
            job_id = record.get('job_id') or '—'
            item.setToolTip(f"{title}\nSlurm #{job_id} · {record.get('username', '')}\n"
                            f"{record.get('local_directory') or path}")
            self.table.setItem(row, 0, item)
            saved_state = str(record.get('state', 'unknown'))
            state = QTableWidgetItem(background_status(record) if tracked_analysis(record) else saved_state)
            state.setToolTip(saved_state)
            self.table.setItem(row, 1, state)
            if terminal_record(record):
                self.table.removeCellWidget(row, 2)
                self.table.setRowHeight(row, CONTROL_HEIGHT + 6)
                continue
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
            self.hint.setText('No saved TRUBA jobs.')
        elif not self.workers and not self._session_releasing:
            self.hint.setText('Background analyses update and download automatically when connected.')

    @staticmethod
    def _record_modified(path):
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return 0

    def sync_background(self, connected=True):
        if not connected or self.sync_worker is not None:
            return
        auth = self.dialog.analysis_auth()
        if auth is None:
            return
        records = [(path, record) for path, record in unfinished_records(self.dialog.state_dir)
                   if tracked_analysis(record) and record.get('submission_attempted')
                   and not (self.submitting and record.get('state') == 'submitting')
                   and record.get('username') == auth.username and path not in self.workers]
        if records:
            self.sync_worker = BackgroundJobSync(records, auth, self)
            self.sync_worker.updated.connect(self._background_updated)
            self.sync_worker.finished.connect(self._sync_finished)
            self.sync_worker.start()

    def _background_updated(self, record, result):
        self.refresh()
        self.background_updated.emit(record, result)

    def _sync_finished(self):
        self.sync_worker.deleteLater()
        self.sync_worker = None

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
        self.sync_timer.stop()
        if self.sync_worker is not None:
            self.sync_worker.requestInterruption()
            self.sync_worker.wait()
        for worker in tuple(self.workers.values()):
            worker.wait()
