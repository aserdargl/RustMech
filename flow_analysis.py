"""Isothermal, incompressible OpenFOAM CFD, separate from solid mechanics.

Geometry is in mm at the UI boundary; all OpenFOAM quantities are SI.
The first mesh is explicitly a stair-step, body-fitted Cartesian approximation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from functools import lru_cache
import json
import math
from pathlib import Path
import re
import shlex
import subprocess

import numpy as np
import pyvista as pv
from scipy import ndimage

from model import Settings, validate_geometry
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
    channel_shape: str = "cylinder"
    width_mm: float = 6.0
    height_mm: float = 6.0
    section_length_mm: float = 0.0  # zero preserves the legacy cube-length section
    specimen_offset_x_mm: float = 0.0
    specimen_offset_y_mm: float = 0.0
    specimen_offset_z_mm: float = 0.0
    specimen_rotation_x_deg: float = 0.0
    specimen_rotation_y_deg: float = 0.0
    specimen_rotation_z_deg: float = 0.0
    allow_bypass: bool = False
    fluid_material_card: dict | None = None
    mesh_method: str = "cartesian"
    surface_refinement: int = 1
    boundary_layers: int = 3
    max_fitted_cells: int = 2000000

    @property
    def cross_section(self):
        return (self.diameter_mm, self.diameter_mm) if self.channel_shape == "cylinder" else (self.width_mm, self.height_mm)

    @property
    def hydraulic_diameter_mm(self):
        return self.diameter_mm if self.channel_shape == "cylinder" else 2*self.width_mm*self.height_mm/(self.width_mm+self.height_mm)

    @property
    def cell_size_mm(self):
        return min(self.cross_section)/self.cells_across

    def section_length(self, specimen):
        return self.section_length_mm or specimen.size

    @property
    def section_label(self):
        return f"Ø {self.diameter_mm:g} mm" if self.channel_shape == "cylinder" else f"{self.width_mm:g} × {self.height_mm:g} mm"

    @property
    def reynolds(self):
        return self.density * self.inlet_speed * self.hydraulic_diameter_mm * 0.001 / self.dynamic_viscosity

    def validate(self, specimen: Settings):
        if specimen.test_profile != 'custom':
            raise ValueError('Standard test profiles define mechanical fixtures. Select Özel deney / Custom experiment for flow and thermal analysis.')
        for name in (("size",) if specimen.imported_geometry_path else ("size", "cell_size", "solid_fraction")):
            value = getattr(specimen,name)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"Specimen {name} must be finite and positive.")
        if not specimen.imported_geometry_path and (specimen.solid_fraction >= 100 or specimen.cell_size > specimen.size):
            raise ValueError("Select a solid fraction below 100% and a TPMS period no larger than the specimen.")
        if self.channel_shape not in ("cylinder", "rectangular"):
            raise ValueError("Select a cylindrical or rectangular channel.")
        if self.mesh_method not in ("cartesian","snappy"):
            raise ValueError("Select Cartesian or fitted OpenFOAM meshing.")
        for name,low,high in (("surface_refinement",0,4),("boundary_layers",0,8),("max_fitted_cells",1000,10000000)):
            if type(getattr(self,name)) is not int or not low<=getattr(self,name)<=high:
                raise ValueError(f"{name} must be an integer between {low} and {high}.")
        if type(self.allow_bypass) is not bool:
            raise ValueError("Bypass selection must be a boolean.")
        for name in ("diameter_mm", "width_mm", "height_mm", "inlet_length_mm", "outlet_length_mm", "inlet_speed", "density", "dynamic_viscosity"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive.")
        for name, lo, hi in (("cells_across", 8, 128), ("iterations", 20, 20000)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
                raise ValueError(f"{name} must be an integer between {lo} and {hi}.")
        if not self.empty_pipe:
            errors = validate_geometry(specimen, include_mesh=False)
            if errors:
                raise ValueError("\n".join(errors))
        for name in ("section_length_mm", "specimen_offset_x_mm", "specimen_offset_y_mm", "specimen_offset_z_mm",
                     "specimen_rotation_x_deg", "specimen_rotation_y_deg", "specimen_rotation_z_deg"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"{name} must be finite.")
        if self.section_length_mm < 0:
            raise ValueError("Section length must be positive, or zero for the Model cube length.")
        if not self.empty_pipe and not self.allow_bypass and any(a > b + 1e-9 for a, b in zip(self.cross_section, specimen.dimensions[:2])):
            raise ValueError("Channel section exceeds specimen cube size; enable intentional bypass to leave gaps.")
        if self.turbulence not in ("laminar", "kOmegaSST"):
            raise ValueError("Unsupported flow model.")
        h = self.cell_size_mm
        nx, ny = (math.ceil(value/h) for value in self.cross_section)
        nz = sum(math.ceil(length / h) for length in (self.inlet_length_mm, self.section_length(specimen), self.outlet_length_mm))
        if nx * ny * nz > 700000:
            raise ValueError("Mesh exceeds the first-version limit of 700,000 background cells. Reduce resolution or extension lengths.")
        if min(self.inlet_length_mm, self.outlet_length_mm) < 2 * h:
            raise ValueError("Each extension must contain at least two cells.")

    def validate_local_runtime(self):
        if not self.distribution.strip() or not self.foam_bashrc.startswith("/") or "\n" in self.foam_bashrc:
            raise ValueError("Select a WSL distribution and an absolute OpenFOAM bashrc path.")


def build_flow_grid(specimen, flow, *, inspection=False):
    flow.validate(specimen)
    if specimen.imported_geometry_path and not flow.empty_pipe and not inspection:
        path = Path(specimen.imported_geometry_path).resolve()
        stat = path.stat()
        check = _imported_intersections(str(path),stat.st_mtime_ns,stat.st_size)
        if check["status"] != "clear":
            raise ValueError(f"Imported surface is invalid: {check['intersecting_pairs']} intersecting triangle pairs; "
                             f"{check['degenerate_triangles']} degenerate triangles. Use Inspect geometry before solving.")
    width, height = flow.cross_section
    h = flow.cell_size_mm
    nx, ny = (math.ceil(value/h) for value in (width, height))
    length = flow.section_length(specimen)
    x = np.linspace((specimen.dimensions[0]-width)/2, (specimen.dimensions[0]+width)/2, nx+1)
    y = np.linspace((specimen.dimensions[1]-height)/2, (specimen.dimensions[1]+height)/2, ny+1)
    zparts = [np.linspace(a, b, math.ceil((b-a)/h)+1) for a,b in
              ((-flow.inlet_length_mm, 0), (0, length), (length, length+flow.outlet_length_mm))]
    z = np.concatenate([zparts[0], zparts[1][1:], zparts[2][1:]])
    xc, yc, zc = (x[:-1]+x[1:])/2, (y[:-1]+y[1:])/2, (z[:-1]+z[1:])/2
    radius = (xc[:,None]-specimen.dimensions[0]/2)**2 + (yc[None,:]-specimen.dimensions[1]/2)**2
    section = radius < (width/2)**2 if flow.channel_shape == "cylinder" else np.ones((nx, ny), dtype=bool)
    fluid = np.broadcast_to(section[:,:,None], (nx,ny,len(zc))).copy()
    solid = np.zeros_like(fluid)
    bypass = np.zeros_like(fluid)
    geometry_errors = []
    inside = (zc > 0) & (zc < length)
    bypass_cells = 0
    if not flow.empty_pipe:
        coordinates = np.stack(np.broadcast_arrays(xc[:,None,None], yc[None,:,None], zc[inside][None,None,:]), axis=-1)
        sampled, envelope = sample_channel_solid(specimen, flow, coordinates.reshape(-1,3))
        sampled, envelope = sampled.reshape(nx,ny,-1), envelope.reshape(nx,ny,-1)
        bypass_cells = int((~envelope & fluid[:,:,inside]).sum())
        bypass[:,:,inside] = ~envelope & fluid[:,:,inside]
        if bypass_cells and not flow.allow_bypass and not inspection:
            raise ValueError("Placement leaves channel cells outside the specimen envelope. Enable intentional bypass or change placement/section length.")
        solid[:,:,inside] = sampled & fluid[:,:,inside]
        fluid &= ~solid
        if not solid.any() and not inspection:
            raise ValueError("Mesh does not resolve any specimen solid. Refine the channel mesh.")
        if not solid.any():
            geometry_errors.append("No specimen solid is resolved; inspect placement and refine thin features.")
    labels, count = ndimage.label(fluid)
    entrances = set(np.unique(labels[:,:,0])) - {0}
    exits = set(np.unique(labels[:,:,-1])) - {0}
    through = entrances & exits
    if not through and not inspection:
        raise ValueError("No face-connected fluid path from inlet to outlet. Refine the mesh or change the structure.")
    if not through:
        geometry_errors.append("No face-connected fluid path from inlet to outlet.")
    # Blind but connected pores are retained. Only completely isolated cavities are excluded.
    isolated = fluid & ~np.isin(labels, list(through))
    isolated_ids = np.unique(labels[isolated])
    counts = np.bincount(labels.ravel())
    markers = []
    for identifier in sorted(isolated_ids, key=lambda key:int(counts[key]), reverse=True)[:64]:
        centres = np.argwhere(labels == identifier)
        markers.append({"component":int(identifier),"cells":int(counts[identifier]),
                        "centre_mm":[float(xc[centres[:,0]].mean()),float(yc[centres[:,1]].mean()),float(zc[centres[:,2]].mean())]})
    if not inspection:
        fluid &= ~isolated
    cells = np.argwhere(fluid)
    point_shape = (nx+1,ny+1,len(z))
    corners = np.array(((0,0,0),(1,0,0),(1,1,0),(0,1,0),(0,0,1),(1,0,1),(1,1,1),(0,1,1)))
    indices = cells[:,None,:] + corners[None,:,:]
    original = np.ravel_multi_index(indices.transpose(2,0,1), point_shape)
    unique, inverse = np.unique(original, return_inverse=True)
    ii,jj,kk = np.unravel_index(unique, point_shape)
    points = np.column_stack((x[ii],y[jj],z[kk])) * .001
    conn = inverse.reshape(-1,8)
    vtk_cells = np.column_stack((np.full(len(cells),8),conn)).ravel()
    grid = pv.UnstructuredGrid(vtk_cells, np.full(len(cells),pv.CellType.HEXAHEDRON), points)
    grid.cell_data["grid_i"] = cells[:,0]
    grid.cell_data["grid_j"] = cells[:,1]
    grid.cell_data["grid_k"] = cells[:,2]
    grid.cell_data["GeometryIssue"] = np.where(isolated[tuple(cells.T)],2,
                                                np.where(bypass[tuple(cells.T)],1,0)).astype(np.uint8)
    dx, dy = width/nx, height/ny
    metadata = dict(fluid_cells=len(cells), isolated_cells=int(isolated.sum()), solid_cells=int(solid.sum()),
                    effective_diameter_mm=2*math.sqrt(float(fluid[:,:,0].sum())*dx*dy/math.pi),
                    hydraulic_diameter_mm=flow.hydraulic_diameter_mm, channel_shape=flow.channel_shape,
                    bypass_envelope_cells=bypass_cells,
                    solid_components=int(ndimage.label(solid)[1]),
                    isolated_fluid_components=len(isolated_ids), isolated_component_markers=markers,
                    geometry_errors=geometry_errors, geometry_valid_for_flow=not geometry_errors,
                    geometry_valid_for_cht=not geometry_errors and not isolated.any(),
                    inlet_area_m2=float(fluid[:,:,0].sum())*dx*dy*1e-6,
                    specimen_solid_fraction=float(solid[:,:,inside].sum())/float(section.sum()*inside.sum()),
                    nominal_cell_mm=h, specimen_start_mm=0., specimen_end_mm=length,
                    z_centres_mm=zc.tolist(), warnings=[])
    if specimen.imported_geometry_path:
        metadata["warnings"].append("Imported surface sampled at cell centres; refine to preserve thin walls, pore openings and connectivity.")
    elif not flow.empty_pipe and h > specimen.cell_size * min(specimen.solid_fraction,100-specimen.solid_fraction)/100/6:
        metadata["warnings"].append("Thin specimen walls/pores may be under-resolved. Compare finer meshes before interpreting pressure loss.")
    if flow.turbulence == "laminar" and flow.reynolds > 2000:
        metadata["warnings"].append("High pipe Reynolds number with laminar model; evaluate turbulence and pore-scale Reynolds numbers.")
    metadata["warnings"].append("Cartesian stair-step channel and specimen surfaces; no fitted boundary layers. Mesh independence is required.")
    if bypass_cells:
        metadata["warnings"].append(f"Intentional bypass: {bypass_cells} cells lie outside the placed specimen envelope.")
    return grid, metadata


def export_geometry_inspection(specimen, flow, directory):
    """Export all sampled fluid regions for inspection, without solver files."""
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Choose a new or empty inspection directory.")
    grid, metadata = build_flow_grid(specimen, replace(flow,allow_bypass=True),inspection=True)
    if metadata["bypass_envelope_cells"] and not flow.allow_bypass:
        metadata["geometry_errors"].append("Specimen envelope leaves bypass gaps; reposition or explicitly allow them before solving.")
    metadata["geometry_valid_for_flow"] = not metadata["geometry_errors"]
    metadata["geometry_valid_for_cht"] = metadata["geometry_valid_for_flow"] and not metadata["isolated_cells"]
    if metadata["isolated_cells"]:
        metadata["warnings"].append("Isolated fluid cells are retained for inspection (red); they are excluded from CFD and unsupported in current CHT.")
    metadata["warnings"].append("Inspection tests sampled connectivity; sub-cell walls, pores and self-intersections need surface/mesh refinement checks.")
    directory.mkdir(parents=True,exist_ok=True)
    if not flow.empty_pipe:
        from analysis_process import report_analysis_step
        from surface_audit import audit_surface
        from mesh_engine import make_surface
        report_analysis_step("Checking surface intersections and sampled wall/pore resolution…")
        surface = make_surface(specimen)
        audit, annotated = audit_surface(surface,flow.cell_size_mm,progress=report_analysis_step)
        rotation,centre = specimen_transform(specimen,flow)
        annotated.points = (annotated.points-np.asarray(specimen.dimensions)/2) @ rotation.T + centre
        for check in (audit["intersection"],audit.get("chords")):
            if check:
                for marker in check["markers"]:
                    marker["centre_mm"] = ((np.asarray(marker["centre_mm"])-np.asarray(specimen.dimensions)/2) @ rotation.T+centre).tolist()
        annotated.save(directory/"surface_audit.vtp")
        metadata["surface_audit"] = audit
        if audit["intersection"]["status"] == "invalid":
            metadata["geometry_errors"].append("The specimen surface has intersecting or degenerate triangles; repair it before analysis.")
        elif audit["intersection"]["status"] == "incomplete":
            metadata["warnings"].append("Surface intersection search reached its candidate budget; surface validity is unconfirmed.")
        if audit.get("chords"):
            for kind in ("wall","pore"):
                ratio = audit["chords"][kind+"_minimum_cells"]
                if ratio is not None and ratio<audit["chords"]["required_cells"]:
                    metadata["warnings"].append(f"Sampled {kind} normal chord spans only {ratio:.3g} cells; refine and compare meshes.")
        metadata["surface_validity_confirmed"] = audit["intersection"]["status"] == "clear"
        metadata["geometry_valid_for_flow"] = not metadata["geometry_errors"] and metadata["surface_validity_confirmed"]
        metadata["geometry_valid_for_cht"] = metadata["geometry_valid_for_flow"] and not metadata["isolated_cells"]
    grid.save(directory/"fluid_mesh.vtu")
    manifest = dict(specimen=asdict(specimen),flow=asdict(flow),mesh=metadata,inspection_only=True,
                    geometry_issue_legend={"0":"regular fluid","1":"outside specimen envelope","2":"not connected to both ends"})
    (directory/"flow_case.json").write_text(json.dumps(manifest,indent=2,allow_nan=False),encoding="utf-8")
    return manifest


def sample_channel_solid(specimen, flow, points_mm):
    """Sample the same finite cube in a translated/rotated channel section.

    Rotations are extrinsic X, then Y, then Z, about the specimen centre.
    Changing section length clips the specimen; it never stretches its cells.
    """
    rotation,centre = specimen_transform(specimen,flow)
    local = (np.asarray(points_mm)-centre) @ rotation + np.asarray(specimen.dimensions)/2
    envelope = ((local >= 0) & (local <= specimen.dimensions)).all(axis=1)
    solid = np.zeros(len(local), dtype=bool)
    if envelope.any():
        p = local[envelope]
        if specimen.imported_geometry_path:
            path = Path(specimen.imported_geometry_path).resolve()
            stat = path.stat()
            surface = _flow_surface(str(path), stat.st_mtime_ns, stat.st_size)
            selected = pv.PolyData(p).select_interior_points(surface, method="signed_distance", check_surface=True)
            solid[envelope] = np.asarray(selected.point_data["selected_points"], dtype=bool)
        else:
            solid[envelope] = evaluate_priority_field(specimen, p[:,0], p[:,1], p[:,2]) <= geometry_threshold(specimen)
    return solid, envelope


def specimen_transform(specimen, flow):
    """Rigid local-to-channel transform shared by sampling and diagnostic markers."""
    ax, ay, az = np.radians([flow.specimen_rotation_x_deg, flow.specimen_rotation_y_deg,
                            flow.specimen_rotation_z_deg])
    cx, sx, cy, sy, cz, sz = np.cos(ax), np.sin(ax), np.cos(ay), np.sin(ay), np.cos(az), np.sin(az)
    rotation = np.array([[cz,-sz,0],[sz,cz,0],[0,0,1]]) @ np.array([[cy,0,sy],[0,1,0],[-sy,0,cy]]) @ np.array([[1,0,0],[0,cx,-sx],[0,sx,cx]])
    centre = np.array([specimen.dimensions[0]/2+flow.specimen_offset_x_mm,
                       specimen.dimensions[1]/2+flow.specimen_offset_y_mm,
                       flow.section_length(specimen)/2+flow.specimen_offset_z_mm])
    return rotation,centre


@lru_cache(maxsize=8)
def _flow_surface(path, modified_ns, file_size):
    from imported_geometry import read_surface
    return read_surface(path, require_single=False)


@lru_cache(maxsize=8)
def _imported_intersections(path, modified_ns, file_size):
    from analysis_process import report_analysis_step
    from surface_audit import intersection_report
    return intersection_report(_flow_surface(path,modified_ns,file_size),progress=report_analysis_step)


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
        wall_patch = '"(walls|insert)"' if flow.mesh_method=="snappy" else "walls"
        _write(directory/"0"/name,_header(kind,name)+f"dimensions [{dimensions}];\ninternalField uniform {initial};\nboundaryField\n{{ inlet {{ {inlet} }} outlet {{ {outlet} }} {wall_patch} {{ {wall} }} }}\n")
    velocity = f"(0 0 {flow.inlet_speed:.14g})"
    field("U","volVectorField","0 1 -1 0 0 0 0",velocity,f"type fixedValue; value uniform {velocity};","type zeroGradient;","type noSlip;")
    field("p","volScalarField","0 2 -2 0 0 0 0","0","type zeroGradient;","type fixedValue; value uniform 0;","type zeroGradient;")
    if flow.turbulence != "laminar":
        k = 1.5*(.05*flow.inlet_speed)**2
        omega = math.sqrt(k)/(.09**.25*.07*flow.hydraulic_diameter_mm*.001)
        for name,dim,value,wall in (("k","0 2 -2 0 0 0 0",k,"kqRWallFunction"),("omega","0 0 -1 0 0 0 0",omega,"omegaWallFunction"),("nut","0 2 -1 0 0 0 0",0,"nutkWallFunction")):
            inlet = "type calculated; value uniform 0;" if name=="nut" else f"type fixedValue; value uniform {value:.14g};"
            field(name,"volScalarField",dim,f"{value:.14g}",inlet,"type zeroGradient;",f"type {wall}; value uniform {value:.14g};")
    functions = ""
    if flow.mesh_method=="snappy" and flow.turbulence=="kOmegaSST":
        functions += 'yPlus { type yPlus; libs ("libfieldFunctionObjects.so"); executeControl timeStep; executeInterval 1; writeControl writeTime; }\n'
    for patch in ("inlet","outlet"):
        for op,fields in (("areaAverage","p"),("sum","phi")):
            functions += f"{patch}_{fields} {{ type surfaceFieldValue; libs (\"libfieldFunctionObjects.so\"); writeControl timeStep; writeInterval 1; regionType patch; name {patch}; operation {op}; fields ({fields}); writeFields false; }}\n"
    _write(directory/"system"/"controlDict",_header("dictionary","controlDict")+f"application simpleFoam; startFrom startTime; startTime 0; stopAt endTime; endTime {flow.iterations}; deltaT 1; writeControl timeStep; writeInterval {flow.iterations}; purgeWrite 1; writeFormat ascii; writePrecision 12; runTimeModifiable false; functions {{ {functions} }}\n")
    _write(directory/"system"/"fvSchemes",_header("dictionary","fvSchemes")+"ddtSchemes { default steadyState; } gradSchemes { default Gauss linear; } divSchemes { default none; div(phi,U) bounded Gauss upwind; div(phi,k) bounded Gauss upwind; div(phi,omega) bounded Gauss upwind; div((nuEff*dev2(T(grad(U))))) Gauss linear; } laplacianSchemes { default Gauss linear corrected; } interpolationSchemes { default linear; } snGradSchemes { default corrected; } fluxRequired { default no; p; } wallDist { method meshWave; }\n")
    _write(directory/"system"/"fvSolution",_header("dictionary","fvSolution")+"solvers { p { solver GAMG; tolerance 1e-9; relTol 0.05; smoother GaussSeidel; } \"(U|k|omega)\" { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-9; relTol 0.05; } } SIMPLE { nNonOrthogonalCorrectors 0; consistent yes; residualControl { p 1e-5; U 1e-6; \"(k|omega)\" 1e-5; } } relaxationFactors { fields { p 0.3; } equations { U 0.7; k 0.7; omega 0.7; } }\n")
    _write(directory/"case.foam", "")
    manifest = dict(specimen=asdict(specimen),flow=asdict(flow),mesh=metadata,
                    pressure_units="Pa (OpenFOAM p multiplied by density)",velocity_units="m/s",
                    geometry=f"Placed finite specimen clipped to {flow.channel_shape} channel; +Z flow; section {flow.section_length(specimen):g} mm",
                    discretization="Cartesian stair-step finite volumes; no-slip walls; uniform velocity inlet; zero gauge pressure outlet")
    (directory/"flow_case.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    _write(directory/"Allrun", "#!/bin/bash\nset -eo pipefail\necho $$ > .runner.pid\ntrap 'rm -f .runner.pid' EXIT\ntest ! -f .cancel-request\ncheckMesh > log.checkMesh 2>&1\ngrep -q 'Mesh OK' log.checkMesh\ntest ! -f .cancel-request\nsimpleFoam > log.simpleFoam 2>&1\n")
    if flow.mesh_method=="snappy":
        from fitted_mesh import fit_fluid_case
        fitted = fit_fluid_case(specimen,flow,directory)
        metadata["cartesian_precheck_cells"] = metadata["fluid_cells"]
        metadata.update({key:value for key,value in fitted.items() if key!="warnings"})
        metadata["precheck_warnings"] = list(metadata["warnings"])
        metadata["warnings"] = ["Cartesian precheck: "+warning for warning in metadata["warnings"]]+fitted["warnings"]
        manifest["discretization"] = "Body-fitted OpenFOAM finite volumes; requested surface refinement and boundary layers. See fitted mesh diagnostics."
        (directory/"flow_case.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
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
    before,after = z[z<0][-1],z[z>manifest["mesh"].get("specimen_end_mm", manifest["specimen"]["size"])][0]
    fitted = flow.mesh_method=="snappy"
    interpolated = grid.cell_data_to_point_data() if fitted else None
    def section_values(at):
        if fitted:
            plane = interpolated.slice(normal="z",origin=(0,0,at*.001))
            if not plane.n_cells:
                raise RuntimeError("Cannot locate fitted-mesh pressure measurement plane.")
            weights = plane.compute_cell_sizes(length=False,volume=False).cell_data["Area"]
            values = plane.point_data_to_cell_data()
            if not np.isfinite(weights).all() or weights.sum()<=0:
                raise RuntimeError("Invalid fitted-mesh measurement plane area.")
            return (float(np.average(values.cell_data["Pressure_Pa"],weights=weights)),
                    float(np.average(values.cell_data["Velocity_m_s"][:,2],weights=weights)),
                    float(np.max(values.cell_data["Speed_m_s"])))
        mask = np.isclose(centres,at,rtol=0,atol=1e-5)
        if not mask.any(): raise RuntimeError("Cannot locate pressure measurement section.")
        return float(pressure[mask].mean()),float(velocity[mask,2].mean()),float(np.linalg.norm(velocity[mask],axis=1).max())
    import csv
    with (directory/"axial_profile.csv").open("w",newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("z_mm","mean_pressure_Pa","mean_axial_velocity_m_s","max_speed_m_s"))
        for at in z:
            mask = np.isclose(centres,at,rtol=0,atol=1e-5)
            if mask.any() or fitted:
                writer.writerow((at,*section_values(at)))
    summary = dict(status="converged" if converged else "not_converged",time=max(reader.time_values),
                   reynolds_diameter=flow.reynolds,pressure_drop_total_Pa=(pi-po)*flow.density,
                   pressure_drop_specimen_Pa=section_values(before)[0]-section_values(after)[0],
                   specimen_pressure_planes_mm=[float(before),float(after)],
                   pressure_definition="Static, area-averaged gauge pressure. Specimen value uses the adjacent empty-channel cell-centre planes, including entrance/exit effects.",
                   inlet_flow_m3_s=-qi,outlet_flow_m3_s=qo,mass_imbalance_fraction=imbalance,
                   maximum_speed_m_s=float(np.linalg.norm(velocity,axis=1).max()),
                   final_initial_residuals=residuals,mesh=manifest["mesh"],warnings=manifest["mesh"]["warnings"])
    if not converged: summary["warnings"].append("SIMPLE residual criteria and/or 1% mass-balance criterion not satisfied; these are provisional results.")
    if fitted:
        summary["pressure_definition"] = "Static, area-averaged pressure on interpolated planes adjacent to the insert section; includes entrance/exit effects."
        paths = list((directory/"postProcessing/yPlus").glob("*/*.dat"))
        summary["y_plus_files"] = [path.relative_to(directory).as_posix() for path in paths]
        summary["y_plus"] = {}
        for path in paths:
            for line in path.read_text().splitlines():
                if line.strip() and not line.lstrip().startswith("#"):
                    parts = line.split()
                    if len(parts)>=5:
                        values = [float(value) for value in parts[2:5]]
                        if not np.isfinite(values).all():
                            raise RuntimeError("Non-finite y+ output.")
                        summary["y_plus"][parts[1]] = dict(minimum=values[0],maximum=values[1],average=values[2])
    (directory/"flow_summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    return summary
