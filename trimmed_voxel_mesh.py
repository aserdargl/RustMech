"""Experimental TET4 clipping of an implicit solid on a Cartesian grid."""

from itertools import permutations
from operator import index

import numpy as np
import pyvista as pv
from vtkmodules.vtkCommonExecutionModel import vtkAlgorithm
from vtkmodules.vtkFiltersGeneral import vtkTableBasedClipDataSet

from mesh_resources import require_memory
from tetrahedral_mesh import _grid_from_arrays


def build_trimmed_tet4(field, subdivisions, *, size=1.0):
    """Mesh field(points) <= 0 in [0, size]³ using linear edge intersections.

    This prototype only resolves features sampled by the background grid.
    A near-tangent surface may produce small, positive-volume tetrahedra.
    """
    subdivisions = index(subdivisions)
    if subdivisions < 1 or not np.isfinite(size) or size <= 0:
        raise ValueError("The grid requires positive subdivisions and a positive finite size.")
    width = subdivisions + 1
    require_memory(width**3 * 64 + subdivisions**3 * 1400, "Trimmed TET4 meshing")
    axis = np.linspace(0.0, size, width)
    points = np.stack(np.meshgrid(axis, axis, axis, indexing="ij"), axis=-1).reshape(-1, 3)
    values = np.asarray(field(points), dtype=float)
    if values.shape != (len(points),) or not np.isfinite(values).all():
        raise ValueError("The field must return one finite scalar per grid vertex.")
    # Treat evaluation roundoff at a grid vertex as an exact intersection.
    zero_tolerance = np.max(np.abs(values)) * (16 * np.finfo(float).eps)
    values = np.where(np.abs(values) <= zero_tolerance, 0, values)
    if not np.any(values < 0):
        raise ValueError("No solid interior was sampled by the Cartesian grid.")

    # The six monotone paths between opposite cube corners choose the same
    # face diagonals in every adjacent voxel.
    steps = (width * width, width, 1)
    voxel_origins = np.arange(width**3).reshape((width,) * 3)[:-1, :-1, :-1].ravel()
    offsets = np.asarray([(0, a, a + b, a + b + c) for a, b, c in permutations(steps)])
    connectivity = (voxel_origins[:, None, None] + offsets).reshape(-1, 4)
    background = pv.UnstructuredGrid()
    background.points = points
    background.SetCells(int(pv.CellType.TETRA), pv.CellArray.from_regular_cells(connectivity))
    background.point_data["level_set"] = values
    background.set_active_scalars("level_set")

    clip = vtkTableBasedClipDataSet()
    clip.SetInputData(background)
    clip.SetInsideOut(True)
    clip.SetOutputPointsPrecision(vtkAlgorithm.DOUBLE_PRECISION)
    clip.Update()
    # Triangulate as one grid so neighboring cut cells share triangulations.
    # Merge exact coincidences when an intersection lands on a grid vertex.
    clipped = pv.wrap(clip.GetOutput()).clean(tolerance=0).triangulate()
    if not clipped.n_cells or not np.all(clipped.celltypes == pv.CellType.TETRA):
        raise ValueError("Clipping did not produce a solid TET4 mesh.")

    cells = np.asarray(clipped.cell_connectivity).reshape(-1, 4)
    ordered = np.sort(cells, axis=1)
    # Exact coincidences can collapse a cut cell to a face, edge or point.
    cells = cells[np.all(np.diff(ordered, axis=1) != 0, axis=1)]
    used, connectivity = np.unique(cells, return_inverse=True)
    points = np.asarray(clipped.points)[used]
    cells = connectivity.reshape(-1, 4)
    corners = points[cells]
    edges = corners[:, 1:] - corners[:, :1]
    determinants = np.einsum("ij,ij->i", edges[:, 0], np.cross(edges[:, 1], edges[:, 2]))
    if not len(cells) or not np.all(np.isfinite(determinants) & (determinants != 0)):
        raise ValueError("Clipping produced zero-volume or invalid tetrahedra.")
    reversed_orientation = determinants < 0
    cells[reversed_orientation] = cells[reversed_orientation][:, [1, 0, 2, 3]]
    return _grid_from_arrays(points, cells)
