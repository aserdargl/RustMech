"""Qualify genuine slot closure, a no-contact control and Coulomb shear on TRUBA."""
import argparse
from dataclasses import replace
from datetime import datetime
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
import pyvista as pv
from model import FEMesh,Settings,settings_from_dict
from kratos_export import export_kratos
from kratos_backend import KratosBackend,KratosResults
from solver_api import AnalysisRequest
from truba_jobs import get_auth, run_remote


def slotted_mesh(tetra=False):
    h=.1
    levels=[0.,.1,.2,.3,.4,.475,.525,.6,.7,.8,.9,1.]
    raw=pv.RectilinearGrid(np.linspace(0,1,11),np.linspace(0,.4,5),levels).cast_to_unstructured_grid()
    cells=raw.cells.reshape(-1,9).copy()
    if np.all(raw.celltypes==11):cells[:,1:]=cells[:,1:][:,[0,1,3,2,4,5,7,6]]
    grid=pv.UnstructuredGrid(cells,np.full(raw.n_cells,12),raw.points)
    x,y,z=grid.cell_centers().points.T
    keep=(x<.3)|(x>.7)|(z<.1)|(z>.9)|((x>.4)&(x<.6)&((z<.475)|(z>.525)))
    grid=grid.extract_cells(keep)
    if tetra:grid=grid.triangulate()
    ids=np.arange(1,grid.n_points+1)
    grid.point_data['ansys_node_num']=ids
    grid.cell_data['ansys_elem_num']=np.arange(1,grid.n_cells+1)
    return FEMesh(grid,h,10,100*grid.volume/.4,1,ids[np.isclose(grid.points[:,2],0)],ids[np.isclose(grid.points[:,2],1)],pv.UnstructuredGrid(),1,grid.n_cells)


def fixture_settings(mode='frictionless',tetra=False):
    return Settings(size=1,cell_size=1,mesh_resolution=.1,compression=.07,substeps=70,
        solver_backend='kratos',material_model='linear_elastic',young_modulus=1000,
        self_contact_mode=mode,kratos_contact_method='penalty',kratos_max_iterations=100,
        bottom_platen_contact='frictional',top_platen_contact='frictional',bottom_platen_friction_coeff=.6,top_platen_friction_coeff=.6,
        mesh_type='TET4' if tetra else 'HEX8',truba_transport='winscp',truba_walltime='00:10:00')


