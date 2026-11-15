"""Persistent, cancellable preview worker with child-side geometry/mesh caching."""

import atexit
import tempfile
import sys
from concurrent.futures import CancelledError
from multiprocessing import get_context
from multiprocessing.shared_memory import SharedMemory
from threading import Lock

_CANCEL_POLL_SECONDS = 0.05


def _serve_preview(connection, scratch):
    from mesh_engine import build_preview
    from preview_transport import pack_preview, write_arrays

    tempfile.tempdir = scratch
    cached = None
    with connection:
        while True:
            try:
                settings, parent_key = connection.recv()
            except EOFError:
                return

            def report(stage):
                connection.send(("progress", stage))

            try:
                result = build_preview(settings, cached, on_progress=report)
                shared = cached is not None and cached.key == parent_key
                surface = (
                    None
                    if shared and result.surface is cached.surface
                    else result.surface
                )
                mesh = None if shared and result.mesh is cached.mesh else result.mesh
                if surface is None and mesh is None:
                    connection.send(("result", (result.key, None, None)))
                else:
                    payload, arrays, size = pack_preview(result.key, surface, mesh)
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
            except Exception as exc:
                connection.send(("error", str(exc)))


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
        self._scratch = tempfile.TemporaryDirectory(prefix="rustmech-preview-")
        parent, child = context.Pipe(duplex=True)
        process = context.Process(
            target=_serve_preview, args=(child, self._scratch.name), daemon=True
        )
        try:
            process.start()
        except BaseException:
            parent.close()
            process.close()
            self._scratch.cleanup()
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
            self._scratch.cleanup()
            self._scratch = None

    def _release_segment(self):
        if self._segment is not None:
            self._segment.close()
            self._segment.unlink()
            self._segment = None

    def build(self, settings, cached, on_progress, cancelled):
        from mesh_engine import Preview
        from preview_transport import unpack_preview

        with self._lock:
            if cancelled.is_set():
                raise CancelledError
            if self._process is None or not self._process.is_alive():
                self._stop()
                self._start()
            try:
                self._connection.send((settings, cached.key if cached else None))
                while not cancelled.is_set():
                    if not self._connection.poll(_CANCEL_POLL_SECONDS):
                        if not self._process.is_alive():
                            raise RuntimeError(
                                "The mesh worker stopped before returning a preview."
                            )
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
                        key, surface, mesh = value
                        return Preview(
                            surface if surface is not None else cached.surface,
                            mesh if mesh is not None else cached.mesh,
                            key,
                        )
                    else:
                        raise ValueError(value)
                raise CancelledError
            except (EOFError, BrokenPipeError, OSError) as exc:
                self._stop()
                raise RuntimeError(
                    "The mesh worker stopped before returning a preview."
                ) from exc
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


def run_mesh_preview(settings, cached, on_progress, cancelled):
    return _DEFAULT_WORKER.build(settings, cached, on_progress, cancelled)


def shutdown_mesh_preview():
    _DEFAULT_WORKER.close()


def warmup_mesh_preview():
    _DEFAULT_WORKER.warmup()
