"""Portable 3D exports of a saved RustMech mechanical result."""

from pathlib import Path
from struct import pack
from tempfile import TemporaryDirectory

import numpy as np
import pyvista as pv
from vtkmodules.vtkIOXML import vtkXMLUnstructuredGridWriter


_ELEMENT_TYPES = {
    pv.CellType.HEXAHEDRON: "hexa8",
    pv.CellType.QUADRATIC_HEXAHEDRON: "hexa20",
    pv.CellType.TETRA: "tetra4",
    pv.CellType.QUADRATIC_TETRA: "tetra10",
}
_FIELDS = (
    ("Displacement_mm", 3, True),
    ("Displacement_Magnitude_mm", 1, True),
    ("Von_Mises_MPa", 1, True),
    ("Self_Contact_Pressure_MPa", 1, False),
    ("Self_Contact_Shear_MPa", 1, False),
)
_SOURCE_NAME = "results_undeformed.vtu"


def find_mechanical_result(directory: Path) -> Path:
    """Find the saved reference mesh in a run or its results directory."""
    directory = Path(directory)
    for folder in (directory, directory / "03_Results", directory / "full_export" / "03_Results"):
        path = folder / _SOURCE_NAME
        if path.is_file():
            return path
    raise FileNotFoundError("The selected folder does not contain results_undeformed.vtu.")


def _write_records(stream, *records):
    for record in records:
        stream.write(record.encode("ascii").ljust(80, b"\0"))


def _write_component_values(stream, values):
    # EnSight Gold stores coordinates and nodal vectors component by component.
    with np.errstate(over="ignore"):
        components = np.asarray(values.T, dtype="<f4", order="C")
    if not np.isfinite(components).all():
        raise ValueError("The result contains values outside the EnSight Gold numeric range.")
    components.tofile(stream)


def _write_ensight(folder, grid, fields):
    # Gold C Binary uses fixed 80-byte text records and 32-bit numbers.
    if max(grid.n_points, grid.n_cells) > np.iinfo(np.int32).max:
        raise ValueError("The result exceeds the EnSight Gold mesh size limit.")
    with (folder / "mechanical.geo").open("wb") as stream:
        _write_records(stream, "C Binary", "RustMech mechanical result", "Coordinates in mm",
                       "node id assign", "element id assign", "part")
        stream.write(pack("<i", 1))
        _write_records(stream, "Specimen", "coordinates")
        stream.write(pack("<i", grid.n_points))
        _write_component_values(stream, grid.points)
        for cell_type, connectivity in grid.cells_dict.items():
            _write_records(stream, _ELEMENT_TYPES[cell_type])
            stream.write(pack("<i", len(connectivity)))
            # The Gold element types here have the same local node order as VTK.
            (connectivity.astype("<i4") + 1).tofile(stream)

    case = ["FORMAT", "type: ensight gold", "", "GEOMETRY", "model: mechanical.geo", "", "VARIABLE"]
    for name, values in fields.items():
        kind = "vector" if values.ndim == 2 else "scalar"
        filename = f"{name}.var"
        with (folder / filename).open("wb") as stream:
            _write_records(stream, name, "part")
            stream.write(pack("<i", 1))
            _write_records(stream, "coordinates")
            _write_component_values(stream, values)
        case.append(f"{kind} per node: {name} {filename}")
    (folder / "mechanical.case").write_text("\n".join(case) + "\n", encoding="ascii")


def _write_vtu(path, grid):
    writer = vtkXMLUnstructuredGridWriter()
    writer.SetFileName(str(path))
    writer.SetInputData(grid)
    if not writer.Write() or writer.GetErrorCode():
        raise OSError(f"Could not write VTU: {path.name}")


def export_mechanical_result(source: Path, destination: Path) -> Path:
    """Export the existing final load step and return the completed package folder."""
    grid = pv.XMLUnstructuredGridReader(source).read()
    if not grid.n_points or not grid.n_cells:
        raise ValueError("The selected mechanical result has an empty mesh.")
    unsupported = set(grid.celltypes).difference(_ELEMENT_TYPES)
    if unsupported:
        raise ValueError(f"Unsupported EnSight Gold cell types: {sorted(map(int, unsupported))}")

    fields = {}
    for name, components, required in _FIELDS:
        if not required and name not in grid.point_data:
            continue
        values = grid.point_data.get(name)
        shape = (grid.n_points, 3) if components == 3 else (grid.n_points,)
        if values is None or values.shape != shape:
            raise ValueError(f"Missing or invalid nodal result field: {name}")
        fields[name] = np.asarray(values)

    # Publish only a complete package. Each export gets its own folder so retrying
    # or exporting another run never replaces a user's previous export.
    with TemporaryDirectory(dir=destination, prefix=".RustMech-3D-") as staging:
        staging = Path(staging)
        package = staging / "package"
        package.mkdir()
        ensight = package / "EnSight"
        ensight.mkdir()
        paraview = package / "ParaView"
        paraview.mkdir()
        _write_ensight(ensight, grid, fields)
        _write_vtu(paraview / _SOURCE_NAME, grid)
        grid.points = np.asarray(grid.points, dtype=np.float64) + fields["Displacement_mm"]
        _write_vtu(paraview / "deformed_shape.vtu", grid)
        target = staging.with_name(staging.name.removeprefix("."))
        package.rename(target)
    return target
