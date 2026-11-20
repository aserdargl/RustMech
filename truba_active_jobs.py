"""Live Slurm jobs grouped by the work name used at submission."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import PurePosixPath
import re
import shlex

from paramiko import SSHException
from PySide6.QtCore import QItemSelection, QItemSelectionModel, QSignalBlocker, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFormLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QItemDelegate, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QScrollArea, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from esen_jobs import jobs_directory as esen_jobs_directory, read_job as read_esen_job
from studio_style import CONTROL_HEIGHT
from truba_job_list import MONITORED_ANALYSES, OPERATIONS, job_description, unfinished_records
from truba_jobs import SLURM_TERMINAL_STATES
from truba_remote import RemoteContext, RemoteTransport, analysis_code
from ui_language import translate


# Job names and command paths may contain ordinary punctuation.
QUEUE_SEPARATOR = '\x1f'
QUEUE_FIELDS = ('%i', '%T', '%S', '%M', '%L', '%C', '%D', '%P', '%r', '%j', '%o')
ARRAY_TASK_RANGE = r'[0-9]+(?:-[0-9]+(?::[0-9]+)?)?'
CANCEL_JOB_ID = re.compile(
    rf'[0-9]+(?:_[0-9]+|_\[{ARRAY_TASK_RANGE}(?:,{ARRAY_TASK_RANGE})*(?:%[0-9]+)?\])?'
)
SLURM_JOB_HEADERS = (
    'Slurm ID', 'Status', 'Work name', 'Job description', 'Script / command', 'CPUs',
    'Nodes', 'Partition', 'Started at', 'Elapsed time', 'Time left',
)
ACTIVE_JOB_HEADERS = (*SLURM_JOB_HEADERS[:2], 'Work', 'Job', 'Command',
                      *SLURM_JOB_HEADERS[5:], 'Estimated start', 'Pending reason')
PENDING_REASON_COLUMN = len(ACTIVE_JOB_HEADERS) - 1
ACTIVE_JOB_COLUMN_WIDTHS = (76, 108, 88, 134, 128, 60, 66, 94, 158, 85, 80, 158, 218)
ACTIVE_JOB_WIDTH_KEY = 'truba/activeJobs/columnWidths'
PENDING_REASON_LABELS = {
    'Resources': 'Waiting for resources',
    'Priority': 'Higher-priority jobs',
    'Dependency': 'Waiting for a job dependency',
    'None': 'Awaiting scheduler review',
    'AssocGrpCpuLimit': 'Account CPU limit reached',
    'QOSGrpCpuLimit': 'QoS CPU limit reached',
}


def configure_slurm_job_table(table):
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setWordWrap(False)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    row_numbers = table.verticalHeader()
    row_numbers.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    row_numbers.setSectionsClickable(False)
    row_numbers.show()
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    for column, width in ((2, 88), (3, 134), (4, 128)):
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(column, width)
    for column in (2, 4, 7):
        table.setItemDelegateForColumn(column, QItemDelegate(table))


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
    pending_reason: str
    category: str


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


def queue_rows(output):
    for line in output.splitlines():
        fields = line.split(QUEUE_SEPARATOR, len(QUEUE_FIELDS) - 1)
        if len(fields) == len(QUEUE_FIELDS):
            row = tuple(field.strip() for field in fields)
            if re.fullmatch(r'[0-9]+(?:[_+][0-9]+|_\[[0-9,:%-]+\])*', row[0]):
                yield row


def query_active_jobs(auth, state_dir):
    transport = RemoteTransport(RemoteContext(transport='ssh'), auth)
    # The login shell may define squeue filters for interactive use.
    queue_command = (
        "TZ=UTC SLURM_TIME_FORMAT='%Y-%m-%dT%H:%M:%S' env "
        "-u SQUEUE_ACCOUNT -u SQUEUE_NAMES -u SQUEUE_LICENSES -u SQUEUE_PARTITION -u SQUEUE_QOS "
        "squeue --noheader --all --array --states=all --user="
        + shlex.quote(auth.username) + ' --format=' + shlex.quote(QUEUE_SEPARATOR.join(QUEUE_FIELDS))
    )
    rows = list(queue_rows(transport.command(queue_command)))
    records = defaultdict(list)
    records_by_code = {}
    tracked_ids = set()
    for _, record in unfinished_records(state_dir):
        if record.get('username') != auth.username:
            continue
        if not record.get('job_id'):
            code = analysis_code(record)
            if code:
                records_by_code[code] = [record]
            continue
        job_id = str(record['job_id'])
        records[job_id].append(record)
        state = str(record.get('state', '')).partition('|')[0].upper().rstrip('+')
        if re.fullmatch(r'[0-9]+', job_id) and state in {
            'SUBMITTED', 'PENDING', 'RUNNING', 'CONFIGURING', 'COMPLETING', 'SUSPENDED',
        }:
            tracked_ids.add(job_id)

    missing_ids = tracked_ids.difference(row[0] for row in rows)
    if missing_ids:
        # Reconfirm tracked jobs by ID before using them in the live table.
        try:
            output = transport.command(queue_command + ' --jobs=' + ','.join(sorted(missing_ids)), timeout=12)
        except (OSError, RuntimeError, SSHException):
            pass  # Keep displaying the successful account-wide result.
        else:
            rows.extend(row for row in queue_rows(output) if row[0] in missing_ids)

    groups = defaultdict(list)
    for job_id, state, start, elapsed, remaining, cpus, nodes, partition, reason, slurm_name, command in rows:
        state = state.rstrip('+')
        if state in SLURM_TERMINAL_STATES:
            continue
        esen_script = esen_job_script(slurm_name, job_id, auth.username)
        script = esen_script or (PurePosixPath(command).name if command.startswith('/') else command)
        fallback_name = slurm_name or job_id
        matching = records.get(job_id) or records_by_code.get(slurm_name, ())
        operations = {record.get('operation') for record in matching}
        categories = {OPERATIONS[operation] for operation in operations if operation in OPERATIONS}
        if any(record.get('session') or record.get('kind') == 'rustmech_session' for record in matching):
            category = 'Resource session'
        elif len(categories) == 1:
            category = next(iter(categories))
        else:
            category = 'LAMMPS' if esen_script else 'TRUBA jobs'
        has_study = any(record.get('study_name') for record in matching)
        purposes_by_work = defaultdict(list)
        for record in matching:
            if has_study and record.get('kind') == 'rustmech_session':
                continue
            operation = record.get('operation')
            default_work = fallback_name
            if operation in MONITORED_ANALYSES and not record.get('session'):
                default_work = OPERATIONS[operation]
            purposes_by_work[record.get('study_name') or default_work].append(job_description(record))
        if not purposes_by_work:
            purpose = f'LAMMPS · {esen_script}' if esen_script else slurm_name
            purposes_by_work[fallback_name] = [purpose or '—']
        for name, purposes in purposes_by_work.items():
            groups[name].append(ActiveJob(
                job_id, state, start, elapsed, remaining, tuple(dict.fromkeys(purposes)),
                script or '—', command, cpus or '—', nodes or '—', partition or '—',
                reason if state == 'PENDING' else '', category,
            ))
    return dict(groups)


def display_start(value):
    try:
        utc = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
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


def slurm_cancel_target(job_id):
    if CANCEL_JOB_ID.fullmatch(job_id):
        # squeue includes the concurrency limit in grouped array IDs.
        return re.sub(r'%[0-9]+\]$', ']', job_id)
    return None


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
    def __init__(self, auth, job_ids, parent):
        super().__init__(parent)
        self.auth = auth
        self.job_ids = job_ids
        self.failed = False
        self.invalidated = False

    def run(self):
        try:
            targets = [slurm_cancel_target(job_id) for job_id in self.job_ids]
            if not targets or any(target is None for target in targets):
                raise ValueError('Expected Slurm allocation or array task IDs')
            transport = RemoteTransport(RemoteContext(transport='ssh'), self.auth)
            arguments = ' '.join(shlex.quote(target) for target in targets)
            transport.command(f'scancel --user={shlex.quote(self.auth.username)} {arguments}')
        except Exception:
            self.failed = True
        finally:
            self.auth = None


class ActiveJobTable(QTableWidget):
    section_focused = Signal(object)

    def __init__(self, parent):
        super().__init__(0, len(ACTIVE_JOB_HEADERS), parent)
        self.job_rows = []
        self.setHorizontalHeaderLabels(ACTIVE_JOB_HEADERS)
        configure_slurm_job_table(self)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setTabKeyNavigation(False)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.moveSection(header.visualIndex(PENDING_REASON_COLUMN), 2)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.section_focused.emit(self)

    def fit_height(self):
        height = self.horizontalHeader().sizeHint().height() + 2 * self.frameWidth()
        height += min(self.rowCount(), 8) * self.verticalHeader().defaultSectionSize()
        height += self.horizontalScrollBar().sizeHint().height()
        self.setFixedHeight(height)


class TrubaActiveJobs(QWidget):
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self.worker = None
        self.cancel_worker = None
        self.refresh_after_cancel = False
        self.shutting_down = False
        self.groups = {}
        self.sections = {}
        self.jobs = None  # The selected section's table.
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
        self.cancel_button = QPushButton('Cancel job')
        self.cancel_button.setObjectName('destructiveActionBtn')
        self.cancel_button.clicked.connect(self.cancel_selected_jobs)
        self.refresh_button = QPushButton('Refresh')
        self.refresh_button.clicked.connect(self.refresh)
        header_row = QHBoxLayout()
        header_row.addLayout(form, 1)
        for button in (self.cancel_button, self.refresh_button):
            button.setFixedHeight(CONTROL_HEIGHT)
            button.setStyleSheet('QPushButton { padding: 0 8px; } QPushButton:focus { padding: 0 7px; }')
            button.setAutoDefault(False)
            button.setEnabled(False)
            button.setProperty('managesLocalizedWidth', True)
            header_row.addWidget(button)
        layout.addLayout(header_row)
        self.fit_action_buttons()
        dialog.language_manager.changed.connect(self.fit_action_buttons)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName('trubaActiveJobsSplitter')
        self.splitter.setChildrenCollapsible(False)
        self.works = self._table(['Work'])
        self.works.setItemDelegate(QItemDelegate(self.works))
        self.sections_scroll = QScrollArea()
        self.sections_scroll.setWidgetResizable(True)
        self.sections_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sections_content = QWidget()
        self.sections_layout = QVBoxLayout(sections_content)
        self.sections_layout.setContentsMargins(0, 0, 0, 0)
        self.sections_layout.setSpacing(6)
        self.sections_layout.addStretch()
        self.sections_scroll.setWidget(sections_content)
        self.splitter.addWidget(self.works)
        self.splitter.addWidget(self.sections_scroll)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([115, 375])
        layout.addWidget(self.splitter, 1)
        state_store = getattr(QApplication.instance(), '_rustmech_window_state', None)
        self.column_settings = state_store.settings if state_store is not None else dialog.language_manager.preferences
        self.works.currentCellChanged.connect(lambda *_: self.select_work(select_first=True))

        self.notice = QLabel('Connect to TRUBA to see active jobs.')
        self.notice.setObjectName('mutedHintLabel')
        self.notice.setWordWrap(True)
        self.cancel_notice = QLabel()
        self.cancel_notice.setObjectName('mutedHintLabel')
        self.cancel_notice.setWordWrap(True)
        self.cancel_notice.hide()
        status_row = QHBoxLayout()
        status_row.addWidget(self.notice, 1)
        status_row.addWidget(self.cancel_notice, 1)
        layout.addLayout(status_row)

    def add_section(self, category):
        section = QGroupBox(category, self.sections_layout.parentWidget())
        layout = QVBoxLayout(section)
        layout.setContentsMargins(4, 3, 4, 4)
        table = ActiveJobTable(section)
        layout.addWidget(table)
        self.dialog.language_manager.localize_tree(section)
        table.setAccessibleName(section.title())
        header = table.horizontalHeader()
        header.ensurePolished()
        for column, default_width in enumerate(ACTIVE_JOB_COLUMN_WIDTHS):
            default_width = max(default_width, header.sectionSizeHint(column))
            width = self.column_settings.value(f'{ACTIVE_JOB_WIDTH_KEY}/{column}', default_width, type=int)
            table.setColumnWidth(column, width if width > 0 else default_width)
        header.sectionResized.connect(lambda column, old, width: self.save_column_width(table, column, old, width))
        table.section_focused.connect(self.activate_section)
        table.itemSelectionChanged.connect(lambda: self.activate_section(table))
        self.sections[category] = table
        return table

    def activate_section(self, table):
        if table not in self.sections.values():
            return
        if self.jobs is not table:
            if self.jobs is not None:
                with QSignalBlocker(self.jobs):
                    self.jobs.clearSelection()
            self.jobs = table
        self.update_cancel_button()

    def save_column_width(self, source, column, old_width, width):
        # The work filter hides and shows its column without changing the preference.
        if old_width > 0 and width > 0:
            self.column_settings.setValue(f'{ACTIVE_JOB_WIDTH_KEY}/{column}', width)
            for table in self.sections.values():
                if table is not source and not table.isColumnHidden(column):
                    with QSignalBlocker(table.horizontalHeader()):
                        table.setColumnWidth(column, width)

    def fit_action_buttons(self, *_):
        buttons = (self.cancel_button, self.refresh_button)
        width = max(button.fontMetrics().horizontalAdvance(translate(button.text())) for button in buttons) + 20
        for button in buttons:
            button.setFixedWidth(width)

    @staticmethod
    def set_notice(label, message):
        label.setText(message)
        label.setVisible(bool(message))

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
        if self.works.rowCount():
            self.works.setCurrentCell(0, 0)
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
            self.set_notice(self.cancel_notice, '')
            self.show_groups({})
            self.set_notice(self.notice, 'Connect to TRUBA to see active jobs.')
        else:
            self.refresh()

    def refresh(self):
        if self.shutting_down or not self.isVisible() or self.worker is not None:
            return
        auth = self.dialog.analysis_auth()
        if auth is None:
            return
        if not self.groups:
            self.set_notice(self.notice, 'Updating active jobs…')
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
            self.set_notice(self.notice, 'Could not update active jobs. Retrying…')
            return
        self.show_groups(worker.groups)
        self.set_notice(self.notice, '' if worker.groups else 'No active Slurm jobs.')

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

    def select_work(self, *, select_first=False):
        selected = self.works.currentItem()
        selected_name = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        groups = self.groups if selected_name is None else {selected_name: self.groups[selected_name]}
        rows_by_category = defaultdict(list)
        for name, group in groups.items():
            for job in group:
                rows_by_category[job.category].append((name, job))

        was_empty = not self.sections
        for category in self.sections.keys() - rows_by_category.keys():
            table = self.sections.pop(category)
            if self.jobs is table:
                self.jobs = None
            section = table.parentWidget()
            self.sections_layout.removeWidget(section)
            section.hide()
            section.deleteLater()
        for index, category in enumerate(sorted(rows_by_category, key=str.casefold)):
            table = self.sections.get(category)
            if table is None:
                table = self.add_section(category)
            section = table.parentWidget()
            if self.sections_layout.itemAt(index).widget() is not section:
                self.sections_layout.insertWidget(index, section)
            self.populate_section(table, rows_by_category[category], selected_name is not None)

        if rows_by_category and (select_first or was_empty):
            table = self.jobs if self.jobs is not None else self.sections[min(rows_by_category, key=str.casefold)]
            if not table.selectedItems():
                flags = QItemSelectionModel.SelectionFlag
                table.setCurrentCell(0, 0, flags.ClearAndSelect | flags.Rows)
        self.update_cancel_button()

    def populate_section(self, table, job_rows, hide_work):
        selection_model = table.selectionModel()
        previous_rows = [(name, job.job_id) for name, job in table.job_rows]
        previous_selection = {previous_rows[index.row()] for index in selection_model.selectedRows()}
        row = table.currentRow()
        previous_current = previous_rows[row] if row >= 0 else None
        table.job_rows = job_rows
        horizontal_scroll = table.horizontalScrollBar()
        scroll_position = horizontal_scroll.value()
        with QSignalBlocker(table):
            table.setColumnHidden(2, hide_work)
            table.setRowCount(len(job_rows))
            selected_rows = []
            current_row = -1
            for index, (name, job) in enumerate(job_rows):
                table.setItem(index, 0, QTableWidgetItem(job.job_id))
                table.setItem(index, 1, QTableWidgetItem(job.state))
                table.setItem(index, 2, QTableWidgetItem(name))
                description = QTableWidgetItem(' / '.join(job.purposes))
                tooltip = '<br>'.join(escape(translate(purpose)) for purpose in job.purposes)
                description.setToolTip(f'<qt>{tooltip}</qt>')
                table.setItem(index, 3, description)
                script = QTableWidgetItem(job.script)
                if job.command:
                    script.setToolTip(f'<qt>{escape(job.command)}</qt>')
                table.setItem(index, 4, script)
                started = job.state not in ('PENDING', 'CONFIGURING')
                values = (
                    job.cpus, job.nodes, job.partition,
                    display_start(job.start) if started else '—',
                    display_duration(job.elapsed) if started else '—',
                    display_duration(job.remaining) if started else '—',
                )
                for column, value in enumerate(values, start=5):
                    table.setItem(index, column, QTableWidgetItem(value))
                estimate = display_start(job.start) if job.state == 'PENDING' else '—'
                estimate_item = QTableWidgetItem(estimate)
                if job.state == 'PENDING':
                    estimate_item.setToolTip(translate(
                        'Slurm has no start estimate yet.' if estimate == '—'
                        else 'Start time estimated by Slurm. It may change.'
                    ))
                table.setItem(index, len(SLURM_JOB_HEADERS), estimate_item)
                reason = job.pending_reason
                reason_item = QTableWidgetItem(PENDING_REASON_LABELS.get(reason, reason) or '—')
                if reason:
                    reason_item.setToolTip(f'<qt>{escape(reason)}</qt>')
                table.setItem(index, PENDING_REASON_COLUMN, reason_item)
                if (name, job.job_id) in previous_selection:
                    selected_rows.append(index)
                if (name, job.job_id) == previous_current:
                    current_row = index
            if current_row == -1 and selected_rows:
                current_row = selected_rows[0]
            selection = QItemSelection()
            for row in selected_rows:
                index = table.model().index(row, 0)
                selection.select(index, index)
            flags = QItemSelectionModel.SelectionFlag
            selection_model.select(selection, flags.ClearAndSelect | flags.Rows)
            table.setCurrentCell(current_row, 0, flags.NoUpdate)
        table.fit_height()
        horizontal_scroll.setValue(scroll_position)

    def selected_job_ids(self):
        if self.jobs is None:
            return ()
        rows = sorted(index.row() for index in self.jobs.selectionModel().selectedRows())
        return tuple(dict.fromkeys(self.jobs.job_rows[row][1].job_id for row in rows))

    def update_cancel_button(self, *_):
        job_ids = self.selected_job_ids()
        unsupported = any(slurm_cancel_target(job_id) is None for job_id in job_ids)
        self.cancel_button.setEnabled(
            bool(job_ids) and not unsupported and self.cancel_worker is None
            and not self.shutting_down and self.dialog.analysis_auth() is not None
        )
        self.cancel_button.setToolTip(translate(
            'Only individual Slurm allocations can be cancelled here.'
        ) if unsupported else '')

    def cancel_selected_jobs(self):
        auth = self.dialog.analysis_auth()
        job_ids = self.selected_job_ids()
        if self.shutting_down or auth is None or not job_ids or self.cancel_worker is not None:
            return
        if any(slurm_cancel_target(job_id) is None for job_id in job_ids):
            return
        if len(job_ids) == 1:
            message = (f'Cancel Slurm job {job_ids[0]}? This stops the entire allocation '
                       'and any analyses using it.')
        else:
            message = (f'Cancel {len(job_ids)} selected Slurm jobs? This stops each allocation '
                       'and any analyses using it.')
        # Keep the confirmed targets even if a refresh changes the selection.
        answer = QMessageBox.question(
            self, 'Cancel job', message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if (answer != QMessageBox.StandardButton.Yes or self.shutting_down or self.cancel_worker is not None
                or self.dialog.analysis_auth() != auth):
            return
        active_ids = {item.job_id for group in self.groups.values() for item in group}
        if not active_ids.issuperset(job_ids):
            self.set_notice(self.cancel_notice, 'The selected jobs changed. Review the list and try again.')
            return
        self.cancel_worker = CancelSlurmJobWorker(auth, job_ids, self)
        self.cancel_worker.finished.connect(self.finish_cancel)
        self.set_notice(self.cancel_notice, 'Sending cancellation request to Slurm…')
        self.cancel_button.setEnabled(False)
        self.cancel_worker.start()

    def finish_cancel(self):
        worker, self.cancel_worker = self.cancel_worker, None
        worker.deleteLater()
        self.update_cancel_button()
        if worker.invalidated or self.shutting_down:
            return
        count = len(worker.job_ids)
        if count == 1:
            job_id = worker.job_ids[0]
            message = (f'Could not request cancellation for Slurm job {job_id}. Refresh and retry.' if worker.failed
                       else f'Cancellation requested for Slurm job {job_id}.')
        else:
            message = (f'Could not confirm cancellation requests for all {count} Slurm jobs. Check their status.'
                       if worker.failed else f'Cancellation requested for {count} Slurm jobs.')
        self.set_notice(self.cancel_notice, message)
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
