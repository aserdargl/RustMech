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
    # Pipe-flow mesh controls. Defaults keep older saved cases and presets valid.
    mesh_style: str = "ogrid"        # "ogrid": wall-fitted circular pipe; "cartesian": legacy stair-step
    pipe_length_mm: float = 0.0      # empty pipe test length; 0 = Model cube size
    axial_aspect: float = 2.0        # axial / cross-section cell size, empty pipe only
    wall_grading: float = 1.0        # first-to-last radial cell size ratio (1 = uniform)
    convection: str = "linearUpwind" # momentum convection scheme: "linearUpwind" or "upwind"
    solver: str = "openfoam"         # "openfoam": finite volume; "kratos": finite element (stabilised VMS), both run in WSL
    kratos_python: str = "python3"   # Python interpreter with KratosMultiphysics inside the WSL distribution

    @property
    def reynolds(self):
        return self.density * self.inlet_speed * self.diameter_mm * 0.001 / self.dynamic_viscosity

    def test_length_mm(self, specimen):
        """Length of the instrumented section: own pipe length for an empty pipe, otherwise the TPMS block."""
        own = self.empty_pipe and self.mesh_style == "ogrid" and self.pipe_length_mm > 0
        return self.pipe_length_mm if own else specimen.size

    def axial_cell_mm(self):
        return self.diameter_mm / self.cells_across * (self.axial_aspect if self.empty_pipe and self.mesh_style == "ogrid" else 1.0)

    def layers(self, specimen):
        hz = self.axial_cell_mm()
        return sum(max(1, math.ceil(length / hz - 1e-9)) for length in
                   (self.inlet_length_mm, self.test_length_mm(specimen), self.outlet_length_mm))

    def cells_per_layer(self):
        if self.mesh_style == "ogrid":
            from pipe_mesh import split_cells_across
            core, radial = split_cells_across(self.cells_across)
            return core * core + 4 * core * radial
        return self.cells_across ** 2

    def estimated_cells(self, specimen):
        return self.cells_per_layer() * self.layers(specimen)

    def developing_length_mm(self):
        from pipe_flow_theory import developing_length_mm
        return developing_length_mm(self.reynolds, self.diameter_mm)

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
        if self.diameter_mm > specimen.size + 1e-9 and not self.empty_pipe:
            raise ValueError("Channel diameter must not exceed specimen cube size; this would leave bypass gaps.")
        if self.turbulence not in ("laminar", "kOmegaSST"):
            raise ValueError("Unsupported flow model.")
        if self.mesh_style not in ("ogrid", "cartesian"):
            raise ValueError("Unsupported mesh style.")
        if self.convection not in ("linearUpwind", "upwind"):
            raise ValueError("Unsupported convection scheme.")
        if self.solver not in ("openfoam", "kratos"):
            raise ValueError("Unsupported flow solver.")
        if self.solver == "kratos":
            if self.turbulence != "laminar":
                raise ValueError("The Kratos FEM solver currently supports the laminar model only; use OpenFOAM for kOmegaSST.")
            if not re.fullmatch(r"[A-Za-z0-9_./~-]+", self.kratos_python):
                raise ValueError("Enter the WSL Python interpreter name or absolute path (for example python3).")
        for name, lo, hi in (("pipe_length_mm", 0.0, 1e6), ("axial_aspect", 1.0, 20.0), ("wall_grading", 1.0, 20.0)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
                raise ValueError(f"{name} must be between {lo:g} and {hi:g}.")
        hz = self.axial_cell_mm()
        if self.estimated_cells(specimen) > 700000:
            raise ValueError("Mesh exceeds the first-version limit of 700,000 background cells. Reduce resolution or extension lengths.")
        if min(self.inlet_length_mm, self.outlet_length_mm) < 2 * hz:
            raise ValueError("Each extension must contain at least two cells.")

    def validate_local_runtime(self):
        if not self.distribution.strip() or (self.solver == "openfoam" and (not self.foam_bashrc.startswith("/") or "\n" in self.foam_bashrc)):
            raise ValueError("Select a WSL distribution and an absolute OpenFOAM bashrc path.")


def build_flow_grid(specimen, flow):
    """Fluid mesh for the selected style; both styles return ``(grid, metadata)`` in SI metres."""
    flow.validate(specimen)
    return _build_ogrid_grid(specimen, flow) if flow.mesh_style == "ogrid" else _build_cartesian_grid(specimen, flow)


def _segment_edges(flow, test_length, hz):
    parts = [np.linspace(a, b, max(1, math.ceil((b - a) / hz - 1e-9)) + 1) for a, b in
             ((-flow.inlet_length_mm, 0.0), (0.0, test_length), (test_length, test_length + flow.outlet_length_mm))]
    return np.concatenate([parts[0], parts[1][1:], parts[2][1:]])


def _through_cells(fluid, quads):
    """Mask of fluid cells face-connected from inlet to outlet; ``fluid`` is (layers, quads)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from pipe_mesh import quad_adjacency
    nz, nq = fluid.shape
    ids = np.arange(nz * nq).reshape(nz, nq)
    pairs = quad_adjacency(quads)
    ok = fluid[:, pairs[:, 0]] & fluid[:, pairs[:, 1]]
    axial = fluid[:-1] & fluid[1:]
    rows = np.concatenate((ids[:, pairs[:, 0]][ok], ids[:-1][axial]))
    cols = np.concatenate((ids[:, pairs[:, 1]][ok], ids[1:][axial]))
    graph = coo_matrix((np.ones(len(rows), dtype=np.int8), (rows, cols)), shape=(nz * nq, nz * nq))
    _, labels = connected_components(graph, directed=False)
    labels = labels.reshape(nz, nq)
    through = np.intersect1d(labels[0][fluid[0]], labels[-1][fluid[-1]])
    return fluid & np.isin(labels, through)


def _build_ogrid_grid(specimen, flow):
    """Wall-fitted circular pipe. Only TPMS surfaces are stair-stepped; the pipe wall is exact."""
    from pipe_mesh import cross_section
    d = flow.diameter_mm
    length = flow.test_length_mm(specimen)
    h, hz = d / flow.cells_across, flow.axial_cell_mm()
    z = _segment_edges(flow, length, hz)
    xy, quads, section = cross_section(d, flow.cells_across, flow.wall_grading)
    areas = section.pop("areas_mm2")
    centre = (specimen.size / 2, specimen.size / 2)
    nq, nz = len(quads), len(z) - 1
    zc = (z[:-1] + z[1:]) / 2
    dz = np.diff(z)
    inside = (zc > 0) & (zc < length)
    solid = np.zeros((nz, nq), dtype=bool)
    if not flow.empty_pipe:
        qc = xy[quads].mean(axis=1) + centre
        # Same TPMS phase and threshold as the mechanical model; the CFD mesh never retunes geometry.
        field = evaluate_priority_field(specimen, qc[None, :, 0], qc[None, :, 1], zc[inside][:, None])
        solid[inside] = np.broadcast_to(field, (int(inside.sum()), nq)) <= geometry_threshold(specimen)
        if not solid.any():
            raise ValueError("Mesh does not resolve any TPMS solid. Increase cells across the diameter.")
    fluid = ~solid
    keep = _through_cells(fluid, quads)
    if not keep[0].any() or not keep[-1].any() or not (keep.any(axis=1)).all():
        raise ValueError("No face-connected water path from inlet to outlet. Refine the mesh or change TPMS geometry.")
    isolated = fluid & ~keep
    layer, quad = np.nonzero(keep)
    conn_full = np.concatenate((quads[quad] + layer[:, None] * len(xy), quads[quad] + (layer[:, None] + 1) * len(xy)), axis=1)
    used, inverse = np.unique(conn_full.ravel(), return_inverse=True)
    pts = np.column_stack((xy[used % len(xy), 0] + centre[0], xy[used % len(xy), 1] + centre[1], z[used // len(xy)])) * .001
    conn = inverse.reshape(-1, 8)
    cells = np.column_stack((np.full(len(conn), 8), conn)).ravel()
    grid = pv.UnstructuredGrid(cells, np.full(len(conn), pv.CellType.HEXAHEDRON), pts)
    grid.cell_data["grid_i"] = quad
    grid.cell_data["grid_j"] = np.zeros(len(quad), dtype=int)
    grid.cell_data["grid_k"] = layer
    inlet_area = float(areas[keep[0]].sum())
    zone = inside[:, None] & np.ones((1, nq), dtype=bool)
    weight = dz[:, None] * areas[None, :]
    metadata = dict(fluid_cells=len(quad), isolated_cells=int(isolated.sum()), solid_cells=int(solid.sum()),
                    effective_diameter_mm=2 * math.sqrt(inlet_area / math.pi), inlet_area_m2=inlet_area * 1e-6,
                    specimen_solid_fraction=float(weight[zone & solid].sum() / weight[zone].sum()),
                    nominal_cell_mm=h, axial_cell_mm=hz, specimen_start_mm=0., specimen_end_mm=float(length),
                    z_centres_mm=zc.tolist(), mesh_style="ogrid", layers=nz, wall_grading=flow.wall_grading,
                    developing_length_mm=flow.developing_length_mm(), warnings=[], **section)
    section_warnings(metadata, flow, specimen)
    return grid, metadata


def section_warnings(metadata, flow, specimen):
    warnings = metadata["warnings"]
    if not flow.empty_pipe and metadata["nominal_cell_mm"] > specimen.cell_size * min(specimen.solid_fraction, 100 - specimen.solid_fraction) / 100 / 6:
        warnings.append("Thin TPMS walls/pores may be under-resolved. Compare finer meshes before interpreting pressure loss.")
    if flow.turbulence == "laminar" and flow.reynolds > 2000:
        warnings.append("High pipe Reynolds number with laminar model; evaluate turbulence and pore-scale Reynolds numbers.")
    if metadata["min_angle_deg"] < 40:
        warnings.append(f"Poor cross-section cells (minimum angle {metadata['min_angle_deg']:.1f}°); increase cells across the diameter.")
    from pipe_flow_theory import estimate_y_plus
    y_plus = estimate_y_plus(flow.reynolds, flow.density, flow.inlet_speed, flow.dynamic_viscosity, metadata["first_cell_centre_mm"])
    metadata["estimated_y_plus"] = y_plus
    if flow.turbulence != "laminar" and not 30 <= y_plus <= 300 and y_plus > 5:
        warnings.append(f"Estimated wall y+ ≈ {y_plus:.3g}. The ω wall function is least reliable for 5 < y+ < 30; adjust cells across or wall grading.")
    total = flow.inlet_length_mm + flow.test_length_mm(specimen) + flow.outlet_length_mm
    if flow.empty_pipe and total < 1.2 * metadata["developing_length_mm"]:
        warnings.append(f"Pipe ({total:.4g} mm) is shorter than ~1.2 entrance lengths ({1.2 * metadata['developing_length_mm']:.4g} mm); flow will not be fully developed, so the friction check is not a fully developed comparison.")
    if metadata.get("mesh_style") == "ogrid":
        warnings.append("Body-fitted O-grid pipe wall. Mesh independence is still required." if flow.empty_pipe else
                        "Body-fitted O-grid pipe wall; TPMS surfaces remain stair-stepped. Mesh independence is still required.")


def _build_cartesian_grid(specimen, flow):
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
    if flow.solver == "kratos":
        return _export_kratos_case(specimen, flow, directory, grid, metadata)
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
    momentum = "bounded Gauss linearUpwind grad(U)" if flow.convection == "linearUpwind" else "bounded Gauss upwind"
    gradients = "default Gauss linear; grad(U) cellLimited Gauss linear 1;" if flow.convection == "linearUpwind" else "default Gauss linear;"
    _write(directory/"system"/"fvSchemes",_header("dictionary","fvSchemes")+f"ddtSchemes {{ default steadyState; }} gradSchemes {{ {gradients} }} divSchemes {{ default none; div(phi,U) {momentum};"+" div(phi,k) bounded Gauss upwind; div(phi,omega) bounded Gauss upwind; div((nuEff*dev2(T(grad(U))))) Gauss linear; } laplacianSchemes { default Gauss linear corrected; } interpolationSchemes { default linear; } snGradSchemes { default corrected; } fluxRequired { default no; p; } wallDist { method meshWave; }\n")
    _write(directory/"system"/"fvSolution",_header("dictionary","fvSolution")+"solvers { p { solver GAMG; tolerance 1e-9; relTol 0.05; smoother GaussSeidel; } \"(U|k|omega)\" { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-9; relTol 0.05; } } SIMPLE { nNonOrthogonalCorrectors 0; consistent yes; residualControl { p 1e-5; U 1e-6; \"(k|omega)\" 1e-5; } } relaxationFactors { fields { p 0.3; } equations { U 0.7; k 0.7; omega 0.7; } }\n")
    _write(directory/"case.foam", "")
    manifest = dict(specimen=asdict(specimen),flow=asdict(flow),mesh=metadata,
                    pressure_units="Pa (OpenFOAM p multiplied by density)",velocity_units="m/s",
                    geometry=("Empty circular pipe" if flow.empty_pipe else "TPMS phase clipped to a concentric cylinder")+f", Z flow; test length {flow.test_length_mm(specimen):g} mm",
                    discretization=("Wall-fitted O-grid hexahedra" if flow.mesh_style=="ogrid" else "Cartesian stair-step finite volumes")+f"; {flow.convection} momentum convection; no-slip walls; uniform velocity inlet; zero gauge pressure outlet")
    (directory/"flow_case.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    _write(directory/"Allrun", "#!/bin/bash\nset -eo pipefail\necho $$ > .runner.pid\ntrap 'rm -f .runner.pid' EXIT\ntest ! -f .cancel-request\ncheckMesh > log.checkMesh 2>&1\ngrep -q 'Mesh OK' log.checkMesh\ntest ! -f .cancel-request\nsimpleFoam > log.simpleFoam 2>&1\n")
    return manifest


def _export_kratos_case(specimen, flow, directory, grid, metadata):
    from kratos_flow import export_kratos_flow_case
    report = export_kratos_flow_case(grid, flow, directory, _write)
    metadata["warnings"].append("Kratos FEM: each hexahedron is split into 6 tetrahedra (P1/P1 stabilised VMS, steady pseudo-time). Compare against a finer mesh before trusting pressure loss.")
    manifest = dict(specimen=asdict(specimen), flow=asdict(flow), mesh=metadata, kratos=report,
                    pressure_units="Pa", velocity_units="m/s",
                    geometry=("Empty circular pipe" if flow.empty_pipe else "TPMS phase clipped to a concentric cylinder")+f", Z flow; test length {flow.test_length_mm(specimen):g} mm",
                    discretization=("Wall-fitted O-grid hexahedra split into tetrahedra" if flow.mesh_style=="ogrid" else "Cartesian stair-step hexahedra split into tetrahedra")
                    +"; Kratos stabilised VMS finite elements, steady pseudo-time; no-slip walls; uniform velocity inlet; zero gauge pressure outlet")
    (Path(directory)/"flow_case.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
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
    if flow.solver == "kratos":
        python = shlex.quote(flow.kratos_python)
        result = _wsl(flow, f"{python} -c 'import KratosMultiphysics.FluidDynamicsApplication' >/dev/null 2>&1 && command -v {python}", capture_output=True, timeout=120)
        if result.returncode:
            raise RuntimeError("Kratos FluidDynamicsApplication unavailable in the WSL Python. Install with: pip install KratosMultiphysics-all. "+result.stderr.decode(errors="replace")[-500:])
        return result.stdout.decode(errors="replace").strip()
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
    report_analysis_step("Checking Kratos runtime…" if flow.solver == "kratos" else "Checking OpenFOAM runtime…")
    check_flow_runtime(flow)
    export_flow_case(specimen,flow,directory)
    case = shlex.quote(linux_path(directory))
    prefix = "" if flow.solver == "kratos" else f"source {shlex.quote(flow.foam_bashrc)} >/dev/null 2>&1 && "
    script = f"{prefix}cd {case} && exec setsid --wait bash Allrun"
    report_analysis_step("Solving flow with Kratos…" if flow.solver == "kratos" else "Checking mesh and solving flow…")
    result = _wsl(flow,script,capture_output=True)
    if result.returncode:
        details = ""
        for filename in ("log.checkMesh","log.simpleFoam","log.kratos"):
            path = Path(directory)/filename
            if path.exists(): details += "\n"+filename+"\n"+path.read_text(errors="replace")[-3500:]
        raise RuntimeError(("Kratos" if flow.solver == "kratos" else "OpenFOAM")+" failed; inspect the case logs. "+result.stderr.decode(errors="replace")[-1000:]+details)
    report_analysis_step("Processing flow results…")
    return summarize_flow_case(directory)


def _last_monitor(directory,name):
    paths = list((Path(directory)/"postProcessing"/name).glob("*/surfaceFieldValue.dat"))
    if not paths: raise RuntimeError(f"Missing OpenFOAM boundary monitor: {name}")
    rows = [line.split() for line in paths[0].read_text().splitlines() if line.strip() and not line.lstrip().startswith("#")]
    return float(rows[-1][0]),float(rows[-1][1])


def _openfoam_fields(directory, flow):
    reader = pv.OpenFOAMReader(directory/"case.foam")
    if max(reader.time_values) <= 0:
        raise RuntimeError("No solved time step exists; initial fields are not CFD results.")
    reader.set_active_time_value(max(reader.time_values))
    reader.disable_all_patch_arrays()
    reader.enable_patch_array("internalMesh")
    grid = reader.read()["internalMesh"]
    if grid is None or "U" not in grid.cell_data or "p" not in grid.cell_data:
        raise RuntimeError("OpenFOAM did not write velocity and pressure fields.")
    velocity = np.asarray(grid.cell_data["U"])
    pressure = np.asarray(grid.cell_data["p"])*flow.density
    if not np.isfinite(velocity).all() or not np.isfinite(pressure).all():
        raise RuntimeError("Non-finite CFD result. Inspect mesh and solver settings.")
    _,pi = _last_monitor(directory,"inlet_p")
    _,po = _last_monitor(directory,"outlet_p")
    _,qi = _last_monitor(directory,"inlet_phi")
    _,qo = _last_monitor(directory,"outlet_phi")
    log = (directory/"log.simpleFoam").read_text(errors="replace")
    if not re.search(r"^End\s*$",log,re.MULTILINE):
        raise RuntimeError("OpenFOAM did not end cleanly; inspect log.simpleFoam.")
    if not np.isfinite([pi,po,qi,qo]).all():
        raise RuntimeError("Non-finite boundary pressure or flow monitor.")
    residuals = {}
    for name,value in re.findall(r"Solving for (\w+), Initial residual = ([\deE+.\-]+)",log):
        residuals[name] = float(value)
    return dict(grid=grid,velocity=velocity,pressure=pressure,p_in=pi*flow.density,p_out=po*flow.density,q_in=-qi,q_out=qo,
                log=log,time=max(reader.time_values),solver_converged="SIMPLE solution converged" in log,residuals=residuals)


def _kratos_fields(directory, flow):
    from kratos_flow import read_kratos_fields
    log = (directory/"log.kratos").read_text(errors="replace")
    if "KRATOS_DONE" not in log:
        raise RuntimeError("Kratos did not finish cleanly; inspect log.kratos.")
    grid = pv.read(directory/"fluid_mesh.vtu")
    fields = read_kratos_fields(directory,grid,flow)
    last = re.findall(r"^STEP \d+ dU=([\deE+.\-]+) dP=([\deE+.\-]+)",log,re.MULTILINE)
    residuals = {"dU":float(last[-1][0]),"dP":float(last[-1][1])} if last else {}
    return dict(grid=grid,velocity=fields["velocity"],pressure=fields["pressure"],p_in=fields["p_in"],p_out=fields["p_out"],
                q_in=fields["q_in"],q_out=fields["q_out"],log=log,time=float(fields["steps"]),
                solver_converged=fields["converged"],residuals=residuals)


def summarize_flow_case(directory):
    directory = Path(directory)
    manifest = json.loads((directory/"flow_case.json").read_text())
    flow = FlowSettings(**manifest["flow"])
    fields = _kratos_fields(directory,flow) if flow.solver == "kratos" else _openfoam_fields(directory,flow)
    grid,velocity,pressure,log = fields["grid"],fields["velocity"],fields["pressure"],fields["log"]
    if not np.isfinite(velocity).all() or not np.isfinite(pressure).all():
        raise RuntimeError("Non-finite CFD result. Inspect mesh and solver settings.")
    grid.cell_data["Velocity_m_s"] = velocity
    grid.cell_data["Speed_m_s"] = np.linalg.norm(velocity,axis=1)
    grid.cell_data["Pressure_Pa"] = pressure
    grid.save(directory/"flow_result.vtu")
    qi,qo = fields["q_in"],fields["q_out"]
    imbalance = abs(qi-qo)/max(abs(qi),abs(qo),1e-30)
    # Equal-order stabilised FEM enforces continuity weakly, so its inlet/outlet flux match is looser than a finite-volume one.
    balance_limit = .03 if flow.solver == "kratos" else .01
    converged = fields["solver_converged"] and imbalance < balance_limit
    residuals = fields["residuals"]
    volume = np.asarray(grid.compute_cell_sizes(length=False,area=False,volume=True).cell_data["Volume"])
    cell_centres = grid.cell_centers().points
    centres = cell_centres[:,2]*1000
    mesh = manifest["mesh"]
    z = np.asarray(mesh["z_centres_mm"])
    end_mm = mesh.get("specimen_end_mm",manifest["specimen"]["size"])
    before,after = z[z<0][-1],z[z>end_mm][0]
    def mean(values,mask): return float(np.average(values[mask],weights=volume[mask]))
    def section(at):
        mask = np.isclose(centres,at,rtol=0,atol=1e-5)
        if not mask.any(): raise RuntimeError("Cannot locate pressure measurement section.")
        return mean(pressure,mask)
    import csv
    profile_z,profile_p = [],[]
    with (directory/"axial_profile.csv").open("w",newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("z_mm","mean_pressure_Pa","mean_axial_velocity_m_s","max_speed_m_s"))
        for at in z:
            mask = np.isclose(centres,at,rtol=0,atol=1e-5)
            if mask.any():
                profile_z.append(float(at)); profile_p.append(mean(pressure,mask))
                writer.writerow((at,profile_p[-1],mean(velocity[:,2],mask),float(np.linalg.norm(velocity[mask],axis=1).max())))
    analysis = _pipe_analysis(flow,manifest,directory,velocity,volume,cell_centres,centres,z,profile_z,profile_p,
                              section(before)-section(after),after-before)
    if flow.solver == "kratos":
        from kratos_flow import write_kratos_residuals
        write_kratos_residuals(directory,log)
    else:
        _write_residual_history(directory,log)
    summary = dict(status="converged" if converged else "not_converged",solver=flow.solver,time=fields["time"],
                   reynolds_diameter=flow.reynolds,pressure_drop_total_Pa=fields["p_in"]-fields["p_out"],
                   pressure_drop_specimen_Pa=section(before)-section(after),
                   specimen_pressure_planes_mm=[float(before),float(after)],
                   pressure_definition="Static, area-averaged gauge pressure. Specimen value uses the adjacent empty-channel cell-centre planes, including entrance/exit effects.",
                   inlet_flow_m3_s=qi,outlet_flow_m3_s=qo,mass_imbalance_fraction=imbalance,
                   maximum_speed_m_s=float(np.linalg.norm(velocity,axis=1).max()),
                   final_initial_residuals=residuals,mesh=manifest["mesh"],warnings=manifest["mesh"]["warnings"])
    summary["pipe_analysis"] = analysis
    summary["warnings"] = list(summary["warnings"]) + list(analysis.get("notes",[]))
    if not converged: summary["warnings"].append(("Kratos pseudo-time change criterion" if flow.solver == "kratos" else "SIMPLE residual criteria")+f" and/or {balance_limit:.0%} mass-balance criterion not satisfied; these are provisional results.")
    (directory/"flow_summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    return summary


def _pipe_analysis(flow,manifest,directory,velocity,volume,cell_centres,centres,z,profile_z,profile_p,specimen_drop,specimen_span_mm):
    """Analytical comparison (empty pipe) or apparent permeability (TPMS fill) plus radial_profile.csv."""
    import csv
    from pipe_flow_theory import analyze_empty_pipe, analyze_porous, radial_profile
    mesh = manifest["mesh"]
    d_eff = mesh.get("effective_diameter_mm",flow.diameter_mm)
    if not flow.empty_pipe:
        return analyze_porous(flow,1-mesh["specimen_solid_fraction"],specimen_span_mm,specimen_drop,d_eff)
    margin = 2*mesh.get("axial_cell_mm",mesh["nominal_cell_mm"])
    analysis = analyze_empty_pipe(flow,d_eff,profile_z,profile_p,margin)
    at = z[z<z.max()-margin][-1]
    mask = np.isclose(centres,at,rtol=0,atol=1e-5)
    middle = manifest["specimen"]["size"]/2
    r = np.hypot(cell_centres[mask,0]-middle*.001,cell_centres[mask,1]-middle*.001)*1000
    rows = radial_profile(r,velocity[mask,2],volume[mask],d_eff/2)
    laminar = analysis["regime"] == "laminar"
    with (directory/"radial_profile.csv").open("w",newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("r_mm","r_over_R","axial_velocity_m_s","poiseuille_velocity_m_s"))
        for radius,speed in rows:
            writer.writerow((float(radius),float(radius/(d_eff/2)),float(speed),
                             float(2*flow.inlet_speed*(1-(radius/(d_eff/2))**2)) if laminar else ""))
    analysis["profile_plane_mm"] = float(at)
    analysis["centreline_to_mean_velocity"] = float(rows[:3,1].mean()/flow.inlet_speed)
    return analysis


def _write_residual_history(directory,log):
    import csv
    history,order,iteration = {},[],0
    for line in log.splitlines():
        step = re.match(r"^Time = (\d+(?:\.\d+)?)\s*$",line)
        if step:
            iteration = float(step.group(1))
            continue
        solved = re.search(r"Solving for (\w+), Initial residual = ([\deE+.\-]+)",line)
        if solved and iteration:
            name = solved.group(1)
            row = history.setdefault(iteration,{})
            if name not in row: row[name] = float(solved.group(2))
            if name not in order: order.append(name)
    with (Path(directory)/"residuals.csv").open("w",newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("iteration",*order))
        for number in sorted(history):
            writer.writerow((number,*(history[number].get(name,"") for name in order)))
