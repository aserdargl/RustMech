"""Geometric contact selection, exact-mesh export and pressure result integrity."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import numpy as np
from model import Settings
from solver_api import KRATOS_CAPABILITIES
from kratos_export import export_kratos
from tools.truba.kratos_self_contact import contact_partition
from tools.verify_kratos_pipeline import cube_mesh


class SelfContactTests(unittest.TestCase):
    def pair(self):
        lower=np.array([[0,0,0],[1,0,0],[1,1,0],[0,1,0]],dtype=float)*.1
        upper=lower[::-1].copy();upper[:,2]=.005
        return np.array([lower,upper]),np.arange(8).reshape(2,4),np.full(2,.1)

    def test_facing_surfaces_receive_opposite_stable_roles_after_rotation(self):
        points,ids,h=self.pair()
        roles,locked,pairs=contact_partition(points,ids,h)
        self.assertEqual(pairs,[(0,1)])
        self.assertNotEqual(roles[0],roles[1])
        rotated=points*np.array([-1,1,-1])
        moved,_,pairs=contact_partition(rotated,ids,h,locked)
        np.testing.assert_array_equal(moved,roles)
        self.assertEqual(pairs,[(0,1)])

    def test_connected_neighbours_and_distant_faces_are_not_self_pairs(self):
        points,ids,h=self.pair();ids[1,0]=ids[0,0]
        self.assertEqual(contact_partition(points,ids,h)[2],[])
        points,ids,h=self.pair();points[1,:,2]=1
        self.assertEqual(contact_partition(points,ids,h)[2],[])

    def test_conflicting_contact_history_is_reported_instead_of_losing_contact(self):
        points,ids,h=self.pair()
        with self.assertRaisesRegex(ValueError,'reverse'):
            contact_partition(points,ids,h,{0:1,1:1})
        points[0,:,:]=0
        with self.assertRaisesRegex(ValueError,'Degenerate'):contact_partition(points,ids,h)

    def test_self_contact_preserves_solid_and_separates_all_three_friction_properties(self):
        settings=Settings(solver_backend='kratos',self_contact_mode='frictional',kratos_contact_method='penalty',
            bottom_platen_friction_coeff=.2,top_platen_friction_coeff=.4,self_friction_coeff=.1)
        self.assertEqual(KRATOS_CAPABILITIES.validate(settings),[])
        mesh=cube_mesh()
        with TemporaryDirectory() as temp:
            folder=Path(temp);payload=export_kratos(mesh,settings,folder)
            with np.load(folder/'mesh_input.npz') as data:
                np.testing.assert_array_equal(data['points'],mesh.grid.points)
                np.testing.assert_array_equal(data['cells'],mesh.grid.cell_connectivity.reshape(-1,8))
            parameters=json.loads((folder/'ProjectParameters.json').read_text())
            contact=parameters['processes']['contact_process_list'][0]['Parameters']
            self.assertEqual(len(set(contact['contact_property_ids'].values())),3)
            self.assertEqual(contact['friction_coefficients'],{'0':.2,'1':.4,'2':.1})
            self.assertEqual(parameters['solver_settings']['convergence_criterion'],'contact_displacement_criterion')
            self.assertFalse(parameters['solver_settings']['contact_settings']['adaptative_strategy'])
            nodes=[n for face in payload['self_contact_surface']['faces'] for n in face]
            self.assertEqual(len(nodes),len(set(nodes)))
            self.assertTrue((folder/'kratos_self_contact.py').is_file())

    def test_unqualified_self_contact_combinations_are_rejected(self):
        settings=Settings(solver_backend='kratos',self_contact_mode='frictionless',kratos_contact_method='penalty')
        for changed in (replace(settings,mesh_type='TET4'),replace(settings,kratos_contact_method='alm'),
                        replace(settings,self_contact_mode='rough'),replace(settings,kratos_penalty_factor=float('nan'))):
            self.assertTrue(KRATOS_CAPABILITIES.validate(changed))


if __name__=='__main__':unittest.main()
