"""Compact cylindrical-channel CFD workspace using the shared Datenburg controls."""
from dataclasses import asdict, replace
from datetime import datetime
import json
from pathlib import Path
import uuid

import numpy as np
import pyvista as pv
from studio_style import CompactSpinBox, CompactComboBox
from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout,
                              QLabel, QLineEdit, QPushButton, QScrollArea,
                              QSplitter, QVBoxLayout, QWidget, QSizePolicy)

from analysis_process import AnalysisProcess
from file_dialog import QFileDialog
from flow_analysis import FlowSettings, export_flow_case, export_geometry_inspection, execute_flow_case, stop_flow_case
from model import ROOT
from preview_viewport import create_preview_viewport
from studio_style import ActionFlowLayout, CompactDoubleSpinBox, CONTROL_HEIGHT, configure_form, field_label


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
        heading = QLabel("Channel · Isothermal CFD")
        heading.setWordWrap(True)
        left.addWidget(heading)
        note = QLabel("Uses the current Model specimen in a cylindrical or rectangular channel. Flow is along +Z. Section length 0 uses the Model cube size. Placement rotates the finite specimen without stretching it. Geometry: mm; fluid: SI units.")
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
        self.fluid_material_card = defaults.fluid_material_card
        self.channel_shape = CompactComboBox()
        self.channel_shape.addItem("Cylinder", "cylinder")
        self.channel_shape.addItem("Square / rectangular", "rectangular")
        self.channel_shape.setCurrentIndex(max(0, self.channel_shape.findData(defaults.channel_shape)))
        self.channel_shape.currentIndexChanged.connect(self.inputs_changed)
        form.addRow(field_label("Channel section", self.channel_shape), self.channel_shape)
        self.mesh_method = CompactComboBox()
        self.mesh_method.addItem("Cartesian", "cartesian")
        self.mesh_method.addItem("Fitted · OpenFOAM", "snappy")
        self.mesh_method.setFixedWidth(160)
        self.mesh_method.setCurrentIndex(max(0,self.mesh_method.findData(defaults.mesh_method)))
        self.mesh_method.currentIndexChanged.connect(self.inputs_changed)
        form.addRow(field_label("Fluid mesh",self.mesh_method),self.mesh_method)
        for name,label,lo,hi in (
            ("diameter_mm","Diameter / mm",.01,10000),
            ("width_mm","Width / mm",.01,10000),
            ("height_mm","Height / mm",.01,10000),
            ("section_length_mm","Insert section / mm",0,100000),
            ("inlet_length_mm","Inlet extension / mm",.01,100000),
            ("outlet_length_mm","Outlet extension / mm",.01,100000),
            ("specimen_offset_x_mm","Specimen X / mm",-100000,100000),
            ("specimen_offset_y_mm","Specimen Y / mm",-100000,100000),
            ("specimen_offset_z_mm","Specimen Z / mm",-100000,100000),
            ("specimen_rotation_x_deg","Rotation X / °",-360,360),
            ("specimen_rotation_y_deg","Rotation Y / °",-360,360),
            ("specimen_rotation_z_deg","Rotation Z / °",-360,360),
            ("inlet_speed","Inlet speed / m/s",.000001,100),
            ("density","Density / kg/m³",.001,100000),
            ("dynamic_viscosity","Viscosity / Pa·s",.00000001,1000),
            ("cells_across","Cells / narrow side",8,128),
            ("iterations","Max iterations",20,20000),
            ("surface_refinement","Surface refinement",0,4),
            ("boundary_layers","Wall layers",0,8),
            ("max_fitted_cells","Fitted cell budget",1000,10000000)):
            control = CompactSpinBox() if name in ("cells_across","iterations","surface_refinement","boundary_layers","max_fitted_cells") else CompactDoubleSpinBox()
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
        self.allow_bypass = QCheckBox("Allow intentional bypass")
        self.allow_bypass.setToolTip("Allow channel cells outside the placed specimen envelope. Gaps around the specimen can strongly change pressure drop and heat transfer.")
        self.allow_bypass.setChecked(defaults.allow_bypass)
        self.allow_bypass.toggled.connect(self.inputs_changed)
        form.addRow(self.allow_bypass)
        self.load_fluid_button = QPushButton("Load fluid material...")
        self.load_fluid_button.setAutoDefault(False)
        self.load_fluid_button.setDefault(False)
        self.load_fluid_button.setObjectName("secondaryActionBtn")
        self.load_fluid_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.load_fluid_button.clicked.connect(self.load_fluid_material)
        fluid_actions = ActionFlowLayout(equal_width=True)
        self.fluid_library_button = QPushButton("Library")
        self.fluid_library_button.setAutoDefault(False)
        self.fluid_library_button.setDefault(False)
        self.fluid_library_button.setObjectName("secondaryActionBtn")
        self.fluid_library_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        material_action_width = max(self.fontMetrics().horizontalAdvance(label) for label in
                                    ("Load fluid material...", "Akışkan malzemesi yükle...")) + 24
        for button in (self.load_fluid_button, self.fluid_library_button):
            button.setFixedSize(material_action_width, CONTROL_HEIGHT)
        self.fluid_library_button.clicked.connect(self.show_fluid_library)
        fluid_actions.addWidget(self.load_fluid_button)
        fluid_actions.addWidget(self.fluid_library_button)
        form.addRow(fluid_actions)
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
        self.inspect_button = QPushButton("Inspect geometry (no solve)")
        self.inspect_button.setAutoDefault(False)
        self.inspect_button.setDefault(False)
        self.inspect_button.setObjectName("secondaryActionBtn")
        self.inspect_button.clicked.connect(lambda: self._start(False,inspection=True))
        left.addWidget(self.inspect_button)
        warning = QLabel("Channel and specimen surfaces use Cartesian steps. Refine and compare pressure loss before using results. SST uses wall functions; near-wall resolution needs separate validation. Use Thermal (CHT) for coupled heat transfer.")
        self.mesh_warning = warning
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
        self.view.addItems(("Fluid domain cutaway","Speed · central slice","Pressure · central slice","Speed · volume cutaway","Pressure · volume cutaway","Streamlines","Geometry diagnostics"))
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
        from operation_progress import OperationProgress
        self.operation_progress = OperationProgress(self, provider=lambda: (
            self.job is not None, self.status.text(),
        ))
        layout.addWidget(self.operation_progress)
        self.inputs_changed()

    def collect(self):
        return FlowSettings(**{key:control.value() for key,control in self.controls.items()},
                            mesh_method=self.mesh_method.currentData(),
                            fluid_material_card=self.fluid_material_card,
                            channel_shape=self.channel_shape.currentData(),allow_bypass=self.allow_bypass.isChecked(),
                            turbulence=self.model.currentData(),empty_pipe=self.empty.isChecked(),
                            distribution=self.distribution.text().strip(),foam_bashrc=self.bashrc.text().strip())

    def load_fluid_material(self):
        filename, _ = QFileDialog.getOpenFileName(self,"Load fluid material",str(ROOT/"materials"),"Material card (*.json *.csv)")
        if filename:
            try:
                self.load_fluid_material_path(filename)
            except (OSError, ValueError, TypeError) as error:
                self.status.setText(str(error))

    def load_fluid_material_path(self, path):
        from material_library import read_material_card
        self.apply_fluid_card(read_material_card(path,kind="fluid"))

    def show_fluid_library(self):
        from material_library_dialog import MaterialLibraryDialog
        from window_state import show_restored_window
        if not hasattr(self, "fluid_library_dialog"):
            self.fluid_library_dialog = MaterialLibraryDialog(self, kind="fluid",
                required=("density", "dynamic_viscosity", "heat_capacity", "conductivity"))
            self.fluid_library_dialog.setObjectName("fluidMaterialLibrary")
            self.fluid_library_dialog.material_added.connect(self.apply_fluid_library_card)
        show_restored_window(self.fluid_library_dialog)

    def apply_fluid_library_card(self, card):
        try:
            self.apply_fluid_card(card)
        except (ValueError, TypeError) as error:
            self.status.setText(str(error))

    def apply_fluid_card(self, card):
        if card.kind != "fluid":
            raise ValueError("Select a fluid material card.")
        thermal = getattr(self,"thermal_controls",{})
        temperature = thermal["inlet_temperature_c"].value()+273.15 if thermal else 293.15
        required = ("density","dynamic_viscosity","heat_capacity","conductivity") if thermal else ("density","dynamic_viscosity")
        values = card.evaluate(temperature,required)
        assignments = [(self.controls[key], values[key]) for key in ("density","dynamic_viscosity")]
        if thermal:
            assignments += [(thermal["water_cp"], values["heat_capacity"]),
                            (thermal["water_conductivity"], values["conductivity"])]
        for control, value in assignments:
            if not control.minimum() <= value <= control.maximum():
                raise ValueError("Material value is outside the supported input range.")
        for control, value in assignments:
            control.setValue(value)
        self.fluid_material_card = card.to_dict()
        self.inputs_changed()
        self.status.setText(f"{card.name}: constant properties evaluated at {temperature:g} K. Source tables retained in the case.")

    def inputs_changed(self,*_):
        if not hasattr(self,"info"): return
        flow = self.collect()
        self.preferences.setValue(self.SETTINGS_KEY,json.dumps(asdict(flow)))
        self.preferences.sync()
        if hasattr(self,"inspect_button"):
            self.size_action_buttons()
        form = self.input_panel.layout()
        for key in ("surface_refinement","boundary_layers","max_fitted_cells"):
            form.setRowVisible(self.controls[key],flow.mesh_method=="snappy")
        form.setRowVisible(self.controls["diameter_mm"], flow.channel_shape == "cylinder")
        for key in ("width_mm", "height_mm"):
            form.setRowVisible(self.controls[key], flow.channel_shape == "rectangular")
        self.info.setText(f"Re (hydraulic diameter) ≈ {flow.reynolds:.5g}\nDh: {flow.hydraulic_diameter_mm:.5g} mm · nominal cell: {flow.cell_size_mm:.5g} mm. Inlet velocity is the mean over the discretized inlet; pore velocity is solved.")
        if hasattr(self,"mesh_warning") and not hasattr(self,"thermal_controls"):
            self.mesh_warning.setText("Fitted OpenFOAM mesh. Surface refinement and wall layers may be limited by local geometry; review actual coverage and y+ in results. Mesh independence is required."
                                      if flow.mesh_method=="snappy" else "Cartesian stair-step surfaces. Refine and compare pressure loss; SST near-wall resolution needs separate validation.")
        if self.display_directory:
            self.status.setText("Inputs changed. The displayed case keeps its saved geometry and flow conditions; build/run to apply changes.")

    def size_action_buttons(self):
        buttons = [*self.buttons,self.stop,self.inspect_button]
        width = max(button.fontMetrics().horizontalAdvance(button.text()) for button in buttons)+24
        for button in buttons:
            button.setFixedSize(width,CONTROL_HEIGHT)

    def _start(self,solve,*,inspection=False):
        if self.job is not None: return
        try:
            specimen,flow = self.settings_provider(),self.collect()
            (replace(flow,allow_bypass=True) if inspection else flow).validate(specimen)
            directory = ROOT/"results"/("Flow_"+datetime.now().strftime("%Y%m%d_%H%M%S_")+uuid.uuid4().hex[:8])
            self.case_directory = directory
            self.submitted_flow = flow
            self.solving = solve
            controller = getattr(self,"remote_controller",None)
            self._remote_job = bool(controller is not None and controller.requested)
            if self._remote_job:
                from truba_remote import run_remote_operation
                context,auth = controller.require_analysis("flow") if solve else controller.require("mesh")
                request = dict(operation="flow_solve" if solve else "flow_export",settings=asdict(specimen),flow=asdict(flow))
                if inspection:
                    request["inspection"] = True
                self.job = AnalysisProcess(run_remote_operation,request,context,remote_auth=auth)
                if solve:
                    controller.window.truba_job_monitor.start_submission()
            else:
                target = export_geometry_inspection if inspection else execute_flow_case if solve else export_flow_case
                self.job = AnalysisProcess(target,specimen,flow,str(directory))
        except Exception as error:
            self.status.setText(str(error))
            return
        self.input_panel.setEnabled(False)
        for button in self.buttons: button.setEnabled(False)
        self.stop.setEnabled(True)
        self.inspect_button.setEnabled(False)
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
        self.inspect_button.setEnabled(not remote or (controller.ready and controller.capabilities.get("mesh",False)))
        for index,capability in ((0,"mesh"),(1,"thermal" if hasattr(self,"thermal_controls") else "flow")):
            available = not remote or (controller.analysis_solver_enabled(capability) if index
                                       else controller.solver_enabled(capability))
            self.buttons[index].setEnabled(available)
            self.buttons[index].setToolTip("" if available else
                "Connect to TRUBA and check solver availability." if index else
                "A ready TRUBA resource session is required for this mesh operation.")
        self.distribution.setEnabled(not remote)
        self.bashrc.setEnabled(not remote)

    def poll(self):
        if self.job is None: return
        if not self.job.done():
            if getattr(self,"_remote_job",False):
                record = self.job.remote_status()
                if record: self.status.setText(f"TRUBA job {record.get('job_id','pending')} · {record.get('state','preparing')}")
                return
            stage = self.job.progress_status() if hasattr(self.job, "progress_status") else None
            if stage:
                self.status.setText(stage)
            path = self.case_directory/self.LOG_NAME
            if path.exists() and (not stage or stage.startswith(("Checking mesh and solving", "Splitting regions"))):
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
            if self._remote_job and self.solving:
                self.remote_controller.window.truba_job_monitor.submission_failed(error)
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
        spec,flow = manifest["specimen"],asdict(FlowSettings(**manifest["flow"]))
        self.channel_shape.setCurrentIndex(self.channel_shape.findData(flow["channel_shape"]))
        self.mesh_method.setCurrentIndex(self.mesh_method.findData(flow["mesh_method"]))
        self.allow_bypass.setChecked(flow["allow_bypass"])
        self.fluid_material_card = flow["fluid_material_card"]
        for key,control in self.controls.items():
            control.setValue(flow[key])
        self.model.setCurrentIndex(self.model.findData(flow["turbulence"]))
        self.empty.setChecked(flow["empty_pipe"])
        self.distribution.setText(flow["distribution"])
        self.bashrc.setText(flow["foam_bashrc"])
        self.inputs_changed()
        config = FlowSettings(**flow)
        name = spec.get("imported_geometry_name") or (Path(spec["imported_geometry_path"]).name if spec.get("imported_geometry_path") else spec["structure_name"])
        self.case_label.setText(f"Saved case: {'Empty channel' if flow['empty_pipe'] else name} · {config.section_label} · {flow['inlet_speed']:g} m/s · {flow['turbulence']}")
        self.case_label.setToolTip("Displayed results use this saved case. A new run uses the current Model workspace geometry and the flow inputs at left.")
        summary_path = directory/"flow_summary.json"
        if summary_path.exists():
            summary = json.loads(summary_path.read_text())
            self.results.setText(f"{summary['status'].upper()} · Re = {summary['reynolds_diameter']:.5g} · {self.grid.n_cells:,} fluid cells\n"
                f"Static Δp, channel: {summary['pressure_drop_total_Pa']:.6g} Pa · Insert section: {summary['pressure_drop_specimen_Pa']:.6g} Pa\n"
                f"Q inlet / outlet: {summary['inlet_flow_m3_s']*6e7:.6g} / {summary['outlet_flow_m3_s']*6e7:.6g} mL/min · mass imbalance: {summary['mass_imbalance_fraction']*100:.4g}%\n"
                f"Maximum speed: {summary['maximum_speed_m_s']:.6g} m/s · Cartesian precheck solid fraction: {summary['mesh']['specimen_solid_fraction']*100:.4g}%\n"
                f"Insert pressure planes: {summary['specimen_pressure_planes_mm'][0]:.5g} / {summary['specimen_pressure_planes_mm'][1]:.5g} mm (includes entrance/exit effects).\n"+
                "\n".join(summary["warnings"]))
            self.status.setText("Results and axial_profile.csv saved. "+str(directory))
            if summary.get("mesh",{}).get("method")=="snappyHexMesh":
                layers = summary["mesh"].get("layers",{})
                fraction = layers.get("face_coverage_fraction")
                text = f"\nFitted layer face coverage: {fraction*100:.3g}%" if fraction is not None else "\nFitted mesh: no wall layers requested."
                for name,patch in layers.get("patches",{}).items():
                    text += f" · {name}: {patch['average_layers']:g}/{patch['requested_layers']} average layers"
                for name,values in summary.get("y_plus",{}).items():
                    text += f"\ny+ {name}: min {values['minimum']:.4g}, max {values['maximum']:.4g}, average {values['average']:.4g}"
                self.results.setText(self.results.text()+text)
            self.view.setCurrentIndex(1)
        else:
            mesh = manifest["mesh"]
            self.results.setText(f"{mesh['fluid_cells']:,} fluid cells · actual inlet-equivalent diameter: {mesh['effective_diameter_mm']:.5g} mm\nCartesian precheck solid fraction: {mesh['specimen_solid_fraction']*100:.4g}% · isolated cavity cells excluded: {mesh['isolated_cells']}\n"+"\n".join(mesh["warnings"]))
            self.status.setText("Case exported; no CFD solve performed. "+str(directory))
            self.view.setCurrentIndex(0)
        if manifest.get("inspection_only"):
            mesh = manifest["mesh"]
            audit = mesh.get("surface_audit")
            audit_text = ""
            if audit:
                check = audit["intersection"]
                audit_text = f"Surface intersections: {check['status']} · intersecting pairs: {check['intersecting_pairs']} · degenerate triangles: {check['degenerate_triangles']}\n"
                chords = audit.get("chords")
                if chords:
                    for kind in ("wall","pore"):
                        value = chords[kind+"_minimum_sampled_mm"]
                        if value is not None:
                            audit_text += f"Sampled {kind} minimum normal chord: {value:.5g} mm ({chords[kind+'_minimum_cells']:.3g} cells)\n"
                    audit_text += f"Sampled faces: {chords['sampled_faces']}/{chords['total_faces']}; this is not a global minimum thickness proof.\n"
            self.results.setText(f"GEOMETRY INSPECTION · flow ready: {mesh['geometry_valid_for_flow']} · CHT ready: {mesh['geometry_valid_for_cht']}\n"
                                 f"Solid components: {mesh['solid_components']} · isolated fluid components: {mesh['isolated_fluid_components']}\n"+
                                 "Blue: regular fluid · Orange: outside specimen envelope · Red: isolated fluid\n"+
                                 audit_text+
                                 "\n".join(mesh["geometry_errors"]+mesh["warnings"]))
            self.view.setCurrentIndex(6)
            self.status.setText("Geometry inspection saved. No solver files were generated.")
        self.render()
        self.plotter.view_isometric()
        self.plotter.reset_camera()

    def render(self,*_):
        if self.grid is None: return
        try:
            self.plotter.clear()
            self.plotter.add_axes()
            view = self.view.currentIndex()
            source = self.grid
            if view == 6 and "GeometryIssue" not in source.cell_data:
                path = self.display_directory/"fluid_mesh.vtu" if self.display_directory else None
                if path is not None and path.exists():
                    source = pv.read(path)
                if "GeometryIssue" not in source.cell_data:
                    self.status.setText("Rebuild or inspect this case to obtain geometry diagnostics.")
                    return
            grid = source.copy()
            grid.points *= 1000
            centre = np.asarray(grid.center)
            config = FlowSettings(**self.manifest["flow"])
            width, height = config.cross_section
            specimen = self.manifest["specimen"]
            # Mark the specimen interval even when long entrance/exit sections dominate the view.
            angles = np.linspace(0,2*np.pi,65)
            for z in (0,self.manifest["mesh"].get("specimen_end_mm",specimen["size"])):
                if config.channel_shape == "cylinder":
                    points = np.column_stack((specimen["size"]/2+width/2*np.cos(angles),
                                              specimen["size"]/2+height/2*np.sin(angles),np.full_like(angles,z)))
                else:
                    points = np.array([[specimen["size"]/2+sx*width/2,specimen["size"]/2+sy*height/2,z]
                                       for sx,sy in ((-1,-1),(1,-1),(1,1),(-1,1),(-1,-1))])
                self.plotter.add_mesh(pv.lines_from_points(points),color="#c47b28",line_width=2)
            has_result = "Speed_m_s" in grid.cell_data
            if view==6 and "GeometryIssue" in grid.cell_data:
                self.plotter.add_mesh(grid,scalars="GeometryIssue",cmap=["#93c5fd","#f59e0b","#dc2626"],
                                      clim=(0,2),opacity=.65,show_scalar_bar=False)
                markers = self.manifest["mesh"].get("isolated_component_markers",[])
                if markers:
                    self.plotter.add_mesh(pv.PolyData(np.asarray([m["centre_mm"] for m in markers])),color="#dc2626",point_size=12,render_points_as_spheres=True)
                audit = self.manifest["mesh"].get("surface_audit")
                if audit:
                    audit_path = self.display_directory/"surface_audit.vtp"
                    if audit_path.exists():
                        surface = pv.read(audit_path)
                        if np.any(surface.cell_data["SurfaceIssue"]):
                            self.plotter.add_mesh(surface.extract_cells(surface.cell_data["SurfaceIssue"]>0),color="#a855f7",opacity=.9)
                    chord_markers = (audit.get("chords") or {}).get("markers",[])
                    if chord_markers:
                        self.plotter.add_mesh(pv.PolyData(np.asarray([m["centre_mm"] for m in chord_markers])),color="#facc15",point_size=9,render_points_as_spheres=True)
            elif view==0 or not has_result:
                self.plotter.add_mesh(grid.clip(normal="x",origin=centre),color="#8aafc8",show_edges=True,edge_color="#526174",opacity=.5)
            else:
                scalar = "Pressure_Pa" if view in (2,4) else "Speed_m_s"
                label = "Pressure / Pa" if view in (2,4) else "Speed / m/s"
                if view==5:
                    # Trace in SI coordinates so integration lengths remain physical.
                    data = self.grid.cell_data_to_point_data()
                    z = data.bounds[4]+self.manifest["mesh"]["nominal_cell_mm"]*.001
                    config = FlowSettings(**self.manifest["flow"])
                    d = min(config.cross_section)*.001
                    c = data.center
                    seeds = pv.Disc(center=(c[0],c[1],z),inner=0,outer=d*.4,normal=(0,0,1),r_res=3,c_res=16)
                    traces = data.streamlines_from_source(seeds,vectors="Velocity_m_s",integration_direction="forward",max_length=data.length*5)
                    traces.points *= 1000
                    if traces.n_points:
                        from ui_language import translate
                        self.plotter.add_mesh(traces,scalars="Speed_m_s",line_width=2,cmap="viridis",scalar_bar_args={"title":translate("Speed / m/s")})
                    self.plotter.add_mesh(grid.outline(),color="#526174")
                else:
                    display = grid.slice(normal="x",origin=centre) if view in (1,2) else grid.clip(normal="x",origin=centre)
                    from ui_language import translate
                    self.plotter.add_mesh(display,scalars=scalar,cmap="viridis",scalar_bar_args={"title":translate(label)})
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
