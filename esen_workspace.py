"""Native ESEN workspace for running and inspecting LAMMPS jobs."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import escape
from itertools import islice
from math import cbrt, sqrt
from operator import attrgetter
import os
from os.path import commonpath
from pathlib import Path, PurePosixPath
import re
import subprocess
from threading import Event
from time import time

from PySide6.QtCore import (
    QModelIndex, QItemSelectionModel, QPoint, QSettings, QSignalBlocker, QSize, QSortFilterProxyModel,
    Qt, QTimer, QUrl, Signal,
)
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDialog, QFormLayout, QFrame, QGridLayout, QHeaderView, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QStyle, QTabBar, QTableView, QTableWidget, QTableWidgetItem, QTabWidget,
    QToolTip, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from datenburg_widgets import qss_asset_url
from esen_ai_prompt import ESEN_AI_PROMPT
from esen_elements import AlloyElements
import esen_jobs as jobs
import esen_md as md
from file_dialog import QFileDialog
from studio_style import (
    ActionFlowLayout, CompactComboBox, CompactDoubleSpinBox, CompactSpinBox, CONTROL_HEIGHT,
    STUDIO_STYLESHEET, SidebarGroupBox, configure_form, field_label, stepper_with_unit,
)
from truba_active_jobs import SLURM_JOB_HEADERS, configure_slurm_job_table, display_duration, display_start
from truba_file_browser import TrubaFileBrowser, modified_date_text
from truba_files import project_root, sync_tree
from ui_language import translate


WORKSPACE_TITLE = "Esen"
ACTIVE_TRUBA_STATES = frozenset({"Submitting", "Submitted", "Queued", "Running", "Cancellation requested"})
ACTIVE_HISTORY_STATES = ACTIVE_TRUBA_STATES | {"Preparing", "Waiting to run"}
PANEL_PADDING = 7
INPUT_TOOL_SPACING = 2
HISTORY_REMOTE_ACTIVE_ROLE = Qt.ItemDataRole.UserRole + 1
HISTORY_REFRESH_OWNER_ROLE = Qt.ItemDataRole.UserRole + 2
HISTORY_QUEUE_ROLE = Qt.ItemDataRole.UserRole + 3


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
#esenWorkspace QPushButton#esenFolderMenu { padding-right: 5px; }
#esenWorkspace QPushButton#esenFolderMenu::menu-indicator { width: 6px; height: 6px; }
#mdPeriodicTable QToolButton { padding: 0; font-size: 11px; min-height: 0; }
#mdPeriodicTable QToolButton::menu-indicator { image: none; width: 0; height: 0; }
#mdPeriodicTable QToolButton[inAlloy="true"] { background: #BFDBFE; border-color: #93C5FD; }
#mdPeriodicTable QToolButton:hover, #mdPeriodicTable QToolButton:focus { border-color: #2563EB; }
#mdPeriodicTable QToolButton:pressed { background: #93C5FD; }
#esenFileFilter { background: #FFFFFF; border: 1px solid #CBD5E1; border-radius: 4px;
                  color: #0F172A; font-size: 11px; padding: 0 3px; min-height: 23px; max-height: 23px; }
#esenFileFilter:hover { border-color: #93C5FD; }
#esenFileFilter:focus { border-color: #3B82F6; }
#esenRun { color: #FFFFFF; background: #285EA6; }
#esenRun:hover { background: #245692; }
#esenRun:focus { border-color: #0F172A; }
#esenRun:pressed { background: #1E477C; }
#esenRun:disabled { background: #EBEBEB; color: #808080; border-color: #D6D6D6; }
""".replace("@ESEN_CHECK@", qss_asset_url("checkbox-check-blue.svg"))


def panel(title="", *, paper=False, scrollable=False, header_action=None):
    card = QFrame()
    card.setProperty("esenCard", "paper" if paper else "true")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(PANEL_PADDING, PANEL_PADDING, PANEL_PADDING, PANEL_PADDING)
    layout.setSpacing(5)
    if title:
        header = QLabel(title)
        header.setObjectName("esenSection")
        header.setFixedHeight(CONTROL_HEIGHT)
        if header_action is None:
            layout.addWidget(header)
        else:
            header_row = QHBoxLayout()
            header_row.setSpacing(4)
            header_row.addWidget(header, 1)
            header_row.addWidget(header_action)
            layout.addLayout(header_row)
    if scrollable:
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(card)
        return scroll, layout
    return card, layout


def command(text, slot=None, *, role="", hint=""):
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
    if slot is not None:
        button.clicked.connect(slot)
    return button


def button_row(*buttons):
    layout = ActionFlowLayout(equal_width=True)
    for button in buttons:
        layout.addWidget(button)
    return layout


class EqualWidthTabBar(QTabBar):
    def tabSizeHint(self, index):
        size = super().tabSizeHint(index)
        size.setWidth(max((QTabBar.tabSizeHint(self, i).width() for i in range(self.count())),
                          default=size.width()))
        return size


def compact_combo(options):
    box = CompactComboBox()
    for label, value in options:
        box.addItem(label, value)
    return box


def code_view(name):
    edit = QPlainTextEdit()
    edit.setReadOnly(True)
    edit.setAccessibleName(name)
    edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    return edit


