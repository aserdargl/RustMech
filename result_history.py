"""Discover saved analysis outputs and present them in a single history window."""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHeaderView, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from studio_style import CONTROL_HEIGHT
from ui_language import translate


@dataclass(frozen=True)
class SavedResult:
    path: Path
    name: str
    kind: str
    modified: float
    solver: str = ""


def _read_report(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _entries(path, pattern="*"):
    try:
        return tuple(path.glob(pattern))
    except OSError:
        return ()


def _result(path, marker, kind, *, name=None, solver=""):
    try:
        modified = marker.stat().st_mtime
    except OSError:
        return None
    return SavedResult(path, name or path.name, kind, modified, solver)


def _analysis_result(folder, *, name=None):
    for filename, kind in (
        ("flow_summary.json", "Flow (CFD)"),
        ("thermal_summary.json", "Thermal (CHT)"),
        ("mpm_summary.json", "MPM"),
    ):
        marker = folder / filename
        if marker.is_file():
            return _result(folder, marker, kind, name=name)

    marker = folder / "summary.json"
    report = _read_report(marker)
    if report.get("status") in ("completed", "completed_with_force_balance_warning", "partial_solution"):
        return _result(folder, marker, "Mechanic", name=name, solver=report.get("solver", ""))

    marker = folder / "status.json"
    state = _read_report(marker).get("state")
    if state in ("complete", "incomplete") and _entries(folder / "frames", "step_*.vtp"):
        return _result(folder, marker, "Peridynamics", name=name)
    if state == "complete" and (folder / "stiffness_mpa.csv").is_file():
        return _result(folder, marker, "DAMASK FFT", name=name)
    return None


def _remote_result(folder, *, name=None):
    output = folder / "output"
    marker = output / "result.json"
    report = _read_report(marker)
    operation = report.get("operation")
    if operation == "convergence_level" and not report.get("error"):
        return _result(output, marker, "Mesh convergence", name=name or folder.name)
    if operation not in (
        "mechanical", "fft_solve", "peridynamic_solve", "mpm_solve", "flow_solve", "thermal_solve", "doe_solve",
    ):
        return None

    # Resolve the layout on disk. Older records can still contain a remote path.
    for candidate in (output, *_entries(output), *_entries(output / "HumanResults")):
        if candidate.is_dir():
            result = _analysis_result(candidate, name=name or folder.name)
            if result is not None:
                return result
    return None


def _doe_results(folder):
    # Grouped submissions put row manifests directly in their downloaded output.
    for base in (folder, folder / "truba" / "output"):
        for marker in _entries(base, "row_*/experiment.json"):
            report = _read_report(marker)
            if report.get("state") != "Completed":
                continue
            row = marker.parent
            saved = report.get("results")
            target = Path(saved) if isinstance(saved, str) and saved else row
            if not target.is_absolute():
                target = row / target
            if not target.is_relative_to(folder) or not target.is_dir():
                target = row
            name = f"{folder.name} / {row.name}"
            yield _analysis_result(target, name=name) or _result(target, marker, "DoE", name=name)


def discover_results(root, doe_root, *, esen_root=None, remote_folders=(), analysis_folders=()):
    """Read existing on-disk formats without requiring an index or a connection."""
    candidates = []
    human_results = root / "HumanResults"
    for folder in _entries(human_results):
        candidates.append(_analysis_result(folder))
    for folder in _entries(root / "results"):
        if not folder.is_dir() or (human_results / folder.name).is_dir():
            continue
        if folder.name.startswith("TRUBA_"):
            candidates.append(_remote_result(folder))
            continue
        result = _analysis_result(folder) or _analysis_result(folder / "full_export", name=folder.name)
        if result is None:
            for filename in ("history.csv", "force_displacement.csv"):
                marker = folder / filename
                if marker.is_file():
                    result = _result(folder, marker, "Mechanic")
                    break
        candidates.append(result)
    for folder in remote_folders:
        candidates.append(_remote_result(folder))
    for folder in analysis_folders:
        candidates.append(_analysis_result(folder) or _remote_result(folder / "TRUBA", name=folder.name))
    for path in _entries(doe_root):
        if path.is_file() and path.suffix.lower() == ".csv":
            candidates.append(_result(path, path, "DoE"))
        elif (path / "matrix.csv").is_file():
            candidates.extend(_doe_results(path))
    if esen_root is not None:
        from esen_jobs import result_directory

        for marker in _entries(esen_root, "*/job.json"):
            record = _read_report(marker)
            if record.get("state") != "Completed" or record.get("target") not in ("local", "remote"):
                continue
            folder = marker.parent
            try:
                exported = result_directory(folder, record)
            except (TypeError, ValueError):
                continue
            if exported is not None and exported.is_dir():
                folder = exported
            elif (folder / "downloads").is_dir():
                folder = folder / "downloads"
            elif record["target"] == "local" and (folder / "work").is_dir():
                folder = folder / "work"
            else:
                continue
            name = f"{record.get('name') or folder.name} · {marker.parent.name}"
            candidates.append(_result(folder, marker, "ESEN", name=name))
    return sorted(
        {item.path: item for item in candidates if item is not None}.values(),
        key=lambda item: (item.modified, item.name, str(item.path)),
        reverse=True,
    )


class ResultHistoryDialog(QDialog):
    def __init__(self, results, open_result, parent):
        super().__init__(parent)
        self.setObjectName("resultHistory")
        self.setWindowTitle("Results")
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(820, 480)
        self.open_result = open_result

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(7)
        self.table = QTableWidget(len(results), 3)
        self.table.setAccessibleName("Results")
        self.table.setHorizontalHeaderLabels(("Date modified", "Analysis", "Run name"))
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(CONTROL_HEIGHT)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setProperty("preserveItemText", True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.results = results
        for row, result in enumerate(results):
            date = datetime.fromtimestamp(result.modified).strftime("%Y-%m-%d %H:%M:%S")
            for column, value in enumerate((date, translate(result.kind), result.name)):
                item = QTableWidgetItem(value)
                item.setToolTip(str(result.path))
                self.table.setItem(row, column, item)
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, row)
        self.table.setSortingEnabled(True)
        self.table.sortItems(0, Qt.SortOrder.DescendingOrder)
        self.table.itemSelectionChanged.connect(self._update_open_button)
        self.table.cellDoubleClicked.connect(lambda row, _: self._open_row(row))
        layout.addWidget(self.table, 1)
        if not results:
            layout.addWidget(QLabel("No saved analysis results found."))

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.open_button = QPushButton("Open")
        self.open_button.setObjectName("primaryLaunchBtn")
        self.open_button.clicked.connect(lambda: self._open_row(self.table.currentRow()))
        self.open_button.setEnabled(False)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)
        width = max(self.fontMetrics().horizontalAdvance(translate(label)) for label in ("Open", "Close")) + 24
        for button in (self.open_button, close_button):
            button.setProperty("managesLocalizedWidth", True)
            button.setAutoDefault(False)
            button.setFixedSize(width, CONTROL_HEIGHT)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.table.setFocus()

    def _update_open_button(self):
        self.open_button.setEnabled(bool(self.table.selectedItems()))

    def _open_row(self, row):
        item = self.table.item(row, 0)
        if item is not None and self.open_result(self.results[item.data(Qt.ItemDataRole.UserRole)]):
            self.accept()
