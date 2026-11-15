"""Cold native-kernel generation, fallback, progress and failure recovery."""

from concurrent.futures import CancelledError
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np
import pyvista as pv

from model import Settings
import netgen_kernel
import tetrahedral_mesh as tetra


@unittest.skipUnless(
    netgen_kernel.kernel_path(), "Optimized Netgen core has not been built"
)
class NetgenKernelTests(unittest.TestCase):
    def setUp(self):
        self.settings = replace(
            Settings(),
            size=1.0,
            cell_size=0.5,
            mesh_type="TET4",
            mesh_resolution=0.5,
            local_mesh_size=0.3,
        )
        self.surface = pv.Box(bounds=(0, 1, 0, 1, 0, 1)).triangulate()
        self.boundary = tetra._clean_boundary(self.surface, 1.0, 1e-6)
        self.samples = (
            tetra._platen_samples(self.boundary, self.settings),
            tetra._thin_wall_samples(self.boundary, 0.3, 0.1),
        )
        tetra.clear_tetrahedral_cache()

    def tearDown(self):
        tetra.clear_tetrahedral_cache()

    def test_cold_core_matches_stock_nodes_cells_volume_and_contacts(self):
        stages = []
        optimized = tetra.generate_tetrahedral_mesh(
            self.settings, self.surface, use_cache=False, on_progress=stages.append
        )
        with patch.object(tetra, "kernel_path", return_value=None):
            stock = tetra.generate_tetrahedral_mesh(
                self.settings, self.surface, use_cache=False
            )
        np.testing.assert_array_equal(optimized.grid.points, stock.grid.points)
        np.testing.assert_array_equal(
            optimized.grid.cell_connectivity, stock.grid.cell_connectivity
        )
        np.testing.assert_array_equal(optimized.bottom_ids, stock.bottom_ids)
        np.testing.assert_array_equal(optimized.top_ids, stock.top_ids)
        self.assertEqual(optimized.fraction, stock.fraction)
        self.assertEqual(optimized.solve_errors, [])
        self.assertIn("Loading the boundary into Netgen…", stages)
        self.assertEqual([s for s in stages if s in netgen_kernel._STAGES.values()],
                         list(netgen_kernel._STAGES.values()))

    def test_callback_cancellation_releases_state_before_the_next_generation(self):
        for stage in (2, 3, 4, 5, 6):

            def cancel(message):
                if message == netgen_kernel._STAGES[stage]:
                    raise CancelledError("cancel this Netgen stage")

            with self.assertRaises(CancelledError):
                netgen_kernel.generate_arrays(
                    self.boundary,
                    self.settings,
                    self.samples,
                    library_path=netgen_kernel.kernel_path(),
                    on_progress=cancel,
                )
        mesh = tetra.generate_tetrahedral_mesh(
            self.settings, self.surface, use_cache=False
        )
        self.assertEqual(mesh.solve_errors, [])
        self.assertAlmostEqual(mesh.fraction, 100.0, places=8)

    def test_invalid_refinement_is_rejected_without_corrupting_the_next_mesh(self):
        with self.assertRaisesRegex(ValueError, "Non-finite refinement"):
            netgen_kernel.generate_arrays(
                self.boundary,
                self.settings,
                ([np.nan, 0, 0],),
                library_path=netgen_kernel.kernel_path(),
            )
        mesh = tetra.generate_tetrahedral_mesh(
            self.settings, self.surface, use_cache=False
        )
        self.assertEqual(mesh.solve_errors, [])


if __name__ == "__main__":
    unittest.main()
