"""Export the exact RustMech solid mesh and explicit contact surfaces to Kratos."""
from dataclasses import asdict
import hashlib
import math
import json
from pathlib import Path
import shutil
import sys

import numpy as np
from solver_api import KRATOS_QUADRATIC_MESH_TYPES, kratos_quadratic_platen_errors
from tools.truba.kratos_self_contact import contact_partition


SOLID_CELLS = {"HEX8": (12, 8), "HEX20": (25, 20), "TET4": (10, 4), "TET10": (24, 10)}
# Kratos orders the HEX20 vertical edge nodes before the top edge nodes.
HEX20_KRATOS_ORDER = (*range(12), *range(16, 20), *range(12, 16))
# Outward-facing linear mortar facets on the existing quadratic face nodes.
QUADRATIC_FACE_TRIANGLES = {
    6: ((0, 3, 5), (1, 4, 3), (2, 5, 4), (3, 4, 5)),
    8: ((0, 4, 7), (1, 5, 4), (2, 6, 5), (3, 7, 6), (4, 5, 6), (4, 6, 7)),
}


def mesh_digest(points, cells, ids):
    digest = hashlib.sha256()
    for array, dtype in ((points, '<f8'), (cells, '<i8'), (ids, '<i8')):
        digest.update(np.ascontiguousarray(array, dtype=dtype).tobytes())
    return digest.hexdigest()


def material_properties(settings):
    values = {"YOUNG_MODULUS": settings.young_modulus, "POISSON_RATIO": settings.poisson_ratio,
              "DENSITY": settings.material_density * 1e-9}
    law = "LinearElastic3DLaw"
    if settings.material_model != "linear_elastic":
        law = "FiniteStrainIsotropicPlasticity3DVonMisesVonMises"
        if settings.material_model == "bilinear_isotropic":
            slope = settings.young_modulus * settings.tangent_modulus / (settings.young_modulus - settings.tangent_modulus)
            curve = [(0., settings.yield_stress), (100., settings.yield_stress + 100 * slope)]
        else:
            curve = list(settings.hardening_curve)
            slope = (curve[-1][1] - curve[-2][1]) / (curve[-1][0] - curve[-2][0])
            end = max(100., curve[-1][0] * 10)
            curve.append((end, curve[-1][1] + slope * (end - curve[-1][0])))
        energy = sum(.5 * (a[1]+b[1]) * (b[0]-a[0]) for a,b in zip(curve,curve[1:]))
        values.update(YIELD_STRESS=curve[0][1], HARDENING_CURVE=6, FRACTURE_ENERGY=max(1e4,10*energy*settings.size),
                      PLASTIC_STRAIN_VECTOR_PLASTICITY_POINT_CURVE=[p[0] for p in curve],
                      EQUIVALENT_STRESS_VECTOR_PLASTICITY_POINT_CURVE=[p[1] for p in curve])
    return {"model_part_name": "Structure.Specimen", "properties_id": 1,
            "Material": {"constitutive_law": {"name": law}, "Variables": values, "Tables": {}}}


def specimen_surfaces(grid, ids, points, *, quadratic):
    if quadratic:
        import pyvista as pv
        from vtkmodules.vtkFiltersGeometry import vtkUnstructuredGridGeometryFilter

        # Retain whole quadratic faces so platen membership is decided before
        # triangulation. VTK's default tessellation can include collinear nodes.
        boundary = vtkUnstructuredGridGeometryFilter()
        boundary.MergingOff()
        boundary.PassThroughPointIdsOn()
        boundary.SetInputData(grid)
        boundary.Update()
        skin = pv.wrap(boundary.GetOutput())
    else:
        skin = grid.extract_surface(algorithm="dataset_surface")
    original = np.asarray(skin.point_data["vtkOriginalPointIds"])
    zmin, zmax = points[:, 2].min(), points[:, 2].max()
    tolerance = max((zmax - zmin) * 1e-9, 1e-12)
    surfaces = {"BottomSpecimen": [], "TopSpecimen": [], "SelfSurface": []}
    for i in range(skin.n_cells):
        indices = original[np.asarray(skin.get_cell(i).point_ids)]
        z = points[indices, 2]
        if np.all(abs(z - zmin) < tolerance):
            key = "BottomSpecimen"
        elif np.all(abs(z - zmax) < tolerance):
            key = "TopSpecimen"
        elif quadratic:
            continue  # Quadratic elements have platen contact only.
        else:
            key = "SelfSurface"
        face = ids[indices]
        if quadratic:
            triangles = QUADRATIC_FACE_TRIANGLES[len(face)]
            surfaces[key].extend(face[list(triangle)].tolist() for triangle in triangles)
        else:
            surfaces[key].append(face.tolist())
    return surfaces


