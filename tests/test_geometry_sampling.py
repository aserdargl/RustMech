"""Automatic geometry fidelity and independence from FE discretization."""

from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, PropertyMock, patch

import numpy as np

import mesh_engine
from model import Settings, load_settings, save_settings, settings_from_dict, validate_geometry
from structure_library import evaluate_priority_field


class GeometrySamplingTests(unittest.TestCase):
    def test_explicit_stl_resolution_is_saved_validated_and_used(self):
        settings = replace(Settings(size=1, cell_size=1), stl_resolution=.05)
        self.assertEqual(settings.surface_resolution, .05)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            save_settings(settings, path)
            self.assertEqual(load_settings(path), settings)
        for value in (0, -1, float('nan'), float('inf'), 'invalid', True):
            self.assertTrue(validate_geometry(replace(settings, stl_resolution=value), include_mesh=False))
        coarse = mesh_engine.make_surface(settings)
        fine = mesh_engine.make_surface(replace(settings, stl_resolution=.025))
        self.assertGreater(fine.n_cells, coarse.n_cells)

    def test_old_surface_spacing_is_ignored_and_not_saved(self):
        for legacy_spacing in (0.01, 2.0, "obsolete"):
            settings = settings_from_dict({"surface_resolution": legacy_spacing})
            self.assertEqual(settings, Settings())
            self.assertNotIn("surface_resolution", asdict(settings))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            save_settings(settings, path)
            self.assertEqual(load_settings(path), settings)
            self.assertNotIn("surface_resolution", path.read_text())

    def test_sampling_tracks_feature_scale_without_using_mesh_settings(self):
        settings = Settings()
        thin = replace(settings, solid_fraction=10)
        self.assertLess(thin.surface_resolution, settings.surface_resolution)
        self.assertEqual(
            replace(settings, solid_fraction=90).surface_resolution,
            thin.surface_resolution,
        )
        self.assertAlmostEqual(
            replace(settings, cell_size=settings.cell_size / 2).surface_resolution,
            settings.surface_resolution / 2,
        )
        for mesh_type in ("HEX8", "HEX20", "TET4", "TET10"):
            changed = replace(settings, mesh_type=mesh_type, mesh_resolution=0.5, local_mesh_size=0.1)
            self.assertEqual(changed.surface_resolution, settings.surface_resolution)
            self.assertEqual(mesh_engine.geometry_threshold(changed), mesh_engine.geometry_threshold(settings))

    def test_surface_is_identical_after_mesh_size_order_and_family_changes(self):
        settings = Settings(size=1, cell_size=1, mesh_resolution=0.2)
        expected = mesh_engine.make_surface(settings)
        for mesh_type, global_size, local_size in (
            ("HEX8", 0.1, 0.01),
            ("HEX20", 0.05, 0.02),
            ("TET4", 0.3, 0.15),
            ("TET10", 0.1, 0.05),
        ):
            changed = replace(settings, mesh_type=mesh_type, mesh_resolution=global_size, local_mesh_size=local_size)
            with self.subTest(mesh_type=mesh_type):
                actual = mesh_engine.make_surface(changed)
                np.testing.assert_array_equal(actual.points, expected.points)
                np.testing.assert_array_equal(actual.faces, expected.faces)

    def test_preview_reuses_geometry_but_rebuilds_a_coarser_mesh(self):
        settings = Settings(size=1, cell_size=1, mesh_resolution=0.05)
        first = mesh_engine.build_preview(settings, include_mesh=True)
        with patch.object(mesh_engine, "make_surface", side_effect=AssertionError("geometry was rebuilt")):
            coarse = mesh_engine.build_preview(replace(settings, mesh_resolution=0.125), first, include_mesh=True)
        self.assertIs(coarse.surface, first.surface)
        self.assertIsNot(coarse.mesh, first.mesh)
        self.assertLess(coarse.mesh.grid.n_cells, first.mesh.grid.n_cells)

    def test_hex_meshes_sample_one_fixed_boundary_including_coincident_centers(self):
        settings = Settings(size=1, cell_size=1, solid_fraction=37, mesh_resolution=0.2)
        masks = []
        for n, mesh_type in ((5, "HEX8"), (15, "HEX20")):
            current = replace(settings, mesh_resolution=1 / n, mesh_type=mesh_type)
            mesh = mesh_engine.generate_volume_mesh(current)
            axis = (np.arange(n) + 0.5) / n
            values = evaluate_priority_field(current, axis[:, None, None], axis[None, :, None], axis[None, None, :])
            expected = values < mesh_engine.geometry_threshold(settings)
            actual = np.zeros((n,) * 3, dtype=bool)
            for grid in (mesh.grid, mesh.removed_grid):
                if grid.n_cells:
                    indices = np.floor(grid.cell_centers().points * n).astype(int)
                    actual[tuple(indices.T)] = True
            np.testing.assert_array_equal(actual, expected)
            self.assertEqual(mesh.candidate_elements, np.count_nonzero(expected))
            masks.append(actual)
        np.testing.assert_array_equal(masks[0], masks[1][1::3, 1::3, 1::3])

    def test_thin_walls_pores_and_caps_match_a_twice_finer_reference(self):
        for family, name, morphology, fraction in (
            ("tpms", "Gyroid", "sheet", 10),
            ("tpms", "Lidinoid", "sheet", 10),
            ("tpms", "Gyroid", "network_a", 85),
            ("lattice", "BCC", "sheet", 10),
            ("porous", "Gaussian Random Field", "sheet", 30),
            ("porous", "Voronoi Cell", "sheet", 10),
        ):
            settings = Settings(size=1, cell_size=1, mesh_resolution=0.1, structure_family=family,
                                structure_name=name, tpms_morphology=morphology, solid_fraction=fraction)
            spacing = settings.surface_resolution
            with self.subTest(structure=name, morphology=morphology, fraction=fraction):
                actual = mesh_engine.make_surface(settings)
                with patch.object(Settings, "surface_resolution", new_callable=PropertyMock, return_value=spacing / 2):
                    reference = mesh_engine.make_surface(settings)
                self.assertTrue(actual.is_manifold)
                self.assertTrue(reference.is_manifold)
                self.assertEqual(actual.n_lines, 0)
                self.assertEqual(actual.n_verts, 0)
                self.assertLess(abs(actual.volume / reference.volume - 1), 0.015)
                self.assertTrue(np.all(actual.points >= 0))
                self.assertTrue(np.all(actual.points <= settings.size))
                self.assertTrue(np.any(actual.points[:, 2] == 0))
                self.assertTrue(np.any(actual.points[:, 2] == settings.size))

    def test_memory_preflight_never_silently_coarsens_features(self):
        settings = Settings(cell_size=0.1)
        self.assertEqual(validate_geometry(settings), [])
        with patch("mesh_resources.available_memory", return_value=1024):
            with self.assertRaisesRegex(MemoryError, "Reference surface sampling"):
                mesh_engine.make_surface(settings)
        self.assertEqual(validate_geometry(replace(settings, mesh_resolution=0.5)), [])
        for fraction in (0, 100, 101, float("nan")):
            self.assertTrue(validate_geometry(replace(Settings(), solid_fraction=fraction)))


