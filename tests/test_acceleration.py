import tempfile
import unittest
from concurrent.futures import CancelledError
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pyvista as pv

import structure_library as library
from benchmark_acceleration import reference_apdl, reference_map, reference_mises
from mapdl_backend import write_apdl_mesh
from mesh_preview_worker import PreviewWorker
from model import Settings
from rust_mesh import tet_metrics, von_mises
from solver_api import map_node_values


class AccelerationTests(unittest.TestCase):
    def test_all_lattice_fields_match_reference(self):
        axis = np.linspace(-0.25, 6.25, 15)
        coords = axis[:, None, None], axis[None, :, None], axis[None, None, :]
        for name in library.LATTICE_ITEMS:
            settings = replace(
                Settings(), structure_family="lattice", structure_name=name
            )
            with patch.object(library, "graph_field", return_value=None):
                expected = library.evaluate_priority_field(settings, *coords)
            with self.subTest(name=name):
                np.testing.assert_array_equal(
                    library.evaluate_priority_field(settings, *coords), expected
                )

    def test_porous_interpolation_matches_scipy(self):
        axis = np.linspace(-0.1, 6.1, 17)
        coords = axis[:, None, None], axis[None, :, None], axis[None, None, :]
        for name in [
            "Gaussian Random Field",
            "Stochastic Foam",
            "Stochastic Reconstruction",
        ]:
            for morphology in ["sheet", "network_a", "network_b"]:
                settings = replace(
                    Settings(),
                    structure_family="porous",
                    structure_name=name,
                    tpms_morphology=morphology,
                )
                with patch.object(library, "interpolate_field", return_value=None):
                    expected = library.evaluate_priority_field(settings, *coords)
                with self.subTest(name=name, morphology=morphology):
                    np.testing.assert_array_equal(
                        library.evaluate_priority_field(settings, *coords), expected
                    )

    def test_result_mapping_preserves_dtypes_and_errors(self):
        rng = np.random.default_rng(76)
        for source in [np.arange(100), np.arange(100) * 3, rng.permutation(100)]:
            for values in [
                rng.standard_normal((100, 6)),
                np.arange(200).reshape(100, 2),
            ]:
                target = source[rng.integers(0, 100, 250)]
                np.testing.assert_array_equal(
                    map_node_values(source, values, target),
                    reference_map(source, values, target),
                )
        for source, values, targets in [
            ([1, 1], [0, 1], []),
            ([1, 2], [0, 1], [3]),
            ([1], [np.nan], [1]),
            ([1, 2], [0], [1]),
        ]:
            with self.assertRaises(ValueError):
                map_node_values(source, values, targets)
        self.assertEqual(map_node_values([], np.empty((0, 3)), []).shape, (0, 3))

    def test_apdl_formats_match_python_for_all_elements_and_float_exponents(self):
        rng = np.random.default_rng(758)
        points = rng.uniform(-1.0, 1.0, (5000, 3)) * 10.0 ** rng.integers(
            -300, 300, (5000, 3)
        )
        points[:4] = [
            [0, -0.0, 1e16],
            [1e-5, 1e-4, 1e15],
            [1.2345678901234567, -1e-20, 1e-300],
            [1, 2, 3],
        ]
        with tempfile.TemporaryDirectory() as folder:
            expected, actual = (
                Path(folder) / "ref.inp",
                Path(folder) / "Türkçe RustMech.inp",
            )
            for cell_type, width in [
                (pv.CellType.HEXAHEDRON, 8),
                (pv.CellType.QUADRATIC_HEXAHEDRON, 20),
                (pv.CellType.TETRA, 4),
                (pv.CellType.QUADRATIC_TETRA, 10),
            ]:
                grid = pv.UnstructuredGrid(
                    np.r_[width, np.arange(width)],
                    np.array([cell_type], np.uint8),
                    points,
                )
                grid.point_data["ansys_node_num"] = np.arange(1, len(points) + 1)
                grid.cell_data["ansys_elem_num"] = [12]
                mesh = SimpleNamespace(grid=grid)
                reference_apdl(mesh, expected)
                write_apdl_mesh(mesh, actual)
                self.assertEqual(actual.read_bytes(), expected.read_bytes())

    def test_tet_metrics_match_numpy_and_detect_inversion(self):
        points = np.array(
            [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        )
        for order, determinant in [([0, 1, 2, 3], 1), ([1, 0, 2, 3], -1)]:
            grid = pv.UnstructuredGrid(np.r_[4, order], [pv.CellType.TETRA], points)
            volume, ratio = tet_metrics(grid)
            self.assertEqual(volume[0], determinant)
            self.assertAlmostEqual(ratio[0], np.sqrt(2))

    def test_von_mises_matches_numpy(self):
        tensors = np.random.default_rng(24).standard_normal((200_000, 6))
        np.testing.assert_array_equal(von_mises(tensors), reference_mises(tensors))


class PersistentPreviewTests(unittest.TestCase):
    def test_cache_reuse_cancel_and_restart(self):
        worker = PreviewWorker()
        settings = replace(
            Settings(),
            size=1.0,
            cell_size=0.5,
            mesh_resolution=0.1,
        )
        cancelled = Event()
        try:
            worker.warmup()
            prewarmed_pid = worker._process.pid
            first = worker.build(settings, None, None, cancelled, include_mesh=True)
            pid = worker._process.pid
            self.assertEqual(pid, prewarmed_pid)
            second = worker.build(
                replace(settings, mesh_resolution=0.05), first, None, cancelled, include_mesh=True
            )
            self.assertEqual(worker._process.pid, pid)
            self.assertIs(second.surface, first.surface)
            self.assertIsNot(second.mesh, first.mesh)
            third = worker.build(
                replace(settings, mesh_resolution=0.05), second, None, cancelled, include_mesh=True
            )
            self.assertIs(third.mesh, second.mesh)
            self.assertIs(third.surface, second.surface)
            with self.assertRaises(CancelledError):
                worker.build(
                    replace(settings, cell_size=0.6),
                    third,
                    lambda _: cancelled.set(),
                    cancelled,
                )
            self.assertIsNone(worker._process)
            cancelled.clear()
            restarted = worker.build(settings, None, None, cancelled, include_mesh=True)
            self.assertEqual(restarted.key, first.key)
            self.assertNotEqual(worker._process.pid, pid)
        finally:
            worker.close()


if __name__ == "__main__":
    unittest.main()
