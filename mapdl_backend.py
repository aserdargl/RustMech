"""ANSYS-specific serialization, commands, result mapping and lifecycle."""
from __future__ import annotations

import traceback
from shutil import copyfile
from pathlib import Path
from dataclasses import replace

import numpy as np
import pyvista as pv
from rust_mesh import von_mises, write_apdl

from model import FEMesh, HEX_EDGES, PLATEN_MARGIN_RATIO, Settings, TET_EDGES, validate_settings
from solver_api import (
    AnalysisRequest,
    MAPDL_CAPABILITIES,
    SolverCapabilities,
    SolverExecution,
    map_node_values,
)


_SOLID_ELEMENTS = {
    pv.CellType.HEXAHEDRON: (185, 8),
    pv.CellType.QUADRATIC_HEXAHEDRON: (186, 20),
    pv.CellType.TETRA: (285, 4),
    pv.CellType.QUADRATIC_TETRA: (187, 10),
}
_APDL_NODES_PER_COMMAND = 8
_QUADRATIC_EDGES = {
    pv.CellType.QUADRATIC_HEXAHEDRON: (8, HEX_EDGES),
    pv.CellType.QUADRATIC_TETRA: (4, TET_EDGES),
}


def write_apdl_mesh(mesh: FEMesh, path: Path) -> None:
    grid = mesh.grid
    ids = grid.point_data["ansys_node_num"]
    element_type, connectivity = _solid_connectivity(grid)
    write_apdl(path, grid.points, ids, connectivity, grid.cell_data["ansys_elem_num"], element_type)


def _solid_connectivity(grid):
    if grid.n_cells:
        cell_types = grid.celltypes
        element = _SOLID_ELEMENTS.get(cell_types[0])
        if element is not None and np.all(cell_types == cell_types[0]):
            element_type, width = element
            return element_type, grid.cell_connectivity.reshape(-1, width)
    raise ValueError("Expected a homogeneous HEX8, HEX20, TET4 or TET10 solid mesh.")


PLATEN_TARGET_TYPE = 4
PLATEN_CONTACT_TYPE = 5
BOTTOM_PLATEN_PAIR = 4
TOP_PLATEN_PAIR = 5
SPRING_STIFFNESS_RATIO = 1e-6
FORCE_CONVERGENCE_TOLERANCE = 0.0005
PLATEN_PENETRATION_BUDGET_RATIO = 0.001


def configure_contact(mapdl, settings, element_type, pair, mode):
    """CONTA174 constants use their documented indices, shared by all interfaces."""
    platen = pair in (BOTTOM_PLATEN_PAIR, TOP_PLATEN_PAIR, 9)
    mapdl.keyopt(element_type, 2, {"augmented_lagrange": 0, "penalty": 1}[settings.contact_formulation])
    # For rigid platens, keep material-based penalty stiffness constant within
    # a load step. Iteration-based updates also scale initial stiffness inversely
    # with FTOLN: the strict platen tolerance can otherwise inflate it by 1000x
    # and cause frictional contact chattering. Augmented Lagrange still enforces
    # the requested penetration tolerance through its multiplier iterations.
    # Only use this treatment when the combined penetration allowance is small
    # compared with the prescribed compression. At very small displacements,
    # even converged contact penetration can bias specimen stiffness. Force
    # control has no known displacement budget, so retain adaptive stiffness.
    penetration_budget = 2 * settings.platen_penetration_tolerance_factor * settings.mesh_resolution
    constant_stiffness = (platen and settings.contact_formulation == "augmented_lagrange"
                          and settings.loading_mode == "displacement"
                          and penetration_budget <= settings.compression * PLATEN_PENETRATION_BUDGET_RATIO)
    update = 1 if constant_stiffness else 2
    mapdl.keyopt(element_type, 10, update)
    mapdl.keyopt(element_type, 12, {"rough": 1, "bonded": 6}.get(mode, 0))
    tolerance = (settings.platen_penetration_tolerance_factor if platen
                 else settings.penetration_tolerance_factor)
    mapdl.r(pair, "", "", settings.normal_contact_stiffness_factor, tolerance)
    # FDMN is real constant 31. Constant 14 is thermal contact conductance.
    mapdl.rmodif(pair, 31, settings.contact_damping_factor)


