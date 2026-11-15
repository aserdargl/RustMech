"""Isolated licensed profile checks; never modifies the user's saved settings."""
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import argparse
import sys
import json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from model import Settings
from standard_tests import apply_profile, profile_report
from mesh_engine import generate_volume_mesh
from mapdl_backend import create_mapdl_backend
from solver_api import AnalysisRequest
from nonlinear_compression import extract_curve, export_graphs


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--case',choices=['tension','flexure'],required=True)
    parser.add_argument('--loading-mode',choices=['displacement','force'],default='displacement')
    args=parser.parse_args()
    folder=Path('results')/('Standard_'+args.case+'_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
    folder.mkdir(parents=True)
    settings=replace(apply_profile(Settings(),'astm_e8_subsize' if args.case=='tension' else 'iso178_preferred'),
        young_modulus=2000.,poisson_ratio=0.,material_model='linear_elastic',
        self_contact_mode='disabled',compression=.01,substeps=3,cores=1,
        mesh_resolution=1. if args.case=='flexure' else .5)
    settings=replace(settings,loading_mode=args.loading_mode,applied_force=.2 if args.case=='flexure' else 5.)
    mesh=generate_volume_mesh(settings)
    print(f'{args.case}: {mesh.grid.n_cells} elements; {folder.resolve()}',flush=True)
    execution=create_mapdl_backend().solve(AnalysisRequest(settings,mesh,folder.resolve()))
    report={'profile':profile_report(settings),'solver_error':execution.error,'diagnostics':execution.diagnostics}
    passed=False
    if execution.results is not None:
        history,report['reactions']=extract_curve(execution.results)
        report['graphs']=export_graphs(history,settings,folder,mesh,False,execution.results)
        measured=report['graphs']['final_measured_deformation_mm']
        force=history[-1,2]
        if args.case=='tension':
            modulus=(force/(6*3))/(measured/25)
        else:
            modulus=force*64**3/(4*10*4**3*measured)
        report['reference_modulus_MPa']=settings.young_modulus
        report['measured_modulus_MPa']=float(modulus)
        report['relative_modulus_error']=float(abs(modulus/settings.young_modulus-1))
        # Beam shear / finite contact differs from the Euler-Bernoulli reference.
        passed=(execution.error is None and report['graphs']['completed'] and
                not report['reactions']['force_balance_warning'] and report['relative_modulus_error']<.1)
    report['passed']=passed
    (folder/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2),flush=True)
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
