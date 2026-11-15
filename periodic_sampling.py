"""Bounded regular-grid sampling with exact-period reuse for analytic fields."""

import numpy as np

from structure_library import evaluate_priority_field


SAMPLE_BUDGET = 2_000_000


def has_repeating_cell(settings):
    # Porous generators describe a whole random specimen, not identical cells.
    family = (settings.structure_family or 'tpms').strip().lower()
    return family in ('tpms', 'lattice')


def _sample_blocks(settings, axis, output, threshold):
    width = len(axis)
    # Bound every native call, including grids with a large transverse plane.
    z_width = min(width, SAMPLE_BUDGET)
    y_width = min(width, max(1, SAMPLE_BUDGET // z_width))
    x_width = min(width, max(1, SAMPLE_BUDGET // (y_width * z_width)))
    for x in range(0, width, x_width):
        for y in range(0, width, y_width):
            for z in range(0, width, z_width):
                slices = (slice(x, x + x_width), slice(y, y + y_width), slice(z, z + z_width))
                output[slices] = evaluate_priority_field(
                    settings, axis[slices[0], None, None],
                    axis[None, slices[1], None], axis[None, None, slices[2]],
                ) - threshold


def sample_priority_grid(settings, axis, *, dtype=np.float64, threshold=0.0):
    """Keep the requested grid and boundary; reuse only grid-aligned periods.

    Unaligned grids and procedural structures use bounded direct evaluation.
    Partial final cells and ghost layers can reuse samples too; no rounding of
    the user's spacing, cell size, or specimen dimensions is performed.
    """
    axis = np.asarray(axis, dtype=float)
    width = len(axis)
    output = np.empty((width,) * 3, dtype=dtype)
    period_steps = 0
    if width > 1 and has_repeating_cell(settings):
        step = axis[1] - axis[0]
        if step > 0:
            ratio = settings.cell_size / step
            candidate = int(round(ratio))
            if (0 < candidate < width and
                    np.isclose(ratio, candidate, rtol=0, atol=1e-9) and
                    np.allclose(axis, axis[0] + np.arange(width) * step, rtol=0,
                                atol=abs(step) * 1e-9)):
                period_steps = candidate
    if not period_steps:
        _sample_blocks(settings, axis, output, threshold)
        return output

    _sample_blocks(settings, axis[:period_steps], output[:period_steps, :period_steps, :period_steps], threshold)
    # Grow initialized regions by copying disjoint slices. Advanced-index
    # gathers would allocate another slab and read each value indirectly.
    limits = [period_steps] * 3
    for dimension in (2, 1, 0):
        filled = period_steps
        while filled < width:
            copied = min(filled, width - filled)
            source = [slice(0, limit) for limit in limits]
            target = source.copy()
            source[dimension] = slice(0, copied)
            target[dimension] = slice(filled, filled + copied)
            output[tuple(target)] = output[tuple(source)]
            filled += copied
        limits[dimension] = width
    return output
