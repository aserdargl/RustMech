"""Kratos Multiphysics (finite element, stabilised incompressible Navier-Stokes) back end for pipe flow.

The fluid mesh is the same body-fitted O-grid used for OpenFOAM. Every hexahedron is split into six
tetrahedra with a vertex-index rule that keeps neighbouring cells conforming, so TPMS-clipped meshes
stay watertight. Kratos runs in WSL; results come back as plain NumPy arrays.
"""
import json
import re
from pathlib import Path

import numpy as np

SKIN_PARTS = ("Inlet3D_inlet", "Outlet3D_outlet", "NoSlip3D_walls")
MODEL_PART = "FluidModelPart"
CONVERGENCE_TOLERANCE = 1e-4   # relative change of velocity and pressure between pseudo-steps; below the linear-solver noise of fine meshes


def hex_to_tets(conn, points):
    """Split hexahedra (VTK order) into positively oriented tetrahedra; returns ``(n*6, 4)``."""
    conn = np.asarray(conn)
    triangles = np.array(((0, 1, 2), (0, 2, 3)))
    bottom = conn[:, triangles]            # (n, 2, 3) bottom triangle vertices
    top = conn[:, triangles + 4]
    order = np.argsort(bottom, axis=2, kind="stable")
    p = np.take_along_axis(bottom, order, axis=2)
    q = np.take_along_axis(top, order, axis=2)
    # Staircase decomposition of a triangular prism; the side-face diagonal depends only on vertex order.
    tets = np.stack((
        np.stack((p[..., 0], p[..., 1], p[..., 2], q[..., 2]), axis=-1),
        np.stack((p[..., 0], p[..., 1], q[..., 1], q[..., 2]), axis=-1),
        np.stack((p[..., 0], q[..., 0], q[..., 1], q[..., 2]), axis=-1)), axis=2).reshape(-1, 4)
    a, b, c, d = (points[tets[:, i]] for i in range(4))
    negative = np.einsum("ij,ij->i", np.cross(b - a, c - a), d - a) < 0
    tets[negative] = tets[negative][:, (0, 1, 3, 2)]
    return tets


def boundary_faces(tets):
    """Outward-oriented triangles that belong to a single tetrahedron, as ``(m, 3)`` vertex ids."""
    faces = np.concatenate((tets[:, (1, 2, 3)], tets[:, (0, 3, 2)], tets[:, (0, 1, 3)], tets[:, (0, 2, 1)]))
    key = np.sort(faces, axis=1)
    _, inverse, count = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    return faces[count[inverse.ravel()] == 1]


def classify_faces(faces, points):
    z = points[:, 2]
    low, high = z.min(), z.max()
    tolerance = 1e-9 * max(high - low, 1e-30)
    fz = z[faces]
    inlet = np.all(np.abs(fz - low) < tolerance, axis=1)
    outlet = np.all(np.abs(fz - high) < tolerance, axis=1)
    return {"Inlet3D_inlet": faces[inlet], "Outlet3D_outlet": faces[outlet], "NoSlip3D_walls": faces[~inlet & ~outlet]}


def inlet_flux_scale(points, groups):
    """Factor on the inlet speed that restores the nominal flow rate.

    Rim nodes of the inlet are shared with the no-slip wall and stay at zero, so a uniform nodal inlet value
    delivers less than ``speed * area``. The scale makes the integrated inlet flux exact.
    """
    faces = groups["Inlet3D_inlet"]
    rim = np.zeros(len(points), dtype=bool)
    rim[np.unique(groups["NoSlip3D_walls"])] = True
    p = points[faces]
    area = .5 * np.abs(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])[:, 2])
    free = (~rim[faces]).mean(axis=1)
    delivered = float((area * free).sum())
    if delivered <= 0:
        raise ValueError("Kratos inlet has no free nodes; refine the mesh.")
    return float(area.sum() / delivered)


def _block(name, rows):
    return f"Begin {name}\n" + "\n".join(rows) + f"\nEnd {name.split()[0]}\n"


def write_mdpa(points, tets, groups, path):
    count = len(tets)
    lines = ["Begin ModelPartData\nEnd ModelPartData\n", "Begin Properties 1\nEnd Properties\n",
             _block("Nodes", [f"{i + 1} {x:.14g} {y:.14g} {z:.14g}" for i, (x, y, z) in enumerate(points)]),
             _block("Elements Element3D4N", [f"{i + 1} 1 {a + 1} {b + 1} {c + 1} {d + 1}" for i, (a, b, c, d) in enumerate(tets)])]
    condition_id = 1
    ranges = {}
    rows = []
    for name, faces in groups.items():
        ranges[name] = (condition_id, condition_id + len(faces) - 1)
        for a, b, c in faces:
            rows.append(f"{condition_id} 1 {a + 1} {b + 1} {c + 1}")
            condition_id += 1
    lines.append(_block("Conditions SurfaceCondition3D3N", rows))
    lines.append("Begin SubModelPart Parts_Fluid\n" + _block("SubModelPartNodes", [str(i + 1) for i in range(len(points))])
                 + _block("SubModelPartElements", [str(i + 1) for i in range(count)]) + "End SubModelPart\n")
    for name, faces in groups.items():
        first, last = ranges[name]
        nodes = np.unique(faces)
        lines.append(f"Begin SubModelPart {name}\n" + _block("SubModelPartNodes", [str(i + 1) for i in nodes])
                     + _block("SubModelPartConditions", [str(i) for i in range(first, last + 1)]) + "End SubModelPart\n")
    Path(path).write_text("\n".join(lines), encoding="ascii", newline="\n")


