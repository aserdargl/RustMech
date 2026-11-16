"""Closed surface import and explicit planar compression-face selection (mm)."""
from hashlib import sha256
from pathlib import Path

import numpy as np
import pyvista as pv


def read_surface(path):
    path = Path(path)
    if path.suffix.lower() not in ('.stl', '.obj', '.ply', '.vtp'):
        raise ValueError('Supported geometry files: STL, OBJ, PLY and VTP (coordinates in mm).')
    surface = pv.read(path)
    if not isinstance(surface, pv.PolyData):
        raise ValueError('Import a polygonal surface.')
    surface = surface.clean().triangulate()
    if not surface.n_cells or not np.isfinite(surface.points).all():
        raise ValueError('Geometry is empty or contains non-finite coordinates.')
    if surface.n_open_edges or not surface.is_manifold:
        raise ValueError('Geometry must be a closed, manifold solid. Repair gaps before importing.')
    if surface.connectivity().cell_data['RegionId'].max() != 0:
        raise ValueError('Import one connected solid at a time.')
    return surface.compute_normals(auto_orient_normals=True, split_vertices=False)


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
    from PyQt6.QtCore import QStandardPaths
    directory = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / 'imported_geometry'
    directory.mkdir(parents=True, exist_ok=True)
    digest = sha256(surface.points.tobytes() + surface.faces.tobytes()).hexdigest()
    path = directory / f'{digest}.vtp'
    if not path.exists():
        surface.save(path)
    return str(path)

