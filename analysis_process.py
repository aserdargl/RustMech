"""Isolated analysis jobs whose owned process tree can be forcibly stopped."""

import json
import multiprocessing
import os
from pathlib import Path
import pickle
import signal
import subprocess
import tempfile
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

_progress_path = None


def report_analysis_step(message, completed=None, total=None):
    """Publish a stage from an owned analysis process without affecting its result."""
    if _progress_path is not None:
        try:
            temporary = _progress_path.with_suffix(".tmp")
            temporary.write_text(json.dumps((message, completed, total)), encoding="utf-8")
            temporary.replace(_progress_path)
        except OSError:
            pass


def _execute(result_path, target, args):
    global _progress_path
    _progress_path = Path(result_path).with_suffix(".progress")
    report_analysis_step("Preparing operation…")
    if os.name != "nt":
        os.setsid()
    try:
        payload = (True, target(*args))
    except BaseException:
        payload = (False, traceback.format_exc())
    with open(result_path, "wb") as stream:
        pickle.dump(payload, stream)


class AnalysisProcess:
    def __init__(self, target, *args, remote_auth=None, remote_options=None):
        self._scratch = tempfile.TemporaryDirectory(prefix="rustmech-analysis-")
        self._result_path = Path(self._scratch.name) / "result.pkl"
        self._remote_auth=remote_auth
        self._remote_marker=None
        self._generic_remote_target=bool(getattr(target,"remote_job_target",False))
        self.background_submission = bool(getattr(target, "remote_submit_target", False))
        if self._generic_remote_target or target is run_resume_analysis or (
            target is run_single_analysis and remote_auth is not None and args[0].solver_backend == "kratos"
        ):
            self._remote_marker=Path(self._scratch.name)/"remote_job.json"
            args=(*args,remote_auth,str(self._remote_marker))
        self._remote_future = None
        if self._generic_remote_target:
            # Only SSH orchestration runs here; numerical work remains on Slurm.
            # Stay in the panel's process to reuse its authenticated SSH session.
            self._remote_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='truba-job')
            self._remote_future = self._remote_executor.submit(target, *args, **(remote_options or {}))
            return
        self._process = multiprocessing.get_context("spawn").Process(
            target=_execute, args=(str(self._result_path), target, args),
            name="RustMech-analysis",
        )
        try:
            self._process.start()
        except BaseException:
            self._scratch.cleanup()
            raise

    def done(self):
        if self._remote_future is not None:
            return self._remote_future.done()
        return not self._process.is_alive()

    @property
    def is_remote(self):
        return self._remote_marker is not None

    def remote_status(self):
        if self._remote_marker is not None and self._remote_marker.exists():
            try:return json.loads(self._remote_marker.read_text())
            except (OSError,ValueError):pass
        return None

    def progress_status(self, *, with_counts=False):
        try:
            payload = self._result_path.with_suffix(".progress").read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None
        try:
            message, completed, total = json.loads(payload)
        except (ValueError, TypeError):
            message, completed, total = payload, None, None
        return (message, completed, total) if with_counts else message

    def result(self):
        if self._remote_future is not None:
            try:
                return self._remote_future.result()
            finally:
                self._remote_executor.shutdown(wait=False)
                self._scratch.cleanup()
        self._process.join()
        try:
            if not self._result_path.exists():
                raise RuntimeError(f"Analysis process exited ({self._process.exitcode}).")
            with self._result_path.open("rb") as stream:
                success, payload = pickle.load(stream)
            if not success:
                raise RuntimeError(payload)
            return payload
        finally:
            self._process.close()
            self._scratch.cleanup()

    def detach_remote(self):
        """Finish the SSH submission and stop local tracking of the Slurm job."""
        if self._remote_future is None:
            self._terminate_local()
            return None
        self._remote_marker.with_suffix('.detach').touch()
        self._remote_future.add_done_callback(self._cleanup_detached_remote)
        return self._remote_future

    def _cleanup_detached_remote(self, _):
        self._remote_executor.shutdown(wait=False)
        self._scratch.cleanup()

    def force_stop(self):
        if self._remote_marker is not None:
            if getattr(self,"_generic_remote_target",False): self._remote_marker.with_suffix(".cancel").touch()
            from truba_jobs import cancel_remote
            cancel_remote(self._remote_marker,self._remote_auth)
        if getattr(self, '_remote_future', None) is not None:
            try:
                self._remote_future.result(timeout=10)
            except FutureTimeout:
                raise RuntimeError('Remote cancellation was requested; waiting for the SSH worker to stop. Retry Stop.')
            except Exception:
                pass  # The cancellation marker normally raises CancelledError.
            self._remote_executor.shutdown(wait=False)
            self._scratch.cleanup()
            return
        self._terminate_local()

    def _terminate_local(self):
        if self._process.is_alive():
            if os.name == "nt":
                # The same force-termination mechanism used by Task Manager,
                # scoped to this job's PID and its descendants (including MAPDL).
                result = subprocess.run(
                    ["taskkill", "/PID", str(self._process.pid), "/T", "/F"],
                    capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=10,
                )
                if result.returncode and self._process.is_alive():
                    raise RuntimeError(result.stderr.decode(errors="replace"))
            else:
                try:
                    os.killpg(self._process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    if self._process.is_alive():
                        self._process.kill()
            self._process.join(timeout=2)
            if self._process.is_alive():
                raise RuntimeError("Analysis process could not be terminated.")
        self._process.join()
        self._process.close()
        self._scratch.cleanup()


def run_single_analysis(settings, mesh, remote_auth=None, job_marker=None):
    from mechanical_analysis import run_analysis
    from solver_api import create_solver_backend

    return str(run_analysis(settings, mesh, create_solver_backend(settings.solver_backend,remote_auth,job_marker)))


def run_resume_analysis(record_path,remote_auth=None,job_marker=None):
    from mechanical_analysis import resume_kratos_analysis
    return str(resume_kratos_analysis(record_path,remote_auth,job_marker))


    
