"""Live Slurm jobs grouped by the work name used at submission."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import PurePosixPath
import re
import shlex

from PySide6.QtCore import QSignalBlocker, QThread, QTimer, Qt
from PySide6.QtWidgets import (QAbstractItemView, QFormLayout, QHBoxLayout, QHeaderView,
                               QItemDelegate, QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from esen_jobs import jobs_directory as esen_jobs_directory, read_job as read_esen_job
from studio_style import CONTROL_HEIGHT
from truba_job_list import job_description, unfinished_records
from truba_jobs import SLURM_TERMINAL_STATES
from truba_remote import RemoteContext, RemoteTransport
from ui_language import translate


# Job names and command paths may contain ordinary punctuation.
QUEUE_SEPARATOR = '\x1f'


@dataclass(frozen=True)
class ActiveJob:
    job_id: str
    state: str
    start: str
    elapsed: str
    remaining: str
    purposes: tuple[str, ...]
    script: str
    command: str
    cpus: str
    nodes: str
    partition: str


def esen_job_script(slurm_name, job_id, username):
    match = re.fullmatch(r'ESEN-([0-9a-f]{32})', slurm_name)
    if not match:
        return None
    try:
        record = read_esen_job(esen_jobs_directory() / match[1])
    except (OSError, ValueError):
        return None
    if (isinstance(record, dict) and record.get('username') == username
            and str(record.get('job_id')) == job_id and record.get('name')):
        return record['name']
    return None


def query_active_jobs(auth, state_dir):
    transport = RemoteTransport(RemoteContext(transport='ssh'), auth)
    fields_format = QUEUE_SEPARATOR.join(('%i', '%T', '%S', '%M', '%L', '%C', '%D', '%P', '%j', '%o'))
    output = transport.command(
        "TZ=UTC SLURM_TIME_FORMAT='%Y-%m-%dT%H:%M:%S' squeue --noheader --all --array --states=all --user="
        + shlex.quote(auth.username) + ' --format=' + shlex.quote(fields_format)
    )
    records = defaultdict(list)
    for _, record in unfinished_records(state_dir):
        if record.get('username') == auth.username and record.get('job_id'):
            records[str(record['job_id'])].append(record)
    groups = defaultdict(list)
    for line in output.splitlines():
        fields = line.split(QUEUE_SEPARATOR, 9)
        if len(fields) != 10:
            continue
        job_id, state, start, elapsed, remaining, cpus, nodes, partition, slurm_name, command = (
            field.strip() for field in fields
        )
        if (not re.fullmatch(r'[0-9]+(?:[_+][0-9]+|_\[[0-9,:%-]+\])*', job_id)
                or state in SLURM_TERMINAL_STATES):
            continue
        esen_script = esen_job_script(slurm_name, job_id, auth.username)
        script = esen_script or (PurePosixPath(command).name if command.startswith('/') else command)
        fallback_name = slurm_name or job_id
        matching = records.get(job_id, ())
        has_study = any(record.get('study_name') for record in matching)
        purposes_by_work = defaultdict(list)
        for record in matching:
            if has_study and record.get('kind') == 'rustmech_session':
                continue
            purposes_by_work[record.get('study_name') or fallback_name].append(job_description(record))
        if not purposes_by_work:
            purpose = f'LAMMPS · {esen_script}' if esen_script else slurm_name
            purposes_by_work[fallback_name] = [purpose or '—']
        for name, purposes in purposes_by_work.items():
            groups[name].append(ActiveJob(
                job_id, state, start, elapsed, remaining, tuple(dict.fromkeys(purposes)),
                script or '—', command, cpus or '—', nodes or '—', partition or '—',
            ))
    return dict(groups)


def display_start(job):
    try:
        utc = datetime.fromisoformat(job.start).replace(tzinfo=timezone.utc)
    except ValueError:
        return '—'
    return utc.astimezone().strftime('%d.%m.%Y %H:%M:%S %z')


def display_duration(value):
    if value == 'UNLIMITED':
        return 'Unlimited'
    match = re.fullmatch(r'(?:(\d+)-)?(?:(\d+):)?(\d+):(\d\d)', value)
    if not match:
        return '—'
    days, hours, minutes, seconds = match.groups()
    total_hours = int(days or 0) * 24 + int(hours or 0)
    return f'{total_hours:02d}:{int(minutes):02d}:{seconds}'


class ActiveJobsWorker(QThread):
    def __init__(self, auth, state_dir, parent):
        super().__init__(parent)
        self.auth = auth
        self.username = auth.username
        self.state_dir = state_dir
        self.groups = None
        self.invalidated = False

    def run(self):
        try:
            self.groups = query_active_jobs(self.auth, self.state_dir)
        except Exception:
            pass  # The tab retains the last successful snapshot and retries.
        finally:
            self.auth = None


class CancelSlurmJobWorker(QThread):
    def __init__(self, auth, job_id, parent):
        super().__init__(parent)
        self.auth = auth
        self.job_id = job_id
        self.failed = False
        self.invalidated = False

    def run(self):
        try:
            if not re.fullmatch(r'[0-9]+', self.job_id):
                raise ValueError('Expected a single Slurm allocation ID')
            transport = RemoteTransport(RemoteContext(transport='ssh'), self.auth)
            transport.command(f'scancel --user={shlex.quote(self.auth.username)} {self.job_id}')
        except Exception:
            self.failed = True
        finally:
            self.auth = None


class TrubaActiveJobs(QWidget):
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self.worker = None
        self.cancel_worker = None
        self.refresh_after_cancel = False
        self.shutting_down = False
        self.groups = {}
        self.selected_jobs = []
        self.timer = QTimer(self, interval=10000)
        self.timer.timeout.connect(self.refresh)
        dialog.connection_changed.connect(self.connection_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(5)
        self.study_name = QLineEdit()
        self.study_name.setMaxLength(100)
        self.study_name.setMaximumWidth(240)
        self.study_name.setFixedHeight(CONTROL_HEIGHT)
        self.study_name.setToolTip('Use the same work name to group multiple submissions.')
        form = QFormLayout()
        form.addRow('Work name for new jobs', self.study_name)
        self.refresh_button = QPushButton('Refresh')
        self.refresh_button.setFixedHeight(CONTROL_HEIGHT)
        self.refresh_button.setStyleSheet('QPushButton { padding: 0 8px; } QPushButton:focus { padding: 0 7px; }')
        self.refresh_button.setAutoDefault(False)
        self.refresh_button.setEnabled(False)
        self.refresh_button.clicked.connect(self.refresh)
        header_row = QHBoxLayout()
        header_row.addLayout(form, 1)
        header_row.addWidget(self.refresh_button)
        layout.addLayout(header_row)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName('trubaActiveJobsSplitter')
        self.splitter.setChildrenCollapsible(False)
        self.works = self._table(['Work name'])
        self.works.setItemDelegate(QItemDelegate(self.works))
        self.jobs = self._table([
            'Slurm ID', 'Status', 'Work name', 'Job description', 'Script / command', 'CPUs',
            'Nodes', 'Partition', 'Started at', 'Elapsed time', 'Time left',
        ])
        row_numbers = self.jobs.verticalHeader()
        row_numbers.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        row_numbers.setSectionsClickable(False)
        row_numbers.show()
        self.jobs.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        header = self.jobs.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        for column in (2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        self.jobs.setColumnWidth(2, 88)
        self.jobs.setColumnWidth(3, 134)
        self.jobs.setColumnWidth(4, 128)
        for column in (2, 4, 7):
            self.jobs.setItemDelegateForColumn(column, QItemDelegate(self.jobs))
        self.splitter.addWidget(self.works)
        self.splitter.addWidget(self.jobs)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([115, 375])
        layout.addWidget(self.splitter, 1)
        self.works.currentCellChanged.connect(self.select_work)
        self.jobs.currentCellChanged.connect(self.update_cancel_button)

        self.notice = QLabel('Connect to TRUBA to see active jobs.')
        self.notice.setObjectName('mutedHintLabel')
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        actions = QHBoxLayout()
        self.cancel_notice = QLabel()
        self.cancel_notice.setObjectName('mutedHintLabel')
        self.cancel_notice.setWordWrap(True)
        actions.addWidget(self.cancel_notice, 1)
        self.cancel_button = QPushButton('Cancel job')
        self.cancel_button.setObjectName('destructiveActionBtn')
        self.cancel_button.setFixedHeight(CONTROL_HEIGHT)
        self.cancel_button.setStyleSheet(self.refresh_button.styleSheet())
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_selected_job)
        actions.addWidget(self.cancel_button)
        layout.addLayout(actions)

    @staticmethod
    def _table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().hide()
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setWordWrap(False)
        return table

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()
        self.refresh()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def connection_changed(self, connected):
        self.refresh_button.setEnabled(connected and self.worker is None)
        if not connected:
            if self.worker is not None:
                self.worker.invalidated = True
            if self.cancel_worker is not None:
                self.cancel_worker.invalidated = True
            self.cancel_notice.clear()
            self.show_groups({})
            self.notice.setText('Connect to TRUBA to see active jobs.')
        else:
            self.refresh()

    def refresh(self):
        if self.shutting_down or not self.isVisible() or self.worker is not None:
            return
        auth = self.dialog.analysis_auth()
        if auth is None:
            return
        if not self.groups:
            self.notice.setText('Updating active jobs…')
        self.refresh_button.setEnabled(False)
        self.worker = ActiveJobsWorker(auth, self.dialog.state_dir, self)
        self.worker.finished.connect(self.finish_refresh)
        self.worker.start()

    def finish_refresh(self):
        worker, self.worker = self.worker, None
        if worker is None:
            return
        worker.deleteLater()
        refresh_again, self.refresh_after_cancel = self.refresh_after_cancel, False
        auth = self.dialog.analysis_auth()
        self.refresh_button.setEnabled(auth is not None)
        if auth is None:
            return
        if refresh_again or worker.invalidated or auth.username != worker.username:
            self.refresh()
            return
        if worker.groups is None:
            self.notice.setText('Could not update active jobs. Retrying…')
            return
        self.show_groups(worker.groups)
        self.notice.setText('' if worker.groups else 'No active Slurm jobs.')

    def show_groups(self, groups):
        current = self.works.currentItem()
        selected_name = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.groups = groups
        names = sorted(groups, key=str.casefold)
        with QSignalBlocker(self.works):
            self.works.setRowCount(len(names) + 1 if names else 0)
            if names:
                self.works.setItem(0, 0, QTableWidgetItem(translate('All jobs')))
            for row, name in enumerate(names, start=1):
                item = QTableWidgetItem(name)
                item.setData(Qt.ItemDataRole.UserRole, name)
                item.setToolTip(f'<qt>{escape(name)}</qt>')
                self.works.setItem(row, 0, item)
            row = names.index(selected_name) + 1 if selected_name in names else 0
            self.works.setCurrentCell(row, 0)
        self.select_work()

    def select_work(self, *_):
        selected = self.works.currentItem()
        row = self.jobs.currentRow()
        previous = (
            (self.jobs.item(row, 2).text(), self.selected_jobs[row].job_id)
            if 0 <= row < len(self.selected_jobs) else None
        )
        selected_name = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        groups = self.groups if selected_name is None else {selected_name: self.groups[selected_name]}
        jobs = [(name, job) for name, group in groups.items() for job in group]
        self.selected_jobs = [job for _, job in jobs]
        horizontal_scroll = self.jobs.horizontalScrollBar()
        scroll_position = horizontal_scroll.value()
        with QSignalBlocker(self.jobs):
            self.jobs.setColumnHidden(2, selected_name is not None)
            self.jobs.setRowCount(len(self.selected_jobs))
            row = 0
            for index, (name, job) in enumerate(jobs):
                self.jobs.setItem(index, 0, QTableWidgetItem(job.job_id))
                self.jobs.setItem(index, 1, QTableWidgetItem(job.state))
                self.jobs.setItem(index, 2, QTableWidgetItem(name))
                description = QTableWidgetItem(' / '.join(job.purposes))
                tooltip = '<br>'.join(escape(translate(purpose)) for purpose in job.purposes)
                description.setToolTip(f'<qt>{tooltip}</qt>')
                self.jobs.setItem(index, 3, description)
                script = QTableWidgetItem(job.script)
                if job.command:
                    script.setToolTip(f'<qt>{escape(job.command)}</qt>')
                self.jobs.setItem(index, 4, script)
                started = job.state not in ('PENDING', 'CONFIGURING')
                values = (
                    job.cpus, job.nodes, job.partition,
                    display_start(job) if started else '—',
                    display_duration(job.elapsed) if started else '—',
                    display_duration(job.remaining) if started else '—',
                )
                for column, value in enumerate(values, start=5):
                    self.jobs.setItem(index, column, QTableWidgetItem(value))
                if (name, job.job_id) == previous:
                    row = index
            self.jobs.setCurrentCell(row, 0)
        horizontal_scroll.setValue(scroll_position)
        self.update_cancel_button()

    def selected_job(self):
        row = self.jobs.currentRow()
        return self.selected_jobs[row] if 0 <= row < len(self.selected_jobs) else None

    def update_cancel_button(self, *_):
        job = self.selected_job()
        single_job = job is not None and bool(re.fullmatch(r'[0-9]+', job.job_id))
        self.cancel_button.setEnabled(single_job and self.cancel_worker is None)
        self.cancel_button.setToolTip(translate(
            'Only individual Slurm allocations can be cancelled here.'
        ) if job is not None and not single_job else '')

    def cancel_selected_job(self):
        auth = self.dialog.analysis_auth()
        job = self.selected_job()
        if self.shutting_down or auth is None or job is None or self.cancel_worker is not None:
            return
        if not re.fullmatch(r'[0-9]+', job.job_id):
            return
        # Keep the confirmed target even if a refresh changes the current row.
        answer = QMessageBox.question(
            self, 'Cancel job',
            f'Cancel Slurm job {job.job_id}? This stops the entire allocation and any analyses using it.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if (answer != QMessageBox.StandardButton.Yes or self.shutting_down or self.cancel_worker is not None
                or self.dialog.analysis_auth() != auth
                or not any(item.job_id == job.job_id for group in self.groups.values() for item in group)):
            return
        self.cancel_worker = CancelSlurmJobWorker(auth, job.job_id, self)
        self.cancel_worker.finished.connect(self.finish_cancel)
        self.cancel_notice.setText('Sending cancellation request to Slurm…')
        self.cancel_button.setEnabled(False)
        self.cancel_worker.start()

    def finish_cancel(self):
        worker, self.cancel_worker = self.cancel_worker, None
        worker.deleteLater()
        self.update_cancel_button()
        if worker.invalidated or self.shutting_down:
            return
        message = ('Could not request cancellation for Slurm job {job_id}. Refresh and retry.' if worker.failed
                   else 'Cancellation requested for Slurm job {job_id}.')
        self.cancel_notice.setText(message.format(job_id=worker.job_id))
        if self.worker is not None:
            self.refresh_after_cancel = True
        else:
            self.refresh()

    def shutdown(self):
        self.shutting_down = True
        self.timer.stop()
        if self.worker is not None:
            self.worker.wait()
        if self.cancel_worker is not None:
            self.cancel_worker.wait()
