"""Analytical solids and adversarial triangle contacts for geometric preflight."""
import unittest

import numpy as np
import pyvista as pv

from surface_audit import intersection_report, normal_chords, audit_surface


def mesh(points, faces):
    return pv.PolyData(np.asarray(points,dtype=np.float64),np.column_stack((np.full(len(faces),3),faces)).ravel())


class SurfaceAuditTests(unittest.TestCase):
    def test_closed_manifold_box_and_sphere_have_only_legal_adjacencies(self):
        for surface in (pv.Box().triangulate(),pv.Sphere(theta_resolution=12,phi_resolution=12)):
            with self.subTest(cells=surface.n_cells):
                report = intersection_report(surface)
                self.assertEqual(report["status"],"clear")
                self.assertTrue(report["complete"])
                self.assertEqual(report["intersecting_pairs"],0)

    def test_overlapping_closed_components_are_detected(self):
        surface = pv.Box().triangulate()+pv.Box(bounds=(0,2,0,2,0,2)).triangulate()
        self.assertEqual(surface.n_open_edges,0)
        report, annotated = audit_surface(surface,.1)
        self.assertEqual(report["intersection"]["status"],"invalid")
        self.assertGreater(report["intersection"]["intersecting_pairs"],0)
        self.assertTrue(annotated.cell_data["SurfaceIssue"].any())
        self.assertIsNone(report["chords"])

    def test_budget_exhaustion_is_never_reported_clear(self):
        report = intersection_report(pv.Box().triangulate(),max_pairs=0)
        self.assertEqual(report["status"],"incomplete")
        self.assertFalse(report["complete"])

    def test_shared_edge_is_allowed_but_coplanar_overlap_is_rejected(self):
        points = [[0,0,0],[2,0,0],[0,2,0],[0,-2,0],[1,1,0]]
        allowed = mesh(points,[[0,1,2],[1,0,3]])
        bad = mesh(points,[[0,1,2],[0,1,4]])
        self.assertEqual(intersection_report(allowed)["status"],"clear")
        self.assertEqual(intersection_report(bad)["status"],"invalid")

    def test_shared_vertex_does_not_hide_crossing_triangle_interiors(self):
        points = [[0,0,0],[2,0,0],[0,2,0],[1,-1,0],[1,1,0],[-1,0,0],[0,-1,0]]
        self.assertEqual(intersection_report(mesh(points,[[0,1,2],[0,3,4]]))["status"],"invalid")
        self.assertEqual(intersection_report(mesh(points,[[0,1,2],[0,5,6]]))["status"],"clear")
        spatial = mesh([[0,0,0],[2,0,0],[0,2,0],[1,1,-1],[1,1,1]],[[0,1,2],[0,3,4]])
        self.assertEqual(intersection_report(spatial)["status"],"invalid")

    def test_duplicate_and_degenerate_triangles_are_invalid(self):
        duplicate = mesh([[0,0,0],[1,0,0],[0,1,0]],[[0,1,2],[0,2,1]])
        self.assertEqual(intersection_report(duplicate)["status"],"invalid")
        degenerate = mesh([[0,0,0],[1,0,0],[2,0,0]],[[0,1,2]])
        self.assertEqual(intersection_report(degenerate)["degenerate_triangles"],1)

    def test_nearly_coplanar_neighbours_meeting_only_at_vertex_are_not_crossings(self):
        points = [[1.7531366,1.6999999,.1],[1.8,1.728963,.1],[1.8,1.6999999,.0531367],
                  [1.6999999,1.6999999,.1531367],[1.6999999,1.728963,.20000002]]
        self.assertEqual(intersection_report(mesh(points,[[0,1,2],[3,4,1]]))["status"],"clear")

    def test_wall_measurement_matches_analytical_slab_and_refinement_ratio(self):
        slab = pv.Box(bounds=(0,2,0,3,0,.2)).triangulate()
        coarse,_ = normal_chords(slab,.1)
        fine,_ = normal_chords(slab,.025)
        self.assertAlmostEqual(coarse["wall_minimum_sampled_mm"],.2,places=6)
        self.assertAlmostEqual(coarse["wall_minimum_cells"],2,places=6)
        self.assertAlmostEqual(fine["wall_minimum_cells"],8,places=6)
        self.assertIsNone(coarse["pore_minimum_sampled_mm"])
        self.assertEqual(coarse["sampled_faces"],12)

    def test_outward_pore_chords_measure_gap_between_disjoint_solids(self):
        a = pv.Box(bounds=(0,1,0,2,0,2)).triangulate()
        b = pv.Box(bounds=(1.3,2.3,0,2,0,2)).triangulate()
        report,_ = normal_chords(a+b,.1)
        self.assertAlmostEqual(report["pore_minimum_sampled_mm"],.3,places=6)

    def test_scale_translation_and_sampling_coverage_are_explicit(self):
        surface = pv.Box(bounds=(0,2,0,3,0,.2)).triangulate()
        surface.points = surface.points.astype(np.float64)*10+np.array([1e6,-2e6,3e6])
        report = intersection_report(surface)
        self.assertEqual(report["status"],"clear")
        chords,_ = normal_chords(surface,1,max_samples=4)
        self.assertEqual(chords["sampled_faces"],4)
        self.assertLess(chords["sample_fraction"],1)
        self.assertGreater(chords["wall_minimum_sampled_mm"],0)

    def test_open_surfaces_are_not_valid_solid_chord_measurements(self):
        plane = pv.Plane().triangulate()
        report,_ = audit_surface(plane,.1)
        self.assertEqual(report["intersection"]["status"],"invalid")
        self.assertIsNone(report["chords"])


if __name__ == "__main__": unittest.main()
