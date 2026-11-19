import unittest

import numpy as np
import pyvista as pv

from benchmark_trimmed_voxels import make_cases, make_gyroid_case, surface_errors, thin_wall_probes
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

    def test_sphere_snapping_improves_jacobian_and_limits_surface_error(self):
        _, sphere = make_cases()
        baseline = build_trimmed_tet4(sphere.field, 16, snap_boundary=False)
        snapped = build_trimmed_tet4(sphere.field, 16)
        self.assert_conforming_tets(snapped)
        old_quality = baseline.cell_quality("scaled_jacobian").cell_data["scaled_jacobian"]
        new_quality = snapped.cell_quality("scaled_jacobian").cell_data["scaled_jacobian"]
        self.assertGreater(new_quality.min(), old_quality.min())
        old_error = max(surface_errors(baseline.extract_surface(algorithm="dataset_surface"), sphere))
        new_error = max(surface_errors(snapped.extract_surface(algorithm="dataset_surface"), sphere))
        self.assertLess(new_error, 1.2 * old_error)

    def test_gyroid_preserves_thin_wall_probes_and_rejects_topology_changes(self):
        gyroid = make_gyroid_case()
        probes = thin_wall_probes(gyroid.field).reshape(-1, 3)
        self.assertGreater(len(probes), 0)
        for level in (16, 32):
            with self.subTest(level=level):
                grid = build_trimmed_tet4(gyroid.field, level)
                self.assertTrue(np.all(grid.find_containing_cell(probes) >= 0))
                self.assertEqual(grid.extract_surface(algorithm="dataset_surface").n_open_edges, 0)
                self.assertEqual(np.unique(face_components(grid)).size, 3)
                # At n=16 the snapping candidates change the sheet topology.
                self.assertEqual("snap_fraction" in grid.field_data, level == 32)


if __name__ == "__main__":
    unittest.main()
