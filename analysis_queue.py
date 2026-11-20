"""Persistent analysis snapshots using the established batch worker and table."""
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QLockFile, Qt, Signal
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton, QTableWidgetItem

from design_exchange import pool_directory
from doe_studio import (COL_RUN, COL_STRUCTURE, DoEStudioWidget, DoEStudioWindow,
                        ExperimentRow, TABLE_COLUMNS, generate_doe_rows)
from folder_batch_results import (FolderResultMixin, LEGACY_BATCH_DIRECTORY, batch_directory,
                                  folder_job_row, move_batch_workspace,
                                  relocate_batch_report, relocate_queue_row, validate_folder_snapshot)
from model import ROOT, settings_from_dict, validate_settings


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

    def migrate_batch_workspaces(self, rows, roots=None):
        if roots is None:
            roots = {row.folder_job['root'] for row in rows if row.folder_job}
        candidates = []
        for root in sorted({Path(root).resolve() for root in roots}):
            old, new = root / LEGACY_BATCH_DIRECTORY, batch_directory(root)
            # A prior rename may have completed before the queue file was saved.
            if old.exists() or old.is_symlink() or old.is_junction() or (new.is_dir() and any(
                relocate_queue_row(row, [(old, new)]) != row for row in rows
            )):
                candidates.append(root)
        if not candidates:
            return False

        with self._write_lock(), ExitStack() as moves, ExitStack() as undo:
            aliases = []
            for root in candidates:
                alias = moves.enter_context(move_batch_workspace(root))
                if alias is not None:
                    aliases.append(alias)
            updated = [relocate_queue_row(row, aliases) for row in rows]
            relocate_batch_report(self.path.with_name('analysis_queue_results.csv'), aliases, undo)
            if updated != rows:
                self._write_rows(updated)
            undo.pop_all()
        rows[:] = updated
        return bool(aliases)


class AnalysisQueueWidget(FolderResultMixin, DoEStudioWidget):
    queue_changed = Signal()

    def __init__(self, settings_provider, mesh_generator, batch_solver_runner, parent=None, queue_path=None):
        self.store = QueueStore(queue_path)
        self._storage_ready = False
        self.storage_error = ""
        super().__init__(settings_provider, mesh_generator, batch_solver_runner, parent)
        self.setObjectName("analysisQueueWorkspace")
        # Reuse the proven batch lifecycle without exposing sampling/generation
        # operations: every job is an independently configured model snapshot.
        self.plan_splitter.hide()
        self.matrix_tabs.hide()
        self.footer_label.setText("Select rows to move, remove or preview. Each job retains its saved analysis settings.")
        for button in self.findChildren(QPushButton):
            if button.text() in ("+ Row", "Table data"):
                button.hide()
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
        else:
            try:
                self.store.migrate_batch_workspaces(self._rows)
            except (OSError, ValueError) as error:
                self.notice.setText(f"Batch workspace migration could not finish: {error}")
        self._matrix_rows[0] = self._rows
        self._storage_ready = not bool(self.storage_error)
        self._refresh_table_view()
        self.run_batch_btn.setText("Run analysis queue")

    def migrate_batch_workspace(self, root):
        legacy = root / LEGACY_BATCH_DIRECTORY
        if not (legacy.exists() or legacy.is_symlink() or legacy.is_junction()):
            return
        if self._active_mode is not None or self._active_future is not None:
            raise ValueError("Stop the analysis queue before moving the batch workspace.")
        if not self._storage_ready or self.storage_error:
            raise ValueError(self.storage_error or "Analysis queue is not writable.")
        if self.store.migrate_batch_workspaces(self._rows, [root]):
            self._refresh_table_view()

    def _refresh_table_view(self):
        super()._refresh_table_view()
        self._updating_table = True
        try:
            count = len(TABLE_COLUMNS)
            self.table.setColumnCount(count + 4)
            for offset, title in enumerate(("Material", "Solver", "Elements", "Execution target")):
                self.table.setHorizontalHeaderItem(count + offset, QTableWidgetItem(title))
            for index, row in enumerate(self._rows):
                settings = row.design_settings
                target = (row.folder_job or {}).get('execution_target')
                target_label = {'truba': 'TRUBA', 'local': 'Local mesh'}.get(target, 'Current workspace')
                for offset, value in enumerate((settings.material_name, settings.solver_backend,
                                                settings.mesh_type, target_label)):
                    item = QTableWidgetItem(str(value))
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.table.setItem(index, count + offset, item)
            header = self.table.horizontalHeader()
            for offset in range(4):
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

    autosave_recovery_plan = False

    def _new_dataset_path(self):
        # The queue's results never share a file with a DoE session dataset.
        return self.store.path.parent / "analysis_queue_results.csv"

    def start_batch_solve(self):
        if self._active_mode is not None or self._active_future is not None:
            return
        if any((self._rows[index].folder_job or {}).get('execution_target') == 'truba'
               for index in self.rows_to_run()):
            controller = getattr(self, 'remote_controller', None)
            try:
                if controller is None or not controller.requested:
                    raise RuntimeError("Enable TRUBA mode and connect before running these jobs.")
                controller.require_analysis('kratos')
            except (RuntimeError, ValueError) as error:
                QMessageBox.warning(self, "TRUBA batch analysis", str(error))
                return
        super().start_batch_solve()

    def add_snapshot(self, settings):
        if self._active_mode is not None:
            raise ValueError("Stop the analysis queue before adding jobs.")
        if not self._storage_ready or self.storage_error:
            raise ValueError(self.storage_error or "Analysis queue is not writable.")
        errors = validate_settings(settings)
        if errors:
            raise ValueError("\n".join(errors))
        snapshot = settings_from_dict(asdict(settings))
        row = generate_doe_rows([snapshot], {}, "grid", 1, 42)[0]
        row.exp_id = "JOB-" + uuid4().hex[:10].upper()
        # Save before displaying success. A failed write must not enqueue a job.
        self.store.save([*self._rows, row])
        self._rows.append(row)
        self._refresh_table_view()
        self.table.selectRow(len(self._rows) - 1)
        return row

    def require_writable_folder_queue(self):
        if self._active_mode is not None or self._active_future is not None:
            raise ValueError("Stop the analysis queue before adding jobs.")
        if not self._storage_ready or self.storage_error:
            raise ValueError(self.storage_error or "Analysis queue is not writable.")

    def add_folder_snapshots(self, snapshots):
        """Publish the whole prepared batch atomically; never start a solver here."""
        self.require_writable_folder_queue()
        known = {row.folder_job['key'] for row in self._rows if row.folder_job}
        added = []
        for settings, metadata in snapshots:
            if metadata['key'] in known:
                continue
            validate_folder_snapshot(settings)
            added.append(folder_job_row(settings, metadata))
            known.add(metadata['key'])
        self.store.save([*self._rows, *added])
        self._rows.extend(added)
        self._refresh_table_view()
        self._write_folder_reports()
        return added

    def _persist_progress(self):
        super()._persist_progress()
        self._write_folder_reports()

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
        self._write_folder_reports()

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


class AnalysisQueueWindow(DoEStudioWindow):
    def __init__(self, widget, parent=None):
        super().__init__(widget, parent)
        self.setObjectName("analysisQueueWindow")
        self.setWindowTitle("Analysis queue - RustMech")

    def closeEvent(self, event):
        self.centralWidget().save_queue()
        super().closeEvent(event)
