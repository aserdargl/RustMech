"""Grid Convergence Index (Roache; Celik et al., ASME J. Fluids Eng. 130, 078001, 2008).

Pure numerical helpers plus a runner that solves a pipe-flow case on three systematically refined meshes.
Works for any scalar result (pressure drop, friction factor, permeability) and either flow solver.
"""
import csv
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path

SAFETY_THREE_GRIDS = 1.25
SAFETY_TWO_GRIDS = 3.0
MIN_REFINEMENT_RATIO = 1.3

QUANTITIES = {
    # name: (path inside the flow summary, label)
    "pressure_drop_specimen_Pa": (("pressure_drop_specimen_Pa",), "Specimen pressure drop [Pa]"),
    "pressure_drop_total_Pa": (("pressure_drop_total_Pa",), "Total pressure drop [Pa]"),
    "friction_factor": (("pipe_analysis", "friction_factor_cfd"), "Darcy friction factor [-]"),
    "darcy_permeability_m2": (("pipe_analysis", "darcy_permeability_m2"), "Darcy permeability [m²]"),
}


@dataclass(frozen=True)
class GciResult:
    quantity: str
    h: tuple             # representative cell sizes, fine to coarse
    values: tuple        # solutions, fine to coarse
    ratios: tuple        # (r21, r32) for three grids, (r21,) for two
    order: float         # observed (three grids) or assumed (two grids) order of accuracy
    order_source: str    # "observed" or "assumed"
    extrapolated: float
    approx_relative_error: float      # |(phi1-phi2)/phi1|
    extrapolated_relative_error: float
    gci_fine: float                   # fraction of the fine-grid value
    gci_coarse: float                 # NaN with two grids
    asymptotic_ratio: float           # ~1 when the grids are in the asymptotic range; NaN with two grids
    monotonic: bool
    status: str                       # "ok", "oscillatory", "diverging", "non-asymptotic", "unresolved", "two-grid"
    notes: tuple

    def as_dict(self):
        data = {key: (list(value) if isinstance(value, tuple) else value) for key, value in self.__dict__.items()}
        return {key: (None if isinstance(v, float) and not math.isfinite(v) else v) for key, v in data.items()}


def representative_cell_size(volume, cells):
    """Cell size h = (V/N)^(1/3) for a 3-D mesh (Celik et al. eq. 1)."""
    if volume <= 0 or cells <= 0:
        raise ValueError("Volume and cell count must be positive.")
    return (volume / cells) ** (1.0 / 3.0)


def _solve_order(r21, r32, ratio, sign):
    """Fixed-point iteration of Celik eq. 3-5 for the apparent order p."""
    p = abs(math.log(abs(ratio))) / math.log(r21)
    for _ in range(200):
        q = 0.0 if abs(r21 - r32) < 1e-12 else math.log((r21 ** p - sign) / (r32 ** p - sign))
        new = abs(math.log(abs(ratio)) + q) / math.log(r21)
        if abs(new - p) < 1e-10:
            return new
        p = new
    return p


