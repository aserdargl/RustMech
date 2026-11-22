"""Write specimen meshes for the ANSYS Workbench External Model importer."""

from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from pyvista import CellType

from mapdl_backend import write_cdb_mesh
from mesh_resources import require_memory


_ABAQUS = {
    CellType.TETRA: "C3D4", CellType.QUADRATIC_TETRA: "C3D10",
    CellType.HEXAHEDRON: "C3D8", CellType.QUADRATIC_HEXAHEDRON: "C3D20",
    CellType.WEDGE: "C3D6", CellType.QUADRATIC_WEDGE: "C3D15",
}
_NASTRAN = {
    CellType.TETRA: "CTETRA", CellType.QUADRATIC_TETRA: "CTETRA",
    CellType.HEXAHEDRON: "CHEXA", CellType.QUADRATIC_HEXAHEDRON: "CHEXA",
    CellType.WEDGE: "CPENTA", CellType.QUADRATIC_WEDGE: "CPENTA",
    CellType.PYRAMID: "CPYRAM", CellType.QUADRATIC_PYRAMID: "CPYRAM",
}
_WEDGE_ORDER = {
    CellType.WEDGE: (0, 2, 1, 3, 5, 4),
    CellType.QUADRATIC_WEDGE: (0, 2, 1, 3, 5, 4, 8, 7, 6, 11, 10, 9, 12, 14, 13),
}
_DYNA_ORDER = {
    CellType.TETRA: (0, 1, 2, 3, 3, 3, 3, 3),
    CellType.QUADRATIC_TETRA: tuple(range(10)),
    CellType.HEXAHEDRON: tuple(range(8)),
    CellType.WEDGE: (0, 2, 1, 1, 3, 5, 4, 4),
}
# Fluent's interior face is oriented towards its first adjacent cell.
_FLUENT = {
    CellType.TETRA: (2, ((0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0))),
    CellType.HEXAHEDRON: (4, ((0, 1, 2, 3), (4, 7, 6, 5), (0, 3, 7, 4),
                              (1, 5, 6, 2), (0, 4, 5, 1), (3, 2, 6, 7))),
    CellType.PYRAMID: (5, ((0, 1, 2, 3), (0, 4, 1), (1, 4, 2), (2, 4, 3), (3, 4, 0))),
    CellType.WEDGE: (6, ((0, 2, 1), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (2, 0, 3, 5))),
}


def ansys_export_filters(mesh):
    return list(_export_options(mesh).values())


def _export_options(mesh):
    types = set(mesh.grid.celltypes)
    filters = {".cdb": "ANSYS mesh (*.cdb)"}
    for suffix, (label, supported, _) in _TEXT_EXPORTS.items():
        if types <= supported.keys():
            filters[suffix] = label
    return filters


def write_ansys_mesh(mesh, path: Path) -> Path:
    suffix = path.suffix.lower()
    if suffix == ".cdb":
        return write_cdb_mesh(mesh, path)
    if suffix not in _export_options(mesh):
        raise ValueError(f"The current mesh cannot be exported as {suffix or 'a file without an extension'}.")
    with TemporaryDirectory(prefix=".rustmech-mesh-", dir=path.parent) as folder:
        staged = Path(folder) / f"export{suffix}"
        with staged.open("w", encoding="ascii", newline="\n", buffering=1024 * 1024) as stream:
            _TEXT_EXPORTS[suffix][2](stream, mesh.grid)
        staged.replace(path)
    return path


def _element_blocks(grid):
    node_ids = grid.point_data["ansys_node_num"]
    element_ids = grid.cell_data["ansys_elem_num"]
    for cell_type, cells in grid.cells_dict.items():
        yield cell_type, element_ids[grid.celltypes == cell_type], node_ids[cells]


def _write_abaqus(stream, grid):
    stream.write("*HEADING\nRustMech mesh, coordinates in mm\n*NODE\n")
    for node, (x, y, z) in zip(grid.point_data["ansys_node_num"], grid.points, strict=True):
        stream.write(f"{node}, {x:.16g}, {y:.16g}, {z:.16g}\n")
    for cell_type, ids, nodes in _element_blocks(grid):
        stream.write(f"*ELEMENT, TYPE={_ABAQUS[cell_type]}\n")
        if cell_type in _WEDGE_ORDER:
            nodes = nodes[:, _WEDGE_ORDER[cell_type]]
        for element, connected in zip(ids, nodes, strict=True):
            values = [str(element), *map(str, connected)]
            stream.write(", ".join(values[:16]))
            if len(values) > 16:
                stream.write(",\n" + ", ".join(values[16:]))
            stream.write("\n")


