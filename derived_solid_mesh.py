"""Conforming prism, pyramid and mixed meshes derived from a voxel solid."""

from dataclasses import replace

import numpy as np
import pyvista as pv

from mesh_resources import require_memory
from model import HEX_EDGES


WEDGE_EDGES = ((0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3), (0, 3), (1, 4), (2, 5))
PYRAMID_EDGES = ((0, 1), (1, 2), (2, 3), (3, 0), (0, 4), (1, 4), (2, 4), (3, 4))
_PRISM_CORNERS = ((0, 2, 1, 4, 6, 5), (0, 3, 2, 4, 7, 6))
_PYRAMID_BASES = ((0, 1, 2, 3), (4, 7, 6, 5), (0, 3, 7, 4),
                  (1, 5, 6, 2), (0, 4, 5, 1), (3, 2, 6, 7))
_SECOND_ORDER = {
    pv.CellType.HEXAHEDRON: (pv.CellType.QUADRATIC_HEXAHEDRON, HEX_EDGES),
    pv.CellType.WEDGE: (pv.CellType.QUADRATIC_WEDGE, WEDGE_EDGES),
    pv.CellType.PYRAMID: (pv.CellType.QUADRATIC_PYRAMID, PYRAMID_EDGES),
}


def derive_solid_mesh(mesh, mesh_type):
    grid = mesh.grid
    quadratic = mesh_type in ("PRISM15", "PYRAMID13", "HYBRID20")
    require_memory(grid.n_cells * (6500 if quadratic else 850), f"{mesh_type} meshing")
    bricks = grid.cell_connectivity.reshape(-1, 8)
    points = grid.points
    if mesh_type.startswith("PRISM"):
        wedges = bricks[:, _PRISM_CORNERS].reshape(-1, 6)
        blocks = [(pv.CellType.WEDGE, wedges)]
    else:
        blocks = []
        if mesh_type.startswith("HYBRID"):
            if len(bricks) < 2:
                raise ValueError("A mixed mesh needs at least two retained voxels. Refine the mesh.")
            blocks.append((pv.CellType.HEXAHEDRON, bricks[1::2]))
            bricks = bricks[::2]
        centers = points[bricks].mean(axis=1)
        center_ids = np.arange(len(points), len(points) + len(centers))
        pyramids = np.column_stack((bricks[:, _PYRAMID_BASES].reshape(-1, 4), np.repeat(center_ids, 6)))
        points = np.concatenate((points, centers))
        blocks.append((pv.CellType.PYRAMID, pyramids))

    node_ids = grid.point_data["ansys_node_num"]
    bottom = np.flatnonzero(np.isin(node_ids, mesh.bottom_ids))
    top = np.flatnonzero(np.isin(node_ids, mesh.top_ids))
    if quadratic:
        points, blocks, bottom, top = _add_midside_nodes(points, blocks, bottom, top)
    derived = pv.UnstructuredGrid(dict(blocks), points)
    derived.point_data["ansys_node_num"] = np.arange(1, derived.n_points + 1)
    derived.cell_data["ansys_elem_num"] = np.arange(1, derived.n_cells + 1)
    return replace(mesh, grid=derived, bottom_ids=bottom + 1, top_ids=top + 1)


def _add_midside_nodes(points, blocks, bottom, top):
    edges = [
        np.sort(cells[:, _SECOND_ORDER[cell_type][1]], axis=2).reshape(-1, 2)
        for cell_type, cells in blocks
    ]
    unique, inverse = np.unique(np.concatenate(edges), axis=0, return_inverse=True)
    midpoint_ids = len(points) + np.arange(len(unique))
    end_points = points[unique]
    points = np.concatenate((points, end_points.mean(axis=1)))
    positions = np.cumsum([len(block) for block in edges[:-1]])
    quadratic = [
        (_SECOND_ORDER[cell_type][0], np.column_stack((cells, ids.reshape(len(cells), -1))))
        for (cell_type, cells), ids in zip(blocks, np.split(midpoint_ids[inverse], positions), strict=True)
    ]
    bottom = np.concatenate((bottom, midpoint_ids[np.isin(unique, bottom).all(axis=1)]))
    top = np.concatenate((top, midpoint_ids[np.isin(unique, top).all(axis=1)]))
    return points, quadratic, bottom, top
