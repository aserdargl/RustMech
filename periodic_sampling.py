"""Bounded regular-grid sampling with exact-period reuse for analytic fields."""

import numpy as np

from structure_library import evaluate_priority_field
from mesh_resources import require_memory


SAMPLE_BUDGET = 2_000_000


def has_repeating_cell(settings):
    # Porous generators describe a whole random specimen, not identical cells.
    family = (settings.structure_family or 'tpms').strip().lower()
    return family in ('tpms', 'lattice')


def aligned_period_steps(settings, axis):
    """Return an exact grid-aligned period; never round the requested spacing."""
    if len(axis) < 2 or not has_repeating_cell(settings):
        return 0
    step = axis[1] - axis[0]
    if step <= 0:
        return 0
    ratio = settings.cell_size / step
    candidate = int(round(ratio))
    if (0 < candidate < len(axis) and
            np.isclose(ratio, candidate, rtol=0, atol=1e-9) and
            np.allclose(axis, axis[0] + np.arange(len(axis)) * step,
                        rtol=0, atol=abs(step) * 1e-9)):
        return candidate
    return 0


class BlockSampler:
    """Bounded rectangular samples; retain just one exact periodic cell."""

    def __init__(self, settings, axis, *, threshold=0.0, max_cell_points=SAMPLE_BUDGET):
        self.settings, self.axis, self.threshold = settings, np.asarray(axis), threshold
        period = aligned_period_steps(settings, self.axis)
        self.period = period if period ** 3 <= max_cell_points else 0
        self.cell = None
        self.evaluated_points = 0
        self.peak_points = 0

    def _evaluate(self, axes):
        shape = tuple(map(len, axes))
        require_memory(int(np.prod(shape)) * 12, 'Reference surface sampling')
        output = np.empty(shape, np.float32)
        widths = [1, 1, min(shape[2], SAMPLE_BUDGET)]
        widths[1] = min(shape[1], max(1, SAMPLE_BUDGET // widths[2]))
        widths[0] = min(shape[0], max(1, SAMPLE_BUDGET // (widths[1] * widths[2])))
        from itertools import product
        for starts in product(*(range(0, n, w) for n, w in zip(shape, widths))):
            slices = tuple(slice(start, min(start + width, n)) for start, width, n in zip(starts, widths, shape))
            vectors = [axis[part] for axis, part in zip(axes, slices)]
            output[slices] = evaluate_priority_field(self.settings, vectors[0][:, None, None],
                                                    vectors[1][None, :, None], vectors[2][None, None, :]) - self.threshold
            self.evaluated_points += int(np.prod([len(vector) for vector in vectors]))
        self.peak_points = max(self.peak_points, output.size)
        return output

    def sample(self, starts, stops):
        if self.period:
            if self.cell is None:
                self.cell = self._evaluate([self.axis[:self.period]] * 3)
            indices = [np.arange(start, stop) % self.period for start, stop in zip(starts, stops)]
            result = self.cell[np.ix_(*indices)]
            self.peak_points = max(self.peak_points, result.size)
            return result
        return self._evaluate([self.axis[start:stop] for start, stop in zip(starts, stops)])


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
    require_memory(width ** 3 * np.dtype(dtype).itemsize, "Geometry field sampling")
    output = np.empty((width,) * 3, dtype=dtype)
    period_steps = aligned_period_steps(settings, axis)
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
