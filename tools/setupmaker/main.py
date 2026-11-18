"""Native Setup Maker UI, with builds delegated to the selected project's Python."""

import argparse
import codecs
import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

from PySide6.QtCore import QProcess, QProcessEnvironment, QSettings, QSignalBlocker, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from app_identity import configure_application
from file_dialog import QFileDialog
from studio_style import CompactSpinBox, STUDIO_STYLESHEET
from tools.setupmaker.worker import (
    RELEASE_REPO, REPO_PATTERN, build_python, check_project, read_version, release_version, version_parts,
)


APP_NAME = "RustMech Setup Maker"
WORKER = (Path(sys._MEIPASS) / "tools/setupmaker/worker.py" if getattr(sys, "frozen", False)
          else Path(__file__).with_name("worker.py"))
STYLE = """
QMainWindow#setupMaker { background: #F5F5F3; }
QFrame[setupCard="true"] { background: #FCFCFB; border: 1px solid #D5DBE2; border-radius: 7px; }
QLabel[setupSection="true"] {
    background: #E5ECF4; border: 1px solid #D5DBE2; border-radius: 5px;
    color: #0F172A; font-size: 13px; padding: 0 7px;
}
QMainWindow#setupMaker QLabel[setupField="true"] { color: #475569; font-size: 11px; }
QFrame[setupCard="true"] QLineEdit[setupInput="true"] {
    background: white; border: 1px solid #CBD5E1; border-radius: 4px; font-size: 11px;
}
QFrame[setupCard="true"] QLineEdit[setupInput="true"]:hover { border-color: #93C5FD; }
QFrame[setupCard="true"] QLineEdit[setupInput="true"]:focus { border-color: #2563EB; }
QFrame[setupCard="true"] QLineEdit[setupInput="true"]:disabled { background: #F1F5F9; }
QPlainTextEdit#setupLog, QPlainTextEdit#setupNotes {
    background: white; border: 1px solid #DCE3EC; border-radius: 3px;
}
QPlainTextEdit#setupLog:focus, QPlainTextEdit#setupNotes:focus { border-color: #2563EB; }
QProgressBar#setupProgress {
    border: 1px solid #CBD5E1; border-radius: 4px; background: white; text-align: center;
}
QProgressBar#setupProgress::chunk { background: #BFDBFE; border-radius: 3px; }
"""


def default_project():
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parents[2]
    exe_directory = Path(sys.executable).resolve().parent
    candidates = (exe_directory.parent, exe_directory, Path.cwd())
    return next((path for path in candidates if (path / "Build-Exe.ps1").is_file()), candidates[0])


def worker_python(project):
    return build_python(project, None if getattr(sys, "frozen", False) else sys.executable)


def child_environment():
    environment = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    if getattr(sys, "frozen", False):
        extraction = Path(sys._MEIPASS)
        environment["PATH"] = os.pathsep.join(
            part for part in environment.get("PATH", "").split(os.pathsep)
            if part and not Path(part).is_relative_to(extraction)
        )
        # A frozen launcher must not pass its DLL search directory to system Python.
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    return environment


def button(text, action, *, primary=False):
    control = QPushButton(text)
    control.setAutoDefault(False)
    control.setFixedHeight(25)
    control.setProperty("primary", primary)
    control.clicked.connect(action)
    return control


def section(parent, title):
    card = QFrame()
    card.setProperty("setupCard", True)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(7, 6, 7, 6)
    layout.setSpacing(4)
    header = QLabel(title)
    header.setProperty("setupSection", True)
    header.setFixedHeight(25)
    layout.addWidget(header)
    parent.addWidget(card)
    return layout


def field(name, text=""):
    control = QLineEdit(text)
    control.setAccessibleName(name)
    control.setProperty("setupInput", True)
    control.setFixedHeight(25)
    return control


def label(text, buddy=None):
    control = QLabel(text)
    control.setProperty("setupField", True)
    if buddy is not None:
        control.setBuddy(buddy)
    return control


