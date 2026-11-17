"""Analytical pipe-flow references and post-processing of CFD pressure profiles (SI unless noted)."""
from __future__ import annotations

import math

import numpy as np

LAMINAR_LIMIT = 2300.0
TURBULENT_LIMIT = 4000.0


def reynolds(density, speed, diameter_m, viscosity):
    return density * speed * diameter_m / viscosity


def regime(re):
    return "laminar" if re < LAMINAR_LIMIT else "transitional" if re < TURBULENT_LIMIT else "turbulent"


def darcy_friction_factor(re):
    """Smooth-pipe Darcy factor: 64/Re (laminar), Colebrook–White (turbulent); ``None`` in transition."""
    if re <= 0 or not math.isfinite(re):
        raise ValueError("Reynolds number must be positive and finite.")
    if re < LAMINAR_LIMIT:
        return 64.0 / re
    if re < TURBULENT_LIMIT:
        return None
    inv_sqrt = 1.0 / math.sqrt(0.316 * re ** -0.25)
    for _ in range(60):
        updated = -2.0 * math.log10(2.51 * inv_sqrt / re)
        if abs(updated - inv_sqrt) < 1e-13:
            break
        inv_sqrt = updated
    return 1.0 / inv_sqrt ** 2


def developing_length_mm(re, diameter_mm):
    """Entrance length to fully developed flow: 0.05 Re D (laminar), 4.4 Re^(1/6) D (turbulent)."""
    return (0.05 * re if re < TURBULENT_LIMIT else 4.4 * re ** (1 / 6)) * diameter_mm


def hagen_poiseuille_gradient(viscosity, speed, diameter_m):
    """Fully developed laminar pressure gradient |dp/dz| / (Pa/m)."""
    return 32.0 * viscosity * speed / diameter_m ** 2


def friction_velocity(friction_factor, density, speed):
    return speed * math.sqrt(friction_factor / 8.0)


def estimate_y_plus(re, density, speed, viscosity, first_centre_mm):
    f = darcy_friction_factor(re) if re < LAMINAR_LIMIT or re >= TURBULENT_LIMIT else 0.316 * re ** -0.25
    return density * friction_velocity(f, density, speed) * first_centre_mm * 1e-3 / viscosity


def fit_gradient(z_mm, pressure_pa, start_mm, end_mm):
    """Least-squares dp/dz in Pa/m over ``start_mm <= z <= end_mm``; returns ``(slope, r2, n)`` or ``None``."""
    z, p = np.asarray(z_mm, float), np.asarray(pressure_pa, float)
    mask = (z >= start_mm) & (z <= end_mm)
    if mask.sum() < 5:
        return None
    zz, pp = z[mask] * 1e-3, p[mask]
    slope, intercept = np.polyfit(zz, pp, 1)
    residual = pp - (slope * zz + intercept)
    total = float(np.sum((pp - pp.mean()) ** 2))
    r2 = 1.0 - float(np.sum(residual ** 2)) / total if total > 0 else 1.0
    return float(slope), r2, int(mask.sum())


def analyze_empty_pipe(flow, effective_diameter_mm, z_mm, pressure_pa, outlet_margin_mm):
    """Compare the CFD pressure gradient of an empty pipe with Hagen–Poiseuille / Colebrook."""
    d = effective_diameter_mm * 1e-3
    u = flow.inlet_speed
    re = reynolds(flow.density, u, d, flow.dynamic_viscosity)
    z = np.asarray(z_mm, float)
    entrance = developing_length_mm(re, effective_diameter_mm)
    start = z.min() + 1.2 * entrance
    end = z.max() - outlet_margin_mm
    fitted = fit_gradient(z, pressure_pa, start, end)
    notes, developed = [], True
    if fitted is None:
        developed = False
        start = z.min() + 0.5 * (z.max() - z.min())
        fitted = fit_gradient(z, pressure_pa, start, end)
        notes.append("Pipe is shorter than 1.2 entrance lengths; the fitted gradient still includes developing flow. Lengthen the inlet extension or pipe.")
    result = dict(kind="empty_pipe", reynolds=re, regime=regime(re), developing_length_mm=entrance,
                  fully_developed_fit=developed, notes=notes)
    f_theory = darcy_friction_factor(re)
    if f_theory is None:
        notes.append("Transitional Reynolds number: no reliable closed-form friction factor.")
    elif re < LAMINAR_LIMIT:
        result["gradient_theory_Pa_m"] = hagen_poiseuille_gradient(flow.dynamic_viscosity, u, d)
    else:
        result["gradient_theory_Pa_m"] = f_theory * flow.density * u * u / (2 * d)
    result["friction_factor_theory"] = f_theory
    if fitted is not None:
        slope, r2, count = fitted
        gradient = -slope
        result.update(gradient_cfd_Pa_m=gradient, fit_r2=r2, fit_points=count,
                      fit_range_mm=[float(start), float(end)],
                      friction_factor_cfd=gradient * 2 * d / (flow.density * u * u),
                      wall_shear_stress_Pa=gradient * d / 4)
        if f_theory:
            result["friction_factor_error_pct"] = 100 * (result["friction_factor_cfd"] / f_theory - 1)
    return result


def analyze_porous(flow, porosity, length_mm, pressure_drop_pa, effective_diameter_mm):
    """Apparent Darcy permeability and friction of a TPMS-filled pipe section (includes entrance/exit losses)."""
    d = effective_diameter_mm * 1e-3
    u = flow.inlet_speed
    length = length_mm * 1e-3
    gradient = pressure_drop_pa / length if length > 0 else float("nan")
    result = dict(kind="tpms_filled_pipe", porosity=porosity, pressure_gradient_Pa_m=gradient,
                  friction_factor_apparent=gradient * 2 * d / (flow.density * u * u) if math.isfinite(gradient) else None,
                  notes=[])
    if pressure_drop_pa > 0:
        k = flow.dynamic_viscosity * u * length / pressure_drop_pa
        result["darcy_permeability_m2"] = k
        result["permeability_reynolds"] = flow.density * u * math.sqrt(k) / flow.dynamic_viscosity
        if result["permeability_reynolds"] > 1:
            result["notes"].append("Permeability Reynolds number exceeds 1: inertial (Forchheimer) losses are present, so the Darcy permeability depends on speed.")
    else:
        result["notes"].append("Non-positive specimen pressure drop; permeability was not computed.")
    return result


def radial_profile(r_mm, axial_velocity, weights, radius_mm, bins=24):
    """Volume-weighted axial velocity versus radius; returns rows ``(r_mm, mean_u)`` for non-empty bins."""
    r = np.asarray(r_mm, float)
    edges = np.linspace(0, radius_mm, bins + 1)
    index = np.clip(np.digitize(r, edges) - 1, 0, bins - 1)
    w = np.asarray(weights, float)
    num = np.bincount(index, weights=w * axial_velocity, minlength=bins)
    den = np.bincount(index, weights=w, minlength=bins)
    centres = np.bincount(index, weights=w * r, minlength=bins)
    ok = den > 0
    return np.column_stack((centres[ok] / den[ok], num[ok] / den[ok]))