def select_specimen_boundaries(mapdl, mesh: FEMesh) -> None:
    with mapdl.non_interactive:
        for name, ids in (("GY_BOTTOM", mesh.bottom_ids), ("GY_TOP", mesh.top_ids)):
            mapdl.nsel("NONE")
            for node in ids:
                mapdl.nsel("A", "NODE", "", int(node))
            mapdl.cm(name, "NODE")
        mapdl.allsel()


def create_platen_contacts(mapdl, settings: Settings, mesh: FEMesh) -> tuple[int, int]:
    """Create independent finite-sliding Coulomb pairs and return their pilot IDs."""
    from standard_tests import kind, load_sign
    if kind(settings) == 'flexure':
        return create_flexure_contacts(mapdl, settings, mesh)
    if kind(settings) == 'tension':
        settings = replace(settings, bottom_platen_contact='bonded', top_platen_contact='bonded')
    mapdl.et(PLATEN_TARGET_TYPE, 170)
    mapdl.keyopt(PLATEN_TARGET_TYPE, 2, 1)  # Prescribe rigid target motion at the pilot.
    # Each side needs its own type: KEYOPT(12) is a type-level property.
    for element_type, pair, mode in (
            (PLATEN_CONTACT_TYPE, BOTTOM_PLATEN_PAIR, settings.bottom_platen_contact),
            (8, TOP_PLATEN_PAIR, settings.top_platen_contact)):
        mapdl.et(element_type, 174)
        if settings.mesh_type == "HEX20":
            mapdl.keyopt(element_type, 4, 2)
        configure_contact(mapdl, settings, element_type, pair, mode)

    first_node = int(mesh.grid.point_data["ansys_node_num"].max()) + 1
    bottom = create_platen(
        mapdl, settings.size, first_node, BOTTOM_PLATEN_PAIR, "GY_BOTTOM",
        settings.bottom_platen_friction_coeff if settings.bottom_platen_contact == "frictional" else 0.0,
        0.0,
        xy_bounds=mesh.grid.bounds[:4] if settings.imported_geometry_path or settings.test_profile != 'custom' else None,
    )
    top = create_platen(
        mapdl, settings.size, first_node + 5, TOP_PLATEN_PAIR, "GY_TOP",
        settings.top_platen_friction_coeff if settings.top_platen_contact == "frictional" else 0.0,
        load_sign(settings)*settings.compression, contact_type=8,
        xy_bounds=mesh.grid.bounds[:4] if settings.imported_geometry_path or settings.test_profile != 'custom' else None,
    )
    if settings.loading_mode == "force":
        mapdl.ddele(top, "UZ")
        mapdl.f(top, "FZ", load_sign(settings)*settings.applied_force)
    return bottom, top