def gci_three_grids(h, values, quantity="value", formal_order=None):
    """GCI from three solutions ordered fine → coarse; ``h`` are the matching representative sizes."""
    if len(h) != 3 or len(values) != 3:
        raise ValueError("Three grids are required.")
    h1, h2, h3 = (float(v) for v in h)
    f1, f2, f3 = (float(v) for v in values)
    if not (0 < h1 < h2 < h3):
        raise ValueError("Cell sizes must increase from the fine to the coarse mesh.")
    r21, r32 = h2 / h1, h3 / h2
    notes = []
    if min(r21, r32) < MIN_REFINEMENT_RATIO:
        notes.append(f"Refinement ratio {min(r21, r32):.2f} is below the recommended {MIN_REFINEMENT_RATIO}; differences may be noise.")
    e21, e32 = f2 - f1, f3 - f2
    scale = max(abs(f1), abs(f2), abs(f3), 1e-300)
    if abs(e21) <= 1e-9 * scale or abs(e32) <= 1e-9 * scale:
        return GciResult(quantity, (h1, h2, h3), (f1, f2, f3), (r21, r32), float("nan"), "observed", f1, 0.0, 0.0, 0.0, 0.0,
                         float("nan"), True, "unresolved",
                         tuple(notes + ["Solutions are identical within round-off; the result is mesh independent to the solver precision."]))
    ratio = e32 / e21
    sign = 1.0 if ratio > 0 else -1.0
    monotonic = ratio > 0
    order = _solve_order(r21, r32, ratio, sign)
    status = "ok"
    if not monotonic:
        status = "oscillatory"
        notes.append("Oscillatory convergence: the observed order and GCI are only indicative. Use a finer mesh series.")
    elif abs(ratio) < 1:
        status = "diverging"
        notes.append("The fine-pair change is larger than the coarse-pair change, so the series is not converging.")
    if not math.isfinite(order) or order <= 0:
        status = "unresolved"
        notes.append("Observed order could not be determined.")
        order = float(formal_order) if formal_order else 1.0
    elif formal_order and (order > 2 * formal_order or order < 0.5 * formal_order):
        notes.append(f"Observed order {order:.2f} differs strongly from the formal order {formal_order:g}; treat the GCI cautiously.")
        if status == "ok":
            status = "non-asymptotic"
    p = order
    ext21 = (r21 ** p * f1 - f2) / (r21 ** p - 1)
    ea21 = abs((f1 - f2) / f1) if f1 else float("inf")
    ea32 = abs((f2 - f3) / f2) if f2 else float("inf")
    eext21 = abs((ext21 - f1) / ext21) if ext21 else float("inf")
    gci21 = SAFETY_THREE_GRIDS * ea21 / (r21 ** p - 1)
    gci32 = SAFETY_THREE_GRIDS * ea32 / (r32 ** p - 1)
    asymptotic = gci32 / (r21 ** p * gci21) if gci21 else float("nan")
    if status == "ok" and math.isfinite(asymptotic) and not 0.9 <= asymptotic <= 1.1:
        status = "non-asymptotic"
        notes.append(f"Asymptotic ratio {asymptotic:.2f} is not close to 1; the meshes may not be in the asymptotic range.")
    if status == "ok":
        notes.append(f"Fine-grid value {f1:.6g} ± {gci21:.2%} (GCI); extrapolated value {ext21:.6g}.")
    return GciResult(quantity, (h1, h2, h3), (f1, f2, f3), (r21, r32), p, "observed", ext21, ea21, eext21, gci21, gci32,
                     asymptotic, monotonic, status, tuple(notes))


def gci_two_grids(h, values, order, quantity="value"):
    """Conservative GCI from two solutions using an assumed formal order and a safety factor of 3."""
    if len(h) != 2 or len(values) != 2:
        raise ValueError("Two grids are required.")
    h1, h2 = float(h[0]), float(h[1])
    f1, f2 = float(values[0]), float(values[1])
    if not 0 < h1 < h2 or order <= 0:
        raise ValueError("Cell sizes must increase from fine to coarse and the order must be positive.")
    r = h2 / h1
    ea = abs((f1 - f2) / f1) if f1 else float("inf")
    ext = (r ** order * f1 - f2) / (r ** order - 1)
    gci = SAFETY_TWO_GRIDS * ea / (r ** order - 1)
    nan = float("nan")
    return GciResult(quantity, (h1, h2), (f1, f2), (r,), float(order), "assumed", ext, ea,
                     abs((ext - f1) / ext) if ext else float("inf"), gci, nan, nan, True, "two-grid",
                     (f"Two meshes only: GCI uses an assumed order of {order:g} and a safety factor of 3. Add a third mesh for an observed order.",))


def refined_cells(base_cells, ratio=1.5, count=3, step=2):
    """Cells-across values from fine to coarse; each coarser level is about ``ratio`` times larger in cell size."""
    if count < 2:
        raise ValueError("At least two meshes are required.")
    levels = [int(base_cells)]
    for _ in range(count - 1):
        coarser = max(8, int(round(levels[-1] / ratio / step)) * step)
        if coarser >= levels[-1]:
            raise ValueError("The fine mesh is too coarse to build a refinement series; increase cells across the diameter.")
        levels.append(coarser)
    return levels


def extract_quantity(summary, quantity):
    if quantity not in QUANTITIES:
        raise ValueError(f"Unknown quantity {quantity!r}. Choose from {', '.join(QUANTITIES)}.")
    value = summary
    for key in QUANTITIES[quantity][0]:
        if not isinstance(value, dict) or key not in value:
            raise ValueError(f"{QUANTITIES[quantity][1]} is not available in this analysis (empty pipe vs TPMS fill).")
        value = value[key]
    return float(value)


