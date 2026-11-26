"""Persistent analysis snapshots using the established batch worker and table."""
from contextlib import contextmanager
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QLockFile, Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QTableWidgetItem

from design_exchange import pool_directory
from doe_studio import (COL_RUN, COL_STRUCTURE, ExperimentTableWidget, ExperimentWindow,
                        ExperimentRow, TABLE_COLUMNS, generate_doe_rows)
from model import settings_from_dict, validate_settings


class QueueStore:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("RUSTMECH_ANALYSIS_QUEUE") or
                         pool_directory().parent / "RustMech" / "analysis_queue.json")
        self.digest = None

    def load(self):
        if not self.path.exists():
            return []
        content = self.path.read_bytes()
        data = json.loads(content)
        if data.get("schema_version") != 1 or not isinstance(data.get("jobs"), list):
            raise ValueError("Unsupported analysis queue file.")
        rows = []
        for raw in data["jobs"]:
            raw = dict(raw)
            # Version 1 files can contain entries from the retired folder workflow.
            if raw.pop("folder_job", None) is not None:
                continue
            raw["design_settings"] = settings_from_dict(raw["design_settings"])
            row = ExperimentRow(**raw)
            if row.status.startswith(("Checking", "Solving", "TRUBA")):
                row.status = "Interrupted - check solver/job before retry"
                row.enabled = False
            rows.append(row)
        self.digest = sha256(content).hexdigest()
        return rows

    def save(self, rows):
        with self._write_lock():
            self._write_rows(rows)

    @contextmanager
    def _write_lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(self.path) + ".lock")
        if not lock.tryLock(0):
            raise OSError("Another RustMech instance is saving the analysis queue.")
        try:
            current = sha256(self.path.read_bytes()).hexdigest() if self.path.exists() else None
            if current != self.digest:
                raise OSError("Analysis queue changed in another RustMech instance. Reopen RustMech before editing it.")
            yield
        finally:
            lock.unlock()

    def _write_rows(self, rows):
        temporary = self.path.with_name(self.path.name + "." + uuid4().hex + ".tmp")
        try:
            content = json.dumps(dict(schema_version=1, jobs=[asdict(row) for row in rows]),
                                 allow_nan=False, ensure_ascii=True, indent=2).encode("utf-8")
            with temporary.open("wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
            self.digest = sha256(content).hexdigest()
        finally:
            temporary.unlink(missing_ok=True)


class AnalysisQueueWidget(ExperimentTableWidget):
    queue_changed = Signal()

    def __init__(self, settings_provider, mesh_generator, batch_solver_runner, parent=None, queue_path=None):
        self.store = QueueStore(queue_path)
        self._storage_ready = False
        self.storage_error = ""
        super().__init__(settings_provider, mesh_generator, batch_solver_runner, parent)
        self.setObjectName("analysisQueueWorkspace")
        self.footer_label.setText("Select rows to move, remove or preview. Each job retains its saved analysis settings.")
        for delta, label in ((-1, "Move up"), (1, "Move down")):
            button = QPushButton(label)
            button.setAutoDefault(False)
            button.clicked.connect(lambda checked=False, step=delta: self.move_selected(step))
            self.run_batch_btn.parentWidget().layout().addWidget(button)
        self.notice = QLabel("Jobs keep their own geometry, material, mesh and solver settings. Run starts only checked pending jobs.")
        self.notice.setWordWrap(True)
        self.layout().insertWidget(self.layout().count() - 1, self.notice)
        try:
            self._rows = self.store.load()
        except (OSError, ValueError, TypeError, KeyError) as error:
            self.storage_error = f"Queue could not be restored; original file retained: {error}"
            self.notice.setText(self.storage_error)
        self._storage_ready = not bool(self.storage_error)
        self._refresh_table_view()
        self.run_batch_btn.setText("Run analysis queue")

    def _refresh_table_view(self):
        super()._refresh_table_view()
        self._updating_table = True
        try:
            count = len(TABLE_COLUMNS)
            self.table.setColumnCount(count + 3)
            for offset, title in enumerate(("Material", "Solver", "Elements")):
                self.table.setHorizontalHeaderItem(count + offset, QTableWidgetItem(title))
            for index, row in enumerate(self._rows):
                settings = row.design_settings
                for offset, value in enumerate((settings.material_name, settings.solver_backend,
                                                settings.mesh_type)):
                    item = QTableWidgetItem(str(value))
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.table.setItem(index, count + offset, item)
            header = self.table.horizontalHeader()
            for offset in range(3):
                header.moveSection(header.visualIndex(count + offset), 2 + offset)
            self.table.resizeColumnsToContents()
        finally:
            self._updating_table = False
        self.queue_changed.emit()

    def save_queue(self):
        if not self._storage_ready:
            return
        try:
            self.store.save(self._rows)
            self.storage_error = ""
        except (OSError, ValueError) as error:
            self.storage_error = str(error)
            self.notice.setText("Queue not saved: " + str(error))

    def _new_dataset_path(self):
        # The queue's results never share a file with a DoE session dataset.
        return self.store.path.parent / "analysis_queue_results.csv"

    def add_snapshot(self, settings):
        if self._active_mode is not None:
            raise ValueError("Stop the analysis queue before adding jobs.")
        if not self._storage_ready or self.storage_error:
            raise ValueError(self.storage_error or "Analysis queue is not writable.")
        errors = validate_settings(settings)
        if errors:
            raise ValueError("\n".join(errors))
        snapshot = settings_from_dict(asdict(settings))
        row = generate_doe_rows([snapshot], {}, 1, 42)[0]
        row.exp_id = "JOB-" + uuid4().hex[:10].upper()
        # Save before displaying success. A failed write must not enqueue a job.
        self.store.save([*self._rows, row])
        self._rows.append(row)
        self._refresh_table_view()
        self.table.selectRow(len(self._rows) - 1)
        return row

    def _write_row_cells(self, index, row):
        super()._write_row_cells(index, row)
        settings = row.design_settings
        for col in range(self.table.columnCount()):
            item = self.table.item(index, col)
            if item and col != COL_RUN:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setToolTip(
                    f"{settings.imported_geometry_name or settings.structure_name}\n"
                    f"{settings.material_name} | {settings.solver_backend} | {settings.mesh_type}\n"
                    f"Global mesh: {settings.mesh_resolution:g} mm | Local: {settings.local_mesh_size:g} mm\n"
                    f"Loading: {settings.loading_mode} | Displacement: {settings.compression:g} mm\n"
                    f"Force: {settings.applied_force:g} N")
        if settings.imported_geometry_path:
            self.table.item(index, COL_STRUCTURE).setText(settings.imported_geometry_name)

    def _update_badge(self):
        super()._update_badge()
        self.save_queue()

    def _launch_next_in_queue(self):
        self.save_queue()
        if self.storage_error:
            self._queue.clear()
        super()._launch_next_in_queue()
        self.save_queue()

    def _on_table_item_changed(self, item):
        if self._updating_table:
            return
        # Snapshot fields are immutable; only the explicit run checkbox changes.
        if item.column() == COL_RUN and self._active_mode is None:
            self._rows[item.row()].enabled = item.checkState() == Qt.CheckState.Checked
            self._update_badge()

    def delete_selected_rows(self):
        if self._active_mode is not None:
            return
        selected = {self._rows[index.row()].exp_id for index in self.table.selectedIndexes()}
        try:
            self.remove_jobs(selected)
        except (OSError, ValueError) as error:
            self.notice.setText(str(error))

    def remove_jobs(self, job_ids):
        if self._active_mode is not None or self._active_future is not None:
            raise ValueError("Stop the analysis queue before removing jobs.")
        if not self._storage_ready or self.storage_error:
            raise ValueError(self.storage_error or "Analysis queue is not writable.")
        removed = [row for row in self._rows if row.exp_id in job_ids]
        if not removed:
            return []
        remaining = [row for row in self._rows if row.exp_id not in job_ids]
        self.store.save(remaining)
        self._rows[:] = remaining
        self._refresh_table_view()
        return removed

    def move_selected(self, delta):
        if self._active_mode is not None:
            return
        indices = sorted({item.row() for item in self.table.selectedIndexes()}, reverse=delta > 0)
        selected = set(indices)
        for index in indices:
            target = index + delta
            if 0 <= target < len(self._rows) and target not in selected:
                self._rows[index], self._rows[target] = self._rows[target], self._rows[index]
                selected.remove(index)
                selected.add(target)
        self._refresh_table_view()
        self.table.clearSelection()
        for index in selected:
            for col in range(self.table.columnCount()):
                self.table.item(index, col).setSelected(True)


class AnalysisQueueWindow(ExperimentWindow):
    def __init__(self, widget, parent=None):
        super().__init__(widget, parent)
        self.setObjectName("analysisQueueWindow")
        self.setWindowTitle("Analysis queue - RustMech")

    def closeEvent(self, event):
        self.centralWidget().save_queue()
        super().closeEvent(event)
