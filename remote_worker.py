"""SLURM-side entry point. No SSH or credentials and no desktop application.

The module itself uses only the standard library so a missing runtime can be
reported by the cluster's bootstrap Python without importing the application.
"""
from contextlib import ExitStack
from dataclasses import asdict,replace
from functools import partial
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
    capabilities = dict(mesh=False,netgen=False,kratos=False,damask=False,peridynamics=False,flow=False,thermal=False,ansys_mapdl=False,
                        mesh_types=[],reasons={},hostname=socket.gethostname(),python=sys.executable,
                        foam_version=os.environ.get("WM_PROJECT_VERSION",""))
    capabilities["packages"] = {}
    capabilities["mpm"] = False
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
        from model import DERIVED_SOLID_MESH_TYPES
        capabilities["mesh_types"] = ["HEX8", "HEX20", *DERIVED_SOLID_MESH_TYPES]
        for package in ("numpy","scipy","pyvista","vtk","netgen-mesher","KratosMultiphysics"):
            try: capabilities["packages"][package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError: pass
    except Exception as error:
        capabilities["reasons"]["mesh"] = f"Mesh runtime unavailable: {_exception_detail(error)}"
        return capabilities
    if full or operation == 'mpm_solve':
        try:
            from local_kratos import MPM_PACKAGE, VERSION
            if importlib.metadata.version(MPM_PACKAGE) != VERSION:
                raise RuntimeError(f'{MPM_PACKAGE} {VERSION} is required.')
            importlib.import_module('KratosMultiphysics.MPMApplication')
            capabilities['packages'][MPM_PACKAGE] = VERSION
            capabilities['mpm'] = True
        except Exception as error:
            capabilities['reasons']['mpm'] = f'Kratos MPM unavailable: {error}. Use Prepare remote runtime.'
        if not full:
            return capabilities
    if full or operation == 'peridynamic_solve':
        try:
            importlib.import_module('peridynamic_analysis')
            capabilities['peridynamics'] = True
        except ImportError as error:
            capabilities['reasons']['peridynamics'] = f"Peridynamic solver unavailable: {error}."
        if not full:
            return capabilities
    if full or operation == 'fft_solve':
        try:
            from damask_fft import damask_grid_executable
            importlib.import_module('h5py')
            subprocess.run([damask_grid_executable(), '-h'], check=True, timeout=30,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            capabilities['damask'] = True
        except Exception as error:
            capabilities['reasons']['damask'] = f"DAMASK unavailable: {error}. Use Prepare remote runtime."
        if not full:
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
                     "mechanical_analysis") if full or mechanics else ()):
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


