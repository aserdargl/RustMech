"""Transient thermal controls and time-resolved water/solid result playback."""
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import uuid

import numpy as np
import pyvista as pv
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import (QFileDialog,QFormLayout,QHBoxLayout,QLabel,QPushButton,
                              QSlider,QSpinBox,QSplitter,QWidget)

from analysis_process import AnalysisProcess
from flow_workspace import FlowWorkspace
from model import ROOT
from studio_style import CompactDoubleSpinBox,configure_form,field_label,CONTROL_HEIGHT
from thermal_analysis import ThermalSettings,execute_thermal_case,export_thermal_case


class ThermalWorkspace(FlowWorkspace):
    SETTINGS_KEY = "thermal/flowSettings"
    LOG_NAME = "log.chtMultiRegionFoam"
    ITERATION_LABEL = "CHT coupling iterations"

    def __init__(self,settings_provider,parent=None,preferences=None,*,viewport_factory=None):
        self.thermal_controls = {}
        self.thermal_report = None
        self.solid_grid = None
        super().__init__(settings_provider,parent,preferences,viewport_factory=viewport_factory)
        self.setObjectName("thermalWorkspace")
        self.splitter.setObjectName("thermalWorkspaceSplitter")
        self.panel.layout().itemAt(0).widget().setText("Transient water + TPMS heat transfer")
        self.panel.layout().itemAt(1).widget().setText("Hot water heats the initially cooler TPMS. Insulated channel exterior; perfect water–solid thermal contact. Flow +Z. Temperatures: °C; time: s.")
        self.input_panel.layout().setRowVisible(self.controls["iterations"],False)
        self.model.setCurrentIndex(0)
        self.model.setEnabled(False)
        self.model.setToolTip("This initial transient CHT model supports laminar flow.")
        self.thermal_panel = QWidget()
        form = QFormLayout(self.thermal_panel)
        form.setContentsMargins(0,0,0,0)
        configure_form(form)
        defaults = ThermalSettings()
        try:
            saved = json.loads(self.preferences.value("thermal/settings","{}"))
            defaults = ThermalSettings(**{**asdict(defaults),**{k:v for k,v in saved.items() if k in asdict(defaults)}})
        except (ValueError,TypeError,AttributeError): pass
        for name,label,lo,hi in (
            ("inlet_temperature_c","Inlet water / °C",0,95),
            ("initial_water_c","Initial water / °C",0,95),
            ("initial_solid_c","Initial solid / °C",0,95),
            ("water_cp","Water cp / J/(kg·K)",1,100000),
            ("water_conductivity","Water k / W/(m·K)",.0001,10000),
            ("solid_density","Solid ρ / kg/m³",.01,100000),
            ("solid_cp","Solid cp / J/(kg·K)",1,100000),
            ("solid_conductivity","Solid k / W/(m·K)",.0001,10000),
            ("duration_s","Duration / s",.000001,100000),
            ("time_step_s","Max time step / s",.000001,1000),
            ("output_interval_s","Save interval / s",.000001,10000),
            ("outer_correctors","Coupling correctors",2,50)):
            control = QSpinBox() if name=="outer_correctors" else CompactDoubleSpinBox()
            if isinstance(control,CompactDoubleSpinBox): control.setDecimals(6)
            control.setRange(lo,hi)
            control.setValue(getattr(defaults,name))
            control.setKeyboardTracking(False)
            control.setFixedHeight(CONTROL_HEIGHT)
            self.thermal_controls[name] = control
            control.valueChanged.connect(self.inputs_changed)
            form.addRow(field_label(label,control),control)
        self.thermal_controls["outer_correctors"].setToolTip("Fluid/solid coupling exchanges per time step. Increase this or reduce the time step if the reported interface heat imbalance is high.")
        self.thermal_controls["time_step_s"].setToolTip("Maximum physical time step. OpenFOAM may reduce it to satisfy Courant and diffusion limits. Compare a smaller step to check temporal accuracy.")
        self.thermal_controls["output_interval_s"].setToolTip("Physical time between saved 3D frames; this does not set the solver's time step.")
        self.panel.layout().insertWidget(3,self.thermal_panel)
        material_note = QLabel("Thermal properties are independent of the mechanical material. Initial values are a steel-like example; enter the actual material data. Water properties are constant: set them for your experiment.")
        material_note.setObjectName("mutedHintLabel")
        material_note.setWordWrap(True)
        self.panel.layout().insertWidget(4,material_note)
        self.buttons[0].setText("Build thermal regions / export case")
        self.buttons[1].setText("Run transient heat transfer")
        self.buttons[2].setText("Open saved thermal case")
        self.stop.setText("Stop heat transfer analysis")
        # Replace the inherited isothermal note without changing shared styling.
        self.panel.layout().itemAt(self.panel.layout().count()-2).widget().setText("Laminar, single-phase water; constant properties; no buoyancy or external heat loss. Cartesian stair-step surfaces. Check mesh, time-step and coupling refinement. The simulation starts with flowing water and the specified initial temperatures.")
        self.view.clear()
        self.view.addItems(("Water temperature · slice","TPMS temperature · cutaway","Water + TPMS temperatures","Water speed · slice","Water pressure · slice"))
        timebar = QHBoxLayout()
        self.play = QPushButton("Play")
        self.play.setAutoDefault(False)
        self.play.setDefault(False)
        self.play.setObjectName("secondaryActionBtn")
        self.play.clicked.connect(self.toggle_play)
        self.time_slider = QSlider(Qt.Orientation.Horizontal)
        self.time_slider.setRange(0,0)
        self.time_slider.valueChanged.connect(self.show_frame)
        self.time_label = QLabel("t = 0 s")
        timebar.addWidget(self.play)
        timebar.addWidget(self.time_slider,1)
        timebar.addWidget(self.time_label)
        self.right_layout.insertLayout(1,timebar)
        self.figure = Figure(figsize=(6,1.8),layout="constrained")
        self.figure.set_facecolor("#f8fafc")
        self.chart = FigureCanvasQTAgg(self.figure)
        self.chart.setMinimumHeight(140)
        self.chart.setMaximumHeight(500)
        self.chart_splitter = QSplitter(Qt.Orientation.Vertical)
        self.chart_splitter.setObjectName("thermalTemperatureChartSplitter")
        self.right_layout.removeWidget(self.plotter.interactor)
        self.chart_splitter.addWidget(self.plotter.interactor)
        self.chart_splitter.addWidget(self.chart)
        self.chart_splitter.setStretchFactor(0,1)
        self.chart_splitter.setStretchFactor(1,0)
        self.chart_splitter.setSizes([600,180])
        self.right_layout.insertWidget(2,self.chart_splitter,1)
        self.play_timer = QTimer(self)
        self.play_timer.setInterval(180)
        self.play_timer.timeout.connect(self.advance)
        self.results.setText("Set the inlet and initial temperatures. Build the water/solid regions, then explicitly run the transient analysis.")
        self.inputs_changed()

    def collect_thermal(self):
        return ThermalSettings(**{key:control.value() for key,control in self.thermal_controls.items()})

    def inputs_changed(self,*_):
        super().inputs_changed(*_)
        if len(self.thermal_controls)==len(ThermalSettings.__dataclass_fields__):
            thermal = self.collect_thermal()
            self.preferences.setValue("thermal/settings",json.dumps(asdict(thermal)))
            self.preferences.sync()

    def _start(self,solve):
        if self.job is not None: return
        try:
            specimen,flow,thermal = self.settings_provider(),self.collect(),self.collect_thermal()
            thermal.validate(specimen,flow)
            directory = ROOT/"results"/("Thermal_"+datetime.now().strftime("%Y%m%d_%H%M%S_")+uuid.uuid4().hex[:8])
            self.case_directory,self.submitted_flow = directory,flow
            self.solving = solve
            controller = getattr(self,"remote_controller",None)
            self._remote_job = bool(controller is not None and controller.requested)
            if self._remote_job:
                from truba_remote import run_remote_operation
                context,auth = controller.require("thermal" if solve else "mesh")
                request = dict(operation="thermal_solve" if solve else "thermal_export",settings=asdict(specimen),flow=asdict(flow),thermal=asdict(thermal))
                self.job = AnalysisProcess(run_remote_operation,request,context,remote_auth=auth)
            else:
                self.job = AnalysisProcess(execute_thermal_case if solve else export_thermal_case,specimen,flow,thermal,str(directory))
        except Exception as error:
            self.status.setText(str(error))
            return
        self.play_timer.stop()
        self.play.setText("Play")
        self.input_panel.setEnabled(False)
        self.thermal_panel.setEnabled(False)
        for button in self.buttons: button.setEnabled(False)
        self.stop.setEnabled(True)
        self.status.setText("Building water/solid regions and starting transient CHT…" if solve else "Building water/solid regions…")
        self.timer.start()

    def _unlock(self):
        super()._unlock()
        self.thermal_panel.setEnabled(True)

    def open_case(self):
        filename,_ = QFileDialog.getOpenFileName(self,"Open thermal case",str(ROOT/"results"),"Thermal case (thermal_case.json)")
        if filename:
            try: self.load_directory(Path(filename).parent)
            except Exception as error: self.status.setText("Cannot open thermal case: "+str(error))

    def load_directory(self,directory):
        self.play_timer.stop()
        self.play.setText("Play")
        directory = Path(directory)
        manifest = json.loads((directory/"thermal_case.json").read_text())
        self.display_directory,self.manifest = directory,manifest
        for key,control in self.controls.items(): control.setValue(manifest["flow"][key])
        self.empty.setChecked(manifest["flow"]["empty_pipe"])
        self.distribution.setText(manifest["flow"]["distribution"])
        self.bashrc.setText(manifest["flow"]["foam_bashrc"])
        for key,control in self.thermal_controls.items(): control.setValue(manifest["thermal"][key])
        self.inputs_changed()
        self.case_label.setText(f"Saved thermal case: {manifest['specimen']['structure_name']} · Ø {manifest['flow']['diameter_mm']:g} mm · inlet {manifest['thermal']['inlet_temperature_c']:g} °C")
        summary = directory/"thermal_summary.json"
        self.thermal_report = json.loads(summary.read_text()) if summary.exists() else None
        if self.thermal_report:
            self.time_slider.setRange(0,len(self.thermal_report["frames"])-1)
            self.draw_history()
            self.view.setCurrentIndex(2 if "tpms" in manifest["regions"] else 0)
            self.time_slider.setValue(self.time_slider.maximum())
            self.show_frame(self.time_slider.maximum())
            self.status.setText("Transient results and temperature_history.csv saved. "+str(directory))
        else:
            self.time_slider.setRange(0,0)
            self.grid = pv.read(directory/"thermal_mesh.vtu")
            self.solid_grid = self.grid.extract_cells(self.grid.cell_data["Solid"]==1)
            self.grid = self.grid.extract_cells(self.grid.cell_data["Solid"]==0)
            self.grid.cell_data["Temperature_C"] = np.full(self.grid.n_cells,manifest["thermal"]["initial_water_c"])
            if self.solid_grid.n_cells: self.solid_grid.cell_data["Temperature_C"] = np.full(self.solid_grid.n_cells,manifest["thermal"]["initial_solid_c"])
            self.figure.clear()
            self.chart.draw_idle()
            self.render()
            self.results.setText("Thermal mesh exported. Initial temperatures shown; no transient solve performed. "+manifest["assumptions"])
            self.status.setText(str(directory))
        self.plotter.view_isometric()
        self.plotter.reset_camera()

    def draw_history(self):
        self.figure.clear()
        axis = self.figure.add_subplot(111)
        axis.set_facecolor("#f8fafc")
        rows = self.thermal_report["history"]
        times = [r["time_s"] for r in rows]
        for key,label,color in (("water_mean_C","Water mean","#1687c8"),("outlet_bulk_C","Outlet water","#12986b"),("tpms_mean_C","TPMS mean","#d67b22")):
            if key in rows[0]: axis.plot(times,[r[key] for r in rows],label=label,color=color,linewidth=1.5)
        if "tpms_min_C" in rows[0]:
            axis.fill_between(times,[r["tpms_min_C"] for r in rows],[r["tpms_max_C"] for r in rows],color="#d67b22",alpha=.12,label="TPMS min–max")
        axis.axhline(self.manifest["thermal"]["inlet_temperature_c"],color="#cc4455",linestyle=":",label="Inlet water")
        self.time_cursor = axis.axvline(0,color="#526174",linewidth=.8)
        axis.set_xlabel("Time / s",fontsize=9)
        axis.set_ylabel("Temperature / °C",fontsize=9)
        axis.tick_params(labelsize=8)
        axis.grid(alpha=.2)
        axis.legend(fontsize=8,loc="best",ncol=4)
        self.chart.draw_idle()

    def show_frame(self,index):
        if not self.thermal_report: return
        try:
            frame = self.thermal_report["frames"][index]
            self.grid = pv.read(self.display_directory/frame["water"])
            self.solid_grid = pv.read(self.display_directory/frame["tpms"]) if "tpms" in frame else None
            row = self.thermal_report["history"][index]
            self.time_label.setText(f"t = {frame['time_s']:.6g} s")
            solid = f" · TPMS: {row['tpms_mean_C']:.4g} °C [{row['tpms_min_C']:.4g}, {row['tpms_max_C']:.4g}]" if "tpms_mean_C" in row else ""
            coupling = f"Interface heat imbalance: {row['interface_heat_mismatch_W']:.3g} W · interface ΔT: {row['interface_temperature_jump_K']:.3g} K.\n" if "interface_heat_mismatch_W" in row else ""
            geometry = self.manifest["mesh"]
            self.results.setText(f"{self.thermal_report['status'].upper()} · t = {frame['time_s']:.6g} s · water mean: {row['water_mean_C']:.4g} °C · outlet: {row['outlet_bulk_C']:.4g} °C{solid}\n"
                f"Static Δp: {row['pressure_drop_Pa']:.5g} Pa. "+coupling+
                f"Sampled solid: {geometry['specimen_solid_fraction']*100:.4g}% · solid components: {geometry.get('solid_components','—')}. Check mesh, time-step and coupling refinement.\nConstant properties; insulated exterior; no boiling or buoyancy."+
                ("\n"+self.thermal_report["warnings"][-1] if self.thermal_report["status"]!="completed" else ""))
            if hasattr(self,"time_cursor"):
                self.time_cursor.set_xdata([frame["time_s"]]*2)
                self.chart.draw_idle()
            self.render()
        except Exception as error:
            self.status.setText("Cannot display thermal time step: "+str(error))
            self.play_timer.stop()
            self.play.setText("Play")

    def render(self,*_):
        if self.grid is None or not hasattr(self,"manifest"): return
        try:
            self.plotter.clear()
            self.plotter.add_axes()
            view = self.view.currentIndex()
            thermal = self.manifest["thermal"]
            limits = [min(thermal[k] for k in ("inlet_temperature_c","initial_water_c","initial_solid_c")),max(thermal[k] for k in ("inlet_temperature_c","initial_water_c","initial_solid_c"))]
            if limits[0]==limits[1]: limits[1]+=1
            regions = [("water",self.grid)] if view in (0,3,4) else [("tpms",self.solid_grid)] if view==1 else [("water",self.grid),("tpms",self.solid_grid)]
            for name,source in regions:
                if source is None or not source.n_cells: continue
                grid = source.copy()
                grid.points *= 1000
                display = grid.slice(normal="x",origin=np.asarray(self.grid.center)*1000) if view in (0,3,4) else grid.clip(normal="x",origin=np.asarray(self.grid.center)*1000)
                field = "Speed_m_s" if view==3 else "Pressure_Pa" if view==4 else "Temperature_C"
                if field not in display.cell_data: continue
                self.plotter.add_mesh(display,scalars=field,cmap="turbo" if view<3 else "viridis",clim=limits if view<3 else None,
                                      opacity=.65 if view==2 and name=="water" else 1,
                                      scalar_bar_args={"title":"Temperature / °C" if view<3 else "Speed / m/s" if view==3 else "Gauge pressure / Pa"})
            self.plotter.render()
        except Exception as error: self.status.setText("Cannot render thermal field: "+str(error))

    def toggle_play(self):
        if self.play_timer.isActive():
            self.play_timer.stop()
            self.play.setText("Play")
        elif self.thermal_report:
            if self.time_slider.value()==self.time_slider.maximum(): self.time_slider.setValue(0)
            self.play_timer.start()
            self.play.setText("Pause")

    def advance(self):
        if self.time_slider.value()>=self.time_slider.maximum():
            self.play_timer.stop()
            self.play.setText("Play")
        else: self.time_slider.setValue(self.time_slider.value()+1)

    def dispose(self):
        self.play_timer.stop()
        super().dispose()
