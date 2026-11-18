"""Native ESEN workspace for running and inspecting LAMMPS jobs."""

from concurrent.futures import ThreadPoolExecutor
from itertools import islice
from pathlib import Path
import re
import subprocess
from threading import Event

from PySide6.QtCore import QSettings, QSignalBlocker, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QSizePolicy, QSplitter, QVBoxLayout,
)

import esen_jobs as jobs
from studio_style import (
    ActionFlowLayout, CompactComboBox, CompactSpinBox, CONTROL_HEIGHT,
    STUDIO_STYLESHEET, configure_form, field_label,
)
from ui_language import translate


ESEN_STYLE = """
QDialog#esenWorkspace { background: #F5F5F3; }
#esenTitle { background: #BFDBFE; border: 1px solid #2563EB; border-radius: 6px;
             color: #0F172A; font-size: 14px; padding-left: 6px; }
QFrame[esenCard="true"] { background: #FCFCFB; border: 1px solid #D5DBE2; border-radius: 7px; }
QFrame[esenCard="paper"] { background: #FFFFFF; border: 1px solid #D5DBE2; border-radius: 7px; }
#esenSection { background: #E5ECF4; border: 1px solid #D5DBE2; border-radius: 5px;
               color: #0F172A; font-size: 13px; padding-left: 5px; }
#esenWorkspace QPlainTextEdit, #esenWorkspace QListWidget {
    background: #FFFFFF; border: 1px solid #DCE3EC; border-radius: 3px;
}
#esenWorkspace QPlainTextEdit { font-family: Consolas; font-size: 12px; }
#esenFieldLabel { color: #475569; font-size: 11px; }
#esenWorkspace QPushButton { padding: 0 7px; }
#esenRun { color: #FFFFFF; background: #285EA6; }
#esenRun:hover { background: #245692; }
#esenRun:pressed { background: #1E477C; }
#esenRun:disabled { background: #EBEBEB; color: #808080; border-color: #D6D6D6; }
"""


def panel(title, *, paper=False):
    card = QFrame()
    card.setProperty("esenCard", "paper" if paper else "true")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(7, 7, 7, 7)
    layout.setSpacing(5)
    header = QLabel(title)
    header.setObjectName("esenSection")
    header.setFixedHeight(CONTROL_HEIGHT)
    layout.addWidget(header)
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


