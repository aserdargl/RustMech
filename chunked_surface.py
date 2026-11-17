"""Bounded scalar bricks and exact-period contour reuse, with no internal caps.

Bricks own disjoint grid cells and share one plane of samples. Seam vertices
are reconstructed from their global grid edge and the original scalar values;
no spatial tolerance, welding quantization or resolution reduction is needed.
Only output geometry scales with specimen volume. Intermediate templates are
spooled to a temporary directory and the in-memory template cache is bounded.
"""
from collections import OrderedDict
from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from mesh_resources import available_memory, require_memory
from periodic_sampling import BlockSampler, aligned_period_steps

AUTO_POINTS = 16_000_000
BLOCK_POINTS = 2_000_000
CACHE_BYTES = 128 * 1024**2
REUSE_TEMPLATES = True


def should_chunk(width):
    free = available_memory()
    return width ** 3 > AUTO_POINTS or (free is not None and width ** 3 * 12 > free // 4)


def block_cuts(width, target_cells, period):
    cells = width - 1
    if period and period <= target_cells:
        stride = max(period, target_cells // period * period)
        # Start regular bricks on an actual specimen plane, after the ghost.
        return [0, *range(stride + 1, cells - 1, stride), cells]
    return [*range(0, cells, target_cells), cells]


def edge_template(vertices, faces, field):
    """Represent each contour vertex by its integer grid edge and exact fraction."""
    nearest = np.rint(vertices)
    axes = np.argmax(np.abs(vertices - nearest), axis=1)
    lower = nearest.astype(np.int32)
    rows = np.arange(len(vertices))
    lower[rows, axes] = np.minimum(np.floor(vertices[rows, axes]).astype(np.int32),
                                   np.asarray(field.shape)[axes] - 2)
    upper = lower.copy()
    upper[rows, axes] += 1
    a, b = field[tuple(lower.T)].astype(float), field[tuple(upper.T)].astype(float)
    fraction = vertices[rows, axes].astype(float) - lower[rows, axes]
    np.divide(-a, b - a, out=fraction, where=(b != a))
    np.clip(fraction, 0, 1, out=fraction)
    return dict(lower=lower, axes=axes.astype(np.uint8), fraction=fraction, faces=faces.astype(np.int32, copy=False))


def build_chunked_surface(settings, axis, spacing, *, backend='flying_edges', on_progress=None):
    from mesh_engine import contour_arrays, geometry_threshold, _project_reference_surface, _reference_is_closed

    width = len(axis)
    free = available_memory()
    cache_limit = min(CACHE_BYTES, free // 16) if free is not None else CACHE_BYTES
    budget = min(BLOCK_POINTS, max(4096, free // 64)) if free is not None else BLOCK_POINTS
    target_cells = max(1, int(np.cbrt(budget)) - 1)
    period = aligned_period_steps(settings, axis)
    cuts = block_cuts(width, target_cells, period)
    peak_shape = max(np.diff(cuts)) + 1
    require_memory(int(peak_shape) ** 3 * 32, 'Reference surface sampling')
    sampler = BlockSampler(settings, axis, threshold=geometry_threshold(settings), max_cell_points=budget)
    intervals = list(zip(cuts[:-1], cuts[1:]))
    total = len(intervals) ** 3
    cache, cache_size = OrderedDict(), 0
    records, paths = [], {}
    points = triangles = extracted = reused = 0

    def remember(path, data):
        nonlocal cache_size
        size = sum(value.nbytes for value in data.values())
        if size > cache_limit:
            np.savez(path, **data)
            return
        while cache and cache_size + size > cache_limit:
            old_path, old = cache.popitem(last=False)
            if not old_path.exists():
                np.savez(old_path, **old)
            cache_size -= sum(value.nbytes for value in old.values())
        cache[path] = data
        cache_size += size

    with TemporaryDirectory(prefix='rustmech-surface-') as directory:
        for index, bounds in enumerate(product(intervals, repeat=3), 1):
            starts = tuple(a for a, _ in bounds)
            stops = tuple(b + 1 for _, b in bounds)
            shape = tuple(b - a for a, b in zip(starts, stops))
            boundary = tuple((a == 0, b == width) for a, b in zip(starts, stops))
            # Procedural and unaligned fields never reuse contour templates.
            key = (shape, tuple(a % period for a in starts), boundary) if period and REUSE_TEMPLATES else ('unique', index)
            if key in paths:
                path, count, face_count = paths[key]
                reused += 1
            else:
                field = sampler.sample(starts, stops)
                for dimension, (first, last) in enumerate(boundary):
                    if first:
                        plane = [slice(None)] * 3
                        plane[dimension] = 0
                        field[tuple(plane)] = 1
                    if last:
                        plane = [slice(None)] * 3
                        plane[dimension] = -1
                        field[tuple(plane)] = 1
                if np.any(field < 0) and np.any(field >= 0):
                    vertices, faces = contour_arrays(field, backend=backend)
                    data = edge_template(vertices, faces, field)
                else:
                    data = dict(lower=np.empty((0, 3), np.int32), axes=np.empty(0, np.uint8),
                                fraction=np.empty(0), faces=np.empty((0, 3), np.int32))
                extracted += 1
                count, face_count = len(data['lower']), len(data['faces'])
                path = Path(directory) / f'{extracted}.npz'
                paths[key] = path, count, face_count
                remember(path, data)
                del field, data
            points += count
            triangles += face_count
            if points > np.iinfo(np.int32).max:
                raise ValueError('Reference surface exceeds the native point-index representation.')
            require_memory(points * 96 + triangles * 80, 'Reference surface assembly')
            records.append((path, starts, count, face_count))
            if on_progress:
                on_progress(f'Chunked surface {index}/{total} · {reused} periodic blocks reused')
        if not triangles:
            raise ValueError('Geometry is empty; adjust its parameters.')
        vertices = np.empty((points, 3), np.float32)
        faces = np.empty((triangles, 3), np.int32)
        point_offset = face_offset = 0
        if on_progress:
            on_progress('Joining surface blocks and checking topology…')
        for path, starts, count, face_count in records:
            data = cache.get(path)
            if data is None:
                with np.load(path, allow_pickle=False) as archive:
                    data = {name: archive[name] for name in archive.files}
                remember(path, data)
            else:
                cache.move_to_end(path)
            # Evaluate world positions in float64, then store the same float32
            # output precision as the single-volume VTK extractor. Identical
            # global edge expressions round identically on either side of a seam.
            part = data['lower'].astype(np.float64)
            part += starts
            part[np.arange(count), data['axes']] += data['fraction']
            part -= 1  # Remove the global negative ghost before scaling.
            upper_plane = part == width - 3
            part *= spacing
            part[upper_plane] = settings.size
            vertices[point_offset:point_offset + count] = part
            faces[face_offset:face_offset + face_count] = data['faces'] + point_offset
            point_offset += count
            face_offset += face_count
        cache.clear()
        surface = _project_reference_surface(vertices, faces, 0, settings.size)
    # Do not retain the raw assembled arrays while checking or retrying the
    # completed surface; VTK owns the returned geometry independently.
    del vertices, faces, data, part, upper_plane, records, paths
    closed = _reference_is_closed(surface)
    if not closed and backend == 'flying_edges':
        if on_progress:
            on_progress('Repairing ambiguous contours with chunked Lewiner extraction…')
        del surface
        return build_chunked_surface(settings, axis, spacing, backend='marching_cubes', on_progress=on_progress)
    if not closed:
        raise ValueError('Chunked surface is not closed/manifold; adjust geometry sampling.')
    surface.field_data['_surface_extractor'] = [backend]
    surface.field_data['_surface_mode'] = ['chunked']
    surface.field_data['_surface_chunks'] = [total, extracted, reused]
    surface.field_data['_surface_sampling'] = [sampler.evaluated_points, sampler.peak_points]
    return surface
