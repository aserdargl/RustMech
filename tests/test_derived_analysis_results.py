import csv
from dataclasses import asdict, replace
import json

import numpy as np
import pytest

from damask_fft import directional_elastic_constants
from flow_analysis import FlowSettings, fit_porous_resistance, hydraulic_metrics
from mechanical_results import compression_result_metrics, export_graphs
from model import Settings
from thermal_analysis import ThermalSettings, thermal_performance_metrics


def test_fft_engineering_shear_and_poisson_conventions():
    young, poisson = 210_000., .28
    shear = young / (2 * (1 + poisson))
    lam = 2 * shear * poisson / (1 - 2 * poisson)
    matrix = np.diag([2 * shear] * 3 + [shear] * 3)
    matrix[:3, :3] += lam
    values = directional_elastic_constants(matrix)
    assert [values[f"E_{axis}_MPa"] for axis in "xyz"] == pytest.approx([young] * 3)
    assert [values[f"G_{axis}_MPa"] for axis in ("yz", "xz", "xy")] == pytest.approx([shear] * 3)
    assert [v for k, v in values.items() if k.startswith("nu_")] == pytest.approx([poisson] * 6)
    assert values["universal_anisotropy_index"] == pytest.approx(0, abs=1e-12)
    compliance = np.linalg.inv(matrix)
    compliance[0, 0] *= 2
    directional = directional_elastic_constants(np.linalg.inv(compliance))
    assert directional["nu_xy"] == pytest.approx(poisson / 2)
    assert directional["nu_yx"] == pytest.approx(poisson)
    assert directional["young_modulus_axis_ratio"] == pytest.approx(2)
    assert directional["universal_anisotropy_index"] > 0
    assert directional_elastic_constants(np.ones((6, 6))) == {}


def test_compression_exports_metrics_and_handles_short_and_archived_curves(tmp_path):
    folder = tmp_path / "04_Graphs"
    folder.mkdir()
    settings = Settings(size=10)
    strain = np.array([0, .1, .2, .26, .31, .4, .5])
    stress = np.array([0, 15, 20, 20, 20, 20, 40])
    history = np.column_stack((strain, 10 * strain, 100 * stress, 100 * stress, np.zeros(len(strain))))
    report = export_graphs(history, settings, folder, images=False)
    assert report["plateau_stress_MPa"] == pytest.approx(20)
    assert report["densification_strain"] == pytest.approx(.43)
    assert report["densification_stress_MPa"] == pytest.approx(26)
    with (folder / "compression_metrics.csv").open(newline="") as stream:
        assert float(next(csv.DictReader(stream))["densification_strain"]) == pytest.approx(.43)
    archived = {k: report[k] for k in ("energy_absorption_J", "volumetric_energy_absorption_J_cm3")}
    assert compression_result_metrics(tmp_path, {"settings": asdict(settings), "graphs": archived})[
        "densification_strain"] == pytest.approx(.43)
    plateau_only = export_graphs(history[:-1], settings, folder, images=False)
    assert plateau_only["plateau_stress_MPa"] == pytest.approx(20)
    assert plateau_only["densification_strain"] is None
    short = export_graphs(history[:4], settings, folder, images=False)
    assert short["plateau_stress_MPa"] is None
    with (folder / "compression_metrics.csv").open(newline="") as stream:
        assert next(csv.DictReader(stream))["plateau_stress_MPa"] == ""


@pytest.fixture
def flow_cases(tmp_path):
    config = FlowSettings(density=1000, dynamic_viscosity=.001)
    area, length = .0001, .02
    permeability, beta = 2e-8, 200.
    paths = []
    for index, speed in enumerate((.01, .02, .05, .07)):
        case = tmp_path / str(index)
        case.mkdir()
        path = case / "flow_case.json"
        paths.append(path)
        mesh = dict(solid_cells=2, inlet_area_m2=area, specimen_solid_fraction=.35)
        path.write_text(json.dumps(dict(specimen=asdict(Settings()), mesh=mesh,
                                        flow=asdict(replace(config, inlet_speed=speed)))), encoding="utf-8")
        pressure = length * (config.dynamic_viscosity * speed / permeability + config.density * beta * speed**2)
        (case / "flow_summary.json").write_text(json.dumps(dict(
            status="converged", mesh=mesh, specimen_pressure_planes_mm=[0, 20],
            inlet_flow_m3_s=area * speed, outlet_flow_m3_s=area * speed, pressure_drop_specimen_Pa=pressure,
            pressure_drop_total_Pa=2 * pressure, reynolds_diameter=10, mass_imbalance_fraction=0,
            maximum_speed_m_s=2 * speed, warnings=[])), encoding="utf-8")
    return paths, permeability, beta


