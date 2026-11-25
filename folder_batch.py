"""Prepare and run folder-based mechanical analyses locally or on TRUBA."""
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
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QDialog, QGridLayout, QGroupBox, QHeaderView,
                              QHBoxLayout, QLabel, QLineEdit, QPushButton, QSplitter, QStyle,
                              QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from camera_toolbar import camera_icon
from doe_studio import DoEStudioWidget, STATE_COMPLETED, classify_status
from file_dialog import QFileDialog
from folder_batch_preview import FacePreviewButton, FacePreviewPopup, store_face_preview
from folder_batch_results import (BATCH_DIRECTORY, LEGACY_BATCH_DIRECTORY, FolderResultMixin, atomic_text,
                                  batch_directory, folder_job_row, validate_folder_snapshot)
from imported_geometry import (SURFACE_EXTENSIONS, SURFACE_FILE_FILTER, SurfaceComponents,
                               align_compression_surface, automatic_compression_selection,
                               compression_direction, read_polygonal_surface, store_surface, surface_cache_directory)
from model import MIN_MESH_INPUT_MM, settings_from_dict
from operation_progress import OperationProgress
from remote_operations import BATCH_RESULTS_DIRECTORY
from studio_style import (CompactComboBox, CompactDoubleSpinBox, CONTROL_HEIGHT,
                          STUDIO_STYLESHEET, field_label, mesh_precision_stepper, stepper_with_unit)
from truba_file_browser import TrubaFileBrowser
from truba_resources import BASELINES
from ui_language import translate
from window_state import install_window_state


FACES_COLUMN, RUN_COLUMN, SPECIMEN_COLUMN, PREFLIGHT_COLUMN, ANALYSIS_COLUMN = range(5)


class SelectAllSpecimens(QCheckBox):
    def nextCheckState(self):
        self.setCheckState(Qt.CheckState.Unchecked if self.checkState() == Qt.CheckState.Checked
                           else Qt.CheckState.Checked)


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


def prepare_batch_snapshots(items, root, base, solvers, target, auth=None, remote_directory=None):
    snapshots = [snapshot for item in items for snapshot in
                 specimen_snapshots(item, root, base, solvers, execution_target=target)]
    settings_by_geometry = {}
    for settings, _ in snapshots:
        validate_folder_snapshot(settings)
        if auth is not None:
            values = asdict(settings)
            values.pop('imported_geometry_path')
            settings_by_geometry.setdefault(settings.imported_geometry_path, []).append(values)
    if auth is not None:
        from truba_files import stage_batch_geometries
        staged = stage_batch_geometries(auth, settings_by_geometry, settings_by_geometry=settings_by_geometry,
                                        relative=remote_directory)
        for settings, metadata in snapshots:
            metadata['staged_geometry'] = staged[settings.imported_geometry_path]
    return snapshots


class FolderBatchRunner(FolderResultMixin, DoEStudioWidget):
    """Use the shared solver lifecycle for one explicit Batch run."""

    batch_execution = None

    def _require_truba_row(self, capability, settings):
        if self.batch_execution is not None:
            context, auth = self.batch_execution
            return replace(context, parallel_jobs=1), auth
        return super()._require_truba_row(capability, settings)

    def _parallel_limit(self):
        if self.batch_execution is not None:
            context, _ = self.batch_execution
            return context.parallel_jobs
        return super()._parallel_limit()

    @property
    def active(self):
        return self._active_mode is not None

    def start(self, snapshots):
        rows = [folder_job_row(settings, metadata) for settings, metadata in snapshots]
        self._rows[:] = rows
        self._refresh_table_view()
        self._report_persistence_problem("")
        self.start_batch_solve()

    def _new_dataset_path(self):
        first = self._rows[0]
        return Path(first.folder_job['results_root']) / f"toplu_ozet_{first.exp_id}.csv"

    def _persist_progress(self):
        self._write_folder_reports(summary_name=self._dataset_path.name)

    def dispose(self):
        self._poll_timer.stop()
        self.stop_active_queue()


