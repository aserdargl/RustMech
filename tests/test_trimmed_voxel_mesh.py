import unittest

import numpy as np
import pyvista as pv

from benchmark_trimmed_voxels import make_cases
from periodic_tetra import face_components
from trimmed_voxel_mesh import build_trimmed_tet4


class TrimmedVoxelMeshTests(unittest.TestCase):
    def assert_conforming_tets(self, grid):
        self.assertTrue(np.all(grid.celltypes == pv.CellType.TETRA))
        self.assertEqual(len(np.unique(face_components(grid))), 1)
        skin = grid.extract_surface(algorithm="dataset_surface")
        self.assertEqual(skin.n_open_edges, 0)
        quality = grid.cell_quality("scaled_jacobian").cell_data["scaled_jacobian"]
        self.assertTrue(np.all(quality > 0))

    def test_exact_grid_intersections_do_not_leave_degenerate_cells(self):
        for field, volume in (
            (lambda points: points[:, 2] - 0.5, 0.5),
            (lambda points: points[:, 0] + points[:, 1] - 0.75, 0.28125),
        ):
            with self.subTest(volume=volume):
                grid = build_trimmed_tet4(field, 8)
                self.assert_conforming_tets(grid)
                self.assertAlmostEqual(grid.volume, volume, places=13)

    def test_plane_volume_is_exact_at_all_benchmark_levels(self):
        plane, _ = make_cases()
        for level in (8, 16, 32):
            with self.subTest(level=level):
                grid = build_trimmed_tet4(plane.field, level)
                self.assert_conforming_tets(grid)
                self.assertAlmostEqual(grid.volume, plane.volume, places=13)

    def test_sphere_volume_error_decreases_on_refinement(self):
        _, sphere = make_cases()
        errors = []
        for level in (8, 16, 32):
            with self.subTest(level=level):
                grid = build_trimmed_tet4(sphere.field, level)
                self.assert_conforming_tets(grid)
                errors.append(abs(grid.volume - sphere.volume))
        self.assertGreater(errors[0], errors[1])
        self.assertGreater(errors[1], errors[2])

    def test_no_sampled_interior_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "No solid interior"):
            build_trimmed_tet4(lambda points: np.ones(len(points)), 4)


if __name__ == "__main__":
    unittest.main()
