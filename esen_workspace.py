"""Native ESEN workspace for running and inspecting LAMMPS jobs."""

from concurrent.futures import ThreadPoolExecutor
from itertools import islice
import os
from os.path import commonpath
from pathlib import Path
import re
import subprocess
from threading import Event

from PySide6.QtCore import QSettings, QSignalBlocker, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from datenburg_widgets import qss_asset_url
import esen_jobs as jobs
from file_dialog import QFileDialog
from studio_style import (
    ActionFlowLayout, CompactComboBox, CompactSpinBox, CONTROL_HEIGHT,
    STUDIO_STYLESHEET, SidebarGroupBox, configure_form, field_label,
)
from ui_language import translate
from window_state import show_restored_window


WORKSPACE_TITLE = "ESEN STUDIES"
ACTIVE_TRUBA_STATES = frozenset({"Submitting", "Submitted", "Queued", "Running", "Cancellation requested"})

ESEN_STYLE = """
QDialog#esenWorkspace, QDialog#esenBatch { background: #F5F5F3; }
#esenTitle { background: #BFDBFE; border: 1px solid #2563EB; border-radius: 6px;
             color: #0F172A; font-size: 14px; padding-left: 6px; }
QFrame[esenCard="true"] { background: #FCFCFB; border: 1px solid #D5DBE2; border-radius: 7px; }
QFrame[esenCard="paper"] { background: #FFFFFF; border: 1px solid #D5DBE2; border-radius: 7px; }
#esenSection { background: #E5ECF4; border: 1px solid #D5DBE2; border-radius: 5px;
               color: #0F172A; font-size: 13px; padding-left: 5px; }
#esenWorkspace QPlainTextEdit, #esenWorkspace QListWidget {
    background: #FFFFFF; border: 1px solid #DCE3EC; border-radius: 3px;
}
#esenBatch QTableWidget { background: #FFFFFF; border: 1px solid #DCE3EC; border-radius: 3px; }
#esenBatch QHeaderView::section { background: #E5ECF4; }
#esenBatch QTableWidget::indicator { width: 13px; height: 13px; background: #FFFFFF;
                                    border: 1px solid #AAAAAA; border-radius: 2px; }
#esenBatch QTableWidget::indicator:checked { border-color: #2563EB;
                                           image: url(@ESEN_CHECK@); }
#esenBatch QTableWidget::indicator:hover { border-color: #2563EB; }
#esenWorkspace QPlainTextEdit { font-family: Consolas; font-size: 12px; }
#esenFieldLabel { color: #475569; font-size: 11px; }
#esenWorkspace #trubaConnectionBadge { margin: 0; }
#esenWorkspace #trubaConnectionBadge[state="working"] { color: #713F12; background: #FFF4CE; }
#esenWorkspace QPushButton { padding: 0 7px; }
#esenRun { color: #FFFFFF; background: #285EA6; }
#esenRun:hover { background: #245692; }
#esenRun:pressed { background: #1E477C; }
#esenRun:disabled { background: #EBEBEB; color: #808080; border-color: #D6D6D6; }
""".replace("@ESEN_CHECK@", qss_asset_url("checkbox-check-blue.svg"))


def panel(title, *, paper=False, scrollable=False):
    card = QFrame()
    card.setProperty("esenCard", "paper" if paper else "true")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(7, 7, 7, 7)
    layout.setSpacing(5)
    header = QLabel(title)
    header.setObjectName("esenSection")
    header.setFixedHeight(CONTROL_HEIGHT)
    layout.addWidget(header)
    if scrollable:
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(card)
        return scroll, layout
    return card, layout


def command(text, slot, *, role=""):
    button = QPushButton(text)
    button.setAutoDefault(False)
    button.setDefault(False)
    button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    button.setFixedHeight(CONTROL_HEIGHT)
    if role:
        button.setObjectName(role)
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


class InputFileList(CompactListWidget):
    files_dropped = Signal(list)

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