def _write_nastran(stream, grid):
    stream.write("$ RustMech mesh, coordinates in mm\nBEGIN BULK\n")
    for node, (x, y, z) in zip(grid.point_data["ansys_node_num"], grid.points, strict=True):
        stream.write(f"GRID*   {node:16d}{'':16}{x:16.9E}{y:16.9E}\n*       {z:16.9E}\n")
    for cell_type, ids, nodes in _element_blocks(grid):
        if cell_type in _WEDGE_ORDER:
            nodes = nodes[:, _WEDGE_ORDER[cell_type]]
        if cell_type == CellType.QUADRATIC_HEXAHEDRON:
            # Nastran places upright edges before edges of the top face.
            nodes = nodes[:, (*range(12), *range(16, 20), *range(12, 16))]
        if cell_type == CellType.QUADRATIC_WEDGE:
            nodes = nodes[:, (*range(9), *range(12, 15), *range(9, 12))]
        for element, connected in zip(ids, nodes, strict=True):
            fields = [int(element), 1, *connected]
            stream.write(f"{_NASTRAN[cell_type]:8}" + "".join(f"{value:8d}" for value in fields[:8]) + "\n")
            for start in range(8, len(fields), 8):
                stream.write("+       " + "".join(f"{value:8d}" for value in fields[start:start + 8]) + "\n")
    stream.write("ENDDATA\n")


def _write_dyna(stream, grid):
    stream.write("*KEYWORD\n$ RustMech mesh, coordinates in mm\n*PART\nRustMech\n1,1,1\n*SECTION_SOLID\n1,1\n")
    if CellType.QUADRATIC_TETRA in grid.celltypes:
        stream.write("*PART\nRustMech quadratic tetrahedra\n2,2,1\n*SECTION_SOLID\n2,16\n")
    stream.write("*NODE\n")
    for node, (x, y, z) in zip(grid.point_data["ansys_node_num"], grid.points, strict=True):
        stream.write(f"{node:8d}{x:16.9E}{y:16.9E}{z:16.9E}\n")
    stream.write("*ELEMENT_SOLID\n")
    for cell_type, ids, nodes in _element_blocks(grid):
        quadratic = cell_type == CellType.QUADRATIC_TETRA
        part = 2 if quadratic else 1
        card = "\n" if quadratic else ""
        for element, connected in zip(ids, nodes[:, _DYNA_ORDER[cell_type]], strict=True):
            stream.write(f"{element:8d}{part:8d}{card}" + "".join(f"{node:8d}" for node in connected) + "\n")
    stream.write("*END\n")


def _write_fluent(stream, grid):
    require_memory(grid.n_cells * 2500, "Fluent mesh export")
    face_map = {}
    cell_types = np.empty(grid.n_cells, dtype=np.uint8)
    for cell_type, cells in grid.cells_dict.items():
        code, faces = _FLUENT[cell_type]
        indices = np.flatnonzero(grid.celltypes == cell_type)
        cell_types[indices] = code
        for cell, corners in zip(indices + 1, cells + 1, strict=True):
            for face in faces:
                nodes = tuple(int(corners[i]) for i in face)
                key = tuple(sorted(nodes))
                if key not in face_map:
                    face_map[key] = (nodes, cell, 0)
                else:
                    first_nodes, first_cell, second_cell = face_map[key]
                    if second_cell:
                        raise ValueError("Fluent export requires a manifold volume mesh.")
                    face_map[key] = (first_nodes, first_cell, cell)
    interiors = sum(bool(face[2]) for face in face_map.values())
    stream.write('(1 "RustMech volume mesh, coordinates in meters")\n(2 3)\n')
    stream.write(f"(10 (0 1 {grid.n_points:x} 0 3))\n(12 (0 1 {grid.n_cells:x} 0))\n")
    stream.write(f"(13 (0 1 {len(face_map):x} 0))\n(10 (1 1 {grid.n_points:x} 1 3)(\n")
    np.savetxt(stream, grid.points * .001, fmt="%.16e")
    stream.write("))\n")
    if np.all(cell_types == cell_types[0]):
        stream.write(f"(12 (2 1 {grid.n_cells:x} 1 {cell_types[0]:x}))\n")
    else:
        stream.write(f"(12 (2 1 {grid.n_cells:x} 1 0)(\n")
        stream.writelines(f"{code:x}\n" for code in cell_types)
        stream.write("))\n")
    for zone, first, count, kind in ((3, 1, interiors, 2), (4, interiors + 1, len(face_map) - interiors, 3)):
        if count:
            stream.write(f"(13 ({zone:x} {first:x} {first + count - 1:x} {kind:x} 0)(\n")
            for nodes, left, right in face_map.values():
                if bool(right) == (kind == 2):
                    stream.write(" ".join(f"{item:x}" for item in (len(nodes), *nodes, left, right)) + "\n")
            stream.write("))\n")
    stream.write("(45 (2 fluid rustmech-volume)())\n")
    if interiors:
        stream.write("(45 (3 interior interior)())\n")
    stream.write("(45 (4 wall exterior)())\n")


_TEXT_EXPORTS = {
    ".inp": ("Abaqus input (*.inp)", _ABAQUS, _write_abaqus),
    ".bdf": ("Nastran bulk data (*.bdf)", _NASTRAN, _write_nastran),
    ".dat": ("Nastran bulk data (*.dat)", _NASTRAN, _write_nastran),
    ".nas": ("Nastran bulk data (*.nas)", _NASTRAN, _write_nastran),
    ".msh": ("Fluent mesh (*.msh)", _FLUENT, _write_fluent),
    ".k": ("LS-DYNA keyword (*.k)", _DYNA_ORDER, _write_dyna),
    ".key": ("LS-DYNA keyword (*.key)", _DYNA_ORDER, _write_dyna),
}