def create_flexure_contacts(mapdl, settings, mesh):
    """Two fixed cylindrical supports and one moving cylindrical loading nose."""
    from standard_tests import profile
    p=profile(settings)
    length,width,height=p.dimensions
    fixed=[((length-p.span)/2,-p.support_radius),((length+p.span)/2,-p.support_radius)]
    moving=[(length/2,height+p.nose_radius)]
    next_id = int(mesh.grid.point_data['ansys_node_num'].max())+1
    pilots=[]
    mapdl.et(PLATEN_TARGET_TYPE,170)
    mapdl.keyopt(PLATEN_TARGET_TYPE,2,1)
    for cylinders, pair, element_type, boundary, mode in (
        ([fixed[0]],4,5,'GY_BOTTOM',settings.bottom_platen_contact),
        ([fixed[1]],9,9,'GY_BOTTOM',settings.bottom_platen_contact),
        (moving,5,8,'GY_TOP',settings.top_platen_contact)):
        mapdl.et(element_type,174)
        configure_contact(mapdl,settings,element_type,pair,mode)
        # Nodal detection closes the exact initial tangent line. Gauss points
        # between beam nodes lie above the curved support, creating a false
        # seating gap and a free vertical rigid-body mode at small loads.
        mapdl.keyopt(element_type,4,2)
        if settings.contact_formulation=='augmented_lagrange':
            mapdl.keyopt(element_type,10,1)
        radius=p.nose_radius if pair==5 else p.support_radius
        mapdl.rmodif(pair,1,radius)
        coefficient = getattr(settings,('top' if pair==5 else 'bottom')+'_platen_friction_coeff') if mode=='frictional' else 0.
        pilot=next_id+2*len(cylinders)
        pilots.append(pilot)
        with mapdl.non_interactive:
            mapdl.allsel();mapdl.real(pair);mapdl.mat(pair);mapdl.mp('MU',pair,coefficient)
            mapdl.type(PLATEN_TARGET_TYPE)
            # TARGE170 CYLI uses two axis nodes and real constant R1. Exact
            # cylinders avoid facet gaps at the initial line of tangency.
            for i,(x,z) in enumerate(cylinders):
                node=next_id+2*i
                mapdl.n(node,x,-2.,z);mapdl.n(node+1,x,width+2.,z)
                mapdl.run('TSHAPE,CYLI');mapdl.e(node,node+1)
            mapdl.n(pilot,length/2,width/2,cylinders[0][1]);mapdl.run('TSHAPE,PILO');mapdl.e(pilot)
            mapdl.d(pilot,'ALL',0)
            if pair==5:
                if settings.loading_mode=='force':
                    mapdl.ddele(pilot,'UZ');mapdl.f(pilot,'FZ',-settings.applied_force)
                else:mapdl.d(pilot,'UZ',-settings.compression)
            mapdl.esel('S','TYPE','',1);mapdl.cmsel('S',boundary)
            mapdl.type(element_type);mapdl.esurf()
        next_id=pilot+1
    mapdl.allsel()
    return (tuple(pilots[:2]),pilots[2])


def create_platen(mapdl, size, first_node, pair, boundary, coefficient, displacement,
                  contact_type=PLATEN_CONTACT_TYPE, xy_bounds=None):
    """Overlay specimen end faces and create the opposing rigid target plane."""
    is_top = pair == TOP_PLATEN_PAIR
    z = size if is_top else 0.0
    margin = PLATEN_MARGIN_RATIO * size
    low, high = -margin, size + margin
    corners = [(low, low), (high, low), (high, high), (low, high)]
    if xy_bounds is not None:
        xmin, xmax, ymin, ymax = xy_bounds
        corners = [(xmin-margin, ymin-margin), (xmax+margin, ymin-margin),
                   (xmax+margin, ymax+margin), (xmin-margin, ymax+margin)]
    if is_top:
        corners.reverse()
    nodes = tuple(range(first_node, first_node + len(corners)))
    pilot = nodes[-1] + 1

    with mapdl.non_interactive:
        mapdl.allsel()
        mapdl.real(pair)
        mapdl.mp("MU", pair, coefficient)
        mapdl.mat(pair)
        mapdl.type(PLATEN_TARGET_TYPE)
        for node, (x, y) in zip(nodes, corners):
            mapdl.n(node, x, y, z)
        mapdl.run("TSHAPE,QUAD")
        mapdl.e(*nodes)
        center = (size / 2, size / 2) if xy_bounds is None else ((xmin+xmax)/2, (ymin+ymax)/2)
        mapdl.n(pilot, *center, z)
        mapdl.run("TSHAPE,PILO")
        mapdl.e(pilot)
        mapdl.d(pilot, "ALL", 0)
        mapdl.d(pilot, "UZ", displacement)

        mapdl.esel("S", "TYPE", "", 1)
        mapdl.cmsel("S", boundary)
        mapdl.type(contact_type)
        mapdl.esurf()
        mapdl.esel("S", "TYPE", "", contact_type)
        mapdl.esel("R", "REAL", "", pair)
    if not mapdl.get_value("ELEM", 0, "COUNT"):
        raise ValueError(f"No specimen contact faces generated for {boundary}.")
    mapdl.allsel()
    return pilot


