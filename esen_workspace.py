"""Native ESEN workspace for running and inspecting LAMMPS jobs."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
from operator import attrgetter
import os
from os.path import commonpath
from pathlib import Path, PurePosixPath
import re
import subprocess
from threading import Event
from time import time

from PySide6.QtCore import QPoint, QSettings, QSignalBlocker, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDialog, QFormLayout, QFrame, QHeaderView, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QStyle, QToolTip, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from datenburg_widgets import qss_asset_url
from esen_ai_prompt import ESEN_AI_PROMPT
import esen_jobs as jobs
from file_dialog import QFileDialog
from studio_style import (
    ActionFlowLayout, CompactComboBox, CompactSpinBox, CONTROL_HEIGHT,
    STUDIO_STYLESHEET, SidebarGroupBox, configure_form, field_label,
)
from truba_file_browser import TrubaFileBrowser
from truba_files import project_root, sync_tree
from ui_language import translate


WORKSPACE_TITLE = "Esen"
ACTIVE_TRUBA_STATES = frozenset({"Submitting", "Submitted", "Queued", "Running", "Cancellation requested"})
PANEL_PADDING = 7
INPUT_TOOL_SPACING = 2


def job_duration(record, state):
    seconds = record.get("elapsed_seconds")
    if record["target"] == "local" and state == "Running" and record.get("started_at") is not None:
        seconds = int(time() - record["started_at"])
    if seconds is None:
        return "—"
    hours, remainder = divmod(max(0, seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

ESEN_STYLE = """
QDialog#esenWorkspace { background: #F5F5F3; }
#esenTitle { background: #BFDBFE; border: 1px solid #2563EB; border-radius: 6px;
             color: #0F172A; font-size: 14px; padding-left: 6px; }
QFrame[esenCard="true"] { background: #FCFCFB; border: 1px solid #D5DBE2; border-radius: 7px; }
QFrame[esenCard="paper"] { background: #FFFFFF; border: 1px solid #D5DBE2; border-radius: 7px; }
#esenSection { background: #E5ECF4; border: 1px solid #D5DBE2; border-radius: 5px;
               color: #0F172A; font-size: 13px; padding: 0 5px; }
#esenWorkspace QPlainTextEdit, #esenWorkspace QListWidget, #esenWorkspace QTreeWidget {
    background: #FFFFFF; border: 1px solid #DCE3EC; border-radius: 3px;
}
#esenWorkspace QTreeWidget { color: #111827; selection-background-color: #E5E5E5; selection-color: #111827; }
#esenWorkspace QTreeWidget::indicator { width: 13px; height: 13px; background: #FFFFFF;
                                    border: 1px solid #AAAAAA; border-radius: 2px; }
#esenWorkspace QTreeWidget::indicator:checked { border-color: #2563EB;
                                           image: url(@ESEN_CHECK@); }
#esenWorkspace QTreeWidget::indicator:hover { border-color: #2563EB; }
#esenWorkspace QPlainTextEdit { font-family: Consolas; font-size: 12px; }
#esenFieldLabel { color: #475569; font-size: 11px; }
#esenWorkspace #trubaConnectionBadge { margin: 0; }
#esenWorkspace #trubaConnectionBadge[state="working"] { color: #713F12; background: #FFF4CE; }
#esenWorkspace QPushButton { padding: 0 7px; }
#esenWorkspace QPushButton[compactInputAction="true"] { padding: 0; }
#esenFileFilter { background: #FFFFFF; border: 1px solid #CBD5E1; border-radius: 4px;
                  color: #0F172A; font-size: 11px; padding: 0 3px; }
#esenFileFilter:hover { border-color: #93C5FD; }
#esenFileFilter:focus { border-color: #3B82F6; }
#esenRun { color: #FFFFFF; background: #285EA6; }
#esenRun:hover { background: #245692; }
#esenRun:focus { border-color: #0F172A; }
#esenRun:pressed { background: #1E477C; }
#esenRun:disabled { background: #EBEBEB; color: #808080; border-color: #D6D6D6; }
""".replace("@ESEN_CHECK@", qss_asset_url("checkbox-check-blue.svg"))


def panel(title, *, paper=False, scrollable=False, actions=()):
    card = QFrame()
    card.setProperty("esenCard", "paper" if paper else "true")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(PANEL_PADDING, PANEL_PADDING, PANEL_PADDING, PANEL_PADDING)
    layout.setSpacing(5)
    header = QLabel(title)
    header.setObjectName("esenSection")
    header.setFixedHeight(CONTROL_HEIGHT)
    if actions:
        toolbar = ActionFlowLayout()
        toolbar.addWidget(header)
        for button in actions:
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
    else:
        layout.addWidget(header)
    if scrollable:
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(card)
        return scroll, layout
    return card, layout


def command(text, slot, *, role="", hint=""):
    button = QPushButton(text)
    button.setAutoDefault(False)
    button.setDefault(False)
    button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    button.setProperty("managesLocalizedWidth", True)
    button.setFixedHeight(CONTROL_HEIGHT)
    if role:
        button.setObjectName(role)
    if hint:
        button.setToolTip(hint)
    button.clicked.connect(slot)
    return button


def button_row(*buttons):
    layout = ActionFlowLayout(equal_width=True)
    for button in buttons:
        layout.addWidget(button)
    return layout


def compact_combo(options):
    box = CompactComboBox()
    for label, value in options:
        box.addItem(label, value)
    width = max(box.fontMetrics().horizontalAdvance(translate(label)) for label, _ in options)
    box.setFixedWidth(max(100, min(200, width + 44)))
    return box


def code_view(name):
    edit = QPlainTextEdit()
    edit.setReadOnly(True)
    edit.setAccessibleName(name)
    edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    return edit


class CompactListWidget(QListWidget):
    """Fill spare panel space without forcing scrollbars from the list's size hint."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(63)

    def sizeHint(self):
        size = super().sizeHint()
        size.setHeight(self.minimumHeight())
        return size


class InputFileItem(QTreeWidgetItem):
    def __init__(self, name, job_order=None):
        path = Path(name)
        super().__init__([path.name])
        self.path = name
        self.job_order = job_order
        self.name_key = (path.name.casefold(), name.casefold(), name)
        self.format_key = (path.suffix.casefold(), *self.name_key)
        self.sort_key = self.name_key
        self.setFlags(self.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)

    def __lt__(self, other):
        return self.sort_key < other.sort_key


