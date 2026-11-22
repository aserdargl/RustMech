"""Persistent, cancellable preview worker with child-side geometry/mesh caching."""

import atexit
import faulthandler
import shutil
import signal
import tempfile
import sys
import traceback
from concurrent.futures import CancelledError
from multiprocessing import get_context
from multiprocessing.shared_memory import SharedMemory
from pathlib import Path
from threading import Lock

_CANCEL_POLL_SECONDS = 0.05
_CRASH_REPORT = "worker-crash.txt"


def _serve_preview(connection, scratch):
    # The desktop process owns cancellation and stops this worker during shutdown.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    with connection, (Path(scratch) / _CRASH_REPORT).open("wb") as crash_report:
        faulthandler.enable(file=crash_report)
        try:
            _serve_preview_requests(connection, scratch)
        except (EOFError, BrokenPipeError):
            return
        except Exception as exc:
            details = traceback.format_exc()
            # Keep startup errors available even if the first request races worker exit.
            crash_report.write(details.encode("utf-8", errors="replace"))
            crash_report.flush()
            connection.send(("error", (str(exc) or type(exc).__name__, details)))
        finally:
            faulthandler.disable()


def _serve_preview_requests(connection, scratch):
    from mesh_engine import build_preview
    from preview_transport import pack_preview, write_arrays

    tempfile.tempdir = scratch
    cached = None
    while True:
        settings, parent_key, include_mesh = connection.recv()

        def report(stage):
            connection.send(("progress", stage))

        result = build_preview(settings, cached, on_progress=report, include_mesh=include_mesh)
        shared = cached is not None and cached.key == parent_key
        surface = None if shared and result.surface is cached.surface else result.surface
        mesh = None if shared and result.mesh is cached.mesh else result.mesh
        if surface is None and mesh is None:
            connection.send(("result", (result.key, result.mesh is not None)))
        else:
            payload, arrays, size = pack_preview(result.key, surface, mesh)
            payload['reuse_mesh'] = bool(shared and result.mesh is cached.mesh)
            connection.send(("shared_layout", (payload, size)))
            kind, name = connection.recv()
            if kind != "shared_buffer":
                raise RuntimeError("Invalid preview buffer response.")
            segment = SharedMemory(
                name=name,
                **({"track": False} if sys.version_info >= (3, 13) else {}),
            )
            try:
                write_arrays(segment, arrays)
            finally:
                segment.close()
            del arrays
            connection.send(("shared_ready", None))
        cached = result


class PreviewWorker:
    """Serialize jobs, reuse imports/cache, and restart after native cancellation."""

    def __init__(self):
        self._lock = Lock()
        self._process = None
        self._connection = None
        self._scratch = None
        self._segment = None

    def _start(self):
        context = get_context("spawn")
        # Remove the scratch directory only after the child closes the crash report.
        self._scratch = Path(tempfile.mkdtemp(prefix="rustmech-preview-"))
        parent, child = context.Pipe(duplex=True)
        process = context.Process(
            target=_serve_preview, args=(child, str(self._scratch)), daemon=True
        )
        try:
            process.start()
        except BaseException:
            parent.close()
            process.close()
            shutil.rmtree(self._scratch)
            self._scratch = None
            raise
        finally:
            child.close()
        self._process, self._connection = process, parent

    def _stop(self):
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        if self._process is not None:
            if self._process.is_alive():
                self._process.terminate()
            self._process.join()
            self._process.close()
            self._process = None
        self._release_segment()
        if self._scratch is not None:
            shutil.rmtree(self._scratch)
            self._scratch = None

    def _release_segment(self):
        if self._segment is not None:
            self._segment.close()
            self._segment.unlink()
            self._segment = None

    def _unexpected_exit(self):
        # Pipe EOF can precede publication of the process exit code.
        self._process.join(timeout=0.2)
        code = self._process.exitcode
        error = RuntimeError("The mesh worker stopped before returning a preview.")
        if code is not None:
            status = str(code)
            if sys.platform == "win32":
                status += f" (0x{code & 0xffffffff:08X})"
            error.add_note(f"Mesh worker exit code: {status}")
        try:
            report = (self._scratch / _CRASH_REPORT).read_text(
                encoding="utf-8", errors="replace"
            ).strip()
        except OSError:
            report = ""
        if report:
            error.add_note(report)
        return error

    def build(self, settings, cached, on_progress, cancelled, *, include_mesh=False):
        from mesh_engine import Preview
        from preview_transport import unpack_preview

        with self._lock:
            if cancelled.is_set():
                raise CancelledError
            if self._process is None or not self._process.is_alive():
                self._stop()
                self._start()
            try:
                self._connection.send((settings, cached.key if cached else None, include_mesh))
                while not cancelled.is_set():
                    if not self._connection.poll(_CANCEL_POLL_SECONDS):
                        if not self._process.is_alive():
                            raise EOFError
                        continue
                    kind, value = self._connection.recv()
                    if kind == "progress":
                        if on_progress:
                            on_progress(value)
                    elif kind == "shared_layout":
                        payload, size = value
                        self._segment = SharedMemory(create=True, size=max(1, size))
                        self._connection.send(("shared_buffer", self._segment.name))
                    elif kind == "shared_ready":
                        try:
                            return unpack_preview(payload, self._segment, cached)
                        finally:
                            self._release_segment()
                    elif kind == "result":
                        key, has_mesh = value
                        return Preview(cached.surface, cached.mesh if has_mesh else None, key)
                    elif kind == "error":
                        message, child_traceback = value
                        error = ValueError(message)
                        error.add_note(child_traceback.rstrip())
                        raise error
                    else:
                        raise RuntimeError(f"Unexpected preview worker response: {kind!r}")
                raise CancelledError
            except (EOFError, OSError) as exc:
                try:
                    if cancelled.is_set():
                        raise CancelledError from None
                    if not isinstance(exc, (EOFError, BrokenPipeError)) and self._process.is_alive():
                        raise
                    raise self._unexpected_exit() from exc
                finally:
                    self._stop()
            except BaseException:
                self._stop()
                raise

    def warmup(self):
        """Import the computation stack while the parent builds its interface."""
        with self._lock:
            if self._process is None or not self._process.is_alive():
                self._stop()
                self._start()

    def close(self):
        with self._lock:
            self._stop()


_DEFAULT_WORKER = PreviewWorker()
atexit.register(_DEFAULT_WORKER.close)


def run_mesh_preview(settings, cached, on_progress, cancelled, *, include_mesh=False):
    return _DEFAULT_WORKER.build(settings, cached, on_progress, cancelled, include_mesh=include_mesh)


def shutdown_mesh_preview():
    _DEFAULT_WORKER.close()


def warmup_mesh_preview():
    _DEFAULT_WORKER.warmup()
