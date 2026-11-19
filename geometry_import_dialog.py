"""Interactive geometry import workspace."""
from imported_geometry import (align_compression_surface, automatic_compression_selection, plane_faces,
                               read_surface, store_surface)
from pathlib import Path
from contextlib import nullcontext

import numpy as np
import pyvista as pv
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QGroupBox, QHBoxLayout, QLabel,
                              QListWidget, QPushButton, QSplitter, QVBoxLayout)


PLATEN_NAMES = ('fixed_platen', 'moving_platen')
PLATEN_COLORS = ('#15803d', '#c2410c')

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


class GeometryImportDialog(QDialog):
    def __init__(self, surface, filename, parent=None, *, save_selection=None, initial_cells=(), geometry_error='',
                 components=None, component_index=None, direction=None, normalized_direction=None):
        super().__init__(parent)
        from pyvistaqt import QtInteractor
        from window_state import install_window_state
        from PySide6.QtWidgets import QApplication
        install_window_state(QApplication.instance())
        self.save_selection = save_selection
        self.geometry_error = geometry_error
        self.setObjectName('batchSpecimenFaces' if save_selection else 'geometryImport')
        from ui_language import translate
        title = translate('Specimen faces' if save_selection else 'Import geometry')
        self.setWindowTitle(f'{title} — {filename}')
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinimizeButtonHint | Qt.WindowType.WindowMaximizeButtonHint)
        self.resize(900, 700)
        self.setSizeGripEnabled(True)
        self.surface = surface
        self.components = components
        self.component_index = component_index
        self.direction = direction
        self.normalized_direction = normalized_direction
        self._initial_cells = initial_cells
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
        self.show_platens = None
        if save_selection is not None:
            self.show_platens = QCheckBox(translate('Show platens'))
            self.show_platens.setObjectName('legendPlatenPill')
            self.show_platens.setToolTip(translate('Fixed platen: green. Moving platen: orange.'))
            self.show_platens.setChecked(True)
            self.show_platens.toggled.connect(self.set_platens_visible)
            row.addWidget(self.show_platens)
        row.addStretch()
        layout.addLayout(row)

        from operation_progress import OperationProgress
        self.operation_progress = OperationProgress(self)
        self.plotter = QtInteractor(self)
        if components is None:
            layout.addWidget(self.plotter.interactor, 1)
        else:
            layout.addWidget(self.component_workspace(), 1)
        self.plotter.set_background('#f0f0f0', top='#ffffff')
        self.plotter.enable_lightkit()
        self.draw_components()
        self.plotter.add_axes()
        self.plotter.enable_surface_point_picking(callback=self.pick, use_picker=True, show_point=False, show_message=False, picker='cell')
        self.plotter.reset_camera()
        self.status = QLabel(geometry_error or 'Selecting support face')
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

    def showEvent(self, event):
        super().showEvent(event)
        if self._initial_cells or self.direction is not None or self.normalized_direction is not None:
            # Wait for Qt to show the modal window and its native viewport.
            QTimer.singleShot(0, self, self._restore_faces)

    def _restore_faces(self):
        if not self.isVisible():
            return
        cells, self._initial_cells = self._initial_cells, ()
        direction = self.normalized_direction if self.normalized_direction is not None else self.direction
        if direction is not None and not self.geometry_error and (not cells or self.normalized_direction is not None):
            try:
                surface, automatic_cells = automatic_compression_selection(self.surface, direction)
            except ValueError as error:
                self.normalized_direction = None
                self.status.setText(str(error))
                return
            if surface is not self.surface:
                self.surface = surface
                self.normalized_direction = tuple(float(value) for value in direction)
                self.draw_components()
            cells = cells or automatic_cells
        for role, cell in enumerate(cells):
            self.set_role(role)
            try:
                self._select_cell(cell)
            except ValueError as error:
                self.status.setText(str(error))

    def component_workspace(self):
        from ui_language import translate
        panels = QSplitter(Qt.Orientation.Horizontal)
        panels.setObjectName('specimenComponentPanels')
        panels.setHandleWidth(7)
        panels.setChildrenCollapsible(False)
        group = QGroupBox('Connected components')
        layout = QVBoxLayout(group)
        hint = QLabel('Choose one solid to keep. Blue remains, red is excluded.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.component_list = QListWidget()
        self.component_list.setAccessibleName('Connected components')
        for index, area in enumerate(self.components.areas):
            self.component_list.addItem(translate('Component {index}').format(index=index + 1) + f' · {area:.5g} mm²')
        self.component_list.setCurrentRow(self.component_index if self.component_index is not None else -1)
        self.component_list.itemSelectionChanged.connect(self.change_selected_component)
        layout.addWidget(self.component_list)
        panels.addWidget(group)
        panels.addWidget(self.plotter.interactor)
        panels.setSizes([225, 675])
        return panels

    def draw_components(self):
        if self.components is None or self.component_index is not None:
            self.plotter.add_mesh(self.surface, color='#4ea8de', show_edges=True, name='geometry',
                                  reset_camera=False, render=False)
        if self.components is not None:
            excluded = (self.components.surface if self.component_index is None
                        else self.components.extract(self.component_index, excluded=True))
            self.plotter.add_mesh(excluded, color='#dc2626', opacity=.5, pickable=False,
                                  name='excludedComponents', reset_camera=False, render=False)

    def change_selected_component(self):
        if not self.component_list.selectedItems():
            return
        index = self.component_list.currentRow()
        if index == self.component_index:
            return
        self.component_index = index
        self._initial_cells = ()
        self.normalized_direction = None
        self.selected = [None, None]
        self.result_surface = None
        self.confirm.setEnabled(False)
        self.geometry_error = ''
        try:
            self.surface = self.components.solid(index)
        except ValueError as error:
            self.surface = self.components.extract(index)
            self.geometry_error = str(error)
        for name in ('geometry', 'selection0', 'selection1', *PLATEN_NAMES):
            self.plotter.remove_actor(name, reset_camera=False, render=False)
        self.draw_components()
        self.set_role(0)
        self._restore_faces()
        self.plotter.render()

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
        self.status.setText(self.geometry_error or ('Selecting support face (right-click)' if role == 0
                                                   else 'Selecting compression face (right-click)'))

    def set_platens_visible(self, visible):
        for name in PLATEN_NAMES:
            actor = self.plotter.actors.get(name)
            if actor is not None:
                actor.SetVisibility(visible)
        self.plotter.render()

    def draw_platen(self, origin, normal):
        # Build in the selected face's coordinates, then return to the source view.
        points = self.surface.points - origin
        if (points @ normal).mean() > 0:
            normal = -normal
        right = np.cross(np.eye(3)[np.argmin(abs(normal))], normal)
        right /= np.linalg.norm(right)
        up = np.cross(normal, right)
        basis = np.column_stack((right, up, normal))
        footprint = points @ basis[:, :2]
        margin = self.surface.length * 0.04
        x_min, y_min = footprint.min(axis=0) - margin
        x_max, y_max = footprint.max(axis=0) + margin
        platen = pv.Box(bounds=(x_min, x_max, y_min, y_max, 0, 2 * margin))
        platen.points = platen.points @ basis.T + origin
        actor = self.plotter.add_mesh(platen, name=PLATEN_NAMES[self.role], color=PLATEN_COLORS[self.role],
                                     opacity=0.45, pickable=False, reset_camera=False, render=False)
        actor.SetVisibility(self.show_platens.isChecked())
        self.plotter.render()

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
        ids, origin, normal = plane_faces(self.surface, cell)
        self.selected[self.role] = cell
        actor = self.plotter.add_mesh(self.surface.extract_cells(ids), color=('#22c55e' if self.role == 0 else '#f97316'),
                                     name=f'selection{self.role}', pickable=False, lighting=False)
        actor.mapper.SetResolveCoincidentTopologyToPolygonOffset()
        actor.mapper.SetRelativeCoincidentTopologyPolygonOffsetParameters(-2, -2)
        if self.show_platens is not None:
            self.draw_platen(origin, normal)
        self.result_surface = None
        if self.geometry_error:
            if self.selected[1 - self.role] is None:
                self.set_role(1 - self.role)
            self.status.setText(self.geometry_error)
        elif None not in self.selected:
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