def nominal_volume(specimen, flow):
    """Bounding-cylinder volume used to size cells identically on every level (m³)."""
    length = (flow.inlet_length_mm + flow.test_length_mm(specimen) + flow.outlet_length_mm) * 1e-3
    return math.pi / 4 * (flow.diameter_mm * 1e-3) ** 2 * length


def run_flow_gci(specimen, flow, directory, quantity="pressure_drop_specimen_Pa", ratio=1.5, levels=3,
                 execute=None, progress=None, formal_order=None):
    """Solve ``flow`` on ``levels`` refined meshes (fine → coarse) and return the GCI result.

    ``execute(specimen, flow, folder)`` must return a flow summary; it defaults to ``execute_flow_case``.
    A failed level raises with the level name so the user can see which mesh did not solve.
    """
    if execute is None:
        from flow_analysis import execute_flow_case as execute
    directory = Path(directory)
    cells = refined_cells(flow.cells_across, ratio, levels)
    volume = nominal_volume(specimen, flow)
    sizes, values, rows = [], [], []
    for index, across in enumerate(cells):
        if progress:
            progress(f"GCI level {index + 1}/{len(cells)}: {across} cells across the diameter")
        level_flow = replace(flow, cells_across=across)
        folder = directory / f"gci_level{index + 1}_n{across}"
        try:
            summary = execute(specimen, level_flow, folder)
            value = extract_quantity(summary, quantity)
        except Exception as error:
            raise RuntimeError(f"GCI level {index + 1} ({across} cells across) failed: {error}") from error
        count = int(summary["mesh"]["fluid_cells"])
        h = representative_cell_size(volume, count)
        sizes.append(h)
        values.append(value)
        rows.append(dict(level=index + 1, cells_across=across, fluid_cells=count, h_mm=h * 1000, value=value,
                         status=summary.get("status", "")))
    # Representative sizes must be strictly increasing; guard against non-monotonic cell counts.
    if any(a >= b for a, b in zip(sizes, sizes[1:])):
        raise RuntimeError("Cell counts did not decrease with the coarser levels; the mesh series is not usable.")
    result = gci_three_grids(sizes, values, quantity, formal_order) if len(sizes) == 3 else gci_two_grids(sizes, values, formal_order or 2.0, quantity)
    if any(row["status"] == "not_converged" for row in rows):
        result = replace(result, notes=result.notes + ("At least one level did not meet its solver convergence criteria; the GCI is provisional.",))
    write_gci_report(directory, result, rows, flow)
    return result


def run_flow_gci_job(specimen, flow, directory):
    """Top-level entry point for ``AnalysisProcess``: runs the study and returns the report text."""
    from analysis_process import report_analysis_step
    quantity = "friction_factor" if flow.empty_pipe else "pressure_drop_specimen_Pa"
    return describe(run_flow_gci(specimen, flow, directory, quantity=quantity, progress=report_analysis_step))


def write_gci_report(directory, result, rows, flow):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    payload = dict(result=result.as_dict(), levels=rows, solver=flow.solver, convection=flow.convection,
                   label=QUANTITIES.get(result.quantity, ((), result.quantity))[1])
    (directory / "gci.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with (directory / "gci.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("level", "cells_across", "fluid_cells", "h_mm", result.quantity, "status"))
        for row in rows:
            writer.writerow((row["level"], row["cells_across"], row["fluid_cells"], row["h_mm"], row["value"], row["status"]))


def describe(result):
    """Compact plain-text summary for the UI and logs."""
    label = QUANTITIES.get(result.quantity, ((), result.quantity))[1]
    lines = [f"{label}: grid convergence ({result.status})"]
    for index, (h, value) in enumerate(zip(result.h, result.values), 1):
        lines.append(f"  mesh {index}: h = {h * 1000:.4g} mm, value = {value:.6g}")
    lines.append(f"  {result.order_source} order p = {result.order:.3g}, extrapolated = {result.extrapolated:.6g}")
    lines.append(f"  GCI fine = {result.gci_fine:.2%}" + ("" if math.isnan(result.asymptotic_ratio) else f", asymptotic ratio = {result.asymptotic_ratio:.3f}"))
    lines.extend("  - " + note for note in result.notes)
    return "\n".join(lines)