class EsenQueueModel(QSortFilterProxyModel):
    def filterAcceptsRow(self, row, parent):
        model = self.sourceModel()
        return bool(model.index(row, 0, parent).data(HISTORY_QUEUE_ROLE))

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Vertical and role == Qt.ItemDataRole.DisplayRole:
            return section + 1
        return super().headerData(section, orientation, role)


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

    def add_path(self, item, root, modified=None):
        parent = self.invisibleRootItem()
        folder = ""
        for part in Path(item.path).parts[:-1]:
            folder = f"{folder}/{part}" if folder else part
            if folder not in self.folders:
                self.add_path(InputFileItem(folder), root, modified)
            parent = self.folders[folder]
        directory = item.job_order is None
        if directory:
            self.folders[item.path] = item
        item.setIcon(0, self.folder_icon if directory else self.file_icon)
        if root is not None:
            path = root / item.path
            item.setToolTip(0, str(path))
            timestamp = jobs.file_modified_time(path) if modified is None else modified.get(item.path)
            item.setText(2, modified_date_text(timestamp))
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
        self.setColumnCount(3)
        self.setColumnHidden(1, True)
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.set_headers()
        language = getattr(QApplication.instance(), "_rustmech_language", None)
        if language is not None:
            language.changed.connect(self.set_headers)
        self.setProperty("preserveItemText", True)
        self.setUniformRowHeights(True)
        self.setMinimumHeight(63)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        help_text = translate("Use Ctrl or Shift to select files. Press Space to check or uncheck selected scripts.")
        self.setAccessibleDescription(help_text)
        self.setToolTip(help_text)

    def set_headers(self, *_):
        self.setHeaderLabels([translate("Name"), translate("Job order"), translate("Date modified")])

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
        self.md_plans_root = self.history_root.parent / "md-preparations"
        self.preferences = QSettings("RustMech", "RustMech")
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="esen")
        self.local_task = self.remote_task = None
        self.md_task = None
        self.md_prepared = None
        self.folder_root = None
        self.folder_scan = None
        self.folder_error = ""
        self.folder_upload = None
        self.folder_slurm = False
        self.folder_replay = False
        self.unsent_slurm_jobs = {}
        self.files_upload = None
        self.upload_task = None
        self.upload_error = ""
        self.sync_task = None
        self.sync_message = ""
        self._run_truba_selection = False
        self.run_list_task = None
        self.run_list_message = ""
        self.batch_queue = {}
        self.batch_active = None
        self.reuse_batch = []
        self.history_refresh_queue = deque()
        self._history_preview = None
        self._active_truba_jobs = {}
        self.local_stop = Event()
        self.record = None
        self.input_root = None
        self._browse_root = None
        self._input_locations = [""]
        self._input_location = 0
        self.entry = ""
        self.extra = []
        self.command_valid = False
        self.job_error = ""
        self._result_key = None
        self._profile_partition = None
        self._disposed = False
        self._build()
        language = getattr(QApplication.instance(), "_rustmech_language", None)
        if language is not None:
            for button in (*self._command_buttons, self.active_history_only,
                           self.md_preview_button, self.md_prepare, self.md_run,
                           self.md_apply_lattice, *self.md_file_buttons, *self.md_plan_buttons):
                language.localize(button)
            language.changed.connect(self._localize_md)
            language.changed.connect(self._localize_batch_size)
            language.changed.connect(self._preview)
        self._size_command_buttons()
        self._localize_batch_size()
        self._load_profiles()
        self._md_preview()
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
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("esenWorkspaceTabs")
        tab_bar = EqualWidthTabBar()
        tab_bar.setExpanding(False)
        self.tabs.setTabBar(tab_bar)
        shell.addWidget(self.tabs)
        esen_page = QWidget()
        esen_index = self.tabs.addTab(esen_page, "ESEN")
        self.tabs.setTabToolTip(esen_index, "Thanks to Prof. Dr. Ismail Esen")
        outer = QVBoxLayout(esen_page)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(7)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setObjectName("esenThreeColumns")
        split.setChildrenCollapsible(False)
        split.setHandleWidth(7)
        outer.addWidget(split, 1)

        left = QSplitter(Qt.Orientation.Vertical)
        left.setObjectName("esenInputsAndJobs")
        left.setChildrenCollapsible(False)
        left.setHandleWidth(7)
        left.setMinimumWidth(305)
        right = QSplitter(Qt.Orientation.Vertical)
        right.setObjectName("esenPreviewAndResults")
        right.setChildrenCollapsible(False)
        right.setHandleWidth(7)
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)

        self.batch_folder = command("", role="esenFolderMenu", hint="Select Folder")
        folder_menu = QMenu(self.batch_folder)
        self.scan_folder_action = folder_menu.addAction("Scan folder for jobs", self._pick_batch_folder)
        self.job_folder_action = folder_menu.addAction("Set job folder for selected files", self._pick_root)
        self.batch_folder.setMenu(folder_menu)
        self.upload = command("Send Truba", self._upload_inputs, hint="Upload inputs to the open TRUBA folder")
        self.sync = command("Sync", self._sync_folders,
                            hint="Sync the local folder with the open TRUBA subfolder")
        self.load_files = command("", self._pick_files, hint="Load input files")
        self.pick_script = command("", self._pick_script, hint="LAMMPS input script")
        self.use_as_script = command("", self._use_as_script, hint="Use As Script")
        self.remove_inputs = command("", self._remove_files, hint="Remove selected files")
        self.move_up = command("↑", lambda: self._move_script(-1), hint="Move script up")
        self.move_down = command("↓", lambda: self._move_script(1), hint="Move script down")
        self.input_back = command("", lambda: self._step_input_directory(-1), hint="Previous folder")
        self.input_forward = command("", lambda: self._step_input_directory(1), hint="Next folder")
        self.input_up = command("", self._up_input_directory, hint="Up")
        icons = ((self.batch_folder, QStyle.StandardPixmap.SP_DialogOpenButton),
                 (self.load_files, QStyle.StandardPixmap.SP_FileDialogContentsView),
                 (self.pick_script, QStyle.StandardPixmap.SP_FileIcon),
                 (self.use_as_script, QStyle.StandardPixmap.SP_DialogApplyButton),
                 (self.remove_inputs, QStyle.StandardPixmap.SP_DialogDiscardButton),
                 (self.input_back, QStyle.StandardPixmap.SP_ArrowBack),
                 (self.input_forward, QStyle.StandardPixmap.SP_ArrowForward),
                 (self.input_up, QStyle.StandardPixmap.SP_FileDialogToParent))
        for button, icon in icons:
            button.setIcon(self.style().standardIcon(icon))
            button.setIconSize(QSize(14, 14))
        input_edits = (self.batch_folder, self.load_files, self.pick_script, self.use_as_script,
                       self.remove_inputs, self.move_up, self.move_down)
        input_navigation = (self.input_back, self.input_forward, self.input_up)
        for button in (*input_edits, *input_navigation):
            button.setProperty("compactInputAction", True)
            button.setAccessibleName(button.toolTip())
            button.setFixedWidth(CONTROL_HEIGHT)
        settings, layout = panel(scrollable=True)
        left.addWidget(settings)

        self.input_path = QLineEdit()
        self.input_path.setReadOnly(True)
        self.input_path.setFixedHeight(CONTROL_HEIGHT)
        self.input_path.setAccessibleName("Job folder path")
        self.input_path.textChanged.connect(self.input_path.setToolTip)
        layout.addWidget(self.input_path)

        filters = QHBoxLayout()
        filters.setSpacing(4)
        self.input_filter = QLineEdit()
        self.input_filter.setObjectName("esenFileFilter")
        self.input_filter.setFixedHeight(CONTROL_HEIGHT)
        self.input_filter.setClearButtonEnabled(True)
        self.input_filter.setMinimumWidth(90)
        self.input_filter.setPlaceholderText("Filter")
        self.input_filter.setAccessibleName("Filter input files")
        self.input_filter.setToolTip("Checked files stay checked when hidden by the filter.")
        self.input_filter.textChanged.connect(self._filter_inputs)
        self.input_sort = compact_combo((("Job order", "job"), ("Name (A–Z)", "name"),
                                         ("Format (A–Z)", "format")))
        self.input_sort.setAccessibleName("Sort input files")
        self.input_sort.setToolTip("For manual Slurm ordering, select Job order and clear the filter.")
        self.input_sort.currentIndexChanged.connect(self._sort_inputs)
        self.copy_ai = command("Copy AI", self._copy_ai, hint="Copy ESEN file preparation instructions for AI")
        filters.addWidget(self.input_filter, 1)
        filters.addWidget(self.input_sort)
        layout.addLayout(filters)
        self.inputs = InputFileTree()
        self.inputs.setAcceptDrops(True)
        self.inputs.viewport().setAcceptDrops(True)
        self.inputs.files_dropped.connect(self._load_files)
        self.inputs.setAccessibleName("LAMMPS input files")
        self.inputs.itemChanged.connect(self._select_local_input)
        self.inputs.itemClicked.connect(self._select_local_input)
        self.inputs.itemActivated.connect(self._open_input_directory)
        self.inputs.setExpandsOnDoubleClick(False)
        self.inputs.itemSelectionChanged.connect(self._local_selection_changed)
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
        layout.addWidget(self.whole_folder, alignment=Qt.AlignmentFlag.AlignLeft)
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
        target_row.setSpacing(4)
        target_row.addWidget(self.target)
        target_row.addWidget(self.truba_badge)
        target_row.addStretch()

        self.job_form = form = QFormLayout()
        configure_form(form)
        layout.addLayout(form)
        self._form_row(form, "Run on", self.target, field_layout=target_row)
        form.labelForField(target_row).setFixedHeight(CONTROL_HEIGHT)
        self._form_row(form, "Local CPU cores", self.ranks)
        self._form_row(form, "Local RAM limit (MiB)", self.local_memory)
        self._form_row(form, "LAMMPS executable", self.local_exe)
        self._form_row(form, "MPI launcher", self.local_mpi)
        self.local_fields = (self.ranks, self.local_memory, self.local_exe, self.local_mpi)
        self.partition = compact_combo([(name, name) for name in jobs.CPU_QUEUES])
        self.partition.setCurrentIndex(self.partition.findData(jobs.DEFAULT_PARTITION))
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
        self.history = QTableWidget(0, len(SLURM_JOB_HEADERS))
        self.history.setHorizontalHeaderLabels(SLURM_JOB_HEADERS)
        self._configure_history_table(self.history)
        self.history.setAccessibleName("ESEN job history")
        self.history.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.active_history_only = QCheckBox("Show only active jobs")
        self.active_history_only.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.active_history_only.setFixedHeight(CONTROL_HEIGHT)
        self.active_history_only.toggled.connect(self._filter_history)
        self.select_history = command("Select all", self.history.selectAll)
        history, layout = panel("Job history", paper=True, scrollable=True,
                                header_action=self.active_history_only)
        left.addWidget(history)
        self.history.currentItemChanged.connect(self._select_job)
        self.history.itemSelectionChanged.connect(self._select_job)
        layout.addWidget(self.history, 1)
        self.use_inputs = command("Reuse", self._reuse_inputs, hint="Reuse inputs")
        self.refresh = command("Refresh", self._refresh_history, hint="Refresh job")
        self.resume = command("Resume", self._resume_job, hint="Resume job")
        self.stop = command("Stop", self._cancel, role="destructiveActionBtn", hint="Stop job")
        self.remote_folder = QLineEdit()
        self.remote_folder.setFixedHeight(CONTROL_HEIGHT)
        self.remote_folder.setPlaceholderText("TRUBA run folder")
        self.remote_folder.setAccessibleName("TRUBA run folder")
        self.remote_folder.textChanged.connect(self._buttons)
        self.attach = command("Attach", self._attach_remote, hint="Attach folder")
        layout.addWidget(self.remote_folder)
        left.setSizes([420, 270])

        toolbar = QScrollArea(esen_page)
        toolbar.setObjectName("esenLeftActions")
        toolbar.setAccessibleName("Local input and job history actions")
        toolbar.setFrameShape(QFrame.Shape.NoFrame)
        toolbar.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        toolbar.setWidgetResizable(True)
        actions = QWidget()
        row = QHBoxLayout(actions)
        row.setContentsMargins(0, 1, 0, 2)
        row.setSpacing(INPUT_TOOL_SPACING)
        groups = (
            input_navigation, input_edits, (self.upload, self.sync, self.copy_ai),
            (self.select_history, self.delete_job),
            (self.use_inputs, self.refresh, self.resume, self.stop, self.attach),
        )
        for index, buttons in enumerate(groups):
            if index:
                row.addSpacing(6)
            for button in buttons:
                row.addWidget(button)
        row.addStretch()
        toolbar.setWidget(actions)
        toolbar.setFixedHeight(CONTROL_HEIGHT + 3 + toolbar.horizontalScrollBar().sizeHint().height())
        outer.insertWidget(0, toolbar)

        preview, layout = panel("Command preview", paper=True, scrollable=True)
        preview.setMinimumHeight(130)
        right.addWidget(preview)
        self.preview = code_view("Command preview")
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.preview.setMinimumHeight(72)
        layout.addWidget(self.preview)
        self.input_note = QLabel()
        self.input_note.setObjectName("mutedHintLabel")
        self.input_note.setTextFormat(Qt.TextFormat.PlainText)
        self.input_note.setWordWrap(True)
        self.input_note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.input_note)

        result, layout = panel("Logs and files", paper=True, scrollable=True)
        result.setMinimumHeight(115)
        results_splitter = QSplitter(Qt.Orientation.Vertical)
        results_splitter.setObjectName("esenLogsAndActiveJobs")
        results_splitter.setChildrenCollapsible(False)
        results_splitter.setHandleWidth(7)
        right.addWidget(results_splitter)
        results_splitter.addWidget(result)
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
        self.log.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.log.setMinimumHeight(45)
        layout.addWidget(self.log, 2)
        self.results = QListWidget()
        self.results.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.results.setMinimumHeight(38)
        self.results.setAccessibleName("Result files")
        self.results.currentItemChanged.connect(self._buttons)
        layout.addWidget(self.results, 1)
        self.open_file = command("Open File", self._open_result)
        self.open_folder = command("Job Folder", self._open_directory, hint="Open job folder")
        self.open_results_folder = command("Results", self._open_results_directory, hint="Open result folder")
        self.download = command("Download", self._download, hint="Download file")
        result_actions = QGridLayout()
        result_actions.setSpacing(4)
        result_actions.setColumnStretch(2, 1)
        result_actions.addWidget(self.open_file, 0, 0)
        result_actions.addWidget(self.download, 0, 1)
        result_actions.addWidget(self.open_folder, 1, 0)
        result_actions.addWidget(self.open_results_folder, 1, 1)
        layout.addLayout(result_actions)
        active_jobs, layout = panel("Queued and running jobs", paper=True)
        self.queue_model = EsenQueueModel(self)
        self.queue_model.setSourceModel(self.history.model())
        self.active_jobs = QTableView()
        self.active_jobs.setObjectName("esenActiveJobs")
        self.active_jobs.setModel(self.queue_model)
        self._configure_history_table(self.active_jobs)
        self.active_jobs.setAccessibleName("Queued and running jobs")
        self.active_jobs.clicked.connect(self._select_active_job)
        self.active_jobs.activated.connect(self._select_active_job)
        layout.addWidget(self.active_jobs)
        results_splitter.addWidget(active_jobs)
        results_splitter.setSizes([320, 175])
        right.setSizes([190, 500])

        truba_card, truba_layout = panel("TRUBA files", paper=True)
        self.truba_browser = TrubaFileBrowser(self.truba, self.executor, command, self.preferences)
        self.truba_browser.files.itemSelectionChanged.connect(self._select_truba_input)
        self.truba_browser.files.itemClicked.connect(self._select_truba_input)
        self.truba_browser.state_changed.connect(self._buttons)
        truba_layout.addWidget(self.truba_browser, 1)
        self.jobs_per_script = CompactSpinBox()
        self.jobs_per_script.setRange(0, 9999)
        self.jobs_per_script.valueChanged.connect(self._preview)
        self.jobs_per_script_label = QLabel("Jobs per script")
        self.jobs_per_script_label.setObjectName("esenFieldLabel")
        self.jobs_per_script_label.setBuddy(self.jobs_per_script)
        self.start = command("Run", self._start, role="esenRun", hint="Run LAMMPS")
        self.clear_reuse = command("Clear reuse", self._clear_reuse)
        self.clear_reuse.hide()
        run_toolbar = QHBoxLayout()
        run_toolbar.setSpacing(4)
        run_toolbar.addStretch()
        run_toolbar.addWidget(self.jobs_per_script_label)
        run_toolbar.addWidget(self.jobs_per_script)
        run_toolbar.addWidget(self.clear_reuse)
        run_toolbar.addWidget(self.start)
        truba_layout.addLayout(run_toolbar)
        split.addWidget(truba_card)
        split.setStretchFactor(2, 1)
        split.setSizes([360, 570, 340])
        self._command_buttons = esen_page.findChildren(QPushButton)
        self.tabs.addTab(self._build_md(), "MD Preparation")

    def _build_md(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(7)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setObjectName("esenMdSettingsAndPreview")
        split.setChildrenCollapsible(False)
        split.setHandleWidth(7)
        outer.addWidget(split, 1)

        inputs, layout = panel("Alloy elements", scrollable=True)
        self.md_form = QWidget()
        fields = QVBoxLayout(self.md_form)
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setSpacing(5)
        defaults = [(element, 20) for element in ("Hf", "Nb", "Ta", "Ti", "Zr")]
        self.md_elements = AlloyElements(defaults)
        fields.addWidget(self.md_elements)
        settings, settings_layout = panel()
        fields.addWidget(settings)
        self.md_preview_button = command("Preview model", self._md_preview)
        self.md_prepare = command("Prepare package", lambda: self._start_md(submit=False))
        self.md_run = command("Prepare and run on TRUBA", self._start_md, role="esenRun")
        settings_layout.addLayout(button_row(self.md_prepare, self.md_run))
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        configure_form(form)
        settings_layout.addLayout(form)
        self.md_settings_form = form
        layout.addWidget(self.md_form)
        layout.addStretch()
        split.addWidget(inputs)

        self.md_crystal = compact_combo((("BCC single crystal", "single"), ("BCC polycrystal", "poly")))
        self._form_row(form, "Crystal type", self.md_crystal)
        self.md_order = QLineEdit(str(self.preferences.value("esen/md/order", "")))
        self.md_order.setPlaceholderText("Example: Hf Nb Ta Ti Zr")
        self.md_order.setToolTip("Enter the element order used by indices in the MEAM parameter file.")
        self.md_atoms = CompactSpinBox()
        self.md_atoms.setRange(2, md.MAX_ATOMS)
        self.md_atoms.setValue(250_000)
        self._form_row(form, "Target atom count", self.md_atoms)
        self.md_atoms.setFixedWidth(123)
        self.md_grains = CompactSpinBox()
        self.md_grains.setRange(2, md.MAX_GRAINS)
        self.md_grains.setValue(12)
        self.md_grains.setToolTip("Number of Voronoi grains in the periodic box.")
        self._form_row(form, "Grain count", self.md_grains)
        self.md_lattice = CompactDoubleSpinBox()
        self.md_lattice.setDecimals(4)
        self.md_lattice.setRange(0.0001, 100.0)
        self.md_lattice.setValue(3.40)
        self._form_row(form, "Lattice parameter", self.md_lattice,
                       field_layout=stepper_with_unit(self.md_lattice, "Å"))
        self.md_temperature = CompactSpinBox()
        self.md_temperature.setRange(1, 10000)
        self.md_temperature.setValue(300)
        self._form_row(form, "Temperature", self.md_temperature,
                       field_layout=stepper_with_unit(self.md_temperature, "K"))
        self.md_seed = CompactSpinBox()
        self.md_seed.setRange(1, 900_000_000)
        self.md_seed.setValue(765896)
        self._form_row(form, "Random seed", self.md_seed)
        self.md_seed.setFixedWidth(123)
        self.md_queue = compact_combo([(name, name) for name in jobs.MODULES])
        queue_index = self.md_queue.findData(self.preferences.value("esen/md/queue", jobs.DEFAULT_PARTITION))
        self.md_queue.setCurrentIndex(max(0, queue_index))
        self._form_row(form, "TRUBA queue", self.md_queue)
        self.md_file_buttons = []
        for key, title in (("library", "MEAM library"), ("parameters", "MEAM parameters")):
            field = QLineEdit()
            field.setReadOnly(True)
            field.setText(str(self.preferences.value(f"esen/md/{key}", "")))
            field.setToolTip(field.text())
            setattr(self, f"md_{key}", field)
            pick = command("Browse…", lambda _checked=False, k=key, label=title: self._pick_md_meam(k, label))
            self.md_file_buttons.append(pick)
            row = QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(field, 1)
            row.addWidget(pick)
            self._form_row(form, title, field, field_layout=row)
        self._form_row(form, "MEAM element order", self.md_order)
        self.md_elements.changed.connect(self._md_preview)
        for stepper in (self.md_atoms, self.md_grains, self.md_lattice, self.md_temperature, self.md_seed):
            stepper.editingFinished.connect(self._md_preview)
        self.md_order.editingFinished.connect(self._save_md_order)

        preview_split = QSplitter(Qt.Orientation.Horizontal)
        preview_split.setObjectName("esenMdPreviewAndPlans")
        preview_split.setChildrenCollapsible(False)
        preview_split.setHandleWidth(7)
        split.addWidget(preview_split)
        split.setSizes([480, 700])
        card, layout = panel("Model preview", paper=True, scrollable=True, header_action=self.md_preview_button)
        preview_split.addWidget(card)
        self.md_preview = code_view("Model preview")
        self.md_preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.md_preview.setMinimumHeight(125)
        layout.addWidget(self.md_preview, 1)
        self.md_note = QLabel()
        self.md_note.setTextFormat(Qt.TextFormat.PlainText)
        self.md_note.setWordWrap(True)
        self.md_note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.md_note)
        self._build_md_help(layout)
        preview_split.addWidget(self._build_md_plans())
        preview_split.setSizes([390, 310])
        self.md_queue.currentIndexChanged.connect(self._md_preview)
        self.md_crystal.currentIndexChanged.connect(self._md_preview)
        self.tabs.currentChanged.connect(lambda index: self._refresh_md_plans()
                                         if self.tabs.widget(index) is page else None)
        return page

    def _build_md_plans(self):
        card, layout = panel("Saved MD plans", paper=True)
        card.setMinimumWidth(220)
        self.md_add_plan = command("Add package…", self._add_md_plan)
        self.md_open_plan = command("Open folder", self._open_md_plan)
        self.md_plan_buttons = (self.md_add_plan, self.md_open_plan)
        layout.addLayout(button_row(*self.md_plan_buttons))

        self.md_plan_hint = QLabel()
        self.md_plan_hint.setTextFormat(Qt.TextFormat.PlainText)
        self.md_plan_hint.setWordWrap(True)
        layout.addWidget(self.md_plan_hint)
        self.md_plans = QListWidget()
        self.md_plans.setAccessibleName("Saved MD plans")
        self.md_plans.setWordWrap(True)
        self.md_plans.setMinimumHeight(95)
        self.md_plans.currentItemChanged.connect(self._show_md_plan)
        layout.addWidget(self.md_plans, 1)

        self.md_plan_file = compact_combo([("Model details", ""), *((name, name) for name in md.PLAN_FILES)])
        self.md_plan_file.setAccessibleName("MD package file")
        self.md_plan_file.currentIndexChanged.connect(self._show_md_plan)
        layout.addWidget(self.md_plan_file, alignment=Qt.AlignmentFlag.AlignLeft)
        self.md_plan_source = code_view("MD package file")
        self.md_plan_source.setMinimumHeight(100)
        self.md_plan_source.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        layout.addWidget(self.md_plan_source, 2)
        self._show_md_plan()
        return card

    def _refresh_md_plans(self, selected=None):
        previous = self.md_plans.currentItem()
        if selected is None and previous is not None:
            selected = previous.data(Qt.ItemDataRole.UserRole)
        imported = self.preferences.value("esen/md/importedPlans", [], type=list)
        folders = {Path(path) for path in imported}
        if self.md_plans_root.is_dir():
            folders.update(self.md_plans_root.glob("md-bcc-*"))
        plans = []
        for folder in folders:
            try:
                model = md.read_plan(folder)
                plans.append((folder, model, (folder / md.PLAN_METADATA).stat().st_mtime))
            except (OSError, ValueError):
                continue
        with QSignalBlocker(self.md_plans):
            self.md_plans.clear()
            for folder, model, modified in sorted(plans, key=lambda plan: (-plan[2], str(plan[0]))):
                title = "–".join(model["elements"])
                crystal = translate("BCC polycrystal" if model["grains"] > 1 else "BCC single crystal")
                date = datetime.fromtimestamp(modified).strftime("%Y-%m-%d %H:%M")
                item = QListWidgetItem(f'{title} · {model["temperature"]} K\n'
                                       f'{crystal} · {model["atoms"]:,} {translate("atoms")}\n{date}')
                item.setData(Qt.ItemDataRole.UserRole, folder)
                item.setData(Qt.ItemDataRole.UserRole + 1, model)
                item.setToolTip(str(folder))
                self.md_plans.addItem(item)
                if folder == selected:
                    self.md_plans.setCurrentItem(item)
            if self.md_plans.currentRow() == -1 and self.md_plans.count():
                self.md_plans.setCurrentRow(0)
        self._show_md_plan()

    def _add_md_plan(self):
        folder = QFileDialog.getExistingDirectory(self, translate("Add package…"))
        if not folder:
            return
        try:
            path = Path(folder).resolve()
            md.read_plan(path)
        except (OSError, ValueError) as error:
            self.md_plan_hint.setText(f'{translate("Choose a complete MD package folder.")}\n{error}')
            self.md_plan_hint.show()
            return
        imported = self.preferences.value("esen/md/importedPlans", [], type=list)
        if str(path) not in imported:
            self.preferences.setValue("esen/md/importedPlans", [*imported, str(path)])
        self._refresh_md_plans(path)

    def _show_md_plan(self, *_):
        item = self.md_plans.currentItem()
        self.md_open_plan.setEnabled(item is not None)
        self.md_plan_file.setEnabled(item is not None)
        self.md_plan_hint.setVisible(item is None)
        if item is None:
            self.md_plan_hint.setText(translate("Prepare a package or add an existing MD package folder."))
            self.md_plan_source.clear()
            return
        filename = self.md_plan_file.currentData()
        if not filename:
            model = item.data(Qt.ItemDataRole.UserRole + 1)
            details = [
                f'{translate("Target atom count")}  {model.get("target_atoms", "—")}',
                f'{translate("Prepared atom count")}  {model["atoms"]:,}',
                f'{translate("Grain count")}  {model["grains"]}',
                f'{translate("Temperature")}  {model["temperature"]} K',
                f'{translate("Lattice parameter")}  {model.get("lattice", "—")} Å',
                f'{translate("Random seed")}  {model.get("seed", "—")}',
                f'{translate("Protocol")}  {model.get("protocol", "—")}',
                "",
                translate("Prepared composition"),
                *(f'{element}  {count:,}  ({100 * count / model["atoms"]:.4f} %)'
                  for element, count in zip(model["elements"], model["counts"])),
                "",
                translate("MEAM element order"),
                " ".join(model["meam_order"]),
            ]
            self.md_plan_source.setPlainText("\n".join(details))
            return
        path = item.data(Qt.ItemDataRole.UserRole) / filename
        try:
            with path.open(encoding="utf-8", errors="replace") as source:
                contents = source.read(65_537)
            if len(contents) > 65_536:
                contents = contents[:65_536] + "\n\n" + translate("Open the folder to read the full file.")
        except OSError as error:
            contents = str(error)
        self.md_plan_source.setPlainText(contents)

    def _open_md_plan(self):
        if item := self.md_plans.currentItem():
            path = item.data(Qt.ItemDataRole.UserRole)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                self.md_plan_hint.setText(f'{translate("Could not open the package folder.")}\n{path}')
                self.md_plan_hint.show()

    def _build_md_help(self, layout):
        help_card, help_layout = panel("How to choose parameters")
        layout.addWidget(help_card)
        for text in (
            "Preparation checks the structure and MEAM mapping. Confirm scientific suitability with a small LAMMPS run and compare against reference data for your alloy.",
            "The lattice parameter a is one BCC cell edge in Å. Use a value for the same alloy and phase. The initial 3.4000 Å is an example.",
            "From the BCC (110) XRD spacing, use a = √2 × d₁₁₀. With MEAM, relax a small periodic single crystal at zero pressure. At the target temperature, average the equilibrated NPT box length and divide by the cell repeats along that axis.",
            "BCC has two atoms per cell. For a polycrystal, atom count sets the box volume and grain count sets the average grain volume. Fix the random seed to reproduce grain positions, orientations and alloy site assignments.",
            "The E20 template uses a fixed 3.9 Å CNA cutoff. Check the cutoff for your lattice parameter before interpreting CNA results.",
        ):
            hint = QLabel(text)
            hint.setWordWrap(True)
            hint.setObjectName("mutedHintLabel")
            help_layout.addWidget(hint)
        self.md_xrd_spacing = CompactDoubleSpinBox()
        self.md_xrd_spacing.setDecimals(4)
        self.md_xrd_spacing.setRange(0, 70.7106)
        self.md_xrd_spacing.setSingleStep(0.001)
        xrd_form = QFormLayout()
        configure_form(xrd_form)
        help_layout.addLayout(xrd_form)
        self._form_row(xrd_form, "XRD d₁₁₀", self.md_xrd_spacing,
                       field_layout=stepper_with_unit(self.md_xrd_spacing, "Å"))
        self.md_apply_lattice = command("Use calculated a", self._apply_md_lattice)
        self.md_xrd_result = QLabel()
        self.md_xrd_result.setObjectName("mutedHintLabel")
        xrd_action = ActionFlowLayout()
        xrd_action.addWidget(self.md_apply_lattice)
        xrd_action.addWidget(self.md_xrd_result)
        help_layout.addLayout(xrd_action)
        self.md_xrd_spacing.valueChanged.connect(self._md_lattice_result)
        self._md_lattice_result()

    def _localize_md(self, *_):
        self._size_command_buttons()
        self.md_elements.update_total()
        self._md_preview()
        self._refresh_md_plans()

    def _localize_batch_size(self, *_):
        label = translate("Jobs per script")
        self.jobs_per_script_label.setText(label)
        self.jobs_per_script.setAccessibleName(label)
        self.jobs_per_script.setToolTip(translate("Automatically include all runnable scripts in the folder."))
        self.jobs_per_script.setSpecialValueText(translate("Manual"))
        self.jobs_per_script.setValue(self.jobs_per_script.value())

    def _md_lattice_result(self):
        spacing = self.md_xrd_spacing.value()
        self.md_apply_lattice.setEnabled(spacing > 0)
        self.md_xrd_result.setText(f"a = {sqrt(2) * spacing:.4f} Å" if spacing else "")

    def _apply_md_lattice(self):
        self.md_xrd_spacing.interpretText()
        if spacing := self.md_xrd_spacing.value():
            self.md_lattice.setValue(sqrt(2) * spacing)
            self._md_preview()

    def _pick_md_meam(self, key, title):
        field = getattr(self, f"md_{key}")
        filename, _ = QFileDialog.getOpenFileName(self, translate(title), field.text(),
                                                  translate("MEAM files (*.meam);;All files (*)"))
        if filename:
            self.md_prepared = None
            field.setText(filename)
            field.setToolTip(filename)
            self.preferences.setValue(f"esen/md/{key}", filename)
            self.md_order.clear()
            try:
                self.md_order.setText(md.documented_meam_order(Path(self.md_library.text()),
                                                              Path(self.md_parameters.text())))
                self.md_note.clear()
            except OSError as error:
                self.md_note.setText(str(error))
            self._save_md_order()

    def _save_md_order(self):
        self.preferences.setValue("esen/md/order", self.md_order.text())

    def _md_model(self):
        for stepper in (self.md_atoms, self.md_grains, self.md_lattice, self.md_temperature, self.md_seed):
            stepper.interpretText()
        composition = self.md_elements.composition()
        if not composition:
            raise ValueError(translate("Add at least one element."))
        return md.plan_model(composition, self.md_atoms.value(), self.md_lattice.value(),
                             self.md_temperature.value(), self.md_seed.value(),
                             grains=self.md_grains.value() if self.md_crystal.currentData() == "poly" else 1)

    def _md_preview(self):
        self.md_settings_form.setRowVisible(self.md_grains, self.md_crystal.currentData() == "poly")
        try:
            model = self._md_model()
        except ValueError as error:
            self.md_preview.setPlainText(str(error))
            return
        if model.grains > 1:
            mean_size = model.box[0] / cbrt(model.grains)
            lines = [translate("BCC polycrystal"),
                     f"{translate('Grain count')}   {model.grains}",
                     f"{translate('Characteristic grain size')}   {mean_size:.3f} Å",
                     translate("Atom count and composition counts are estimates until Atomsk builds the grain boundaries.")]
        else:
            cell = " × ".join(map(str, model.repeats))
            lines = [f"{translate('BCC supercell')}   {cell}",
                     f"{translate('Generated atom count')}   {model.atoms:,}"]
            if model.atoms != model.target_atoms:
                lines.append(translate("The nearest nearly cubic BCC supercell is shown."))
        dimensions = " × ".join(f"{edge:.3f}" for edge in model.box)
        lines.extend([
            f"{translate('Box dimensions')}   {dimensions} Å",
            f"{translate('Target atom count')}   {model.target_atoms:,}",
            "",
            *(f"{element:<3}  {count:>10,}    {100 * count / model.atoms:8.4f} %"
              for element, count in zip(model.elements, model.counts)),
            "",
            translate("Atomsk → Minimization → Equilibration → Tension and CNA"),
            f"TRUBA · {translate('Queue')}   {self.md_queue.currentText()}",
        ])
        self.md_preview.setPlainText("\n".join(lines))

    def _start_md(self, *, submit=True):
        if self.md_task is not None:
            return
        self._md_preview()
        auth = self.truba.analysis_auth() if submit else None
        try:
            if submit and auth is None:
                raise ValueError(translate("Connect using the main TRUBA button."))
            model = self._md_model()
            order = md.meam_order(self.md_order.text(), model.elements)
            library, parameters = Path(self.md_library.text()), Path(self.md_parameters.text())
            if not library.is_file() or not parameters.is_file():
                raise ValueError(translate("Choose the MEAM library and parameter files."))
            settings = None
            if auth is not None:
                partition = self.md_queue.currentData()
                settings = {**self._remote_settings(auth), "partition": partition, "resume_input": md.RESTART}
                if partition != self.partition.currentData() or settings["module"] == jobs.DEFAULT_MODULE:
                    settings["module"] = jobs.MODULES[partition]
                self._remote_command(md.ENTRY, settings)
                self.preferences.setValue("esen/md/queue", partition)
            inputs = (model, library, parameters, order)
            request = (inputs, tuple(path.stat().st_mtime_ns for path in (library, parameters)))
            if self.md_prepared is not None and self.md_prepared[0] == request and self.md_prepared[1].is_dir():
                self._finish_md(self.md_prepared[1], settings, auth)
                return
            executable = md.atomsk_executable()
            future = self.executor.submit(md.prepare_job, self.md_plans_root, *inputs, executable)
        except (OSError, ValueError) as error:
            self.md_note.setText(str(error))
            return
        self.md_task = future, request, settings, auth
        self.md_form.setEnabled(False)
        self.md_prepare.setEnabled(False)
        self.md_run.setEnabled(False)
        self.md_note.setText(translate("Generating the structure with Atomsk…"))

    def _check_md(self):
        if self.md_task is None or not self.md_task[0].done():
            return
        future, request, settings, auth = self.md_task
        if (settings is not None and self._md_submission_busy()
                and self.truba.analysis_auth() == auth and future.exception() is None):
            self.md_note.setText(translate("Model prepared. Waiting for ESEN to finish its current transfer…"))
            return
        self.md_task = None
        self.md_form.setEnabled(True)
        self.md_prepare.setEnabled(True)
        self.md_run.setEnabled(True)
        try:
            folder = future.result()
            self.md_prepared = request, folder
            self._finish_md(folder, settings, auth)
        except Exception as error:
            folder = self.md_prepared[1] if self.md_prepared and self.md_prepared[0] == request else None
            self.md_note.setText(f"{error}\n{folder}" if folder is not None else str(error))

    def _md_submission_busy(self):
        return bool(self.remote_task or self.upload_task or self.sync_task or self.run_list_task)

    def _finish_md(self, folder, settings, auth):
        self._refresh_md_plans(folder)
        if settings is None:
            self.md_note.setText(translate("Package prepared at {path}").format(path=folder))
        else:
            self._submit_md(folder, settings, auth)

    def _submit_md(self, folder, settings, auth):
        if self.truba.analysis_auth() != auth:
            raise ValueError(translate("TRUBA connection changed. Press Prepare and run on TRUBA to submit the prepared model."))
        if self._md_submission_busy():
            raise ValueError(translate("ESEN is busy. Press Prepare and run on TRUBA again when it is ready."))
        directory, record = jobs.new_job(folder, md.ENTRY, "remote", None, parent=self.history_root,
                                         batch_folder=folder.name)
        self._save_profiles()
        self._submit_remote(directory, record, (folder, md.ENTRY, (), True), settings, auth)
        self.md_prepared = None
        item = self._history_item(directory, record, prepend=True)
        self.history.setCurrentItem(item, QItemSelectionModel.SelectionFlag.ClearAndSelect
                                    | QItemSelectionModel.SelectionFlag.Rows)
        self.md_note.setText(translate("Follow submission, progress and results in ESEN job history."))
        self.tabs.setCurrentIndex(0)
        self._buttons()

    @staticmethod
    def _configure_history_table(table):
        configure_slurm_job_table(table)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        table.setMinimumSize(0, 74)
        header = table.horizontalHeader()
        for position, column in enumerate((4, 5, 6, 7), start=2):
            header.moveSection(header.visualIndex(column), position)

    def _size_command_buttons(self):
        # Reserve space for the shared focus border on both sides.
        transfer_buttons = (self.upload, self.sync)
        history_actions = (self.select_history, self.delete_job)
        history_commands = (self.use_inputs, self.refresh, self.resume, self.stop)
        md_actions = (self.md_prepare, self.md_run)
        result_actions = (self.open_file, self.download, self.open_folder, self.open_results_folder)
        for group in (transfer_buttons, history_actions, history_commands,
                      result_actions, md_actions, self.md_file_buttons, self.md_plan_buttons):
            width = max(button.sizeHint().width() for button in group) + 2
            for button in group:
                button.setFixedWidth(width)

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
        queue = self.preferences.value("esen/queue")
        if queue is None:
            # The old profile saved debug even when the user never picked a queue.
            legacy_queue = self.preferences.value("esen/partition")
            queue = jobs.DEFAULT_PARTITION if legacy_queue == "debug" else legacy_queue
        for field, value in ((self.partition, queue), (self.remote_mpi, self.preferences.value("esen/remote_mpi"))):
            index = field.findData(value)
            if index >= 0:
                with QSignalBlocker(field):
                    field.setCurrentIndex(index)
        self._profile_partition = self.partition.currentData()

    def _save_profiles(self):
        for key in ("local_exe", "local_mpi", "account", "module", "remote_exe"):
            self.preferences.setValue(f"esen/{key}", getattr(self, key).text())
        for key in ("ranks", "local_memory"):
            self.preferences.setValue(f"esen/{key}", getattr(self, key).value())
        self.preferences.setValue("esen/queue", self.partition.currentData())
        self.preferences.setValue("esen/remote_mpi", self.remote_mpi.currentData())

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
        if self.reuse_batch:
            self._preview()
        elif self.folder_root is not None:
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

    def _open_input_directory(self, item, _column):
        if item.job_order is None:
            self._visit_input_directory(item.path)

    def _up_input_directory(self):
        parent = PurePosixPath(self._input_locations[self._input_location]).parent
        self._visit_input_directory(parent.as_posix() if parent.parts else "")

    def _visit_input_directory(self, path):
        if path == self._input_locations[self._input_location]:
            return
        self._input_location += 1
        self._input_locations[self._input_location:] = [path]
        self._restore_input_directory()
        self._buttons()

    def _step_input_directory(self, step):
        self._input_location += step
        self._restore_input_directory()
        self._buttons()

    def _sync_input_browse_root(self):
        root = self.folder_root or self.input_root
        if root != self._browse_root:
            self._browse_root = root
            self._input_locations = [""]
            self._input_location = 0
            self.inputs.setRootIndex(QModelIndex())
        return root

    def _restore_input_directory(self):
        self._sync_input_browse_root()
        path = self._input_locations[self._input_location]
        if path and path not in self.inputs.folders:
            self._input_locations = [""]
            self._input_location = 0
            path = ""
        else:
            locations = [name for name in self._input_locations if not name or name in self.inputs.folders]
            if locations != self._input_locations:
                self._input_location = sum(not name or name in self.inputs.folders
                                           for name in self._input_locations[:self._input_location])
                self._input_locations = locations
        folder = self.inputs.folders.get(path)
        with QSignalBlocker(self.inputs):
            self.inputs.clearSelection()
            self.inputs.setRootIndex(self.inputs.indexFromItem(folder) if folder else QModelIndex())
        self.inputs.scrollToTop()

    def _clear_batch_source(self):
        self.reuse_batch = []
        if self.folder_scan is not None:
            self.folder_scan[0].cancel()
        self.folder_root = self.folder_scan = None
        self.folder_error = ""
        self.folder_upload = None
        self.folder_slurm = False
        self.folder_replay = False
        self.unsent_slurm_jobs.clear()
        self.upload_error = ""
        self.sync_message = ""
        self._run_truba_selection = False
        self.truba_browser.files.clearSelection()

    def _select_local_input(self, *_):
        self._run_truba_selection = False
        self.truba_browser.files.clearSelection()
        self._preview()

    def _local_selection_changed(self):
        if self.inputs.selectedItems():
            self._select_local_input()
        else:
            self._buttons()

    def _select_truba_input(self, *_):
        if self.target.currentData() != "remote":
            return
        if self.truba_browser.selected_file(self.truba.analysis_auth()):
            self._run_truba_selection = True
            with QSignalBlocker(self.inputs):
                self.inputs.clearSelection()
        if self._run_truba_selection:
            self._preview()

    def _check_folder_scan(self):
        if self.folder_scan is None or not self.folder_scan[0].done():
            return
        (scan, checked), self.folder_scan = self.folder_scan, None
        remote = self.target.currentData() == "remote"
        try:
            directories, names, modified = scan.result()
        except (OSError, ValueError) as error:
            self.folder_error = str(error)
        else:
            self.folder_slurm = any(map(jobs.is_slurm_script, names))
            with QSignalBlocker(self.inputs):
                for name in directories:
                    self.inputs.add_path(InputFileItem(name), self.folder_root, modified)
                for position, name in enumerate(names):
                    item = InputFileItem(name, position)
                    runnable = (jobs.is_slurm_script(name) if remote and self.folder_slurm else
                                jobs.is_input_script(name))
                    if runnable and (not self.unsent_slurm_jobs or name in self.unsent_slurm_jobs):
                        selected = name in checked if checked is not None else not remote
                        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                        item.setCheckState(0, Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked)
                    self.inputs.add_path(item, self.folder_root, modified)
            self._restore_input_directory()
            self._sort_inputs()
            if not remote and next(self._folder_jobs(), None) is None:
                self.folder_error = "No LAMMPS input scripts named in.* or *.in were found."
        self._preview()

    def _folder_jobs(self):
        for item in self.inputs.job_items():
            if item.checkState(0) == Qt.CheckState.Checked:
                yield item.path

    def _automatic_folder(self):
        return (self.target.currentData() == "remote" and self.folder_root is not None
                and not self.folder_replay and not self._run_truba_selection
                and not self.reuse_batch and not self.unsent_slurm_jobs)

    def _automatic_jobs(self):
        entries = (item.path for item in self.inputs.job_items()
                   if item.data(0, Qt.ItemDataRole.CheckStateRole) is not None)
        if self.folder_slurm or not (restart := self.resume_input.text().strip()):
            return tuple(entries)
        restart = restart.replace("\\", "/").casefold()
        return tuple(name for name in entries if not f"/{name.casefold()}".endswith(f"/{restart}"))

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
        self._select_local_input()
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
            self._restore_input_directory()
            if names and (slurm or not self.entry):
                path = self._input_locations[self._input_location]
                first = next((item for item in self.inputs.items()
                              if not path or item.path.startswith(f"{path}/")), None)
                if first is not None:
                    self.inputs.setCurrentItem(first)
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
        return self._remote_command(entry, settings, work_subdir=work_subdir)

    @staticmethod
    def _remote_command(entry, settings, *, work_subdir=""):
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
        using_truba = remote and self._run_truba_selection
        auth = self.truba.analysis_auth() if remote else None
        remote_file = self.truba_browser.selected_file(auth) if using_truba else None
        slurm = (bool(remote_file and (jobs.is_slurm_script(remote_file[1]) or remote_file[1] == jobs.RUN_LIST))
                 if using_truba else self._slurm_mode())
        for field in self.local_fields:
            self.job_form.setRowVisible(field, not remote)
        self.job_form.setRowVisible(self.partition, remote and not slurm)
        self.truba_settings.setVisible(remote and not slurm)
        folder_mode = self.folder_root is not None
        can_group = self._automatic_folder()
        automatic = can_group and self.jobs_per_script.value() > 0
        self.jobs_per_script_label.setVisible(can_group)
        self.jobs_per_script.setVisible(can_group)
        self.use_as_script.setVisible(not folder_mode and not slurm)
        self.remove_inputs.setVisible(not folder_mode)
        self.whole_folder.setVisible(not folder_mode)
        self.job_folder_action.setVisible(not folder_mode)
        self.upload.setVisible(remote)
        self.sync.setVisible(remote)
        self.sync_status.setText(self.sync_message)
        self.sync_status.setVisible(remote and bool(self.sync_message))
        self.clear_reuse.setVisible(bool(self.reuse_batch))
        self.move_up.setVisible(slurm and not using_truba)
        self.move_down.setVisible(slurm and not using_truba)
        if self.reuse_batch:
            self.preview.setPlainText("\n".join(
                f"{'TRUBA' if record['target'] == 'remote' else translate('This computer')} · {record['name']}"
                for _, record in self.reuse_batch
            ))
            self.input_note.setText(translate("{count} jobs ready to reuse. Run queues them on their saved targets.").format(
                count=len(self.reuse_batch)))
            self.command_valid = True
            self._buttons()
            return
        if using_truba:
            self._preview_truba_file(remote_file, auth)
            return
        batch_mode = folder_mode or slurm
        automatic_jobs = self._automatic_jobs() if automatic else ()
        first = next(iter(automatic_jobs), None) if automatic else (
            next(self._folder_jobs(), None) if batch_mode else None)
        root, entry, extra, whole_folder = self._input_files()
        try:
            if batch_mode and first is None:
                raise ValueError(self.folder_error or ("Scanning folder…" if self.folder_scan
                                                       else "No runnable scripts found." if automatic
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
            elif folder_mode and not slurm and not automatic:
                lines.append(translate("Upload the folder to TRUBA before running selected scripts."))
            elif entry and not slurm:
                lines.append(translate("Run uploads the selected local inputs to TRUBA."))
            if self.upload_error:
                lines.append(translate("File upload failed: {error}").format(error=self.upload_error))
        if automatic:
            lines.append(translate("{count} jobs found · up to {maximum} per Slurm script").format(
                count=len(automatic_jobs), maximum=self.jobs_per_script.value()))
            if self.jobs_per_script.value() > 1:
                lines.append(translate("Grouped jobs run in sequence and share one Slurm time limit."))
                if slurm:
                    lines.append(translate("Slurm scripts with different resource settings run in separate groups."))
                elif self.resume_input.text().strip():
                    lines.append(translate("Jobs with a restart input run separately."))
        elif self.unsent_slurm_jobs:
            lines.append(translate("Run submits the checked jobs that have not been sent to Slurm yet."))
        elif slurm:
            lines.append(translate("Use ↑ and ↓ in Job order. Run syncs the inputs and sends each checked Slurm script as a separate job."))
        elif remote:
            lines.append(translate("Preview values are estimates. Slurm limits apply at submission."))
        file_count = len(self.inputs.items()) if batch_mode else 0
        if file_count and not automatic:
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

    def _preview_truba_file(self, selected, auth):
        self.command_valid = False
        self.input_note.clear()
        try:
            if not selected:
                raise ValueError("Select a script in the TRUBA file browser.")
            folder, name = selected
            self.input_note.setText(str(project_root(auth.username) / folder / name))
            if folder.parts:
                jobs.relative_file(folder.as_posix())
            jobs.relative_file(name)
            if name == jobs.RUN_LIST:
                invocation = translate("Submit every Slurm script listed in Run.esen.")
                if self.run_list_message:
                    self.input_note.setText(f"{self.input_note.text()}\n{self.run_list_message}")
            elif jobs.is_slurm_script(name):
                invocation = translate("Submit the selected TRUBA Slurm script.")
            elif jobs.is_input_script(name):
                invocation = self._remote_command(name, self._remote_settings(auth))
            else:
                raise ValueError("Select a Slurm or LAMMPS input script on TRUBA.")
            self.preview.setPlainText(invocation)
            self.command_valid = True
        except (OSError, ValueError) as error:
            self.preview.setPlainText(translate(str(error)))
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
        if self.history.rowCount():
            self.history.setCurrentCell(0, 0)

    def _history_item(self, directory, record, *, prepend=False):
        row = 0 if prepend else self.history.rowCount()
        self.history.insertRow(row)
        item = QTableWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, str(directory))
        self.history.setItem(row, 0, item)
        self._update_history_item(item, directory, record)
        if prepend and not self.history.isRowHidden(row) and len(self._history_selection()) <= 1:
            self.history.setCurrentItem(item, QItemSelectionModel.SelectionFlag.ClearAndSelect
                                        | QItemSelectionModel.SelectionFlag.Rows)
        return item

    def _update_history_item(self, item, directory, record):
        username = record.get("username")
        item.setData(HISTORY_REMOTE_ACTIVE_ROLE,
                     record["target"] == "remote" and record["state"] in ACTIVE_TRUBA_STATES)
        can_refresh = (record["target"] == "remote" and record["state"] != "Preparing"
                       and (record.get("job_id") or record["state"] not in jobs.TERMINAL))
        item.setData(HISTORY_REFRESH_OWNER_ROLE, username if can_refresh else None)
        if record["target"] == "remote" and username and record["state"] in ACTIVE_TRUBA_STATES:
            self._active_truba_jobs[directory] = username
        else:
            self._active_truba_jobs.pop(directory, None)
        state = self._state(directory, record)
        local = record["target"] == "local"
        attached = record.get("remote_run")
        group = record.get("group_entries")
        script_name = "—" if attached else record["name"]
        if group:
            script_name = translate("Group of {count} jobs").format(count=len(group))
        work_name = record["name"] if attached else record.get("batch_folder") or "—"
        slurm = record.get("slurm", {})
        start = slurm.get("start", "")
        if local and record.get("started_at") is not None:
            start = datetime.fromtimestamp(record["started_at"], timezone.utc).isoformat()
        values = (
            record.get("job_id") or "—", state, work_name, "This computer" if local else "TRUBA", script_name,
            slurm.get("cpus") or record.get("ranks") or "—", slurm.get("nodes") or "—",
            slurm.get("partition") or record.get("partition") or "—",
            display_start(start) if state != "Queued" else "—",
            job_duration(record, state) if state != "Queued" else "—",
            display_duration(slurm.get("remaining", "")) if state == "Running" else "—",
        )
        row = item.row()
        for column, value in enumerate(values):
            cell = self.history.item(row, column)
            if cell is None:
                cell = QTableWidgetItem()
                self.history.setItem(row, column, cell)
            cell.setText(str(value))
        if attached:
            self.history.item(row, 2).setToolTip(f"<qt>{escape(attached)}</qt>")
        else:
            source = record.get("truba_input_folder") or record.get("source")
            script = "<br>".join(map(escape, group or (script_name,)))
            if source:
                script += "<br>" + escape(source)
            self.history.item(row, 4).setToolTip(f"<qt>{script}</qt>")
        item.setData(HISTORY_QUEUE_ROLE, state in {"Queued", "Running"})
        with QSignalBlocker(self.history):
            selection_changed = self._filter_history_row(row)
        if selection_changed:
            self._select_job()

    def _filter_history_row(self, row):
        hidden = self.active_history_only.isChecked() and self.history.item(row, 1).text() not in ACTIVE_HISTORY_STATES
        # Qt's Select All can include hidden rows. Discard those selections before revealing them.
        selection_changed = (hidden or self.history.isRowHidden(row)) and self.history.item(row, 0).isSelected()
        if selection_changed:
            self.history.selectionModel().select(self.history.model().index(row, 0),
                                                QItemSelectionModel.SelectionFlag.Deselect
                                                | QItemSelectionModel.SelectionFlag.Rows)
        self.history.setRowHidden(row, hidden)
        return selection_changed

    def _filter_history(self):
        with QSignalBlocker(self.history):
            for row in range(self.history.rowCount()):
                self._filter_history_row(row)
        self._select_job()

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

    def _history_focus(self):
        current = self.history.item(self.history.currentRow(), 0)
        if current is not None and current.isSelected() and not self.history.isRowHidden(current.row()):
            return current
        return next(iter(self._history_selection()), None)

    @staticmethod
    def _history_directory(item):
        return Path(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _selected_directory(self):
        return self._history_directory(self._history_focus())

    def _history_selection(self):
        rows = self.history.selectionModel().selectedRows()
        return [self.history.item(row, 0) for row in sorted(index.row() for index in rows)
                if not self.history.isRowHidden(row)]

    def _select_active_job(self, index):
        row = self.queue_model.mapToSource(index).row()
        if row >= 0:
            self.history.setCurrentCell(row, 0)

    def _can_delete_job(self, directory, remote_active):
        return (directory != self.batch_active and directory not in self._active_directories()
                and not remote_active)

    def _delete_history_job(self):
        selection = self._history_selection()
        eligible = [(item, self._history_directory(item)) for item in selection
                    if self._can_delete_job(self._history_directory(item), item.data(HISTORY_REMOTE_ACTIVE_ROLE))]
        if not eligible:
            return
        message = "Remove the selected job from history? Job files and results will be kept."
        if len(selection) > 1:
            message = ("Remove {count} selected jobs from history? "
                       "Active jobs, job files and results will be kept.")
        reply = QMessageBox.question(
            self, translate("Remove job from history"), translate(message).format(count=len(eligible)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        errors = []
        with QSignalBlocker(self.history):
            for item, directory in eligible:
                try:
                    record = jobs.read_job(directory)
                    remote_active = record["target"] == "remote" and record["state"] in ACTIVE_TRUBA_STATES
                    if not self._can_delete_job(directory, remote_active):
                        errors.append(translate("Job became active: {name}").format(name=record["name"]))
                        continue
                    jobs.save_job(directory, record, hidden_from_history=True)
                except (OSError, ValueError) as error:
                    errors.append(str(error))
                    continue
                self.batch_queue.pop(directory, None)
                self._active_truba_jobs.pop(directory, None)
                self.history.removeRow(item.row())
        self._select_job()
        if errors:
            self.job_error = "\n".join(errors)
            self._show_selected()

    def _start(self):
        if self.reuse_batch:
            if self.start.isEnabled():
                self._queue_reuse_batch()
            return
        target = self.target.currentData()
        auth = self.truba.analysis_auth() if target == "remote" else None
        if (self.sync_task or self.run_list_task or self.batch_active or self.batch_queue or
                (target == "remote" and (self.remote_task or not auth or self.upload_task or self.truba_browser.deleting)) or
                (target == "local" and self.local_task)):
            return
        try:
            if target == "remote" and self._run_truba_selection:
                self._preview()
                selected = self.truba_browser.selected_file(auth)
                if self.start.isEnabled() and selected:
                    folder, name = selected
                    if name == jobs.RUN_LIST:
                        self.run_list_message = translate("Reading Run.esen from TRUBA…")
                        self.run_list_task = self.executor.submit(jobs.read_truba_run_list, auth, folder), auth, folder
                        self._preview()
                        return
                    directory, record = jobs.new_job(None, name, "remote", None, parent=self.history_root,
                                                     truba_input_folder=folder.as_posix())
                    self._save_profiles()
                    self._submit_remote(directory, record, (None, name, (), True), self._remote_settings(auth), auth)
                    self._history_item(directory, record, prepend=True)
                    self._buttons()
                return
            if self._automatic_folder() and self.jobs_per_script.value():
                selected = self._automatic_jobs()
                if self.command_valid and not self.folder_scan and selected:
                    self._transfer_inputs(auth, selected)
                return
            if self._slurm_mode():
                selected = tuple(self._folder_jobs())
                if self.command_valid and not self.folder_scan and selected:
                    if self.unsent_slurm_jobs:
                        if self._upload_ready(auth):
                            self._queue_batch(target, auth, selected=selected)
                    else:
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

    def _check_run_list(self):
        if self.run_list_task is None or not self.run_list_task[0].done():
            return
        future, auth, folder = self.run_list_task
        self.run_list_task = None
        self.run_list_message = ""
        try:
            entries = future.result()
            if self.truba.analysis_auth() != auth:
                raise ValueError(translate("TRUBA connection changed. Run the list again."))
            errors = []
            for path in entries:
                case_folder = (folder / path.parent).as_posix()
                try:
                    directory, record = jobs.new_job(
                        None, path.name, "remote", None, parent=self.history_root,
                        batch_folder=case_folder, truba_input_folder=case_folder,
                    )
                except OSError as error:
                    errors.append(f"{path}: {error}")
                    continue
                self.batch_queue[directory] = record, (None, path.name, (), True), (None, auth.username)
                self._history_item(directory, record, prepend=True)
            self.run_list_message = translate("{count} scripts prepared from {folder}.").format(
                count=len(entries) - len(errors), folder=folder.as_posix())
            if errors:
                self.run_list_message += "\n" + "\n".join(errors)
        except Exception as error:
            self.run_list_message = str(error)
        self._preview()

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
        automatic = self._automatic_folder() and self.jobs_per_script.value() > 0
        groups = (jobs.folder_job_groups(source, selected, self.jobs_per_script.value(), slurm=self.folder_slurm,
                                        restart=bool(settings and settings["resume_input"]))
                  if automatic else ((name,) for name in selected))
        for group in groups:
            name = group[0]
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
            prepared.append((name, files, options, group if len(group) > 1 else ()))
        self._save_profiles()
        errors = []
        for name, files, options, group in prepared:
            folder, entry = files[:2]
            label = root.name if auth else (Path(root.name) / folder.relative_to(root)).as_posix()
            try:
                if self.unsent_slurm_jobs:
                    directory = self.unsent_slurm_jobs[name]
                    record = jobs.read_job(directory)
                    if record["state"] != "Preparing" or record.get("job_id") or record.get("hidden_from_history"):
                        continue
                else:
                    directory, record = jobs.new_job(root if auth else folder, name if auth else entry,
                                                     target, ranks, parent=self.history_root, batch_folder=label,
                                                     folder_upload=upload, group_entries=group)
            except (OSError, ValueError) as error:
                errors.append(f"{label}: {error}")
                continue
            self.batch_queue[directory] = (record, files, options)
            if self.unsent_slurm_jobs:
                self._update_finished_job(directory, record)
            else:
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
        # Explicit Slurm scripts are independent; serialize staging and sbatch,
        # then let Slurm schedule the jobs concurrently.
        if not (remote and (record.get("group_entries") or jobs.is_slurm_script(record["name"]))):
            self.batch_active = directory
        if remote:
            self._submit_remote(directory, record, files, options[0], auth)
        else:
            self._run_local(directory, record, files, *options)
        item = self._update_finished_job(directory, record)
        if len(self._history_selection()) <= 1:
            self.history.setCurrentCell(item.row(), 0)

    def _select_job(self, *_):
        item = self._history_focus()
        proxy_index = self.queue_model.mapFromSource(self.history.indexFromItem(item)) if item else QModelIndex()
        selection = self.active_jobs.selectionModel()
        if not selection.currentIndex().isValid() or selection.currentIndex().row() != proxy_index.row():
            selection.setCurrentIndex(proxy_index, QItemSelectionModel.SelectionFlag.ClearAndSelect
                                      | QItemSelectionModel.SelectionFlag.Rows)
        directory = self._selected_directory()
        if self._history_preview == directory:
            self._buttons()
            return
        self._history_preview = directory
        self.job_error = ""
        self._result_key = None
        self.log.clear()
        self._show_selected()
        if len(self._history_selection()) == 1:
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
        item = self._history_focus()
        self._update_history_item(item, directory, record)
        if self.history.isRowHidden(item.row()):
            return
        if record.get("folder_upload") and not record.get("direct_upload") and jobs.is_slurm_script(record["name"]):
            upload = jobs.FolderUpload(record["folder_upload"], record["username"], record.get("upload_directory", ""))
            self.truba_browser.set_slurm_directory(upload.username, upload.path())
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
                       or self.run_list_task is not None
                       or auth.username in self._active_truba_jobs.values())
            state = "working" if working else "connected"
            if self.truba_badge.property("state") != state:
                self.truba_badge.setProperty("state", state)
                self.truba_badge.setText(translate("Working" if working else "Connected"))
                self.truba_badge.style().unpolish(self.truba_badge)
                self.truba_badge.style().polish(self.truba_badge)
        browser = self.truba_browser
        can_submit_remote = authenticated and not self.remote_task and not browser.deleting
        ready = can_submit_remote if remote else not self.local_task
        batch_busy = bool(self.batch_active or self.batch_queue)
        uploading = self.upload_task is not None or self.sync_task is not None or self.run_list_task is not None
        browser.set_delete_blocked(bool(uploading or self.remote_task or batch_busy))
        root = self._sync_input_browse_root()
        path = self._input_locations[self._input_location]
        self.input_path.setText(str(root / path) if root is not None else "")
        browsing = not uploading and not self.folder_scan
        self.input_back.setEnabled(browsing and self._input_location > 0)
        self.input_forward.setEnabled(browsing and self._input_location + 1 < len(self._input_locations))
        self.input_up.setEnabled(browsing and bool(path))
        if remote:
            if self._run_truba_selection:
                ready = ready and self.truba_browser.task is None
            elif self.unsent_slurm_jobs or (self.folder_root is not None and not self.folder_slurm
                                          and not (self._automatic_folder() and self.jobs_per_script.value())):
                ready = ready and self._upload_ready(auth)
        if self.reuse_batch:
            targets = {record["target"] for _, record in self.reuse_batch}
            ready = ("remote" not in targets or can_submit_remote) and (
                "local" not in targets or not self.local_task)
            self.connection.setVisible("remote" in targets and not authenticated)
        self.start.setEnabled(self.command_valid and ready and not batch_busy and not uploading)
        has_inputs = self.folder_root is not None or (
            self.input_root is not None and (self.entry or self.extra or self.whole_folder.isChecked())
        )
        self.upload.setEnabled(bool(remote and authenticated and has_inputs
                                    and not self.folder_scan and not uploading and not batch_busy and not self.reuse_batch
                                    and not browser.deleting and not browser.restoring_directory
                                    and not browser.is_slurm_snapshot_open()))
        sync_directory = browser.open_directory(auth)
        self.sync.setEnabled(bool(remote and authenticated and self.folder_root and not self.folder_replay
                                  and not self.folder_scan and not uploading and not batch_busy and not self.reuse_batch
                                  and browser.loaded and browser.task is None
                                  and sync_directory and "esen-folders" not in PurePosixPath(sync_directory).parts))
        for control in (self.target, self.batch_folder, self.load_files, self.pick_script, self.inputs,
                        self.whole_folder, self.input_filter, self.input_sort, self.jobs_per_script):
            control.setEnabled(not uploading)
        selected = self.inputs.selectedItems()
        supporting_file_selected = any(item.job_order is not None and item.path in self.extra for item in selected)
        self.use_as_script.setEnabled(len(selected) == 1 and supporting_file_selected and not uploading)
        self.remove_inputs.setEnabled(supporting_file_selected and not uploading)
        self.move_up.setEnabled(not uploading and self._script_neighbor(-1) is not None)
        self.move_down.setEnabled(not uploading and self._script_neighbor(1) is not None)
        selection = self._history_selection()
        multiple = len(selection) > 1
        visible_rows = self.history.rowCount() - self.history.verticalHeader().hiddenSectionCount()
        self.select_history.setEnabled(visible_rows > len(selection))
        self.delete_job.setEnabled(any(
            self._can_delete_job(self._history_directory(item), item.data(HISTORY_REMOTE_ACTIVE_ROLE))
            for item in selection
        ))
        self.open_folder.setEnabled(directory is not None)
        self.open_results_folder.setEnabled(self._downloaded_results(directory, record) is not None)
        self.attach.setEnabled(bool(authenticated and self.remote_folder.text().strip() and not self.remote_task))
        self.use_inputs.setEnabled(not uploading and (multiple or self._can_reuse_job(directory, record)))
        remote_ready = bool(record and record["target"] == "remote" and record.get("username")
                            and authenticated and not self.remote_task
                            and record["state"] != "Preparing"
                            and (record.get("job_id") or record["state"] not in jobs.TERMINAL))
        refresh_selected = authenticated and not self.remote_task and any(
            item.data(HISTORY_REFRESH_OWNER_ROLE) == auth.username for item in selection)
        self.refresh.setEnabled(not self.history_refresh_queue and (refresh_selected if multiple else remote_ready))
        self.resume.setEnabled(bool(not multiple and remote_ready and record.get("resume_input") and record.get("has_checkpoint")
                                    and record["state"] in {"Failed", "Cancelled", "Interrupted"}))
        self.stop.setEnabled(not multiple and bool(
            (self.local_task and directory == self.local_task[0] and not self.local_stop.is_set())
            or (remote_ready and not record.get("remote_run") and record["state"] not in jobs.TERMINAL)))
        result = self._result_path()
        self.open_file.setEnabled(result is not None and result.is_file())
        self.download.setEnabled(bool(remote_ready and self.results.currentItem()))

    def _tick(self):
        self.truba_browser.check_task()
        self._check_folder_scan()
        self._check_upload()
        self._check_sync()
        self._check_run_list()
        for field in ("local_task", "remote_task"):
            task = getattr(self, field)
            if task and not task[1].done() and self.isVisible() and task[0] != self._selected_directory():
                try:
                    record = jobs.read_job(task[0])
                except (OSError, ValueError):
                    pass
                else:
                    self._update_finished_job(task[0], record)
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
        self._check_md()
        self._run_next_batch_job()
        self._refresh_next_history_job()
        if self.isVisible():
            self._show_selected()

    def _update_finished_job(self, directory, record):
        for row in range(self.history.rowCount()):
            item = self.history.item(row, 0)
            if item.data(Qt.ItemDataRole.UserRole) == str(directory):
                self._update_history_item(item, directory, record)
                return item
        return self._history_item(directory, record, prepend=True)

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
        for index in range(self.history.rowCount()):
            item = self.history.item(index, 0)
            if item.data(Qt.ItemDataRole.UserRole) == str(directory):
                if self._history_focus() == item:
                    self._refresh_remote(manual=True)
                elif self.history.isRowHidden(index):
                    try:
                        record = jobs.read_job(directory)
                    except (OSError, ValueError) as error:
                        self.job_error = str(error)
                        self._show_selected()
                    else:
                        self._refresh_remote(manual=True, job=(directory, record))
                else:
                    self.history.setCurrentCell(index, 0)
                return
        self.job_error = ""
        self.remote_task = (directory, self.executor.submit(jobs.attach_remote_run, directory, path, auth))
        self._buttons()

    def _auto_refresh(self, *, manual=False):
        if self.isVisible() and not self.history_refresh_queue:
            self._refresh_remote(manual=manual)

    def _refresh_history(self):
        selection = self._history_selection()
        if len(selection) == 1:
            self._refresh_remote(manual=True)
        elif self.refresh.isEnabled():
            auth = self.truba.analysis_auth()
            if not auth:
                return
            self.history_refresh_queue.extend(self._history_directory(item) for item in selection
                                              if item.data(HISTORY_REFRESH_OWNER_ROLE) == auth.username)
            self._refresh_next_history_job()
            self._buttons()

    def _refresh_next_history_job(self):
        auth = self.truba.analysis_auth()
        if not auth:
            self.history_refresh_queue.clear()
            return
        while self.history_refresh_queue and not self.remote_task:
            directory = self.history_refresh_queue.popleft()
            try:
                record = jobs.read_job(directory)
            except (OSError, ValueError):
                continue
            if (not record.get("hidden_from_history") and record["target"] == "remote"
                    and record.get("username") == auth.username):
                self._refresh_remote(manual=True, job=(directory, record))

    def _poll_remote(self):
        auth = self.truba.analysis_auth()
        if self.remote_task or not auth or self.history_refresh_queue:
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
            if not was_terminal or (manual and (record.get("resume_input") or record.get("elapsed_seconds") is None
                                                or not record.get("slurm"))):
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
        if not self.stop.isEnabled():
            return
        if self.local_task and self._selected_directory() == self.local_task[0]:
            self.local_stop.set()
            self._buttons()
        else:
            self._refresh_remote(manual=True, cancel=True)

    def _can_reuse_job(self, directory, record):
        if not directory or not record or record.get("remote_run") or directory in self._active_directories():
            return False
        try:
            return bool(record.get("truba_input_folder") or
                        (jobs.job_inputs(directory, record) / jobs.relative_file(record["name"])).is_file())
        except (OSError, ValueError):
            return False

    def _clear_reuse(self):
        self.reuse_batch = []
        self._preview()

    def _reuse_inputs(self):
        if not self.use_inputs.isEnabled():
            return
        selection = self._history_selection()
        if len(selection) > 1 or (self.record and (self.record.get("truba_input_folder")
                                                   or self.record.get("group_entries"))):
            prepared, skipped = [], []
            auth = self.truba.analysis_auth()
            for item in selection:
                directory = self._history_directory(item)
                try:
                    record = jobs.read_job(directory)
                except (OSError, ValueError):
                    skipped.append(item.text().splitlines()[0])
                    continue
                needs_owner = record.get("folder_upload") or record.get("truba_input_folder")
                if (self._can_reuse_job(directory, record)
                        and (not needs_owner or not auth or auth.username == record.get("username"))):
                    prepared.append((directory, record))
                else:
                    skipped.append(record["name"])
            self.reuse_batch = prepared
            self._preview()
            self.job_error = (translate("Reuse unavailable for: {names}").format(names=", ".join(skipped))
                              if skipped else "")
            self._show_selected()
            return
        directory = self._selected_directory()
        if directory and self.record:
            self.reuse_batch = []
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
                if self.record.get("batch_folder") and self._unsent_slurm_job(self.record):
                    for row in range(self.history.rowCount()):
                        path = self._history_directory(self.history.item(row, 0))
                        try:
                            record = jobs.read_job(path)
                        except (OSError, ValueError):
                            continue
                        if (self._unsent_slurm_job(record) and all(
                            record.get(key) == self.record.get(key)
                            for key in ("folder_upload", "username", "source", "batch_folder")
                        )):
                            self.unsent_slurm_jobs[record["name"]] = path
                self._scan_folder(checked=set(self.unsent_slurm_jobs) or {self.record["name"]})
                return
            self.input_root = directory / "inputs"
            self.entry = self.record["name"]
            self.resume_input.setText(self.record.get("resume_input", ""))
            self.extra = []
            if self.record.get("ranks") is not None:
                self.ranks.setValue(self.record["ranks"])
            self.whole_folder.setChecked(True)
            self._update_inputs()

    def _reuse_job_arguments(self, original, record, auth):
        target, name = record["target"], record["name"]
        remote_folder = record.get("truba_input_folder")
        upload = None
        work_subdir = record.get("work_subdir", "")
        entry = jobs.relative_file(name).name if work_subdir else name
        root = jobs.job_inputs(original, record) if remote_folder is None else None
        ranks = None
        files = root, entry, (), True
        if target == "local":
            ranks = min(record.get("ranks") or self.ranks.value(), self.ranks.maximum())
            invocation = jobs.local_command(entry, ranks, self.local_exe.text().strip(), self.local_mpi.text().strip())
            options = invocation, self.local_memory.value()
        else:
            if (remote_folder or record.get("folder_upload")) and record.get("username") != auth.username:
                raise ValueError(translate("Connect using the TRUBA account that submitted this job."))
            settings = {**self._remote_settings(auth), "resume_input": record.get("resume_input", "")}
            if not jobs.is_slurm_script(entry):
                restart = settings["resume_input"]
                if restart and root is not None:
                    base = root / jobs.relative_file(work_subdir) if work_subdir else root
                    if not (base / jobs.relative_file(restart)).is_file():
                        raise ValueError(translate("Include the LAMMPS restart input in the job files."))
                self._remote_command(entry, settings, work_subdir=work_subdir)
            options = settings, auth.username
            if upload_id := record.get("folder_upload"):
                upload = jobs.FolderUpload(upload_id, auth.username, record.get("upload_directory", ""),
                                           record.get("direct_upload", False))
        source = Path(record["source"]) if upload and record.get("source") else root
        return (source, name, target, ranks), dict(
            batch_folder=record.get("batch_folder"), folder_upload=upload, work_subdir=work_subdir,
            truba_input_folder=remote_folder, group_entries=record.get("group_entries", ()),
        ), files, options

    def _queue_reuse_batch(self):
        auth = self.truba.analysis_auth()
        prepared = []
        for original, record in self.reuse_batch:
            try:
                if not self._can_reuse_job(original, record):
                    raise ValueError(translate("The selected input script is missing."))
                args = self._reuse_job_arguments(original, record, auth)
            except (OSError, ValueError) as error:
                self.input_note.setText(f"{record['name']}\n{error}")
                return
            prepared.append((original, record, args))

        self._save_profiles()
        self.reuse_batch = []
        errors = []
        for original, record, (args, kwargs, files, options) in prepared:
            try:
                directory, new_record = jobs.new_job(*args, parent=self.history_root, **kwargs)
            except OSError as error:
                self.reuse_batch.append((original, record))
                errors.append(f"{record['name']}: {error}")
                continue
            self.batch_queue[directory] = new_record, files, options
            self._history_item(directory, new_record, prepend=True)
        self._run_next_batch_job()
        self._preview()
        if errors:
            self.input_note.setText("\n".join(errors))

    @staticmethod
    def _unsent_slurm_job(record):
        return (record["target"] == "remote" and record["state"] == "Preparing"
                and not record.get("group_entries") and not record.get("job_id")
                and jobs.is_slurm_script(record["name"]))

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