def export_kratos(mesh, settings, directory):
    from standard_tests import kind, profile, load_sign, profile_report
    experiment=kind(settings)
    tension=experiment=='tension'
    kinematic=experiment!='compression'
    quadratic = settings.mesh_type in KRATOS_QUADRATIC_MESH_TYPES
    if errors := kratos_quadratic_platen_errors(settings):
        raise ValueError("\n".join(errors))
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    grid = mesh.grid
    if settings.self_contact_mode not in ('disabled','frictionless','frictional'):
        raise ValueError("Select disabled, frictionless or Coulomb self-contact.")
    if settings.self_contact_mode!='disabled' and settings.kratos_contact_method!='penalty':
        raise ValueError('Self-contact requires penalty mortar.')
    if settings.self_contact_mode!='disabled' and settings.mesh_type!='HEX8':
        raise ValueError('Kratos self-contact requires HEX8.')
    if settings.mesh_type not in SOLID_CELLS:
        raise ValueError(f"Unsupported Kratos element type: {settings.mesh_type}.")
    expected_type, count = SOLID_CELLS[settings.mesh_type]
    if not np.all(np.asarray(grid.celltypes) == expected_type):
        raise ValueError(f"Kratos export requires homogeneous {settings.mesh_type} connectivity.")
    cells = np.asarray(grid.cell_connectivity).reshape(-1, count)
    ids = np.asarray(grid.point_data.get("ansys_node_num", np.arange(1, grid.n_points + 1)), dtype=np.int64)
    if len(np.unique(ids)) != len(ids) or np.any(ids <= 0):
        raise ValueError("Specimen node IDs must be unique positive integers.")
    points = np.asarray(grid.points, dtype=float)
    fingerprint = mesh_digest(points, cells, ids)
    np.savez(directory / "mesh_input.npz", points=points, cells=cells, ids=ids)
    grid.save(directory/"submitted_mesh.vtu")
    zmin, zmax = points[:, 2].min(), points[:, 2].max()
    surfaces = specimen_surfaces(grid, ids, points, quadratic=quadratic)
    if not surfaces["BottomSpecimen"] or not surfaces["TopSpecimen"]:
        raise ValueError("The specimen must touch both platen planes.")
    next_id = int(ids.max()) + 1
    extra_points = []
    self_nodes = {}
    if settings.self_contact_mode!='disabled':
        # Independent facet nodes keep role/normal changes local to a facet.
        # Their displacement is tied to the unchanged solid with exact MPCs.
        coordinates={int(n):p for n,p in zip(ids,points)}
        copied=[]
        for face in surfaces['SelfSurface']:
            nodes=[]
            for original_id in face:
                self_nodes[next_id]=int(original_id)
                extra_points.append((next_id,tuple(coordinates[original_id])))
                nodes.append(next_id);next_id+=1
            copied.append(nodes)
        surfaces['SelfSurface']=copied
    else:
        # Noncontact skin conditions would contaminate the contact-node
        # normals at edges (e.g. a beam's side normal added to its bottom
        # normal). Only active interfaces belong in the contact model.
        surfaces['SelfSurface']=[]
    pad = settings.size * .12
    xmin, ymin = points[:, :2].min(axis=0) - pad
    xmax, ymax = points[:, :2].max(axis=0) + pad
    plate_nodes = {}
    spacing = max(float(getattr(mesh,"spacing",settings.mesh_resolution)),1e-9)
    nx=ny=1  # Rigid planar master patches need no deformation interpolation refinement.
    for name, z in (() if tension or experiment=='flexure' else (("BottomPlaten", zmin), ("TopPlaten", zmax))):
        patch=np.arange(next_id,next_id+(nx+1)*(ny+1)).reshape(ny+1,nx+1)
        plate_nodes[name]=patch.ravel().tolist()
        for j,y in enumerate(np.linspace(ymin,ymax,ny+1)):
            for i,x in enumerate(np.linspace(xmin,xmax,nx+1)):
                extra_points.append((int(patch[j,i]),(float(x),float(y),z)))
        faces=[]
        for j in range(ny):
            for i in range(nx):
                face=[int(patch[j,i]),int(patch[j,i+1]),int(patch[j+1,i+1]),int(patch[j+1,i])]
                if name == "TopPlaten":
                    face.reverse()
                if quadratic:
                    faces.extend((face[:3], [face[0], face[2], face[3]]))
                else:
                    faces.append(face)
        surfaces[name]=faces
        next_id+=(nx+1)*(ny+1)
    if tension:
        # Kinematic end grips cannot open under tensile loading.
        for side in ('Bottom','Top'):
            plate_nodes[side+'Platen']=sorted({n for face in surfaces[side+'Specimen'] for n in face})
    elif experiment=='flexure':
        from kratos_fixtures import line_fixture_nodes
        supports=line_fixture_nodes(mesh,profile(settings))
        plate_nodes['BottomPlaten']=supports['left']+supports['right']
        plate_nodes['TopPlaten']=supports['loading']
    pilot = next_id
    extra_points.append((pilot, ((xmin+xmax)/2, (ymin+ymax)/2, zmax)))
    interface_coefficients={'0':settings.bottom_platen_friction_coeff if settings.bottom_platen_contact=='frictional' else 0.,
                            '1':settings.top_platen_friction_coeff if settings.top_platen_contact=='frictional' else 0.,
                            '2':settings.self_friction_coeff if settings.self_contact_mode=='frictional' else 0.}
    lines = ["Begin ModelPartData", "End ModelPartData", "Begin Properties 1", "End Properties",
             "Begin Properties 2", f"YOUNG_MODULUS {settings.young_modulus:.17g}", "POISSON_RATIO 0.3", "End Properties"]
    for key,coefficient in interface_coefficients.items():
        lines += [f'Begin Properties {100+int(key)}',f'YOUNG_MODULUS {settings.young_modulus:.17g}',
                  f'POISSON_RATIO {settings.poisson_ratio:.17g}',f'FRICTION_COEFFICIENT {coefficient:.17g}','End Properties']
    lines += ['Begin Nodes']
    lines += [f"{int(n)} {x:.17g} {y:.17g} {z:.17g}" for n,(x,y,z) in zip(ids,points)]
    lines += [f"{n} {x:.17g} {y:.17g} {z:.17g}" for n,(x,y,z) in extra_points]
    lines += ["End Nodes", f"Begin Elements TotalLagrangianElement3D{count}N"]
    solver_cells = cells[:, HEX20_KRATOS_ORDER] if settings.mesh_type == "HEX20" else cells
    lines += [f"{i+1} 1 " + " ".join(map(str,ids[cell])) for i,cell in enumerate(solver_cells)]
    lines += ["End Elements"]
    condition_ids, condition_nodes = {}, {}
    cid = 1
    for name, faces in surfaces.items():
        condition_ids[name] = []
        condition_nodes[name] = sorted({node for face in faces for node in face})
        for face in faces:
            prop=100 if name.startswith('Bottom') else 101 if name.startswith('Top') else 102 if settings.self_contact_mode!='disabled' else 1
            lines += [f"Begin Conditions SurfaceCondition3D{len(face)}N", f"{cid} {prop} " + " ".join(map(str,face)), "End Conditions"]
            condition_ids[name].append(cid)
            cid += 1
    if settings.loading_mode == "force":
        lines += ["Begin Conditions PointLoadCondition3D1N", f"{cid} 2 {pilot}", "End Conditions"]
    groups = {"Specimen": (ids.tolist(), list(range(1,len(cells)+1)), []),
              **{name: (condition_nodes[name], [], condition_ids[name]) for name in surfaces},
              "Pilot": ([pilot], [], [cid] if settings.loading_mode == "force" else [])}
    if kinematic:
        for side in ('Bottom','Top'):groups[side+'Platen']=(plate_nodes[side+'Platen'],[],[])
    if settings.self_contact_mode!='disabled':
        all_points={int(n):p for n,p in zip(ids,points)}
        all_points.update(dict(extra_points))
        faces=surfaces['SelfSurface']
        roles,_,_=contact_partition([[all_points[n] for n in face] for face in faces],
            [[self_nodes[n] for n in face] for face in faces],np.full(len(faces),spacing))
        selected=[i for i,role in enumerate(roles) if role==1]
        groups['SelfSlave']=(sorted({n for i in selected for n in faces[i]}),[],[condition_ids['SelfSurface'][i] for i in selected])
    for name,(nodes,elements,conditions) in groups.items():
        lines += [f"Begin SubModelPart {name}", "Begin SubModelPartNodes", *map(str,nodes), "End SubModelPartNodes",
                  "Begin SubModelPartElements", *map(str,elements), "End SubModelPartElements",
                  "Begin SubModelPartConditions", *map(str,conditions), "End SubModelPartConditions", "End SubModelPart"]
    (directory/"model.mdpa").write_text("\n".join(lines)+"\n", encoding="utf-8")
    frictional = any(getattr(settings,side+'_platen_contact')=='frictional' and getattr(settings,side+'_platen_friction_coeff')>0 for side in ('bottom','top'))
    method = "ALMContact" if settings.kratos_contact_method == "alm" else "PenaltyContact"
    contact_type = "Frictional" if frictional else "Frictionless"
    contact_parts = {} if kinematic else {"0": ["BottomSpecimen", "BottomPlaten"], "1": ["TopSpecimen", "TopPlaten"]}
    # Kratos 10.4.4's assume_master_slave lists the SLAVE model parts.
    slaves = {} if kinematic else {"0": ["BottomSpecimen"], "1": ["TopSpecimen"]}
    coefficients = {"0": settings.bottom_platen_friction_coeff if settings.bottom_platen_contact == "frictional" else 0.,
                    "1": settings.top_platen_friction_coeff if settings.top_platen_contact == "frictional" else 0.}
    if kinematic:coefficients={}
    if settings.self_contact_mode != 'disabled':
        if not surfaces['SelfSurface']:raise ValueError('No specimen self-contact surface was found.')
        contact_parts['2']=['SelfSurface']
        slaves['2']=['SelfSlave']
        coefficients['2']=settings.self_friction_coeff if settings.self_contact_mode=='frictional' else 0.
        if coefficients['2']>0:
            contact_type='Frictional'
    # Kratos searches MASTER CONDITION CENTRES within factor * slave radius.
    # A single rigid patch spans the whole platen: a fixed factor loses edge
    # contact as the specimen mesh gets finer. Cover every slave face centre.
    point_by_id={int(n):p for n,p in zip(ids,points)}
    point_by_id.update(dict(extra_points))
    search_factor=3.5
    for name in ('BottomSpecimen','TopSpecimen'):
        if kinematic:continue
        platen = name.replace('Specimen', 'Platen')
        master_centers = [np.mean([point_by_id[n][:2] for n in face], axis=0) for face in surfaces[platen]]
        for face in surfaces[name]:
            coordinates=np.array([point_by_id[n] for n in face])
            center=coordinates.mean(axis=0)
            radius=np.linalg.norm(coordinates-center,axis=1).max()
            distance=max(np.linalg.norm(center[:2]-master_center) for master_center in master_centers)
            search_factor=max(search_factor,float(distance/max(radius,1e-12))+2.)
    penalty_factor = 500 if settings.kratos_contact_method == "alm" else settings.kratos_penalty_factor
    penalty = penalty_factor * settings.young_modulus / spacing
    if quadratic and experiment == "compression":
        # The linear mortar triangles on quadratic platen faces use a gap
        # weighted by surface area. Normalize by the nominal mesh face area
        # so refining the specimen does not progressively soften the interface.
        penalty /= spacing**2
    process = {"python_module": "alm_contact_process" if settings.kratos_contact_method == "alm" else "penalty_contact_process",
               "kratos_module": "KratosMultiphysics.ContactStructuralMechanicsApplication", "Parameters": {
                   "model_part_name": "Structure", "contact_model_part": contact_parts,
                   "assume_master_slave": slaves, "friction_coefficients": coefficients,
                   "contact_property_ids":{key:100+int(key) for key in contact_parts},
                   "advance_ALM_parameters": {"manual_ALM": True, "penalty": penalty, "scale_factor": settings.young_modulus},
                   "search_parameters": {"search_factor": search_factor, "consider_gap_threshold": True, "dynamic_search": True,
                                         **({'normal_orientation_threshold':1.5} if settings.self_contact_mode!='disabled' else {})},
                   "not_normal_update_frictional": True,
                   "contact_type": contact_type, "normal_variation": "no_derivatives_computation"}}
    contact_settings = {"mortar_type": method+contact_type, "fancy_convergence_criterion": False,
                        "use_mixed_ulm_solver": False, "frictional_decomposed": False,
                        "adaptative_strategy": settings.self_contact_mode=='disabled', "split_factor": 2.,
                        "max_number_splits": int(math.floor(math.log2(max(1, settings.substep_limits[1]/settings.substeps))))}
    parameters = {"problem_data": {"problem_name": "rustmech", "parallel_type": "OpenMP", "echo_level": 0, "start_time": 0., "end_time": 1.},
                  "solver_settings": {"solver_type": "Static", "model_part_name": "Structure", "domain_size": 3,
                     "echo_level": 0, "analysis_type": "non_linear", "model_import_settings": {"input_type": "mdpa", "input_filename": "model"},
                     "material_import_settings": {"materials_filename": "materials.json"},
                     "time_stepping": {"time_step": 1./settings.substeps}, "contact_settings": contact_settings,
                     "convergence_criterion": "contact_displacement_criterion" if settings.self_contact_mode!='disabled' else "contact_residual_criterion", "compute_reactions": True,
                     "max_iteration": settings.kratos_max_iterations, "move_mesh_flag": True,
                     "reform_dofs_at_each_step": settings.self_contact_mode!='disabled',
                     "linear_solver_settings": {"solver_type": settings.kratos_linear_solver},
                     "solving_strategy_settings": {"type": "line_search" if settings.kratos_line_search else "newton_raphson"},
                     **{f"{prefix}_{kind}_tolerance": getattr(settings, f"kratos_{kind}_tolerance") for prefix in ("displacement", "residual") for kind in ("relative", "absolute")}},
                  "processes": {"contact_process_list": [process]}, "output_processes": {}}
    # Use native boundary processes so adaptive substeps ramp the load too.
    parameters['processes']['constraints_process_list']=[{
        'python_module':'assign_vector_variable_process','kratos_module':'KratosMultiphysics',
        'Parameters':{'model_part_name':'Structure.TopPlaten' if settings.loading_mode=='displacement' else 'Structure.Pilot',
            'variable_name':'DISPLACEMENT' if settings.loading_mode=='displacement' else 'POINT_LOAD',
            'constrained':([False,False,True] if experiment=='flexure' else [True,True,True]) if settings.loading_mode=='displacement' else [False,False,False],
            'value':[None if experiment=='flexure' and settings.loading_mode=='displacement' else 0.,None if experiment=='flexure' and settings.loading_mode=='displacement' else 0.,f'{load_sign(settings)*(settings.compression if settings.loading_mode=="displacement" else settings.applied_force):.17g}*t'],
            'interval':[0.,'End']}}]
    payload = {"schema_version": 1, "settings": asdict(settings), "mesh_digest": fingerprint,
               "specimen_node_ids": ids.tolist(), "plate_nodes": plate_nodes, "pilot_node": pilot,
               "self_contact_nodes": self_nodes}
    if settings.mesh_type == "HEX20":
        payload["solver_cell_order"] = HEX20_KRATOS_ORDER
    payload.update(test_profile=profile_report(settings),experiment=experiment,load_sign=load_sign(settings))
    if experiment=='flexure':
        payload['support_nodes']=supports
        payload['test_profile']['fixture_model']='Idealized pin/roller support lines and a uniform-displacement loading line; no roller indentation, lift-off or contact migration.'
    if settings.loading_mode=='force' or self_nodes:
        parameters['solver_settings']['multi_point_constraints_used']=True
    if not contact_parts:
        parameters['solver_settings'].pop('contact_settings')
        parameters['solver_settings']['convergence_criterion']='residual_criterion'
        parameters['solver_settings']['multi_point_constraints_used']=settings.loading_mode=='force'
        parameters['processes']['contact_process_list']=[]
    if kinematic or quadratic:
        # Imposed motion can make the initial residual much larger than the
        # fixture reactions. Require tighter equilibrium in these cases.
        if contact_parts:parameters['solver_settings']['convergence_criterion']='contact_residual_criterion'
        if quadratic and not kinematic:
            parameters['solver_settings']['convergence_criterion'] = 'residual_criterion'
        for prefix in ('displacement','residual'):
            parameters['solver_settings'][prefix+'_relative_tolerance']=min(settings.kratos_relative_tolerance,1e-8)
            parameters['solver_settings'][prefix+'_absolute_tolerance']=min(settings.kratos_absolute_tolerance,1e-10)
        payload['effective_tolerances']={'relative':min(settings.kratos_relative_tolerance,1e-8),'absolute':min(settings.kratos_absolute_tolerance,1e-10)}
    payload['platen_specimen_nodes']=sorted(set(condition_nodes['BottomSpecimen']+condition_nodes['TopSpecimen']))
    if settings.self_contact_mode!='disabled':
        payload['self_contact_surface']={'faces':surfaces['SelfSurface'],'condition_ids':condition_ids['SelfSurface']}
    payload["mesh_metadata"]={name:getattr(mesh,name,None) for name in ("spacing","subdivisions","fraction","components","original_components","candidate_elements")}
    for filename,value in (("ProjectParameters.json",parameters), ("materials.json", {"properties": [material_properties(settings)]}), ("input.json",payload)):
        (directory/filename).write_text(json.dumps(value, indent=2, allow_nan=False)+"\n",encoding="utf-8")
    scripts = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "tools" / "truba"
    shutil.copyfile(scripts/"kratos_driver.py", directory/"kratos_driver.py")
    shutil.copyfile(scripts/"kratos_self_contact.py", directory/"kratos_self_contact.py")
    return payload
