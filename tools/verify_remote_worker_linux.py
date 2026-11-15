"""Exercise actual mesh/physics workers in Linux, independently of SSH/SLURM.

This is explicitly not a live TRUBA test. The transport/scheduler protocol has
separate deterministic tests, and production still requires a working account.
"""
from dataclasses import asdict,replace
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from model import Settings
from flow_analysis import FlowSettings
from thermal_analysis import ThermalSettings
from remote_worker import probe


def main():
    directory=ROOT/"build"/("remote-worker-linux-"+uuid.uuid4().hex[:8])
    directory.mkdir(parents=True)
    stamp=json.loads(Path(os.environ["RUSTMECH_CORE_LIBRARY"]).with_suffix(".json").read_text())
    base=dict(schema_version=1,request_id="",source_sha256="linux-verification",native_sha256=stamp["native_sha256"])
    capabilities=probe({**base,"operation":"probe"})
    report=dict(live_truba_test=False,capabilities=capabilities,cases={},passed=False)
    mechanical=Settings(size=1,cell_size=.5,solid_fraction=70,mesh_resolution=.25,mesh_type="HEX8",
                        solver_backend="kratos",material_model="linear_elastic",young_modulus=1000,
                        compression=.002,substeps=2,minimum_substeps=2,maximum_substeps=200,
                        self_contact_mode="disabled",bottom_platen_contact="frictionless",top_platen_contact="frictionless",cores=2)
    geometry=Settings()
    cases=[("preview","mesh",dict(settings=asdict(mechanical))),
           ("tet4_preview","netgen",dict(_operation="preview",settings=asdict(replace(mechanical,cell_size=1,solid_fraction=40,mesh_type="TET4",mesh_resolution=.3,local_mesh_size=.15,tetra_strategy="netgen")))),
           ("tet10_preview","netgen",dict(_operation="preview",settings=asdict(replace(mechanical,cell_size=1,solid_fraction=40,mesh_type="TET10",mesh_resolution=.3,local_mesh_size=.15,tetra_strategy="netgen")))),
           ("doe_mesh","mesh",dict(settings=asdict(mechanical),row=0)),
           ("mechanical","kratos",dict(settings=asdict(mechanical))),
           ("convergence_level","kratos",dict(settings=asdict(mechanical),max_elements=10000,selected=[0,1,2])),
           ("flow_solve","flow",dict(settings=asdict(geometry),flow=asdict(FlowSettings(cells_across=12,iterations=500,empty_pipe=True)))),
           ("thermal_solve","thermal",dict(settings=asdict(geometry),flow=asdict(FlowSettings(cells_across=12,inlet_length_mm=1,outlet_length_mm=1)),
                                            thermal=asdict(ThermalSettings(duration_s=.2,time_step_s=.01,output_interval_s=.05))))]
    for index,(operation,needed,values) in enumerate(cases):
        if not capabilities.get(needed):
            report["cases"][operation]=dict(skipped=capabilities["reasons"].get(needed,"unavailable"))
            continue
        case=directory/operation
        case.mkdir()
        target_operation=values.pop("_operation",operation)
        request={**base,**values,**dict(operation=target_operation,request_id=operation)}
        (case/"request.json").write_text(json.dumps(request))
        env={**os.environ,"SLURM_JOB_ID":str(5000+index),"OMP_NUM_THREADS":"2","OPENBLAS_NUM_THREADS":"1"}
        with (case/"worker.log").open("w") as log:
            status=subprocess.run([sys.executable,str(ROOT/"remote_worker.py"),"request.json"],cwd=case,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=180)
        if not (case/"output/result.json").exists():
            report["cases"][operation]=dict(passed=False,error=f"Worker exited {status.returncode} without a result manifest",log=str(case/"worker.log"))
            continue
        result=json.loads((case/"output/result.json").read_text())
        passed=status.returncode==0 and not result.get("error")
        if operation=="mechanical": passed=passed and result.get("summary",{}).get("status")=="completed"
        if operation=="convergence_level": passed=passed and result.get("level",{}).get("status")=="completed"
        if operation=="flow_solve" and passed:
            passed=json.loads((case/"output/flow_summary.json").read_text())["status"]=="converged"
        if operation=="thermal_solve" and passed:
            value=json.loads((case/"output/thermal_summary.json").read_text())
            passed=value["status"]=="completed" and value["history"][-1]["tpms_mean_C"]>20
        report["cases"][operation]=dict(passed=passed,result=result,log=str(case/"worker.log"))
        print(operation,passed,flush=True)
    report["passed"]=all(value.get("passed",False) for value in report["cases"].values())
    (directory/"verification.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(dict(passed=report["passed"],capabilities={k:capabilities[k] for k in ("mesh","kratos","flow","thermal")},
                         report=str(directory/"verification.json")),indent=2))
    return 0 if report["passed"] else 1


if __name__=="__main__": raise SystemExit(main())