def test_hydraulic_power_and_darcy_forchheimer_fit_from_saved_cases(tmp_path, flow_cases):
    assert hydraulic_metrics(10, .002, 4) == pytest.approx(
        {"channel_hydraulic_power_W": .02, "insert_hydraulic_power_W": .008})
    paths, permeability, beta = flow_cases
    with pytest.raises(ValueError, match="at least three"):
        fit_porous_resistance([paths[0], paths[0], paths[1]], tmp_path)
    values = fit_porous_resistance(paths, tmp_path)
    assert values["apparent_permeability_m2"] == pytest.approx(permeability, rel=1e-10)
    assert values["apparent_forchheimer_1_m"] == pytest.approx(beta)
    assert values["relative_fit_residual"] < 1e-12
    with (tmp_path / "darcy_forchheimer.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 4
    assert float(rows[0]["pressure_gradient_Pa_m"]) == pytest.approx(float(rows[0]["fitted_gradient_Pa_m"]))
    different = json.loads(paths[1].read_text(encoding="utf-8"))
    different["flow"]["specimen_offset_x_mm"] = 1
    paths[1].write_text(json.dumps(different), encoding="utf-8")
    with pytest.raises(ValueError, match="must match"):
        fit_porous_resistance(paths, tmp_path)


def test_flow_screen_accumulates_cases_from_separate_directories_and_reopens_fit(tmp_path, flow_cases, monkeypatch):
    from unittest.mock import MagicMock

    import pyvista as pv
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QWidget
    from flow_workspace import FlowWorkspace

    app = QApplication.instance() or QApplication([])
    paths, _, _ = flow_cases
    case = paths[0].parent
    grid = pv.ImageData(dimensions=(2, 2, 2)).cast_to_unstructured_grid()
    grid.save(case / "flow_result.vtu")
    choices = iter(paths[1:3])
    monkeypatch.setattr("flow_workspace.QFileDialog.getOpenFileName",
                        lambda *_: (str(next(choices)), ""))
    view = FlowWorkspace(Settings, preferences=QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat),
                         viewport_factory=lambda parent: MagicMock(interactor=QWidget(parent)))
    try:
        view.load_directory(case)
        assert not view.fit_resistance_button.isEnabled()
        assert "Hydraulic power" in view.results.text()
        for _ in range(2):
            view.add_resistance_case_button.click()
        assert view.fit_resistance_button.isEnabled()
        view.fit_resistance_button.click()
        assert (case / "darcy_forchheimer.csv").is_file()
        assert "200 m⁻¹" in view.resistance_result.text()
        view.load_directory(case)
        assert not view.fit_resistance_button.isEnabled()
        assert "200 m⁻¹" in view.resistance_result.text()
    finally:
        view.dispose()
        view.deleteLater()
        app.processEvents()


def test_transient_cht_metrics_with_explicit_reference_and_missing_denominators():
    flow = FlowSettings(density=1000, diameter_mm=6)
    thermal = ThermalSettings(water_conductivity=.6)
    row = dict(time_s=1, inlet_bulk_C=80, outlet_bulk_C=60, inlet_mass_kg_s=.02,
               outlet_mass_kg_s=.02, pressure_drop_Pa=100, interface_water_to_solid_W=40,
               interface_area_m2=.02, interface_wall_mean_C=50)
    values = thermal_performance_metrics(row, thermal, flow)
    assert values["apparent_h_W_m2_K"] == pytest.approx(100)
    assert values["apparent_Nusselt"] == pytest.approx(1)
    assert values["apparent_thermal_resistance_K_W"] == pytest.approx(.5)
    assert values["interface_mean_heat_flux_W_m2"] == pytest.approx(2000)
    assert values["interface_heat_to_hydraulic_power_ratio"] == pytest.approx(20_000)
    assert values["water_sensible_heat_loss_W"] == pytest.approx(.02 * thermal.water_cp * 20)
    assert thermal_performance_metrics(row | {"interface_wall_mean_C": 70.}, thermal, flow)[
        "apparent_h_W_m2_K"] is None
    assert thermal_performance_metrics(row | {"time_s": 0.}, thermal, flow)["apparent_Nusselt"] is None
