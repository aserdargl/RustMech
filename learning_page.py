"""Training controls and results in the Experimental Design workspace."""

from datetime import datetime
import json
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QFormLayout, QHeaderView, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from analysis_process import AnalysisProcess
from doe_studio import results_directory
from learning_data import METHODS, read_dataset, target_columns, training_request
from learning_training import NEURAL, run_local_training
from operation_progress import OperationProgress
from studio_style import CONTROL_HEIGHT, CompactComboBox, CompactSpinBox, SidebarGroupBox
from ui_language import translate


class LearningPage(QWidget):
    prefix = "learningTraining"

    def __init__(self, host, preferences):
        super().__init__()
        self.host, self.preferences = host, preferences
        self.dataset = self.directory = self.job = None
        self.timer = QTimer(self, interval=300)
        self.timer.timeout.connect(self.poll)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 6)
        settings = SidebarGroupBox("Model training")
        form = QFormLayout(settings.content)
        layout.addWidget(settings)
        data_row = QWidget()
        data_layout = QHBoxLayout(data_row)
        data_layout.setContentsMargins(0, 0, 0, 0)
        self.source = QLabel()
        self.source.setTextFormat(Qt.TextFormat.PlainText)
        self.source.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.choose = self._button("Open CSV", self.choose_source)
        data_layout.addWidget(self.choose)
        data_layout.addWidget(self.source, 1)
        form.addRow("Dataset", data_row)
        self.target = CompactComboBox()
        self.target.currentTextChanged.connect(self.target_changed)
        form.addRow("Prediction target", self.target)
        self.method = CompactComboBox()
        for key, name in {"all": "All methods", **METHODS}.items():
            self.method.addItem(name, key)
        self.method.currentTextChanged.connect(lambda name: self.method.setToolTip(translate(name)))
        self.location = CompactComboBox()
        self.location.addItem("Local", "local")
        self.location.addItem("TRUBA", "truba")
        form.addRow("Method", self.method)
        form.addRow("Execution target", self.location)
        self.epochs, self.patience = CompactSpinBox(), CompactSpinBox()
        for field, key, default in ((self.epochs, "epochs", 200), (self.patience, "patience", 20)):
            field.setFixedSize(100, CONTROL_HEIGHT)
            field.setRange(1, 2000)
            field.setValue(self.preference(key, default, int))
            field.valueChanged.connect(lambda value, name=key: self.save(name, value))
        self.epochs.valueChanged.connect(self.patience.setMaximum)
        self.patience.setMaximum(self.epochs.value())
        form.addRow("Maximum epochs", self.epochs)
        form.addRow("Early stopping patience", self.patience)

        buttons = QHBoxLayout()
        self.start = self._button("Train models", self.start_training)
        self.stop = self._button("Stop", self.stop_training)
        self.open = self._button("Open results", self.open_results)
        for button in (self.start, self.stop, self.open):
            buttons.addWidget(button)
        width = max(button.sizeHint().width() for button in (self.start, self.stop, self.open))
        for button in (self.start, self.stop, self.open):
            button.setFixedWidth(width)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.progress = OperationProgress(self)
        layout.addWidget(self.progress)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Method", "Test MAE", "Test RMSE"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)

        for combo, name, default in ((self.method, "method", "gp"), (self.location, "location", "local")):
            combo.setCurrentIndex(max(0, combo.findData(self.preference(name, default, str))))
            combo.currentIndexChanged.connect(lambda _index, c=combo, key=name: self.save(key, c.currentData()))
        self.method.currentIndexChanged.connect(self.refresh)
        self.refresh()

    @staticmethod
    def _button(text, callback):
        button = QPushButton(text)
        button.setAutoDefault(False)
        button.setFixedHeight(CONTROL_HEIGHT)
        button.clicked.connect(callback)
        return button

    def preference(self, name, default, type_):
        return default if self.preferences is None else self.preferences.value(
            f"{self.prefix}/{name}", default, type=type_)

    def save(self, name, value):
        if self.preferences is not None:
            self.preferences.setValue(f"{self.prefix}/{name}", value)

    def choose_source(self):
        from file_dialog import QFileDialog

        filename, _ = QFileDialog.getOpenFileName(self, "Open CSV", str(results_directory()), "CSV (*.csv)")
        if filename:
            self.load_source(filename)

    def restore_source(self, completed_datasets):
        if self.dataset is not None:
            return
        last = self.preference("dataset", "", str)
        if last and Path(last).is_file():
            self.load_source(last)
        elif completed_datasets:
            self.load_source(max(completed_datasets, key=lambda path: path.stat().st_mtime))

    def load_source(self, filename):
        try:
            columns, rows = read_dataset(filename)
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
            return
        self.dataset = (columns, rows)
        self.source.setText(Path(filename).name)
        self.source.setToolTip(str(filename))
        self.save("dataset", str(filename))
        previous = self.preference("output", "", str)
        self.target.blockSignals(True)
        self.target.clear()
        self.target.addItems(target_columns(columns, rows))
        if self.target.findText(previous) >= 0:
            self.target.setCurrentText(previous)
        self.target.blockSignals(False)
        self.target.setToolTip(self.target.currentText())
        self.status.setText(translate("Completed samples: {count}").format(count=len(rows)))
        self.refresh()

    def target_changed(self, target):
        self.target.setToolTip(target)
        if target:
            self.save("output", target)
        self.refresh()

    def refresh(self):
        busy = self.job is not None
        for widget in (self.choose, self.target, self.method, self.location):
            widget.setEnabled(not busy)
        method = self.method.currentData()
        for field in (self.epochs, self.patience):
            field.setEnabled(not busy and (method == "all" or method in NEURAL))
        self.start.setEnabled(not busy and self.target.count() > 0)
        self.stop.setEnabled(busy)
        self.open.setEnabled(self.directory is not None)

    def start_training(self):
        if self.job is not None or self.dataset is None or self.target.count() == 0:
            return
        self.epochs.interpretText()
        self.patience.interpretText()
        method = self.method.currentData()
        directory = results_directory() / ("Training_" + datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid4().hex[:8])
        try:
            request = training_request(self.dataset, self.target.currentText(),
                                       METHODS if method == "all" else [method],
                                       self.epochs.value(), self.patience.value())
            if self.location.currentData() == "truba":
                from remote_operations import required_capability
                from truba_remote import run_remote_operation

                context, auth = self.host.remote_controller.require_analysis(
                    required_capability(request), explicit_target=True)
                self.job = AnalysisProcess(run_remote_operation, request, context, remote_auth=auth,
                                           remote_options={"directory": directory})
            else:
                self.job = AnalysisProcess(run_local_training, request, directory)
        except Exception as error:
            self.status.setText(str(error))
            return
        self.directory = directory
        self.table.setRowCount(0)
        self.status.clear()
        self.progress.update_step(True, "Preparing operation…")
        self.refresh()
        self.timer.start()

    def poll(self):
        if not self.job.done():
            status = self.job.remote_status()
            step = self.job.progress_status() or "Preparing operation…"
            if status:
                step = " · ".join(str(value) for value in (status.get("job_id"), status.get("state")) if value)
            self.progress.update_step(True, step)
            return
        try:
            result = self.job.result()
            if not result:
                raise RuntimeError("Training completed without a results folder.")
            self.show_report(Path(result))
        except Exception as error:
            self.status.setText(str(error).strip().splitlines()[-1] if str(error).strip() else type(error).__name__)
        self.clear_job()

    def show_report(self, directory):
        report = json.loads((directory / "training_report.json").read_text(encoding="utf-8"))
        results = sorted(report["results"].items(), key=lambda item: item[1]["mae"])
        self.table.setRowCount(len(results))
        for row, (method, metrics) in enumerate(results):
            for column, value in enumerate((METHODS[method], f"{metrics['mae']:.5g}", f"{metrics['rmse']:.5g}")):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.directory = directory
        self.status.setText(translate("Training complete. Test geometry groups: {count}").format(
            count=report["groups"]["test"]))

    def clear_job(self):
        self.job = None
        self.timer.stop()
        self.progress.update_step(False, "")
        self.refresh()

    def stop_training(self):
        if self.job is None:
            return
        try:
            self.job.force_stop()
        except Exception as error:
            self.status.setText(str(error))
            return
        self.status.setText(translate("Training stopped."))
        self.clear_job()

    def detach_remote(self):
        if self.job is not None and self.job.is_remote:
            job = self.job
            self.clear_job()
            return job
        return None

    def open_results(self):
        if self.directory is not None and self.directory.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.directory.resolve())))
