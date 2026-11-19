"""Closed surface import and explicit planar compression-face selection (mm)."""
from hashlib import sha256
from pathlib import Path

import numpy as np
import pyvista as pv


SURFACE_EXTENSIONS = ('.stl', '.obj', '.ply', '.vtp')
SURFACE_FILE_FILTER = 'Surface geometry (' + ' '.join(f'*{suffix}' for suffix in SURFACE_EXTENSIONS) + ')'


def read_surface(path, *, require_single=True):
    path = Path(path)
    if path.suffix.lower() not in SURFACE_EXTENSIONS:
        raise ValueError('Supported geometry files: STL, OBJ, PLY and VTP (coordinates in mm).')
    surface = pv.read(path)
    if not isinstance(surface, pv.PolyData):
        raise ValueError('Import a polygonal surface.')
    surface = surface.clean().triangulate()
    if not surface.n_cells or not np.isfinite(surface.points).all():
        raise ValueError('Geometry is empty or contains non-finite coordinates.')
    if surface.n_open_edges or not surface.is_manifold:
        raise ValueError('Geometry must be a closed, manifold solid. Repair gaps before importing.')
    if require_single and surface.connectivity().cell_data['RegionId'].max() != 0:
        raise ValueError('Import one connected solid at a time.')
    return surface.compute_normals(auto_orient_normals=True, split_vertices=False)


def prepare_general_surface(surface):
    """Centre an unscaled, mm solid in its containing cube for channel placement."""
    result = surface.copy(deep=True)
    bounds = np.asarray(result.bounds).reshape(3,2)
    extent = bounds[:,1]-bounds[:,0]
    size = float(extent.max())
    if size <= 0 or not np.isfinite(size):
        raise ValueError('Imported geometry has no finite extent.')
    result.points = result.points-bounds[:,0]+(size-extent)/2
    return result, size


def plane_faces(surface, cell_id):
    """Return all triangles on the clicked triangle's plane."""
    triangles = surface.points[surface.faces.reshape(-1, 4)[:, 1:]]
    triangle = triangles[cell_id]
    normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
    length = np.linalg.norm(normal)
    if length <= 1e-14:
        raise ValueError('Select a non-degenerate planar face.')
    normal /= length
    tolerance = max(surface.length * 1e-6, 1e-9)
    distances = (triangles - triangle[0]) @ normal
    ids = np.flatnonzero(np.max(np.abs(distances), axis=1) < tolerance)
    return ids, triangle[0], normal


def align_compression_surface(surface, bottom_cell, top_cell):
    bottom, origin, normal = plane_faces(surface, bottom_cell)
    top, target, top_normal = plane_faces(surface, top_cell)
    if abs(np.dot(normal, top_normal)) < 1 - 1e-6:
        raise ValueError('Support and compression faces must be parallel planar faces.')
    if np.dot(target - origin, normal) < 0:
        normal = -normal
    height = float(np.dot(target - origin, normal))
    tolerance = max(surface.length * 1e-6, 1e-9)
    if height <= tolerance:
        raise ValueError('Select two different, opposite end faces.')
    helper = np.eye(3)[np.argmin(abs(normal))]
    x = np.cross(helper, normal)
    x /= np.linalg.norm(x)
    y = np.cross(normal, x)
    result = surface.copy()
    result.points = (surface.points - origin) @ np.column_stack((x, y, normal))
    z = result.points[:, 2]
    if z.min() < -tolerance or z.max() > height + tolerance:
        raise ValueError('Selected faces must bound the entire solid between the two compression platens.')
    result.points[:, :2] -= result.points[:, :2].min(axis=0)
    z[np.abs(z) < tolerance] = 0
    z[np.abs(z - height) < tolerance] = height
    result.cell_data['compression_face'] = np.zeros(result.n_cells, dtype=np.uint8)
    result.cell_data['compression_face'][bottom] = 1
    result.cell_data['compression_face'][top] = 2
    result = result.compute_normals(auto_orient_normals=True, split_vertices=False)
    return result, height


def store_surface(surface):
    from PySide6.QtCore import QStandardPaths
    directory = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / 'imported_geometry'
    directory.mkdir(parents=True, exist_ok=True)
    digest = sha256(surface.points.tobytes() + surface.faces.tobytes()).hexdigest()
    path = directory / f'{digest}.vtp'
    if not path.exists():
        surface.save(path)
    return str(path)


def compression_direction(surface, bottom_cell, top_cell):
    """A reusable direction in source coordinates, never triangle identities."""
    _, origin, normal = plane_faces(surface, bottom_cell)
    _, target, _ = plane_faces(surface, top_cell)
    align_compression_surface(surface, bottom_cell, top_cell)
    return normal if np.dot(target - origin, normal) > 0 else -normal


def automatic_compression_faces(surface, direction):
    """Find planar triangles at both global extrema along a chosen direction.

    All coplanar patches are marked by align_compression_surface, including
    disconnected contact patches on one connected lattice. Curved ends and
    planes inside the specimen are deliberately not guessed.
    """
    direction = np.asarray(direction, dtype=float)
    if direction.shape != (3,) or not np.isfinite(direction).all() or np.linalg.norm(direction) < 1e-12:
        raise ValueError('Choose a finite, nonzero compression direction.')
    direction = direction / np.linalg.norm(direction)
    triangles = surface.points[surface.faces.reshape(-1, 4)[:, 1:]]
    projections = triangles @ direction
    low, high = float(projections.min()), float(projections.max())
    tolerance = max(surface.length * 1e-6, 1e-9)
    if high - low <= tolerance:
        raise ValueError('No compression height along this direction.')
    cells = []
    for level in (low, high):
        candidates = np.flatnonzero(np.max(np.abs(projections - level), axis=1) < tolerance)
        for cell in candidates:
            try:
                _, _, normal = plane_faces(surface, int(cell))
            except ValueError:
                continue
            if abs(np.dot(normal, direction)) >= 1 - 1e-6:
                cells.append(int(cell))
                break
        else:
            raise ValueError('No planar end face at the specimen boundary; select faces manually or repair the end geometry.')
    aligned, height = align_compression_surface(surface, *cells)
    return aligned, height, tuple(cells)