class GeometryUITests(unittest.TestCase):
    def test_stl_resolution_auto_manual_and_enter(self):
        from PyQt6.QtWidgets import QApplication, QLabel, QWidget
        from nonlinear_compression import RustMechDialog

        app = QApplication.instance() or QApplication([])
        plotter = MagicMock()
        plotter.interactor = QWidget()
        # Keep the real forms/signals; only skip OpenGL and background meshing.
        # A plain function preserves PySide's QObject method introspection.
        with patch("nonlinear_compression.create_preview_viewport", return_value=plotter), patch.object(
            RustMechDialog, "start_preview", new=lambda self: None
        ):
            dialog = RustMechDialog(Settings())
            try:
                self.assertNotIn("surface_resolution", dialog.controls)
                self.assertFalse(any("surface spacing" in label.text().lower() for label in dialog.findChildren(QLabel)))
                spacing = dialog.collect().surface_resolution
                self.assertEqual(dialog.stl_resolution_input.text(), 'auto')
                self.assertIsNone(dialog.collect().stl_resolution)
                dialog.stl_resolution_input.setText('0.04')
                self.assertEqual(dialog.collect().surface_resolution, .04)
                from PyQt6.QtCore import Qt
                from PyQt6.QtTest import QTest, QSignalSpy
                clicks = QSignalSpy(dialog.stl_auto_button.clicked)
                for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    QTest.keyClick(dialog.stl_resolution_input, key)
                self.assertEqual(len(clicks), 0)
                self.assertEqual(dialog.collect().surface_resolution, .04)
                dialog.controls['cell_size'].setValue(2)
                expected = dialog.collect().automatic_surface_resolution
                dialog.stl_auto_button.click()
                self.assertAlmostEqual(float(dialog.stl_resolution_input.text()), expected)
                dialog.stl_resolution_input.clear()
                self.assertIsNone(dialog.collect().stl_resolution)
                dialog.controls['cell_size'].setValue(Settings().cell_size)
                dialog.controls["mesh_resolution"].setValue(0.4)
                dialog.controls["local_mesh_size"].setValue(0.03)
                self.assertEqual(dialog.collect().surface_resolution, spacing)
                app.processEvents()
            finally:
                dialog.dispose()
                dialog.close()


if __name__ == "__main__":
    unittest.main()
