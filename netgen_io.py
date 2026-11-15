"""Optional array bridge to Netgen, with a portable ASCII fallback."""

from functools import lru_cache
import hashlib
import importlib.util
from importlib.metadata import version
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import numpy as np
from rust_mesh import triangle_normals


@lru_cache(maxsize=1)
def _array_bridge():
    # Load only a bridge built against these exact Netgen/Python binaries.
    # The EXE keeps its existing private dependencies until it is repackaged.
    import netgen

    folder = Path(__file__).resolve().parent / "native" / "bin"
    try:
        metadata = json.loads(
            (folder / "netgen_bridge.json").read_text(encoding="utf-8-sig")
        )
        library = Path(netgen.__file__).parent / "nglib.dll"
        with library.open("rb") as stream:
            library_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        if (
            metadata["netgen_version"] != version("netgen-mesher")
            or metadata["python_cache_tag"] != sys.implementation.cache_tag
            or metadata["netgen_library_sha256"] != library_hash
        ):
            return None
        spec = importlib.util.spec_from_file_location(
            "_rustmech_netgen", folder / "_rustmech_netgen.pyd"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except (OSError, ImportError, ValueError, KeyError):
        return None


def geometry_from_surface(boundary):
    """Avoid text formatting, disk I/O and STL parsing when the bridge is built."""
    from netgen.stl import STLGeometry

    bridge = _array_bridge()
    if bridge is not None:
        points = np.ascontiguousarray(boundary.points, dtype=np.float64)
        triangles = np.ascontiguousarray(
            boundary.faces.reshape(-1, 4)[:, 1:], dtype=np.int64
        )
        # vtkSTLWriter recomputes normals in double precision; the dataset's
        # cached point/cell normals are float32. Preserve the writer's values.
        normals = triangle_normals(points, triangles)
        return bridge.geometry_from_arrays(points, triangles, normals)
    with TemporaryDirectory() as folder:
        path = Path(folder) / "specimen.stl"
        boundary.save(path, binary=False)
        return STLGeometry(str(path))


def restrict_h(parameters, groups, maxh):
    """Append all refinement points in one native call, preserving their order."""
    bridge = _array_bridge()
    if bridge is not None:
        arrays = [
            np.asarray(points, dtype=np.float64).reshape(-1, 3) for points in groups
        ]
        bridge.restrict_h(parameters, np.concatenate(arrays), maxh)
    else:
        for points in groups:
            for x, y, z in points:
                parameters.RestrictH(float(x), float(y), float(z), maxh)
