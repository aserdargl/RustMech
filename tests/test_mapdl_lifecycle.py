"""Failures preserve solver evidence and cannot target an existing instance."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, call, patch

import numpy as np

import mapdl_backend as adapter
from model import Settings
from solver_api import AnalysisRequest


class MapdlLifecycleTests(unittest.TestCase):
    def run_backend(self, folder, session, reader=None):
        launcher = MagicMock(return_value=session)
        reader = reader or MagicMock()
        mesh = SimpleNamespace(solve_errors=[], grid=SimpleNamespace(
            n_points=8, n_cells=1, celltypes=np.array([12])))
        with patch.object(adapter, "write_apdl_mesh"), patch.object(
            adapter, "verify_imported_mesh"
        ), patch.object(adapter, "prepare_ansys", return_value=(9, 10)):
            execution = adapter.MapdlBackend(launcher, reader).solve(
                AnalysisRequest(Settings(), mesh, Path(folder)))
        return execution, launcher, reader

    def test_solve_failure_preserves_primary_error_and_live_output(self):
        with TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / "anstmp").write_text("original contact iteration output")
            session = MagicMock()
            session.solve.side_effect = RuntimeError("original SOLVE connection lost")
            session.finish.side_effect = RuntimeError("secondary FINISH failure")
            # Simulate shutdown replacing the temporary command output.
            session.exit.side_effect = lambda: (directory / "anstmp").write_text("exit output")
            execution, launcher, _ = self.run_backend(folder, session)
            self.assertEqual(execution.error, "original SOLVE connection lost")
            self.assertEqual(execution.diagnostics["solver_error_type"], "RuntimeError")
            self.assertEqual((directory / "solve_output.txt").read_text(),
                             "original contact iteration output")
            session.finish.assert_not_called()
            session.exit.assert_called_once()
            self.assertIs(launcher.call_args.kwargs["start_instance"], True)
            self.assertEqual(launcher.call_args.kwargs["run_location"], str(directory))

    def test_empty_result_does_not_invoke_binary_reader(self):
        with TemporaryDirectory() as folder:
            (Path(folder) / "gyroid_nonlinear.rst").touch()
            execution, _, reader = self.run_backend(folder, MagicMock())
            self.assertIn("empty result file", execution.error)
            reader.assert_not_called()
            self.assertIsNone(execution.results)

    def test_shutdown_error_cannot_replace_solve_error(self):
        with TemporaryDirectory() as folder:
            session = MagicMock()
            session.solve.side_effect = RuntimeError("first failure")
            session.exit.side_effect = RuntimeError("exit failure")
            execution, _, _ = self.run_backend(folder, session)
            self.assertEqual(execution.error, "first failure")
            self.assertEqual(execution.diagnostics["shutdown_warning"], "exit failure")

    def test_solve_failure_preserves_grpc_response_when_temp_log_is_gone(self):
        with TemporaryDirectory() as folder:
            session = MagicMock()
            session.solve.side_effect = RuntimeError("Solution not converged")
            session.last_response = "MAPDL SOLVE COMMAND\nSolution not converged"
            execution, _, _ = self.run_backend(folder, session)
            self.assertEqual(execution.error, "Solution not converged")
            self.assertEqual((Path(folder) / "solve_output.txt").read_text(),
                             session.last_response)

    def test_success_saves_output_before_finishing(self):
        with TemporaryDirectory() as folder:
            session = MagicMock()
            session.solve.return_value = "SOLUTION CONVERGED"
            execution, _, _ = self.run_backend(folder, session)
            self.assertIsNone(execution.error)
            session.assert_has_calls([
                call.clear(), call.verify("OFF"), call.run("/NERR,DEFA"),
                call.input(str(Path(folder) / "gyroid_mesh.inp")), call.solve(),
            ])
            session.finish.assert_called_once()
            self.assertEqual((Path(folder) / "solve_output.txt").read_text(), "SOLUTION CONVERGED")


if __name__ == "__main__":
    unittest.main()
