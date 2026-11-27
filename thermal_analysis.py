"""Transient water/TPMS conjugate heat transfer using OpenCFD chtMultiRegionFoam.

Constant properties, laminar single-phase water, perfect thermal contact,
adiabatic exterior and zero gravity. Inputs use Celsius; OpenFOAM uses Kelvin/SI.
"""
from dataclasses import asdict, dataclass, replace
import csv
import json
import math
from pathlib import Path
import re
import shlex

import numpy as np
import pyvista as pv
from scipy import ndimage
from scipy.spatial import cKDTree

from flow_analysis import (FlowSettings as FlowSettings, build_flow_grid, hydraulic_metrics, sample_channel_solid,
                           write_poly_mesh, _header, _write, _wsl, linux_path)
from model import Settings as Settings


@dataclass(frozen=True)
class ThermalSettings:
    solid_material_card: dict | None = None
    inlet_temperature_c: float = 80.0
    initial_water_c: float = 20.0
    initial_solid_c: float = 20.0
    water_cp: float = 4182.0
    water_conductivity: float = 0.6
    solid_density: float = 7850.0
    solid_cp: float = 500.0
    solid_conductivity: float = 16.0
    duration_s: float = 5.0
    time_step_s: float = 0.01
    output_interval_s: float = 0.1
    outer_correctors: int = 8

    def validate(self, specimen, flow):
        flow.validate(specimen)
        if flow.mesh_method != "cartesian":
            raise ValueError("Fitted meshing is currently qualified for Flow only. Select Cartesian for CHT.")
        if flow.turbulence != "laminar":
            raise ValueError("The initial transient CHT implementation supports laminar flow only.")
        for name in ("inlet_temperature_c","initial_water_c","initial_solid_c"):
            value = getattr(self,name)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0 <= value <= 95:
                raise ValueError(f"{name}: use 0..95 °C for this single-phase, near-atmospheric-pressure water model.")
        for name in ("water_cp","water_conductivity","solid_density","solid_cp","solid_conductivity","duration_s","time_step_s","output_interval_s"):
            value = getattr(self,name)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive.")
        if not self.time_step_s <= self.output_interval_s <= self.duration_s:
            raise ValueError("Use time step ≤ output interval ≤ duration.")
        if math.ceil(self.duration_s/self.output_interval_s)>200:
            raise ValueError("Limit saved output to 200 intervals; increase the output interval.")
        if math.ceil(self.duration_s/self.time_step_s)>20000:
            raise ValueError("Requested maximum time step implies over 20,000 steps; increase it or shorten the run.")
        if isinstance(self.outer_correctors,bool) or not isinstance(self.outer_correctors,int) or not 2<=self.outer_correctors<=50:
            raise ValueError("Use 2..50 fluid/solid coupling correctors.")


def build_thermal_grid(specimen, flow):
    """A conformal full cylinder, with exactly the same TPMS phase as flow CFD."""
    _,flow_meta = build_flow_grid(specimen,flow)
    if flow_meta["isolated_cells"]:
        raise ValueError("This mesh has isolated water cavities. Refine/change the TPMS: disconnected fluid pressure regions are not supported by this CHT version.")
    grid,metadata = build_flow_grid(specimen,replace(flow,empty_pipe=True))
    centres = grid.cell_centers().points*1000
    solid = np.zeros(grid.n_cells,dtype=bool)
    inside = (centres[:,2]>0)&(centres[:,2]<flow.section_length(specimen))
    if not flow.empty_pipe:
        p = centres[inside]
        solid[inside], _ = sample_channel_solid(specimen, flow, p)
    grid.cell_data["Solid"] = solid.astype(np.uint8)
    metadata.update(flow_meta)
    metadata["total_cells"] = grid.n_cells
    ijk = np.column_stack([grid.cell_data[f"grid_{name}"] for name in "ijk"])
    phase = np.zeros(tuple(ijk.max(axis=0)+1),dtype=bool)
    phase[tuple(ijk[solid].T)] = True
    metadata["solid_components"] = int(ndimage.label(phase)[1])
    if metadata["solid_components"]>1:
        metadata["warnings"].append(f"The sampled solid has {metadata['solid_components']} disconnected components. Check morphology and refine thin walls before interpreting solid conduction.")
    return grid,metadata


