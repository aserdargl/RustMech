"""Folder preflight and explicit preparation of sequential mechanical jobs."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from html import escape
import json
from math import isfinite
import os
from pathlib import Path
import re

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QDialog, QGridLayout, QGroupBox, QHeaderView,
                              QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                              QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout)

from camera_toolbar import camera_icon
from file_dialog import QFileDialog
from folder_batch_preview import FacePreviewButton, FacePreviewPopup, store_face_preview
from folder_batch_results import BATCH_DIRECTORY, LEGACY_BATCH_DIRECTORY, atomic_text, batch_directory
from imported_geometry import (SURFACE_EXTENSIONS, SURFACE_FILE_FILTER, SurfaceComponents,
                               align_compression_surface, automatic_compression_selection,
                               compression_direction, read_polygonal_surface, store_surface, surface_cache_directory)
from model import MIN_MESH_INPUT_MM, settings_from_dict
from studio_style import (ActionFlowLayout, CompactComboBox, CompactDoubleSpinBox, CONTROL_HEIGHT,
                          STUDIO_STYLESHEET, field_label)
from ui_language import translate
from window_state import install_window_state


FACES_COLUMN, RUN_COLUMN, SPECIMEN_COLUMN, PREFLIGHT_COLUMN = range(4)


@dataclass
class PreparedSpecimen:
    source: str
    digest: str = ''
    cached: str = ''
    height: float = 0
    direction: tuple = (0., 0., 1.)
    cells: tuple | None = None
    error: str = ''
    component: int | None = None
    excluded_components: int = 0
    normalized_direction: tuple | None = None
    preview: str = ''
    preview_error: str = ''


def discover_specimens(directory):
    """Natural filename order; never consume prior results or directory links."""
    root = Path(directory).resolve()
    if not root.is_dir():
        raise ValueError('Choose an existing specimen folder.')
    paths = []
    for parent, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories
                                if name.casefold() not in ('result', BATCH_DIRECTORY.casefold(), LEGACY_BATCH_DIRECTORY.casefold())
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


def face_assignment_key(path):
    identity = sha256(os.fsencode(os.path.normcase(path))).hexdigest()
    return f'folderBatch/faceAssignments/{identity}'


def specimen_name(path, root):
    relative = Path(path).relative_to(root)
    name = re.sub(r'[^\w.-]+', '_', relative.stem)[:70] or 'specimen'
    return name + '-' + sha256(relative.as_posix().encode()).hexdigest()[:8]


def face_assignment_file(path, root):
    return batch_directory(root) / 'Prepared' / f'{specimen_name(path, root)}.json'


def prepare_preview(item, root, surface, aligned):
    try:
        item.preview = store_face_preview(batch_directory(root) / 'Images', surface, aligned, item.direction)
    except (OSError, RuntimeError, ValueError) as error:
        item.preview_error = str(error)


def prepare_specimen(path, direction, saved_faces=None, root=None):
    item = PreparedSpecimen(str(path))
    root = Path(path).parent if root is None else root
    try:
        item.digest = source_digest(path)
        components = SurfaceComponents(read_polygonal_surface(path))
        selection = saved_faces if saved_faces and saved_faces[0] == item.digest else None
        component = selected_component(components, selection)
        if component is None:
            raise ValueError('Multiple substantial components. Choose which solid to keep in Edit.')
        if components.count > 1:
            item.component = component
            item.excluded_components = components.count - 1
        surface = components.solid(component)
        item.cells = matching_cells(selection, components, component)
        if item.cells:
            if len(selection) == 4:
                surface, _ = automatic_compression_selection(surface, selection[3])
                item.normalized_direction = selection[3]
            aligned, item.height = align_compression_surface(surface, *item.cells)
            direction = compression_direction(surface, *item.cells)
        elif direction is None:
            raise ValueError('Face selection required')
        else:
            original = surface
            surface, item.cells = automatic_compression_selection(surface, direction)
            if surface is not original:
                item.normalized_direction = tuple(float(value) for value in direction)
            aligned, item.height = align_compression_surface(surface, *item.cells)
        item.direction = tuple(float(x) for x in direction)
        item.cached = store_surface(aligned, batch_directory(root) / 'Prepared')
        if source_digest(path) != item.digest:
            raise ValueError('Source changed during preparation. Check the folder again.')
    except Exception as error:
        item.error = str(error)
        item.cached = ''
    else:
        prepare_preview(item, root, surface, aligned)
    return item


def selected_component(components, selection):
    if selection and len(selection) >= 3 and selection[2] is not None:
        index = selection[2]
        if 0 <= index < components.count:
            return index
    return components.automatic_choice()


def matching_cells(selection, components, component):
    if selection and (components.count == 1 or len(selection) >= 3 and selection[2] == component):
        return selection[1]
    return None


def specimen_snapshots(item, root, base, solvers, *, execution_target=None):
    if item.error or not item.cached:
        raise ValueError('Choose valid support and compression faces first.')
    if source_digest(item.source) != item.digest:
        raise ValueError('Source changed after preparation. Check the folder again.')
    yield from _prepared_snapshots(item, root, base, solvers, execution_target=execution_target)


def _prepared_snapshots(item, root, base, solvers, *, execution_target=None):
    relative = Path(item.source).relative_to(root)
    # Keep the original units and loading magnitude. No automatic strain scaling.
    settings = replace(base, imported_geometry_path=item.cached,
                       imported_geometry_name=relative.as_posix(), imported_geometry_role='compression',
                       size=item.height, test_profile='custom',
                       mesh_type=base.mesh_type if base.mesh_type in ('TET4', 'TET10') else 'TET4',
                       tetra_strategy='netgen' if base.tetra_strategy == 'periodic' else base.tetra_strategy)
    name = specimen_name(item.source, root)
    results = batch_directory(root) / 'Results'
    for solver in solvers:
        snapshot = replace(settings, solver_backend=solver)
        identity = asdict(snapshot)
        # Keep deduplication compatible with jobs queued before the workspace moved.
        identity['imported_geometry_path'] = str(surface_cache_directory() / Path(item.cached).name)
        if execution_target is not None:
            identity['execution_target'] = execution_target
        key = sha256(json.dumps([str(root), relative.as_posix(), item.digest, item.direction, identity],
                                sort_keys=True).encode()).hexdigest()
        metadata = dict(root=str(root), source=str(item.source), sha256=item.digest,
                        direction=list(item.direction), key=key,
                        results_root=str(results), destination=str(results / name / 'Mechanical' / solver))
        if execution_target is not None:
            metadata['execution_target'] = execution_target
        if item.component is not None:
            metadata.update(kept_component=item.component + 1, excluded_components=item.excluded_components)
        if item.normalized_direction is not None:
            metadata['normalized_end_direction'] = list(item.normalized_direction)
        yield snapshot, metadata


class FolderBatchDialog(QDialog):
    def __init__(self, settings_provider, queue, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        app = QApplication.instance()
        install_window_state(app)
        self.preferences = app._rustmech_window_state.settings
        self.setObjectName('folderBatchWindow')
        self.setWindowTitle('Batch analysis from folder - RustMech')
        self.setStyleSheet(STUDIO_STYLESHEET)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinimizeButtonHint | Qt.WindowType.WindowMaximizeButtonHint)
        self.resize(920, 600)
        self.setMinimumSize(620, 480)
        self.setSizeGripEnabled(True)
        self.settings_provider, self.queue = settings_provider, queue
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='folder-preflight')
        self.future = None
        self.items = []
        self.pending = []
        self.root = None
        self.base = None
        self.compression_axis = None
        self.disposed = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(7)
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
        self.check = self.button('Check Folder', self.check_folder, primary=True)
        row.addWidget(self.check)
        layout.addLayout(row)

        controls = QHBoxLayout()
        controls.setSpacing(5)
        self.mode = CompactComboBox()
        self.mode.addItems(['Z: bottom / top', 'X: min / max', 'Y: min / max',
                            'First specimen', 'Manual faces'])
        self.mode.setFixedWidth(108)
        direction_label = QLabel('Compression direction')
        direction_label.setBuddy(self.mode)
        controls.addWidget(direction_label)
        controls.addWidget(self.mode)
        self.solver = CompactComboBox()
        self.solver.addItems(['Current workspace', 'ANSYS MAPDL', 'Kratos', 'ANSYS + Kratos'])
        self.solver.setFixedWidth(124)
        solver_label = QLabel('Solver')
        solver_label.setBuddy(self.solver)
        controls.addWidget(solver_label)
        controls.addWidget(self.solver)
        self.execution_target = CompactComboBox()
        self.execution_target.addItem('TRUBA', 'truba')
        self.execution_target.addItem(translate('Local mesh'), 'local')
        self.execution_target.setFixedWidth(110)
        execution_label = QLabel('Execution')
        execution_label.setBuddy(self.execution_target)
        controls.addWidget(execution_label)
        controls.addWidget(self.execution_target)
        controls.addStretch()
        layout.addLayout(controls)
        mesh = QGroupBox('Mesh')
        mesh_layout = QGridLayout(mesh)
        mesh_layout.setContentsMargins(6, 6, 6, 6)
        mesh_layout.setVerticalSpacing(3)
        initial = self.settings_provider()
        self.mesh_resolution = self.add_mesh_size(mesh_layout, 0, 'Global mesh size',
                                                  initial.mesh_resolution, 0.05)
        self.local_mesh_size = self.add_mesh_size(mesh_layout, 1, 'Local mesh size',
                                                  initial.local_mesh_size, 0.001)
        self.mesh_type = CompactComboBox()
        self.mesh_type.addItems(['TET4', 'TET10'])
        self.mesh_type.setCurrentText('TET10' if initial.mesh_type == 'TET10' else 'TET4')
        mesh_layout.addWidget(field_label('Element type', self.mesh_type), 0, 2)
        self.mesh_type.setFixedWidth(100)
        mesh_layout.addWidget(self.mesh_type, 0, 3)
        mesh_layout.setColumnStretch(4, 1)
        layout.addWidget(mesh)
        self.settings_hint = QLabel()
        self.settings_hint.setWordWrap(True)
        layout.addWidget(self.settings_hint)
        panels = QSplitter(Qt.Orientation.Horizontal)
        panels.setObjectName('folderBatchPanels')
        panels.setChildrenCollapsible(False)
        panels.setHandleWidth(7)
        layout.addWidget(panels, 1)
        specimens = QGroupBox('Specimens')
        specimen_layout = QVBoxLayout(specimens)
        specimen_layout.setContentsMargins(6, 6, 6, 6)
        panels.addWidget(specimens)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['', 'Run', 'Specimen', 'Preflight'])
        self.face_icon = camera_icon('orthographic')
        self.face_preview = FacePreviewPopup(self)
        edit_header = self.table.horizontalHeaderItem(FACES_COLUMN)
        edit_header.setIcon(QIcon(str(Path(__file__).resolve().parent / 'assets' / 'ui' / 'edit-pencil.svg')))
        edit_header.setToolTip('Edit specimen geometry and faces')
        edit_header.setData(Qt.ItemDataRole.AccessibleTextRole, 'Edit')
        self.table.horizontalHeader().setSectionResizeMode(FACES_COLUMN, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(FACES_COLUMN, CONTROL_HEIGHT + 5)
        self.table.verticalHeader().setMinimumSectionSize(CONTROL_HEIGHT + 2)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        specimen_layout.addWidget(self.table)

        queued = QGroupBox('Analysis Queue')
        queue_layout = QVBoxLayout(queued)
        queue_layout.setContentsMargins(6, 6, 6, 6)
        self.queue_table = QTableWidget(0, 3)
        self.queue_table.setHorizontalHeaderLabels(['Specimen', 'Solver', 'Execution target'])
        self.queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.queue_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.queue_table.verticalHeader().hide()
        queue_header = self.queue_table.horizontalHeader()
        queue_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        queue_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        queue_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        queue_layout.addWidget(self.queue_table)
        self.remove_from_queue = self.button('Remove from Queue', self.return_queued_specimen)
        self.remove_from_queue.setEnabled(False)
        self.queue_table.itemSelectionChanged.connect(self.update_remove_button)
        queue_layout.addWidget(self.remove_from_queue, 0, Qt.AlignmentFlag.AlignLeft)
        panels.addWidget(queued)
        panels.setSizes([560, 320])

        actions = ActionFlowLayout(equal_width=True)
        actions.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.enqueue = self.button('Add to Queue', self.enqueue_ready)
        self.show_queue = self.button('Analysis Queue', self.open_queue)
        close = self.button('Close', self.close)
        for button in (self.enqueue, self.show_queue, close):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.status = QLabel('Choose a folder and check it. No analysis starts during preparation.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self._restore_preferences()
        self.folder.textChanged.connect(self.invalidate)
        self.mode.currentIndexChanged.connect(self.invalidate)
        self.solver.currentIndexChanged.connect(self.refresh_queue_view)
        self.mesh_resolution.valueChanged.connect(self.refresh_queue_view)
        self.local_mesh_size.valueChanged.connect(self.refresh_queue_view)
        self.mesh_type.currentIndexChanged.connect(self.refresh_queue_view)
        self.queue.queue_changed.connect(self.refresh_queue_view)
        self.update_execution_target()
        self.execution_target.currentIndexChanged.connect(self.update_execution_target)
        self.finished.connect(self._save_preferences)

    def _restore_preferences(self):
        self.folder.setText(self.preferences.value("folderBatch/folder", "", type=str))
        for name in ("mode", "solver", "mesh_type", "execution_target"):
            combo = getattr(self, name)
            index = self.preferences.value(f"folderBatch/{name}", combo.currentIndex(), type=int)
            if 0 <= index < combo.count():
                combo.setCurrentIndex(index)
        self._local_solver_index = self.solver.currentIndex()
        self._local_mesh_index = self.mesh_type.currentIndex()
        for name in ("mesh_resolution", "local_mesh_size"):
            control = getattr(self, name)
            value = self.preferences.value(f"folderBatch/{name}", control.value(), type=float)
            if isfinite(value) and value >= MIN_MESH_INPUT_MM:
                control.setMaximum(max(control.maximum(), value))
                control.setValue(value)

    def _save_preferences(self):
        remote = self.execution_target.currentData() == "truba"
        values = {
            "folder": self.folder.text(),
            "mode": self.mode.currentIndex(),
            "solver": self._local_solver_index if remote else self.solver.currentIndex(),
            "mesh_type": self._local_mesh_index if remote else self.mesh_type.currentIndex(),
            "execution_target": self.execution_target.currentIndex(),
            "mesh_resolution": self.mesh_resolution.value(),
            "local_mesh_size": self.local_mesh_size.value(),
        }
        for name, value in values.items():
            self.preferences.setValue(f"folderBatch/{name}", value)
        self.preferences.sync()

    def add_mesh_size(self, layout, row, label, value, step):
        control = CompactDoubleSpinBox()
        control.setRange(min(MIN_MESH_INPUT_MM, value), max(5, value))
        control.setSingleStep(step)
        control.setSuffix(' mm')
        control.setKeyboardTracking(False)
        control.setValue(value)
        layout.addWidget(field_label(label, control), row, 0)
        layout.addWidget(control, row, 1)
        return control

    def update_execution_target(self):
        remote = self.execution_target.currentData() == 'truba'
        with QSignalBlocker(self.solver), QSignalBlocker(self.mesh_type):
            if remote:
                self._local_solver_index = self.solver.currentIndex()
                self._local_mesh_index = self.mesh_type.currentIndex()
                self.solver.setCurrentIndex(2)
                self.mesh_type.setCurrentText('TET4')
            else:
                self.solver.setCurrentIndex(self._local_solver_index)
                self.mesh_type.setCurrentIndex(self._local_mesh_index)
        self.enqueue.setText(translate('Add Truba' if remote else 'Add to Queue'))
        self.update_settings_hint()
        self.set_busy(self.future is not None or bool(self.pending))

    def update_settings_hint(self):
        remote = self.execution_target.currentData() == 'truba'
        text = translate(
            'TRUBA uses Kratos and TET4 with self-contact disabled. Material, loading and platen contact come from the Mechanic workspace.'
            if remote else 'The mesh is generated locally. Kratos solves use TRUBA. Analysis settings come from the Mechanic workspace.')
        if self.base is not None:
            base = self.base
            text += (f'\n{base.material_name} | '
                     f'{base.loading_mode}: {base.compression:g} mm / {base.applied_force:g} N')
        self.settings_hint.setText(text)

    def button(self, text, callback, *, primary=False):
        button = QPushButton(text, self)
        if primary:
            button.setProperty('primary', True)
        button.setStyleSheet('QPushButton { padding: 0 8px; } QPushButton:focus { padding: 0 7px; }')
        button.setAutoDefault(False)
        button.setDefault(False)
        button.clicked.connect(callback)
        return button

    def invalidate(self, *_):
        self.items.clear()
        self.table.setRowCount(0)
        self.root = None
        self.base = None
        self.compression_axis = None
        self.update_settings_hint()
        self.refresh_queue_view()

    def set_busy(self, busy):
        for widget in (self.folder, self.browse, self.mode, self.execution_target, self.check,
                       self.mesh_resolution, self.local_mesh_size):
            widget.setEnabled(not busy)
        local = self.execution_target.currentData() == 'local'
        self.solver.setEnabled(local and not busy)
        self.mesh_type.setEnabled(local and not busy)
        for row in range(self.table.rowCount()):
            self.table.cellWidget(row, FACES_COLUMN).setEnabled(not busy)
        self.refresh_queue_view(busy=busy)

    def selected_solvers(self):
        return {0: [self.base.solver_backend], 1: ['ansys_mapdl'], 2: ['kratos'],
                3: ['ansys_mapdl', 'kratos']}[self.solver.currentIndex()]

    def batch_settings(self):
        settings = replace(self.base, mesh_resolution=self.mesh_resolution.value(),
                           local_mesh_size=self.local_mesh_size.value(), mesh_type=self.mesh_type.currentText())
        if self.execution_target.currentData() == 'truba':
            settings = replace(settings, self_contact_mode='disabled')
        return settings

    def refresh_queue_view(self, *, busy=None):
        root = str(self.root) if self.root is not None else None
        rows = [row for row in self.queue.rows if row.folder_job and row.folder_job['root'] == root]
        self.queue_table.clearSelection()
        self.queue_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            settings = row.design_settings
            target = row.folder_job.get('execution_target')
            target_label = translate({'truba': 'TRUBA', 'local': 'Local mesh'}.get(target, 'Current workspace'))
            for column, value in enumerate((settings.imported_geometry_name, settings.solver_backend, target_label)):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                item.setData(Qt.ItemDataRole.UserRole, row.exp_id)
                self.queue_table.setItem(index, column, item)

        keys = {row.folder_job['key'] for row in rows}
        solvers = self.selected_solvers() if keys else ()
        base = self.batch_settings() if keys else None
        target = self.execution_target.currentData()
        ready = False
        for index, specimen in enumerate(self.items):
            queued = bool(specimen.cached and keys) and all(
                metadata['key'] in keys for _, metadata in _prepared_snapshots(
                    specimen, self.root, base, solvers, execution_target=target))
            self.table.setRowHidden(index, queued)
            ready |= bool(specimen.cached and not queued)
        if busy is None:
            busy = self.future is not None or bool(self.pending)
        self.enqueue.setEnabled(ready and not busy)
        self.update_remove_button()

    def update_remove_button(self):
        idle = self.future is None and not self.pending
        self.remove_from_queue.setEnabled(idle and bool(self.queue_table.selectionModel().hasSelection()))

    def return_queued_specimen(self):
        selected = self.queue_table.selectionModel().selectedRows()
        if not selected or self.future is not None or self.pending:
            return
        item = self.queue_table.item(selected[0].row(), 0)
        try:
            removed = self.queue.remove_jobs({item.data(Qt.ItemDataRole.UserRole)})
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
            return
        if not removed:
            return
        for index, specimen in enumerate(self.items):
            if specimen.source == removed[0].folder_job['source']:
                self.table.setRowHidden(index, False)
                self.table.selectRow(index)
                self.table.scrollToItem(self.table.item(index, SPECIMEN_COLUMN))
                break
        self.status.setText('Selected job removed from the queue.')

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, 'Specimen folder', self.folder.text().strip(),
                                                filter=f'All files (*);;{SURFACE_FILE_FILTER}')
        if path:
            self.folder.setText(path)

    def saved_faces(self, path, root=None):
        root = root or self.root or Path(path).parent
        try:
            value = face_assignment_file(path, root).read_text(encoding='utf-8')
        except FileNotFoundError:
            value = self.preferences.value(face_assignment_key(path), '')
        try:
            digest, cells, *geometry = json.loads(value)
            if not (isinstance(digest, str) and isinstance(cells, list) and len(cells) == 2
                    and all(type(cell) is int and cell >= 0 for cell in cells)):
                return None
            if not geometry:
                return digest, tuple(cells)
            component, *normalization = geometry
            if component is not None and (type(component) is not int or component < 0):
                return None
            if not normalization:
                return digest, tuple(cells), component
            axis, = normalization
            if (isinstance(axis, list) and len(axis) == 3
                    and all(type(x) in (int, float) and isfinite(x) for x in axis) and any(axis)):
                return digest, tuple(cells), component, tuple(axis)
        except (OverflowError, TypeError, ValueError):
            pass
        return None

    def pick_faces(self, path, selection=None, direction=None, *, root=None):
        from geometry_import_dialog import GeometryImportDialog
        root = root or self.root or Path(path).parent
        digest = source_digest(path)
        components = SurfaceComponents(read_polygonal_surface(path))
        selection = selection if selection and selection[0] == digest else None
        component = selected_component(components, selection)
        surface = components.surface
        geometry_error = ''
        if component is None:
            geometry_error = 'Choose which connected component to keep.'
        else:
            try:
                surface = components.solid(component)
            except ValueError as error:
                surface = components.extract(component)
                geometry_error = str(error)
        prepared = None

        def save_selection(aligned, height, cells):
            nonlocal prepared
            axis = compression_direction(dialog.surface, *cells)
            cached = store_surface(aligned, batch_directory(root) / 'Prepared')
            if source_digest(path) != digest:
                raise ValueError('Source changed during face selection. Check the folder again.')
            kept = dialog.component_index if components.count > 1 else None
            geometry = [kept] if kept is not None or dialog.normalized_direction is not None else []
            if dialog.normalized_direction is not None:
                geometry.append(dialog.normalized_direction)
            atomic_text(face_assignment_file(path, root), json.dumps([digest, cells, *geometry]))
            prepared = PreparedSpecimen(str(path), digest, cached, height, tuple(float(x) for x in axis), cells,
                                        component=kept, excluded_components=components.count - 1,
                                        normalized_direction=dialog.normalized_direction)

        cells = matching_cells(selection, components, component)
        initial_cells = cells if cells and all(0 <= cell < surface.n_cells for cell in cells) else ()
        dialog = GeometryImportDialog(surface, Path(path).name, self, save_selection=save_selection,
                                      initial_cells=initial_cells, geometry_error=geometry_error,
                                      components=components if components.count > 1 else None,
                                      component_index=component, direction=direction,
                                      normalized_direction=selection[3] if initial_cells and len(selection) == 4 else None)
        dialog.setStyleSheet(STUDIO_STYLESHEET)
        try:
            if not dialog.exec() or prepared is None:
                return None
            prepare_preview(prepared, root, dialog.surface, dialog.result_surface[0])
            return prepared
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
            self.queue.migrate_batch_workspace(root)
            paths = discover_specimens(root)
            if not paths:
                raise ValueError('No STL, OBJ, PLY or VTP specimens found.')
            workspace = batch_directory(root)
            for name in ('Prepared', 'Images', 'Results'):
                (workspace / name).mkdir(parents=True, exist_ok=True)
            base = settings_from_dict(asdict(self.settings_provider()))
            direction = {0: (0, 0, 1), 1: (1, 0, 0), 2: (0, 1, 0)}.get(self.mode.currentIndex())
            saved = [self.saved_faces(path, root) for path in paths]
            first = None
            if self.mode.currentIndex() == 3:
                first = self.pick_faces(paths[0], saved[0], root=root)
                if first is None:
                    return
                direction = first.direction
            self.items = [PreparedSpecimen(str(path), error='Face selection required') for path in paths]
            if first:
                self.items[0] = first
            self.root, self.base = root, base
            self.compression_axis = direction
            self.pending = [(i, path, direction, selection) for i, (path, selection) in enumerate(zip(paths, saved))
                            if (direction is not None or selection) and not (first and i == 0)]
            self.update_settings_hint()
            self.refresh_table()
            self.set_busy(bool(self.pending))
            self.launch_next()
        except Exception as error:
            self.status.setText(str(error))

    def launch_next(self):
        if self.pending:
            index, path, direction, selection = self.pending.pop(0)
            self.working_index = index
            self.status.setText(f'Checking {index + 1}/{len(self.items)}: {Path(path).name}')
            self.future = self.executor.submit(prepare_specimen, path, direction, selection, self.root)
        else:
            self.set_busy(False)
            ready = sum(bool(item.cached) for item in self.items)
            self.status.setText(f'{ready}/{len(self.items)} ready. Inspect or select faces for the remaining specimens, or leave them unchecked. '
                                'Use Add to Queue, then open Analysis Queue to start the jobs.')

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
        checked = {self.table.item(i, SPECIMEN_COLUMN).text(): self.table.item(i, RUN_COLUMN).checkState()
                   for i in range(self.table.rowCount()) if self.table.item(i, RUN_COLUMN).data(Qt.ItemDataRole.UserRole)}
        self.table.setRowCount(len(self.items))
        for index, specimen in enumerate(self.items):
            name = Path(specimen.source).relative_to(self.root).as_posix()
            button = self.table.cellWidget(index, FACES_COLUMN)
            if button is None:
                button = FacePreviewButton(self.face_preview)
                button.setIcon(self.face_icon)
                button.setIconSize(QSize(16, 16))
                button.setFixedSize(CONTROL_HEIGHT, CONTROL_HEIGHT)
                button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                button.setAccessibleName('Edit specimen geometry and faces')
                button.clicked.connect(lambda checked=False, row=index: self.edit_specimen(row))
                self.table.setCellWidget(index, FACES_COLUMN, button)
            button.setAccessibleDescription(name)
            button.preview_path = specimen.preview
            tip = 'Edit specimen geometry and faces'
            if specimen.preview_error:
                tip = f'<html>Face preview unavailable<br>{escape(specimen.preview_error)}</html>'
            button.setToolTip(tip)
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(checked.get(name, Qt.CheckState.Checked) if specimen.cached else Qt.CheckState.Unchecked)
            check.setData(Qt.ItemDataRole.UserRole, bool(specimen.cached))
            self.table.setItem(index, RUN_COLUMN, check)
            preflight = specimen.error or f'Ready | height {specimen.height:g} mm'
            if specimen.cached and specimen.excluded_components:
                preflight += f' | Excluded components: {specimen.excluded_components}'
            if specimen.cached and specimen.normalized_direction is not None:
                preflight += ' | End faces flattened'
            for column, value in ((SPECIMEN_COLUMN, name), (PREFLIGHT_COLUMN, preflight)):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(index, column, item)
        self.table.resizeColumnToContents(RUN_COLUMN)
        self.table.resizeColumnToContents(SPECIMEN_COLUMN)

    def edit_specimen(self, index):
        if self.future is not None:
            return
        try:
            item = self.items[index]
            selection = (item.digest, item.cells)
            if item.component is not None or item.normalized_direction is not None:
                selection += (item.component,)
            if item.normalized_direction is not None:
                selection += (item.normalized_direction,)
            prepared = self.pick_faces(item.source, selection, self.compression_axis)
            if prepared:
                self.items[index] = prepared
                self.refresh_table()
                self.set_busy(False)
                self.status.setText('Specimen faces saved.')
        except Exception as error:
            self.status.setText(str(error))

    def enqueue_ready(self):
        if self.future is not None or self.base is None:
            return
        try:
            solvers = self.selected_solvers()
            base = self.batch_settings()
            target = self.execution_target.currentData()
            snapshots = []
            for index, item in enumerate(self.items):
                if (not self.table.isRowHidden(index)
                        and self.table.item(index, RUN_COLUMN).checkState() == Qt.CheckState.Checked):
                    snapshots.extend(specimen_snapshots(item, self.root, base, solvers, execution_target=target))
            if not snapshots:
                raise ValueError('Check at least one ready specimen.')
            added = self.queue.add_folder_snapshots(snapshots)
            self.status.setText(f'{len(added)} jobs added. Identical jobs already in the queue were skipped. '
                                f'Results: {batch_directory(self.root) / "Results"}. Open the queue and explicitly start it.')
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
            self._save_preferences()
            self.disposed = True
            self.timer.stop()
            self.pending.clear()
            self.executor.shutdown(wait=False, cancel_futures=True)
