"""Exact-mesh Kratos execution through TRUBA and lazy result access."""
import json
from pathlib import Path
import re
import numpy as np
from kratos_export import export_kratos, mesh_digest
from solver_api import KRATOS_CAPABILITIES, SolverExecution, map_node_values
from truba_jobs import HOST, run_remote, write_record


class KratosResults:
    def __init__(self,directory,mesh,settings=None):
        self.directory=Path(directory)
        from model import Settings, settings_from_dict
        if settings is None:
            source=self.directory.parent/'input.json'
            settings=settings_from_dict(json.loads(source.read_text(encoding='utf-8'))['settings']) if source.is_file() else Settings()
        self.settings=settings
        self.manifest=json.loads((self.directory/"manifest.json").read_text(encoding="utf-8"))
        from standard_tests import kind, load_sign
        if kind(settings)!='compression' and (self.manifest.get('experiment')!=kind(settings)
                or self.manifest.get('test_profile')!=settings.test_profile
                or self.manifest.get('load_sign')!=load_sign(settings)):
            raise ValueError('Kratos result test profile or loading direction does not match the request.')
        grid=mesh.grid
        ids=np.asarray(grid.point_data["ansys_node_num"])
        expected=mesh_digest(grid.points,grid.cell_connectivity,ids)
        if (self.manifest.get("schema_version") != 1 or not self.manifest.get("mesh_verified")
                or self.manifest.get("mesh_digest") != expected):
            raise ValueError("Kratos result mesh does not match the submitted RustMech mesh")
        self.steps=self.manifest["steps"]
        if not self.steps:
            raise ValueError(self.manifest.get("error","Kratos returned no converged solution steps"))
        times=np.array([step["time"] for step in self.steps],dtype=float)
        if not np.isfinite(times).all() or np.any(np.diff(times)<=0) or np.any(times<=0) or times[-1]>1+1e-8:
            raise ValueError("Invalid Kratos load history")
        if self.manifest.get("complete") and not np.isclose(times[-1],1.,rtol=0,atol=1e-8):
            raise ValueError("Kratos reported completion before the requested final load")
        for step in self.steps:
            if not re.fullmatch(r"step_\d{6}\.npz",step["file"]):
                raise ValueError("Invalid Kratos result filename")
            if not all(np.isfinite(step[key]) for key in ("top_displacement","bottom_reaction","top_reaction")):
                raise ValueError("Invalid Kratos platen result")
        self._cache=None
        self._cache_index=None
        self.expected_ids=np.sort(ids)

    @property
    def nsets(self):return len(self.steps)
    @property
    def time_values(self):return np.array([step["time"] for step in self.steps])
    @property
    def stress_definition(self):return self.manifest["stress_definition"]
    @property
    def force_source(self):return "Kratos moving-fixture reaction (or prescribed pilot force), checked against fixed-fixture reactions"

    def _read(self,index):
        if self._cache_index != index:
            with np.load(self.directory/self.steps[index]["file"],allow_pickle=False) as data:
                values={name:data[name].copy() for name in ("ids","displacement","equivalent_stress")}
                for name in ('contact_pressure','contact_tangential_pressure'):
                    if name in data:values[name]=data[name].copy()
            ids=values["ids"]
            if not np.array_equal(np.sort(ids),self.expected_ids) or len(np.unique(ids))!=len(ids):
                raise ValueError("Missing, duplicate or unexpected Kratos result node IDs")
            if values["displacement"].shape!=(len(ids),3) or values["equivalent_stress"].shape!=(len(ids),):
                raise ValueError("Invalid Kratos result array dimensions")
            if not all(np.isfinite(value).all() for value in values.values()) or np.any(values["equivalent_stress"] < 0):
                raise ValueError("Non-finite or negative Kratos field result")
            for name in ('contact_pressure','contact_tangential_pressure'):
                if name in values and (values[name].shape!=(len(ids),) or np.any(values[name]<0)):
                    raise ValueError('Invalid Kratos self-contact pressure field')
            self._cache=values
            self._cache_index=index
        return self._cache

    def displacement(self,index,target_ids):
        data=self._read(index)
        return map_node_values(data["ids"],data["displacement"],target_ids)

    def equivalent_stress(self,index,target_ids):
        data=self._read(index)
        return map_node_values(data["ids"],data["equivalent_stress"],target_ids)

    @property
    def has_self_contact(self):return any('self_contact' in step for step in self.steps)

    def contact_pressure(self,index,target_ids,tangential=False):
        data=self._read(index)
        name='contact_tangential_pressure' if tangential else 'contact_pressure'
        if name not in data:raise ValueError('This result has no saved self-contact pressure field.')
        return map_node_values(data['ids'],data[name],target_ids)

    def top_platen_displacement(self,index):return self.steps[index]["top_displacement"]
    def platen_reactions(self,index):
        step=self.steps[index]
        return step["bottom_reaction"],step["top_reaction"]


