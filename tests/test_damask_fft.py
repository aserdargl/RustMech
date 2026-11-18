import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

import numpy as np

from damask_fft import DamaskAnalysisProcess, RESULT_FILE, _solve_load, run_periodic_fft, solve_cell
from model import Settings


@unittest.skipUnless(os.environ.get("RUSTMECH_TEST_DAMASK"),
                     "Set RUSTMECH_TEST_DAMASK to run the external DAMASK solver.")
class DamaskFFTTests(unittest.TestCase):
    distribution = os.environ.get("RUSTMECH_TEST_DAMASK", "Ubuntu")

    def setUp(self):
        temp = TemporaryDirectory(prefix="rustmech-damask-test-")
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)

    def test_homogeneous_and_layered_cells_match_the_analytic_matrices(self):
        young, poisson, ratio = 1000, 0.3, 0.1
        mu = young / (2 * (1 + poisson))
        lam = 2 * mu * poisson / (1 - 2 * poisson)
        homogeneous = np.diag([2 * mu] * 3 + [mu] * 3)
        homogeneous[:3, :3] += lam
        # For a laminate normal to x, normal traction is continuous. The
        # corresponding block uses the harmonic phase stiffness, while
        # the in-plane block also includes the normal-relaxation correction.
        arithmetic, harmonic = (1 + ratio) / 2, 2 * ratio / (1 + ratio)
        layered = homogeneous * arithmetic
        normal = [0, 4, 5]
        layered[normal, :] = homogeneous[normal, :] * harmonic
        layered[:, normal] = homogeneous[:, normal] * harmonic
        layered[np.ix_([1, 2], [1, 2])] -= lam**2 / (lam + 2 * mu) * (arithmetic - harmonic)
        for name, mask, expected in (
            ("solid", np.ones((8,) * 3, dtype=bool), homogeneous),
            ("laminate", np.indices((8,) * 3)[0] < 4, layered),
        ):
            with self.subTest(cell=name):
                directory = self.folder / name
                directory.mkdir()
                result = solve_cell(mask, 3, young, poisson, ratio, self.distribution, directory)
                np.testing.assert_allclose(result["stiffness_mpa"], expected, rtol=3e-5, atol=0.003)
                self.assertEqual(len(result["load_cases"]), 12)
                self.assertTrue(all(case["converged"] for case in result["load_cases"].values()))
                self.assertTrue((directory / RESULT_FILE).is_file())

    def test_stopping_a_running_solver_does_not_publish_a_matrix(self):
        with patch("damask_fft.new_run_folder", return_value=self.folder):
            job = DamaskAnalysisProcess(Settings(), 32, 0.01, self.distribution)
        try:
            deadline = time.monotonic() + 45
            while not (self.folder / "xx_plus.log").exists() and not job.done():
                self.assertLess(time.monotonic(), deadline, "DAMASK did not start.")
                time.sleep(0.05)
            self.assertTrue((self.folder / "xx_plus.log").exists(), "DAMASK did not start.")
        finally:
            job.force_stop()
        self.assertFalse((self.folder / RESULT_FILE).exists())
        self.assertIn('"cancelled"', (self.folder / "status.json").read_text())

    def test_nonconverged_load_does_not_publish_a_matrix(self):
        def limit_iterations(folder, distribution, case):
            path = folder / "numerics.yaml"
            path.write_text(
                "solver:\n  grid:\n    N_cutback_max: 0\n"
                "    mechanical:\n      N_iter_max: 1\n",
                encoding="utf-8",
            )
            return _solve_load(folder, distribution, case)

        with patch("damask_fft._solve_load", side_effect=limit_iterations):
            with self.assertRaisesRegex(RuntimeError, "xx_plus"):
                run_periodic_fft(Settings(), 16, 0.01, self.distribution, self.folder)
        self.assertFalse((self.folder / RESULT_FILE).exists())
        self.assertIn('"failed"', (self.folder / "status.json").read_text())


if __name__ == "__main__":
    unittest.main()
