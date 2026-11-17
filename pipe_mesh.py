"""Body-fitted O-grid hexahedral cross-sections for circular pipes (all lengths in mm).

The cross-section is a square core surrounded by four curved blocks, so the wall is
a true circle instead of a Cartesian stair-step. Cells are extruded along Z by the
caller. Wall cells can be graded to resolve the boundary layer.
"""
from __future__ import annotations

import math

import numpy as np


def split_cells_across(cells_across):
    """Cells across the diameter = core cells + 2 x radial cells (core is >= 2)."""
    core = max(2, cells_across // 2)
    if (cells_across - core) % 2:
        core += 1
    return core, max(1, (cells_across - core) // 2)


def radial_fractions(count, grading):
    """Node positions 0..1 over ``count`` layers; the wall cell is ``grading`` times thinner than the first."""
    if count < 1:
        raise ValueError("At least one radial cell is required.")
    if count == 1 or grading <= 1.0 + 1e-12:
        return np.linspace(0.0, 1.0, count + 1)
    widths = (grading ** (-1.0 / (count - 1))) ** np.arange(count)
    nodes = np.concatenate(([0.0], np.cumsum(widths)))
    return nodes / nodes[-1]


def _quad_geometry(points, quads):
    corners = points[quads]
    edges = np.roll(corners, -1, axis=1) - corners
    lengths = np.linalg.norm(edges, axis=2)
    previous = -np.roll(edges, 1, axis=1)
    cosine = np.einsum("qij,qij->qi", edges, previous) / np.maximum(
        np.linalg.norm(edges, axis=2) * np.linalg.norm(previous, axis=2), 1e-300)
    angles = np.degrees(np.arccos(np.clip(cosine, -1, 1)))
    x, y = corners[:, :, 0], corners[:, :, 1]
    area = 0.5 * (x * np.roll(y, -1, axis=1) - np.roll(x, -1, axis=1) * y).sum(axis=1)
    return lengths, angles, area


def cross_section(diameter_mm, cells_across, wall_grading=1.0, core_fraction=0.5):
    """Return ``(points (N,2), quads (M,4), info)`` centred on the origin; quads are counter-clockwise."""
    if not 0.2 <= core_fraction <= 0.65:
        raise ValueError("core_fraction must be between 0.2 and 0.65.")
    radius = diameter_mm / 2
    nc, nr = split_cells_across(cells_across)
    a = core_fraction * radius
    t = np.linspace(-1.0, 1.0, nc + 1)
    s = radial_fractions(nr, wall_grading)
    cu, cv = np.meshgrid(a * t, a * t, indexing="ij")
    blocks = [np.column_stack((cu.ravel(), cv.ravel()))]
    quads = []
    base = np.arange((nc + 1) ** 2).reshape(nc + 1, nc + 1)
    quads.append(np.stack((base[:-1, :-1], base[1:, :-1], base[1:, 1:], base[:-1, 1:]), axis=-1).reshape(-1, 4))
    offset = (nc + 1) ** 2
    inner = np.column_stack((np.full(nc + 1, a), a * t))
    angle = np.pi / 4 * t
    outer = radius * np.column_stack((np.cos(angle), np.sin(angle)))
    east = (1 - s)[:, None, None] * inner[None] + s[:, None, None] * outer[None]  # [radial, tangential]
    local = np.arange((nr + 1) * (nc + 1)).reshape(nr + 1, nc + 1)
    block_quads = np.stack((local[:-1, :-1], local[1:, :-1], local[1:, 1:], local[:-1, 1:]), axis=-1).reshape(-1, 4)
    for turn in range(4):
        c, sn = math.cos(turn * math.pi / 2), math.sin(turn * math.pi / 2)
        rotated = east.reshape(-1, 2) @ np.array(((c, sn), (-sn, c)))
        blocks.append(rotated)
        quads.append(block_quads + offset)
        offset += (nr + 1) * (nc + 1)
    raw = np.concatenate(blocks)
    quads = np.concatenate(quads)
    key = np.round(raw / (radius * 1e-9)).astype(np.int64)
    _, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
    points = raw[first]
    quads = inverse.reshape(-1)[quads]
    _, _, area = _quad_geometry(points, quads)
    flip = area < 0
    quads[flip] = quads[flip][:, ::-1]
    lengths, angles, area = _quad_geometry(points, quads)
    centres = points[quads].mean(axis=1)
    wall_distance = radius - np.linalg.norm(centres, axis=1)
    wall_cells = np.linalg.norm(points[quads], axis=2).max(axis=1) > radius * (1 - 1e-6)
    info = dict(core_cells=nc, radial_cells=nr, cells_per_layer=len(quads),
                min_angle_deg=float(angles.min()), max_angle_deg=float(angles.max()),
                max_aspect_2d=float((lengths.max(axis=1) / lengths.min(axis=1)).max()),
                first_cell_centre_mm=float(wall_distance[wall_cells].mean()),
                first_cell_centre_min_mm=float(wall_distance[wall_cells].min()),
                section_area_mm2=float(area.sum()), areas_mm2=area,
                min_cell_size_mm=float(lengths.min()))
    return points, quads, info


def extrude(points2d, quads, z_edges, centre=(0.0, 0.0)):
    """3D VTK hexahedra (layer-major: cell = layer*len(quads)+quad). Returns ``(points, cells)``."""
    nxy, nz = len(points2d), len(z_edges)
    z_edges = np.asarray(z_edges, dtype=float)
    points = np.column_stack((np.tile(points2d[:, 0] + centre[0], nz),
                              np.tile(points2d[:, 1] + centre[1], nz), np.repeat(z_edges, nxy)))
    layer = (np.arange(nz - 1) * nxy)[:, None, None]
    cells = np.concatenate((quads[None] + layer, quads[None] + layer + nxy), axis=2).reshape(-1, 8)
    return points, cells


def quad_adjacency(quads):
    """Pairs of quads sharing an edge (each interior edge is shared by exactly two)."""
    edges = np.sort(np.concatenate((quads[:, (0, 1)], quads[:, (1, 2)], quads[:, (2, 3)], quads[:, (3, 0)])), axis=1)
    owner = np.tile(np.arange(len(quads)), 4)
    order = np.lexsort((edges[:, 1], edges[:, 0]))
    edges, owner = edges[order], owner[order]
    same = np.all(edges[1:] == edges[:-1], axis=1)
    return np.column_stack((owner[:-1][same], owner[1:][same]))