def _batch(settings, mesh, *, result_root):
    from mechanical_analysis import run_batch_experiment

    return run_batch_experiment(settings, mesh, _local_kratos, result_root=result_root)


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
    from remote_operations import BATCH_INPUT_DIRECTORY, batch_geometry_name, required_capability
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
        staged = request.get('staged_geometry')
        if staged is None:
            geometry.write_bytes(base64.b64decode(request['imported_geometry_vtp'], validate=True))
        else:
            inputs = (Path(os.environ['RUSTMECH_BATCH_INPUTS']) if 'directory' in staged
                      else output.resolve().parent.parent / BATCH_INPUT_DIRECTORY)
            source = inputs / batch_geometry_name(staged['sha256'])
            if not source.is_file():
                raise FileNotFoundError('Prepared geometry is missing on TRUBA. Remove the job from the queue and send the specimen again.')
            shutil.copyfile(source, geometry)
            with geometry.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != staged['sha256']:
                    raise ValueError('Prepared geometry checksum does not match on TRUBA.')
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
    if operation == 'mpm_solve':
        from mpm_analysis import CURVE_NAME, PARTICLES_NAME, SUMMARY_NAME, MPMSettings, execute_mpm

        case = Path.cwd() / 'solver_case'
        try:
            execute_mpm(settings, MPMSettings(**request['mpm']), case, remote=True, on_progress=_stage)
        finally:
            log = case / 'mpm_solver.log'
            if log.is_file():
                shutil.copy2(log, output / log.name)
        for name in (CURVE_NAME, PARTICLES_NAME, SUMMARY_NAME):
            shutil.copy2(case / name, output / name)
        return dict(case='.', remote_solver_case=str(case.resolve()))
    if operation == 'fft_solve':
        from damask_fft import run_periodic_fft
        case = Path.cwd() / 'solver_case'
        case.mkdir()
        run_periodic_fft(settings, request['grid'], request['void_ratio'], None, case, on_progress=_stage)
        folder = output / 'fft'
        folder.mkdir()
        for pattern in ('*.csv', '*.json', '*.yaml', '*.log', '*.sta'):
            for path in case.glob(pattern):
                shutil.copy2(path, folder / path.name)
        return dict(run_folder=str(folder.resolve()), remote_solver_case=str(case.resolve()))
    if operation == 'peridynamic_solve':
        from peridynamic_analysis import CompressionOptions, run_compression
        folder = run_compression(settings, CompressionOptions(**request['options']), output / 'peridynamics',
                                 on_progress=lambda message, *_: _stage(message))
        return dict(run_folder=folder)
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
    batch = partial(_batch, result_root=output.resolve())
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
        summary = batch(settings, mesh)
        return dict(run_folder=summary["run_folder"],summary=summary)
    if operation=="convergence_level":
        from convergence_execution import solve_level
        _stage('Meshing specimen')
        level = solve_level(settings, generate_volume_mesh, batch,
                            publish=lambda level: _stage(f'Solving · {level.elements:,} elements'),
                            max_elements=request["max_elements"], selected=request["selected"])
        return dict(level=asdict(level))
    if operation in ("doe_mesh","doe_solve"):
        _stage('Meshing specimen' if operation == 'doe_mesh' else 'Meshing and solving DoE specimen')
        from doe_execution import ExperimentWorker
        worker = ExperimentWorker(generate_volume_mesh,batch)
        method = worker._solve_row if operation=="doe_solve" else worker._evaluate_row_mesh
        return dict(row=method(request["row"],settings))
    raise ValueError("Unsupported remote operation.")


def _run_experiment_matrix(request, output):
    from remote_operations import required_capability

    rows = request["experiment_rows"]
    if not 1 <= len(rows) <= 10000:
        raise ValueError("Invalid experiment count.")
    root = Path.cwd()
    worker = str(Path(__file__).resolve())
    job_id = os.environ.get("SLURM_JOB_ID", "")
    scontrol = shutil.which("scontrol") if job_id.isdecimal() and os.environ.get("SLURMD_NODENAME") else None
    failures = 0
    for index, row in enumerate(rows, 1):
        if (root / "cancel.request").exists():
            raise RuntimeError("Experiment matrix cancelled.")
        name = f"row_{index:05d}"
        case = (output / name).resolve()
        case.mkdir()
        work = root / "matrix_work" / name
        work.mkdir(parents=True)
        record = dict(experiment=index, execution_target="truba", inputs=row, state="Running")
        _write(case / "experiment.json", record)
        _stage(f"Running experiment {index} / {len(rows)}")
        if scontrol:
            try:
                update = subprocess.run([scontrol, "update", f"JobId={job_id}", f"Comment=DoE {index}/{len(rows)}"],
                                        capture_output=True, timeout=5)
                if update.returncode:
                    scontrol = None
            except (OSError, subprocess.TimeoutExpired):
                scontrol = None
        try:
            if row["operation"] != request["operation"]:
                raise ValueError("Inconsistent matrix operation.")
            required_capability(row)
            child = dict(row, request_id=request["request_id"], source_sha256=request["source_sha256"],
                         native_sha256=request["native_sha256"], remote_context=request["remote_context"])
            path = row["settings"].get("imported_geometry_path")
            if path:
                child["imported_geometry_vtp"] = request["matrix_geometries"][path]
            _write(work / "request.json", child)
            with (case / "worker.log").open("w") as log:
                result = subprocess.run([sys.executable, worker, "request.json", "--row-output", str(case)],
                                        cwd=work, stdout=log, stderr=subprocess.STDOUT)
            if result.returncode:
                report = case / "result.json"
                detail = json.loads(report.read_text(encoding="utf-8")).get("error") if report.is_file() else None
                raise RuntimeError(detail or f"Worker exited with code {result.returncode}.")
            record.update(state="Completed", results=".")
        except Exception as error:
            failures += 1
            record.update(state="Failed", error=str(error))
        _write(case / "experiment.json", record)
    return dict(experiments=len(rows), failed=failures)