class InputFileTree(QTreeWidget):
    files_dropped = Signal(list)

    def items(self):
        return [item for item in self.walk(self.invisibleRootItem()) if item.job_order is not None]

    @staticmethod
    def walk(parent):
        for index in range(parent.childCount()):
            child = parent.child(index)
            yield child
            yield from InputFileTree.walk(child)

    def add_path(self, item, root):
        parent = self.invisibleRootItem()
        folder = ""
        for part in Path(item.path).parts[:-1]:
            folder = f"{folder}/{part}" if folder else part
            if folder not in self.folders:
                self.add_path(InputFileItem(folder), root)
            parent = self.folders[folder]
        directory = item.job_order is None
        if directory:
            self.folders[item.path] = item
        item.setIcon(0, self.folder_icon if directory else self.file_icon)
        if root is not None:
            item.setToolTip(0, str(root / item.path))
        parent.addChild(item)

    def clear(self):
        self.folders.clear()
        super().clear()

    def job_items(self):
        return sorted(self.items(), key=attrgetter("job_order"))

    def sort_view(self, mode):
        scripts = [item.path for item in self.job_items()
                   if jobs.is_slurm_script(item.path) and item.data(0, Qt.ItemDataRole.CheckStateRole) is not None]
        ranks = {path: str(rank) for rank, path in enumerate(scripts, 1)}
        self.setColumnHidden(1, not ranks or mode != "job")
        for item in reversed(list(self.walk(self.invisibleRootItem()))):
            item.setText(1, ranks.get(item.path, "") if mode == "job" else "")
            directory = item.job_order is None
            if mode == "job" and directory:
                first = min((item.child(index).sort_key for index in range(item.childCount())),
                            default=(True, float("inf"), item.name_key))
                item.sort_key = first
            elif mode == "job":
                item.sort_key = (not jobs.is_slurm_script(item.path), item.job_order, item.name_key)
            else:
                key = item.format_key if mode == "format" and not directory else item.name_key
                item.sort_key = (not directory, key)
        self.sortItems(0, Qt.SortOrder.AscendingOrder)

    def __init__(self):
        super().__init__()
        self.folders = {}
        self.folder_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self.file_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        self.setColumnCount(2)
        self.setColumnHidden(1, True)
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.setHeaderHidden(True)
        self.setProperty("preserveItemText", True)
        self.setUniformRowHeights(True)
        self.setMinimumHeight(63)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        help_text = translate("Use Ctrl or Shift to select files. Press Space to check or uncheck selected scripts.")
        self.setAccessibleDescription(help_text)
        self.setToolTip(help_text)

    def sizeHint(self):
        size = super().sizeHint()
        size.setHeight(self.minimumHeight())
        return size

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            items = [item for item in self.selectedItems()
                     if item.data(0, Qt.ItemDataRole.CheckStateRole) is not None]
            if items:
                check = any(item.checkState(0) != Qt.CheckState.Checked for item in items)
                state = Qt.CheckState.Checked if check else Qt.CheckState.Unchecked
                with QSignalBlocker(self):
                    for item in items:
                        item.setCheckState(0, state)
                self.itemChanged.emit(items[0], 0)
                event.accept()
                return
        super().keyPressEvent(event)

    @staticmethod
    def _local_files(event):
        urls = event.mimeData().urls()
        return [url.toLocalFile() for url in urls] if all(url.isLocalFile() for url in urls) else []

    def dragEnterEvent(self, event):
        if self._local_files(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        files = self._local_files(event)
        if files:
            self.files_dropped.emit(files)
            event.acceptProposedAction()
        else:
            event.ignore()


class EsenWorkspace(QDialog):
    def __init__(self, truba, parent=None, *, history=None):
        super().__init__(parent)
        self.setObjectName("esenWorkspace")
        self.setWindowTitle(WORKSPACE_TITLE)
        self.setWindowFlag(Qt.WindowType.WindowMinMaxButtonsHint)
        self.setStyleSheet(STUDIO_STYLESHEET + ESEN_STYLE)
        self.resize(1300, 750)
        self.truba = truba
        self.history_root = history if history is not None else jobs.jobs_directory()
        self.preferences = QSettings("RustMech", "RustMech")
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="esen")
        self.local_task = self.remote_task = None
        self.folder_root = None
        self.folder_scan = None
        self.folder_error = ""
        self.folder_upload = None
        self.folder_slurm = False
        self.folder_replay = False
        self.files_upload = None
        self.upload_task = None
        self.upload_error = ""
        self.sync_task = None
        self.sync_message = ""
        self.batch_queue = {}
        self.batch_active = None
        self._active_truba_jobs = {}
        self.local_stop = Event()
        self.record = None
        self.input_root = None
        self.entry = ""
        self.extra = []
        self.command_valid = False
        self.job_error = ""
        self._result_key = None
        self._profile_partition = None
        self._disposed = False
        self._build()
        self._command_buttons = self.findChildren(QPushButton)
        language = getattr(QApplication.instance(), "_rustmech_language", None)
        if language is not None:
            for button in self._command_buttons:
                language.localize(button)
            language.changed.connect(self._size_command_buttons)
        self._size_command_buttons()
        self._load_profiles()
        self._preview()
        self._load_history()
        self._show_selected()
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(5000)
        self.poll_timer.timeout.connect(self._poll_remote)
        self.poll_timer.start()
        self.truba.connection_changed.connect(self._connection_changed)

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(7)
        title = QLabel(WORKSPACE_TITLE)
        title.setToolTip("Thanks to Prof. Dr. Ismail Esen")
        title.setObjectName("esenTitle")
        title.setFixedHeight(30)
        outer.addWidget(title)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setObjectName("esenThreeColumns")
        split.setChildrenCollapsible(False)
        split.setHandleWidth(7)
        outer.addWidget(split, 1)

        left = QSplitter(Qt.Orientation.Vertical)
        left.setObjectName("esenInputsAndJobs")
        left.setChildrenCollapsible(False)
        left.setHandleWidth(7)
        self._settings_column = left
        right = QSplitter(Qt.Orientation.Vertical)
        right.setObjectName("esenPreviewAndResults")
        right.setChildrenCollapsible(False)
        right.setHandleWidth(7)
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)

        self.batch_folder = command("Folder", self._pick_batch_folder, hint="Select Folder")
        self.upload = command("Send Truba", self._upload_inputs, hint="Upload inputs to the open TRUBA folder")
        self.sync = command("Sync", self._sync_folders,
                            hint="Sync the local folder with the open TRUBA subfolder")
        self.load_files = command("", self._pick_files, hint="Load input files")
        self.pick_script = command("", self._pick_script, hint="LAMMPS input script")
        self.use_as_script = command("", self._use_as_script, hint="Use As Script")
        self.remove_inputs = command("", self._remove_files, hint="Remove selected files")
        self.pick_root = command("", self._pick_root, hint="Job folder")
        self.move_up = command("↑", lambda: self._move_script(-1), hint="Move script up")
        self.move_down = command("↓", lambda: self._move_script(1), hint="Move script down")
        icons = ((self.load_files, QStyle.StandardPixmap.SP_DialogOpenButton),
                 (self.pick_script, QStyle.StandardPixmap.SP_FileIcon),
                 (self.pick_root, QStyle.StandardPixmap.SP_DirHomeIcon),
                 (self.use_as_script, QStyle.StandardPixmap.SP_DialogApplyButton),
                 (self.remove_inputs, QStyle.StandardPixmap.SP_DialogDiscardButton))
        for button, icon in icons:
            button.setIcon(self.style().standardIcon(icon))
            button.setIconSize(QSize(14, 14))
        self._compact_input_actions = (*(button for button, _ in icons), self.move_up, self.move_down)
        for button in self._compact_input_actions:
            button.setProperty("compactInputAction", True)
            button.setAccessibleName(button.toolTip())
            button.setFixedWidth(CONTROL_HEIGHT)
        settings, layout = panel("Job settings", scrollable=True)
        left.addWidget(settings)
        actions = QHBoxLayout()
        actions.setSpacing(INPUT_TOOL_SPACING)
        for button in (self.batch_folder, self.upload, self.sync, self.load_files, self.pick_script,
                       self.move_up, self.move_down, self.pick_root, self.use_as_script, self.remove_inputs):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)

        self.input_filter = QLineEdit()
        self.input_filter.setObjectName("esenFileFilter")
        self.input_filter.setFixedHeight(CONTROL_HEIGHT)
        self.input_filter.setClearButtonEnabled(True)
        self.input_filter.setPlaceholderText("Filter by name or extension")
        self.input_filter.setAccessibleName("Filter input files")
        self.input_filter.setToolTip("Checked files stay checked when hidden by the filter.")
        self.input_filter.textChanged.connect(self._filter_inputs)
        self.input_sort = compact_combo((("Job order", "job"), ("Name (A–Z)", "name"),
                                         ("Format (A–Z)", "format")))
        self.input_sort.setAccessibleName("Sort input files")
        self.input_sort.setToolTip("For manual Slurm ordering, select Job order and clear the filter.")
        self.input_sort.currentIndexChanged.connect(self._sort_inputs)
        file_view = QHBoxLayout()
        file_view.addWidget(self.input_filter, 1)
        file_view.addWidget(self.input_sort)
        layout.addLayout(file_view)
        self.inputs = InputFileTree()
        self.inputs.setAcceptDrops(True)
        self.inputs.viewport().setAcceptDrops(True)
        self.inputs.files_dropped.connect(self._load_files)
        self.inputs.setAccessibleName("LAMMPS input files")
        self.inputs.itemChanged.connect(self._preview)
        self.inputs.itemSelectionChanged.connect(self._buttons)
        layout.addWidget(self.inputs, 1)
        self.transfer_progress = QProgressBar()
        self.transfer_progress.setObjectName("operationProgressBar")
        self.transfer_progress.setAccessibleName("TRUBA file transfer progress")
        self.transfer_progress.setFixedHeight(CONTROL_HEIGHT)
        self.transfer_progress.hide()
        layout.addWidget(self.transfer_progress)
        self.sync_status = QLabel()
        self.sync_status.setObjectName("mutedHintLabel")
        self.sync_status.setTextFormat(Qt.TextFormat.PlainText)
        self.sync_status.setWordWrap(True)
        self.sync_status.hide()
        layout.addWidget(self.sync_status)
        self.whole_folder = QCheckBox("Include entire job folder")
        self.whole_folder.toggled.connect(self._preview)
        layout.addWidget(self.whole_folder)

        self.job_form = form = QFormLayout()
        configure_form(form)
        layout.addLayout(form)
        self.target = compact_combo((("TRUBA", "remote"), ("This computer", "local")))
        self.ranks = CompactSpinBox()
        self.ranks.setRange(1, getattr(os, "process_cpu_count", os.cpu_count)() or 1)
        self.local_memory = CompactSpinBox()
        self.local_memory.setRange(0, 4194304)
        self.local_memory.setSingleStep(256)
        self.local_memory.setSpecialValueText(translate("No limit"))
        self.local_memory.setToolTip(translate("Total memory limit across local MPI processes."))
        self.local_exe = QLineEdit("lmp")
        self.local_mpi = QLineEdit()
        self.local_mpi.setPlaceholderText("Automatic")
        self.truba_badge = QLabel()
        self.truba_badge.setObjectName("trubaConnectionBadge")
        self.truba_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.truba_badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        target_row = QHBoxLayout()
        target_row.setSpacing(6)
        target_row.addWidget(self.target)
        target_row.addWidget(self.truba_badge)
        target_row.addStretch()
        self._form_row(form, "Run on", self.target, field_layout=target_row)
        self._form_row(form, "Local CPU cores", self.ranks)
        self._form_row(form, "Local RAM limit (MiB)", self.local_memory)
        self._form_row(form, "LAMMPS executable", self.local_exe)
        self._form_row(form, "MPI launcher", self.local_mpi)
        self.local_fields = (self.ranks, self.local_memory, self.local_exe, self.local_mpi)
        self.partition = compact_combo([(name, name) for name in jobs.CPU_QUEUES])
        self.account = QLineEdit()
        self.account.setPlaceholderText("TRUBA username")
        self.module = QLineEdit(jobs.DEFAULT_MODULE)
        self.remote_exe = QLineEdit("lmp")
        self.remote_mpi = compact_combo((("mpirun", "mpirun"), ("srun", "srun")))
        self.resume_input = QLineEdit()
        self.resume_input.setPlaceholderText("Optional relative path")
        self.resume_input.setToolTip(translate("Include a restart input in the job files to enable checkpoint recovery and the 70-hour handover."))
        self._form_row(form, "Queue", self.partition)
        self.connection = QLabel("Connect using the main TRUBA button.")
        self.connection.setWordWrap(True)
        layout.addWidget(self.connection)
        self.truba_settings = SidebarGroupBox("Advanced TRUBA settings")
        advanced = QFormLayout(self.truba_settings.content)
        advanced.setContentsMargins(5, 4, 5, 3)
        configure_form(advanced)
        self._form_row(advanced, "Account", self.account)
        self._form_row(advanced, "LAMMPS module", self.module)
        self._form_row(advanced, "Linux executable", self.remote_exe)
        self._form_row(advanced, "MPI launcher", self.remote_mpi)
        self._form_row(advanced, "Restart input", self.resume_input)
        layout.addWidget(self.truba_settings)
        self.truba_settings.set_expanded(False)
        self.target.currentIndexChanged.connect(self._target_changed)
        self.ranks.valueChanged.connect(self._preview)
        self.partition.currentIndexChanged.connect(self._queue_changed)
        self.remote_mpi.currentIndexChanged.connect(self._preview)
        for field in (self.local_exe, self.local_mpi, self.account, self.module, self.remote_exe):
            field.editingFinished.connect(self._preview)
            field.returnPressed.connect(self._preview)
        self.resume_input.textChanged.connect(self._preview)

        self.delete_job = command("Delete", self._delete_history_job, role="destructiveActionBtn",
                                  hint="Remove job from history")
        history, layout = panel("Job history", paper=True, scrollable=True, actions=(self.delete_job,))
        left.addWidget(history)
        self.history = CompactListWidget()
        self.history.setAccessibleName("ESEN job history")
        self.history.currentItemChanged.connect(self._select_job)
        layout.addWidget(self.history, 1)
        self.use_inputs = command("Reuse", self._reuse_inputs, hint="Reuse inputs")
        self.refresh = command("Refresh", lambda: self._refresh_remote(manual=True), hint="Refresh job")
        self.resume = command("Resume", self._resume_job, hint="Resume job")
        self.stop = command("Stop", self._cancel, role="destructiveActionBtn", hint="Stop job")
        layout.addLayout(button_row(self.use_inputs, self.refresh, self.resume, self.stop))
        self.remote_folder = QLineEdit()
        self.remote_folder.setFixedHeight(CONTROL_HEIGHT)
        self.remote_folder.setPlaceholderText("TRUBA run folder")
        self.remote_folder.setAccessibleName("TRUBA run folder")
        self.remote_folder.textChanged.connect(self._buttons)
        self.attach = command("Attach", self._attach_remote, hint="Attach folder")
        attachment = QHBoxLayout()
        attachment.addWidget(self.remote_folder, 1)
        attachment.addWidget(self.attach)
        layout.addLayout(attachment)
        left.setSizes([470, 230])

        preview, layout = panel("Command preview", paper=True)
        right.addWidget(preview)
        self.preview = code_view("Command preview")
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.preview.setMinimumHeight(72)
        layout.addWidget(self.preview)
        self.input_note = QLabel()
        self.input_note.setObjectName("mutedHintLabel")
        self.input_note.setTextFormat(Qt.TextFormat.PlainText)
        self.input_note.setWordWrap(True)
        layout.addWidget(self.input_note)

        result, layout = panel("Logs and files", paper=True)
        right.addWidget(result)
        self.job_progress = QLabel()
        self.job_progress.setTextFormat(Qt.TextFormat.PlainText)
        self.job_progress.setWordWrap(True)
        self.job_progress.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.job_progress.hide()
        layout.addWidget(self.job_progress)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.log_type = compact_combo((("Standard output", jobs.LOGS[0]), ("Standard error", jobs.LOGS[1])))
        self.log_type.currentIndexChanged.connect(self._show_log)
        layout.addWidget(self.log_type)
        self.log = code_view("Job log tail")
        layout.addWidget(self.log, 2)
        self.results = QListWidget()
        self.results.setAccessibleName("Result files")
        self.results.currentItemChanged.connect(self._buttons)
        layout.addWidget(self.results, 1)
        self.open_file = command("Open File", self._open_result)
        self.open_folder = command("Job Folder", self._open_directory, hint="Open job folder")
        self.open_results_folder = command("Results", self._open_results_directory, hint="Open result folder")
        self.download = command("Download", self._download, hint="Download file")
        layout.addLayout(button_row(self.open_file, self.download, self.open_folder, self.open_results_folder))
        right.setSizes([240, 450])

        truba_card, truba_layout = panel("TRUBA files", paper=True)
        self.truba_browser = TrubaFileBrowser(self.truba, self.executor, command)
        truba_layout.addWidget(self.truba_browser, 1)
        split.addWidget(truba_card)
        split.setStretchFactor(2, 1)
        split.setSizes([385, 570, 315])

        self.start = command("Run", self._start, role="esenRun", hint="Run LAMMPS")
        self.copy_ai = command("Copy AI", self._copy_ai, hint="Copy ESEN file preparation instructions for AI")
        footer = QHBoxLayout()
        footer.addWidget(self.copy_ai)
        footer.addStretch()
        footer.addWidget(self.start)
        outer.addLayout(footer)

    def _size_command_buttons(self):
        # Reserve space for the shared focus border on both sides.
        toolbar_labels = (self.batch_folder, self.upload, self.sync)
        navigation = self.truba_browser.navigation_buttons
        other_buttons = [button for button in self._command_buttons
                         if button not in (*toolbar_labels, *self._compact_input_actions, *navigation)]
        for group in (other_buttons, toolbar_labels, navigation):
            width = max(button.sizeHint().width() for button in group) + 2
            for button in group:
                button.setFixedWidth(width)
        # The two order arrows replace Use As Script in Slurm mode.
        icon_count = len(self._compact_input_actions) - 1
        gaps = (len(toolbar_labels) + icon_count - 1) * INPUT_TOOL_SPACING
        toolbar_width = sum(button.width() for button in toolbar_labels) + icon_count * CONTROL_HEIGHT + gaps
        scrollbar = self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        self._settings_column.setMinimumWidth(max(305, toolbar_width + 2 * PANEL_PADDING + scrollbar))

    def _copy_ai(self):
        QApplication.clipboard().setText(ESEN_AI_PROMPT)
        QToolTip.showText(self.copy_ai.mapToGlobal(QPoint(0, 0)),
                          translate("ESEN instructions copied to the clipboard."), self.copy_ai)

    @staticmethod
    def _form_row(form, name, field, *, field_layout=None):
        label = field_label(name, field)
        label.setObjectName("esenFieldLabel")
        if isinstance(field, CompactComboBox):
            field.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        if isinstance(field, QLineEdit) and not field.toolTip():
            label.setToolTip(name)
            field.textChanged.connect(field.setToolTip)
            field.setToolTip(field.text())
        form.addRow(label, field if field_layout is None else field_layout)

    def _load_profiles(self):
        for key in ("local_exe", "local_mpi", "account", "module", "remote_exe"):
            field = getattr(self, key)
            field.setText(str(self.preferences.value(f"esen/{key}", field.text())))
        for key in ("ranks", "local_memory"):
            field = getattr(self, key)
            try:
                field.setValue(int(self.preferences.value(f"esen/{key}", field.value())))
            except (ValueError, TypeError):
                pass
        for key in ("partition", "remote_mpi"):
            field = getattr(self, key)
            index = field.findData(self.preferences.value(f"esen/{key}", field.currentData()))
            if index >= 0:
                with QSignalBlocker(field):
                    field.setCurrentIndex(index)
        self._profile_partition = self.partition.currentData()

    def _save_profiles(self):
        for key in ("local_exe", "local_mpi", "account", "module", "remote_exe"):
            self.preferences.setValue(f"esen/{key}", getattr(self, key).text())
        for key in ("ranks", "local_memory"):
            self.preferences.setValue(f"esen/{key}", getattr(self, key).value())
        for key in ("partition", "remote_mpi"):
            self.preferences.setValue(f"esen/{key}", getattr(self, key).currentData())

    def _queue_changed(self):
        current = self.partition.currentData()
        if self.module.text() == jobs.MODULES.get(self._profile_partition, jobs.DEFAULT_MODULE):
            self.module.setText(jobs.MODULES.get(current, jobs.DEFAULT_MODULE))
        self._profile_partition = current
        self._preview()

    def _pick_script(self):
        initial = self.folder_root or self.input_root or Path.home()
        name, _ = QFileDialog.getOpenFileName(self, translate("LAMMPS input script"), str(initial))
        if name:
            path = Path(name)
            if self.input_root is None or not path.is_relative_to(self.input_root):
                self.input_root = path.parent
                self.extra.clear()
                with QSignalBlocker(self.whole_folder):
                    self.whole_folder.setChecked(False)
            self.entry = path.relative_to(self.input_root).as_posix()
            self.extra = [name for name in self.extra if name != self.entry]
            self._update_inputs()

    def _pick_root(self):
        initial = self.folder_root or self.input_root or Path.home()
        name = QFileDialog.getExistingDirectory(self, translate("Job folder"), str(initial))
        if name:
            source, root = self.input_root, Path(name)
            items = ([self.entry] if self.entry else []) + self.extra
            if source and all((source / item).is_relative_to(root) for item in items):
                self.entry = (source / self.entry).relative_to(root).as_posix() if self.entry else ""
                self.extra = [(source / item).relative_to(root).as_posix() for item in self.extra]
            else:
                self.entry, self.extra = "", []
            self.input_root = root
            if root != source:
                with QSignalBlocker(self.whole_folder):
                    self.whole_folder.setChecked(False)
            self._update_inputs()

    def _pick_files(self):
        initial = self.folder_root or self.input_root or Path.home()
        files, _ = QFileDialog.getOpenFileNames(self, translate("Load input files"), str(initial))
        self._load_files(files)

    def _pick_batch_folder(self):
        initial = self.preferences.value("esen/batchFolder", str(self.input_root or Path.home()))
        name = QFileDialog.getExistingDirectory(self, translate("Select Folder"), str(initial))
        if name:
            self._load_batch_folder(Path(name))

    def _load_batch_folder(self, path):
        try:
            root = path.resolve(strict=True)
            if not root.is_dir():
                raise ValueError("Choose an existing folder.")
        except (OSError, ValueError) as error:
            self.input_note.setText(translate(str(error)))
            return
        self._clear_batch_source()
        self.preferences.setValue("esen/batchFolder", str(root))
        self.folder_root = root
        self._scan_folder()

    def _target_changed(self, *_):
        if self.folder_root is not None:
            self._scan_folder()
        else:
            self._update_inputs()

    def _slurm_mode(self):
        if self.target.currentData() != "remote":
            return False
        if self.folder_root is not None:
            return self.folder_slurm
        return any(map(jobs.is_slurm_script, (self.entry, *self.extra)))

    def _folder_source(self):
        if self.target.currentData() == "remote" and self.folder_upload:
            return jobs.upload_snapshot(self.history_root, self.folder_upload.upload_id)
        return self.folder_root

    def _scan_folder(self, checked=None):
        if self.folder_scan is not None:
            self.folder_scan[0].cancel()
        root = (self.folder_root if self.folder_slurm and not self.folder_replay
                else self._folder_source())
        remote = self.target.currentData() == "remote"
        self.folder_error = ""
        self.folder_scan = self.executor.submit(jobs.scan_folder_tree, root, include_results=remote), checked
        with QSignalBlocker(self.inputs):
            self.inputs.clear()
        self._preview()

    def _clear_batch_source(self):
        if self.folder_scan is not None:
            self.folder_scan[0].cancel()
        self.folder_root = self.folder_scan = None
        self.folder_error = ""
        self.folder_upload = None
        self.folder_slurm = False
        self.folder_replay = False
        self.upload_error = ""
        self.sync_message = ""

    def _check_folder_scan(self):
        if self.folder_scan is None or not self.folder_scan[0].done():
            return
        (scan, checked), self.folder_scan = self.folder_scan, None
        remote = self.target.currentData() == "remote"
        try:
            directories, names = scan.result()
        except (OSError, ValueError) as error:
            self.folder_error = str(error)
        else:
            self.folder_slurm = any(map(jobs.is_slurm_script, names))
            with QSignalBlocker(self.inputs):
                for name in directories:
                    self.inputs.add_path(InputFileItem(name), self.folder_root)
                for position, name in enumerate(names):
                    item = InputFileItem(name, position)
                    runnable = (jobs.is_slurm_script(name) if remote and self.folder_slurm else
                                jobs.is_input_script(name))
                    if runnable:
                        selected = name in checked if checked is not None else not remote
                        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                        item.setCheckState(0, Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked)
                    self.inputs.add_path(item, self.folder_root)
            self._sort_inputs()
            if not remote and next(self._folder_jobs(), None) is None:
                self.folder_error = "No LAMMPS input scripts named in.* or *.in were found."
        self._preview()

    def _folder_jobs(self):
        for item in self.inputs.job_items():
            if item.checkState(0) == Qt.CheckState.Checked:
                yield item.path

    def _filter_inputs(self):
        query = self.input_filter.text().strip().casefold()
        with QSignalBlocker(self.inputs):
            for item in reversed(list(self.inputs.walk(self.inputs.invisibleRootItem()))):
                hidden = query not in item.path.casefold() and all(
                    item.child(index).isHidden() for index in range(item.childCount()))
                if hidden and item.isSelected():
                    item.setSelected(False)
                item.setHidden(hidden)
                if query and not hidden and item.childCount():
                    item.setExpanded(True)
        self._buttons()

    def _sort_inputs(self):
        with QSignalBlocker(self.inputs):
            self.inputs.sort_view(self.input_sort.currentData())
        self._filter_inputs()

    def _script_neighbor(self, direction):
        if self.input_sort.currentData() != "job" or self.input_filter.text().strip():
            return None
        items = self.inputs.selectedItems()
        scripts = [item for item in self.inputs.job_items() if jobs.is_slurm_script(item.path)]
        if len(items) != 1 or items[0] not in scripts:
            return None
        index = scripts.index(items[0]) + direction
        return scripts[index] if 0 <= index < len(scripts) else None

    def _move_script(self, direction):
        neighbor = self._script_neighbor(direction)
        if self._slurm_mode() and neighbor is not None and not self.upload_task:
            item = self.inputs.selectedItems()[0]
            item.job_order, neighbor.job_order = neighbor.job_order, item.job_order
            self._sort_inputs()
            self._preview()

    def _folder_preview_files(self, name):
        path = jobs.relative_file(name)
        root = (self.folder_root if self.folder_slurm and not self.folder_replay
                else self._folder_source())
        return root / path.parent, path.name, (), True

    def _upload_ready(self, auth):
        return bool(auth and self.folder_upload and self.folder_upload.username == auth.username
                    and not self.upload_task and not self.folder_scan)

    def _upload_inputs(self):
        auth = self.truba.analysis_auth()
        if not self.upload.isEnabled() or not auth:
            return
        self._transfer_inputs(auth)

    def _sync_folders(self):
        self._buttons()
        auth = self.truba.analysis_auth()
        if not self.sync.isEnabled() or auth is None:
            return
        directory = self.truba_browser.open_directory(auth)
        if not directory:
            return
        history = self.history_root.resolve()
        if self.folder_root.is_relative_to(history) or history.is_relative_to(self.folder_root):
            self.sync_message = translate("Choose a job folder outside the ESEN history directory.")
            self._preview()
            return
        future = self.executor.submit(sync_tree, auth, PurePosixPath(directory), self.folder_root)
        self.sync_task = future, auth, directory
        self.upload_error = ""
        self.sync_message = translate("Syncing with {path}…").format(path=project_root(auth.username) / directory)
        self.transfer_progress.setRange(0, 0)
        self.transfer_progress.setFormat("")
        self.transfer_progress.show()
        self._preview()

    def _check_sync(self):
        if self.sync_task is None or not self.sync_task[0].done():
            return
        future, auth, directory = self.sync_task
        self.sync_task = None
        self.transfer_progress.setRange(0, 100)
        self.transfer_progress.hide()
        try:
            sent, received = future.result()
            self.sync_message = translate("Folders synced. Uploaded: {sent} · Downloaded: {received}").format(
                sent=sent, received=received)
        except FileExistsError as error:
            self.sync_message = translate("Folder sync conflict: {path}").format(path=error.filename or str(error))
        except Exception as error:
            self.sync_message = translate("Folder sync failed: {error}").format(error=error)
        self.folder_upload = None
        self._scan_folder(checked=set(self._folder_jobs()))
        self.truba_browser.refresh_after_upload(auth, directory)

    def _transfer_inputs(self, auth, selected=()):
        updates = deque(maxlen=1)

        def report(transferred, total):
            updates.append((transferred, total))

        options = {"progress": report}
        source = self.folder_root
        if source is None:
            source, entry, extra, whole_folder = self._input_files()
            if not whole_folder:
                options["files"] = tuple(filter(None, (entry, *extra)))
        elif self.folder_replay:
            source = self._folder_source()
        self.upload_error = self.sync_message = ""
        if selected:
            previous = self.folder_upload if self.folder_root is not None else self._uploaded_files(auth)
            directory = previous.directory if previous and previous.username == auth.username and not previous.direct else ""
            if directory:
                options["directory"] = directory
            future = self.executor.submit(jobs.sync_slurm_folder, self.history_root, source, auth, selected, **options)
        else:
            directory = self.truba_browser.open_directory(auth)
            future = self.executor.submit(
                jobs.upload_folder, self.history_root, source, auth, directory=directory,
                include_results=not self._slurm_mode(), **options,
            )
        self.upload_task = future, auth, selected, directory, updates
        self._set_transfer_progress(0, "Preparing files for TRUBA…")
        self._preview()

    def _set_transfer_progress(self, value, message):
        self.transfer_progress.setFormat(translate(message))
        self.transfer_progress.setValue(value)
        self.transfer_progress.show()

    def _check_upload(self):
        if self.upload_task is None:
            return
        future, auth, selected, directory, updates = self.upload_task
        if updates:
            transferred, total = updates.pop()
            if transferred >= total:
                self._set_transfer_progress(99 if total else 0, "Finalizing TRUBA transfer…")
            else:
                self._set_transfer_progress(min(99, transferred * 100 // total), "Uploading to TRUBA · %p%")
        if not future.done():
            return
        self.upload_task = None
        succeeded = False
        try:
            upload_id = future.result()
            succeeded = True
            self._set_transfer_progress(100, "TRUBA transfer complete · %p%")
            upload = jobs.FolderUpload(upload_id, auth.username, directory, direct=not selected)
            if self.folder_root is not None:
                self.folder_upload = upload
            else:
                self.files_upload = self._input_files(), upload
            if selected:
                self.truba_browser.set_slurm_directory(auth.username, upload.path())
                self._queue_batch("remote", auth, selected=selected)
            elif self.folder_root is not None and not self.folder_slurm:
                self._scan_folder(checked=set(self._folder_jobs()))
        except FileExistsError as error:
            self.upload_error = translate("A file or folder with different contents already exists: {path}").format(
                path=error.filename or str(error),
            )
        except Exception as error:
            self.upload_error = str(error)
        finally:
            if not succeeded:
                self._set_transfer_progress(self.transfer_progress.value(), "TRUBA transfer failed")
            if not selected:
                self.truba_browser.refresh_after_upload(auth, directory)
        self._preview()

    def _load_files(self, files):
        if not files or self.upload_task or self.sync_task:
            return
        paths = [Path(name) for name in files]
        if len(paths) == 1 and paths[0].is_dir():
            self._load_batch_folder(paths[0])
            return
        if not all(path.is_file() for path in paths):
            self.input_note.setText(translate("Select files or a single folder."))
            return
        try:
            root = (self.input_root if self.folder_root is None else None) or Path(commonpath([path.parent for path in paths]))
            names = [path.relative_to(root).as_posix() for path in paths]
        except ValueError:
            self.input_note.setText(translate("Choose files inside the job folder."))
            return
        try:
            for name in names:
                jobs.relative_file(name)
        except ValueError as error:
            self.input_note.setText(translate(str(error)))
            return
        if self.folder_root is not None:
            self.entry, self.extra = "", []
            with QSignalBlocker(self.whole_folder):
                self.whole_folder.setChecked(False)
        self.input_root = root
        supporting_files = [name for name in names if name != self.entry]
        self.extra = list(dict.fromkeys(self.extra + supporting_files))
        if not self.entry:
            scripts = [name for name in self.extra if jobs.is_input_script(name)]
            if len(self.extra) == 1 or len(scripts) == 1:
                self.entry = (scripts or self.extra)[0]
                self.extra.remove(self.entry)
        self._update_inputs()

    def _use_as_script(self):
        items = self.inputs.selectedItems()
        if len(items) == 1 and items[0].job_order is not None and items[0].path in self.extra:
            name = items[0].path
            self.extra.remove(name)
            if self.entry:
                self.extra.insert(0, self.entry)
            self.entry = name
            self._update_inputs()

    def _remove_files(self):
        selected = {item.path for item in self.inputs.selectedItems() if item.job_order is not None}
        remaining = [name for name in self.extra if name not in selected]
        if remaining != self.extra:
            self.extra = remaining
            self._update_inputs()

    def _update_inputs(self):
        self._clear_batch_source()
        self.files_upload = None
        slurm = self._slurm_mode()
        ordered = [item.path for item in self.inputs.job_items()] if slurm else []
        checked = set(self._folder_jobs()) if slurm else set()
        names = ([self.entry] if self.entry else []) + self.extra
        if slurm:
            names = list(dict.fromkeys([name for name in ordered if name in names] + names))
        with QSignalBlocker(self.inputs):
            self.inputs.clear()
            for position, name in enumerate(names):
                item = InputFileItem(name, position)
                self.inputs.add_path(item, self.input_root)
                if slurm and jobs.is_slurm_script(name):
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(0, Qt.CheckState.Checked if name in checked else Qt.CheckState.Unchecked)
                elif name == self.entry:
                    item.setData(0, Qt.ItemDataRole.AccessibleDescriptionRole, translate("LAMMPS input script"))
                    font = item.font(0)
                    font.setBold(True)
                    item.setFont(0, font)
            self.inputs.expandToDepth(0)
            if names and (slurm or not self.entry):
                self.inputs.setCurrentItem(self.inputs.items()[0])
        self._sort_inputs()
        self._preview()

    def _remote_settings(self, auth):
        return dict(
            partition=self.partition.currentData(),
            account=self.account.text().strip() or (auth.username if auth else "TRUBA_USER"),
            module=self.module.text().strip(),
            executable=self.remote_exe.text().strip(),
            launcher=self.remote_mpi.currentData(),
            resume_input=self.resume_input.text().strip(),
        )

    def _command(self, files=None, *, work_subdir=""):
        root, entry, extra, whole_folder = files if files is not None else self._input_files()
        if not entry:
            raise ValueError("Choose a LAMMPS input script.")
        if root is None or not (root / jobs.relative_file(entry)).is_file():
            raise ValueError("The selected input script is missing.")
        if self.target.currentData() == "local":
            return jobs.local_command(entry, self.ranks.value(), self.local_exe.text().strip(), self.local_mpi.text().strip())
        if self._slurm_mode() and jobs.is_slurm_script(entry):
            return jobs.read_slurm_script(root / jobs.relative_file(entry))
        settings = self._remote_settings(self.truba.analysis_auth())
        if settings["resume_input"]:
            relative = jobs.relative_file(settings["resume_input"])
            if (not (root / relative).is_file() or not (whole_folder or relative.as_posix() in [entry, *extra])):
                raise ValueError("Include the LAMMPS restart input in the job files.")
        ranks, memory, walltime = jobs.full_node_resources(settings["partition"], jobs.BASELINES)
        return jobs.remote_script(entry, ranks, memory=memory, walltime=walltime,
                                  **settings, work_subdir=work_subdir)

    def _input_files(self):
        return self.input_root, self.entry, tuple(self.extra), self.whole_folder.isChecked()

    def _uploaded_files(self, auth):
        if self.files_upload and auth:
            files, upload = self.files_upload
            if upload.username == auth.username and files == self._input_files():
                return upload
        return None

    def _preview(self, *_):
        remote = self.target.currentData() == "remote"
        slurm = self._slurm_mode()
        for field in self.local_fields:
            self.job_form.setRowVisible(field, not remote)
        self.job_form.setRowVisible(self.partition, remote and not slurm)
        self.truba_settings.setVisible(remote and not slurm)
        folder_mode = self.folder_root is not None
        self.use_as_script.setVisible(not folder_mode and not slurm)
        self.remove_inputs.setVisible(not folder_mode)
        self.whole_folder.setVisible(not folder_mode)
        self.pick_root.setVisible(not folder_mode)
        self.upload.setVisible(remote)
        self.sync.setVisible(remote)
        self.sync_status.setText(self.sync_message)
        self.sync_status.setVisible(remote and bool(self.sync_message))
        self.move_up.setVisible(slurm)
        self.move_down.setVisible(slurm)
        batch_mode = folder_mode or slurm
        first = next(self._folder_jobs(), None) if batch_mode else None
        root, entry, extra, whole_folder = self._input_files()
        try:
            if batch_mode and first is None:
                raise ValueError(self.folder_error or ("Scanning folder…" if self.folder_scan
                                                       else "Select a script to run."))
            work_subdir = ""
            if first:
                if folder_mode:
                    root, entry, extra, whole_folder = self._folder_preview_files(first)
                else:
                    entry = first
                if remote and Path(first).parent != Path():
                    work_subdir = Path(first).parent.as_posix()
            invocation = self._command((root, entry, extra, whole_folder), work_subdir=work_subdir)
            self.preview.setPlainText(invocation if remote else subprocess.list2cmdline(invocation))
            self.command_valid = True
        except (OSError, ValueError) as error:
            self.preview.setPlainText(translate(str(error)))
            self.command_valid = False
        source = self.folder_root if folder_mode else root
        lines = [str(source)] if source is not None else []
        if remote and source is not None:
            auth = self.truba.analysis_auth()
            upload = self.folder_upload if folder_mode else self._uploaded_files(auth)
            if self.upload_task:
                lines.append(translate("Uploading files to TRUBA…"))
            elif auth and upload and upload.username == auth.username:
                lines.append(translate("Uploaded to TRUBA: {path}").format(path=upload.path()))
            elif folder_mode and not slurm:
                lines.append(translate("Upload the folder to TRUBA before running selected scripts."))
            if self.upload_error:
                lines.append(translate("File upload failed: {error}").format(error=self.upload_error))
        if slurm:
            lines.append(translate("Use ↑ and ↓ in Job order. Run syncs the inputs and submits each checked Slurm script after the previous job ends."))
        elif remote:
            lines.append(translate("Preview values are estimates. Slurm limits apply at submission."))
        file_count = len(self.inputs.items()) if batch_mode else 0
        if file_count:
            lines.append(translate("{files} files · {scripts} scripts selected").format(
                files=file_count, scripts=sum(1 for _ in self._folder_jobs())))
        elif not folder_mode and not entry:
            lines.append(translate("Select the input file and click Use As Script." if self.extra else
                                   "Add a folder or drop inputs here."))
        if remote and entry and (not folder_mode or first):
            try:
                with (root / entry).open("rb") as stream:
                    script = stream.read(128 * 1024).decode("utf-8", errors="replace")
                if re.search(r"(?:\b[A-Za-z]:[\\/]|(?<!\w)\.\.?\\)", script):
                    lines.append(translate("Windows paths found in the script. Use relative paths with / on TRUBA."))
            except OSError:
                pass
        self.input_note.setText("\n".join(lines))
        self._buttons()

    def _connection_changed(self, *_):
        self._preview()
        self._auto_refresh(manual=bool(self.record and self.record.get("remote_run")))

    def _load_history(self):
        records = []
        for path in self.history_root.glob("*/job.json"):
            if not re.fullmatch(r"[0-9a-f]{32}", path.parent.name):
                continue
            try:
                record = jobs.read_job(path.parent)
                if not record.get("hidden_from_history"):
                    records.append((record, path.parent))
            except (OSError, ValueError):
                continue
        for record, directory in sorted(records, key=lambda pair: pair[0].get("created", ""), reverse=True):
            self._history_item(directory, record)
        if self.history.count():
            self.history.setCurrentRow(0)

    def _history_item(self, directory, record, *, prepend=False):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, str(directory))
        self._update_history_item(item, directory, record)
        if prepend:
            self.history.insertItem(0, item)
            self.history.setCurrentItem(item)
        else:
            self.history.addItem(item)

    def _update_history_item(self, item, directory, record):
        username = record.get("username")
        if record["target"] == "remote" and username and record["state"] in ACTIVE_TRUBA_STATES:
            self._active_truba_jobs[directory] = username
        else:
            self._active_truba_jobs.pop(directory, None)
        target = translate("This computer") if record["target"] == "local" else "TRUBA"
        state = self._state(directory, record)
        duration = job_duration(record, state)
        name = record["name"]
        if record.get("batch_folder"):
            name = f"{record['batch_folder']}/{name}"
        summary = f"{translate(state)} · {target}"
        if duration != "—":
            summary = f"{duration} · {summary}"
        item.setText(f"{name}\n{summary}")
        if record.get("source"):
            item.setToolTip(record["source"])

    def _state(self, directory, record):
        if record["state"] == "Preparing" and directory in self.batch_queue:
            return "Waiting to run"
        if record["state"] == "Preparing" and directory not in self._active_directories():
            return "Interrupted"
        if record["target"] == "local" and record["state"] == "Running" and directory not in self._active_directories():
            return "Interrupted"
        return record["state"]

    def _active_directories(self):
        return [task[0] for task in (self.local_task, self.remote_task) if task]

    def _selected_directory(self):
        item = self.history.currentItem()
        return Path(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _can_delete_job(self, directory, record):
        return bool(record and directory and directory != self.batch_active
                    and directory not in self._active_directories()
                    and not (record["target"] == "remote" and record["state"] in ACTIVE_TRUBA_STATES))

    def _delete_history_job(self):
        directory = self._selected_directory()
        if not self._can_delete_job(directory, self.record):
            return
        reply = QMessageBox.question(
            self, translate("Remove job from history"),
            translate("Remove the selected job from history? Job files and results will be kept."),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes or directory != self._selected_directory():
            return
        try:
            record = jobs.read_job(directory)
            if not self._can_delete_job(directory, record):
                return
            jobs.save_job(directory, record, hidden_from_history=True)
        except (OSError, ValueError) as error:
            self.job_error = str(error)
            self._show_selected()
            return
        self.batch_queue.pop(directory, None)
        self._active_truba_jobs.pop(directory, None)
        self.history.takeItem(self.history.currentRow())
        self._show_selected()

    def _start(self):
        target = self.target.currentData()
        auth = self.truba.analysis_auth() if target == "remote" else None
        if (self.sync_task or self.batch_active or self.batch_queue or
                (target == "remote" and (self.remote_task or not auth or self.upload_task)) or
                (target == "local" and self.local_task)):
            return
        try:
            if self._slurm_mode():
                selected = tuple(self._folder_jobs())
                if self.command_valid and not self.folder_scan and selected:
                    self._transfer_inputs(auth, selected)
                return
            if self.folder_root is not None:
                if target == "remote" and not self._upload_ready(auth):
                    return
                self._queue_batch(target, auth)
                return
            invocation = self._command()
            ranks = self.ranks.value() if target == "local" else None
            directory, record = jobs.new_job(
                self.input_root, self.entry, target, ranks, parent=self.history_root,
                folder_upload=self._uploaded_files(auth), work_subdir="",
            )
        except (OSError, ValueError) as error:
            self.input_note.setText(translate(str(error)))
            return
        files = self._input_files()
        self._save_profiles()
        if target == "remote":
            self._submit_remote(directory, record, files, self._remote_settings(auth), auth)
        else:
            self._run_local(directory, record, files, invocation, self.local_memory.value())
        self._history_item(directory, record, prepend=True)
        self._buttons()

    def _submit_remote(self, directory, record, files, settings, auth):
        future = self.executor.submit(jobs.submit_full_node, directory, record, files, settings, auth)
        self.remote_task = (directory, future)

    def _run_local(self, directory, record, files, invocation, memory_mib):
        self.local_stop = Event()
        future = self.executor.submit(jobs.run_local, directory, record, files, invocation, self.local_stop,
                                      memory_mib=memory_mib)
        self.local_task = (directory, future)

    def _queue_batch(self, target, auth, *, selected=None):
        if selected is None:
            selected = list(self._folder_jobs())
        if not selected:
            raise ValueError("Select a script to run.")
        root = self.folder_root or self.input_root
        upload = None
        if auth:
            upload = self.folder_upload if self.folder_root is not None else self._uploaded_files(auth)
        source = jobs.upload_snapshot(self.history_root, upload.upload_id) if upload else root
        ranks = self.ranks.value() if target == "local" else None
        settings = self._remote_settings(auth) if auth else None
        memory_mib = self.local_memory.value()
        history = self.history_root.resolve()
        prepared = []
        for name in selected:
            try:
                path = jobs.relative_file(name)
                subdir = path.parent
                files = source / subdir, path.name, (), True
                work_subdir = subdir.as_posix() if auth and subdir != Path() else ""
                if not auth and history.is_relative_to(files[0]):
                    raise ValueError("Choose a job folder outside the ESEN history directory.")
                invocation = self._command(files, work_subdir=work_subdir)
            except (OSError, ValueError) as error:
                raise ValueError(f"{root / name}\n{translate(str(error))}") from error
            options = (settings, auth.username) if auth else (invocation, memory_mib)
            prepared.append((name, files, options))
        self._save_profiles()
        errors = []
        for name, files, options in prepared:
            folder, entry = files[:2]
            label = root.name if auth else (Path(root.name) / folder.relative_to(root)).as_posix()
            try:
                directory, record = jobs.new_job(root if auth else folder, name if auth else entry,
                                                 target, ranks, parent=self.history_root, batch_folder=label,
                                                 folder_upload=upload)
            except OSError as error:
                errors.append(f"{label}: {error}")
                continue
            self.batch_queue[directory] = (record, files, options)
            self._history_item(directory, record, prepend=True)
        if errors:
            if not self.batch_queue:
                raise OSError("\n".join(errors))
            self.input_note.setText("\n".join(errors))
        self._run_next_batch_job()
        self._buttons()

    def _run_next_batch_job(self):
        if not self.batch_queue or self.batch_active is not None:
            return
        directory = next(iter(self.batch_queue))
        record, files, options = self.batch_queue[directory]
        remote = record["target"] == "remote"
        task = self.remote_task if remote else self.local_task
        if task is not None:
            return
        auth = self.truba.analysis_auth() if remote else None
        if remote and (not auth or auth.username != options[1]):
            return
        self.batch_queue.pop(directory)
        self.batch_active = directory
        if remote:
            self._submit_remote(directory, record, files, options[0], auth)
        else:
            self._run_local(directory, record, files, *options)
        self.history.setCurrentItem(self._update_finished_job(directory, record))

    def _select_job(self, *_):
        self.job_error = ""
        self._result_key = None
        self.log.clear()
        self._show_selected()
        self._auto_refresh(manual=bool(self.record and self.record.get("remote_run")))

    def _show_selected(self):
        directory = self._selected_directory()
        if directory is None:
            self.record = None
            uploading = bool(self.upload_task and self.upload_task[2])
            self.job_progress.setVisible(uploading)
            if uploading:
                self.job_progress.setText(translate("Uploading files to TRUBA…"))
            self.status.setText(translate(self.job_error or "Select an ESEN job."))
            self.results.clear()
            self._buttons()
            return
        try:
            self.record = record = jobs.read_job(directory)
        except (OSError, ValueError) as error:
            self.record = None
            self.job_progress.hide()
            self.status.setText(str(error))
            self._buttons()
            return
        if record.get("folder_upload") and not record.get("direct_upload") and jobs.is_slurm_script(record["name"]):
            upload = jobs.FolderUpload(record["folder_upload"], record["username"], record.get("upload_directory", ""))
            self.truba_browser.set_slurm_directory(upload.username, upload.path())
        self._update_history_item(self.history.currentItem(), directory, record)
        state = self._state(directory, record)
        details = [record.get("job_id", ""), record.get("created", ""),
                   self.job_error or translate(record.get("detail", ""))]
        exported = self._downloaded_results(directory, record)
        if exported is not None:
            details.append(f"{translate('Results')} · {exported}")
        if record.get("result_export_error"):
            details.append(f"{translate('Automatic result download failed. Use Refresh job to retry.')} "
                           f"{record['result_export_error']}")
        if record.get("files_omitted"):
            details.append(translate("Some files are omitted from this list."))
        stage = state
        if (state == "Completed" and exported is None and not record.get("result_export_error")
                and self.remote_task and directory == self.remote_task[0]
                and jobs.result_directory(directory, record) is not None):
            stage = "Collecting results"
        step = record.get("script_step", "")
        if record["target"] == "local":
            try:
                step = jobs.script_step(jobs.log_tail(directory / jobs.LOGS[0]))
            except OSError:
                pass
        progress = translate("Stage: {stage}").format(stage=translate(stage))
        duration_label = ("Slurm allocation runtime: {time}" if record.get("resume_input")
                          else "Runtime: {time}")
        progress += " · " + translate(duration_label).format(time=job_duration(record, state))
        if step:
            progress += "\n" + translate("Last reported step: {step}").format(step=step)
        self.job_progress.setText(progress)
        self.job_progress.show()
        self.status.setText(" · ".join(filter(None, details))[:900])
        self._show_log()
        self._show_files(directory, record)
        self._buttons()

    def _show_log(self, *_):
        directory = self._selected_directory()
        try:
            content = jobs.log_tail(directory / self.log_type.currentData()) if directory else ""
        except OSError as error:
            content = str(error)
        if content != self.log.toPlainText():
            bar = self.log.verticalScrollBar()
            at_end, position = bar.value() == bar.maximum(), bar.value()
            self.log.setPlainText(content)
            bar.setValue(bar.maximum() if at_end else position)

    def _show_files(self, directory, record):
        key = directory, record["state"], tuple(record.get("files", ()))
        if key == self._result_key:
            return
        self._result_key = key
        current = self.results.currentItem()
        selected = current.text() if current else ""
        if record["target"] == "local":
            logs = [name for name in jobs.LOGS if (directory / name).is_file()]
            work = directory / "work"
            outputs = (file.relative_to(directory).as_posix() for file in work.rglob("*")
                       if file.is_file() and not jobs.is_link(file))
            names = logs + sorted(islice(outputs, jobs.FILE_LIMIT))
        else:
            names = record.get("files", [])
        self.results.clear()
        self.results.addItems(names)
        matching = self.results.findItems(selected, Qt.MatchFlag.MatchExactly)
        if matching:
            self.results.setCurrentItem(matching[0])

    def _buttons(self, *_):
        if not hasattr(self, "download"):
            return
        directory, record = self._selected_directory(), self.record
        auth = self.truba.analysis_auth()
        authenticated = auth is not None
        remote = self.target.currentData() == "remote"
        self.connection.setVisible(remote and not authenticated)
        self.truba_badge.setVisible(remote and authenticated)
        if remote and authenticated:
            working = (self.remote_task is not None or self.upload_task is not None or self.sync_task is not None
                       or auth.username in self._active_truba_jobs.values())
            state = "working" if working else "connected"
            if self.truba_badge.property("state") != state:
                self.truba_badge.setProperty("state", state)
                self.truba_badge.setText(translate("Working" if working else "Connected"))
                self.truba_badge.style().unpolish(self.truba_badge)
                self.truba_badge.style().polish(self.truba_badge)
        ready = authenticated and not self.remote_task if remote else not self.local_task
        batch_busy = bool(self.batch_active or self.batch_queue)
        uploading = self.upload_task is not None or self.sync_task is not None
        if remote and self.folder_root is not None and not self.folder_slurm:
            ready = ready and self._upload_ready(auth)
        self.start.setEnabled(self.command_valid and ready and not batch_busy and not uploading)
        has_inputs = self.folder_root is not None or (
            self.input_root is not None and (self.entry or self.extra or self.whole_folder.isChecked())
        )
        self.upload.setEnabled(bool(remote and authenticated and has_inputs
                                    and not self.folder_scan and not uploading and not batch_busy
                                    and not self.truba_browser.is_slurm_snapshot_open()))
        browser = self.truba_browser
        sync_directory = browser.open_directory(auth)
        self.sync.setEnabled(bool(remote and authenticated and self.folder_root and not self.folder_replay
                                  and not self.folder_scan and not uploading and not batch_busy
                                  and browser.loaded and browser.task is None
                                  and sync_directory and "esen-folders" not in PurePosixPath(sync_directory).parts))
        for control in (self.target, self.batch_folder, self.load_files, self.pick_script, self.inputs,
                        self.pick_root, self.whole_folder, self.input_filter, self.input_sort):
            control.setEnabled(not uploading)
        selected = self.inputs.selectedItems()
        supporting_file_selected = any(item.job_order is not None and item.path in self.extra for item in selected)
        self.use_as_script.setEnabled(len(selected) == 1 and supporting_file_selected and not uploading)
        self.remove_inputs.setEnabled(supporting_file_selected and not uploading)
        self.move_up.setEnabled(not uploading and self._script_neighbor(-1) is not None)
        self.move_down.setEnabled(not uploading and self._script_neighbor(1) is not None)
        self.delete_job.setEnabled(self._can_delete_job(directory, record))
        self.open_folder.setEnabled(directory is not None)
        self.open_results_folder.setEnabled(self._downloaded_results(directory, record) is not None)
        self.attach.setEnabled(bool(authenticated and self.remote_folder.text().strip() and not self.remote_task))
        self.use_inputs.setEnabled(bool(record and not record.get("remote_run") and directory and not uploading
                                        and directory not in self._active_directories()
                                        and (jobs.job_inputs(directory, record) /
                                             jobs.relative_file(record["name"])).is_file()))
        remote_ready = bool(record and record["target"] == "remote" and record.get("username")
                            and authenticated and not self.remote_task
                            and record["state"] != "Preparing"
                            and (record.get("job_id") or record["state"] not in jobs.TERMINAL))
        self.refresh.setEnabled(remote_ready)
        self.resume.setEnabled(bool(remote_ready and record.get("resume_input") and record.get("has_checkpoint")
                                    and record["state"] in {"Failed", "Cancelled", "Interrupted"}))
        self.stop.setEnabled(bool((self.local_task and directory == self.local_task[0] and not self.local_stop.is_set())
                                  or (remote_ready and not record.get("remote_run") and record["state"] not in jobs.TERMINAL)))
        result = self._result_path()
        self.open_file.setEnabled(result is not None and result.is_file())
        self.download.setEnabled(bool(remote_ready and self.results.currentItem()))

    def _tick(self):
        self.truba_browser.check_task()
        self._check_folder_scan()
        self._check_upload()
        self._check_sync()
        for field in ("local_task", "remote_task"):
            task = getattr(self, field)
            if task and task[1].done():
                directory, future = task
                setattr(self, field, None)
                try:
                    future.result()
                    record = jobs.read_job(directory)
                    if directory == self.batch_active and record["state"] in jobs.TERMINAL:
                        self.batch_active = None
                    self._update_finished_job(directory, record)
                except Exception as error:
                    if directory == self._selected_directory() or (
                        field == "remote_task" and directory not in self._active_truba_jobs
                    ):
                        self.job_error = str(error)
        self._run_next_batch_job()
        if self.isVisible():
            self._show_selected()

    def _update_finished_job(self, directory, record):
        for row in range(self.history.count()):
            item = self.history.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == str(directory):
                self._update_history_item(item, directory, record)
                return item
        self._history_item(directory, record, prepend=True)
        return self.history.currentItem()

    def _attach_remote(self):
        auth = self.truba.analysis_auth()
        if not auth or self.remote_task:
            return
        try:
            directory, path = jobs.remote_run_directory(self.history_root, self.remote_folder.text(), auth)
        except ValueError as error:
            self.job_error = str(error)
            self._show_selected()
            return
        for index in range(self.history.count()):
            item = self.history.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == str(directory):
                if self.history.currentItem() == item:
                    self._refresh_remote(manual=True)
                else:
                    self.history.setCurrentItem(item)
                return
        self.job_error = ""
        self.remote_task = (directory, self.executor.submit(jobs.attach_remote_run, directory, path, auth))
        self._buttons()

    def _auto_refresh(self, *, manual=False):
        if self.isVisible():
            self._refresh_remote(manual=manual)

    def _poll_remote(self):
        auth = self.truba.analysis_auth()
        if self.remote_task or not auth:
            return
        directory = next((path for path, username in self._active_truba_jobs.items()
                          if username == auth.username), None)
        if directory is None:
            self._auto_refresh()
            return
        # Reuse the existing poll interval while giving each active job a turn.
        self._active_truba_jobs[directory] = self._active_truba_jobs.pop(directory)
        try:
            record = jobs.read_job(directory)
        except (OSError, ValueError):
            self._active_truba_jobs.pop(directory, None)
            return
        self._refresh_remote(job=(directory, record))

    def _refresh_remote(self, *, manual=False, cancel=False, job=None):
        directory, record = (self._selected_directory(), self.record) if job is None else job
        auth = self.truba.analysis_auth()
        if (not directory or not record or not auth or self.remote_task
                or not record.get("username") or record["state"] == "Preparing"):
            return
        if auth.username != record["username"]:
            self.job_error = "Connect using the TRUBA account that submitted this job."
            return
        if record.get("remote_run") and not manual:
            return
        exported = jobs.result_directory(directory, record)
        was_terminal = record["state"] in jobs.TERMINAL
        needs_export = (record["state"] == "Completed" and exported is not None
                        and not exported.is_dir() and not record.get("result_export_error"))
        if was_terminal and (
            not record.get("job_id") or ("files" in record and not manual and not needs_export)
        ):
            self._active_truba_jobs.pop(directory, None)
            return
        if directory == self._selected_directory():
            self.job_error = ""

        def refresh():
            if not was_terminal or (manual and (record.get("resume_input") or record.get("elapsed_seconds") is None)):
                jobs.refresh_remote(directory, record, auth, cancel=cancel)
            if record["state"] in jobs.TERMINAL and (not was_terminal or "files" not in record or manual):
                jobs.remote_files(directory, record, auth)
            if record["state"] == "Completed" and exported is not None and (manual or not record.get("result_export_error")):
                try:
                    jobs.export_remote_results(directory, record, auth)
                except Exception as error:
                    jobs.save_job(directory, record, result_export_error=str(error))
                else:
                    if record.get("result_export_error"):
                        jobs.save_job(directory, record, result_export_error="")

        self.remote_task = (directory, self.executor.submit(refresh))
        self._buttons()

    def _resume_job(self):
        directory = self._selected_directory()
        auth = self.truba.analysis_auth()
        if not self.resume.isEnabled() or not directory or not auth:
            return
        self.job_error = ""
        self.remote_task = (directory, self.executor.submit(jobs.resume_remote, directory, self.record, auth))
        self._buttons()

    def _cancel(self):
        if self.local_task and self._selected_directory() == self.local_task[0]:
            self.local_stop.set()
            self._buttons()
        else:
            self._refresh_remote(manual=True, cancel=True)

    def _reuse_inputs(self):
        directory = self._selected_directory()
        if directory and self.record:
            if upload_id := self.record.get("folder_upload"):
                self._clear_batch_source()
                self.target.setCurrentIndex(self.target.findData("remote"))
                self.folder_root = Path(self.record["source"])
                self.folder_upload = jobs.FolderUpload(
                    upload_id, self.record["username"], self.record.get("upload_directory", ""),
                    direct=self.record.get("direct_upload", False),
                )
                self.folder_replay = True
                self.resume_input.setText(self.record.get("resume_input", ""))
                self._scan_folder(checked={self.record["name"]})
                return
            self.input_root = directory / "inputs"
            self.entry = self.record["name"]
            self.resume_input.setText(self.record.get("resume_input", ""))
            self.extra = []
            self.ranks.setValue(self.record["ranks"])
            self.whole_folder.setChecked(True)
            self._update_inputs()

    @staticmethod
    def _downloaded_results(directory, record):
        if directory is None or record is None or record["state"] != "Completed":
            return None
        folder = jobs.result_directory(directory, record)
        return folder if folder is not None and folder.is_dir() else None

    def _result_path(self):
        directory = self._selected_directory()
        item = self.results.currentItem()
        if not directory or not item or not self.record:
            return None
        try:
            relative = jobs.relative_file(item.text())
            if self.record["target"] == "remote":
                exported = self._downloaded_results(directory, self.record)
                if exported is not None and (exported / relative).is_file():
                    path = exported / relative
                    if path.resolve().is_relative_to(exported.resolve()):
                        return path
                directory = directory / "downloads"
            path = directory / relative
            return path if path.resolve().is_relative_to(directory.resolve()) else None
        except (OSError, ValueError):
            return None

    def _open_result(self):
        path = self._result_path()
        if path and path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_directory(self):
        directory = self._selected_directory()
        if directory:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _open_results_directory(self):
        directory = self._downloaded_results(self._selected_directory(), self.record)
        if directory is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _download(self):
        directory, item = self._selected_directory(), self.results.currentItem()
        auth = self.truba.analysis_auth()
        if directory and item and self.record and auth and not self.remote_task:
            self.job_error = ""
            future = self.executor.submit(jobs.download_file, directory, self.record, item.text(), auth)
            self.remote_task = (directory, future)
            self._buttons()

    def closeEvent(self, event):
        self._save_profiles()
        super().closeEvent(event)

    def dispose(self):
        if not self._disposed:
            self._disposed = True
            self._save_profiles()
            self.timer.stop()
            self.poll_timer.stop()
            self.local_stop.set()
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.close()
