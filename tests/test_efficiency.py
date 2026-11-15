import subprocess
import sys
import unittest
import ctypes
from multiprocessing.shared_memory import SharedMemory
from dataclasses import replace
from unittest.mock import PropertyMock, patch

import numpy as np
import pyvista as pv

import mesh_engine
from benchmark_efficiency import reference_surface_cleanup
from benchmark_mesh import reference_grid
from model import Settings
from rust_mesh import build_hex_grid, largest_triangle_surface
from structure_library import LATTICE_ITEMS, TPMS_ITEMS
from mesh_preview_worker import PreviewWorker
from preview_transport import pack_preview, unpack_preview, write_arrays
from threading import Event


class EfficiencyTests(unittest.TestCase):
    def test_default_surface_uses_fast_path_with_identical_geometry(self):
        settings = Settings()
        with patch.object(
            mesh_engine, "surface_from_marching_cubes", reference_surface_cleanup
        ):
            expected = mesh_engine.make_surface(settings)
        with patch.object(
            mesh_engine,
            "_vtk_surface_cleanup",
            side_effect=AssertionError("unexpected fallback"),
        ):
            actual = mesh_engine.make_surface(settings)
        np.testing.assert_array_equal(
            actual.points[actual.faces.reshape(-1, 4)[:, 1:]],
            expected.points[expected.faces.reshape(-1, 4)[:, 1:]],
        )
        np.testing.assert_array_equal(actual.cell_normals, expected.cell_normals)
        self.assertEqual(actual.n_open_edges, 0)
        self.assertAlmostEqual(actual.volume, expected.volume, places=10)

    @patch.object(Settings, "surface_resolution", new_callable=PropertyMock, return_value=0.3)
    def test_surface_geometry_winding_and_normals_match_reference(self, _spacing):
        cases = [
            ("tpms", name, morphology)
            for name in TPMS_ITEMS
            for morphology in ("sheet", "network_a", "network_b")
        ]
        cases += [("lattice", name, "sheet") for name in LATTICE_ITEMS]
        cases += [
            ("porous", "Gaussian Random Field", "sheet"),
            ("porous", "Voronoi Cell", "network_a"),
        ]
        for family, name, morphology in cases:
            settings = replace(
                Settings(),
                structure_family=family,
                structure_name=name,
                tpms_morphology=morphology,
            )
            with self.subTest(family=family, name=name, morphology=morphology):
                with patch.object(
                    mesh_engine,
                    "surface_from_marching_cubes",
                    reference_surface_cleanup,
                ):
                    expected = mesh_engine.make_surface(settings)
                actual = mesh_engine.make_surface(settings)
                actual_cells = actual.points[actual.faces.reshape(-1, 4)[:, 1:]]
                expected_cells = expected.points[expected.faces.reshape(-1, 4)[:, 1:]]
                np.testing.assert_array_equal(actual_cells, expected_cells)
                np.testing.assert_array_equal(
                    actual.cell_normals, expected.cell_normals
                )
                # Normal association remains equal after remapping point IDs.
                np.testing.assert_array_equal(
                    actual.point_normals[actual.faces.reshape(-1, 4)[:, 1:]],
                    expected.point_normals[expected.faces.reshape(-1, 4)[:, 1:]],
                )
                self.assertEqual(actual.n_open_edges, expected.n_open_edges)
                self.assertEqual(actual.is_manifold, expected.is_manifold)
                self.assertAlmostEqual(actual.volume, expected.volume, places=10)

    def test_surface_merging_and_component_ties(self):
        first = pv.Box().triangulate()
        second = first.copy()
        second.translate((5, 0, 0), inplace=True)
        combined = first + second
        points = np.asarray(combined.points)
        faces = combined.faces.reshape(-1, 4)[:, 1:]
        expected = reference_surface_cleanup(points, faces)
        actual = largest_triangle_surface(points, faces)
        np.testing.assert_array_equal(
            actual.points[actual.faces.reshape(-1, 4)[:, 1:]],
            expected.points[expected.faces.reshape(-1, 4)[:, 1:]],
        )
        duplicate_points = np.vstack((points, points))
        duplicate_faces = faces.copy()
        duplicate_faces[::2] += len(points)
        merged = largest_triangle_surface(duplicate_points, duplicate_faces)
        np.testing.assert_array_equal(
            merged.points[merged.faces.reshape(-1, 4)[:, 1:]],
            actual.points[actual.faces.reshape(-1, 4)[:, 1:]],
        )

    def test_compact_hex_grid_and_contact_ids_match_dense_reference(self):
        rng = np.random.default_rng(3811)
        for shape in [(1, 1, 1), (3, 7, 5), (8, 9, 10), (1, 150, 1)]:
            for quadratic in (False, True):
                solid = rng.random(shape) < 0.6
                for spacing in (0.0137, 2.357):
                    with self.subTest(
                        shape=shape, quadratic=quadratic, spacing=spacing
                    ):
                        grid, bottom, top = build_hex_grid(
                            solid, spacing, quadratic=quadratic, return_boundaries=True
                        )
                        expected = reference_grid(solid, spacing, quadratic=quadratic)
                        np.testing.assert_array_equal(grid.points, expected.points)
                        np.testing.assert_array_equal(grid.cells, expected.cells)
                        if grid.n_cells:
                            ids = expected.point_data["ansys_node_num"]
                            z = np.asarray(expected.points)[:, 2]
                            np.testing.assert_array_equal(
                                bottom,
                                ids[np.isclose(z, 0, rtol=0, atol=spacing * 1e-8)],
                            )
                            np.testing.assert_array_equal(
                                top,
                                ids[
                                    np.isclose(
                                        z,
                                        shape[2] * spacing,
                                        rtol=0,
                                        atol=spacing * 1e-8,
                                    )
                                ],
                            )
        empty, bottom, top = build_hex_grid(
            np.zeros((3, 4, 5), bool), 0.1, return_boundaries=True
        )
        self.assertEqual((empty.n_cells, len(bottom), len(top)), (0, 0, 0))

    def test_worker_computation_does_not_import_gui_or_plotting(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, mesh_engine; assert 'PySide6' not in sys.modules; assert 'matplotlib.pyplot' not in sys.modules; assert 'nonlinear_compression' not in sys.modules",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_shared_preview_transfer_is_detached_and_preserves_attributes(self):
        settings = replace(
            Settings(),
            size=1.0,
            cell_size=0.5,
            mesh_resolution=0.1,
        )
        mesh = mesh_engine.generate_volume_mesh(settings)
        for surface in [pv.Box().triangulate().compute_normals(), pv.Line()]:
            surface.point_data["vectors"] = np.ones((surface.n_points, 3))
            surface.point_data.active_vectors_name = "vectors"
            surface.field_data["label"] = ["RustMech"]
            payload, arrays, size = pack_preview(("test",), surface, mesh)
            segment = SharedMemory(create=True, size=max(size, 1))
            try:
                low = ctypes.addressof(ctypes.c_char.from_buffer(segment.buf))
                write_arrays(segment, arrays)
                preview = unpack_preview(payload, segment, None)
                self.assertFalse(low <= preview.surface.points.ctypes.data < low + size)
                self.assertFalse(
                    low <= preview.mesh.grid.points.ctypes.data < low + size
                )
            finally:
                segment.close()
                segment.unlink()
            np.testing.assert_array_equal(preview.surface.points, surface.points)
            np.testing.assert_array_equal(preview.surface.faces, surface.faces)
            np.testing.assert_array_equal(preview.surface.lines, surface.lines)
            self.assertEqual(preview.surface.point_data.active_vectors_name, "vectors")
            np.testing.assert_array_equal(
                preview.surface.field_data["label"], ["RustMech"]
            )
            np.testing.assert_array_equal(preview.mesh.grid.cells, mesh.grid.cells)
            np.testing.assert_array_equal(preview.mesh.bottom_ids, mesh.bottom_ids)
            if surface.GetPointData().GetNormals() is not None:
                np.testing.assert_array_equal(
                    preview.surface.point_normals, surface.point_normals
                )
            clone = preview.mesh.grid.copy(deep=False)
            del preview
            np.testing.assert_array_equal(clone.points, mesh.grid.points)

    def test_failed_shared_transfer_cleans_up_and_worker_restarts(self):
        settings = replace(
            Settings(),
            size=1.0,
            cell_size=0.5,
            mesh_resolution=0.1,
        )
        worker = PreviewWorker()
        names = []

        def fail_decode(payload, segment, cached):
            names.append(segment.name)
            raise ValueError("transport test failure")

        try:
            with patch("preview_transport.unpack_preview", fail_decode):
                with self.assertRaisesRegex(ValueError, "transport test failure"):
                    worker.build(settings, None, None, Event())
            self.assertIsNone(worker._segment)
            self.assertIsNone(worker._process)
            self.assertEqual(len(names), 1)
            with self.assertRaises(FileNotFoundError):
                SharedMemory(name=names[0])
            preview = worker.build(settings, None, None, Event())
            self.assertGreater(preview.mesh.grid.n_cells, 0)
        finally:
            worker.close()


if __name__ == "__main__":
    unittest.main()
