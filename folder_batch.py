"""Folder preflight and explicit preparation of sequential mechanical jobs."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import re

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QDialog,
                              QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                              QTableWidget, QTableWidgetItem, QVBoxLayout)

from file_dialog import QFileDialog
from imported_geometry import (SURFACE_EXTENSIONS, SURFACE_FILE_FILTER,
                               automatic_compression_faces, compression_direction, read_surface, store_surface)
from model import settings_from_dict
from studio_style import ActionFlowLayout, CompactComboBox, STUDIO_STYLESHEET
from window_state import install_window_state


@dataclass
class PreparedSpecimen:
    source: str
    digest: str = ''
    cached: str = ''
    height: float = 0
    direction: tuple = (0., 0., 1.)
    cells: tuple | None = None
    error: str = ''


def discover_specimens(directory):
    """Natural filename order; never consume prior results or directory links."""
    root = Path(directory).resolve()
    if not root.is_dir():
        raise ValueError('Choose an existing specimen folder.')
    paths = []
    for parent, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories
                                if name.casefold() != 'result'
                                and not (Path(parent) / name).is_symlink()
                                and not (Path(parent) / name).is_junction())
        for name in files:
            path = Path(parent) / name
            if path.suffix.lower() in SURFACE_EXTENSIONS and not path.is_symlink():
                paths.append(path)
    def natural(path):
        return [int(part) if part.isdigit() else part.casefold()
                for part in re.split(r'(\d+)', path.relative_to(root).as_posix())]
    return sorted(paths, key=natural)


def source_digest(path):
    digest = sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def prepare_specimen(path, direction):
    item = PreparedSpecimen(str(path), direction=tuple(float(x) for x in direction))
    try:
        item.digest = source_digest(path)
        surface = read_surface(path)
        aligned, item.height, item.cells = automatic_compression_faces(surface, direction)
        item.cached = store_surface(aligned)
        if source_digest(path) != item.digest:
            raise ValueError('Source changed during preparation. Check the folder again.')
    except Exception as error:
        item.error = str(error)
        item.cached = ''
    return item


def specimen_snapshots(item, root, base, solvers):
    if item.error or not item.cached:
        raise ValueError('Choose valid support and compression faces first.')
    if source_digest(item.source) != item.digest:
        raise ValueError('Source changed after preparation. Check the folder again.')
    relative = Path(item.source).relative_to(root)
    # Keep the original units and loading magnitude. No automatic strain scaling.
    settings = replace(base, imported_geometry_path=item.cached,
                       imported_geometry_name=relative.as_posix(), imported_geometry_role='compression',
                       size=item.height, test_profile='custom',
                       mesh_type=base.mesh_type if base.mesh_type in ('TET4', 'TET10') else 'TET4',
                       tetra_strategy='netgen' if base.tetra_strategy == 'periodic' else base.tetra_strategy)
    name = re.sub(r'[^\w.-]+', '_', relative.stem)[:70] or 'specimen'
    name += '-' + sha256(relative.as_posix().encode()).hexdigest()[:8]
    for solver in solvers:
        snapshot = replace(settings, solver_backend=solver)
        key = sha256(json.dumps([str(root), relative.as_posix(), item.digest, item.direction, asdict(snapshot)],
                                sort_keys=True).encode()).hexdigest()
        metadata = dict(root=str(root), source=str(item.source), sha256=item.digest,
                        direction=list(item.direction), key=key,
                        destination=str(root / 'Result' / name / 'Mechanical' / solver))
        yield snapshot, metadata


class FolderBatchDialog(QDialog):
    def __init__(self, settings_provider, queue, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        install_window_state(QApplication.instance())
        self.setObjectName('folderBatchWindow')
        self.setWindowTitle('Batch analysis from folder - RustMech')
        self.setStyleSheet(STUDIO_STYLESHEET)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinimizeButtonHint | Qt.WindowType.WindowMaximizeButtonHint)
        self.resize(920, 600)
        self.setMinimumSize(620, 420)
        self.setSizeGripEnabled(True)
        self.settings_provider, self.queue = settings_provider, queue
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='folder-preflight')
        self.future = None
        self.items = []
        self.pending = []
        self.root = None
        self.base = None
        self.disposed = False
        layout = QVBoxLayout(self)
        hint = QLabel('Mechanical compression • coordinates in mm. Choose a direction or select faces on the first specimen. '
                      'Every specimen is checked independently. Isotropy is not assumed. Flow and thermal studies use their existing workspaces.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        self.folder = QLineEdit()
        self.folder.setPlaceholderText('Folder containing specimen geometries')
        self.folder.setAccessibleName('Specimen folder')
        row.addWidget(self.folder, 1)
        self.browse = self.button('Choose folder...', self.choose_folder)
        row.addWidget(self.browse)
        layout.addLayout(row)
        controls = QHBoxLayout()
        controls.addWidget(QLabel('Compression direction'))
        self.mode = CompactComboBox()
        self.mode.addItems(['Z: bottom / top', 'X: minimum / maximum', 'Y: minimum / maximum',
                            'Direction from first specimen', 'Select every specimen manually'])
        self.mode.setFixedWidth(195)
        controls.addWidget(self.mode)
        controls.addWidget(QLabel('Solver'))
        self.solver = CompactComboBox()
        self.solver.addItems(['Current workspace', 'ANSYS MAPDL', 'Kratos', 'ANSYS + Kratos'])
        self.solver.setFixedWidth(155)
        controls.addWidget(self.solver)
        controls.addStretch()
        layout.addLayout(controls)
        self.check = self.button('Check folder', self.check_folder)
        layout.addWidget(self.check, 0, Qt.AlignmentFlag.AlignLeft)
        self.settings_hint = QLabel('Material, mesh, loading, contact and solver parameters are captured from the main Mechanic workspace when checking. '
                                   'Displacement stays in mm; force stays in N. Imported geometries use tetrahedral elements.')
        self.settings_hint.setWordWrap(True)
        layout.addWidget(self.settings_hint)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Run', 'Specimen', 'Preflight'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        actions = ActionFlowLayout()
        self.edit = self.button('Inspect / select faces...', self.edit_selected)
        self.enqueue = self.button('Add ready specimens to queue', self.enqueue_ready)
        action_width = max(self.edit.sizeHint().width(), self.enqueue.sizeHint().width())
        for button in (self.edit, self.enqueue):
            button.setFixedWidth(action_width)
        self.show_queue = self.button('Open analysis queue', self.open_queue)
        close = self.button('Close', self.close)
        for button in (self.edit, self.enqueue, self.show_queue, close):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.status = QLabel('Choose a folder and check it. No analysis starts during preparation.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.folder.textChanged.connect(self.invalidate)
        self.mode.currentIndexChanged.connect(self.invalidate)
        self.set_busy(False)

    def button(self, text, callback):
        button = QPushButton(text, self)
        button.setAutoDefault(False)
        button.setDefault(False)
        button.clicked.connect(callback)
        button.setFixedWidth(button.sizeHint().width())
        return button

    def invalidate(self, *_):
        self.items.clear()
        self.table.setRowCount(0)
        self.root = None
        self.base = None
        self.enqueue.setEnabled(False)

    def set_busy(self, busy):
        for widget in (self.folder, self.browse, self.mode, self.solver, self.check, self.edit):
            widget.setEnabled(not busy)
        self.enqueue.setEnabled(not busy and any(item.cached for item in self.items))

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, 'Specimen folder', self.folder.text(),
                                                filter=SURFACE_FILE_FILTER)
        if path:
            self.folder.setText(path)

    def pick_faces(self, path, cells=None):
        from geometry_import_dialog import GeometryImportDialog
        digest = source_digest(path)
        surface = read_surface(path)
        dialog = GeometryImportDialog(surface, Path(path).name, self)
        dialog.setStyleSheet(STUDIO_STYLESHEET)
        try:
            if cells:
                for role, cell in enumerate(cells):
                    dialog.set_role(role)
                    dialog.select_cell(cell)
            if not dialog.exec():
                return None
            aligned, height = dialog.result_surface
            direction = compression_direction(surface, *dialog.selected)
            if source_digest(path) != digest:
                raise ValueError('Source changed during face selection. Check the folder again.')
            return PreparedSpecimen(str(path), digest, store_surface(aligned), height,
                                    tuple(float(x) for x in direction), tuple(dialog.selected))
        finally:
            dialog.plotter.close()
            dialog.deleteLater()

    def check_folder(self):
        if self.future is not None:
            return
        try:
            if not self.folder.text().strip():
                raise ValueError('Choose a specimen folder.')
            root = Path(self.folder.text()).expanduser().resolve()
            paths = discover_specimens(root)
            if not paths:
                raise ValueError('No STL, OBJ, PLY or VTP specimens found. Result folders are excluded.')
            base = settings_from_dict(asdict(self.settings_provider()))
            direction = {0: (0, 0, 1), 1: (1, 0, 0), 2: (0, 1, 0)}.get(self.mode.currentIndex())
            first = None
            if self.mode.currentIndex() == 3:
                first = self.pick_faces(paths[0])
                if first is None:
                    return
                direction = first.direction
            self.items = [PreparedSpecimen(str(path), error='Face selection required') for path in paths]
            if first:
                self.items[0] = first
            self.root, self.base = root, base
            self.pending = [(i, path, direction) for i, path in enumerate(paths)
                            if direction is not None and not (first and i == 0)]
            self.settings_hint.setText(f'Captured: {base.material_name} | mesh {base.mesh_resolution:g} mm / local {base.local_mesh_size:g} mm | '
                                       f'{base.loading_mode}: {base.compression:g} mm / {base.applied_force:g} N | {base.solver_backend}. '
                                       'Original millimetre dimensions are preserved. HEX elements become TET4 for imported solids.')
            self.refresh_table()
            self.set_busy(bool(self.pending))
            self.launch_next()
        except Exception as error:
            self.status.setText(str(error))

    def launch_next(self):
        if self.pending:
            index, path, direction = self.pending.pop(0)
            self.working_index = index
            self.status.setText(f'Checking {index + 1}/{len(self.items)}: {Path(path).name}')
            self.future = self.executor.submit(prepare_specimen, path, direction)
        else:
            self.set_busy(False)
            ready = sum(bool(item.cached) for item in self.items)
            self.status.setText(f'{ready}/{len(self.items)} ready. Inspect or select faces for the remaining specimens, or leave them unchecked. '
                                'Add ready specimens to the queue, then use Run analysis queue to start.')

    def poll(self):
        if self.future is None or not self.future.done():
            return
        future, self.future = self.future, None
        try:
            self.items[self.working_index] = future.result()
        except Exception as error:
            self.items[self.working_index].error = str(error)
        self.refresh_table()
        self.launch_next()

    def refresh_table(self):
        checked = {self.table.item(i, 1).text(): self.table.item(i, 0).checkState()
                   for i in range(self.table.rowCount()) if self.table.item(i, 1)
                   and self.table.item(i, 2).text().startswith('Ready')}
        self.table.setRowCount(len(self.items))
        for index, specimen in enumerate(self.items):
            name = Path(specimen.source).relative_to(self.root).as_posix()
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(checked.get(name, Qt.CheckState.Checked) if specimen.cached else Qt.CheckState.Unchecked)
            self.table.setItem(index, 0, check)
            for column, value in ((1, name), (2, specimen.error or f'Ready | height {specimen.height:g} mm')):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(index, column, item)
        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(1)

    def edit_selected(self):
        index = self.table.currentRow()
        if index < 0 or self.future is not None:
            return
        try:
            item = self.items[index]
            prepared = self.pick_faces(item.source, item.cells)
            if prepared:
                self.items[index] = prepared
                self.refresh_table()
                self.table.item(index, 0).setCheckState(Qt.CheckState.Checked)
                self.set_busy(False)
        except Exception as error:
            self.status.setText(str(error))

    def enqueue_ready(self):
        if self.future is not None or self.base is None:
            return
        try:
            solvers = {0: [self.base.solver_backend], 1: ['ansys_mapdl'],
                       2: ['kratos'], 3: ['ansys_mapdl', 'kratos']}[self.solver.currentIndex()]
            controller = getattr(self.queue, 'remote_controller', None)
            if controller is not None and controller.requested and solvers != [self.base.solver_backend]:
                raise ValueError('TRUBA uses the current workspace solver. Choose Current workspace or switch to local execution.')
            snapshots = []
            for index, item in enumerate(self.items):
                if self.table.item(index, 0).checkState() == Qt.CheckState.Checked:
                    snapshots.extend(specimen_snapshots(item, self.root, self.base, solvers))
            if not snapshots:
                raise ValueError('Check at least one ready specimen.')
            added = self.queue.add_folder_snapshots(snapshots)
            self.status.setText(f'{len(added)} jobs added. Identical jobs already in the queue were skipped. '
                                f'Results: {self.root / "Result"}. Open the queue and explicitly start it.')
        except Exception as error:
            QMessageBox.warning(self, 'Folder analysis', str(error))

    def open_queue(self):
        window = self.queue.window()
        if hasattr(window, 'open_workspace'):
            window.open_workspace()

    def closeEvent(self, event):
        self.pending.clear()
        if self.future is not None:
            self.future.cancel()
            self.future = None
        self.set_busy(False)
        super().closeEvent(event)

    def dispose(self):
        if not self.disposed:
            self.disposed = True
            self.timer.stop()
            self.pending.clear()
            self.executor.shutdown(wait=False, cancel_futures=True)
