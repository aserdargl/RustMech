"""Compact cylindrical-channel CFD workspace using the shared Datenburg controls."""
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import uuid

import numpy as np
import pyvista as pv
from studio_style import CompactComboBox
from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout,
                              QLabel, QLineEdit, QPushButton, QScrollArea,
                              QSpinBox, QSplitter, QVBoxLayout, QWidget)

from analysis_process import AnalysisProcess
from flow_analysis import FlowSettings, export_flow_case, execute_flow_case, stop_flow_case
from model import ROOT
from preview_viewport import create_preview_viewport
from studio_style import CompactDoubleSpinBox, CONTROL_HEIGHT, configure_form, field_label


class FlowWorkspace(QWidget):
    SETTINGS_KEY = "flow/settings"
    LOG_NAME = "log.simpleFoam"
    ITERATION_LABEL = "SIMPLE iterations"
    def __init__(self, settings_provider, parent=None, preferences=None, *, viewport_factory=None):
        super().__init__(parent)
        self.setObjectName("flowWorkspace")
        self.settings_provider = settings_provider
        self.preferences = preferences if preferences is not None else QSettings("RustMech", "RustMech")
        self.job = None
        self.case_directory = None
        self.display_directory = None
        self.grid = None
        self.submitted_flow = None
        self.controls = {}
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.poll)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4,2,4,2)
        self.splitter = QSplitter()
        self.splitter.setObjectName("cylindricalFlowSplitter")
        self.splitter.setHandleWidth(3)
        layout.addWidget(self.splitter)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(255)
        self.panel = QWidget()
        self.panel.setObjectName("sidebarPanel")
        left = QVBoxLayout(self.panel)
        left.setContentsMargins(6,4,6,4)
        left.setSpacing(3)
        heading = QLabel("Cylindrical channel · Isothermal CFD")
        heading.setWordWrap(True)
        left.addWidget(heading)
        note = QLabel("Uses the current Model TPMS, clipped to a concentric cylinder. Flow is along +Z. Specimen length = Model cube size. Geometry: mm; water: SI units.")
        note.setObjectName("mutedHintLabel")
        note.setWordWrap(True)
        left.addWidget(note)
        self.input_panel = QWidget()
        form = QFormLayout(self.input_panel)
        form.setContentsMargins(0,0,0,0)
        configure_form(form)
        defaults = FlowSettings(diameter_mm=settings_provider().size)
        try:
            saved = json.loads(self.preferences.value(self.SETTINGS_KEY, "{}"))
            defaults = FlowSettings(**{**asdict(defaults),**{key:value for key,value in saved.items() if key in asdict(defaults)}})
        except (ValueError,TypeError,AttributeError):
            pass
        for name,label,lo,hi in (
            ("diameter_mm","Diameter / mm",.01,10000),
            ("inlet_length_mm","Inlet extension / mm",.01,100000),
            ("outlet_length_mm","Outlet extension / mm",.01,100000),
            ("inlet_speed","Inlet speed / m/s",.000001,100),
            ("density","Density / kg/m³",.001,100000),
            ("dynamic_viscosity","Viscosity / Pa·s",.00000001,1000),
            ("cells_across","Cells / diameter",8,128),
            ("iterations","Max iterations",20,20000)):
            control = QSpinBox() if name in ("cells_across","iterations") else CompactDoubleSpinBox()
            if isinstance(control,CompactDoubleSpinBox): control.setDecimals(8)
            control.setRange(lo,hi)
            control.setValue(getattr(defaults,name))
            control.setKeyboardTracking(False)
            control.setFixedHeight(CONTROL_HEIGHT)
            self.controls[name] = control
            control.valueChanged.connect(self.inputs_changed)
            form.addRow(field_label(label,control),control)
        self.model = CompactComboBox()
        self.model.addItem("Laminar","laminar")
        self.model.addItem("Turbulent · k–ω SST","kOmegaSST")
        self.model.setCurrentIndex(max(0,self.model.findData(defaults.turbulence)))
        self.model.currentIndexChanged.connect(self.inputs_changed)
        form.addRow(field_label("Flow model",self.model),self.model)
        self.empty = QCheckBox("Empty pipe reference")
        self.empty.setChecked(defaults.empty_pipe)
        self.empty.toggled.connect(self.inputs_changed)
        form.addRow(self.empty)
        self.distribution = QLineEdit(defaults.distribution)
        self.bashrc = QLineEdit(defaults.foam_bashrc)
        self.bashrc.setToolTip("Absolute Linux path to the OpenCFD OpenFOAM environment (simpleFoam).")
        form.addRow(field_label("WSL distribution",self.distribution),self.distribution)
        form.addRow(field_label("OpenFOAM bashrc",self.bashrc),self.bashrc)
        self.distribution.editingFinished.connect(self.inputs_changed)
        self.bashrc.editingFinished.connect(self.inputs_changed)
        left.addWidget(self.input_panel)
        self.info = QLabel()
        self.info.setWordWrap(True)
        left.addWidget(self.info)
        self.buttons = []
        for title,slot in (("Build channel / export case",self.export), ("Run flow analysis",self.run),
                           ("Open saved flow case",self.open_case),("Open case folder",self.open_folder)):
            button = QPushButton(title)
            button.setAutoDefault(False)
            button.setDefault(False)
            button.setObjectName("secondaryActionBtn")
            button.clicked.connect(slot)
            left.addWidget(button)
            self.buttons.append(button)
        self.stop = QPushButton("Stop flow analysis")
        self.stop.setAutoDefault(False)
        self.stop.setDefault(False)
        self.stop.setObjectName("secondaryActionBtn")
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.cancel)
        left.addWidget(self.stop)
        warning = QLabel("The first CFD mesh approximates cylinder and TPMS surfaces with Cartesian steps. Refine and compare pressure loss before using results. SST uses wall functions; near-wall resolution needs separate validation. Temperature coupling will be added later.")
        warning.setObjectName("mutedHintLabel")
        warning.setWordWrap(True)
        left.addWidget(warning)
        left.addStretch()
        scroll.setWidget(self.panel)
        self.splitter.addWidget(scroll)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.right_layout = right_layout
        right_layout.setContentsMargins(2,0,0,0)
        right_layout.setSpacing(2)
        bar = QHBoxLayout()
        self.view = CompactComboBox()
        self.view.addItems(("Fluid domain cutaway","Speed · central slice","Pressure · central slice","Speed · volume cutaway","Pressure · volume cutaway","Streamlines"))
        self.view.currentIndexChanged.connect(self.render)
        bar.addWidget(self.view)
        self.case_label = QLabel("No flow case yet")
        self.case_label.setWordWrap(True)
        bar.addWidget(self.case_label,1)
        reset = QPushButton("Reset view")
        reset.setAutoDefault(False)
        reset.setDefault(False)
        reset.setObjectName("secondaryActionBtn")
        reset.clicked.connect(self.reset_view)
        bar.addWidget(reset)
        right_layout.addLayout(bar)
        self.plotter = (viewport_factory or create_preview_viewport)(self)
        self.plotter.set_background("#f8fafc")
        right_layout.addWidget(self.plotter.interactor,1)
        self.results = QLabel("Build the channel to inspect the water domain; run explicitly to solve velocity and pressure.")
        self.results.setWordWrap(True)
        self.results.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        right_layout.addWidget(self.results)
        self.status = QLabel("Ready")
        self.status.setWordWrap(True)
        right_layout.addWidget(self.status)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(1,1)
        self.splitter.setSizes([300,1000])
        self.inputs_changed()

    def collect(self):
        return FlowSettings(**{key:control.value() for key,control in self.controls.items()},
                            turbulence=self.model.currentData(),empty_pipe=self.empty.isChecked(),
                            distribution=self.distribution.text().strip(),foam_bashrc=self.bashrc.text().strip())

    def inputs_changed(self,*_):
        if not hasattr(self,"info"): return
        flow = self.collect()
        self.preferences.setValue(self.SETTINGS_KEY,json.dumps(asdict(flow)))
        self.preferences.sync()
        self.info.setText(f"Pipe Reynolds ≈ {flow.reynolds:.5g}\nNominal cell: {flow.diameter_mm/flow.cells_across:.5g} mm. Inlet velocity is the mean over the discretized pipe inlet; pore velocity is solved.")
        if self.display_directory:
            self.status.setText("Inputs changed. The displayed case keeps its saved geometry and flow conditions; build/run to apply changes.")

    def _start(self,solve):
        if self.job is not None: return
        try:
            specimen,flow = self.settings_provider(),self.collect()
            flow.validate(specimen)
            directory = ROOT/"results"/("Flow_"+datetime.now().strftime("%Y%m%d_%H%M%S_")+uuid.uuid4().hex[:8])
            self.case_directory = directory
            self.submitted_flow = flow
            self.solving = solve
            controller = getattr(self,"remote_controller",None)
            self._remote_job = bool(controller is not None and controller.requested)
            if self._remote_job:
                from truba_remote import run_remote_operation
                context,auth = controller.require("flow" if solve else "mesh")
                request = dict(operation="flow_solve" if solve else "flow_export",settings=asdict(specimen),flow=asdict(flow))
                self.job = AnalysisProcess(run_remote_operation,request,context,remote_auth=auth)
            else:
                self.job = AnalysisProcess(execute_flow_case if solve else export_flow_case,specimen,flow,str(directory))
        except Exception as error:
            self.status.setText(str(error))
            return
        self.input_panel.setEnabled(False)
        for button in self.buttons: button.setEnabled(False)
        self.stop.setEnabled(True)
        self.status.setText("Checking OpenFOAM and building the channel…" if solve else "Building fluid mesh and exporting OpenFOAM case…")
        self.timer.start()

    def export(self): self._start(False)
    def run(self): self._start(True)

    def _unlock(self):
        self.timer.stop()
        self.input_panel.setEnabled(True)
        for button in self.buttons: button.setEnabled(True)
        self.stop.setEnabled(False)
        self.update_execution_availability()

    def update_execution_availability(self):
        controller = getattr(self,"remote_controller",None)
        if self.job is not None: return
        remote = bool(controller is not None and controller.requested)
        for index,capability in ((0,"mesh"),(1,"thermal" if hasattr(self,"thermal_controls") else "flow")):
            available = not remote or (controller.ready and controller.capabilities.get(capability,False))
            self.buttons[index].setEnabled(available)
            self.buttons[index].setToolTip("" if available else "TRUBA runtime/solver is not verified as available. Check capabilities in the TRUBA panel; local fallback is disabled.")
        self.distribution.setEnabled(not remote)
        self.bashrc.setEnabled(not remote)

    def poll(self):
        if self.job is None: return
        if not self.job.done():
            if getattr(self,"_remote_job",False):
                record = self.job.remote_status()
                if record: self.status.setText(f"TRUBA job {record.get('job_id','pending')} · {record.get('state','preparing')}")
                return
            path = self.case_directory/self.LOG_NAME
            if path.exists():
                with path.open("rb") as stream:
                    stream.seek(max(0,path.stat().st_size-2000))
                    lines = stream.read().decode(errors="replace").splitlines()
                iterations = [line for line in lines if line.startswith("Time =")]
                self.status.setText("Solving · "+(iterations[-1] if iterations else self.ITERATION_LABEL)+" · logs in case folder")
            return
        job,self.job = self.job,None
        self._unlock()
        try:
            result = job.result()
            if getattr(self,"_remote_job",False): self.case_directory = Path(result)
            self.load_directory(self.case_directory)
        except Exception as error:
            self.status.setText("Flow operation failed. "+str(error)[-1800:])

    def cancel(self):
        if self.job is None: return
        try:
            if not getattr(self,"_remote_job",False): stop_flow_case(self.submitted_flow,self.case_directory)
            self.job.force_stop()
        except Exception as error:
            self.status.setText("Cannot stop flow job: "+str(error))
            return
        self.job = None
        self._unlock()
        self.status.setText("Flow operation stopped. Incomplete case files and logs are retained.")

    def open_case(self):
        filename,_ = QFileDialog.getOpenFileName(self,"Open flow case",str(ROOT/"results"),"Flow case (flow_case.json)")
        if filename:
            try: self.load_directory(Path(filename).parent)
            except Exception as error: self.status.setText("Cannot open case: "+str(error))

    def open_folder(self):
        directory = self.display_directory or self.case_directory
        if directory: QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def load_directory(self,directory):
        directory = Path(directory)
        manifest = json.loads((directory/"flow_case.json").read_text())
        result = directory/"flow_result.vtu"
        self.grid = pv.read(result if result.exists() else directory/"fluid_mesh.vtu")
        self.display_directory = directory
        self.manifest = manifest
        spec,flow = manifest["specimen"],manifest["flow"]
        for key,control in self.controls.items():
            control.setValue(flow[key])
        self.model.setCurrentIndex(self.model.findData(flow["turbulence"]))
        self.empty.setChecked(flow["empty_pipe"])
        self.distribution.setText(flow["distribution"])
        self.bashrc.setText(flow["foam_bashrc"])
        self.inputs_changed()
        self.case_label.setText(f"Saved case: {'Empty pipe' if flow['empty_pipe'] else spec['structure_name']} · Ø {flow['diameter_mm']:g} × {spec['size']:g} mm · {flow['inlet_speed']:g} m/s · {flow['turbulence']}")
        self.case_label.setToolTip("Displayed results use this saved case. A new run uses the current Model workspace geometry and the flow inputs at left.")
        summary_path = directory/"flow_summary.json"
        if summary_path.exists():
            summary = json.loads(summary_path.read_text())
            self.results.setText(f"{summary['status'].upper()} · Re = {summary['reynolds_diameter']:.5g} · {self.grid.n_cells:,} fluid cells\n"
                f"Static Δp, channel: {summary['pressure_drop_total_Pa']:.6g} Pa · TPMS section: {summary['pressure_drop_specimen_Pa']:.6g} Pa\n"
                f"Q inlet / outlet: {summary['inlet_flow_m3_s']*6e7:.6g} / {summary['outlet_flow_m3_s']*6e7:.6g} mL/min · mass imbalance: {summary['mass_imbalance_fraction']*100:.4g}%\n"
                f"Maximum speed: {summary['maximum_speed_m_s']:.6g} m/s · sampled cylinder solid: {summary['mesh']['specimen_solid_fraction']*100:.4g}%\n"
                f"TPMS pressure planes: {summary['specimen_pressure_planes_mm'][0]:.5g} / {summary['specimen_pressure_planes_mm'][1]:.5g} mm (includes entrance/exit effects).\n"+
                "\n".join(summary["warnings"]))
            self.status.setText("Results and axial_profile.csv saved. "+str(directory))
            self.view.setCurrentIndex(1)
        else:
            mesh = manifest["mesh"]
            self.results.setText(f"{mesh['fluid_cells']:,} fluid cells · actual inlet-equivalent diameter: {mesh['effective_diameter_mm']:.5g} mm\nSampled cylinder solid: {mesh['specimen_solid_fraction']*100:.4g}% · isolated cavity cells excluded: {mesh['isolated_cells']}\n"+"\n".join(mesh["warnings"]))
            self.status.setText("Case exported; no CFD solve performed. "+str(directory))
            self.view.setCurrentIndex(0)
        self.render()
        self.plotter.view_isometric()
        self.plotter.reset_camera()

    def render(self,*_):
        if self.grid is None: return
        try:
            self.plotter.clear()
            self.plotter.add_axes()
            grid = self.grid.copy()
            grid.points *= 1000
            centre = np.asarray(grid.center)
            diameter = self.manifest["flow"]["diameter_mm"]
            specimen = self.manifest["specimen"]
            # Mark the specimen interval even when long entrance/exit sections dominate the view.
            angles = np.linspace(0,2*np.pi,65)
            for z in (0,specimen["size"]):
                points = np.column_stack((specimen["size"]/2+diameter/2*np.cos(angles),
                                          specimen["size"]/2+diameter/2*np.sin(angles),np.full_like(angles,z)))
                self.plotter.add_mesh(pv.lines_from_points(points),color="#c47b28",line_width=2)
            view = self.view.currentIndex()
            has_result = "Speed_m_s" in grid.cell_data
            if view==0 or not has_result:
                self.plotter.add_mesh(grid.clip(normal="x",origin=centre),color="#8aafc8",show_edges=True,edge_color="#526174",opacity=.5)
            else:
                scalar = "Pressure_Pa" if view in (2,4) else "Speed_m_s"
                label = "Pressure / Pa" if view in (2,4) else "Speed / m/s"
                if view==5:
                    # Trace in SI coordinates so integration lengths remain physical.
                    data = self.grid.cell_data_to_point_data()
                    z = data.bounds[4]+self.manifest["mesh"]["nominal_cell_mm"]*.001
                    d = self.manifest["flow"]["diameter_mm"]*.001
                    c = data.center
                    seeds = pv.Disc(center=(c[0],c[1],z),inner=0,outer=d*.4,normal=(0,0,1),r_res=3,c_res=16)
                    traces = data.streamlines_from_source(seeds,vectors="Velocity_m_s",integration_direction="forward",max_length=data.length*5)
                    traces.points *= 1000
                    if traces.n_points:
                        self.plotter.add_mesh(traces,scalars="Speed_m_s",line_width=2,cmap="viridis",scalar_bar_args={"title":"Speed / m/s"})
                    self.plotter.add_mesh(grid.outline(),color="#526174")
                else:
                    display = grid.slice(normal="x",origin=centre) if view in (1,2) else grid.clip(normal="x",origin=centre)
                    self.plotter.add_mesh(display,scalars=scalar,cmap="viridis",scalar_bar_args={"title":label})
            self.plotter.render()
        except Exception as error:
            self.status.setText("Cannot render flow field: "+str(error))

    def reset_view(self):
        self.plotter.view_isometric()
        self.plotter.reset_camera()

    def dispose(self):
        self.cancel()
        self.preferences.sync()
        self.plotter.close()
