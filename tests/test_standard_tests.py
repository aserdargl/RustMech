"""Exercise specimen envelopes, protected regions, load signs and UI round trips."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock
import unittest
import numpy as np
from model import Settings, validate_settings, save_settings, load_settings
from mesh_engine import make_surface, generate_volume_mesh, preview_key, build_preview
from standard_tests import apply_profile, sample_shape, profile_report


class StandardGeometryTests(unittest.TestCase):
    def test_small_valid_cap_triangles_survive_standard_surface_generation(self):
        import pyvista as pv
        from standard_tests import make_standard_surface

        width = 2e-7
        vertices = np.array([
            [0, 0, 0], [1, 0, 0], [1, width, 0], [0, width, 0],
            [0, 0, width], [1, 0, width], [1, width, width], [0, width, width],
            [.5, 0, 0],
        ], dtype=np.float64)
        faces = np.array([
            [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
            [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
            [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7],
            [0, 8, 1],  # Exactly collinear: this face must still be removed.
        ], dtype=np.int32)
        settings = replace(apply_profile(Settings(), 'iso13314_cube'), stl_resolution=.5)
        # Inject a known closed contour with tiny end caps. Keep the real clipping,
        # triangle filtering, native cleanup and STL writer in the exercised path.
        with patch('standard_tests.sample_shape', return_value=np.zeros((3, 3, 3), np.float32)), patch(
            'skimage.measure.marching_cubes', return_value=(vertices + .5, faces, None, None)
        ):
            surface = make_standard_surface(settings)
        self.assertEqual(surface.n_cells, 12)
        self.assertEqual(surface.n_open_edges, 0)
        self.assertTrue(surface.is_manifold)
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'small-caps.stl'
            surface.save(path)
            restored = pv.read(path)
        self.assertEqual(restored.n_cells, 12)
        self.assertEqual(restored.n_open_edges, 0)
        self.assertTrue(restored.is_manifold)

    def test_profiles_persist_and_old_settings_remain_custom(self):
        from model import settings_from_dict
        self.assertEqual(settings_from_dict({'size':6}).test_profile,'custom')
        for identifier in ('iso13314_cube','astm_e8_subsize','iso178_preferred'):
            settings=apply_profile(Settings(),identifier)
            self.assertEqual(validate_settings(settings),[])
            with TemporaryDirectory() as folder:
                path=Path(folder)/'settings.json';save_settings(settings,path)
                self.assertEqual(load_settings(path),settings)

    def test_solid_envelopes_and_hex_geometry(self):
        from standard_tests import profile
        for identifier in ('astm_e8_subsize','iso178_preferred'):
            settings=apply_profile(Settings(),identifier)
            surface=make_surface(settings)
            self.assertTrue(surface.is_manifold)
            self.assertEqual(surface.n_open_edges,0)
            mesh=generate_volume_mesh(settings,surface)
            self.assertEqual(mesh.solve_errors,[])
            np.testing.assert_allclose(np.array(mesh.grid.bounds).reshape(3,2)[:,1],profile(settings).dimensions)
            self.assertGreater(mesh.grid.volume,0)
            self.assertTrue(np.all(mesh.grid.cell_quality('scaled_jacobian').cell_data['scaled_jacobian']>0))
        self.assertAlmostEqual(mesh.grid.volume,80*10*4)
        mesh=generate_volume_mesh(replace(settings,mesh_resolution=.7))
        for x in (8.,40.,72.):self.assertTrue(np.any(np.isclose(mesh.grid.points[:,0],x)))

    def test_pores_change_centre_only_and_protect_flexure_nose(self):
        for identifier in ('astm_e8_subsize','iso178_preferred'):
            solid=apply_profile(Settings(),identifier)
            porous=replace(solid,specimen_architecture='porous_centre')
            axes=([np.linspace(.1,9.9,17),np.linspace(.1,2.9,9),np.linspace(.1,101.9,205)]
                  if identifier=='astm_e8_subsize' else
                  [np.linspace(.1,79.9,161),np.linspace(.1,9.9,17),np.linspace(.1,3.9,9)])
            a,b=sample_shape(solid,axes)<0,sample_shape(porous,axes)<0
            self.assertGreater(np.count_nonzero(a&~b),0)
            if identifier=='astm_e8_subsize':
                np.testing.assert_array_equal(a[:,:,axes[2]<38.5],b[:,:,axes[2]<38.5])
                np.testing.assert_array_equal(a[:,:,axes[2]>63.5],b[:,:,axes[2]>63.5])
            else:
                protected=(axes[0]<16)|(axes[0]>64)|(abs(axes[0]-40)<5)
                np.testing.assert_array_equal(a[protected],b[protected])
            self.assertTrue(profile_report(porous)['deviations'])

    def test_architecture_invalidates_mesh_cache(self):
        settings=apply_profile(Settings(),'iso178_preferred')
        changed=replace(settings,specimen_architecture='porous_centre')
        cached=build_preview(settings, include_mesh=True)
        rebuilt=build_preview(changed,cached,include_mesh=True)
        self.assertNotEqual(preview_key(settings),preview_key(changed))
        self.assertIsNot(cached.surface,rebuilt.surface)
        self.assertIsNot(cached.mesh,rebuilt.mesh)
        self.assertLess(rebuilt.mesh.grid.volume,cached.mesh.grid.volume)

    def test_unsupported_solver_and_illegal_profiles_are_rejected(self):
        from solver_api import KRATOS_CAPABILITIES
        settings=apply_profile(Settings(),'astm_e8_subsize')
        self.assertEqual(KRATOS_CAPABILITIES.validate(replace(settings,solver_backend='kratos')),[])
        self.assertTrue(validate_settings(replace(settings,specimen_architecture='porous_all')))
        self.assertTrue(validate_settings(replace(settings,core_fraction=float('nan'))))
        from flow_analysis import FlowSettings
        with self.assertRaisesRegex(ValueError,'mechanical fixtures'):FlowSettings().validate(settings)


class StandardResultTests(unittest.TestCase):
    def test_tension_sign_and_virtual_gauge(self):
        from nonlinear_compression import extract_curve, export_graphs
        settings=replace(apply_profile(Settings(),'astm_e8_subsize'),compression=.102)
        mesh=generate_volume_mesh(settings)
        result=MagicMock(settings=settings,nsets=1,time_values=np.array([1.]))
        result.top_platen_displacement.return_value=.102
        result.platen_reactions.return_value=(-180.,180.)
        displacement=np.zeros((mesh.grid.n_points,3));displacement[:,2]=mesh.grid.points[:,2]*.001
        result.displacement.return_value=displacement
        history,report=extract_curve(result)
        self.assertFalse(report['force_balance_warning'])
        self.assertAlmostEqual(history[-1,1],.102)
        with TemporaryDirectory() as folder:
            graphs=export_graphs(history,settings,Path(folder),mesh,False,result)
            self.assertAlmostEqual(graphs['final_measured_deformation_mm'],.025)
            self.assertAlmostEqual(graphs['maximum_nominal_stress_MPa'],10.)
            self.assertTrue(graphs['completed'])

    def test_cylindrical_fixture_commands_and_positive_tension_force(self):
        import mapdl_backend as backend
        settings=apply_profile(Settings(),'iso178_preferred')
        mesh=generate_volume_mesh(settings)
        mapdl=MagicMock()
        pilots=backend.create_platen_contacts(mapdl,settings,mesh)
        self.assertEqual(len(set(pilots)),2)
        mapdl.d.assert_any_call(pilots[1],'UZ',-settings.compression)
        mapdl.run.assert_any_call('TSHAPE,CYLI')
        mapdl.rmodif.assert_any_call(4,1,5.)
        mapdl.rmodif.assert_any_call(5,1,5.)
        mapdl.keyopt.assert_any_call(5,4,2)
        mapdl.keyopt.assert_any_call(8,4,2)
        settings=replace(apply_profile(Settings(),'astm_e8_subsize'),loading_mode='force',applied_force=77.)
        with patch.object(backend,'create_platen',side_effect=[100,101]):
            backend.create_platen_contacts(mapdl,settings,mesh)
        mapdl.f.assert_any_call(101,'FZ',77.)


class StandardUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def test_select_lock_restore_and_enter(self):
        from nonlinear_compression import RustMechDialog
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        with patch('window_state.install_window_state'),patch.object(RustMechDialog,'start_preview',lambda self:None):
            dialog=RustMechDialog(Settings())
            try:
                dialog.test_profile_combo.setCurrentIndex(dialog.test_profile_combo.findData('astm_e8_subsize'))
                self.assertEqual(dialog.collect().size,102)
                self.assertEqual(dialog.controls['size'].value(),102)
                self.assertFalse(dialog.controls['size'].isEnabled())
                self.assertFalse(dialog.family_combo.isEnabled())
                dialog.architecture_combo.setCurrentIndex(dialog.architecture_combo.findData('porous_centre'))
                self.assertTrue(dialog.family_combo.isEnabled())
                self.assertEqual(dialog.collect().specimen_architecture,'porous_centre')
                self.assertEqual(dialog.collect().bottom_platen_contact,'bonded')
                QTest.keyClick(dialog.controls['compression'],Qt.Key.Key_Return)
                self.assertIsNone(dialog.analysis_job)
                self.assertIsNone(dialog.sync_window)
                dialog.test_profile_combo.setCurrentIndex(0)
                self.assertEqual(dialog.collect().test_profile,'custom')
                self.assertEqual(dialog.collect().size,6)
                self.assertTrue(dialog.controls['size'].isEnabled())
            finally:dialog.dispose();dialog.deleteLater()


if __name__=='__main__':unittest.main()
