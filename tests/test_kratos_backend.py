"""Result integrity and safe, resumable remote job boundaries."""
from dataclasses import replace
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
from model import Settings
from kratos_backend import KratosResults, KratosBackend, LocalKratosBackend
from kratos_export import export_kratos, mesh_digest, specimen_surfaces
from local_kratos import local_kratos_python
from solver_api import KRATOS_CAPABILITIES, AnalysisRequest
from tools.verify_kratos_pipeline import cube_mesh
from truba_jobs import RemoteAuth, shell_job_settings, validated_remote_path, run_remote


class KratosIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.mesh=cube_mesh()
        self.ids=np.asarray(self.mesh.grid.point_data['ansys_node_num'])
        grid=self.mesh.grid
        self.manifest={'schema_version':1,'mesh_verified':True,'complete':True,
            'mesh_digest':mesh_digest(grid.points,grid.cell_connectivity.reshape(-1,8),self.ids),
            'stress_definition':'test', 'steps':[{'time':1.,'file':'step_000001.npz',
                'top_displacement':-.1,'bottom_reaction':1.,'top_reaction':-1.}]}
        self.save()

    def save(self,ids=None,displacement=None):
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        np.savez(self.root/'step_000001.npz',ids=self.ids if ids is None else ids,
                 displacement=np.zeros((len(self.ids),3)) if displacement is None else displacement,
                 equivalent_stress=np.ones(len(self.ids if ids is None else ids)))

    def test_maps_requested_node_order(self):
        result=KratosResults(self.root,self.mesh)
        np.testing.assert_array_equal(result.equivalent_stress(0,self.ids[::-1]),np.ones(len(self.ids)))

    def test_rejects_wrong_mesh_and_early_completion(self):
        self.manifest['mesh_digest']='wrong'
        self.save()
        with self.assertRaisesRegex(ValueError,'mesh'):KratosResults(self.root,self.mesh)
        self.manifest['mesh_digest']=mesh_digest(self.mesh.grid.points,self.mesh.grid.cell_connectivity.reshape(-1,8),self.ids)
        self.manifest['steps'][0]['time']=.5
        self.save()
        with self.assertRaisesRegex(ValueError,'final load'):KratosResults(self.root,self.mesh)

    @unittest.skipUnless(local_kratos_python() is not None, "Local Kratos runtime is not installed")
    def test_split_contact_steps_keep_the_prescribed_displacement(self):
        settings = Settings(size=1, cell_size=1, mesh_resolution=.5, compression=.06,
                            substeps=9, maximum_substeps=128, cores=1, solver_backend="kratos",
                            material_model="linear_elastic", young_modulus=1000,
                            self_contact_mode="disabled", bottom_platen_contact="frictionless",
                            top_platen_contact="frictionless", kratos_contact_method="penalty",
                            kratos_max_iterations=3)
        execution = LocalKratosBackend().solve(AnalysisRequest(settings, self.mesh, self.root))
        log = (self.root / "kratos.log").read_text(encoding="utf-8")
        self.assertIn("SPLITTING TIME STEP", log)
        self.assertFalse(execution.error, execution.error)
        result = execution.results
        self.assertTrue(result.manifest["complete"])
        self.assertAlmostEqual(result.time_values[-1], 1.)
        for target in np.linspace(0., 1., settings.substeps + 1)[1:]:
            self.assertTrue(np.isclose(result.time_values, target, rtol=0, atol=1e-8).any())
        for index, load in enumerate(result.time_values):
            self.assertAlmostEqual(result.top_platen_displacement(index), -settings.compression * load)

    def test_rejects_path_escape_duplicate_ids_and_nan(self):
        self.manifest['steps'][0]['file']='../step_000001.npz'
        self.save()
        with self.assertRaisesRegex(ValueError,'filename'):KratosResults(self.root,self.mesh)
        self.manifest['steps'][0]['file']='step_000001.npz'
        ids=self.ids.copy();ids[-1]=ids[0]
        self.save(ids=ids)
        with self.assertRaisesRegex(ValueError,'node IDs'):KratosResults(self.root,self.mesh).displacement(0,self.ids)
        displacement=np.zeros((len(self.ids),3));displacement[0,0]=np.nan
        self.save(displacement=displacement)
        with self.assertRaisesRegex(ValueError,'Non-finite'):KratosResults(self.root,self.mesh).displacement(0,self.ids)

    def test_export_preserves_mesh_and_rejects_unqualified_physics(self):
        settings=Settings(solver_backend='kratos',self_contact_mode='disabled',material_model='linear_elastic')
        export_kratos(self.mesh,settings,self.root)
        with np.load(self.root/'mesh_input.npz') as data:
            np.testing.assert_array_equal(data['cells'],self.mesh.grid.cell_connectivity.reshape(-1,8))
            np.testing.assert_array_equal(data['ids'],self.ids)
        for changed in (replace(settings,self_contact_mode='frictional'),replace(settings,kratos_line_search=True),
                        replace(settings,kratos_linear_solver='amgcl',kratos_contact_method='alm')):
            self.assertTrue(KRATOS_CAPABILITIES.validate(changed))

    def test_platen_search_covers_edge_faces_on_refined_mesh(self):
        settings=Settings(solver_backend='kratos',self_contact_mode='disabled')
        mesh=cube_mesh(subdivisions=6)
        export_kratos(mesh,settings,self.root)
        parameters=json.loads((self.root/'ProjectParameters.json').read_text())
        factor=parameters['processes']['contact_process_list'][0]['Parameters']['search_parameters']['search_factor']
        # Farthest slave face centre is sqrt(2)*(0.5-h/2) from patch centre.
        h=1./6
        required=(.5-h/2)/(h/2)
        self.assertGreater(factor,required)

    def test_quadratic_solids_keep_mesh_and_use_all_platen_nodes(self):
        for mesh_type, width, facets_per_face in (('HEX20', 20, 6), ('TET10', 10, 4)):
            with self.subTest(mesh_type=mesh_type):
                mesh = cube_mesh(tetra=mesh_type=='TET10', quadratic=True)
                grid = mesh.grid
                ids = np.asarray(grid.point_data['ansys_node_num'])
                settings = Settings(solver_backend='kratos', mesh_type=mesh_type,
                                    self_contact_mode='disabled', kratos_contact_method='penalty')
                self.assertEqual(KRATOS_CAPABILITIES.validate(settings), [])
                payload = export_kratos(mesh, settings, self.root)
                cells = grid.cell_connectivity.reshape(-1, width)
                with np.load(self.root/'mesh_input.npz') as exported:
                    np.testing.assert_array_equal(exported['cells'], cells)
                order = payload.get('solver_cell_order', tuple(range(width)))
                if mesh_type == 'HEX20':
                    self.assertEqual(order[12:], (16, 17, 18, 19, 12, 13, 14, 15))
                deck = (self.root/'model.mdpa').read_text().splitlines()
                start = deck.index(f'Begin Elements TotalLagrangianElement3D{width}N')
                self.assertEqual(list(map(int, deck[start+1].split()[2:])), ids[cells[0, list(order)]].tolist())

                faces = specimen_surfaces(grid, ids, grid.points, quadratic=True)
                for side, z, direction in (('BottomSpecimen', 0, -1), ('TopSpecimen', 1, 1)):
                    self.assertEqual(len(faces[side]), (4 if width == 20 else 8) * facets_per_face)
                    triangles = np.array(faces[side])
                    np.testing.assert_array_equal(np.unique(triangles), ids[grid.points[:, 2]==z])
                    a, b, c = np.moveaxis(grid.points[triangles-1], 1, 0)
                    signed_areas = np.cross(b-a, c-a)[:, 2] * direction / 2
                    self.assertTrue(np.all(signed_areas > 0))
                    self.assertAlmostEqual(signed_areas.sum(), 1.)

                self.manifest['mesh_digest'] = payload['mesh_digest']
                self.save(ids=ids, displacement=np.zeros((len(ids), 3)))
                reader = KratosResults(self.root, mesh, settings)
                np.testing.assert_array_equal(reader.equivalent_stress(0, ids[::-1]), np.ones(len(ids)))

    def test_job_resources_and_remote_path_reject_injection(self):
        for settings in (Settings(truba_partition='debug\n#SBATCH --nodes=999'),
                         Settings(truba_walltime='00:99:00'),Settings(cores='4\necho unsafe')):
            with self.assertRaises((ValueError,TypeError)):shell_job_settings(settings,'user')
        for path in ('/arf/scratch/other/RustMech/'+'a'*32,'/arf/scratch/user/RustMech/../home'):
            with self.assertRaises(ValueError):validated_remote_path(path,'user')
        self.assertNotIn('secret',repr(RemoteAuth('user','secret')))

    def test_resume_uses_same_remote_directory_and_job_id(self):
        remote='/arf/scratch/user/RustMech/'+'a'*32
        record={'host':'172.16.6.15','username':'user','transport':'ssh','job_id':'123',
                'remote_directory':remote,'local_directory':str(self.root)}
        (self.root/'remote_job.json').write_text(json.dumps(record))
        class Transport:
            username='user'
            def run(inner,local,target,saved,paths,resume=False):
                self.assertTrue(resume);self.assertEqual(target,remote);self.assertEqual(saved['job_id'],'123')
        with patch('truba_jobs.connection',return_value=Transport()),patch('truba_jobs.state_directory',return_value=self.root/'state'):
            result=run_remote(Settings(),self.root,resume=True)
        self.assertEqual(result['job_id'],'123')
        self.assertFalse((self.root/'run.slurm').exists())

    def test_completed_download_can_be_reported_without_network(self):
        results=self.root/'results'
        results.mkdir()
        self.manifest['slurm_job_id']='123'
        self.save()
        for name in ('manifest.json','step_000001.npz'):shutil.copyfile(self.root/name,results/name)
        (results/'slurm_status.txt').write_text('COMPLETED|0:0|')
        (self.root/'remote_job.json').write_text(json.dumps({'state':'downloaded','host':'172.16.6.15','job_id':'123'}))
        with patch('kratos_backend.run_remote',side_effect=AssertionError('Network must not be used')):
            result=KratosBackend().resume(AnalysisRequest(Settings(solver_backend='kratos',self_contact_mode='disabled'),self.mesh,self.root))
        self.assertIsNone(result.error)
        self.assertEqual(result.diagnostics['result_source'],'downloaded_local_cache')

    def test_pressure_fields_preserve_requested_node_order_and_reject_invalid_data(self):
        self.manifest['steps'][0]['self_contact']={'active_nodes':1,'maximum_pressure':2.}
        self.save()
        values=np.arange(len(self.ids),dtype=float)
        np.savez(self.root/'step_000001.npz',ids=self.ids,displacement=np.zeros((len(self.ids),3)),
                 equivalent_stress=np.ones(len(self.ids)),contact_pressure=values,contact_tangential_pressure=values*.1)
        reader=KratosResults(self.root,self.mesh)
        self.assertTrue(reader.has_self_contact)
        np.testing.assert_array_equal(reader.contact_pressure(0,self.ids[::-1]),values[::-1])
        values[0]=-1
        np.savez(self.root/'step_000001.npz',ids=self.ids,displacement=np.zeros((len(self.ids),3)),
                 equivalent_stress=np.ones(len(self.ids)),contact_pressure=values)
        with self.assertRaisesRegex(ValueError,'pressure'):KratosResults(self.root,self.mesh).contact_pressure(0,self.ids)


if __name__=='__main__':unittest.main()
