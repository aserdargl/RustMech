"""Closed surface import and explicit planar compression-face selection (mm)."""
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import numpy as np
import pyvista as pv


SURFACE_EXTENSIONS = ('.stl', '.obj', '.ply', '.vtp')
SURFACE_FILE_FILTER = 'Surface geometry (' + ' '.join(f'*{suffix}' for suffix in SURFACE_EXTENSIONS) + ')'


def read_polygonal_surface(path):
    path = Path(path)
    if path.suffix.lower() not in SURFACE_EXTENSIONS:
        raise ValueError('Supported geometry files: STL, OBJ, PLY and VTP (coordinates in mm).')
    surface = pv.read(path)
    if not isinstance(surface, pv.PolyData):
        raise ValueError('Import a polygonal surface.')
    surface = surface.clean().triangulate()
    if not surface.n_cells or not np.isfinite(surface.points).all():
        raise ValueError('Geometry is empty or contains non-finite coordinates.')
    if not surface.is_all_triangles:
        raise ValueError('Import a polygonal surface.')
    return surface


def validate_solid_surface(surface, *, require_single=True):
    if surface.n_open_edges or not surface.is_manifold:
        raise ValueError('Geometry must be a closed, manifold solid. Repair gaps before importing.')
    if require_single and surface.connectivity().cell_data['RegionId'].max() != 0:
        raise ValueError('Import one connected solid at a time.')


def read_surface(path, *, require_single=True):
    surface = read_polygonal_surface(path)
    validate_solid_surface(surface, require_single=require_single)
    return surface.compute_normals(auto_orient_normals=True, split_vertices=False)


class SurfaceComponents:
    """Connected triangle regions of a source in original cell order."""

    # Use physical area rather than triangle counts, which depend on tessellation.
    MAX_FRAGMENT_AREA_RATIO = 0.01

    def __init__(self, surface):
        self.surface = surface
        indexed = surface.copy(deep=False)
        indexed.cell_data['_source_triangle'] = np.arange(surface.n_cells)
        connected = indexed.connectivity()
        source_cells = connected.cell_data['_source_triangle']
        region_ids = np.asarray(connected.cell_data['RegionId'])
        first_cells = np.full(int(region_ids.max()) + 1, surface.n_cells, dtype=np.intp)
        np.minimum.at(first_cells, region_ids, source_cells)
        self.labels = np.empty(surface.n_cells, dtype=np.intp)
        self.labels[source_cells] = np.argsort(np.argsort(first_cells))[region_ids]
        self.count = len(first_cells)
        self.areas = ()
        if self.count > 1:
            sizes = surface.compute_cell_sizes(length=False, volume=False)
            self.areas = np.bincount(self.labels, weights=sizes.cell_data['Area'])

    def automatic_choice(self):
        if self.count == 1:
            return 0
        largest = int(np.argmax(self.areas))
        area = self.areas[largest]
        debris = self.areas.sum() - area
        return largest if area > 0 and debris <= area * self.MAX_FRAGMENT_AREA_RATIO else None

    def extract(self, component, *, excluded=False):
        if self.count == 1 and not excluded:
            return self.surface
        mask = self.labels != component if excluded else self.labels == component
        return self.surface.extract_cells(mask, pass_cell_ids=False, pass_point_ids=False).extract_surface(
            pass_cellid=False, pass_pointid=False, algorithm='dataset_surface')

    def solid(self, component):
        surface = self.extract(component)
        # The partition already guarantees connectivity. Validate the retained
        # geometry independently of any open or non-manifold discarded fragments.
        validate_solid_surface(surface, require_single=False)
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


def surface_cache_directory():
    from PySide6.QtCore import QStandardPaths
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / 'imported_geometry'


def store_surface(surface, directory=None):
    directory = Path(directory) if directory is not None else surface_cache_directory()
    directory.mkdir(parents=True, exist_ok=True)
    digest = sha256(surface.points.tobytes() + surface.faces.tobytes()).hexdigest()
    path = directory / f'{digest}.vtp'
    if not path.exists():
        temporary = path.with_name(f'{path.stem}.{uuid4().hex}.vtp')
        try:
            surface.save(temporary)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return str(path)