def stabilize_in_plane(mapdl, settings: Settings, mesh: FEMesh, first_free=None) -> None:
    """Use weak lateral springs for the three in-plane rigid-body modes."""
    ids = mesh.grid.point_data["ansys_node_num"]
    bottom_points = map_node_values(ids, mesh.grid.points, mesh.bottom_ids)
    first = bottom_points[0]
    offsets = bottom_points[:, :2] - first[:2]
    farthest = int(np.argmax(np.sum(offsets**2, axis=1)))
    # A spring perpendicular to the dominant offset prevents rigid rotation about Z.
    rotation_dof = "UY" if abs(offsets[farthest, 0]) >= abs(offsets[farthest, 1]) else "UX"
    ground = int(ids.max()) + 11 if first_free is None else first_free
    with mapdl.non_interactive:
        mapdl.n(ground, *first)
        for element_type, dof in ((6, "UX"), (7, "UY")):
            mapdl.et(element_type, 14)
            mapdl.r(element_type, settings.young_modulus * mesh.spacing * SPRING_STIFFNESS_RATIO)
            mapdl.real(element_type)
            mapdl.keyopt(element_type, 2, 1 if dof == "UX" else 2)
            mapdl.type(element_type)
            mapdl.d(ground, dof, 0)
            mapdl.e(int(mesh.bottom_ids[0]), ground)
        mapdl.n(ground + 1, *bottom_points[farthest])
        mapdl.type(6 if rotation_dof == "UX" else 7)
        mapdl.real(6 if rotation_dof == "UX" else 7)
        mapdl.d(ground + 1, rotation_dof, 0)
        mapdl.e(int(mesh.bottom_ids[farthest]), ground + 1)
        mapdl.allsel()


