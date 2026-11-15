"""Native resizable viewer for downloaded Kratos solution steps."""
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pyvista as pv
from studio_style import CompactComboBox
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QComboBox,QLabel,QPushButton
from studio_style import STUDIO_STYLESHEET,CompactDoubleSpinBox,CONTROL_HEIGHT
from preview_viewport import create_preview_viewport
from kratos_backend import KratosResults


class KratosResultsDialog(QDialog):
    def __init__(self,folder,parent=None):
        super().__init__(parent)
        self.setObjectName("kratosResultsDialog")
        self.setWindowTitle("RustMech · Kratos results")
        self.setStyleSheet(STUDIO_STYLESHEET)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose,True)
        self.resize(1000,700)
        self.setSizeGripEnabled(True)
        self.folder=Path(folder)
        report=json.loads((self.folder/"summary.json").read_text(encoding="utf-8"))
        raw=Path(report["solver_diagnostics"]["local_directory"])
        self.mesh=pv.read(raw/"submitted_mesh.vtu")
        self.reader=KratosResults(raw/"results",SimpleNamespace(grid=self.mesh))
        layout=QVBoxLayout(self)
        bar=QHBoxLayout()
        self.steps=CompactComboBox()
        for i,time in enumerate(self.reader.time_values):self.steps.addItem(f"Step {i+1} · load {time:.6g}",i)
        self.steps.setCurrentIndex(self.reader.nsets-1)
        self.field=CompactComboBox()
        self.field.addItems(["von Mises / MPa","Displacement / mm"])
        if self.reader.has_self_contact:
            self.field.addItems(['Self-contact pressure / MPa','Self-contact shear / MPa'])
            self.field.setToolTip('Self-contact fields show the maximum active slave-facet traction at each original solid node.')
        self.scale=CompactDoubleSpinBox()
        self.scale.setRange(0.,100.)
        self.scale.setDecimals(2)
        self.scale.setValue(1.)
        self.scale.setKeyboardTracking(False)
        open_folder=QPushButton("Open result folder")
        open_folder.setAutoDefault(False)
        open_folder.setDefault(False)
        open_folder.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder))))
        for widget in (self.steps,self.field,self.scale):widget.setFixedHeight(CONTROL_HEIGHT)
        bar.addWidget(self.steps)
        bar.addWidget(self.field)
        bar.addWidget(QLabel("Deformation ×"))
        bar.addWidget(self.scale)
        bar.addWidget(open_folder)
        layout.addLayout(bar)
        self.plotter=create_preview_viewport(self)
        self.plotter.set_background("#f8fafc")
        self.plotter.add_axes()
        layout.addWidget(self.plotter.interactor,1)
        self.status=QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.steps.currentIndexChanged.connect(self.render_result)
        self.field.currentIndexChanged.connect(self.render_result)
        self.scale.valueChanged.connect(self.render_result)
        self.render_result()
        self.plotter.view_isometric()
        self.plotter.reset_camera()

    def render_result(self,*_):
        try:
            self._render_result()
        except (OSError,ValueError,KeyError) as error:
            self.status.setText(f"Cannot display this result: {error}")

    def _render_result(self):
        index=self.steps.currentIndex()
        ids=self.mesh.point_data["ansys_node_num"]
        displacement=self.reader.displacement(index,ids)
        selected=self.field.currentIndex()
        field=(self.reader.equivalent_stress(index,ids) if selected==0 else
               np.linalg.norm(displacement,axis=1) if selected==1 else
               self.reader.contact_pressure(index,ids,tangential=selected==3))
        display=self.mesh.copy(deep=True)
        display.points=np.asarray(self.mesh.points)+displacement*self.scale.value()
        display.point_data["Result"]=field
        self.plotter.remove_actor("kratos_result",render=False)
        self.plotter.add_mesh(display,scalars="Result",name="kratos_result",cmap="viridis",show_edges=False,
                              scalar_bar_args={"title":self.field.currentText()})
        bottom,top=self.reader.platen_reactions(index)
        from standard_tests import kind,load_sign
        sign=load_sign(self.reader.settings)
        label='Compression' if kind(self.reader.settings)=='compression' else 'Actuator travel'
        self.status.setText(f"{label}: {sign*self.reader.top_platen_displacement(index):.6g} mm · Force: {sign*top:.6g} N · Imbalance: {abs(bottom+top):.3g} N\n{self.reader.stress_definition}")
        if self.reader.has_self_contact:
            state=self.reader.steps[index]['self_contact']
            self.status.setText(self.status.text()+f"\nSelf-contact: {state['active_nodes']} active nodes · Peak pressure: {state['maximum_pressure']:.6g} MPa")
        self.plotter.render()

    def keyPressEvent(self,event):
        if event.key() in (Qt.Key.Key_Enter,Qt.Key.Key_Return):event.accept()
        else:super().keyPressEvent(event)

    def closeEvent(self,event):
        self.plotter.close()
        super().closeEvent(event)
