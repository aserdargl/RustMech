"""Interactive geometry import workspace."""
from imported_geometry import read_surface, plane_faces, align_compression_surface, store_surface
from pathlib import Path
from contextlib import nullcontext
from PySide6.QtCore import Qt

def choose_geometry(parent):
    from file_dialog import QFileDialog
    path, _ = QFileDialog.getOpenFileName(parent, 'Import geometry (mm)', '', 'Surface geometry (*.stl *.obj *.ply *.vtp)')
    if not path:
        return None
    progress = getattr(parent, 'operation_progress', None)
    with progress.step('Reading imported geometry…') if progress is not None else nullcontext():
        surface = read_surface(path)
    dialog = GeometryImportDialog(surface, Path(path).name, parent)
    try:
        if dialog.exec():
            surface, height = dialog.result_surface
            with progress.step('Saving imported geometry…') if progress is not None else nullcontext():
                return store_surface(surface), height, Path(path).name
    finally:
        dialog.plotter.close()
        dialog.deleteLater()
    return None


from PySide6.QtWidgets import QButtonGroup, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton


class GeometryImportDialog(QDialog):
    def __init__(self, surface, filename, parent=None, *, save_selection=None):
        super().__init__(parent)
        from pyvistaqt import QtInteractor
        from window_state import install_window_state
        from PySide6.QtWidgets import QApplication
        install_window_state(QApplication.instance())
        self.save_selection = save_selection
        self.setObjectName('batchSpecimenFaces' if save_selection else 'geometryImport')
        from ui_language import translate
        title = translate('Specimen faces' if save_selection else 'Import geometry')
        self.setWindowTitle(f'{title} — {filename}')
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinimizeButtonHint | Qt.WindowType.WindowMaximizeButtonHint)
        self.resize(900, 700)
        self.setSizeGripEnabled(True)
        self.surface = surface
        self.selected = [None, None]
        self.role = 0
        self.result_surface = None
        layout = QVBoxLayout(self)
        self.hint = QLabel('Coordinates are mm. Select two opposite parallel planar end faces. Right-click a face to select it.')
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        row = QHBoxLayout()
        self.role_buttons = QButtonGroup(self)
        for index, label in enumerate(('Select support face', 'Select compression face')):
            button = QPushButton(translate(label))
            button.setAutoDefault(False)
            button.setCheckable(True)
            self.role_buttons.addButton(button, index)
            row.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        role_buttons = self.role_buttons.buttons()
        role_width = max(button.sizeHint().width() for button in role_buttons) + 8
        for button in role_buttons:
            button.setMinimumWidth(role_width)
        self.role_buttons.button(self.role).setChecked(True)
        self.role_buttons.idClicked.connect(self.set_role)
        row.addStretch()
        layout.addLayout(row)

        from operation_progress import OperationProgress
        self.operation_progress = OperationProgress(self)
        self.plotter = QtInteractor(self)
        layout.addWidget(self.plotter.interactor, 1)
        self.plotter.set_background('#f0f0f0', top='#ffffff')
        self.plotter.enable_lightkit()
        self.plotter.add_mesh(surface, color='#4ea8de', show_edges=True, name='geometry')
        self.plotter.add_axes()
        self.plotter.enable_surface_point_picking(callback=self.pick, use_picker=True, show_point=False, show_message=False, picker='cell')
        self.plotter.reset_camera()
        self.status = QLabel('Selecting support face')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.confirm = QPushButton(translate('Save and close' if save_selection else 'Use geometry'))
        self.confirm.setEnabled(False)
        self.confirm.clicked.connect(self.commit)
        cancel = QPushButton(translate('Cancel'))
        cancel.clicked.connect(self.reject)
        action_width = max(self.confirm.sizeHint().width(), cancel.sizeHint().width()) + 8
        for button in (self.confirm, cancel):
            button.setAutoDefault(False)
            button.setDefault(False)
            button.setMinimumWidth(action_width)
            row.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(self.operation_progress)

    def done(self, result):
        # Finalize before QDialog hides the native OpenGL window.
        self.plotter.close()
        super().done(result)

    def closeEvent(self, event):
        self.plotter.close()
        super().closeEvent(event)

    def set_role(self, role):
        self.role = role
        self.role_buttons.button(role).setChecked(True)
        self.status.setText('Selecting support face (right-click)' if role == 0
                            else 'Selecting compression face (right-click)')

    def pick(self, point, picker):
        if picker.GetActor() != self.plotter.actors.get('geometry'):
            return
        cell = picker.GetCellId()
        if cell < 0:
            return
        self.select_cell(cell)

    def select_cell(self, cell):
        self.operation_progress.begin_step('Finding planar faces and validating geometry…')
        try:
            self._select_cell(cell)
        finally:
            self.operation_progress.update_step(False, '')

    def _select_cell(self, cell):
        ids, _, _ = plane_faces(self.surface, cell)
        self.selected[self.role] = cell
        actor = self.plotter.add_mesh(self.surface.extract_cells(ids), color=('#22c55e' if self.role == 0 else '#f97316'),
                                     name=f'selection{self.role}', pickable=False, lighting=False)
        actor.mapper.SetResolveCoincidentTopologyToPolygonOffset()
        actor.mapper.SetRelativeCoincidentTopologyPolygonOffsetParameters(-2, -2)
        self.result_surface = None
        if None not in self.selected:
            try:
                self.result_surface = align_compression_surface(self.surface, *self.selected)
                self.status.setText('Support: green. Compression: orange. Faces validated.' if self.save_selection
                                    else 'Support: green. Compression: orange. Faces validated; ready to import.')
            except ValueError as error:
                self.status.setText(str(error))
        else:
            self.set_role(1 - self.role)
        self.confirm.setEnabled(self.result_surface is not None)

    def commit(self):
        if self.result_surface is None:
            return
        if self.save_selection is not None:
            try:
                self.save_selection(*self.result_surface, tuple(self.selected))
            except (OSError, ValueError, RuntimeError) as error:
                self.status.setText(str(error))
                return
        self.accept()
