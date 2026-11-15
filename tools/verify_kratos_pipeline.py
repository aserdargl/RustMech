"""Small real-solver checks; uses an isolated Kratos interpreter, never TRUBA."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
import pyvista as pv
from model import FEMesh, Settings
from kratos_export import export_kratos
from kratos_backend import KratosResults


def cube_mesh(tetra=False,subdivisions=2):
    spacing=1./subdivisions
    raw=pv.ImageData(dimensions=(subdivisions+1,)*3,spacing=(spacing,)*3).cast_to_unstructured_grid()
    cells=raw.cells.reshape(-1,9).copy()
    cells[:,1:]=cells[:,1:][:,[0,1,3,2,4,5,7,6]]
    grid=pv.UnstructuredGrid(cells,np.full(raw.n_cells,12),raw.points)
    if tetra:grid=grid.triangulate()
    ids=np.arange(1,grid.n_points+1)
    grid.point_data['ansys_node_num']=ids
    grid.cell_data['ansys_elem_num']=np.arange(1,grid.n_cells+1)
    return FEMesh(grid,spacing,subdivisions,100.,1,ids[grid.points[:,2]==0],ids[grid.points[:,2]==1],pv.UnstructuredGrid(),1,grid.n_cells)


if __name__ == '__main__':
    python=ROOT/'build/kratos-validation/Scripts/python.exe'
    base=Settings(size=1,cell_size=1,mesh_resolution=.5,compression=.001,substeps=2,
                  solver_backend='kratos',material_model='linear_elastic',young_modulus=1000,
                  self_contact_mode='disabled',bottom_platen_contact='frictionless',top_platen_contact='frictionless')
    cases={
        'elastic':base,
        'force':replace(base,loading_mode='force',applied_force=1.),
        'friction_plastic':replace(base,material_model='bilinear_isotropic',yield_stress=2.,tangent_modulus=10.,compression=.02,substeps=20,kratos_max_iterations=100,bottom_platen_contact='frictional',top_platen_contact='frictional'),
        'tabulated':replace(base,material_model='multilinear_isotropic',hardening_curve=((0.,2.),(.1,3.)),compression=.005,substeps=10,kratos_max_iterations=100),
        'tet4':replace(base,mesh_type='TET4'),
        'penalty_amgcl':replace(base,kratos_contact_method='penalty',kratos_linear_solver='amgcl'),
        'mixed_contact':replace(base,bottom_platen_contact='frictional'),
        'hex64':replace(base,mesh_resolution=.25),
        'hex216':replace(base,mesh_resolution=1./6),
        'tet_fine':replace(base,mesh_resolution=.25,mesh_type='TET4'),
        'zero_friction':replace(base,bottom_platen_contact='frictional',top_platen_contact='frictional',bottom_platen_friction_coeff=0.,top_platen_friction_coeff=0.),
    }
    report={}
    for name,settings in cases.items():
        mesh=cube_mesh(settings.mesh_type=='TET4',subdivisions=round(1/settings.mesh_resolution))
        target=ROOT/'build/kratos-cases'/name
        export_kratos(mesh,settings,target)
        with (target/'driver.log').open('w') as log:
            run=subprocess.run([str(python),'kratos_driver.py'],cwd=target,stdout=log,stderr=subprocess.STDOUT,env={**os.environ,'OMP_NUM_THREADS':'1'},timeout=120)
        if run.returncode:
            report[name]={'passed':False,'error':json.loads((target/'results/manifest.json').read_text()).get('error','unknown')[:300]}
        else:
            reader=KratosResults(target/'results',mesh)
            bottom,top=reader.platen_reactions(reader.nsets-1)
            error=abs(bottom+top)/max(abs(top),1e-12)
            expected=1. if name in ('elastic','force','tet4','penalty_amgcl','hex64','hex216','tet_fine','zero_friction') else None
            passed=reader.manifest['complete'] and error<.01 and (expected is None or abs(-top-expected)<.02)
            reader.displacement(reader.nsets-1,mesh.grid.point_data['ansys_node_num'])
            reader.equivalent_stress(reader.nsets-1,mesh.grid.point_data['ansys_node_num'])
            report[name]={'passed':bool(passed),'steps':reader.nsets,'force':-top,'relative_force_imbalance':error}
        print(name,json.dumps(report[name]),flush=True)
    (ROOT/'build/kratos-real-solver-tests.json').write_text(json.dumps(report,indent=2)+'\n')
    raise SystemExit(0 if all(v['passed'] for v in report.values()) else 1)
