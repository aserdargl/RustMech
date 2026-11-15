"""Array-only interface to RustMech's separately built Netgen meshing core."""

import ctypes as ct
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from threading import Lock

import numpy as np
from rust_mesh import triangle_normals

_LOCK = Lock()
_DOUBLE = ct.POINTER(ct.c_double)
_INDEX = ct.POINTER(ct.c_int64)
_SIZE = ct.c_size_t
_PROGRESS = ct.CFUNCTYPE(ct.c_int, ct.c_int)
_STAGES = {
    2: "Preparing Netgen geometry charts…",
    3: "Generating Netgen surface mesh…",
    4: "Optimizing Netgen surface mesh…",
    5: "Generating Netgen volume tetrahedra…",
    6: "Optimizing Netgen tetrahedral quality…",
}


@lru_cache(maxsize=1)
def kernel_path():
    """Return only an installed, checksum-verified optimized kernel."""
    folder = Path(__file__).resolve().parent / "native" / "bin" / "netgen_kernel"
    try:
        metadata = json.loads((folder / "build.json").read_text(encoding="utf-8"))
        if metadata["revision"] != "96e5682f-rustmech-atlas-5":
            return None
        for name in ("rustmech_netgen.dll", "rustmech_ngcore.dll"):
            with (folder / name).open("rb") as stream:
                if (
                    hashlib.file_digest(stream, "sha256").hexdigest()
                    != metadata["sha256"][name]
                ):
                    return None
        path = folder / "rustmech_netgen.dll"
        library = _load(str(path))
        library.rustmech_netgen_revision.argtypes = []
        library.rustmech_netgen_revision.restype = ct.c_char_p
        return (
            path
            if library.rustmech_netgen_revision() == b"96e5682f-rustmech-atlas-5"
            else None
        )
    except (OSError, ValueError, KeyError, AttributeError, RuntimeError):
        return None


@lru_cache(maxsize=4)
def _load(path):
    library = ct.CDLL(str(Path(path).resolve()))
    library.rustmech_netgen_abi.argtypes = []
    library.rustmech_netgen_abi.restype = ct.c_uint32
    if library.rustmech_netgen_abi() != 1:
        raise RuntimeError("Unsupported private Netgen ABI; rebuild the kernel.")
    library.rustmech_netgen_error.argtypes = []
    library.rustmech_netgen_error.restype = ct.c_char_p
    library.rustmech_netgen_generate.argtypes = [
        _DOUBLE,
        _SIZE,
        _INDEX,
        _SIZE,
        _DOUBLE,
        _DOUBLE,
        _SIZE,
        ct.c_double,
        ct.c_double,
        ct.POINTER(_SIZE),
    ]
    library.rustmech_netgen_generate.restype = ct.c_void_p
    library.rustmech_netgen_write.argtypes = [
        ct.c_void_p,
        _DOUBLE,
        _SIZE,
        _INDEX,
        _SIZE,
    ]
    library.rustmech_netgen_write.restype = ct.c_int
    library.rustmech_netgen_free.argtypes = [ct.c_void_p]
    library.rustmech_netgen_free.restype = None
    library.rustmech_netgen_timer.argtypes = [ct.c_char_p]
    library.rustmech_netgen_timer.restype = ct.c_double
    if hasattr(library, "rustmech_netgen_set_progress"):
        library.rustmech_netgen_set_progress.argtypes = [_PROGRESS]
        library.rustmech_netgen_set_progress.restype = None
    return library


def generate_arrays(
    boundary, settings, refinement, *, library_path, timings=None, on_progress=None
):
    points = np.ascontiguousarray(boundary.points, dtype=np.float64)
    faces = np.ascontiguousarray(boundary.faces.reshape(-1, 4)[:, 1:], dtype=np.int64)
    normals = triangle_normals(points, faces)
    samples = np.concatenate(
        [np.asarray(group, dtype=float).reshape(-1, 3) for group in refinement]
    )
    library = _load(str(Path(library_path).resolve()))
    counts = (_SIZE * 2)()
    errors = []

    def report(stage):
        try:
            if on_progress:
                on_progress(_STAGES[stage])
            return 0
        except BaseException as exc:
            errors.append(exc)
            return 1

    callback = _PROGRESS(report)
    with _LOCK:
        try:
            if hasattr(library, "rustmech_netgen_set_progress"):
                library.rustmech_netgen_set_progress(callback)
            handle = library.rustmech_netgen_generate(
                points.ctypes.data_as(_DOUBLE),
                len(points),
                faces.ctypes.data_as(_INDEX),
                len(faces),
                normals.ctypes.data_as(_DOUBLE),
                samples.ctypes.data_as(_DOUBLE),
                len(samples),
                settings.mesh_resolution,
                settings.local_mesh_size,
                counts,
            )
        finally:
            if hasattr(library, "rustmech_netgen_set_progress"):
                library.rustmech_netgen_set_progress(_PROGRESS())
        if not handle:
            if errors:
                raise errors[0]
            raise ValueError(
                library.rustmech_netgen_error().decode("utf-8", errors="replace")
            )
        try:
            output = np.empty((counts[0], 3), dtype=np.float64)
            cells = np.empty((counts[1], 4), dtype=np.int64)
            if library.rustmech_netgen_write(
                handle,
                output.ctypes.data_as(_DOUBLE),
                len(output),
                cells.ctypes.data_as(_INDEX),
                len(cells),
            ):
                raise RuntimeError("Netgen output dimensions do not match.")
            if timings is not None:
                for name in (
                    "makeatlas",
                    "find outer chart",
                    "find innner chart",
                    "MeshVolume",
                    "OptimizeVolume",
                    "restrictH OneChart 3",
                    "restrictH OneChart 3a",
                    "restrictH OneChart 3b",
                    "RustMech::GenerateMesh",
                    "Mesh::CreatePoint2VolumeElementTable",
                    "Mesh::ImproveMesh",
                    "Mesh::CalcSurfacesOfNode",
                    "Mesh::Compress",
                ):
                    timings[name] = library.rustmech_netgen_timer(name.encode())
            return output, cells
        finally:
            library.rustmech_netgen_free(handle)
