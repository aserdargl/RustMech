from dataclasses import asdict
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
import pyvista as pv

from model import Settings
from peridynamic_analysis import CompressionOptions, _Compression, _pairs, validate_compression
from remote_operations import ANALYSIS_OPERATIONS, required_capability
from remote_worker import run_operation


class PeridynamicAnalysisTests(unittest.TestCase):
    def test_elastic_and_contact_forces_match_the_energy_gradient(self):
        points = np.array([[0.0, 0.0, 0.5], [0.2, 0.0, 1.5], [0.0, 0.13, 2.5]])
        left, right = _pairs(points, 1.5)
        length = np.linalg.norm(points[right] - points[left], axis=1)
        solver = _Compression(points, left, right, length, np.ones_like(length), 3.0, 3)
        moved = points.copy()
        moved[:, 2] = [0.47, 1.28, 2.29]
        x = moved.ravel()
        args = (2.74, left, right, length, solver.stiffness)
        _, gradient = solver._potential(x, *args)
        delta = 1e-6 * np.eye(len(x))
        difference = [(solver._potential(x + shift, *args)[0] - solver._potential(x - shift, *args)[0])
                      / 2e-6 for shift in delta]
        np.testing.assert_allclose(gradient, difference, atol=1e-9)
        np.testing.assert_allclose(gradient.reshape(-1, 3).sum(axis=0)[:2], 0, atol=1e-12)

    def test_gyroid_compression_runs_through_worker_and_exports_irreversible_damage(self):
        settings = Settings(young_modulus=2000)
        options = CompressionOptions(increments=5, compression_percent=3.75)
        request = dict(operation="peridynamic_solve", settings=asdict(settings), options=asdict(options))
        self.assertIn(request["operation"], ANALYSIS_OPERATIONS)
        self.assertEqual(required_capability(request), "peridynamics")
        with TemporaryDirectory() as tmp, patch("remote_worker.probe", return_value={"peridynamics": True}), \
                patch("remote_worker._stage") as progress:
            result = run_operation(request, Path(tmp))
            folder = Path(result["run_folder"])
            report = json.loads((folder / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(report["state"], "complete")
            self.assertEqual(report["options"], asdict(options))
            self.assertGreater(report["first_bond_break"]["force_n"], 0)
            rows = report["frames"]
            self.assertEqual(len(rows), options.increments + 1)
            self.assertEqual(rows[0]["broken_bonds"], 0)
            self.assertGreater(rows[-1]["broken_bonds"], 0)
            self.assertTrue(all(a["broken_bonds"] <= b["broken_bonds"] for a, b in zip(rows, rows[1:])))
            self.assertTrue(all(abs(row["top_force_n"] - row["bottom_force_n"]) < 0.02 * row["top_force_n"]
                                for row in rows[1:]))
            frame = pv.read(folder / "frames" / "step_005.vtp")
            self.assertEqual(frame.n_points, report["particles"])
            self.assertGreater(frame["Damage"].max(), 0)
            with (folder / "layer_damage.csv").open(encoding="utf-8") as stream:
                layers = list(csv.DictReader(stream))
            self.assertEqual({row["layer"] for row in layers}, {"1", "2"})
            self.assertEqual(len(layers), (options.increments + 1) * 2)
            self.assertTrue((folder / "force_displacement.csv").is_file())
            self.assertTrue(any("Solving compression" in args[0] for args, _ in progress.call_args_list))

    def test_rejects_invalid_discretization_and_non_tpms_geometry(self):
        self.assertTrue(validate_compression(Settings(), CompressionOptions(resolution=12)))
        self.assertTrue(validate_compression(Settings(structure_family="lattice"), CompressionOptions()))
        self.assertTrue(validate_compression(Settings(), CompressionOptions(horizon_ratio=float("nan"))))
