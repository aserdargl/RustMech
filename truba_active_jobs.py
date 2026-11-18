"""Live Slurm jobs grouped by the work name used at submission."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
import re
import shlex

from PySide6.QtCore import QSignalBlocker, QThread, QTimer, Qt
from PySide6.QtWidgets import (QAbstractItemView, QFormLayout, QHeaderView, QLabel,
                               QItemDelegate, QLineEdit, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from studio_style import CONTROL_HEIGHT
from truba_job_list import unfinished_records
from truba_jobs import SLURM_TERMINAL_STATES
from truba_remote import RemoteContext, RemoteTransport


@dataclass(frozen=True)
class ActiveJob:
    job_id: str
    state: str
    start: str
    elapsed: str
    remaining: str


def query_active_jobs(auth, state_dir):
    transport = RemoteTransport(RemoteContext(transport='ssh'), auth)
    output = transport.command(
        "TZ=UTC SLURM_TIME_FORMAT='%Y-%m-%dT%H:%M:%S' squeue --noheader --array --states=all --user="
        + shlex.quote(auth.username) + " --format='%i|%T|%S|%M|%L|%j'"
    )
    names = defaultdict(set)
    for _, record in unfinished_records(state_dir):
        name = record.get('study_name')
        if record.get('username') == auth.username and name and record.get('job_id'):
            names[str(record['job_id'])].add(name)
    groups = defaultdict(list)
    for line in output.splitlines():
        fields = line.strip().split('|', 5)
        if len(fields) != 6:
            continue
        job_id, state, start, elapsed, remaining, slurm_name = fields
        if not re.fullmatch(r'[0-9]+(?:[_+][0-9]+)*', job_id) or state in SLURM_TERMINAL_STATES:
            continue
        for name in names.get(job_id) or (slurm_name.strip() or job_id,):
            groups[name].append(ActiveJob(job_id, state, start, elapsed, remaining))
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

    def run(self):
        try:
            self.groups = query_active_jobs(self.auth, self.state_dir)
        except Exception:
            pass  # The tab retains the last successful snapshot and retries.
        finally:
            self.auth = None


class TrubaActiveJobs(QWidget):
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self.worker = None
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
        layout.addLayout(form)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName('trubaActiveJobsSplitter')
        self.splitter.setChildrenCollapsible(False)
        self.works = self._table(['Work name'])
        self.works.setItemDelegate(QItemDelegate(self.works))
        self.jobs = self._table(['Slurm job ID', 'Status'])
        self.jobs.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.splitter.addWidget(self.works)
        self.splitter.addWidget(self.jobs)
        self.splitter.setSizes([180, 310])
        layout.addWidget(self.splitter, 1)
        self.works.currentCellChanged.connect(self.select_work)
        self.jobs.currentCellChanged.connect(self.show_details)

        details = QFormLayout()
        details.setVerticalSpacing(2)
        self.started = QLabel('—')
        self.elapsed = QLabel('—')
        self.remaining = QLabel('—')
        details.addRow('Started at', self.started)
        details.addRow('Elapsed time', self.elapsed)
        details.addRow('Time left in Slurm allocation', self.remaining)
        layout.addLayout(details)
        self.notice = QLabel('Connect to TRUBA to see active jobs.')
        self.notice.setObjectName('mutedHintLabel')
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)

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
        if not connected:
            self.show_groups({})
            self.notice.setText('Connect to TRUBA to see active jobs.')
        else:
            self.refresh()

    def refresh(self):
        if not self.isVisible() or self.worker is not None:
            return
        auth = self.dialog.analysis_auth()
        if auth is None:
            return
        if not self.groups:
            self.notice.setText('Updating active jobs…')
        self.worker = ActiveJobsWorker(auth, self.dialog.state_dir, self)
        self.worker.finished.connect(self.finish_refresh)
        self.worker.start()

    def finish_refresh(self):
        worker, self.worker = self.worker, None
        if worker is None:
            return
        worker.deleteLater()
        auth = self.dialog.analysis_auth()
        if auth is None:
            return
        if auth.username != worker.username:
            self.refresh()
            return
        if worker.groups is None:
            self.notice.setText('Could not update active jobs. Retrying…')
            return
        self.show_groups(worker.groups)
        self.notice.setText('' if worker.groups else 'No active Slurm jobs.')

    def show_groups(self, groups):
        current = self.works.currentItem()
        selected_name = current.text() if current else None
        self.groups = groups
        names = sorted(groups, key=str.casefold)
        with QSignalBlocker(self.works):
            self.works.setRowCount(len(names))
            for row, name in enumerate(names):
                item = QTableWidgetItem(name)
                item.setToolTip(f'<qt>{escape(name)}</qt>')
                self.works.setItem(row, 0, item)
            row = names.index(selected_name) if selected_name in names else 0
            self.works.setCurrentCell(row, 0)
        self.select_work()

    def select_work(self, *_):
        selected = self.works.currentItem()
        row = self.jobs.currentRow()
        job_id = self.selected_jobs[row].job_id if 0 <= row < len(self.selected_jobs) else None
        self.selected_jobs = self.groups.get(selected.text(), []) if selected else []
        with QSignalBlocker(self.jobs):
            self.jobs.setRowCount(len(self.selected_jobs))
            row = 0
            for index, job in enumerate(self.selected_jobs):
                self.jobs.setItem(index, 0, QTableWidgetItem(job.job_id))
                self.jobs.setItem(index, 1, QTableWidgetItem(job.state))
                if job.job_id == job_id:
                    row = index
            self.jobs.setCurrentCell(row, 0)
        self.show_details()

    def show_details(self, *_):
        row = self.jobs.currentRow()
        job = self.selected_jobs[row] if 0 <= row < len(self.selected_jobs) else None
        started = job is not None and job.state not in ('PENDING', 'CONFIGURING')
        self.started.setText(display_start(job) if started else '—')
        self.elapsed.setText(display_duration(job.elapsed) if started else '—')
        self.remaining.setText(display_duration(job.remaining) if started else '—')

    def shutdown(self):
        self.timer.stop()
        if self.worker is not None:
            self.worker.wait()