def compression_direction(surface, bottom_cell, top_cell):
    """A reusable direction in source coordinates, never triangle identities."""
    _, origin, normal = plane_faces(surface, bottom_cell)
    _, target, _ = plane_faces(surface, top_cell)
    align_compression_surface(surface, bottom_cell, top_cell)
    return normal if np.dot(target - origin, normal) > 0 else -normal


def automatic_compression_selection(surface, direction):
    """Find outer contact patches, flattening shallow broadly axial end tessellation if needed."""
    direction = np.asarray(direction, dtype=float)
    if direction.shape != (3,) or not np.isfinite(direction).all() or np.linalg.norm(direction) < 1e-12:
        raise ValueError('Choose a finite, nonzero compression direction.')
    direction = direction / np.linalg.norm(direction)
    faces = surface.faces.reshape(-1, 4)[:, 1:]
    coordinates = surface.points @ direction
    projections = coordinates[faces]
    low, high = float(projections.min()), float(projections.max())
    tolerance = max(surface.length * 1e-6, 1e-9)
    if high - low <= tolerance:
        raise ValueError('No compression height along this direction.')
    cells = []
    rough_ends = []
    for level in (low, high):
        candidates = np.flatnonzero(np.max(np.abs(projections - level), axis=1) < tolerance)
        normals = triangle_cross(surface.points, faces[candidates])
        lengths = np.linalg.norm(normals, axis=1)
        planar = (lengths > 1e-14) & (np.abs(normals @ direction) >= lengths * (1 - 1e-6))
        if np.any(planar):
            cells.append(int(candidates[planar][0]))
            continue
        rough_ends.append((len(cells), level))
        cells.append(None)

    if not rough_ends:
        return surface, tuple(cells)

    # Padded isosurfaces can have broad caps that undulate over a shallow band.
    # Require genuine projected contact area before moving any vertices.
    max_depth = (high - low) * .01
    transverse = np.cross(np.eye(3)[np.argmin(abs(direction))], direction)
    transverse /= np.linalg.norm(transverse)
    footprint = np.ptp(surface.points @ transverse) * np.ptp(surface.points @ np.cross(direction, transverse))
    error = ValueError('No planar end face at the specimen boundary; select faces manually or repair the end geometry.')
    points = np.array(surface.points, dtype=float)
    moved = np.zeros(surface.n_points, dtype=bool)
    for role, level in rough_ends:
        nearby = np.flatnonzero(np.max(np.abs(projections - level), axis=1) <= max_depth)
        normals = triangle_cross(points, faces[nearby])
        lengths = np.linalg.norm(normals, axis=1)
        projected = (normals @ direction) * (-1 if role == 0 else 1)
        cap = (lengths > 1e-14) & (projected >= .95 * lengths)
        if not np.any(cap) or projected[cap].sum() / 2 < .05 * footprint:
            raise error
        contact = nearby[cap]
        cells[role] = int(contact[np.argmax(projected[cap])])
        vertices = np.unique(faces[contact])
        offsets = level - coordinates[vertices]
        points[vertices] += offsets[:, None] * direction
        moved[vertices] = offsets != 0

    touched = faces[moved[faces].any(axis=1)]
    original_normals = triangle_cross(surface.points, touched)
    adjusted_normals = triangle_cross(points, touched)
    if np.any(np.einsum('ij,ij->i', original_normals, adjusted_normals) <= 0):
        raise error
    adjusted = surface.copy()
    adjusted.points = points
    return adjusted, tuple(cells)


def triangle_cross(points, faces):
    corners = points[faces]
    return np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])


def automatic_compression_faces(surface, direction):
    """Mark all contact patches on the two end planes and align for analysis."""
    source, cells = automatic_compression_selection(surface, direction)
    aligned, height = align_compression_surface(source, *cells)
    return aligned, height, cells

