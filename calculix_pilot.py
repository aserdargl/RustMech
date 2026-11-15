"""Export a RustMech TET10 compression pilot to a portable CalculiX input deck."""
import argparse
from pathlib import Path

import numpy as np
import pyvista as pv

from model import Settings
from tetrahedral_mesh import generate_tetrahedral_mesh

SIZE = 10.0
FACES = ((0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0))


def platen_faces(cells, nodes):
    """Return CalculiX face labels for a specimen boundary plane."""
    return [
        f"{element}, S{face}"
        for element, cell in enumerate(cells, 1)
        for face, corners in enumerate(FACES, 1)
        if all(cell[corner] in nodes for corner in corners)
    ]


def add_platen(lines, first, element, bottom, top, name):
    corners = ((-1, -1), (11, -1), (11, 11), (-1, 11))
    lines.append("*NODE")
    lines.extend(
        f"{first + 4 * layer + i}, {x}, {y}, {z}"
        for layer, z in enumerate((bottom, top))
        for i, (x, y) in enumerate(corners)
    )
    lines.extend([
        f"*ELEMENT, TYPE=C3D8, ELSET={name}",
        f"{element}, " + ", ".join(str(i) for i in range(first, first + 8)),
        f"*NSET, NSET={name}, GENERATE", f"{first}, {first + 7}, 1",
        f"*SOLID SECTION, ELSET={name}, MATERIAL=PLATE",
        f"*SURFACE, NAME={name}_FACE, TYPE=ELEMENT",
        f"{element}, S{2 if name == 'LOWER' else 1}",
    ])


def write_deck(path, friction):
    """Write the cube and fully constrained displacement-controlled platens."""
    settings = Settings(size=SIZE, mesh_resolution=3, local_mesh_size=3)
    cube = pv.Box(bounds=(0, SIZE, 0, SIZE, 0, SIZE)).triangulate()
    mesh = generate_tetrahedral_mesh(settings, cube)
    grid = mesh.grid
    cells = grid.cell_connectivity.reshape(-1, 10) + 1
    lines = ["*HEADING", "RustMech TET10 cube compression (mm, N, MPa)", "*NODE"]
    lines.extend(f"{i}, {x:.12g}, {y:.12g}, {z:.12g}" for i, (x, y, z) in enumerate(grid.points, 1))
    lines.append("*ELEMENT, TYPE=C3D10, ELSET=CUBE")
    lines.extend(f"{i}, " + ", ".join(map(str, cell)) for i, cell in enumerate(cells, 1))
    lines.extend([
        "*MATERIAL, NAME=CUBE", "*ELASTIC", "1000, 0.3",
        "*MATERIAL, NAME=PLATE", "*ELASTIC", "1000, 0.3",
        "*SOLID SECTION, ELSET=CUBE, MATERIAL=CUBE",
    ])
    for index, (name, bottom, top) in enumerate((("LOWER", -1, 0), ("UPPER", SIZE, SIZE + 1))):
        add_platen(lines, grid.n_points + 1 + 8 * index, len(cells) + 1 + index, bottom, top, name)
        plane = np.flatnonzero(np.isclose(grid.points[:, 2], index * SIZE)) + 1
        faces = platen_faces(cells, set(plane))
        if not faces:
            raise ValueError(f"Missing specimen faces for {name} platen")
        lines.extend([f"*SURFACE, NAME=CUBE_{name}, TYPE=ELEMENT", *faces])
    lines.extend(["*SURFACE INTERACTION, NAME=PLATEN", "*SURFACE BEHAVIOR, PRESSURE-OVERCLOSURE=LINEAR", "100000"])
    if friction:
        lines.extend(["*FRICTION", str(friction)])
    lines.extend([
        "*CONTACT PAIR, INTERACTION=PLATEN, TYPE=SURFACE TO SURFACE",
        "CUBE_LOWER, LOWER_FACE", "CUBE_UPPER, UPPER_FACE",
        "*STEP, NLGEOM, INC=100", "*STATIC", "0.1, 1, 1e-6, 0.1",
        "*BOUNDARY", "LOWER, 1, 3", "UPPER, 1, 2", "UPPER, 3, 3, -0.1",
        "*NODE PRINT, NSET=UPPER, TOTALS=ONLY", "RF",
        "*NODE PRINT, NSET=LOWER, TOTALS=ONLY", "RF",
        "*NODE FILE", "U, RF", "*EL FILE", "S", "*END STEP",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    print(f"{path}: {len(cells)} C3D10 elements")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--friction", type=float, choices=(0.0, 0.2), default=0.0)
    args = parser.parse_args()
    write_deck(args.output, args.friction)