def prepare_ansys(mapdl, settings: Settings, mesh: FEMesh) -> tuple[int, int]:
    mapdl.prep7()
    mapdl.mp("EX", 1, settings.young_modulus)
    mapdl.mp("PRXY", 1, settings.poisson_ratio)
    if settings.material_model == "bilinear_isotropic":
        mapdl.tb("PLAS", 1, 1, "", "BISO")
        plastic_tangent = (
            settings.young_modulus
            * settings.tangent_modulus
            / (settings.young_modulus - settings.tangent_modulus)
        )
        mapdl.tbdata(1, settings.yield_stress, plastic_tangent)
    elif settings.material_model == "multilinear_isotropic":
        mapdl.tb("PLAS", 1, 1, len(settings.hardening_curve), "MISO")
        for strain, stress in settings.hardening_curve:
            mapdl.tbpt("DEFI", strain, stress)
    select_specimen_boundaries(mapdl, mesh)
    bottom_pilot, top_pilot = create_platen_contacts(mapdl, settings, mesh)
    from standard_tests import kind
    if kind(settings)=='flexure':
        from standard_tests import profile
        p=profile(settings)
        points=map_node_values(mesh.grid.point_data['ansys_node_num'],mesh.grid.points,mesh.bottom_ids)
        anchors=[]
        for x in ((p.dimensions[0]-p.span)/2,(p.dimensions[0]+p.span)/2):
            target=np.array([x,p.dimensions[1]/2,0.])
            anchors.append(int(mesh.bottom_ids[np.argmin(np.linalg.norm(points-target,axis=1))]))
        # A pin/roller guide removes the in-plane rigid-body modes without
        # adding artificial vertical stiffness or friction at the contacts.
        mapdl.d(anchors[0],'UX',0.)
        for node in anchors:mapdl.d(node,'UY',0.)
    else:
        stabilize_in_plane(mapdl, settings, mesh)

    # Internal Self-Contact & Self-Friction on cellular walls (Material ID 3, Real 3, TARGE170/CONTA174)
    if getattr(settings, "self_contact_mode", "disabled") != "disabled":
        mu_self = (
            0.0
            if settings.self_contact_mode == "frictionless"
            else float(settings.self_friction_coeff)
        )

        mapdl.mp("MU", 3, mu_self)
        mapdl.et(2, 170)
        mapdl.et(3, 174)
        configure_contact(mapdl, settings, 3, 3, settings.self_contact_mode)
        mapdl.keyopt(3, 9, 1)   # Exclude initial geometrical penetration
        mapdl.allsel()
        mapdl.esel("S", "TYPE", "", 1)
        mapdl.nsel("S", "EXT")
        mapdl.cmsel("U", "GY_BOTTOM")
        mapdl.cmsel("U", "GY_TOP")
        mapdl.real(3)
        mapdl.mat(3)
        mapdl.type(2)
        mapdl.esurf()
        mapdl.type(3)
        mapdl.esurf()
    mapdl.allsel()
    mapdl.finish()
    # Keep nodal records readable by PyMAPDL Reader.
    mapdl.run("/FCOMP,RST,0")
    mapdl.slashsolu()
    mapdl.antype("STATIC")
    mapdl.nlgeom("ON")
    mapdl.cnvtol("F", "", FORCE_CONVERGENCE_TOLERANCE)
    mapdl.autots("ON")
    mapdl.kbc(0)
    minimum, maximum = settings.substep_limits
    mapdl.nsubst(settings.substeps, maximum, minimum)
    mapdl.neqit(50)
    mapdl.lnsrch("ON")
    mapdl.time(1.0)
    mapdl.outres("ALL", "ALL")
    mapdl.save()
    return bottom_pilot, top_pilot


def verify_imported_mesh(mapdl, expected: FEMesh) -> None:
    grid = expected.grid
    element_type, connectivity = _solid_connectivity(grid)
    imported = mapdl.mesh
    target_nodes = grid.point_data["ansys_node_num"]
    points = map_node_values(imported.nnum, imported.nodes, target_nodes)
    if not np.allclose(points, grid.points, rtol=0, atol=1e-10):
        raise ValueError("MAPDL import changed FE node coordinates.")
    if len(imported.enum) != grid.n_cells or not np.all(imported.etype == element_type):
        raise ValueError(f"MAPDL import changed SOLID{element_type} cells.")
    # The visualization grid may omit midside nodes. Check the native records.
    elements = np.stack(imported.elem)
    actual = map_node_values(
        elements[:, 8], elements[:, 10:], grid.cell_data["ansys_elem_num"]
    )
    if not np.array_equal(actual, target_nodes[connectivity]):
        raise ValueError("MAPDL import changed FE connectivity.")


def read_displacement(result, index: int, target_ids) -> np.ndarray:
    labels = result.result_dof(index)
    if not all(name in labels for name in ("UX", "UY", "UZ")):
        raise ValueError("Result set has no complete displacement vector.")
    ids, values = result.nodal_solution(index)
    columns = [labels.index(name) for name in ("UX", "UY", "UZ")]
    return map_node_values(ids, np.asarray(values)[:, columns], target_ids)


def reaction_totals(result, index: int, bottom_ids, top_ids) -> tuple[float, float]:
    forces, ids, dofs = (np.asarray(value) for value in result.nodal_reaction_forces(index))
    if not (forces.ndim == ids.ndim == dofs.ndim == 1 and len(forces) == len(ids) == len(dofs)):
        raise ValueError("Inconsistent reaction record dimensions.")
    if not np.all(np.isfinite(forces)):
        raise ValueError("Non-finite reaction values.")
    labels = result.result_dof(index)
    uz = labels.index("UZ") + 1
    totals = []
    for boundary in (bottom_ids, top_ids):
        mask = (dofs == uz) & np.isin(ids, boundary)
        if not np.array_equal(np.sort(ids[mask]), np.sort(boundary)):
            raise ValueError("Missing or duplicate constrained UZ reaction records.")
        totals.append(float(np.sum(forces[mask])))
    return totals[0], totals[1]


