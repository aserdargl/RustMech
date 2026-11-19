"""Read-only Slurm partition capacity for the TRUBA connection dialog."""

from dataclasses import dataclass
from datetime import datetime
import re

from PySide6.QtCore import QThread, QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from studio_style import CONTROL_HEIGHT
from truba_remote import RemoteContext, RemoteTransport
from ui_language import translate


@dataclass(frozen=True)
class PartitionCapacity:
    name: str
    state: str
    allocated: int
    idle: int
    other: int
    total: int
    idle_nodes: int
    total_nodes: int

    def allocated_label(self):
        if not self.total:
            return '—'
        return f'{self.allocated} / {self.total} ({self.allocated / self.total:.0%})'


def query_partition_capacity(auth):
    transport = RemoteTransport(RemoteContext(transport='ssh'), auth)
    output = transport.command("LC_ALL=C sinfo --summarize --noheader --local --format='%R|%a|%C|%F'", timeout=15)
    partitions = []
    for line in output.splitlines():
        match = re.fullmatch(r'([^|\s]+)\|(up|down|drain|inact)\|(\d+(?:/\d+){3})\|(\d+(?:/\d+){3})',
                             line.strip())
        if match:
            name, state, cpus, nodes = match.groups()
            _, idle_nodes, _, total_nodes = map(int, nodes.split('/'))
            partitions.append(PartitionCapacity(name, state, *map(int, cpus.split('/')), idle_nodes, total_nodes))
    if output.strip() and not partitions:
        raise ValueError('Unexpected Slurm partition capacity output.')
    return sorted(partitions, key=lambda partition: partition.name.casefold())


class PartitionCapacityWorker(QThread):
    def __init__(self, auth, parent):
        super().__init__(parent)
        self.auth = auth
        self.invalidated = False
        self.partitions = None

    def run(self):
        try:
            self.partitions = query_partition_capacity(self.auth)
        except Exception:
            pass  # Leave the last successful sample visible, with an update error.
        finally:
            self.auth = None


class TrubaQueueStatus(QWidget):
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self.worker = None
        self.shutting_down = False
        self.updated_at = ''

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(5)
        toolbar = QHBoxLayout()
        self.notice = QLabel('Connect to TRUBA to see queue status.')
        self.notice.setObjectName('mutedHintLabel')
        self.notice.setWordWrap(True)
        toolbar.addWidget(self.notice, 1)
        self.refresh_button = QPushButton('Refresh')
        self.refresh_button.setFixedHeight(CONTROL_HEIGHT)
        self.refresh_button.setStyleSheet('QPushButton { padding: 0 8px; } QPushButton:focus { padding: 0 7px; }')
        self.refresh_button.setAutoDefault(False)
        self.refresh_button.setEnabled(False)
        self.refresh_button.clicked.connect(self.refresh)
        toolbar.addWidget(self.refresh_button)
        layout.addLayout(toolbar)

        headers = ('Queue', 'Status', 'Allocated CPU', 'Idle CPU', 'Other CPU', 'Idle nodes')
        self.table = QTableWidget(0, len(headers))
        self.table.setAccessibleName('Queue status')
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        layout.addWidget(self.table, 1)
        self.timer = QTimer(self, interval=60000)
        self.timer.timeout.connect(self.refresh)
        dialog.connection_changed.connect(self.connection_changed)
        dialog.language_manager.changed.connect(self.update_tooltips)
        self.update_tooltips()

    def update_tooltips(self):
        self.table.horizontalHeaderItem(4).setToolTip(
            translate('CPUs in other Slurm states, such as drained or down.')
        )
        self.table.horizontalHeaderItem(5).setToolTip(translate('Idle nodes / total nodes'))

    def showEvent(self, event):
        super().showEvent(event)
        if self.dialog.analysis_auth() is not None:
            self.connection_changed(True)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def connection_changed(self, connected):
        self.refresh_button.setEnabled(connected and self.worker is None)
        if connected:
            self.refresh()
        else:
            self.timer.stop()
            if self.worker is not None:
                self.worker.invalidated = True
            self.table.setRowCount(0)
            self.updated_at = ''
            self.notice.setText('Connect to TRUBA to see queue status.')

    def refresh(self):
        if self.shutting_down or not self.isVisible() or self.worker is not None:
            return
        auth = self.dialog.analysis_auth()
        if auth is None:
            return
        self.timer.start()
        self.refresh_button.setEnabled(False)
        self.set_notice('Updating queue status…')
        self.worker = PartitionCapacityWorker(auth, self)
        self.worker.finished.connect(self.finish_refresh)
        self.worker.start()

    def finish_refresh(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        if self.shutting_down or self.dialog.analysis_auth() is None:
            return
        self.refresh_button.setEnabled(True)
        if worker.invalidated:
            self.refresh()
        elif worker.partitions is None:
            self.set_notice('Could not update queue status.')
        else:
            self.show_partitions(worker.partitions)
            self.updated_at = datetime.now().strftime('%H:%M:%S')
            self.set_notice('' if worker.partitions else 'No visible Slurm queues.')

    def set_notice(self, message):
        updated = f'Last updated · {self.updated_at}' if self.updated_at else ''
        self.notice.setText(' · '.join(filter(None, (message, updated))))

    def show_partitions(self, partitions):
        self.table.setRowCount(len(partitions))
        states = {'up': 'Accepting jobs', 'down': 'Down', 'drain': 'Draining', 'inact': 'Inactive'}
        for row, partition in enumerate(partitions):
            values = (partition.name, states[partition.state], partition.allocated_label(),
                      str(partition.idle), str(partition.other),
                      f'{partition.idle_nodes} / {partition.total_nodes}')
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column >= 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, column, item)

    def shutdown(self):
        self.shutting_down = True
        self.timer.stop()
        if self.worker is not None:
            self.worker.wait()