def project_parameters(flow, steps, inlet_scale=1.0):
    def process(kind, part, variable, value, constrained):
        module = "assign_vector_variable_process" if kind == "vector" else "assign_scalar_variable_process"
        return {"python_module": module, "kratos_module": "KratosMultiphysics",
                "process_name": "AssignVectorVariableProcess" if kind == "vector" else "AssignScalarVariableProcess",
                "Parameters": {"model_part_name": f"{MODEL_PART}.{part}", "variable_name": variable,
                               "constrained": constrained, "value": value, "interval": [0.0, "End"]}}
    speed = flow.inlet_speed
    return {
        "problem_data": {"problem_name": "flow", "parallel_type": "OpenMP", "echo_level": 0, "start_time": 0.0, "end_time": float(steps)},
        "solver_settings": {
            "model_part_name": MODEL_PART, "domain_size": 3, "solver_type": "Monolithic", "analysis_type": "non_linear",
            "model_import_settings": {"input_type": "mdpa", "input_filename": "flow"},
            "material_import_settings": {"materials_filename": "FluidMaterials.json"},
            "formulation": {"element_type": "vms"},
            "time_scheme": "steady", "velocity_relaxation": 0.9, "pressure_relaxation": 0.9,
            "maximum_iterations": 12,
            "relative_velocity_tolerance": 1e-5, "absolute_velocity_tolerance": 1e-9,
            "relative_pressure_tolerance": 1e-5, "absolute_pressure_tolerance": 1e-9,
            "volume_model_part_name": "Parts_Fluid", "skin_parts": list(SKIN_PARTS), "no_skin_parts": [],
            "time_stepping": {"automatic_time_step": False, "time_step": 1.0},
            "linear_solver_settings": {"solver_type": "amgcl", "smoother_type": "ilu0", "krylov_type": "lgmres",
                                       "coarsening_type": "aggregation", "max_iteration": 400, "provide_coordinates": False,
                                       "gmres_krylov_space_dimension": 100, "verbosity": 0, "tolerance": 1e-9,
                                       "scaling": False, "use_block_matrices_if_possible": True, "coarse_enough": 5000}},
        "processes": {
            "initial_conditions_process_list": [process("vector", "Parts_Fluid", "VELOCITY", [0.0, 0.0, speed], False)],
            "boundary_conditions_process_list": [
                process("vector", "Inlet3D_inlet", "VELOCITY", [0.0, 0.0, speed * inlet_scale], [True, True, True]),
                process("vector", "NoSlip3D_walls", "VELOCITY", [0.0, 0.0, 0.0], [True, True, True]),
                process("scalar", "Outlet3D_outlet", "PRESSURE", 0.0, True)],
            "gravity": []}}


def fluid_materials(flow):
    return {"properties": [{"model_part_name": f"{MODEL_PART}.Parts_Fluid", "properties_id": 1,
                            "Material": {"constitutive_law": {"name": "Newtonian3DLaw"},
                                         "Variables": {"DENSITY": flow.density, "DYNAMIC_VISCOSITY": flow.dynamic_viscosity},
                                         "Tables": {}}}]}


RUNNER = '''"""Kratos steady incompressible pipe-flow driver. Writes kratos_result.npz and log lines parsed by RustMech."""
import sys
from pathlib import Path
import numpy as np
import KratosMultiphysics as K
from KratosMultiphysics.FluidDynamicsApplication.fluid_dynamics_analysis import FluidDynamicsAnalysis

TOLERANCE = %(tolerance)r


class Analysis(FluidDynamicsAnalysis):
    def __init__(self, model, parameters):
        super().__init__(model, parameters)
        self.change = (float("inf"), float("inf"))
        self.previous = None
        self.calm = 0
        self.step_number = 0

    def _fields(self):
        part = self.model[%(part)r]
        return (np.array([n.GetSolutionStepValue(K.VELOCITY) for n in part.Nodes]),
                np.array([n.GetSolutionStepValue(K.PRESSURE) for n in part.Nodes]))

    def KeepAdvancingSolutionLoop(self):
        if Path(".cancel-request").exists():
            print("KRATOS_CANCELLED", flush=True)
            return False
        return super().KeepAdvancingSolutionLoop() and self.calm < 2

    def FinalizeSolutionStep(self):
        super().FinalizeSolutionStep()
        velocity, pressure = self._fields()
        self.step_number += 1
        if self.previous is not None:
            dv = np.linalg.norm(velocity - self.previous[0]) / max(np.linalg.norm(velocity), 1e-30)
            dp = np.linalg.norm(pressure - self.previous[1]) / max(np.linalg.norm(pressure), 1e-30)
            print("STEP %%d dU=%%.6e dP=%%.6e" %% (self.step_number, dv, dp), flush=True)
            self.calm = self.calm + 1 if max(dv, dp) < TOLERANCE else 0
        self.previous = (velocity, pressure)


def main():
    with open("ProjectParameters.json") as stream:
        parameters = K.Parameters(stream.read())
    model = K.Model()
    analysis = Analysis(model, parameters)
    analysis.Run()
    part = model[%(part)r]
    nodes = sorted(part.Nodes, key=lambda n: n.Id)
    velocity = np.array([n.GetSolutionStepValue(K.VELOCITY) for n in nodes])
    pressure = np.array([n.GetSolutionStepValue(K.PRESSURE) for n in nodes])
    np.savez("kratos_result.npz", velocity=velocity, pressure=pressure,
             node_ids=np.array([n.Id for n in nodes]), steps=analysis.step_number, converged=analysis.calm >= 2)
    print("KRATOS_CONVERGED" if analysis.calm >= 2 else "KRATOS_NOT_CONVERGED", flush=True)
    print("KRATOS_DONE", flush=True)


if __name__ == "__main__":
    main()
'''