class MapdlResults:
    """Adapt MAPDL reader data to the solver-independent result contract."""

    def __init__(
        self, reader, platen_pilots: tuple[int, int], grid: pv.UnstructuredGrid | None = None,
        settings: Settings | None = None,
    ):
        self.reader = reader
        self.platen_pilots = platen_pilots
        self.grid = grid
        self.settings = settings
        self.quadratic_topology = (
            _QUADRATIC_EDGES.get(grid.celltypes[0]) if grid is not None and grid.n_cells else None
        )

    @property
    def stress_definition(self) -> str:
        definition = "von Mises of averaged nodal stress tensor"
        if self.quadratic_topology:
            definition += " (missing midside tensors interpolated from edge endpoints)"
        return definition

    @property
    def nsets(self) -> int:
        return int(self.reader.nsets)

    @property
    def time_values(self) -> np.ndarray:
        return np.asarray(self.reader.time_values, dtype=float)

    def displacement(self, index: int, target_ids: np.ndarray) -> np.ndarray:
        return read_displacement(self.reader, index, target_ids)

    def equivalent_stress(self, index: int, target_ids: np.ndarray) -> np.ndarray:
        if self.quadratic_topology:
            ids, stress = self.reader.nodal_stress(index)
            return self._quadratic_equivalent_stress(ids, stress, target_ids)
        ids, principal = self.reader.principal_nodal_stress(index)
        return map_node_values(ids, np.asarray(principal)[:, 4], target_ids)

    def _quadratic_equivalent_stress(self, ids, stress, target_ids):
        corner_count, edges = self.quadratic_topology
        cells = self.grid.cell_connectivity.reshape(-1, corner_count + len(edges))
        grid_ids = self.grid.point_data["ansys_node_num"]
        corners = np.unique(cells[:, :corner_count])
        tensors = np.full((len(grid_ids), 6), np.nan)
        tensors[corners] = map_node_values(ids, stress, grid_ids[corners])
        # MAPDL may store stress only at the corner nodes of quadratic solids.
        valid = np.isfinite(stress).all(axis=1)
        _, grid_indices, stress_indices = np.intersect1d(grid_ids, ids[valid], return_indices=True)
        tensors[grid_indices] = stress[valid][stress_indices]
        for slot, (a, b) in enumerate(edges, start=corner_count):
            midsides = cells[:, slot]
            missing = ~np.isfinite(tensors[midsides, 0])
            tensors[midsides[missing]] = (
                tensors[cells[missing, a]] + tensors[cells[missing, b]]
            ) / 2
        equivalent = von_mises(tensors)
        return map_node_values(grid_ids, equivalent, target_ids)

    def top_platen_displacement(self, index: int) -> float:
        _, top = self.platen_pilots
        return float(self.displacement(index, np.array([top]))[0, 2])

    def platen_reactions(self, index: int) -> tuple[float, float]:
        bottom, top = self.platen_pilots
        if self.settings is not None and self.settings.loading_mode == "force":
            # UZ is free at the loaded pilot, so it has no constrained RFZ.
            bottom_reaction, _ = reaction_totals(self.reader, index, np.atleast_1d(bottom), np.array([], dtype=int))
            from standard_tests import load_sign
            return bottom_reaction, load_sign(self.settings)*self.settings.applied_force * self.time_values[index]
        return reaction_totals(self.reader, index, np.atleast_1d(bottom), np.array([top]))

    @property
    def force_source(self):
        if self.settings is not None:
            from standard_tests import kind
            if kind(self.settings)=='tension':
                return ('Prescribed tensile FZ, balanced against fixed-grip RFZ' if self.settings.loading_mode=='force'
                        else 'Positive UZ reaction at the moving grip pilot, balanced against fixed-grip RFZ')
        return ("prescribed top platen FZ (ramped); balanced against bottom RFZ"
                if self.settings is not None and self.settings.loading_mode == "force" else
                "negative UZ reaction at the top platen pilot node")


