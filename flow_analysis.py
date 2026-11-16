"""Isothermal, incompressible OpenFOAM CFD, separate from solid mechanics.

Geometry is in mm at the UI boundary; all OpenFOAM quantities are SI.
The first mesh is explicitly a stair-step, body-fitted Cartesian approximation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import re
import shlex
import subprocess

import numpy as np
import pyvista as pv
from scipy import ndimage

from model import Settings
from mesh_engine import geometry_threshold
from structure_library import evaluate_priority_field


@dataclass(frozen=True)
class FlowSettings:
    diameter_mm: float = 6.0
    inlet_length_mm: float = 12.0
    outlet_length_mm: float = 18.0
    inlet_speed: float = 0.01
    density: float = 998.2
    dynamic_viscosity: float = 0.001003
    cells_across: int = 32
    iterations: int = 1000
    turbulence: str = "laminar"
    empty_pipe: bool = False
    distribution: str = "Ubuntu"
    foam_bashrc: str = "/usr/lib/openfoam/openfoam2606/etc/bashrc"

    @property
    def reynolds(self):
        return self.density * self.inlet_speed * self.diameter_mm * 0.001 / self.dynamic_viscosity

    def validate(self, specimen: Settings):
        if specimen.test_profile != 'custom':
            raise ValueError('Standard test profiles define mechanical fixtures. Select Özel deney / Custom experiment for flow and thermal analysis.')
        if specimen.imported_geometry_path:
            raise ValueError('Imported geometry is currently supported in mechanical compression. Use a generated structure for flow and thermal analysis.')
        for name in ("size", "cell_size", "solid_fraction"):
            value = getattr(specimen,name)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"Specimen {name} must be finite and positive.")
        if specimen.solid_fraction >= 100 or specimen.cell_size > specimen.size:
            raise ValueError("Select a solid fraction below 100% and a TPMS period no larger than the specimen.")
        for name in ("diameter_mm", "inlet_length_mm", "outlet_length_mm", "inlet_speed", "density", "dynamic_viscosity"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive.")
        for name, lo, hi in (("cells_across", 8, 128), ("iterations", 20, 20000)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
                raise ValueError(f"{name} must be an integer between {lo} and {hi}.")
        if specimen.structure_family != "tpms" and not self.empty_pipe:
            raise ValueError("Select a TPMS geometry in the Model workspace first.")
        if self.diameter_mm > specimen.size + 1e-9:
            raise ValueError("Channel diameter must not exceed specimen cube size; this would leave bypass gaps.")
        if self.turbulence not in ("laminar", "kOmegaSST"):
            raise ValueError("Unsupported flow model.")
        h = self.diameter_mm / self.cells_across
        nz = sum(math.ceil(length / h) for length in (self.inlet_length_mm, specimen.size, self.outlet_length_mm))
        if self.cells_across ** 2 * nz > 700000:
            raise ValueError("Mesh exceeds the first-version limit of 700,000 background cells. Reduce resolution or extension lengths.")
        if min(self.inlet_length_mm, self.outlet_length_mm) < 2 * h:
            raise ValueError("Each extension must contain at least two cells.")

    def validate_local_runtime(self):
        if not self.distribution.strip() or not self.foam_bashrc.startswith("/") or "\n" in self.foam_bashrc:
            raise ValueError("Select a WSL distribution and an absolute OpenFOAM bashrc path.")


def build_flow_grid(specimen, flow):
    flow.validate(specimen)
    n = flow.cells_across
    d = flow.diameter_mm
    h = d / n
    x = np.linspace((specimen.size-d)/2, (specimen.size+d)/2, n+1)
    zparts = [np.linspace(a, b, math.ceil((b-a)/h)+1) for a,b in
              ((-flow.inlet_length_mm, 0), (0, specimen.size), (specimen.size, specimen.size+flow.outlet_length_mm))]
    z = np.concatenate([zparts[0], zparts[1][1:], zparts[2][1:]])
    xc, zc = (x[:-1]+x[1:])/2, (z[:-1]+z[1:])/2
    radius = (xc[:,None]-specimen.size/2)**2 + (xc[None,:]-specimen.size/2)**2
    fluid = np.broadcast_to((radius < (d/2)**2)[:,:,None], (n,n,len(zc))).copy()
    solid = np.zeros_like(fluid)
    inside = (zc > 0) & (zc < specimen.size)
    if not flow.empty_pipe:
        # Reuse the established TPMS phase and threshold; do not retune geometry to the CFD mesh.
        field = evaluate_priority_field(specimen, xc[:,None,None], xc[None,:,None], zc[inside][None,None,:])
        solid[:,:,inside] = (field <= geometry_threshold(specimen)) & fluid[:,:,inside]
        fluid &= ~solid
        if not solid.any():
            raise ValueError("Mesh does not resolve any TPMS solid. Increase cells across the diameter.")
    labels, count = ndimage.label(fluid)
    entrances = set(np.unique(labels[:,:,0])) - {0}
    exits = set(np.unique(labels[:,:,-1])) - {0}
    through = entrances & exits
    if not through:
        raise ValueError("No face-connected water path from inlet to outlet. Refine the mesh or change TPMS geometry.")
    # Blind but connected pores are retained. Only completely isolated cavities are excluded.
    isolated = fluid & ~np.isin(labels, list(through))
    fluid &= ~isolated
    cells = np.argwhere(fluid)
    point_shape = (n+1,n+1,len(z))
    corners = np.array(((0,0,0),(1,0,0),(1,1,0),(0,1,0),(0,0,1),(1,0,1),(1,1,1),(0,1,1)))
    indices = cells[:,None,:] + corners[None,:,:]
    original = np.ravel_multi_index(indices.transpose(2,0,1), point_shape)
    unique, inverse = np.unique(original, return_inverse=True)
    ii,jj,kk = np.unravel_index(unique, point_shape)
    points = np.column_stack((x[ii],x[jj],z[kk])) * .001
    conn = inverse.reshape(-1,8)
    vtk_cells = np.column_stack((np.full(len(cells),8),conn)).ravel()
    grid = pv.UnstructuredGrid(vtk_cells, np.full(len(cells),pv.CellType.HEXAHEDRON), points)
    grid.cell_data["grid_i"] = cells[:,0]
    grid.cell_data["grid_j"] = cells[:,1]
    grid.cell_data["grid_k"] = cells[:,2]
    metadata = dict(fluid_cells=len(cells), isolated_cells=int(isolated.sum()), solid_cells=int(solid.sum()),
                    effective_diameter_mm=2*math.sqrt(float(fluid[:,:,0].sum())*h*h/math.pi),
                    inlet_area_m2=float(fluid[:,:,0].sum())*(h*.001)**2,
                    specimen_solid_fraction=float(solid[:,:,inside].sum())/float((radius<(d/2)**2).sum()*inside.sum()),
                    nominal_cell_mm=h, specimen_start_mm=0., specimen_end_mm=specimen.size,
                    z_centres_mm=zc.tolist(), warnings=[])
    if not flow.empty_pipe and h > specimen.cell_size * min(specimen.solid_fraction,100-specimen.solid_fraction)/100/6:
        metadata["warnings"].append("Thin TPMS walls/pores may be under-resolved. Compare finer meshes before interpreting pressure loss.")
    if flow.turbulence == "laminar" and flow.reynolds > 2000:
        metadata["warnings"].append("High pipe Reynolds number with laminar model; evaluate turbulence and pore-scale Reynolds numbers.")
    metadata["warnings"].append("Cartesian stair-step cylinder and TPMS surfaces; no fitted boundary layers. Mesh independence is required.")
    return grid, metadata


def _header(kind, name):
    return f"FoamFile\n{{ version 2.0; format ascii; class {kind}; object {name}; }}\n"


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="ascii", newline="\n")


def write_poly_mesh(grid, folder):
    """Outward-oriented owner faces; internal faces precede boundary patches."""
    conn = grid.cells.reshape(-1,9)[:,1:]
    local_faces = ((0,4,7,3),(1,2,6,5),(0,1,5,4),(3,7,6,2),(0,3,2,1),(4,5,6,7))
    all_faces = {}
    for owner, vertices in enumerate(conn):
        for local in local_faces:
            face = tuple(int(vertices[i]) for i in local)
            key = tuple(sorted(face))
            if key in all_faces:
                all_faces[key][2] = owner
            else:
                all_faces[key] = [face, owner, -1]
    internal, patches = [], {name: [] for name in ("inlet","outlet","walls")}
    zmin,zmax = grid.bounds[4:6]
    for face,owner,neighbour in all_faces.values():
        item = (face,owner,neighbour)
        if neighbour >= 0:
            internal.append(item)
        else:
            zz = grid.points[list(face),2]
            patch = "inlet" if np.all(np.abs(zz-zmin)<1e-12) else "outlet" if np.all(np.abs(zz-zmax)<1e-12) else "walls"
            patches[patch].append(item)
    internal.sort(key=lambda item: (item[1],item[2]))
    faces = internal + [item for patch in patches.values() for item in patch]
    def listing(name,kind,values):
        _write(folder/name, _header(kind,name)+str(len(values))+"\n(\n"+"\n".join(values)+"\n)\n")
    listing("points","vectorField",["("+" ".join(f"{v:.14g}" for v in point)+")" for point in grid.points])
    listing("faces","faceList",["4("+" ".join(map(str,f[0]))+")" for f in faces])
    listing("owner","labelList",[str(f[1]) for f in faces])
    listing("neighbour","labelList",[str(f[2]) for f in internal])
    offset = len(internal)
    text = _header("polyBoundaryMesh","boundary")+"3\n(\n"
    for name, patch in patches.items():
        text += f"{name}\n{{ type {'wall' if name=='walls' else 'patch'}; nFaces {len(patch)}; startFace {offset}; }}\n"
        offset += len(patch)
    _write(folder/"boundary",text+")\n")


def export_flow_case(specimen, flow, directory):
    from analysis_process import report_analysis_step
    report_analysis_step("Building fluid mesh…")
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Choose a new or empty CFD case folder to avoid mixing meshes and results.")
    grid, metadata = build_flow_grid(specimen, flow)
    report_analysis_step("Exporting OpenFOAM mesh and case files…")
    directory.mkdir(parents=True,exist_ok=True)
    grid.save(directory/"fluid_mesh.vtu")
    write_poly_mesh(grid,directory/"constant"/"polyMesh")
    nu = flow.dynamic_viscosity/flow.density
    _write(directory/"constant"/"transportProperties",_header("dictionary","transportProperties")+f"transportModel Newtonian;\nnu [0 2 -1 0 0 0 0] {nu:.14g};\n")
    turbulence = "simulationType laminar;" if flow.turbulence=="laminar" else "simulationType RAS; RAS { RASModel kOmegaSST; turbulence on; printCoeffs on; }"
    _write(directory/"constant"/"turbulenceProperties",_header("dictionary","turbulenceProperties")+turbulence)
    def field(name,kind,dimensions,initial,inlet,outlet,wall):
        _write(directory/"0"/name,_header(kind,name)+f"dimensions [{dimensions}];\ninternalField uniform {initial};\nboundaryField\n{{ inlet {{ {inlet} }} outlet {{ {outlet} }} walls {{ {wall} }} }}\n")
    velocity = f"(0 0 {flow.inlet_speed:.14g})"
    field("U","volVectorField","0 1 -1 0 0 0 0",velocity,f"type fixedValue; value uniform {velocity};","type zeroGradient;","type noSlip;")
    field("p","volScalarField","0 2 -2 0 0 0 0","0","type zeroGradient;","type fixedValue; value uniform 0;","type zeroGradient;")
    if flow.turbulence != "laminar":
        k = 1.5*(.05*flow.inlet_speed)**2
        omega = math.sqrt(k)/(.09**.25*.07*flow.diameter_mm*.001)
        for name,dim,value,wall in (("k","0 2 -2 0 0 0 0",k,"kqRWallFunction"),("omega","0 0 -1 0 0 0 0",omega,"omegaWallFunction"),("nut","0 2 -1 0 0 0 0",0,"nutkWallFunction")):
            inlet = "type calculated; value uniform 0;" if name=="nut" else f"type fixedValue; value uniform {value:.14g};"
            field(name,"volScalarField",dim,f"{value:.14g}",inlet,"type zeroGradient;",f"type {wall}; value uniform {value:.14g};")
    functions = ""
    for patch in ("inlet","outlet"):
        for op,fields in (("areaAverage","p"),("sum","phi")):
            functions += f"{patch}_{fields} {{ type surfaceFieldValue; libs (\"libfieldFunctionObjects.so\"); writeControl timeStep; writeInterval 1; regionType patch; name {patch}; operation {op}; fields ({fields}); writeFields false; }}\n"
    _write(directory/"system"/"controlDict",_header("dictionary","controlDict")+f"application simpleFoam; startFrom startTime; startTime 0; stopAt endTime; endTime {flow.iterations}; deltaT 1; writeControl timeStep; writeInterval {flow.iterations}; purgeWrite 1; writeFormat ascii; writePrecision 12; runTimeModifiable false; functions {{ {functions} }}\n")
    _write(directory/"system"/"fvSchemes",_header("dictionary","fvSchemes")+"ddtSchemes { default steadyState; } gradSchemes { default Gauss linear; } divSchemes { default none; div(phi,U) bounded Gauss upwind; div(phi,k) bounded Gauss upwind; div(phi,omega) bounded Gauss upwind; div((nuEff*dev2(T(grad(U))))) Gauss linear; } laplacianSchemes { default Gauss linear corrected; } interpolationSchemes { default linear; } snGradSchemes { default corrected; } fluxRequired { default no; p; } wallDist { method meshWave; }\n")
    _write(directory/"system"/"fvSolution",_header("dictionary","fvSolution")+"solvers { p { solver GAMG; tolerance 1e-9; relTol 0.05; smoother GaussSeidel; } \"(U|k|omega)\" { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-9; relTol 0.05; } } SIMPLE { nNonOrthogonalCorrectors 0; consistent yes; residualControl { p 1e-5; U 1e-6; \"(k|omega)\" 1e-5; } } relaxationFactors { fields { p 0.3; } equations { U 0.7; k 0.7; omega 0.7; } }\n")
    _write(directory/"case.foam", "")
    manifest = dict(specimen=asdict(specimen),flow=asdict(flow),mesh=metadata,
                    pressure_units="Pa (OpenFOAM p multiplied by density)",velocity_units="m/s",
                    geometry="TPMS phase clipped to a concentric cylinder, Z flow; specimen length = cube size",
                    discretization="Cartesian stair-step finite volumes; no-slip walls; uniform velocity inlet; zero gauge pressure outlet")
    (directory/"flow_case.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    _write(directory/"Allrun", "#!/bin/bash\nset -eo pipefail\necho $$ > .runner.pid\ntrap 'rm -f .runner.pid' EXIT\ntest ! -f .cancel-request\ncheckMesh > log.checkMesh 2>&1\ngrep -q 'Mesh OK' log.checkMesh\ntest ! -f .cancel-request\nsimpleFoam > log.simpleFoam 2>&1\n")
    return manifest


def _wsl(flow, script, **kwargs):
    return subprocess.run(["wsl.exe","-d",flow.distribution,"--","bash","-c",script],
                          creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),**kwargs)


def linux_path(path):
    path = Path(path).resolve()
    if not path.drive or len(path.drive)!=2:
        raise ValueError("CFD cases must be on a local Windows drive accessible to WSL.")
    return "/mnt/"+path.drive[0].lower()+path.as_posix()[2:]


def check_flow_runtime(flow):
    flow.validate_local_runtime()
    result = _wsl(flow, f"source {shlex.quote(flow.foam_bashrc)} >/dev/null 2>&1 && command -v simpleFoam && command -v checkMesh",capture_output=True,timeout=30)
    if result.returncode:
        raise RuntimeError("OpenFOAM simpleFoam/checkMesh unavailable. Check the WSL distribution and OpenFOAM bashrc. "+result.stderr.decode(errors="replace")[-1000:])
    return result.stdout.decode(errors="replace").strip()


def stop_flow_case(flow, directory):
    if Path(directory).exists():
        (Path(directory)/".cancel-request").touch()
    pidfile = Path(directory)/".runner.pid"
    if pidfile.exists():
        value = pidfile.read_text().strip()
        if re.fullmatch(r"[1-9][0-9]*",value):
            _wsl(flow, f"kill -TERM -- -{value}",capture_output=True,timeout=15)


def execute_flow_case(specimen,flow,directory):
    from analysis_process import report_analysis_step
    report_analysis_step("Checking OpenFOAM runtime…")
    check_flow_runtime(flow)
    export_flow_case(specimen,flow,directory)
    case = shlex.quote(linux_path(directory))
    script = f"source {shlex.quote(flow.foam_bashrc)} >/dev/null 2>&1 && cd {case} && exec setsid --wait bash Allrun"
    report_analysis_step("Checking mesh and solving flow…")
    result = _wsl(flow,script,capture_output=True)
    if result.returncode:
        details = ""
        for filename in ("log.checkMesh","log.simpleFoam"):
            path = Path(directory)/filename
            if path.exists(): details += "\n"+filename+"\n"+path.read_text(errors="replace")[-3500:]
        raise RuntimeError("OpenFOAM failed; inspect the case logs. "+result.stderr.decode(errors="replace")[-1000:]+details)
    report_analysis_step("Processing flow results…")
    return summarize_flow_case(directory)


def _last_monitor(directory,name):
    paths = list((Path(directory)/"postProcessing"/name).glob("*/surfaceFieldValue.dat"))
    if not paths: raise RuntimeError(f"Missing OpenFOAM boundary monitor: {name}")
    rows = [line.split() for line in paths[0].read_text().splitlines() if line.strip() and not line.lstrip().startswith("#")]
    return float(rows[-1][0]),float(rows[-1][1])


def summarize_flow_case(directory):
    directory = Path(directory)
    manifest = json.loads((directory/"flow_case.json").read_text())
    flow = FlowSettings(**manifest["flow"])
    reader = pv.OpenFOAMReader(directory/"case.foam")
    if max(reader.time_values) <= 0:
        raise RuntimeError("No solved time step exists; initial fields are not CFD results.")
    reader.set_active_time_value(max(reader.time_values))
    reader.disable_all_patch_arrays()
    reader.enable_patch_array("internalMesh")
    blocks = reader.read()
    grid = blocks["internalMesh"]
    if grid is None or "U" not in grid.cell_data or "p" not in grid.cell_data:
        raise RuntimeError("OpenFOAM did not write velocity and pressure fields.")
    velocity = np.asarray(grid.cell_data["U"])
    pressure = np.asarray(grid.cell_data["p"])*flow.density
    if not np.isfinite(velocity).all() or not np.isfinite(pressure).all():
        raise RuntimeError("Non-finite CFD result. Inspect mesh and solver settings.")
    grid.cell_data["Velocity_m_s"] = velocity
    grid.cell_data["Speed_m_s"] = np.linalg.norm(velocity,axis=1)
    grid.cell_data["Pressure_Pa"] = pressure
    grid.save(directory/"flow_result.vtu")
    ti,pi = _last_monitor(directory,"inlet_p")
    to,po = _last_monitor(directory,"outlet_p")
    tq,qi = _last_monitor(directory,"inlet_phi")
    _,qo = _last_monitor(directory,"outlet_phi")
    imbalance = abs(qi+qo)/max(abs(qi),abs(qo),1e-30)
    log = (directory/"log.simpleFoam").read_text(errors="replace")
    if not re.search(r"^End\s*$",log,re.MULTILINE):
        raise RuntimeError("OpenFOAM did not end cleanly; inspect log.simpleFoam.")
    if not np.isfinite([pi,po,qi,qo]).all():
        raise RuntimeError("Non-finite boundary pressure or flow monitor.")
    converged = "SIMPLE solution converged" in log and imbalance < .01
    residuals = {}
    for name,value in re.findall(r"Solving for (\w+), Initial residual = ([\deE+.\-]+)",log):
        residuals[name] = float(value)
    centres = grid.cell_centers().points[:,2]*1000
    z = np.asarray(manifest["mesh"]["z_centres_mm"])
    before,after = z[z<0][-1],z[z>manifest["specimen"]["size"]][0]
    def section(at):
        mask = np.isclose(centres,at,rtol=0,atol=1e-5)
        if not mask.any(): raise RuntimeError("Cannot locate pressure measurement section.")
        return float(np.mean(pressure[mask]))
    import csv
    with (directory/"axial_profile.csv").open("w",newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("z_mm","mean_pressure_Pa","mean_axial_velocity_m_s","max_speed_m_s"))
        for at in z:
            mask = np.isclose(centres,at,rtol=0,atol=1e-5)
            if mask.any(): writer.writerow((at,float(pressure[mask].mean()),float(velocity[mask,2].mean()),float(np.linalg.norm(velocity[mask],axis=1).max())))
    summary = dict(status="converged" if converged else "not_converged",time=max(reader.time_values),
                   reynolds_diameter=flow.reynolds,pressure_drop_total_Pa=(pi-po)*flow.density,
                   pressure_drop_specimen_Pa=section(before)-section(after),
                   specimen_pressure_planes_mm=[float(before),float(after)],
                   pressure_definition="Static, area-averaged gauge pressure. Specimen value uses the adjacent empty-channel cell-centre planes, including entrance/exit effects.",
                   inlet_flow_m3_s=-qi,outlet_flow_m3_s=qo,mass_imbalance_fraction=imbalance,
                   maximum_speed_m_s=float(np.linalg.norm(velocity,axis=1).max()),
                   final_initial_residuals=residuals,mesh=manifest["mesh"],warnings=manifest["mesh"]["warnings"])
    if not converged: summary["warnings"].append("SIMPLE residual criteria and/or 1% mass-balance criterion not satisfied; these are provisional results.")
    (directory/"flow_summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    return summary
