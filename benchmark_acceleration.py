"""Compare RustMech geometry, result mapping and APDL serialization kernels."""

import json
import statistics
import tempfile
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import numpy as np

from model import Settings
from rust_mesh import von_mises
from structure_library import evaluate_priority_field


def reference_map(source, values, target):
    order = np.argsort(source)
    if len(np.unique(source)) != len(source):
        raise ValueError("Duplicate IDs")
    indices = np.searchsorted(source[order], target)
    if np.any(indices >= len(source)) or not np.array_equal(
        source[order][indices], target
    ):
        raise ValueError("Missing IDs")
    mapped = values[order][indices]
    if not np.all(np.isfinite(mapped)):
        raise ValueError("Non-finite results")
    return mapped


def reference_apdl(mesh, path):
    from mapdl_backend import _solid_connectivity

    grid = mesh.grid
    ids = grid.point_data["ansys_node_num"]
    element_type, connectivity = _solid_connectivity(grid)
    with Path(path).open("w", encoding="ascii", newline="\n") as stream:
        stream.write(f"/PREP7\nET,1,{element_type}\n")
        if element_type == 185:
            stream.write("KEYOPT,1,2,0\n")
        if element_type == 186:
            stream.write("KEYOPT,1,2,1\n")
        stream.write("TYPE,1\nMAT,1\n")
        for node, point in zip(ids, grid.points):
            stream.write(f"N,{node},{point[0]:.16g},{point[1]:.16g},{point[2]:.16g}\n")
        for element, row in zip(grid.cell_data["ansys_elem_num"], connectivity):
            nodes = ids[row].tolist()
            stream.write(f"EN,{element}," + ",".join(map(str, nodes[:8])) + "\n")
            for start in range(8, len(nodes), 8):
                stream.write(
                    "EMORE," + ",".join(map(str, nodes[start : start + 8])) + "\n"
                )
        stream.write("ALLSEL,ALL\nFINISH\n")


def reference_mises(tensors):
    xx, yy, zz, xy, yz, xz = tensors.T
    return np.sqrt(
        0.5 * ((xx - yy) ** 2 + (yy - zz) ** 2 + (zz - xx) ** 2)
        + 3 * (xy**2 + yz**2 + xz**2)
    )


def compare(name, reference, native, repeats=5, exact=True):
    expected, actual = reference(), native()
    if isinstance(expected, np.ndarray):
        if exact:
            np.testing.assert_array_equal(actual, expected)
        else:
            np.testing.assert_allclose(actual, expected, atol=1e-12, rtol=1e-12)
    del expected, actual
    timings = [[], []]
    for iteration in range(repeats):
        order = [0, 1] if iteration % 2 == 0 else [1, 0]
        for index in order:
            start = perf_counter()
            result = [reference, native][index]()
            timings[index].append(perf_counter() - start)
            del result
    old, new = map(statistics.median, timings)
    return dict(kernel=name, reference_seconds=old, rust_seconds=new, speedup=old / new)


def main():
    import nonlinear_compression as studio
    from mapdl_backend import write_apdl_mesh
    from solver_api import map_node_values

    report = []
    axis = (np.arange(100) + 0.5) * 0.06
    coords = (axis[:, None, None], axis[None, :, None], axis[None, None, :])
    for family, name in [
        ("tpms", "Gyroid"),
        ("tpms", "Fischer Koch Y"),
        ("lattice", "BCC-Z"),
        ("lattice", "X-Braced Cubic"),
        ("porous", "Gaussian Random Field"),
    ]:
        settings = replace(Settings(), structure_family=family, structure_name=name)

        def reference():
            with (
                patch("structure_library.tpms_field", return_value=None),
                patch("structure_library.graph_field", return_value=None),
                patch("structure_library.interpolate_field", return_value=None),
            ):
                return evaluate_priority_field(settings, *coords)

        row = compare(
            name,
            reference,
            lambda: evaluate_priority_field(settings, *coords),
            exact=False,
        )
        report.append(row)
        print(json.dumps(row), flush=True)
    rng = np.random.default_rng(1829)
    source = np.arange(1, 1_000_001, dtype=np.int64)
    values = rng.standard_normal((len(source), 3))
    target = source[::-1]
    row = compare(
        "result_node_mapping",
        lambda: reference_map(source, values, target),
        lambda: map_node_values(source, values, target),
    )
    report.append(row)
    print(json.dumps(row), flush=True)
    tensors = rng.standard_normal((1_000_000, 6))
    row = compare(
        "von_mises", lambda: reference_mises(tensors), lambda: von_mises(tensors)
    )
    report.append(row)
    print(json.dumps(row), flush=True)
    with tempfile.TemporaryDirectory() as folder:
        mesh = studio.generate_volume_mesh(
            replace(Settings(), mesh_resolution=0.1, mesh_type="HEX20")
        )
        reference_path, native_path = (
            Path(folder) / "reference.inp",
            Path(folder) / "rust.inp",
        )
        row = compare(
            "apdl_HEX20_64798_elements",
            lambda: reference_apdl(mesh, reference_path),
            lambda: write_apdl_mesh(mesh, native_path),
            repeats=3,
        )
        assert reference_path.read_bytes() == native_path.read_bytes()
        report.append(row)
        print(json.dumps(row), flush=True)
    Path("acceleration_benchmark.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