class SetupMakerWindow(QMainWindow):
    def __init__(self, project, settings, *, explicit_project=False):
        super().__init__()
        self.settings = settings
        self.project_root = None
        self.python = None
        self.log_file = None
        self.installer = None
        self.job = None
        self.pending = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.started.connect(self.send_job)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.process_error)

        self.setObjectName("setupMaker")
        self.setWindowTitle(APP_NAME)
        self.resize(650, 570)
        saved_project = project if explicit_project else settings.value("project", str(project))
        self.create_ui(str(saved_project))
        self.refresh()

    def create_ui(self, project):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(7)

        self.inputs = QWidget()
        inputs = QVBoxLayout(self.inputs)
        inputs.setContentsMargins(0, 0, 0, 0)
        inputs.setSpacing(7)
        layout.addWidget(self.inputs)
        self.project_section(inputs, project)
        self.version_section(inputs)
        self.github_section(inputs)

        self.status = label("Hazır")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setObjectName("setupProgress")
        self.progress.setFixedHeight(20)
        self.progress.setAccessibleName("Derleme ilerlemesi")
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setObjectName("setupLog")
        self.log.setAccessibleName("İşlem günlüğü")
        self.log.setPlaceholderText("İşlem günlüğü")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        layout.addWidget(self.log, 1)

        actions = QHBoxLayout()
        output = button("Klasörü aç", self.open_output)
        self.refresh_button = button("Yenile", self.refresh)
        self.build_button = button("Kurulum oluştur", self.start_build, primary=True)
        width = max(control.fontMetrics().horizontalAdvance(control.text())
                    for control in (output, self.refresh_button)) + 22
        for control in (output, self.refresh_button):
            control.setFixedWidth(width)
            actions.addWidget(control)
        self.build_button.setFixedWidth(self.build_button.fontMetrics().horizontalAdvance(self.build_button.text()) + 24)
        actions.addStretch()
        actions.addWidget(self.build_button)
        layout.addLayout(actions)

    def project_section(self, inputs, project):
        panel = section(inputs, "Proje")
        grid = QGridLayout()
        grid.setVerticalSpacing(3)
        panel.addLayout(grid)
        self.project = field("Kaynak klasörü", project)
        self.project.editingFinished.connect(self.refresh_if_changed)
        browse = button("Gözat…", self.browse)
        browse.setFixedWidth(browse.fontMetrics().horizontalAdvance(browse.text()) + 22)
        grid.addWidget(label("Kaynak klasörü", self.project), 0, 0)
        grid.addWidget(self.project, 0, 1)
        grid.addWidget(browse, 0, 2)
        self.health = label("")
        self.health.setWordWrap(True)
        grid.addWidget(self.health, 1, 1, 1, 2)

    def version_section(self, inputs):
        panel = section(inputs, "Sürüm")
        parts = QHBoxLayout()
        panel.addLayout(parts)
        self.prefix = field("Ön ek")
        self.suffix = field("Son ek")
        self.major, self.minor = CompactSpinBox(), CompactSpinBox()
        parts.addWidget(self.prefix, 1)
        for name, control, maximum in (("Ana sürüm", self.major, 65535), ("Alt sürüm", self.minor, 10)):
            control.setRange(0, maximum)
            control.setFixedSize(74, 25)
            control.setAccessibleName(name)
            control.setToolTip(name)
            control.setKeyboardTracking(False)
            control.valueChanged.connect(self.spin_changed)
            parts.addWidget(control)
        parts.addWidget(self.suffix, 1)
        for control in (self.prefix, self.suffix):
            control.setPlaceholderText(control.accessibleName())
            control.textEdited.connect(self.compose_version)
        target = QHBoxLayout()
        panel.addLayout(target)
        self.target = field("Hedef sürüm")
        self.target.setMaxLength(64)
        self.target.textEdited.connect(self.sync_version_parts)
        self.target.textChanged.connect(self.update_build_button)
        target.addWidget(label("Hedef sürüm", self.target))
        target.addWidget(self.target, 1)

    def github_section(self, inputs):
        panel = section(inputs, "GitHub Release")
        self.publish = QCheckBox("Kurulumdan sonra GitHub'a yükle")
        panel.addWidget(self.publish)
        grid = QGridLayout()
        grid.setVerticalSpacing(3)
        panel.addLayout(grid)
        self.repo = field("Hedef depo", self.settings.value("repo", RELEASE_REPO))
        self.notes = QPlainTextEdit(self.settings.value("notes", ""))
        self.notes.setObjectName("setupNotes")
        self.notes.setFixedHeight(50)
        self.notes.setPlaceholderText("Bu sürümde neler değişti?")
        grid.addWidget(label("Hedef depo", self.repo), 0, 0)
        grid.addWidget(self.repo, 0, 1)
        grid.addWidget(label("Yayın notları", self.notes), 1, 0)
        grid.addWidget(self.notes, 1, 1)

    def browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Kaynak klasörü", self.project.text())
        if folder:
            self.project.setText(folder)
            self.refresh_if_changed()

    def refresh_if_changed(self):
        if self.project.text() != self.checked_project:
            self.refresh()

    def refresh(self):
        self.checked_project = self.project.text()
        self.project_root = self.python = None
        try:
            if not self.checked_project.strip():
                raise ValueError("RustMech kaynak klasörünü seçin.")
            project = Path(self.checked_project.strip()).expanduser().resolve()
            python = worker_python(project)
            check_project(project, python)
            version = read_version(project)
        except (OSError, ValueError, RuntimeError) as error:
            self.health.setText(str(error))
        else:
            if self.installer and not self.installer.is_relative_to(project):
                self.installer = None
            self.project_root, self.python = project, python
            self.health.setText(f"Proje hazır · Mevcut sürüm {version}")
            self.sync_version_parts(version)
            self.target.setText(version)
        self.update_build_button()

    def sync_version_parts(self, text):
        prefix, major, minor, suffix = version_parts(text)
        with (QSignalBlocker(self.major), QSignalBlocker(self.minor)):
            self.major.setValue(min(major, 65535))
            self.minor.setMaximum(max(10, min(minor, 65535)))
            self.minor.setValue(min(minor, 65535))
        self.prefix.setText(prefix)
        self.suffix.setText(suffix)

    def spin_changed(self):
        with QSignalBlocker(self.major), QSignalBlocker(self.minor):
            if self.minor.value() == 10 and self.major.value() < self.major.maximum():
                self.major.stepUp()
                self.minor.setValue(0)
            if self.minor.value() < 10:
                self.minor.setMaximum(10)
        self.compose_version()

    def compose_version(self):
        prefix, suffix = self.prefix.text().strip(), self.suffix.text().strip()
        if prefix and prefix.split()[-1].casefold() != "v":
            prefix += " "
        if suffix and not suffix.startswith("."):
            suffix = " " + suffix
        self.target.setText(f"{prefix}{self.major.value()}.{self.minor.value()}{suffix}")

    def update_build_button(self):
        try:
            release_version(self.target.text())
        except ValueError:
            valid = False
        else:
            valid = True
        self.build_button.setEnabled(self.job is None and self.project_root is not None and valid)

    def open_output(self):
        if self.installer:
            folder = self.installer.parent
        elif self.project_root:
            folder = self.project_root / "dist/SetupMaker"
        else:
            self.status.setText("Önce RustMech kaynak klasörünü seçin.")
            return
        try:
            folder.mkdir(exist_ok=True, parents=True)
        except OSError as error:
            self.status.setText(str(error))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def start_build(self):
        self.refresh_if_changed()
        if self.project_root is None or self.job is not None:
            return
        try:
            version, _, _ = release_version(self.target.text())
            repo = self.repo.text().strip()
            if self.publish.isChecked() and not REPO_PATTERN.fullmatch(repo):
                raise ValueError("Hedef depoyu kullanıcı/depo biçiminde yazın.")
            logs = self.project_root / "dist/SetupMaker/logs"
            logs.mkdir(exist_ok=True, parents=True)
            path = logs / f"{datetime.now():%Y%m%d-%H%M%S-%f}.log"
            self.log_file = path.open("x", encoding="utf-8", newline="")
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
            return

        self.job = dict(version=version, repo=repo, notes=self.notes.toPlainText(), publish=self.publish.isChecked())
        self.installer = None
        self.pending = ""
        self.decoder.reset()
        self.log.clear()
        self.inputs.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.update_build_button()
        self.status.setText("Derleme başlatılıyor…")
        self.progress.setValue(0)
        self.log.appendPlainText(f"Günlük · {path}")
        environment = QProcessEnvironment()
        for key, value in child_environment().items():
            environment.insert(key, value)
        self.process.setProcessEnvironment(environment)
        self.process.setWorkingDirectory(str(self.project_root))
        self.process.start(str(self.python), ["-I", "-X", "utf8", "-u", str(WORKER), str(self.project_root)])

    def send_job(self):
        self.process.write(json.dumps(self.job).encode("utf-8"))
        self.process.closeWriteChannel()

    def read_output(self, final=False):
        chunk = self.decoder.decode(bytes(self.process.readAllStandardOutput()), final=final)
        if self.log_file is not None and chunk:
            try:
                self.log_file.write(chunk)
                self.log_file.flush()
            except OSError as error:
                self.log.appendPlainText(f"Günlük dosyasına yazılamadı · {error}")
                self.log_file.close()
                self.log_file = None
        lines = (self.pending + chunk).split("\n")
        self.pending = lines.pop()
        if final and self.pending:
            lines.append(self.pending)
        for raw in lines:
            line = raw.rstrip("\r")
            marker, _, message = line.partition("|")
            if marker == "PROGRESS":
                percent, _, status = message.partition("|")
                if percent.isdecimal():
                    self.progress.setValue(int(percent))
                    self.status.setText(status)
                    line = status
            elif marker == "RESULT":
                self.installer = Path(message)
                line = f"Kurulum dosyası · {message}"
            elif marker == "ERROR":
                line = message
            self.log.appendPlainText(line)

    def finished(self, code, exit_status):
        self.read_output(final=True)
        if code or exit_status != QProcess.ExitStatus.NormalExit:
            text = "Kurulum dosyası hazır. GitHub yayını başarısız." if self.installer else "Derleme başarısız."
            self.status.setText(f"{text} İşlem günlüğünü inceleyin.")
        self.unlock()

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.status.setText(f"Derleme işlemi başlatılamadı · {self.process.errorString()}")
            self.unlock()

    def unlock(self):
        if self.log_file is not None:
            self.log_file.close()
            self.log_file = None
        self.job = None
        self.inputs.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.update_build_button()

    def closeEvent(self, event):
        if self.job is not None:
            self.status.setText("Derleme devam ediyor. Tamamlandıktan sonra pencereyi kapatabilirsiniz.")
            event.ignore()
            return
        self.settings.setValue("project", self.project.text())
        self.settings.setValue("repo", self.repo.text())
        self.settings.setValue("notes", self.notes.toPlainText())
        self.settings.sync()
        super().closeEvent(event)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path)
    parser.add_argument("--check-worker", action="store_true")
    options = parser.parse_args()
    project = (options.project or default_project()).resolve()
    if options.check_worker:
        python = worker_python(project)
        if python is None:
            return 1
        return subprocess.run(
            [str(python), "-I", "-X", "utf8", str(WORKER), str(project), "--check"],
            env=child_environment(), stdout=sys.stdout, stderr=sys.stderr,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).returncode
    app = QApplication(sys.argv[:1])
    settings = QSettings("RustMech", "SetupMaker")
    configure_application(app, name=APP_NAME, app_id="RustMech.SetupMaker", settings=settings)
    app.setStyleSheet(STUDIO_STYLESHEET + STYLE)
    window = SetupMakerWindow(project, settings, explicit_project=options.project is not None)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
