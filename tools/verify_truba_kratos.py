"""Submit one tiny validation job, or retrieve its existing recovery record."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from model import Settings, settings_from_dict
from kratos_backend import KratosBackend
from solver_api import AnalysisRequest
from tools.verify_kratos_pipeline import cube_mesh

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--resume',type=Path)
parser.add_argument('--full-report',action='store_true')
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]
if args.resume:
    record=json.loads(args.resume.read_text())
    local=Path(record['local_directory'])
    settings=settings_from_dict(json.loads((local/'input.json').read_text())['settings'])
    subdivisions=json.loads((local/'input.json').read_text())['mesh_metadata']['subdivisions']
else:
    local=root/'build/truba-runs'/datetime.now().strftime('%Y%m%d_%H%M%S')
    subdivisions=4
    settings=Settings(size=1,cell_size=1,mesh_resolution=.25,compression=.001,substeps=2,cores=4,
                      solver_backend='kratos',material_model='linear_elastic',young_modulus=1000,
                      self_contact_mode='disabled',bottom_platen_contact='frictionless',top_platen_contact='frictionless',
                      truba_transport='winscp',truba_walltime='00:05:00')
backend=KratosBackend(marker=root/'build/truba-pilot-current.json')
request=AnalysisRequest(settings,cube_mesh(subdivisions=subdivisions),local)
if args.full_report:
    from nonlinear_compression import run_analysis, resume_kratos_analysis
    folder=resume_kratos_analysis(args.resume,job_marker=root/'build/truba-pilot-current.json') if args.resume else run_analysis(settings,request.mesh,backend)
    (root/'build/truba-retrieved-report.txt').write_text(str(folder),encoding='utf-8')
    report=json.loads((folder/'summary.json').read_text())
    force=report['graphs']['final_force_N']
    passed=report['status']=='completed' and abs(force-1)<.02 and report['reactions']['maximum_relative_force_imbalance']<.01
    verification={'passed':passed,'fixture':'Homogeneous 1 mm cube; prescribed compression .001 mm, E=1000 MPa',
                  'expected_force_N':1.,'force_N':force,'job_id':report['solver_diagnostics']['job_id'],
                  'status':report['status'],'folder':str(folder),'diagnostics':report['solver_diagnostics']}
    (folder/'verification.json').write_text(json.dumps(verification,indent=2)+'\n')
    print(json.dumps(verification,indent=2),flush=True)
    raise SystemExit(0 if passed else 1)
result=backend.resume(request) if args.resume else backend.solve(request)
report={'passed':False,'error':result.error,'diagnostics':result.diagnostics}
if result.results and not result.error:
    reader=result.results
    bottom,top=reader.platen_reactions(reader.nsets-1)
    reader.displacement(reader.nsets-1,request.mesh.grid.point_data['ansys_node_num'])
    reader.equivalent_stress(reader.nsets-1,request.mesh.grid.point_data['ansys_node_num'])
    report.update(passed=bool(abs(bottom+top)<.01 and abs(-top-1)<.02),force=-top,
                  hostname=reader.manifest.get('hostname'),job_id=reader.manifest.get('slurm_job_id'))
(local/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2),flush=True)
raise SystemExit(0 if report['passed'] else 1)
