"""SLURM-side entry point. No SSH or credentials and no desktop application.

The module itself uses only the standard library so a missing runtime can be
reported by the cluster's bootstrap Python without importing the application.
"""
from dataclasses import asdict,replace
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import traceback


def _write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    temporary.replace(path)


def _exception_detail(error):
    details = []
    seen = set()
    while error is not None and id(error) not in seen and len(details) < 4:
        seen.add(id(error))
        message = str(error)
        if message not in details:
            details.append(message)
        error = error.__cause__ or error.__context__
    return ' Caused by: '.join(details)


def _stage(message):
    print(message, flush=True)
    temporary = Path('progress.txt.tmp')
    temporary.write_text(message, encoding='utf-8')
    temporary.replace('progress.txt')


def probe(request,missing=False):
    operation = request.get('operation', 'probe')
    full = operation in ('probe', 'prepare')
    mechanics = operation in ('mechanical', 'convergence_level', 'doe_solve')
    tetra = request.get('settings', {}).get('mesh_type') in ('TET4', 'TET10') and operation != 'surface'
    capabilities = dict(mesh=False,netgen=False,kratos=False,flow=False,thermal=False,ansys_mapdl=False,
                        mesh_types=[],reasons={},hostname=socket.gethostname(),python=sys.executable,
                        foam_version=os.environ.get("WM_PROJECT_VERSION",""))
    capabilities["packages"] = {}
    if missing:
        capabilities["reasons"]["mesh"] = "Remote runtime is not installed. Use Prepare remote runtime."
        return capabilities
    try:
        for name in ("numpy","scipy","skimage","pyvista","mesh_engine"):
            importlib.import_module(name)
        from rust_mesh import _kernel
        _kernel()
        library = Path(os.environ["RUSTMECH_CORE_LIBRARY"])
        stamp = library.with_suffix(".json")
        if not stamp.exists() or json.loads(stamp.read_text()).get("native_sha256")!=request["native_sha256"]:
            raise RuntimeError("Remote native kernel is missing or outdated; prepare the runtime again.")
        capabilities["mesh"] = True
        capabilities["mesh_types"] = ["HEX8","HEX20"]
        for package in ("numpy","scipy","pyvista","vtk","netgen-mesher","KratosMultiphysics"):
            try: capabilities["packages"][package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError: pass
    except Exception as error:
        capabilities["reasons"]["mesh"] = f"Mesh runtime unavailable: {_exception_detail(error)}"
        return capabilities
    # Geometry and HEX meshing do not need Netgen, Kratos or OpenFOAM.
    if not full and not tetra and not mechanics and not operation.startswith(('flow_', 'thermal_')):
        return capabilities
    try:
        if full or tetra:
            importlib.import_module("netgen.meshing")
        if request.get("operation") in ("probe","prepare"):
            checked=subprocess.run([sys.executable,str(Path(__file__).resolve()),"--netgen-smoke"],
                                   capture_output=True,timeout=90)
            if checked.returncode:
                raise RuntimeError("Reference TPMS tetrahedral mesh self-test failed; TET4/TET10 are disabled. "+checked.stderr.decode(errors="replace")[-700:])
        if full or tetra:
            capabilities["netgen"] = True
            capabilities["mesh_types"] += ["TET4","TET10"]
    except Exception as error: capabilities["reasons"]["netgen"] = str(error)
    if not full and not mechanics:
        if not operation.startswith(('flow_', 'thermal_')):
            return capabilities
    try:
        for name in (("KratosMultiphysics","KratosMultiphysics.StructuralMechanicsApplication",
                     "KratosMultiphysics.ContactStructuralMechanicsApplication",
                     "KratosMultiphysics.ConstitutiveLawsApplication","KratosMultiphysics.LinearSolversApplication",
                     "nonlinear_compression") if full or mechanics else ()):
            importlib.import_module(name)
        capabilities["kratos"] = full or mechanics
    except Exception as error: capabilities["reasons"]["kratos"] = f"Kratos/reporting runtime unavailable: {error}"
    capabilities["reasons"]["ansys_mapdl"] = "A remote MAPDL license/runtime integration is not qualified. Use supported Kratos mechanics in TRUBA mode."
    if not full and not operation.startswith(('flow_', 'thermal_')):
        return capabilities
    compatible = bool(re.fullmatch(r"v?\d{4}(?:[-+].*)?",capabilities["foam_version"]))
    for key,commands in (("flow",("simpleFoam","checkMesh")),("thermal",("chtMultiRegionFoam","splitMeshRegions","checkMesh"))):
        if not compatible:
            capabilities["reasons"][key] = "Load a compatible OpenCFD OpenFOAM module (e.g. v2312); Foundation solver modules are not supported."
            continue
        try:
            for command in commands:
                subprocess.run([command,"-help"],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
            capabilities[key] = True
        except (OSError,subprocess.SubprocessError) as error: capabilities["reasons"][key] = f"OpenFOAM commands unavailable: {error}"
    return capabilities


def save_preview(preview,settings,directory):
    import numpy as np
    directory.mkdir(parents=True,exist_ok=True)
    preview.surface.save(directory/"surface.vtp")
    preview.mesh.grid.save(directory/"mesh.vtu")
    preview.mesh.removed_grid.save(directory/"removed.vtu")
    np.savez(directory/"contacts.npz",bottom=preview.mesh.bottom_ids,top=preview.mesh.top_ids)
    geometry_sha256 = (hashlib.sha256(Path(settings.imported_geometry_path).read_bytes()).hexdigest()
                       if settings.imported_geometry_path else "")
    _write(directory/"mesh.json",dict(settings=asdict(settings),remote_source=str(directory.resolve()),
          geometry_sha256=geometry_sha256,
          mesh={key:getattr(preview.mesh,key) for key in ("spacing","subdivisions","fraction","components","original_components","candidate_elements")}))


def _local_kratos():
    from kratos_backend import KratosResults
    from kratos_export import export_kratos
    from solver_api import KRATOS_CAPABILITIES,SolverExecution
    class ComputeNodeKratos:
        capabilities = KRATOS_CAPABILITIES
        def solve(self,request):
            raw = request.run_directory
            export_kratos(request.mesh,request.settings,raw)
            with (raw/"kratos.log").open("w") as stream:
                status = subprocess.run([sys.executable,"kratos_driver.py"],cwd=raw,stdout=stream,stderr=subprocess.STDOUT)
            if status.returncode:
                return SolverExecution(error="Compute-node Kratos failed; see kratos.log.",solve_attempted=True)
            reader = KratosResults(raw/"results",request.mesh,request.settings)
            if str(reader.manifest.get("slurm_job_id"))!=os.environ.get("SLURM_JOB_ID"):
                raise RuntimeError("Kratos results have an invalid SLURM provenance.")
            if request.settings.self_contact_mode!="disabled":
                import numpy as np
                coefficient = request.settings.self_friction_coeff if request.settings.self_contact_mode=="frictional" else 0.
                if not np.isclose(reader.manifest.get("self_contact_coefficient",np.nan),coefficient,rtol=0,atol=1e-12):
                    raise RuntimeError("Kratos did not verify the requested self-contact friction coefficient.")
                if any(step.get("self_contact",{}).get("coulomb_limit_ratio",0.)>1.01 for step in reader.steps):
                    raise RuntimeError("Self-contact traction exceeds the requested Coulomb limit.")
            return SolverExecution(results=reader,solve_attempted=True,
                error=None if reader.manifest.get("complete") else reader.manifest.get("error","Kratos did not finish the load history."),
                diagnostics=dict(local_directory=str(raw.resolve()),execution="TRUBA compute node",mesh_import_verified=True,
                                 slurm_job_id=os.environ.get("SLURM_JOB_ID")))
    return ComputeNodeKratos()


def _batch(settings,mesh):
    import nonlinear_compression as reporting
    folder = reporting.run_analysis(settings,mesh,_local_kratos(),images=False)
    value = json.loads((folder/"summary.json").read_text())
    value["run_folder"] = str(folder)
    return value


def run_operation(request,output):
    from remote_operations import required_capability
    required_capability(request)
    if request['operation'] in ('probe', 'prepare'):
        return _run_operation(request, output)
    from mesh_engine import single_surface_mode
    # Cover indirect surface generation too: previews, meshes, DoE, convergence
    # and flow/thermal exports all share this execution scope.
    with single_surface_mode():
        return _run_operation(request, output)


def _run_operation(request,output):
    operation = request["operation"]
    from remote_operations import required_capability
    needed = required_capability(request)
    if operation in ("probe","prepare"): return dict(capabilities=probe(request))
    _stage('Checking runtime for ' + operation)
    capabilities = probe(request)
    if not capabilities.get(needed):
        raise RuntimeError(capabilities["reasons"].get(needed,capabilities["reasons"].get("mesh",f"{needed} is unavailable")))
    from model import settings_from_dict
    from mesh_engine import build_preview,generate_volume_mesh,make_surface,preview_key
    settings_data = dict(request['settings'])
    if settings_data.get('imported_geometry_path'):
        import base64
        geometry = output / 'imported_geometry.vtp'
        geometry.write_bytes(base64.b64decode(request['imported_geometry_vtp'], validate=True))
        settings_data['imported_geometry_path'] = str(geometry.resolve())
    settings = settings_from_dict(settings_data)
    if request.get("remote_context"):
        allocation=request["remote_context"]
        if settings.cores>allocation["cores"] and needed=="kratos":
            raise ValueError("Requested mechanical threads exceed the SLURM CPU allocation.")
        settings=replace(settings,truba_partition=allocation["partition"],truba_account=allocation["account"],
                         truba_walltime=allocation["walltime"],truba_memory_mb=allocation["memory_mb"])
    if needed=="kratos" and settings.solver_backend!="kratos":
        raise RuntimeError("Only the qualified Kratos mechanical adapter is supported in TRUBA computation mode.")
    if needed=="kratos":
        from solver_api import KRATOS_CAPABILITIES
        errors = KRATOS_CAPABILITIES.validate(settings)
        if errors:
            raise ValueError("\n".join(errors))
    if settings.mesh_type not in capabilities["mesh_types"] and operation not in ("surface","flow_export","flow_solve","thermal_export","thermal_solve"):
        raise RuntimeError(f"Remote mesher does not support {settings.mesh_type} in this environment.")
    if operation=="preview":
        preview = build_preview(settings,on_progress=lambda stage:print(stage,flush=True),include_mesh=True)
        save_preview(preview,settings,output/"preview")
        return dict(preview="preview")
    if operation=="surface":
        _stage('Generating specimen surface')
        make_surface(settings, on_progress=_stage).save(output/"surface.vtp")
        return dict(surface="surface.vtp")
    if operation.startswith(("flow_","thermal_")):
        from flow_analysis import FlowSettings,export_flow_case,export_geometry_inspection,summarize_flow_case
        from thermal_analysis import ThermalSettings,export_thermal_case,summarize_thermal_case
        flow = FlowSettings(**request["flow"])
        case = Path.cwd()/"solver_case"
        if operation.startswith("thermal_"):
            thermal = ThermalSettings(**request["thermal"])
            export_thermal_case(settings,flow,thermal,case)
        elif operation == "flow_export" and request.get("inspection"):
            export_geometry_inspection(settings,flow,case)
        else: export_flow_case(settings,flow,case)
        if operation.endswith("solve"):
            result = subprocess.run(["bash","Allrun"],cwd=case)
            if result.returncode:
                for path in case.glob("log.*"): shutil.copy2(path,output/path.name)
                raise RuntimeError(f"{operation} failed; inspect the solver logs.")
            if operation.startswith("thermal_"): summarize_thermal_case(case)
            else: summarize_flow_case(case)
        # Numerical cases and raw OpenFOAM time directories stay on TRUBA.
        for pattern in ("*_case.json","*_summary.json","*.vtu","*.vtp","*.csv","log.*"):
            for path in case.glob(pattern): shutil.copy2(path,output/path.name)
        if (case/"frames").is_dir(): shutil.copytree(case/"frames",output/"frames")
        return dict(case=".",remote_solver_case=str(case.resolve()))
    import nonlinear_compression as reporting
    reporting.ROOT = output.resolve()
    if operation=="mechanical":
        mesh_source = request.get("mesh_source","")
        if mesh_source:
            # Do not allow a request to read another account or arbitrary host files.
            from truba_jobs import validated_remote_path
            expected = Path(mesh_source)
            if expected.parts[-2:]!=("output","preview"): raise ValueError("Invalid preview source.")
            username = os.environ.get("USER","")
            validated_remote_path(str(expected.parent.parent),username)
            from truba_remote import load_remote_preview
            preview = load_remote_preview(expected,settings=settings)
            mesh = preview.mesh
        else: mesh = generate_volume_mesh(settings)
        summary = _batch(settings,mesh)
        return dict(run_folder=summary["run_folder"],summary=summary)
    if operation=="convergence_level":
        from mesh_convergence import solve_level
        level = solve_level(settings,generate_volume_mesh,_batch,max_elements=request["max_elements"],selected=request["selected"])
        return dict(level=asdict(level))
    if operation in ("doe_mesh","doe_solve"):
        _stage('Meshing specimen' if operation == 'doe_mesh' else 'Meshing and solving DoE specimen')
        from doe_studio import ExperimentWorker
        worker = ExperimentWorker(generate_volume_mesh,_batch)
        method = worker._solve_row if operation=="doe_solve" else worker._evaluate_row_mesh
        return dict(row=method(request["row"],settings))
    raise ValueError("Unsupported remote operation.")


def main():
    if "--netgen-smoke" in sys.argv:
        from mesh_engine import build_preview
        from model import Settings
        preview=build_preview(Settings(size=1,cell_size=1,solid_fraction=40,mesh_type="TET4",
                                       mesh_resolution=.3,local_mesh_size=.15,tetra_strategy="netgen"),include_mesh=True)
        if preview.mesh.grid.n_cells<=0 or preview.mesh.solve_errors:
            raise RuntimeError("Reference tetrahedral mesh is not solve-ready.")
        return 0
    request = json.loads(Path(sys.argv[1]).read_text())
    output = Path.cwd()/"output"
    output.mkdir(exist_ok=True)
    result = dict(schema_version=1,request_id=request["request_id"],source_sha256=request["source_sha256"],
                  slurm_job_id=os.environ.get("SLURM_JOB_ID"),operation=request["operation"],
                  remote_output=str(output.resolve()),hostname=socket.gethostname())
    status = 0
    try:
        if "--missing-runtime" in sys.argv:
            if request["operation"]!="probe": raise RuntimeError("Remote runtime is missing. Prepare it before submitting computations.")
            result["capabilities"] = probe(request,missing=True)
        else: result.update(run_operation(request,output))
    except Exception as error:
        result["error"] = str(error)
        (output/"error.txt").write_text(traceback.format_exc())
        traceback.print_exc()
        status = 1
    _write(output/"result.json",result)
    return status


if __name__=="__main__": raise SystemExit(main())
