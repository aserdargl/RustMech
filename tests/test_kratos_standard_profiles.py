"""Portable fixture export, loading direction, UI routing and result identity."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import numpy as np
from model import Settings
from standard_tests import apply_profile,profile
from mesh_engine import generate_volume_mesh
from kratos_export import export_kratos
from kratos_fixtures import line_fixture_nodes
from solver_api import KRATOS_CAPABILITIES


class KratosStandardProfilesTests(unittest.TestCase):
    def settings(self,identifier,**changes):
        return replace(apply_profile(Settings(),identifier),solver_backend='kratos',**changes)

    def test_tension_grips_and_load_are_tied_to_original_end_faces(self):
        for mode in ('displacement','force'):
            s=self.settings('astm_e8_subsize',loading_mode=mode,mesh_resolution=.75)
            self.assertEqual(KRATOS_CAPABILITIES.validate(s),[])
            mesh=generate_volume_mesh(s)
            before=mesh.grid.points.copy()
            with TemporaryDirectory() as temp:
                folder=Path(temp);data=export_kratos(mesh,s,folder)
                np.testing.assert_array_equal(data['plate_nodes']['BottomPlaten'],np.sort(mesh.bottom_ids))
                np.testing.assert_array_equal(data['plate_nodes']['TopPlaten'],np.sort(mesh.top_ids))
                params=json.loads((folder/'ProjectParameters.json').read_text())
                self.assertNotIn('contact_settings',params['solver_settings'])
                self.assertEqual(params['processes']['contact_process_list'],[])
                value=params['processes']['constraints_process_list'][0]['Parameters']['value'][2]
                self.assertFalse(value.startswith('-'))
                self.assertEqual(data['load_sign'],1.)
                np.testing.assert_array_equal(mesh.grid.points,before)

    def test_flexure_uses_exact_support_lines_and_preserves_solid_mesh(self):
        s=self.settings('iso178_preferred',mesh_resolution=1.)
        mesh=generate_volume_mesh(s)
        with TemporaryDirectory() as temp:
            folder=Path(temp);data=export_kratos(mesh,s,folder)
            params=json.loads((folder/'ProjectParameters.json').read_text())
            self.assertEqual(params['processes']['contact_process_list'],[])
            self.assertEqual(set(data['support_nodes']),{'left','right','loading'})
            self.assertEqual(data['plate_nodes']['BottomPlaten'],data['support_nodes']['left']+data['support_nodes']['right'])
            self.assertEqual(data['plate_nodes']['TopPlaten'],data['support_nodes']['loading'])
            self.assertIn('Idealized',data['test_profile']['fixture_model'])
            self.assertLessEqual(params['solver_settings']['residual_relative_tolerance'],1e-8)
            self.assertEqual(params['processes']['constraints_process_list'][0]['Parameters']['value'][:2],[None,None])
            with np.load(folder/'mesh_input.npz') as values:np.testing.assert_array_equal(values['points'],mesh.grid.points)

    def test_support_lines_are_preserved_under_refinement_and_invalid_mesh_is_rejected(self):
        from solver_api import map_node_values
        for spacing in (1.,.7):
            s=self.settings('iso178_preferred',mesh_resolution=spacing)
            mesh=generate_volume_mesh(s)
            lines=line_fixture_nodes(mesh,profile(s))
            for name,x,z in (('left',8.,0.),('right',72.,0.),('loading',40.,4.)):
                points=map_node_values(mesh.grid.point_data['ansys_node_num'],mesh.grid.points,np.array(lines[name]))
                np.testing.assert_allclose(points[:,0],x)
                np.testing.assert_allclose(points[:,2],z)
                self.assertEqual((points[:,1].min(),points[:,1].max()),(0.,10.))
            mesh.grid.points[:,0]+=.1
            with self.assertRaisesRegex(ValueError,'fixture line'):line_fixture_nodes(mesh,profile(s))
        self.assertTrue(KRATOS_CAPABILITIES.validate(replace(s,mesh_type='TET4')))

    def test_tension_self_contact_keeps_its_own_interface(self):
        s=self.settings('astm_e8_subsize',specimen_architecture='porous_centre',mesh_resolution=.75,
                        self_contact_mode='frictionless',kratos_contact_method='penalty')
        with TemporaryDirectory() as temp:
            folder=Path(temp);data=export_kratos(generate_volume_mesh(s),s,folder)
            params=json.loads((folder/'ProjectParameters.json').read_text())
            contact=params['processes']['contact_process_list'][0]['Parameters']
            self.assertEqual(contact['contact_model_part'],{'2':['SelfSurface']})
            self.assertTrue(data['self_contact_nodes'])

    def test_switch_to_kratos_preserves_standard_and_bonded_grips(self):
        from PySide6.QtWidgets import QApplication,QWidget
        from unittest.mock import MagicMock
        from nonlinear_compression import RustMechDialog
        _app=QApplication.instance() or QApplication([])
        with patch('window_state.install_window_state'),patch('nonlinear_compression.create_preview_viewport',return_value=MagicMock(interactor=QWidget())),patch.object(RustMechDialog,'start_preview',lambda self:None):
            dialog=RustMechDialog(apply_profile(Settings(),'astm_e8_subsize'))
            try:
                dialog.solver_combo.setCurrentIndex(dialog.solver_combo.findData('kratos'))
                selected=dialog.collect()
                self.assertEqual(selected.test_profile,'astm_e8_subsize')
                self.assertEqual(selected.bottom_platen_contact,'bonded')
                self.assertEqual(KRATOS_CAPABILITIES.validate(selected),[])
                self.assertEqual(dialog.substeps_status.text(),'Fixed increments')
                self.assertIsNone(selected.minimum_substeps)
                self.assertFalse(dialog.controls['size'].isEnabled())
                self.assertIsNone(dialog.analysis_job)
                self.assertIsNone(dialog.sync_window)
            finally:dialog.dispose();dialog.deleteLater()


if __name__=='__main__':unittest.main()
