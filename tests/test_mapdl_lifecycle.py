"""Failures preserve solver evidence and cannot target an existing instance."""
from dataclasses import replace
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

    def test_unconverged_debug_set_is_excluded_from_partial_results(self):
        reader = MagicMock(nsets=3, time_values=np.array([.4, .8, 1.]))
        reader._resultheader = {"ls_table": np.array([[1, 1, 4], [1, 2, 8], [1, 999999, 12]])}
        reader.result_dof.return_value = ["UX", "UY", "UZ"]
        reader.nodal_solution.return_value = (np.array([10]), np.array([[0., 0., -.08]]))
        reader.principal_nodal_stress.return_value = (np.array([10]), np.ones((1, 5)))
        reader.nodal_reaction_forces.return_value = (np.array([2., -2.]), np.array([9, 10]), np.array([3, 3]))
        result = adapter.MapdlResults(reader, (9, 10))

        self.assertEqual(result.nsets, 2)
        np.testing.assert_array_equal(result.time_values, [.4, .8])
        self.assertEqual(result.top_platen_displacement(-1), -.08)
        np.testing.assert_array_equal(result.equivalent_stress(-1, np.array([10])), [1.])
        self.assertEqual(result.platen_reactions(-1), (2., -2.))
        reader.nodal_solution.assert_called_once_with(1)
        reader.principal_nodal_stress.assert_called_once_with(1)
        reader.nodal_reaction_forces.assert_called_once_with(1)

    def test_frictionless_linear_tet_platen_uses_target_normal_detection(self):
        settings = Settings(mesh_type="TET4")
        mapdl = MagicMock()
        adapter.configure_contact(mapdl, settings, 5, adapter.BOTTOM_PLATEN_PAIR, "frictionless")
        mapdl.keyopt.assert_any_call(5, 4, 2)
        for other_settings, pair, mode in (
            (replace(settings, mesh_type="TET10"), adapter.BOTTOM_PLATEN_PAIR, "frictionless"),
            (replace(settings, loading_mode="force"), adapter.BOTTOM_PLATEN_PAIR, "frictionless"),
            (settings, adapter.BOTTOM_PLATEN_PAIR, "frictional"),
            (settings, 3, "frictionless"),
        ):
            with self.subTest(settings=other_settings, pair=pair, mode=mode):
                mapdl.reset_mock()
                adapter.configure_contact(mapdl, other_settings, 5, pair, mode)
                self.assertNotIn(call(5, 4, 2), mapdl.keyopt.call_args_list)

    def test_frictionless_quadratic_tet_restrains_only_three_lateral_modes(self):
        grid = SimpleNamespace(
            points=np.array([[0., 0., 0.], [1., 0., 0.], [3., 0., 0.], [1., 1., 1.]]),
            point_data={"ansys_node_num": np.array([1, 2, 3, 4])},
        )
        mesh = SimpleNamespace(grid=grid, bottom_ids=np.array([1, 2, 3]))
        settings = Settings(mesh_type="TET10", bottom_platen_contact="frictionless",
                            top_platen_contact="frictionless")
        mapdl = MagicMock()

        adapter.stabilize_in_plane(mapdl, settings, mesh)

        self.assertEqual(mapdl.d.call_args_list, [call(2, "UX", 0), call(2, "UY", 0), call(3, "UY", 0)])
        mapdl.et.assert_not_called()


if __name__ == "__main__":
    unittest.main()