def verify(folder,mesh,settings):
    reader=KratosResults(folder/'results',mesh)
    points=np.asarray(mesh.grid.points)
    ids=np.asarray(mesh.grid.point_data['ansys_node_num'])
    lower=np.flatnonzero(np.isclose(points[:,2],.475)&(points[:,0]>=.4-1e-8)&(points[:,0]<=.6+1e-8))
    upper=np.flatnonzero(np.isclose(points[:,2],.525)&(points[:,0]>=.4-1e-8)&(points[:,0]<=.6+1e-8))
    lower=np.array(sorted(lower,key=lambda i:tuple(points[i,:2])))
    upper=np.array(sorted(upper,key=lambda i:tuple(points[i,:2])))
    assert len(lower)==len(upper)>0
    rows=[]
    for i,step in enumerate(reader.steps):
        u=reader.displacement(i,ids)
        reader.equivalent_stress(i,ids)
        deformed=points+u
        gap=deformed[upper,2]-deformed[lower,2]
        bottom,top=reader.platen_reactions(i)
        state=step.get('self_contact',{})
        rows.append({'time':step['time'],'minimum_gap_mm':float(gap.min()),'maximum_gap_mm':float(gap.max()),
            'force_N':-top,'force_imbalance':abs(bottom+top)/max(abs(bottom),abs(top),1e-9),
            'active_nodes':state.get('active_nodes',0),'pressure_MPa':state.get('maximum_pressure',0.),
            'tangent_pressure_MPa':state.get('maximum_tangential_pressure',0.),'coulomb_ratio':state.get('coulomb_limit_ratio',0.),
            'maximum_displacement_mm':float(abs(u).max())})
    last=rows[-1]
    enabled=settings.self_contact_mode!='disabled'
    penetration=max(0.,-last['minimum_gap_mm'])
    passed=bool(reader.manifest.get('complete') and max(r['force_imbalance'] for r in rows)<.01
        and max(r['maximum_displacement_mm'] for r in rows)<.2 and last['force_N']>1
        and ((penetration<1e-4 and max(r['active_nodes'] for r in rows)>0 and max(r['pressure_MPa'] for r in rows)>0)
             if enabled else penetration>.01))
    if settings.self_contact_mode=='frictional':
        passed=passed and max(r['coulomb_ratio'] for r in rows)<=1.01
    if enabled:
        expected=settings.self_friction_coeff if settings.self_contact_mode=='frictional' else 0.
        passed=passed and bool(np.isclose(reader.manifest.get('self_contact_coefficient',np.nan),expected,rtol=0,atol=1e-12))
    report={'passed':passed,'fixture':'Connected slotted solid; initial gap 0.05 mm, compression 0.07 mm',
        'self_contact_mode':settings.self_contact_mode,'self_friction_coefficient':settings.self_friction_coeff,
        'executed_self_friction_coefficient':reader.manifest.get('self_contact_coefficient'),'final_penetration_mm':penetration,
        'maximum_relative_force_imbalance':max(r['force_imbalance'] for r in rows),
        'maximum_active_nodes':max(r['active_nodes'] for r in rows),'maximum_pressure_MPa':max(r['pressure_MPa'] for r in rows),
        'maximum_tangential_pressure_MPa':max(r['tangent_pressure_MPa'] for r in rows),
        'maximum_coulomb_ratio':max(r['coulomb_ratio'] for r in rows),'final_force_N':last['force_N'],
        'job_id':reader.manifest.get('slurm_job_id'),'hostname':reader.manifest.get('hostname'),'folder':str(folder)}
    (folder/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    with (folder/'gap_history.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    print(json.dumps(report,indent=2),flush=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',nargs='?',default='frictionless',choices=['disabled','frictionless','frictional'])
    parser.add_argument('--remote',action='store_true')
    parser.add_argument('--shear',action='store_true')
    parser.add_argument('--tetra',action='store_true')
    parser.add_argument('--mu',type=float,default=.15)
    parser.add_argument('--force',type=float)
    parser.add_argument('--resume',type=Path)
    args=parser.parse_args()
    if args.resume:
        record=json.loads(args.resume.read_text());folder=Path(record['local_directory'])
        settings=settings_from_dict(json.loads((folder/'input.json').read_text())['settings'])
        mesh=SimpleNamespace(grid=pv.read(folder/'submitted_mesh.vtu'))
        result=KratosBackend(auth=get_auth(),marker=ROOT/'build/self-contact-remote-job.json').resume(AnalysisRequest(settings,mesh,folder))
        if result.error:raise RuntimeError(result.error)
    else:
        folder=ROOT/'build/self-contact-cases'/(args.mode+'-'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        mesh=slotted_mesh(args.tetra);settings=replace(fixture_settings(args.mode,args.tetra),self_friction_coeff=args.mu)
        if args.force is not None:settings=replace(settings,loading_mode='force',applied_force=args.force)
        export_kratos(mesh,settings,folder)
        if args.shear:
            path=folder/'ProjectParameters.json';data=json.loads(path.read_text())
            data['processes']['constraints_process_list'][0]['Parameters']['value'][0]='0.01*t'
            path.write_text(json.dumps(data))
        (ROOT/'build/self-contact-current.json').write_text(json.dumps({'folder':str(folder)}))
        if args.remote:
            run_remote(settings,folder,auth=get_auth(),marker=ROOT/'build/self-contact-remote-job.json')
        else:
            with (folder/'driver.log').open('w') as log:
                process=subprocess.run([str(ROOT/'build/kratos-validation/Scripts/python.exe'),'kratos_driver.py'],cwd=folder,
                    stdout=log,stderr=subprocess.STDOUT,env={**os.environ,'OMP_NUM_THREADS':'1'},timeout=300)
            if process.returncode:raise RuntimeError((folder/'results/manifest.json').read_text()[:1500])
    report=verify(folder,mesh,settings)
    raise SystemExit(0 if report['passed'] else 1)