class EsenWorkspace(QDialog):
    def __init__(self, truba, parent=None, *, history=None):
        super().__init__(parent)
        self.setObjectName("esenWorkspace")
        self.setWindowTitle("ESEN · LAMMPS")
        self.setWindowFlag(Qt.WindowType.WindowMinMaxButtonsHint)
        self.setStyleSheet(STUDIO_STYLESHEET + ESEN_STYLE)
        self.resize(1020, 750)
        self.truba = truba
        self.history_root = history if history is not None else jobs.jobs_directory()
        self.preferences = QSettings("RustMech", "RustMech")
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="esen")
        self.local_task = self.remote_task = None
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
        self.poll_timer.timeout.connect(self._auto_refresh)
        self.poll_timer.start()
        self.truba.connection_changed.connect(self._connection_changed)

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(7)
        title = QLabel("ESEN · LAMMPS")
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

        settings, layout = panel("Job settings")
        left.addWidget(settings)
        form = QFormLayout()
        configure_form(form)
        layout.addLayout(form)
        self.target = compact_combo((("This computer", "local"), ("TRUBA", "remote")))
        self.ranks = CompactSpinBox()
        self.ranks.setRange(1, 224)
        self.local_exe = QLineEdit("lmp")
        self.local_mpi = QLineEdit()
        self.local_mpi.setPlaceholderText("Automatic")
        self._form_row(form, "Run on", self.target)
        self._form_row(form, "MPI processes", self.ranks)
        self._form_row(form, "LAMMPS executable", self.local_exe)
        self._form_row(form, "MPI launcher", self.local_mpi)
        self.local_fields = (self.local_exe, self.local_mpi)
        self.partition = compact_combo([(name, name) for name in jobs.CPU_QUEUES])
        self.account = QLineEdit()
        self.account.setPlaceholderText("TRUBA username")
        self.memory = CompactSpinBox()
        self.memory.setRange(1, 4194304)
        self.memory.setValue(4096)
        self.walltime = QLineEdit("00:30:00")
        self.module = QLineEdit(jobs.DEFAULT_MODULE)
        self.remote_exe = QLineEdit("lmp")
        self.remote_mpi = compact_combo((("mpirun", "mpirun"), ("srun", "srun")))
        self.remote_fields = (self.partition, self.account, self.memory, self.walltime,
                              self.module, self.remote_exe, self.remote_mpi)
        for text, control in zip(("Queue", "Account", "Memory (MiB)", "Wall time (HH:MM:SS)",
                                  "LAMMPS module", "Linux executable", "MPI launcher"), self.remote_fields):
            self._form_row(form, text, control)
        self.connection = QLabel()
        layout.addWidget(self.connection)
        self.target.currentIndexChanged.connect(self._preview)
        self.ranks.valueChanged.connect(self._preview)
        self.memory.valueChanged.connect(self._preview)
        self.partition.currentIndexChanged.connect(self._queue_changed)
        self.remote_mpi.currentIndexChanged.connect(self._preview)
        for field in (*self.local_fields, self.account, self.walltime, self.module, self.remote_exe):
            field.editingFinished.connect(self._preview)
            field.returnPressed.connect(self._preview)

        layout.addLayout(button_row(command("Input script…", self._pick_script), command("Job folder…", self._pick_root),
                                    command("Add files…", self._add_files)))
        self.inputs = QListWidget()
        self.inputs.setAccessibleName("LAMMPS input files")
        self.inputs.setMinimumHeight(63)
        self.whole_folder = QCheckBox("Include entire job folder")
        self.whole_folder.toggled.connect(self._preview)
        layout.addWidget(self.whole_folder)
        layout.addWidget(self.inputs, 1)
        self.remove_inputs = command("Remove file", self._remove_file)
        self.inputs.currentRowChanged.connect(self._buttons)
        self.start = command("Run LAMMPS", self._start, role="esenRun")
        layout.addLayout(button_row(self.remove_inputs, self.start))

        history, layout = panel("Job history", paper=True)
        left.addWidget(history)
        self.history = QListWidget()
        self.history.setAccessibleName("ESEN job history")
        self.history.currentItemChanged.connect(self._select_job)
        layout.addWidget(self.history, 1)
        self.use_inputs = command("Reuse inputs", self._reuse_inputs)
        self.refresh = command("Refresh job", lambda: self._refresh_remote(manual=True))
        self.stop = command("Stop job", self._cancel, role="destructiveActionBtn")
        layout.addLayout(button_row(self.use_inputs, self.refresh, self.stop))
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
        self.download = command("Download file", self._download)
        layout.addLayout(button_row(self.open_file, self.download, self.open_folder))
        right.setSizes([240, 450])

    @staticmethod
    def _form_row(form, name, field):
        label = field_label(name, field)
        label.setObjectName("esenFieldLabel")
        if isinstance(field, CompactComboBox):
            field.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        if isinstance(field, QLineEdit):
            label.setToolTip(name)
            field.textChanged.connect(field.setToolTip)
            field.setToolTip(field.text())
        form.addRow(label, field)
        field.esen_label = label

    def _load_profiles(self):
        for key in ("local_exe", "local_mpi", "account", "walltime", "module", "remote_exe"):
            field = getattr(self, key)
            field.setText(str(self.preferences.value(f"esen/{key}", field.text())))
        for key in ("ranks", "memory"):
            field = getattr(self, key)
            try:
                field.setValue(int(self.preferences.value(f"esen/{key}", field.value())))
            except (ValueError, TypeError):
                pass
        for key in ("partition", "remote_mpi", "target"):
            field = getattr(self, key)
            index = field.findData(self.preferences.value(f"esen/{key}", field.currentData()))
            if index >= 0:
                with QSignalBlocker(field):
                    field.setCurrentIndex(index)
        self._profile_partition = self.partition.currentData()

    def _save_profiles(self):
        for key in ("local_exe", "local_mpi", "account", "walltime", "module", "remote_exe"):
            self.preferences.setValue(f"esen/{key}", getattr(self, key).text())
        for key in ("ranks", "memory"):
            self.preferences.setValue(f"esen/{key}", getattr(self, key).value())
        for key in ("partition", "remote_mpi", "target"):
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
            self._update_inputs()

    def _add_files(self):
        if self.input_root is None:
            return
        files, _ = QFileDialog.getOpenFileNames(self, translate("Add input files"), str(self.input_root))
        outside = False
        for name in files:
            path = Path(name)
            if not path.is_relative_to(self.input_root):
                outside = True
                continue
            relative = path.relative_to(self.input_root).as_posix()
            if relative != self.entry and relative not in self.extra:
                self.extra.append(relative)
        self._update_inputs()
        if outside:
            self.input_note.setText(translate("Choose files inside the job folder."))

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
            self.inputs.addItem(item)
        self.inputs.addItems(self.extra)
        self._preview()

    def _command(self):
        if not self.entry:
            raise ValueError("Choose a LAMMPS input script.")
        if self.input_root is None or not (self.input_root / self.entry).is_file():
            raise ValueError("The selected input script is missing.")
        if self.target.currentData() == "local":
            return jobs.local_command(self.entry, self.ranks.value(), self.local_exe.text().strip(), self.local_mpi.text().strip())
        auth = self.truba.analysis_auth()
        return jobs.remote_script(
            self.entry, self.ranks.value(), self.partition.currentData(),
            self.account.text().strip() or (auth.username if auth else "TRUBA_USER"),
            self.memory.value(), self.walltime.text().strip(), self.module.text().strip(),
            self.remote_exe.text().strip(), self.remote_mpi.currentData(),
        )

    def _preview(self, *_):
        remote = self.target.currentData() == "remote"
        for field in (*self.local_fields, *self.remote_fields):
            visible = (field in self.remote_fields) == remote
            field.setVisible(visible)
            field.esen_label.setVisible(visible)
        self.connection.setVisible(remote)
        self.connection.setWordWrap(True)
        self.connection.setText("Connected to TRUBA" if self.truba.analysis_auth() else "Connect using the main TRUBA button.")
        try:
            invocation = self._command()
            self.preview.setPlainText(invocation if remote else subprocess.list2cmdline(invocation))
            self.command_valid = True
        except (OSError, ValueError) as error:
            self.preview.setPlainText(translate(str(error)))
            self.command_valid = False
        lines = [str(self.input_root)] if self.input_root else []
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
        self._auto_refresh()

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
        item = QListWidgetItem(self._history_label(directory, record))
        item.setData(Qt.ItemDataRole.UserRole, str(directory))
        if prepend:
            self.history.insertItem(0, item)
            self.history.setCurrentItem(item)
        else:
            self.history.addItem(item)

    def _history_label(self, directory, record):
        target = translate("This computer") if record["target"] == "local" else "TRUBA"
        state = translate(self._state(directory, record))
        return f"{record['name']} · {target} · {state}"

    def _state(self, directory, record):
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
            directory, record = jobs.new_job(self.input_root, self.entry, target, self.ranks.value(), parent=self.history_root)
        except (OSError, ValueError) as error:
            self.input_note.setText(translate(str(error)))
            return
        files = (self.input_root, self.entry, tuple(self.extra), self.whole_folder.isChecked())
        self._save_profiles()
        if target == "remote":
            self.remote_task = (directory, self.executor.submit(jobs.submit_remote, directory, record, files, invocation, auth))
        else:
            self.local_stop = Event()
            self.local_task = (directory, self.executor.submit(jobs.run_local, directory, record, files, invocation, self.local_stop))
        self._history_item(directory, record, prepend=True)
        self._buttons()

    def _select_job(self, *_):
        self.job_error = ""
        self._result_key = None
        self.log.clear()
        self._show_selected()
        self._auto_refresh()

    def _show_selected(self):
        directory = self._selected_directory()
        if directory is None:
            self.record = None
            self.status.setText(translate("Select an ESEN job."))
            self._buttons()
            return
        try:
            self.record = record = jobs.read_job(directory)
        except (OSError, ValueError) as error:
            self.record = None
            self.status.setText(str(error))
            self._buttons()
            return
        self.history.currentItem().setText(self._history_label(directory, record))
        state = translate(self._state(directory, record))
        details = [record.get("job_id", ""), state, record.get("created", ""),
                   self.job_error or translate(record.get("detail", ""))]
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
        authenticated = self.truba.analysis_auth() is not None
        remote = self.target.currentData() == "remote"
        self.start.setEnabled(self.command_valid and ((authenticated and not self.remote_task) if remote else not self.local_task))
        item = self.inputs.currentItem()
        self.remove_inputs.setEnabled(bool(item and item.text() in self.extra))
        self.open_folder.setEnabled(directory is not None)
        self.use_inputs.setEnabled(bool(record and directory and directory not in self._active_directories()
                                        and (directory / "inputs" / jobs.relative_file(record["name"])).is_file()))
        remote_ready = bool(record and record["target"] == "remote" and record.get("username")
                            and authenticated and not self.remote_task
                            and (record.get("job_id") or record["state"] not in jobs.TERMINAL))
        self.refresh.setEnabled(remote_ready)
        self.stop.setEnabled(bool((self.local_task and directory == self.local_task[0] and not self.local_stop.is_set())
                                  or (remote_ready and record["state"] not in jobs.TERMINAL)))
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
                    for row in range(self.history.count()):
                        item = self.history.item(row)
                        if item.data(Qt.ItemDataRole.UserRole) == str(directory):
                            item.setText(self._history_label(directory, record))
                            break
                except Exception as error:
                    if directory == self._selected_directory():
                        self.job_error = str(error)
        if self.isVisible():
            self._show_selected()

    def _auto_refresh(self):
        if self.isVisible():
            self._refresh_remote()

    def _refresh_remote(self, *, manual=False, cancel=False):
        directory, record = self._selected_directory(), self.record
        auth = self.truba.analysis_auth()
        if not directory or not record or not auth or self.remote_task or not record.get("username"):
            return
        if auth.username != record["username"]:
            self.job_error = "Connect using the TRUBA account that submitted this job."
            return
        if record["state"] in jobs.TERMINAL and (not record.get("job_id") or ("files" in record and not manual)):
            return
        self.job_error = ""

        def refresh():
            if record["state"] not in jobs.TERMINAL:
                jobs.refresh_remote(directory, record, auth, cancel=cancel)
            if record["state"] in jobs.TERMINAL:
                jobs.remote_files(directory, record, auth)

        self.remote_task = (directory, self.executor.submit(refresh))
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
            self.extra = []
            self.ranks.setValue(self.record["ranks"])
            self.whole_folder.setChecked(True)
            self._update_inputs()

    def _result_path(self):
        directory = self._selected_directory()
        item = self.results.currentItem()
        if not directory or not item or not self.record:
            return None
        if self.record["target"] == "remote":
            directory = directory / "downloads"
        try:
            path = directory / jobs.relative_file(item.text())
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
