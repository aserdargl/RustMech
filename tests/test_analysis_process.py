"""Real process-tree cancellation, including a solver-like subprocess."""
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from analysis_process import AnalysisProcess


def echo(value):
    return value


def fail():
    raise ValueError("solver failure")


def mesh_snapshot(settings, mesh):
    return settings.mesh_type, mesh.grid.points, mesh.grid.cells


def spawn_solver(marker):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    Path(marker).write_text(str(child.pid))
    child.wait()


def remote_transport_task(value, remote_auth=None, job_marker=None):
    return value, os.getpid()
remote_transport_task.remote_job_target = True


def cancellable_remote_task(remote_auth=None, job_marker=None):
    from concurrent.futures import CancelledError
    Path(job_marker).write_text('{}')
    while not Path(job_marker).with_suffix('.cancel').exists():
        time.sleep(.01)
    raise CancelledError()
cancellable_remote_task.remote_job_target = True


class AnalysisProcessTests(unittest.TestCase):
    def test_remote_orchestration_stays_with_the_authenticated_panel(self):
        job = AnalysisProcess(remote_transport_task, 'remote')
        self.wait_until(job.done)
        self.assertEqual(job.result(), ('remote', os.getpid()))

    def test_remote_thread_cancellation_keeps_slurm_cancellation_required(self):
        job = AnalysisProcess(cancellable_remote_task)
        self.wait_until(job._remote_marker.exists)
        with patch('truba_jobs.cancel_remote') as cancel:
            job.force_stop()
            cancel.assert_called_once()
        self.assertTrue(job.done())

    def test_failed_remote_cancel_keeps_local_worker_available_for_retry(self):
        job=object.__new__(AnalysisProcess)
        job._remote_marker=Path('remote_job.json')
        job._remote_auth=None
        job._process=MagicMock()
        with patch('truba_jobs.cancel_remote',side_effect=RuntimeError('reconnect')):
            with self.assertRaisesRegex(RuntimeError,'reconnect'):job.force_stop()
        job._process.is_alive.assert_not_called()
        job._process.close.assert_not_called()

    def wait_until(self, predicate):
        deadline = time.monotonic() + 20
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("Timed out waiting for worker")
            time.sleep(0.02)

    def test_result_and_restart(self):
        for value in ("first", "second"):
            job = AnalysisProcess(echo, value)
            self.wait_until(job.done)
            self.assertEqual(job.result(), value)

    def test_worker_error_is_reported(self):
        job = AnalysisProcess(fail)
        self.wait_until(job.done)
        with self.assertRaisesRegex(RuntimeError, "solver failure"):
            job.result()

    def test_existing_fe_mesh_survives_process_handoff(self):
        import numpy as np
        from mesh_engine import generate_volume_mesh
        from model import Settings

        settings = Settings(size=1, cell_size=1, compression=0.1, mesh_resolution=0.1)
        mesh = generate_volume_mesh(settings)
        job = AnalysisProcess(mesh_snapshot, settings, mesh)
        self.wait_until(job.done)
        kind, points, cells = job.result()
        self.assertEqual(kind, settings.mesh_type)
        np.testing.assert_array_equal(points, mesh.grid.points)
        np.testing.assert_array_equal(cells, mesh.grid.cells)

    @unittest.skipUnless(os.name == "nt", "Windows process tree termination")
    def test_force_stop_kills_worker_and_solver_child(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "solver.pid"
            job = AnalysisProcess(spawn_solver, str(marker))
            handle = None
            try:
                self.wait_until(marker.exists)
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.OpenProcess.restype = ctypes.c_void_p
                kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
                kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
                handle = kernel.OpenProcess(0x100000, False, int(marker.read_text()))
                self.assertTrue(handle)
                job.force_stop()
                job = None
                self.assertEqual(kernel.WaitForSingleObject(handle, 2000), 0)
            finally:
                if job is not None:
                    job.force_stop()
                if handle:
                    kernel.CloseHandle(handle)


if __name__ == "__main__":
    unittest.main()