class EsenBatchDialog(QDialog):
    def __init__(self, workspace, root):
        super().__init__(workspace)
        self.workspace = workspace
        self.root = root
        self.folders = []
        self.setObjectName("esenBatch")
        self.setWindowTitle("TRUBA folder submission")
        self.setWindowFlag(Qt.WindowType.WindowMinMaxButtonsHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet(STUDIO_STYLESHEET + ESEN_STYLE)
        self.resize(680, 420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        card, layout = panel("Discovered LAMMPS jobs", paper=True)
        outer.addWidget(card, 1)
        location = QLabel(str(root))
        location.setTextFormat(Qt.TextFormat.PlainText)
        location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        location.setWordWrap(True)
        layout.addWidget(location)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Analysis folder", "Main script"])
        self.table.setAccessibleName("Discovered LAMMPS jobs")
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(CONTROL_HEIGHT + 5)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 106)
        layout.addWidget(self.table, 1)
        self.message = QLabel("Scanning for LAMMPS input scripts…")
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.submit = command("Send to TRUBA", self._submit, role="esenRun")
        self.submit.setEnabled(False)
        layout.addLayout(button_row(self.submit, command("Close", self.reject)))

        self.scan = workspace.executor.submit(jobs.discover_job_folders, root)
        self.scan_timer = QTimer(self)
        self.scan_timer.setInterval(100)
        self.scan_timer.timeout.connect(self._check_scan)
        self.scan_timer.start()
        self.finished.connect(lambda: self.scan.cancel())
        self.table.itemChanged.connect(self._selection_changed)

    def _check_scan(self):
        if not self.scan.done():
            return
        self.scan_timer.stop()
        try:
            self.folders = self.scan.result()
        except (OSError, ValueError) as error:
            self.message.setText(str(error))
            return
        if not self.folders:
            self.message.setText(translate("No LAMMPS input scripts named in.* or *.in were found."))
            return
        script_width = 100
        with QSignalBlocker(self.table):
            self.table.setRowCount(len(self.folders))
            for row, (folder, scripts) in enumerate(self.folders):
                name = folder.relative_to(self.root).as_posix()
                item = QTableWidgetItem(self.root.name if name == "." else name)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setToolTip(str(folder))
                item.setCheckState(Qt.CheckState.Checked)
                self.table.setItem(row, 0, item)
                options = [(script, script) for script in scripts]
                if len(scripts) > 1:
                    options.insert(0, ("Choose main script…", None))
                selector = compact_combo(options)
                selector.setAccessibleName(f"{translate('Main script')} · {item.text()}")
                selector.setToolTip(selector.currentText())
                selector.currentTextChanged.connect(selector.setToolTip)
                selector.currentIndexChanged.connect(self._selection_changed)
                self.table.setCellWidget(row, 1, selector)
                script_width = max(script_width, selector.width())
        self.table.setColumnWidth(1, script_width + 6)
        self._selection_changed()

    def _selected_jobs(self):
        return [(folder, self.table.cellWidget(row, 1).currentData())
                for row, (folder, _) in enumerate(self.folders)
                if self.table.item(row, 0).checkState() == Qt.CheckState.Checked]

    def _selection_changed(self, *_):
        selected = self._selected_jobs()
        self.submit.setEnabled(bool(selected and all(script for _, script in selected)
                                    and self.workspace.truba.analysis_auth() and not self.workspace.batch_queue))
        if not self.folders:
            return
        if not self.workspace.truba.analysis_auth():
            message = "Connect using the main TRUBA button."
        elif any(not script for _, script in selected):
            message = "Choose a main script for each checked folder."
        else:
            message = "Checked folders use the current TRUBA settings and run as separate jobs."
        self.message.setText(translate(message))

    def _submit(self):
        try:
            self.workspace._queue_batch(self.root, self._selected_jobs())
        except (OSError, ValueError) as error:
            self.message.setText(translate(str(error)))
        else:
            self.accept()


class EsenWorkspace(QDialog):
    def __init__(self, truba, parent=None, *, history=None):
        super().__init__(parent)
        self.setObjectName("esenWorkspace")
        self.setWindowTitle(WORKSPACE_TITLE)
        self.setWindowFlag(Qt.WindowType.WindowMinMaxButtonsHint)
        self.setStyleSheet(STUDIO_STYLESHEET + ESEN_STYLE)
        self.resize(1020, 750)
        self.truba = truba
        self.history_root = history if history is not None else jobs.jobs_directory()
        self.preferences = QSettings("RustMech", "RustMech")
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="esen")
        self.local_task = self.remote_task = None
        self.batch_dialog = None
        self.batch_queue = {}
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
        title = QLabel(f"{WORKSPACE_TITLE} - (Thanks to Prof. Dr. Ismail Esen)")
        title.setObjectName("esenTitle")
        title.setFixedHeight(30)
        outer.addWidget(title)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setObjectName("esenColumns")
        split.setChildrenCollapsible(False)
        split.setHandleWidth(7)
        outer.addWidget(split, 1)

        left = QSplitter(Qt.Orientation.Vertical)
        left.setObjectName("esenInputsAndJobs")
        left.setChildrenCollapsible(False)
        left.setHandleWidth(7)
        right = QSplitter(Qt.Orientation.Vertical)
        right.setObjectName("esenPreviewAndResults")
        right.setChildrenCollapsible(False)
        right.setHandleWidth(7)
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([405, 590])

        settings, layout = panel("Job settings", scrollable=True)
        left.addWidget(settings)
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
        self.walltime = QLineEdit("00:30:00")
        self.module = QLineEdit(jobs.DEFAULT_MODULE)
        self.remote_exe = QLineEdit("lmp")
        self.remote_mpi = compact_combo((("mpirun", "mpirun"), ("srun", "srun")))
        self.resume_input = QLineEdit()
        self.resume_input.setPlaceholderText("Optional relative path")
        self.resume_input.setToolTip(translate("Include a restart input in the job files to enable checkpoint recovery and the 70-hour handover."))
        self._form_row(form, "Queue", self.partition)
        self._form_row(form, "Wall time (HH:MM:SS)", self.walltime)
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
        self.target.currentIndexChanged.connect(self._preview)
        self.ranks.valueChanged.connect(self._preview)
        self.partition.currentIndexChanged.connect(self._queue_changed)
        self.remote_mpi.currentIndexChanged.connect(self._preview)
        for field in (self.local_exe, self.local_mpi, self.account, self.walltime, self.module, self.remote_exe):
            field.editingFinished.connect(self._preview)
            field.returnPressed.connect(self._preview)
        self.resume_input.textChanged.connect(self._preview)

        self.load_files = command("Load files…", self._pick_files)
        layout.addLayout(button_row(self.load_files, command("Input script…", self._pick_script),
                                    command("Job folder…", self._pick_root)))
        self.batch_folder = command("TRUBA analysis folder…", self._pick_batch_folder)
        layout.addLayout(button_row(self.batch_folder))
        self.inputs = InputFileList()
        self.inputs.setAcceptDrops(True)
        self.inputs.viewport().setAcceptDrops(True)
        self.inputs.files_dropped.connect(self._load_files)
        self.inputs.setAccessibleName("LAMMPS input files")
        self.whole_folder = QCheckBox("Include entire job folder")
        self.whole_folder.toggled.connect(self._preview)
        layout.addWidget(self.whole_folder)
        layout.addWidget(self.inputs, 1)
        self.use_as_script = command("Use as script", self._use_as_script)
        self.remove_inputs = command("Remove file", self._remove_file)
        self.inputs.currentRowChanged.connect(self._buttons)
        self.start = command("Run LAMMPS", self._start, role="esenRun")
        layout.addLayout(button_row(self.use_as_script, self.remove_inputs, self.start))

        history, layout = panel("Job history", paper=True, scrollable=True)
        left.addWidget(history)
        self.history = CompactListWidget()
        self.history.setAccessibleName("ESEN job history")
        self.history.currentItemChanged.connect(self._select_job)
        layout.addWidget(self.history, 1)
        self.use_inputs = command("Reuse inputs", self._reuse_inputs)
        self.refresh = command("Refresh job", lambda: self._refresh_remote(manual=True))
        self.resume = command("Resume job", self._resume_job)
        self.stop = command("Stop job", self._cancel, role="destructiveActionBtn")
        layout.addLayout(button_row(self.use_inputs, self.refresh, self.resume, self.stop))
        self.remote_folder = QLineEdit()
        self.remote_folder.setFixedHeight(CONTROL_HEIGHT)
        self.remote_folder.setPlaceholderText("TRUBA run folder")
        self.remote_folder.setAccessibleName("TRUBA run folder")
        self.remote_folder.textChanged.connect(self._buttons)
        self.attach = command("Attach folder", self._attach_remote)
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
        self.open_file = command("Open file", self._open_result)
        self.open_folder = command("Open job folder", self._open_directory)
        self.open_results_folder = command("Open result folder", self._open_results_directory)
        self.download = command("Download file", self._download)
        layout.addLayout(button_row(self.open_file, self.download, self.open_folder, self.open_results_folder))
        right.setSizes([240, 450])

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
        for key in ("local_exe", "local_mpi", "account", "walltime", "module", "remote_exe"):
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
        for key in ("local_exe", "local_mpi", "account", "walltime", "module", "remote_exe"):
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
        name, _ = QFileDialog.getOpenFileName(self, translate("LAMMPS input script"), str(self.input_root or Path.home()))
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
        name = QFileDialog.getExistingDirectory(self, translate("Job folder"), str(self.input_root or Path.home()))
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
        files, _ = QFileDialog.getOpenFileNames(self, translate("Load input files"), str(self.input_root or Path.home()))
        self._load_files(files)

    def _pick_batch_folder(self):
        initial = self.preferences.value("esen/batchFolder", str(self.input_root or Path.home()))
        name = QFileDialog.getExistingDirectory(self, translate("TRUBA analysis folder"), str(initial))
        if not name:
            return
        try:
            root = Path(name).resolve(strict=True)
        except OSError as error:
            self.input_note.setText(str(error))
            return
        self.preferences.setValue("esen/batchFolder", str(root))
        self.batch_dialog = EsenBatchDialog(self, root)
        self.batch_dialog.finished.connect(self._batch_dialog_closed)
        show_restored_window(self.batch_dialog)
        self._buttons()

    def _batch_dialog_closed(self):
        self.batch_dialog = None
        self._buttons()

    def _load_files(self, files):
        if not files:
            return
        paths = [Path(name) for name in files]
        if not all(path.is_file() for path in paths):
            self.input_note.setText(translate("Load existing files. To include a folder, use Job folder."))
            return
        try:
            root = self.input_root or Path(commonpath([path.parent for path in paths]))
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
        item = self.inputs.currentItem()
        if item and item.text() in self.extra:
            name = item.text()
            self.extra.remove(name)
            if self.entry:
                self.extra.insert(0, self.entry)
            self.entry = name
            self._update_inputs()

    def _remove_file(self):
        item = self.inputs.currentItem()
        if item and item.text() in self.extra:
            self.extra.remove(item.text())
            self._update_inputs()

    def _update_inputs(self):
        self.inputs.clear()
        if self.entry:
            item = QListWidgetItem(self.entry)
            item.setToolTip(translate("LAMMPS input script"))
            item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, translate("LAMMPS input script"))
            font = item.font()
            font.setBold(True)
            item.setFont(font)
            self.inputs.addItem(item)
        self.inputs.addItems(self.extra)
        if not self.entry and self.extra:
            self.inputs.setCurrentRow(0)
        self._preview()

    def _remote_settings(self, auth):
        return dict(
            partition=self.partition.currentData(),
            account=self.account.text().strip() or (auth.username if auth else "TRUBA_USER"),
            walltime=self.walltime.text().strip(),
            module=self.module.text().strip(),
            executable=self.remote_exe.text().strip(),
            launcher=self.remote_mpi.currentData(),
            resume_input=self.resume_input.text().strip(),
        )

    def _command(self):
        if not self.entry:
            raise ValueError("Choose a LAMMPS input script.")
        if self.input_root is None or not (self.input_root / self.entry).is_file():
            raise ValueError("The selected input script is missing.")
        if self.target.currentData() == "local":
            return jobs.local_command(self.entry, self.ranks.value(), self.local_exe.text().strip(), self.local_mpi.text().strip())
        settings = self._remote_settings(self.truba.analysis_auth())
        if settings["resume_input"]:
            relative = jobs.relative_file(settings["resume_input"])
            if (not (self.input_root / relative).is_file() or
                    not (self.whole_folder.isChecked() or relative.as_posix() in [self.entry, *self.extra])):
                raise ValueError("Include the LAMMPS restart input in the job files.")
        limit = jobs.BASELINES[settings["partition"]]
        return jobs.remote_script(self.entry, limit.max_cpus, memory=limit.memory_ceiling(limit.max_cpus), **settings)

    def _preview(self, *_):
        remote = self.target.currentData() == "remote"
        for field in self.local_fields:
            self.job_form.setRowVisible(field, not remote)
        self.job_form.setRowVisible(self.partition, remote)
        self.job_form.setRowVisible(self.walltime, remote)
        self.truba_settings.setVisible(remote)
        try:
            invocation = self._command()
            self.preview.setPlainText(invocation if remote else subprocess.list2cmdline(invocation))
            self.command_valid = True
        except (OSError, ValueError) as error:
            self.preview.setPlainText(translate(str(error)))
            self.command_valid = False
        lines = [str(self.input_root)] if self.input_root else []
        if remote:
            lines.append(translate("Preview values are estimates. Slurm limits apply at submission."))
        if not self.entry:
            lines.append(translate("Select the input file and click Use as script." if self.extra else
                                   "Load files or drop them on the input list."))
        if remote and self.entry:
            try:
                with (self.input_root / self.entry).open("rb") as stream:
                    script = stream.read(128 * 1024).decode("utf-8", errors="replace")
                if re.search(r"(?:\b[A-Za-z]:[\\/]|(?<!\w)\.\.?\\)", script):
                    lines.append(translate("Windows paths found in the script. Use relative paths with / on TRUBA."))
            except OSError:
                pass
        self.input_note.setText("\n".join(lines))
        self._buttons()

    def _connection_changed(self, *_):
        self._preview()
        if self.batch_dialog is not None:
            self.batch_dialog._selection_changed()
        self._auto_refresh(manual=bool(self.record and self.record.get("remote_run")))

    def _load_history(self):
        records = []
        for path in self.history_root.glob("*/job.json"):
            if not re.fullmatch(r"[0-9a-f]{32}", path.parent.name):
                continue
            try:
                records.append((jobs.read_job(path.parent), path.parent))
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
        state = translate(self._state(directory, record))
        name = record["name"]
        if record.get("batch_folder"):
            name = f"{record['batch_folder']}/{name}"
        item.setText(f"{name} · {target} · {state}")
        if record.get("source"):
            item.setToolTip(record["source"])

    def _state(self, directory, record):
        if record["state"] == "Preparing" and directory in self.batch_queue:
            return "Waiting to submit"
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

    def _start(self):
        target = self.target.currentData()
        auth = self.truba.analysis_auth() if target == "remote" else None
        if (target == "remote" and (self.remote_task or not auth)) or (target == "local" and self.local_task):
            return
        try:
            invocation = self._command()
            ranks = self.ranks.value() if target == "local" else None
            directory, record = jobs.new_job(self.input_root, self.entry, target, ranks, parent=self.history_root)
        except (OSError, ValueError) as error:
            self.input_note.setText(translate(str(error)))
            return
        files = (self.input_root, self.entry, tuple(self.extra), self.whole_folder.isChecked())
        self._save_profiles()
        if target == "remote":
            self._submit_remote(directory, record, files, self._remote_settings(auth), auth)
        else:
            self.local_stop = Event()
            future = self.executor.submit(jobs.run_local, directory, record, files, invocation, self.local_stop,
                                          memory_mib=self.local_memory.value())
            self.local_task = (directory, future)
        self._history_item(directory, record, prepend=True)
        self._buttons()

    def _submit_remote(self, directory, record, files, settings, auth):
        future = self.executor.submit(jobs.submit_full_node, directory, record, files, settings, auth)
        self.remote_task = (directory, future)

    def _queue_batch(self, root, selected):
        auth = self.truba.analysis_auth()
        if not auth:
            raise ValueError("Connect using the main TRUBA button.")
        if not selected or any(not entry for _, entry in selected):
            raise ValueError("Choose a main script for each checked folder.")
        if self.batch_queue:
            raise ValueError("An analysis folder is already being submitted.")
        settings = self._remote_settings(auth)
        limit = jobs.BASELINES[settings["partition"]]
        restart = settings["resume_input"]
        history = self.history_root.resolve()
        for folder, entry in selected:
            if history.is_relative_to(folder):
                raise ValueError("Choose a job folder outside the ESEN history directory.")
            if not (folder / jobs.relative_file(entry)).is_file():
                raise ValueError(f"{translate('The selected input script is missing.')} {folder / entry}")
            if restart and not (folder / jobs.relative_file(restart)).is_file():
                raise ValueError(f"{translate('Include the LAMMPS restart input in the job files.')} {folder / restart}")
        jobs.remote_script(selected[0][1], limit.max_cpus, memory=limit.memory_ceiling(limit.max_cpus), **settings)
        self._save_profiles()
        errors = []
        for folder, entry in selected:
            label = (Path(root.name) / folder.relative_to(root)).as_posix()
            try:
                directory, record = jobs.new_job(folder, entry, "remote", None, parent=self.history_root,
                                                 batch_folder=label)
            except OSError as error:
                errors.append(f"{label}: {error}")
                continue
            self.batch_queue[directory] = (record, (folder, entry, (), True), settings, auth.username)
            self._history_item(directory, record, prepend=True)
        if errors:
            if not self.batch_queue:
                raise OSError("\n".join(errors))
            self.input_note.setText("\n".join(errors))
        self._submit_next_batch_job()
        self._buttons()

    def _submit_next_batch_job(self):
        while self.batch_queue and not self.remote_task:
            directory = next(iter(self.batch_queue))
            record, files, settings, username = self.batch_queue.pop(directory)
            auth = self.truba.analysis_auth()
            if auth and auth.username == username:
                self._submit_remote(directory, record, files, settings, auth)
            else:
                try:
                    jobs.save_job(directory, record, state="Interrupted",
                                  detail="TRUBA disconnected before this job was submitted.")
                except OSError as error:
                    self.input_note.setText(str(error))
            self._update_finished_job(directory, record)

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
            self.status.setText(translate(self.job_error or "Select an ESEN job."))
            self._buttons()
            return
        try:
            self.record = record = jobs.read_job(directory)
        except (OSError, ValueError) as error:
            self.record = None
            self.status.setText(str(error))
            self._buttons()
            return
        self._update_history_item(self.history.currentItem(), directory, record)
        state = translate(self._state(directory, record))
        details = [record.get("job_id", ""), state, record.get("created", ""),
                   self.job_error or translate(record.get("detail", ""))]
        exported = self._downloaded_results(directory, record)
        if exported is not None:
            details.append(f"{translate('Results')} · {exported}")
        if record.get("result_export_error"):
            details.append(f"{translate('Automatic result download failed. Use Refresh job to retry.')} "
                           f"{record['result_export_error']}")
        if record.get("files_omitted"):
            details.append(translate("Some files are omitted from this list."))
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
            working = self.remote_task is not None or auth.username in self._active_truba_jobs.values()
            state = "working" if working else "connected"
            if self.truba_badge.property("state") != state:
                self.truba_badge.setProperty("state", state)
                self.truba_badge.setText(translate("Working" if working else "Connected"))
                self.truba_badge.style().unpolish(self.truba_badge)
                self.truba_badge.style().polish(self.truba_badge)
        self.start.setEnabled(self.command_valid and ((authenticated and not self.remote_task) if remote else not self.local_task))
        self.batch_folder.setVisible(remote)
        self.batch_folder.setEnabled(not self.batch_queue and self.batch_dialog is None)
        item = self.inputs.currentItem()
        supporting_file_selected = bool(item and item.text() in self.extra)
        self.use_as_script.setEnabled(supporting_file_selected)
        self.remove_inputs.setEnabled(supporting_file_selected)
        self.open_folder.setEnabled(directory is not None)
        self.open_results_folder.setEnabled(self._downloaded_results(directory, record) is not None)
        self.attach.setEnabled(bool(authenticated and self.remote_folder.text().strip() and not self.remote_task))
        self.use_inputs.setEnabled(bool(record and not record.get("remote_run") and directory and directory not in self._active_directories()
                                        and (directory / "inputs" / jobs.relative_file(record["name"])).is_file()))
        remote_ready = bool(record and record["target"] == "remote" and record.get("username")
                            and authenticated and not self.remote_task
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
        for field in ("local_task", "remote_task"):
            task = getattr(self, field)
            if task and task[1].done():
                directory, future = task
                setattr(self, field, None)
                try:
                    future.result()
                    record = jobs.read_job(directory)
                    self._update_finished_job(directory, record)
                except Exception as error:
                    if directory == self._selected_directory() or (
                        field == "remote_task" and directory not in self._active_truba_jobs
                    ):
                        self.job_error = str(error)
        self._submit_next_batch_job()
        if self.isVisible():
            self._show_selected()

    def _update_finished_job(self, directory, record):
        for row in range(self.history.count()):
            item = self.history.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == str(directory):
                self._update_history_item(item, directory, record)
                break
        else:
            self._history_item(directory, record, prepend=True)

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
        if not directory or not record or not auth or self.remote_task or not record.get("username"):
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
            if not was_terminal or (manual and record.get("resume_input")):
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
        if self.batch_dialog is not None:
            self.batch_dialog.reject()
        self._save_profiles()
        super().closeEvent(event)

    def dispose(self):
        if not self._disposed:
            self._disposed = True
            self._save_profiles()
            self.timer.stop()
            self.poll_timer.stop()
            if self.batch_dialog is not None:
                self.batch_dialog.reject()
            self.local_stop.set()
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.close()