class FolderBatchDialog(QDialog):
    def __init__(self, settings_provider, mesh_generator, solver_runner, remote_controller,
                 migrate_workspace, parent=None):
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
        self.settings_provider = settings_provider
        self.mesh_generator = mesh_generator
        self.solver_runner = solver_runner
        self.remote_controller = remote_controller
        self.migrate_workspace = migrate_workspace
        self.runner = None
        self.staged = None
        self.remote_destination = None
        self.running = False
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='folder-preflight')
        self.future = None
        self.preparing_target = None
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
        self.browse = QToolButton(self)
        self.browse.setIcon(self.browse.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.browse.setIconSize(QSize(16, 16))
        self.browse.setFixedSize(CONTROL_HEIGHT, CONTROL_HEIGHT)
        self.browse.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.browse.setToolTip('Choose folder...')
        self.browse.setAccessibleName('Choose folder...')
        self.browse.clicked.connect(self.choose_folder)
        row.addWidget(self.browse)
        self.check = self.button('Check Folder', self.check_folder, primary=True)
        row.addWidget(self.check)
        layout.addLayout(row)

        controls = QHBoxLayout()
        controls.setSpacing(5)
        self.mode = CompactComboBox()
        self.mode.addItems(['Z: bottom / top', 'X: min / max', 'Y: min / max',
                            'First specimen', 'Manual faces'])
        direction_label = QLabel('Direction')
        direction_label.setBuddy(self.mode)
        controls.addWidget(direction_label)
        controls.addWidget(self.mode)
        self.solver = CompactComboBox()
        self.solver.addItems(['Current workspace', 'ANSYS MAPDL', 'Kratos', 'ANSYS + Kratos'])
        solver_label = QLabel('Solver')
        solver_label.setBuddy(self.solver)
        controls.addWidget(solver_label)
        controls.addWidget(self.solver)
        self.execution_target = CompactComboBox()
        self.execution_target.addItem('TRUBA', 'truba')
        self.execution_target.addItem(translate('Local'), 'local')
        execution_label = QLabel('Execution')
        execution_label.setBuddy(self.execution_target)
        controls.addWidget(execution_label)
        controls.addWidget(self.execution_target)
        self.script = CompactComboBox()
        self.script.addItem('Auto', 'auto')
        for name, limits in BASELINES.items():
            if not limits.min_gpus:
                self.script.addItem(name, name)
        self.script.setToolTip('Choose the TRUBA queue for the generated Slurm script.')
        self.script_label = QLabel('Script')
        self.script_label.setBuddy(self.script)
        controls.addWidget(self.script_label)
        controls.addWidget(self.script)
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
        mesh_layout.addWidget(self.mesh_type, 0, 3)
        self.mesh_precision = mesh_precision_stepper(self.preferences, 'folderBatch/meshDisplayDecimals',
                                                   self.mesh_resolution, self.local_mesh_size)
        mesh_layout.addWidget(field_label('Mesh decimal places', self.mesh_precision), 1, 2)
        mesh_layout.addWidget(self.mesh_precision, 1, 3)
        mesh_layout.setColumnStretch(4, 1)
        layout.addWidget(mesh)
        self.settings_hint = QLabel()
        self.settings_hint.setWordWrap(True)
        layout.addWidget(self.settings_hint)
        specimens = QGroupBox('This computer')
        specimen_layout = QVBoxLayout(specimens)
        specimen_layout.setContentsMargins(6, 6, 6, 6)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['', '', 'Specimen', 'Preflight', 'Status'])
        self.face_icon = camera_icon('orthographic')
        self.face_preview = FacePreviewPopup(self)
        edit_header = self.table.horizontalHeaderItem(FACES_COLUMN)
        edit_header.setIcon(QIcon(str(Path(__file__).resolve().parent / 'assets' / 'ui' / 'edit-pencil.svg')))
        edit_header.setToolTip('Edit specimen geometry and faces')
        edit_header.setData(Qt.ItemDataRole.AccessibleTextRole, 'Edit')
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(FACES_COLUMN, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(FACES_COLUMN, CONTROL_HEIGHT + 5)
        header.setSectionResizeMode(RUN_COLUMN, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(RUN_COLUMN, CONTROL_HEIGHT + 5)
        self.select_all = SelectAllSpecimens(header.viewport())
        self.select_all.setTristate(True)
        self.select_all.setAccessibleName(translate('Select all ready specimens'))
        self.select_all.setToolTip(self.select_all.accessibleName())
        self.select_all.clicked.connect(self.set_all_specimens_checked)
        header.geometriesChanged.connect(self.position_select_all)
        header.sectionResized.connect(self.position_select_all)
        self.table.horizontalScrollBar().valueChanged.connect(self.position_select_all)
        self.position_select_all()
        self.table.verticalHeader().setMinimumSectionSize(CONTROL_HEIGHT + 2)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header.setSectionResizeMode(PREFLIGHT_COLUMN, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(ANALYSIS_COLUMN, 65)
        header.setStretchLastSection(True)
        specimen_layout.addWidget(self.table)

        self.files_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.files_splitter.setObjectName('folderBatchFiles')
        self.files_splitter.setChildrenCollapsible(False)
        self.files_splitter.addWidget(specimens)
        self.truba_group = QGroupBox('TRUBA files')
        remote_layout = QVBoxLayout(self.truba_group)
        remote_layout.setContentsMargins(6, 6, 6, 6)
        self.truba_browser = TrubaFileBrowser(None, self.executor, self.button, self.preferences,
                                             batch_auth=self.truba_auth)
        self.truba_browser.state_changed.connect(self.update_truba_destination)
        remote_layout.addWidget(self.truba_browser)
        self.files_splitter.addWidget(self.truba_group)
        self.files_splitter.setSizes([500, 410])
        layout.addWidget(self.files_splitter, 1)
        footer = QHBoxLayout()
        self.close_button = self.button('Close', self.close)
        self.send = self.button('Send to TRUBA', self.send_to_truba)
        self.run = self.button('Run', self.run_batch, primary=True)
        self.stop = self.button('Stop', self.stop_batch)
        action_width = max(button.sizeHint().width() for button in (self.send, self.run, self.stop))
        for button in (self.send, self.run, self.stop):
            button.setMinimumWidth(action_width)
        footer.addWidget(self.close_button)
        footer.addStretch()
        footer.addWidget(self.send)
        footer.addWidget(self.run)
        footer.addWidget(self.stop)
        layout.addLayout(footer)
        self.progress = OperationProgress(self)
        layout.addWidget(self.progress)

        self.status = QLabel('Choose a folder and check it. No analysis starts during preparation.')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self._restore_preferences()
        self.folder.textChanged.connect(self.invalidate)
        self.mode.currentIndexChanged.connect(self.invalidate)
        self.solver.currentIndexChanged.connect(self.invalidate_transfer)
        self.mesh_resolution.valueChanged.connect(self.invalidate_transfer)
        self.local_mesh_size.valueChanged.connect(self.invalidate_transfer)
        self.mesh_type.currentIndexChanged.connect(self._mesh_type_changed)
        self.table.itemChanged.connect(self.selection_changed)
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
        self._truba_mesh_type = self.preferences.value("folderBatch/truba_mesh_type", "TET4", type=str)
        if self._truba_mesh_type not in ('TET4', 'TET10'):
            self._truba_mesh_type = 'TET4'
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
            "truba_mesh_type": self._truba_mesh_type,
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
        control.setKeyboardTracking(False)
        control.setValue(value)
        layout.addWidget(field_label(label, control), row, 0)
        layout.addWidget(stepper_with_unit(control, 'mm'), row, 1)
        return control

    def _mesh_type_changed(self):
        if self.execution_target.currentData() == 'truba':
            self._truba_mesh_type = self.mesh_type.currentText()
        self.invalidate_transfer()

    def update_execution_target(self):
        remote = self.execution_target.currentData() == 'truba'
        self.script_label.setVisible(remote)
        self.script.setVisible(remote)
        self.truba_group.setVisible(remote)
        with QSignalBlocker(self.solver), QSignalBlocker(self.mesh_type):
            if remote:
                self._local_solver_index = self.solver.currentIndex()
                self._local_mesh_index = self.mesh_type.currentIndex()
                self.solver.setCurrentIndex(2)
                self.mesh_type.setCurrentText(self._truba_mesh_type)
            else:
                self.solver.setCurrentIndex(self._local_solver_index)
                self.mesh_type.setCurrentIndex(self._local_mesh_index)
        self.update_settings_hint()
        self.invalidate_transfer()
        self.set_busy(self.busy)

    def update_settings_hint(self):
        remote = self.execution_target.currentData() == 'truba'
        text = translate(
            'Mesh and analysis run on TRUBA with Kratos. Self-contact is disabled. TET10 uses displacement control and penalty mortar. Material and loading come from the Mechanic workspace.'
            if remote else 'Mesh and solution run locally. Analysis settings come from the Mechanic workspace.')
        if self.base is not None:
            base = self.base
            text += (f'\n{base.material_name} | '
                     f'{base.loading_mode}: {base.compression:g} mm / {base.applied_force:g} N')
        self.settings_hint.setText(text)

    def button(self, text, callback, *, primary=False, hint=None):
        button = QPushButton(text, self)
        if hint:
            button.setToolTip(hint)
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
        self.invalidate_transfer()

    @property
    def busy(self):
        return self.future is not None or bool(self.pending) or self.running

    def invalidate_transfer(self, *_):
        if self.staged is not None:
            self.staged = None
            self.status.setText(translate('Selection or settings changed. Send the batch to TRUBA again.'))
        self.update_actions()

    def set_busy(self, busy):
        for widget in (self.folder, self.browse, self.mode, self.execution_target, self.check,
                       self.mesh_resolution, self.local_mesh_size, self.script):
            widget.setEnabled(not busy)
        self.truba_group.setEnabled(not busy or self.running)
        local = self.execution_target.currentData() == 'local'
        self.solver.setEnabled(local and not busy)
        self.mesh_type.setEnabled(not busy)
        for row in range(self.table.rowCount()):
            self.table.cellWidget(row, FACES_COLUMN).setEnabled(not busy)
        for widget in (self.table, self.mesh_precision):
            widget.setEnabled(not busy)
        self.update_actions(busy=busy)

    def selected_solvers(self):
        return {0: [self.base.solver_backend], 1: ['ansys_mapdl'], 2: ['kratos'],
                3: ['ansys_mapdl', 'kratos']}[self.solver.currentIndex()]

    def batch_settings(self):
        settings = replace(self.base, mesh_resolution=self.mesh_resolution.value(),
                           local_mesh_size=self.local_mesh_size.value(), mesh_type=self.mesh_type.currentText())
        if self.execution_target.currentData() == 'truba':
            settings = replace(settings, self_contact_mode='disabled')
            if settings.mesh_type == 'TET10':
                settings = replace(settings, kratos_contact_method='penalty')
        return settings

    def position_select_all(self, *_):
        header = self.table.horizontalHeader()
        size = self.select_all.sizeHint()
        self.select_all.resize(size)
        self.select_all.move(header.sectionViewportPosition(RUN_COLUMN) + (header.sectionSize(RUN_COLUMN) - size.width()) // 2,
                             (header.viewport().height() - size.height()) // 2)

    def ready_checkboxes(self):
        return [self.table.item(index, RUN_COLUMN) for index, specimen in enumerate(self.items)
                if specimen.cached]

    def selected_specimens(self):
        return [self.items[checkbox.row()] for checkbox in self.ready_checkboxes()
                if checkbox.checkState() == Qt.CheckState.Checked]

    def set_all_specimens_checked(self, checked):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        with QSignalBlocker(self.table):
            for checkbox in self.ready_checkboxes():
                checkbox.setCheckState(state)
        self.invalidate_transfer()

    def selection_changed(self, item):
        if item.column() == RUN_COLUMN:
            self.invalidate_transfer()

    def update_actions(self, *, busy=None):
        if busy is None:
            busy = self.busy
        checkboxes = self.ready_checkboxes()
        checked = sum(checkbox.checkState() == Qt.CheckState.Checked for checkbox in checkboxes)
        state = Qt.CheckState.Unchecked
        if checked:
            state = Qt.CheckState.Checked if checked == len(checkboxes) else Qt.CheckState.PartiallyChecked
        self.select_all.setEnabled(not busy and bool(checkboxes))
        self.select_all.setCheckState(state)
        remote = self.execution_target.currentData() == 'truba'
        navigating = remote and self.truba_browser.navigating
        self.send.setVisible(remote)
        self.send.setEnabled(not busy and not navigating and bool(checked))
        self.run.setEnabled(not busy and not navigating and (self.staged is not None if remote else bool(checked)))
        self.stop.setVisible(self.running)
        self.close_button.setEnabled(self.preparing_target != 'truba')

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, 'Specimen folder', self.folder.text().strip(),
                                                filter=f'All files (*);;{SURFACE_FILE_FILTER}')
        if path:
            self.folder.setText(path)

    def truba_auth(self):
        return self.remote_controller.auth() if self.remote_controller is not None else None

    def update_truba_destination(self):
        browser = self.truba_browser
        destination = (browser.auth.username, browser.directory) if browser.loaded else None
        if destination != self.remote_destination:
            self.remote_destination = destination
            self.invalidate_transfer()
        else:
            self.update_actions()

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
        if self.busy:
            return
        try:
            if not self.folder.text().strip():
                raise ValueError('Choose a specimen folder.')
            root = Path(self.folder.text()).expanduser().resolve()
            legacy = root / LEGACY_BATCH_DIRECTORY
            if legacy.exists() or legacy.is_symlink() or legacy.is_junction():
                self.migrate_workspace(root)
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
            self.invalidate_transfer()
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
            self.status.setText(f'{ready}/{len(self.items)} ' + translate('ready. Review the faces and select the specimens to analyze.'))

    def poll(self):
        if self.truba_browser.isVisible():
            self.truba_browser.sync_connection()
            self.truba_browser.check_task()
        if self.running:
            self.refresh_run()
        if self.future is None or not self.future.done():
            return
        future, self.future = self.future, None
        if self.preparing_target is not None:
            target, self.preparing_target = self.preparing_target, None
            self.finish_preparation(future, target)
            return
        try:
            self.items[self.working_index] = future.result()
        except Exception as error:
            self.items[self.working_index].error = str(error)
        self.refresh_table()
        self.launch_next()

    def refresh_table(self):
        checked = {self.table.item(i, SPECIMEN_COLUMN).text(): self.table.item(i, RUN_COLUMN).checkState()
                   for i in range(self.table.rowCount()) if self.table.item(i, RUN_COLUMN).data(Qt.ItemDataRole.UserRole)}
        blocker = QSignalBlocker(self.table)
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
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable if specimen.cached
                           else Qt.ItemFlag.NoItemFlags)
            check.setCheckState(checked.get(name, Qt.CheckState.Unchecked) if specimen.cached else Qt.CheckState.Unchecked)
            check.setData(Qt.ItemDataRole.UserRole, bool(specimen.cached))
            self.table.setItem(index, RUN_COLUMN, check)
            preflight = specimen.error or f'Ready | height {specimen.height:g} mm'
            if specimen.cached and specimen.excluded_components:
                preflight += f' | Excluded components: {specimen.excluded_components}'
            if specimen.cached and specimen.normalized_direction is not None:
                preflight += ' | End faces flattened'
            for column, value in ((SPECIMEN_COLUMN, name), (PREFLIGHT_COLUMN, preflight), (ANALYSIS_COLUMN, '')):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(index, column, item)
        self.table.resizeColumnToContents(SPECIMEN_COLUMN)
        blocker.unblock()

    def edit_specimen(self, index):
        if self.busy:
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
                self.invalidate_transfer()
                self.items[index] = prepared
                self.refresh_table()
                self.set_busy(False)
                self.status.setText('Specimen faces saved.')
        except Exception as error:
            self.status.setText(str(error))

    def send_to_truba(self):
        if (self.busy or self.truba_browser.navigating
                or self.execution_target.currentData() != 'truba' or not self.selected_specimens()):
            return
        auth = self.truba_auth()
        if auth is None:
            self.status.setText(translate('TRUBA is disconnected. Connect using the TRUBA button, then send the prepared specimens.'))
            return
        if self.remote_destination is None or self.remote_destination[0] != auth.username:
            self.status.setText(translate('Choose a TRUBA folder for the connected account.'))
            return
        self.staged = None
        self.prepare_selected('truba', auth, self.remote_destination[1])

    def run_batch(self):
        if self.busy or not self.selected_specimens():
            return
        if self.execution_target.currentData() == 'local':
            self.prepare_selected('local')
        elif self.staged is not None and not self.truba_browser.navigating:
            self.start_run(self.staged)

    def prepare_selected(self, target, auth=None, remote_directory=None):
        self.status.setText(translate('Uploading geometry and analysis settings to TRUBA…' if auth is not None
                                      else 'Checking specimen files and analysis settings…'))
        self.preparing_target = target
        self.future = self.executor.submit(prepare_batch_snapshots, self.selected_specimens(), self.root,
                                           self.batch_settings(), self.selected_solvers(), target, auth, remote_directory)
        self.set_busy(True)

    def finish_preparation(self, future, target):
        try:
            snapshots = future.result()
        except Exception as error:
            self.status.setText(translate(str(error)))
        else:
            if target == 'truba':
                uploaded = snapshots[0][1]['staged_geometry']
                selected = self.remote_destination
                if selected is not None and (selected[0], selected[1].as_posix()) == (
                        uploaded['username'], uploaded['directory']):
                    self.staged = snapshots
                    self.status.setText(translate('Geometry and analysis settings uploaded to TRUBA. Select Run to start.'))
                    self.refresh_truba_files()
                else:
                    self.status.setText(translate('Selection or settings changed. Send the batch to TRUBA again.'))
            else:
                self.start_run(snapshots)
        finally:
            self.set_busy(self.busy)

    def start_run(self, snapshots):
        try:
            execution = None
            if snapshots[0][1]['execution_target'] == 'truba':
                partition = self.script.currentData()
                for settings, metadata in snapshots:
                    if partition == 'auto':
                        _, auth = self.remote_controller.require_analysis(settings.solver_backend, settings)
                    else:
                        execution = self.remote_controller.require_batch_queue(partition, settings)
                        _, auth = execution
                    if auth is None or auth.username != metadata['staged_geometry']['username']:
                        raise ValueError('Use the TRUBA account that received the prepared specimens.')
            if self.runner is None:
                self.runner = FolderBatchRunner(lambda: self.base, self.mesh_generator, self.solver_runner, self)
                self.runner.hide()
                self.runner.remote_controller = self.remote_controller
            self.runner.batch_execution = execution
            self.runner.start(snapshots)
        except Exception as error:
            self.status.setText(translate(str(error)))
            return
        self.running = True
        self.set_busy(True)
        self.refresh_run()

    def refresh_run(self):
        by_source = {}
        for row in self.runner.rows:
            solver = row.design_settings.solver_backend
            by_source.setdefault(row.folder_job['source'], []).append(f'{solver}: {translate(row.status)}')
        for index, item in enumerate(self.items):
            cell = self.table.item(index, ANALYSIS_COLUMN)
            status = ' | '.join(by_source.get(item.source, ()))
            if cell.text() != status:
                cell.setText(status)
                cell.setToolTip(status)
        self.progress.update_step(*self.runner._operation_state())
        if self.runner.active:
            self.status.setText(translate('Batch analysis running…'))
            return
        self.running = False
        self.set_busy(False)
        rows = self.runner.rows
        completed = sum(classify_status(row.status) == STATE_COMPLETED for row in rows)
        message = translate('Completed {completed}/{total} analyses.').format(completed=completed, total=len(rows))
        message += '\n' + translate(f'Results folder is {batch_directory(self.root) / "Results"}')
        if rows[0].folder_job['execution_target'] == 'truba':
            from truba_files import project_root

            staged = rows[0].folder_job['staged_geometry']
            path = project_root(staged['username']) / staged['directory'] / BATCH_RESULTS_DIRECTORY
            message += '\n' + translate('TRUBA results folder: {path}').format(path=path)
            self.refresh_truba_files()
        if self.runner._persistence_error:
            message += '\n' + self.runner._persistence_error
        self.status.setText(message)

    def refresh_truba_files(self):
        if self.remote_destination is not None:
            self.truba_browser.refresh_after_upload(self.truba_auth(), self.remote_destination[1])

    def stop_batch(self):
        if self.running:
            self.runner.stop_active_queue()
            self.refresh_run()

    def detach_remote_jobs(self):
        return self.runner.detach_remote_queue() if self.runner is not None else []

    def reject(self):
        if self.preparing_target == 'truba':
            self.status.setText(translate('Wait for the TRUBA upload to finish.'))
            return
        super().reject()

    def closeEvent(self, event):
        if self.preparing_target == 'truba':
            event.ignore()
            self.reject()
            return
        if self.running:
            super().closeEvent(event)
            return
        self.pending.clear()
        if self.future is not None:
            self.future.cancel()
            self.future = None
        self.preparing_target = None
        self.set_busy(False)
        super().closeEvent(event)

    def dispose(self):
        if not self.disposed:
            self._save_preferences()
            self.disposed = True
            self.timer.stop()
            self.pending.clear()
            self.preparing_target = None
            self.executor.shutdown(wait=False, cancel_futures=True)
            if self.runner is not None:
                self.runner.dispose()