def _run_convergence_group(request, output):
    steps = request['convergence_steps']
    allocation = request['remote_context']
    if (request['operation'] != 'convergence_level' or not 2 <= len(steps) <= 100
            or sum(step['settings']['cores'] for step in steps) > allocation['cores']
            or sum(step['memory_mb'] for step in steps) > allocation['memory_mb']
            or any(step['settings']['cores'] < 1 or step['memory_mb'] < 1 for step in steps)):
        raise ValueError('Invalid resources for the convergence group.')
    common = {key: value for key, value in request.items() if key != 'convergence_steps'}
    _stage(f'Running {len(steps)} mesh levels in parallel')
    pending = []
    with ExitStack() as stack:
        for index, step in enumerate(steps, 1):
            directory = output / f'level-{index}'
            cores, memory = step['settings']['cores'], step['memory_mb']
            child = dict(common, settings=step['settings'], convergence_step=True,
                         remote_context=dict(allocation, cores=cores, memory_mb=memory))
            _write(directory / 'request.json', child)
            env = dict(os.environ, OMP_NUM_THREADS=str(cores), RUSTMECH_THREADS=str(cores))
            env.pop('SLURM_MEM_PER_CPU', None)
            env.pop('SLURM_MEM_PER_GPU', None)
            args = ['srun', '--exact', '--nodes=1', '--ntasks=1', '--cpu-bind=cores',
                    f'--cpus-per-task={cores}', f'--mem={memory}M', f'--job-name=mesh-{index}',
                    sys.executable, str(Path(__file__).resolve()), 'request.json']
            with (directory / 'step.out').open('w') as stdout, (directory / 'step.err').open('w') as stderr:
                process = stack.enter_context(subprocess.Popen(args, cwd=directory, env=env,
                                                               stdout=stdout, stderr=stderr))
            pending.append((process, directory))

        results = []
        for process, directory in pending:
            code = process.wait()
            manifest = directory / 'output' / 'result.json'
            try:
                result = json.loads(manifest.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                result = {}
            if code == 0 and 'level' in result:
                results.append({'level': result['level']})
                continue
            with (directory / 'step.err').open(encoding='utf-8', errors='replace') as stderr:
                oom = any(re.search(r'\boom[-_]kill(?:ed)?\b|\bout of memory\b', line, re.I) for line in stderr)
            results.append(dict(error=result.get('error') or f'TRUBA mesh step failed ({code}). See {directory / "step.err"}.',
                                out_of_memory=oom))
    return {'levels': results}


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
    if len(sys.argv) == 4 and sys.argv[2] == "--row-output":
        output = Path(sys.argv[3])
    elif 'directory' in request.get('staged_geometry', {}):
        output = Path(os.environ['RUSTMECH_BATCH_OUTPUT'])
    else:
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
        elif 'experiment_rows' in request:
            result.update(_run_experiment_matrix(request, output))
        elif request.get('convergence_steps'):
            result.update(_run_convergence_group(request, output))
        else: result.update(run_operation(request,output))
    except Exception as error:
        result["error"] = str(error)
        (output/"error.txt").write_text(traceback.format_exc())
        traceback.print_exc()
        status = 1
    _write(output/"result.json",result)
    if request.get('convergence_step'):
        _stage(result.get('level', {}).get('status', 'Failed' if status else 'Finished'))
    return status


if __name__=="__main__": raise SystemExit(main())