class MapdlBackend:
    """Own the MAPDL lifecycle; inject launcher and binary reader at composition."""

    def __init__(
        self,
        launcher,
        binary_reader,
        capabilities: SolverCapabilities = MAPDL_CAPABILITIES,
    ):
        self.launcher = launcher
        self.binary_reader = binary_reader
        self.capabilities = capabilities

    def solve(self, request: AnalysisRequest) -> SolverExecution:
        errors = (
            validate_settings(request.settings)
            + self.capabilities.validate(request.settings)
            + request.mesh.solve_errors
        )
        if errors:
            return SolverExecution(error="\n".join(errors))
        execution = SolverExecution(diagnostics={
            "parallel_mode": "SMP",
            "mesh_source": "provided_fe_mesh",
            "specimen_remeshed": False,
            "mesh_import_verified": False,
            "specimen_nodes": int(request.mesh.grid.n_points),
            "specimen_elements": int(request.mesh.grid.n_cells),
        })
        mapdl = None
        directory = request.run_directory
        try:
            write_apdl_mesh(request.mesh, directory / "gyroid_mesh.inp")
            mapdl = self.launcher(
                run_location=str(directory),
                jobname="gyroid_nonlinear",
                start_instance=True,
                nproc=request.settings.cores,
                additional_switches="-smp",
                log_apdl=str(directory / "commands.apdl"),
                mapdl_output=str(directory / "mapdl_process.out"),
            )
            mapdl.clear()
            mapdl.input(str(directory / "gyroid_mesh.inp"))
            verify_imported_mesh(mapdl, request.mesh)
            execution.diagnostics["mesh_import_verified"] = True
            platen_pilots = prepare_ansys(mapdl, request.settings, request.mesh)
            execution.solve_attempted = True
            try:
                output = mapdl.solve()
                (directory / "solve_output.txt").write_text(str(output), encoding="utf-8")
            except Exception as exc:
                execution.error = str(exc)
                execution.diagnostics["solver_error_type"] = type(exc).__name__
                (directory / "solve_error.txt").write_text(
                    traceback.format_exc(), encoding="utf-8"
                )
                # gRPC redirects command output to anstmp, overriding /OUTPUT.
                # Preserve it before FINISH/EXIT can replace the original log.
                try:
                    if (directory / "anstmp").is_file():
                        copyfile(directory / "anstmp", directory / "solve_output.txt")
                except OSError as log_error:
                    execution.diagnostics["output_capture_warning"] = str(log_error)
            else:
                mapdl.finish()
        except Exception as exc:
            execution.error = "; ".join(filter(None, (execution.error, str(exc))))
            (directory / "backend_error.txt").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
        finally:
            if mapdl is not None:
                try:
                    mapdl.exit()
                except Exception as exc:
                    execution.diagnostics["shutdown_warning"] = str(exc)
        if execution.solve_attempted:
            try:
                result_path = directory / "gyroid_nonlinear.rst"
                if result_path.exists() and result_path.stat().st_size == 0:
                    raise ValueError("MAPDL wrote an empty result file; no converged result set is available. See solve_output.txt.")
                reader = self.binary_reader(result_path)
                execution.results = MapdlResults(reader, platen_pilots, request.mesh.grid, request.settings)
            except Exception as exc:
                execution.error = "; ".join(
                    filter(None, (execution.error, f"Result read failed: {exc}"))
                )
        return execution


def create_mapdl_backend() -> MapdlBackend:
    from ansys.mapdl.core import launch_mapdl
    from ansys.mapdl.reader import read_binary

    return MapdlBackend(launch_mapdl, read_binary)
