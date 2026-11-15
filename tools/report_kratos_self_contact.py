"""Create a correctly labelled report of the submitted self-contact fixture."""
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pyvista as pv
from model import FEMesh,settings_from_dict
from kratos_backend import KratosResults
from nonlinear_compression import extract_curve,export_graphs,export_field_results
from tools.verify_kratos_self_contact import verify

raw=Path(sys.argv[1]).resolve()
payload=json.loads((raw/'input.json').read_text())
settings=settings_from_dict(payload['settings'])
grid=pv.read(raw/'submitted_mesh.vtu')
meta=payload['mesh_metadata'];ids=grid.point_data['ansys_node_num'];z=grid.points[:,2]
mesh=FEMesh(grid,meta['spacing'],meta['subdivisions'],meta['fraction'],meta['components'],
            ids[np.isclose(z,z.min())],ids[np.isclose(z,z.max())],pv.UnstructuredGrid(),meta['original_components'],meta['candidate_elements'])
verification=verify(raw,mesh,settings)
assert verification['passed']
reader=KratosResults(raw/'results',mesh)
root=Path(__file__).resolve().parents[1]
folder=root/'HumanResults'/('SelfContact_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
for name in ('01_Geometry','02_MeshQuality','03_Results','04_Graphs'):(folder/name).mkdir(parents=True)
grid.extract_surface(algorithm='dataset_surface').triangulate().save(folder/'01_Geometry/verification_specimen.stl')
grid.save(folder/'02_MeshQuality/mesh.vtu')
history,reactions=extract_curve(reader)
graphs=export_graphs(history,settings,folder/'04_Graphs',mesh=mesh,images=True)
fields=export_field_results(reader,mesh,folder/'03_Results',images=True)
record=json.loads((raw/'remote_job.json').read_text())
summary={'status':'completed','solver':'kratos','settings':asdict(settings),
    'geometry_source':'Exact submitted validation fixture, not the settings-generated structure',
    'verification_fixture':verification['fixture'],'validation':verification,
    'mesh':{'nodes':grid.n_points,'elements':grid.n_cells},'solver_diagnostics':record,
    'reactions':reactions,'graphs':graphs,'fields':fields}
(folder/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
(folder/'verification.json').write_text(json.dumps(verification,indent=2)+'\n')
(root/'build/self-contact-report-folder.txt').write_text(str(folder),encoding='utf-8')
print('REPORT_FOLDER='+str(folder),flush=True)