def export_thermal_case(specimen, flow, thermal, directory):
    from analysis_process import report_analysis_step
    report_analysis_step("Building water and solid region meshes…")
    thermal.validate(specimen,flow)
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Choose a new or empty thermal case folder.")
    grid,metadata = build_thermal_grid(specimen,flow)
    report_analysis_step("Exporting thermal regions and case files…")
    directory.mkdir(parents=True,exist_ok=True)
    grid.save(directory/"thermal_mesh.vtu")
    write_poly_mesh(grid,directory/"constant/polyMesh")
    solid = grid.cell_data["Solid"].astype(bool)
    if not solid.any():
        # splitMeshRegions deliberately does nothing for a single cell zone.
        write_poly_mesh(grid,directory/"constant/water/polyMesh")
    zones = [("water",np.flatnonzero(~solid))]
    if solid.any(): zones.append(("tpms",np.flatnonzero(solid)))
    zone_text = _header("regIOobject","cellZones")+f"{len(zones)}\n(\n"
    for name,ids in zones:
        zone_text += f"{name} {{ type cellZone; cellLabels List<label> {len(ids)} (\n"+"\n".join(map(str,ids))+"\n); }\n"
    _write(directory/"constant/polyMesh/cellZones",zone_text+")\n")
    _write(directory/"constant/regionProperties",_header("dictionary","regionProperties")+f"regions ( fluid (water) solid ({'tpms' if solid.any() else ''}) );\n")
    _write(directory/"constant/g",_header("uniformDimensionedVectorField","g")+"dimensions [0 1 -2 0 0 0 0]; value (0 0 0);\n")
    pr = flow.dynamic_viscosity*thermal.water_cp/thermal.water_conductivity
    thermo = ("thermoType { type heRhoThermo; mixture pureMixture; transport const; thermo hConst; equationOfState rhoConst; specie specie; energy sensibleEnthalpy; }\n"
              f"mixture {{ specie {{ molWeight 18; }} equationOfState {{ rho {flow.density:.14g}; }} thermodynamics {{ Cp {thermal.water_cp:.14g}; Hf 0; }} transport {{ mu {flow.dynamic_viscosity:.14g}; Pr {pr:.14g}; }} }}\n")
    _write(directory/"constant/water/thermophysicalProperties",_header("dictionary","thermophysicalProperties")+thermo)
    _write(directory/"constant/water/turbulenceProperties",_header("dictionary","turbulenceProperties")+"simulationType laminar;\n")
    for name,_ in zones:
        _write(directory/f"constant/{name}/radiationProperties",_header("dictionary","radiationProperties")+"radiation off; radiationModel none;\n")
    if solid.any():
        _write(directory/"constant/tpms/thermophysicalProperties",_header("dictionary","thermophysicalProperties")+
            "thermoType { type heSolidThermo; mixture pureMixture; transport constIso; thermo hConst; equationOfState rhoConst; specie specie; energy sensibleEnthalpy; }\n"+
            f"mixture {{ specie {{ molWeight 50; }} equationOfState {{ rho {thermal.solid_density:.14g}; }} thermodynamics {{ Cp {thermal.solid_cp:.14g}; Hf 0; }} transport {{ kappa {thermal.solid_conductivity:.14g}; }} }}\n")
    def field(region,name,kind,dim,initial,patches):
        _write(directory/f"0/{region}/{name}",_header(kind,name)+f"dimensions [{dim}]; internalField uniform {initial}; boundaryField {{ {patches} }}\n")
    tw,ts,tin = (thermal.initial_water_c+273.15,thermal.initial_solid_c+273.15,thermal.inlet_temperature_c+273.15)
    velocity = f"(0 0 {flow.inlet_speed:.14g})"
    field("water","U","volVectorField","0 1 -1 0 0 0 0",velocity,
          f'".*" {{ type noSlip; }} inlet {{ type fixedValue; value uniform {velocity}; }} outlet {{ type pressureInletOutletVelocity; value uniform {velocity}; }}')
    field("water","p","volScalarField","1 -1 -2 0 0 0 0","100000",'".*" { type calculated; value uniform 100000; }')
    field("water","p_rgh","volScalarField","1 -1 -2 0 0 0 0","100000",'".*" { type fixedFluxPressure; value uniform 100000; } outlet { type fixedValue; value uniform 100000; }')
    field("water","alphat","volScalarField","1 -1 -1 0 0 0 0","0",'".*" { type calculated; value uniform 0; }')
    coupled = lambda region,temp: (f'type compressible::turbulentTemperatureCoupledBaffleMixed; Tnbr T; kappaMethod {"fluidThermo" if region=="water" else "solidThermo"}; value uniform {temp:.14g};')
    water_patches = f'".*" {{ type zeroGradient; }} inlet {{ type fixedValue; value uniform {tin:.14g}; }} outlet {{ type inletOutlet; inletValue uniform {tin:.14g}; value uniform {tw:.14g}; }}'
    if solid.any(): water_patches += f' water_to_tpms {{ {coupled("water",tw)} }}'
    field("water","T","volScalarField","0 0 0 1 0 0 0",f"{tw:.14g}",water_patches)
    if solid.any():
        field("tpms","T","volScalarField","0 0 0 1 0 0 0",f"{ts:.14g}",f'".*" {{ type zeroGradient; }} tpms_to_water {{ {coupled("tpms",ts)} }}')
        field("tpms","p","volScalarField","1 -1 -2 0 0 0 0","100000",'".*" { type calculated; value uniform 100000; }')
    common = "ddtSchemes { default Euler; } gradSchemes { default Gauss linear; } laplacianSchemes { default Gauss linear corrected; } interpolationSchemes { default linear; } snGradSchemes { default corrected; }\n"
    _write(directory/"system/water/fvSchemes",_header("dictionary","fvSchemes")+common+"divSchemes { default none; div(phi,U) Gauss upwind; div(phi,K) Gauss linear; div(phi,h) Gauss upwind; div(((rho*nuEff)*dev2(T(grad(U))))) Gauss linear; } wallDist { method meshWave; }\n")
    _write(directory/"system/water/fvSolution",_header("dictionary","fvSolution")+
        'solvers { "rho.*" { solver PCG; preconditioner DIC; tolerance 1e-9; relTol 0; } "p_rgh.*" { solver GAMG; tolerance 1e-9; relTol 0; smoother GaussSeidel; } "(U|h).*" { solver PBiCGStab; preconditioner DILU; tolerance 1e-9; relTol 0; } } PIMPLE { momentumPredictor yes; nCorrectors 2; nNonOrthogonalCorrectors 0; } relaxationFactors { equations { ".*" 1; } }\n')
    if solid.any():
        _write(directory/"system/tpms/fvSchemes",_header("dictionary","fvSchemes")+common+"divSchemes { default none; }\n")
        _write(directory/"system/tpms/fvSolution",_header("dictionary","fvSolution")+'solvers { "h.*" { solver PCG; preconditioner DIC; tolerance 1e-10; relTol 0; } } PIMPLE { nNonOrthogonalCorrectors 0; }\n')
    _write(directory/"system/fvSolution",_header("dictionary","fvSolution")+f"PIMPLE {{ nOuterCorrectors {thermal.outer_correctors}; }}\n")
    _write(directory/"system/fvSchemes",_header("dictionary","fvSchemes")+common+"divSchemes { default none; }\n")
    functions = ""
    for patch in ("inlet","outlet"):
        for op,name in (("areaAverage","T"),("areaAverage","p"),("sum","phi"),("weightedAverage","T")):
            suffix = "bulkT" if op=="weightedAverage" else name
            functions += f'{patch}_{suffix} {{ type surfaceFieldValue; libs ("libfieldFunctionObjects.so"); region water; writeControl timeStep; writeInterval 1; regionType patch; name {patch}; operation {op}; fields ({name}); weightField phi; writeFields false; }}\n'
    _write(directory/"system/controlDict",_header("dictionary","controlDict")+
        f'application chtMultiRegionFoam; startFrom startTime; startTime 0; stopAt endTime; endTime {thermal.duration_s:.14g}; deltaT {thermal.time_step_s:.14g}; adjustTimeStep yes; maxCo 0.5; maxDi 10; maxDeltaT {thermal.time_step_s:.14g}; writeControl adjustableRunTime; writeInterval {thermal.output_interval_s:.14g}; purgeWrite 0; writeFormat ascii; writePrecision 12; timePrecision 12; runTimeModifiable false; functions {{ {functions} }}\n')
    _write(directory/"case.foam","")
    _write(directory/"Allrun",'#!/bin/bash\nset -eo pipefail\necho $$ > .runner.pid\ntrap \'rm -f .runner.pid\' EXIT\ntest ! -f .cancel-request\nsplitMeshRegions -cellZonesOnly -overwrite > log.splitMeshRegions 2>&1\ncheckMesh -region water > log.checkMesh.water 2>&1\ngrep -q "Mesh OK" log.checkMesh.water\n'+
           ('checkMesh -region tpms > log.checkMesh.tpms 2>&1\ngrep -q "Mesh OK" log.checkMesh.tpms\n' if solid.any() else '')+
           'test ! -f .cancel-request\nchtMultiRegionFoam > log.chtMultiRegionFoam 2>&1\n')
    manifest = dict(specimen=asdict(specimen),flow=asdict(flow),thermal=asdict(thermal),mesh=metadata,
                    solver="chtMultiRegionFoam",regions=[name for name,_ in zones],
                    assumptions="Transient laminar single-phase water; constant properties; zero gravity; insulated exterior; perfect water/solid thermal contact; no pipe-wall thermal mass; no radiation or phase change.")
    (directory/"thermal_case.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    return manifest


def execute_thermal_case(specimen,flow,thermal,directory):
    from analysis_process import report_analysis_step
    report_analysis_step("Checking thermal solver runtime…")
    thermal.validate(specimen,flow)
    flow.validate_local_runtime()
    runtime = _wsl(flow,f"source {shlex.quote(flow.foam_bashrc)} >/dev/null 2>&1 && command -v chtMultiRegionFoam && command -v splitMeshRegions && command -v checkMesh",capture_output=True,timeout=30)
    if runtime.returncode: raise RuntimeError("OpenCFD chtMultiRegionFoam/splitMeshRegions/checkMesh unavailable in the selected WSL environment.")
    export_thermal_case(specimen,flow,thermal,directory)
    report_analysis_step("Splitting regions, checking mesh and solving heat transfer…")
    result = _wsl(flow,f"source {shlex.quote(flow.foam_bashrc)} >/dev/null 2>&1 && cd {shlex.quote(linux_path(directory))} && exec setsid --wait bash Allrun",capture_output=True)
    if result.returncode:
        logs = ""
        for name in ("log.splitMeshRegions","log.checkMesh.water","log.checkMesh.tpms","log.chtMultiRegionFoam"):
            path = Path(directory)/name
            if path.exists(): logs += "\n"+name+"\n"+path.read_text(errors="replace")[-2400:]
        raise RuntimeError("Transient CHT failed. "+logs[-7000:])
    report_analysis_step("Processing temperature history and thermal results…")
    return summarize_thermal_case(directory)


def _internal_regions(blocks):
    found = {}
    def visit(block,name=""):
        if isinstance(block,pv.MultiBlock):
            for key in block.keys(): visit(block[key],name+"/"+key)
        elif isinstance(block,pv.UnstructuredGrid) and "internalMesh" in name:
            for region in ("water","tpms"):
                if region in name.split("/"): found[region] = block
    visit(blocks)
    return found


def _monitor_rows(directory,name):
    paths = list((Path(directory)/"postProcessing"/"water"/name).glob("*/surfaceFieldValue.dat"))
    if not paths: raise RuntimeError(f"Missing thermal monitor: {name}")
    return np.array([[float(v) for v in line.split()] for line in paths[0].read_text().splitlines() if line.strip() and not line.lstrip().startswith("#")])


def _scalar_values(text,key,count):
    """Keep ASCII OpenFOAM scalar precision (VTK field arrays may be float32)."""
    match = re.search(r"\b"+re.escape(key)+r"\s+uniform\s+([-+\deE.]+)\s*;",text)
    if match: return np.full(count,float(match.group(1)))
    match = re.search(r"\b"+re.escape(key)+r"\s+nonuniform\s+List<scalar>\s+(\d+)\s*\((.*?)\)\s*;",text,re.DOTALL)
    if not match: raise RuntimeError(f"Missing ASCII scalar entry: {key}")
    values = np.fromstring(match.group(2),sep=" ")
    if len(values)!=count or int(match.group(1))!=count: raise RuntimeError("Scalar field and mesh cell counts differ.")
    return values


def _interface_diagnostics(blocks,regions,thermal):
    if "tpms" not in regions: return {}
    patches = {region:blocks[region]["boundary"][name] for region,name in
               (("water","water_to_tpms"),("tpms","tpms_to_water"))}
    heat,temps,centres,areas = {},{},{},{}
    for region,k in (("water",thermal.water_conductivity),("tpms",thermal.solid_conductivity)):
        patch,grid = patches[region],regions[region]
        centres[region] = patch.cell_centers().points
        distance,index = cKDTree(grid.cell_centers().points).query(centres[region])
        temps[region] = np.asarray(patch.cell_data["T"])
        areas[region] = patch.compute_cell_sizes(length=False,volume=False).cell_data["Area"]
        heat[region] = float(np.sum(k*(np.asarray(grid.cell_data["T"])[index]-temps[region])/distance*areas[region]))
    _,matching = cKDTree(centres["tpms"]).query(centres["water"])
    return dict(interface_water_to_solid_W=heat["water"],interface_solid_outward_W=heat["tpms"],
                interface_heat_mismatch_W=abs(heat["water"]+heat["tpms"]),
                interface_temperature_jump_K=float(np.max(np.abs(temps["water"]-temps["tpms"][matching]))),
                interface_area_m2=float(areas["water"].sum()),
                interface_wall_mean_C=float(np.average(temps["water"], weights=areas["water"]))-273.15)


def thermal_performance_metrics(row, thermal, flow):
    drop = row["inlet_bulk_C"] - row["outlet_bulk_C"]
    inlet_flow = row["inlet_mass_kg_s"] / flow.density
    values = hydraulic_metrics(row["pressure_drop_Pa"], inlet_flow)
    solved = row["time_s"] > 0
    values.update(water_bulk_temperature_drop_K=drop,
                  water_sensible_heat_loss_W=row["outlet_mass_kg_s"] * thermal.water_cp * drop if solved else None)
    if "interface_water_to_solid_W" in row:
        heat = row["interface_water_to_solid_W"]
        power = values["channel_hydraulic_power_W"]
        values["interface_heat_to_hydraulic_power_ratio"] = abs(heat) / power if solved and power > 0 else None
    if "interface_area_m2" in row and "interface_water_to_solid_W" in row:
        area = row["interface_area_m2"]
        bulk = (row["inlet_bulk_C"] + row["outlet_bulk_C"]) / 2
        difference = bulk - row["interface_wall_mean_C"]
        flux = heat / area if solved and area > 0 else None
        coefficient = flux / difference if flux is not None and abs(difference) > 1e-6 and flux * difference > 0 else None
        values.update(interface_mean_heat_flux_W_m2=flux, reference_bulk_temperature_C=bulk,
                      reference_bulk_to_wall_delta_K=difference, apparent_h_W_m2_K=coefficient,
                      apparent_Nusselt=coefficient * flow.hydraulic_diameter_mm / (1000 * thermal.water_conductivity)
                      if coefficient is not None else None,
                      apparent_thermal_resistance_K_W=1 / (coefficient * area) if coefficient is not None else None)
    return values


def summarize_thermal_case(directory):
    directory = Path(directory)
    manifest = json.loads((directory/"thermal_case.json").read_text())
    log = (directory/"log.chtMultiRegionFoam").read_text(errors="replace")
    if not re.search(r"^End\s*$",log,re.MULTILINE): raise RuntimeError("CHT did not end cleanly.")
    reader = pv.OpenFOAMReader(directory/"case.foam")
    reader.reader.Use64BitFloatsOn()
    reader.disable_all_patch_arrays()
    for region in manifest["regions"]:
        reader.enable_patch_array(f"/{region}/internalMesh")
    if "tpms" in manifest["regions"]:
        reader.enable_patch_array("/water/patch/water_to_tpms")
        reader.enable_patch_array("/tpms/patch/tpms_to_water")
    times = sorted(reader.time_values)
    time_folders = {float(p.name):p for p in directory.iterdir() if p.is_dir() and re.fullmatch(r"[\deE+.\-]+",p.name)}
    thermal = ThermalSettings(**manifest["thermal"])
    flow = FlowSettings(**manifest["flow"])
    if max(times)<thermal.duration_s*(1-1e-7): raise RuntimeError("CHT did not reach the requested physical duration.")
    frames = directory/"frames"
    frames.mkdir(exist_ok=True)
    monitors = {name:_monitor_rows(directory,name) for name in ("inlet_bulkT","outlet_bulkT","inlet_phi","outlet_phi","inlet_p","outlet_p")}
    history,entries = [],[]
    for index,time in enumerate(times):
        reader.set_active_time_value(time)
        blocks = reader.read()
        regions = _internal_regions(blocks)
        if "water" not in regions: raise RuntimeError("Cannot locate the water region in OpenFOAM output.")
        for region,grid in regions.items():
            text = (time_folders[time]/region/"T").read_text()
            grid.cell_data["T"] = _scalar_values(text,"internalField",grid.n_cells)
            if "tpms" in regions:
                patch_name = "water_to_tpms" if region=="water" else "tpms_to_water"
                patch = blocks[region]["boundary"][patch_name]
                section = re.search(r"\b"+patch_name+r"\s*\{(.*?)\}",text,re.DOTALL)
                patch.cell_data["T"] = _scalar_values(section.group(1),"value",patch.n_cells)
        row = {"time_s":float(time)}
        row.update(_interface_diagnostics(blocks,regions,thermal))
        entry = {"time_s":float(time)}
        for region,grid in regions.items():
            temp = np.asarray(grid.cell_data["T"],dtype=float)-273.15
            if not np.isfinite(temp).all(): raise RuntimeError("Non-finite transient temperature.")
            grid.cell_data["Temperature_C"] = temp
            if region=="water":
                grid.cell_data["Speed_m_s"] = np.linalg.norm(grid.cell_data["U"],axis=1)
                grid.cell_data["Pressure_Pa"] = _scalar_values((time_folders[time]/region/"p").read_text(),"internalField",grid.n_cells)-100000
            volume = np.asarray(grid.compute_cell_sizes(length=False,area=False).cell_data["Volume"])
            row[f"{region}_mean_C"] = float(np.average(temp,weights=volume))
            row[f"{region}_min_C"],row[f"{region}_max_C"] = float(temp.min()),float(temp.max())
            initial = thermal.initial_water_c if region=="water" else thermal.initial_solid_c
            rho = manifest["flow"]["density"] if region=="water" else thermal.solid_density
            cp = thermal.water_cp if region=="water" else thermal.solid_cp
            row[f"{region}_stored_energy_J"] = float(np.dot(volume,temp-initial)*rho*cp)
            filename = f"frames/{index:04d}_{region}.vtu"
            grid.save(directory/filename)
            entry[region] = filename
        def at(name):
            data = monitors[name]
            return float(np.interp(time,data[:,0],data[:,1]))
        row["inlet_bulk_C"] = thermal.inlet_temperature_c if time==0 else at("inlet_bulkT")-273.15
        row["outlet_bulk_C"] = thermal.initial_water_c if time==0 else at("outlet_bulkT")-273.15
        row["inlet_mass_kg_s"],row["outlet_mass_kg_s"] = -at("inlet_phi"),at("outlet_phi")
        row["pressure_drop_Pa"] = 0. if time==0 else at("inlet_p")-at("outlet_p")
        row.update(thermal_performance_metrics(row, thermal, flow))
        history.append(row)
        entries.append(entry)
    with (directory/"temperature_history.csv").open("w",newline="") as stream:
        writer = csv.DictWriter(stream,fieldnames=list(history[0]))
        writer.writeheader()
        writer.writerows(history)
    report = dict(status="completed",duration_s=max(times),frames=entries,history=history,
                  final_performance=thermal_performance_metrics(history[-1], thermal, flow),
                  performance_reference="Apparent h uses the water-side interface area and the difference between mean inlet/outlet bulk temperature and area-weighted interface temperature. Nusselt uses the channel hydraulic diameter.",
                  warnings=manifest["mesh"]["warnings"]+[
                      "Constant thermophysical properties: temperature does not change density or viscosity. No buoyancy, boiling, external heat loss or pipe thermal mass.",
                      "Transient results require mesh, time-step and fluid/solid coupling refinement checks; clean solver completion is not an accuracy guarantee."],
                  assumptions=manifest["assumptions"])
    mass_error = max(abs(r["inlet_mass_kg_s"]-r["outlet_mass_kg_s"])/max(abs(r["inlet_mass_kg_s"]),abs(r["outlet_mass_kg_s"]),1e-30) for r in history[1:])
    report["maximum_mass_imbalance_fraction"] = mass_error
    if mass_error>.01:
        report["status"] = "completed_with_warnings"
        report["warnings"].append("Water mass-flow imbalance exceeds 1%; reduce time step and check pressure/coupling convergence.")
    if "tpms" in manifest["regions"]:
        active = [r for r in history[1:] if abs(r["interface_water_to_solid_W"])+abs(r["interface_solid_outward_W"])>1e-4]
        mismatch = max((r["interface_heat_mismatch_W"]/(abs(r["interface_water_to_solid_W"])+abs(r["interface_solid_outward_W"])) for r in active),default=0.)
        jump = max((r["interface_temperature_jump_K"] for r in history[1:]),default=0.)
        report["maximum_interface_relative_heat_mismatch"] = mismatch
        report["maximum_interface_temperature_jump_K"] = jump
        if mismatch>.01 or jump>.1:
            report["status"] = "completed_with_warnings"
            report["warnings"].append("Fluid/solid interface imbalance exceeds 1% or temperature jump exceeds 0.1 K. Increase coupling correctors and/or reduce the time step.")
    (directory/"thermal_summary.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    return report
