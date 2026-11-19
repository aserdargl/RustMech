"""Folder preflight and explicit preparation of sequential mechanical jobs."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
from math import isfinite
import os
from pathlib import Path
import re

from PySide6.QtCore import QSize, QSettings, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QDialog, QGridLayout, QGroupBox, QHeaderView,
                              QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                              QSplitter, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout)

from camera_toolbar import camera_icon
from file_dialog import QFileDialog
from imported_geometry import (SURFACE_EXTENSIONS, SURFACE_FILE_FILTER, SurfaceComponents,
                               align_compression_surface, automatic_compression_selection,
                               compression_direction, read_polygonal_surface, store_surface)
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


def face_assignment_key(path):
    identity = sha256(os.fsencode(os.path.normcase(path))).hexdigest()
    return f'folderBatch/faceAssignments/{identity}'


def prepare_specimen(path, direction, saved_faces=None):
    item = PreparedSpecimen(str(path))
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
            face_surface, item.cells = automatic_compression_selection(surface, direction)
            if face_surface is not surface:
                item.normalized_direction = tuple(float(value) for value in direction)
            aligned, item.height = align_compression_surface(face_surface, *item.cells)
        item.direction = tuple(float(x) for x in direction)
        item.cached = store_surface(aligned)
        if source_digest(path) != item.digest:
            raise ValueError('Source changed during preparation. Check the folder again.')
    except Exception as error:
        item.error = str(error)
        item.cached = ''
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


def specimen_snapshots(item, root, base, solvers):
    if item.error or not item.cached:
        raise ValueError('Choose valid support and compression faces first.')
    if source_digest(item.source) != item.digest:
        raise ValueError('Source changed after preparation. Check the folder again.')
    yield from _prepared_snapshots(item, root, base, solvers)


def _prepared_snapshots(item, root, base, solvers):
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
        self.check = self.button('Check Folder', self.check_folder, primary=True)
        layout.addWidget(self.check, 0, Qt.AlignmentFlag.AlignLeft)
        self.settings_hint = QLabel('Material, loading, contact and solver parameters are captured from the main Mechanic workspace when checking.')
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
        self.queue_table = QTableWidget(0, 2)
        self.queue_table.setHorizontalHeaderLabels(['Specimen', 'Solver'])
        self.queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.queue_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.queue_table.verticalHeader().hide()
        queue_header = self.queue_table.horizontalHeader()
        queue_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        queue_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
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
        self.folder.textChanged.connect(self.invalidate)
        self.mode.currentIndexChanged.connect(self.invalidate)
        self.solver.currentIndexChanged.connect(self.refresh_queue_view)
        self.mesh_resolution.valueChanged.connect(self.refresh_queue_view)
        self.local_mesh_size.valueChanged.connect(self.refresh_queue_view)
        self.mesh_type.currentIndexChanged.connect(self.refresh_queue_view)
        self.queue.queue_changed.connect(self.refresh_queue_view)
        controller = getattr(self.queue, 'remote_controller', None)
        if controller is not None:
            controller.mode_changed.connect(self.update_enqueue_label)
        self.update_enqueue_label(controller is not None and controller.requested)
        self.set_busy(False)

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

    def update_enqueue_label(self, remote):
        self.enqueue.setText(translate('Add Truba' if remote else 'Add to Queue'))
        self.enqueue.setToolTip(translate('Add to the analysis queue. Start it with TRUBA mode active to submit jobs.')
                                if remote else '')

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
        self.refresh_queue_view()

    def set_busy(self, busy):
        for widget in (self.folder, self.browse, self.mode, self.solver, self.check,
                       self.mesh_resolution, self.local_mesh_size, self.mesh_type):
            widget.setEnabled(not busy)
        for row in range(self.table.rowCount()):
            self.table.cellWidget(row, FACES_COLUMN).setEnabled(not busy)
        self.refresh_queue_view(busy=busy)

    def selected_solvers(self):
        return {0: [self.base.solver_backend], 1: ['ansys_mapdl'], 2: ['kratos'],
                3: ['ansys_mapdl', 'kratos']}[self.solver.currentIndex()]

    def batch_settings(self):
        return replace(self.base, mesh_resolution=self.mesh_resolution.value(),
                       local_mesh_size=self.local_mesh_size.value(), mesh_type=self.mesh_type.currentText())

    def refresh_queue_view(self, *, busy=None):
        root = str(self.root) if self.root is not None else None
        rows = [row for row in self.queue.rows if row.folder_job and row.folder_job['root'] == root]
        self.queue_table.clearSelection()
        self.queue_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            settings = row.design_settings
            for column, value in enumerate((settings.imported_geometry_name, settings.solver_backend)):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                item.setData(Qt.ItemDataRole.UserRole, row.exp_id)
                self.queue_table.setItem(index, column, item)

        keys = {row.folder_job['key'] for row in rows}
        solvers = self.selected_solvers() if keys else ()
        base = self.batch_settings() if keys else None
        ready = False
        for index, specimen in enumerate(self.items):
            queued = bool(specimen.cached and keys) and all(
                metadata['key'] in keys for _, metadata in _prepared_snapshots(specimen, self.root, base, solvers))
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

    def saved_faces(self, path):
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

    def pick_faces(self, path, selection=None, direction=None):
        from geometry_import_dialog import GeometryImportDialog
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
            cached = store_surface(aligned)
            if source_digest(path) != digest:
                raise ValueError('Source changed during face selection. Check the folder again.')
            key = face_assignment_key(path)
            previous = self.preferences.value(key)
            kept = dialog.component_index if components.count > 1 else None
            geometry = [kept] if kept is not None or dialog.normalized_direction is not None else []
            if dialog.normalized_direction is not None:
                geometry.append(dialog.normalized_direction)
            self.preferences.setValue(key, json.dumps([digest, cells, *geometry]))
            self.preferences.sync()
            if self.preferences.status() != QSettings.Status.NoError:
                if previous is None:
                    self.preferences.remove(key)
                else:
                    self.preferences.setValue(key, previous)
                raise OSError('Could not save the specimen faces.')
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
            return prepared if dialog.exec() else None
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
            saved = [self.saved_faces(path) for path in paths]
            first = None
            if self.mode.currentIndex() == 3:
                first = self.pick_faces(paths[0], saved[0])
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
            self.settings_hint.setText(f'Captured: {base.material_name} | '
                                       f'{base.loading_mode}: {base.compression:g} mm / {base.applied_force:g} N | {base.solver_backend}.')
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
            self.future = self.executor.submit(prepare_specimen, path, direction, selection)
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
                button = QToolButton()
                button.setIcon(self.face_icon)
                button.setIconSize(QSize(16, 16))
                button.setFixedSize(CONTROL_HEIGHT, CONTROL_HEIGHT)
                button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                button.setAccessibleName('Edit specimen geometry and faces')
                button.setToolTip('Edit specimen geometry and faces')
                button.clicked.connect(lambda checked=False, row=index: self.edit_specimen(row))
                self.table.setCellWidget(index, FACES_COLUMN, button)
            button.setAccessibleDescription(name)
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
            controller = getattr(self.queue, 'remote_controller', None)
            if controller is not None and controller.requested and solvers != [self.base.solver_backend]:
                raise ValueError('TRUBA uses the current workspace solver. Choose Current workspace or switch to local execution.')
            snapshots = []
            for index, item in enumerate(self.items):
                if (not self.table.isRowHidden(index)
                        and self.table.item(index, RUN_COLUMN).checkState() == Qt.CheckState.Checked):
                    snapshots.extend(specimen_snapshots(item, self.root, base, solvers))
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
