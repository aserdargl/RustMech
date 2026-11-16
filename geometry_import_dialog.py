"""Interactive geometry import workspace."""
from imported_geometry import read_surface, plane_faces, align_compression_surface, store_surface
from pathlib import Path
from contextlib import nullcontext
from PySide6.QtCore import Qt

def choose_geometry(parent):
    from PySide6.QtWidgets import QFileDialog
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


from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton


class GeometryImportDialog(QDialog):
    def __init__(self, surface, filename, parent=None):
        super().__init__(parent)
        from pyvistaqt import QtInteractor
        from window_state import install_window_state
        from PySide6.QtWidgets import QApplication
        install_window_state(QApplication.instance())
        self.setObjectName('geometryImport')
        self.setWindowTitle(f'Import geometry — {filename}')
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
        for index, label in enumerate(('Select support face', 'Select compression face')):
            button = QPushButton(label)
            button.setAutoDefault(False)
            button.clicked.connect(lambda checked=False, i=index: self.set_role(i))
            row.addWidget(button)
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
        self.confirm = QPushButton('Use geometry')
        self.confirm.setEnabled(False)
        self.confirm.clicked.connect(self.commit)
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(self.reject)
        for button in (self.confirm, cancel):
            button.setAutoDefault(False)
            button.setDefault(False)
            row.addWidget(button)
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
        self.status.setText('Selecting ' + ('support' if role == 0 else 'compression') + ' face (right-click)')

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
                self.status.setText('Support: green. Compression: orange. Faces validated; ready to import.')
            except ValueError as error:
                self.status.setText(str(error))
        else:
            self.set_role(1 - self.role)
        self.confirm.setEnabled(self.result_surface is not None)

    def commit(self):
        if self.result_surface is not None:
            self.accept()
