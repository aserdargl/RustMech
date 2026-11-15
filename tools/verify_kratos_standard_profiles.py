"""Real Kratos standard-profile references, locally or through the TRUBA adapter."""
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from model import Settings
from standard_tests import apply_profile
from mesh_engine import generate_volume_mesh
from kratos_export import export_kratos
from kratos_backend import KratosResults,KratosBackend
from solver_api import AnalysisRequest,KRATOS_CAPABILITIES
from nonlinear_compression import extract_curve,export_graphs


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--case',choices=['tension','flexure'],required=True)
    parser.add_argument('--loading-mode',choices=['displacement','force'],default='displacement')
    parser.add_argument('--mesh-type',choices=['HEX8','TET4'],default='HEX8')
    parser.add_argument('--mesh-size',type=float)
    parser.add_argument('--solid-fraction',type=float,default=30.)
    parser.add_argument('--porous',action='store_true')
    parser.add_argument('--self-contact',action='store_true')
    parser.add_argument('--contact-method',choices=['alm','penalty'],default='alm')
    parser.add_argument('--truba',action='store_true')
    parser.add_argument('--transport',choices=['auto','winscp','ssh'],default='winscp')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    folder=root/'build/kratos-standard-cases'/(args.case+'_'+args.loading_mode+'_'+args.mesh_type+'_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    settings=replace(apply_profile(Settings(),'astm_e8_subsize' if args.case=='tension' else 'iso178_preferred'),
        solver_backend='kratos',material_model='linear_elastic',young_modulus=2000.,poisson_ratio=0.,
        cores=1,substeps=4,compression=.1 if args.case=='flexure' else .01,
        mesh_resolution=1. if args.case=='flexure' else .75,local_mesh_size=.5,
        mesh_type=args.mesh_type,loading_mode=args.loading_mode,applied_force=2. if args.case=='flexure' else 5.,
        specimen_architecture='porous_centre' if args.porous else 'solid',
        solid_fraction=args.solid_fraction,
        self_contact_mode='frictionless' if args.self_contact else 'disabled',
        kratos_contact_method='penalty' if args.self_contact else args.contact_method,kratos_max_iterations=60,
        truba_walltime='00:05:00')
    errors=KRATOS_CAPABILITIES.validate(settings)
    if args.mesh_size is not None or args.porous:
        settings=replace(settings,mesh_resolution=args.mesh_size if args.mesh_size is not None else .5)
    if args.truba:settings=replace(settings,truba_transport=args.transport)
    if errors:raise ValueError(errors)
    mesh=generate_volume_mesh(settings)
    folder.mkdir(parents=True)
    print(f'{folder}\nElements: {mesh.grid.n_cells}',flush=True)
    if args.truba:
        execution=KratosBackend().solve(AnalysisRequest(settings,mesh,folder))
        if execution.error:raise RuntimeError(execution.error)
        reader=execution.results
    else:
        export_kratos(mesh,settings,folder)
        with (folder/'driver.log').open('w') as log:
            run=subprocess.run([str(root/'build/kratos-validation/Scripts/python.exe'),'kratos_driver.py'],cwd=folder,
                env={**os.environ,'OMP_NUM_THREADS':'1'},stdout=log,stderr=subprocess.STDOUT,timeout=180)
        if run.returncode:
            print((folder/'driver.log').read_text()[-6000:],flush=True)
            return 1
        reader=KratosResults(folder/'results',mesh,settings)
    history,reactions=extract_curve(reader)
    graphs=export_graphs(history,settings,folder,mesh,False,reader)
    force=history[-1,2];measured=graphs['final_measured_deformation_mm']
    modulus=(force/(6*3)/(measured/25) if args.case=='tension' else force*64**3/(4*10*4**3*measured))
    relative=abs(modulus/settings.young_modulus-1)
    passed=reader.manifest['complete'] and graphs['completed'] and not reactions['force_balance_warning'] and (args.porous or relative<.1)
    report={'passed':bool(passed),'modulus_MPa':float(modulus),'relative_reference_error':float(relative),
            'reactions':reactions,'graphs':graphs,'mesh_verified':reader.manifest['mesh_verified'],
            'slurm_job_id':reader.manifest.get('slurm_job_id'),'hostname':reader.manifest.get('hostname')}
    (folder/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2),flush=True)
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