class KratosBackend:
    def __init__(self,auth=None,marker=None,capabilities=KRATOS_CAPABILITIES):
        self.auth=auth
        self.marker=marker
        self.capabilities=capabilities

    def solve(self,request):
        errors=self.capabilities.validate(request.settings)+request.mesh.solve_errors
        if errors:return SolverExecution(error="\n".join(errors))
        export_kratos(request.mesh,request.settings,request.run_directory)
        return self._execute(request,resume=False)

    def resume(self,request):return self._execute(request,resume=True)

    def _execute(self,request,resume):
        execution=SolverExecution(diagnostics={"solver":"kratos","mesh_source":"provided_fe_mesh","specimen_remeshed":False})
        try:
            record=None
            if resume:
                # Fully downloaded terminal jobs can be opened while offline.
                # Incomplete downloads/accounting still reconnect for recovery.
                try:
                    candidate=json.loads((request.run_directory/'remote_job.json').read_text())
                    manifest=json.loads((request.run_directory/'results/manifest.json').read_text())
                    status=(request.run_directory/'results/slurm_status.txt').read_text().strip().rstrip('|')
                    if (candidate.get('state')=='downloaded' and candidate.get('host')==HOST
                            and str(candidate.get('job_id','')).isdigit() and manifest.get('complete')
                            and status=='COMPLETED|0:0'):
                        record=candidate
                        execution.diagnostics['result_source']='downloaded_local_cache'
                        if self.marker:write_record(self.marker,record)
                except (OSError,ValueError):pass
            if record is None:
                record=run_remote(request.settings,request.run_directory,self.auth,self.marker,resume=resume)
            execution.solve_attempted=bool(record.get("job_id"))
            execution.diagnostics.update(record)
            reader=KratosResults(request.run_directory/"results",request.mesh,request.settings)
            execution.diagnostics.update(experiment=reader.manifest.get('experiment','compression'),
                fixture_model=reader.manifest.get('fixture_model','Legacy compression platens'),
                effective_tolerances=reader.manifest.get('effective_tolerances',{}))
            if str(reader.manifest.get("slurm_job_id")) != str(record.get("job_id")):
                raise ValueError("Downloaded results do not belong to the submitted SLURM job")
            if request.settings.self_contact_mode!='disabled':
                coefficient=request.settings.self_friction_coeff if request.settings.self_contact_mode=='frictional' else 0.
                if not np.isclose(reader.manifest.get('self_contact_coefficient',np.nan),coefficient,rtol=0,atol=1e-12):
                    raise ValueError('The downloaded self-contact result does not verify the requested friction coefficient. Run the analysis again with this adapter.')
                if any(step.get('self_contact',{}).get('coulomb_limit_ratio',0.)>1.01 for step in reader.steps):
                    raise ValueError('The self-contact traction exceeds the requested Coulomb limit.')
            execution.results=reader
            execution.diagnostics["mesh_import_verified"]=True
            slurm_status=(request.run_directory/"results/slurm_status.txt").read_text().strip()
            execution.diagnostics["slurm_status"]=slurm_status
            if slurm_status.rstrip('|') != "COMPLETED|0:0":
                execution.error=f"SLURM did not confirm successful completion: {slurm_status or 'accounting not available; retrieve this job again'}"
            if not reader.manifest.get("complete"):
                execution.error=reader.manifest.get("error","Kratos stopped before the requested final load")
        except Exception as error:
            record_path=request.run_directory/"remote_job.json"
            if record_path.exists():
                try:
                    saved=json.loads(record_path.read_text())
                    execution.diagnostics.update(saved)
                    execution.solve_attempted=bool(saved.get("job_id"))
                except (OSError,ValueError):pass
            execution.error=f"{error}\nJob recovery record: {request.run_directory / 'remote_job.json'}"
        return execution


def create_kratos_backend(auth=None,marker=None):return KratosBackend(auth,marker)


def write_kratos_mdpa_skeleton(mesh,settings,run_directory):
    return export_kratos(mesh,settings,run_directory)