def export_kratos_flow_case(grid, flow, directory, write_text):
    """Write mesh, parameters and driver. ``write_text(path, text)`` is the shared LF-safe writer."""
    directory = Path(directory)
    conn = grid.cells.reshape(-1, 9)[:, 1:]
    points = np.asarray(grid.points)
    tets = hex_to_tets(conn, points)
    groups = classify_faces(boundary_faces(tets), points)
    if not all(len(face) for face in groups.values()):
        raise ValueError("Kratos mesh export found no inlet, outlet or wall faces.")
    write_mdpa(points, tets, groups, directory / "flow.mdpa")
    steps = min(flow.iterations, 200)
    scale = inlet_flux_scale(points, groups)
    write_text(directory / "ProjectParameters.json", json.dumps(project_parameters(flow, steps, scale), indent=2))
    write_text(directory / "FluidMaterials.json", json.dumps(fluid_materials(flow), indent=2))
    write_text(directory / "run_kratos.py", RUNNER % {"tolerance": CONVERGENCE_TOLERANCE, "part": MODEL_PART})
    write_text(directory / "Allrun", "#!/bin/bash\nset -eo pipefail\necho $$ > .runner.pid\ntrap 'rm -f .runner.pid' EXIT\n"
               f"test ! -f .cancel-request\n{flow.kratos_python} run_kratos.py > log.kratos 2>&1\n")
    return dict(tetrahedra=int(len(tets)), nodes=int(len(points)), inlet_velocity_scale=scale,
                boundary_faces={k: int(len(v)) for k, v in groups.items()})


def read_kratos_fields(directory, grid, flow):
    """Cell-centred velocity/pressure on the hexahedral grid plus inlet/outlet monitors."""
    directory = Path(directory)
    data = np.load(directory / "kratos_result.npz")
    velocity_nodes, pressure_nodes = data["velocity"], data["pressure"]
    if len(velocity_nodes) != grid.n_points or not np.isfinite(velocity_nodes).all() or not np.isfinite(pressure_nodes).all():
        raise RuntimeError("Kratos result does not match the exported mesh or is not finite.")
    conn = grid.cells.reshape(-1, 9)[:, 1:]
    velocity = velocity_nodes[conn].mean(axis=1)
    pressure = pressure_nodes[conn].mean(axis=1)
    points = np.asarray(grid.points)
    z = points[:, 2]

    def face_monitor(columns, level):
        face = conn[:, columns]
        mask = np.all(np.abs(z[face] - level) < 1e-9 * max(z.max() - z.min(), 1e-30), axis=1)
        quads = face[mask]
        # Same diagonal as hex_to_tets, so fluxes are the exact P1 integrals of the solved field.
        triangles = np.concatenate((quads[:, (0, 1, 2)], quads[:, (0, 2, 3)]))
        p = points[triangles]
        a = .5 * np.abs(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])[:, 2])
        mean_p = pressure_nodes[triangles].mean(axis=1)
        mean_w = velocity_nodes[triangles][:, :, 2].mean(axis=1)
        return float((mean_p * a).sum() / a.sum()), float((mean_w * a).sum())

    p_in, q_in = face_monitor((0, 1, 2, 3), z.min())
    p_out, q_out = face_monitor((4, 5, 6, 7), z.max())
    return dict(velocity=velocity, pressure=pressure, p_in=p_in, p_out=p_out, q_in=q_in, q_out=q_out,
                steps=int(data["steps"]), converged=bool(data["converged"]))


def write_kratos_residuals(directory, log):
    import csv
    rows = re.findall(r"^STEP (\d+) dU=([\deE+.\-]+) dP=([\deE+.\-]+)", log, re.MULTILINE)
    with (Path(directory) / "residuals.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("iteration", "dU", "dP"))
        for step, du, dp in rows:
            writer.writerow((int(step), float(du), float(dp)))
    return rows
