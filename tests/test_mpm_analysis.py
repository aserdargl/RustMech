from dataclasses import asdict, replace
import json
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pytest

from analysis_project import AnalysisProject
from local_kratos import local_mpm_python
from model import Settings
from mpm_analysis import (MPMSettings, PARTICLES_NAME, SUMMARY_NAME, execute_mpm,
                          prepare_mpm_case, read_mpm_result)
from remote_operations import ANALYSIS_OPERATIONS, required_capability
from remote_worker import _run_operation


GEOMETRY = Settings(size=2, cell_size=2, solid_fraction=40, cores=2)
INPUTS = MPMSettings(grid_mm=.4, stroke_mm=.05, duration_ms=.02, young_mpa=100.)


def test_mpm_setup_uses_the_tpms_volume_and_independent_grid(tmp_path):
    case = prepare_mpm_case(GEOMETRY, INPUTS, tmp_path)
    assert case['grid_mm'] == pytest.approx(.4)
    assert case['point_mm'] == pytest.approx(.2)
    assert case['sampled_fraction'] == pytest.approx(.4, abs=.03)
    assert case['dt_s'] * case['steps'] == pytest.approx(.00002)
    assert 'MPMUpdatedLagrangian3D8N' in (tmp_path / 'Body.mdpa').read_text()
    assert 'Element3D8N' in (tmp_path / 'Grid.mdpa').read_text()
    material = json.loads((tmp_path / 'Materials.json').read_text())['properties'][0]['Material']
    assert material['Variables']['YOUNG_MODULUS'] == 1e8
    assert material['Variables']['DENSITY'] == 1200.


@pytest.mark.parametrize('geometry, options', [
    (replace(GEOMETRY, imported_geometry_path='specimen.vtp'), INPUTS),
    (replace(GEOMETRY, structure_family='porous'), INPUTS),
    (GEOMETRY, replace(INPUTS, stroke_mm=2.)),
    (GEOMETRY, replace(INPUTS, points_per_axis=2.5)),
    (GEOMETRY, replace(INPUTS, grid_mm=.001)),
])
def test_mpm_rejects_unsupported_inputs_before_writing(tmp_path, geometry, options):
    with pytest.raises(ValueError):
        prepare_mpm_case(geometry, options, tmp_path)
    assert not tuple(tmp_path.iterdir())


def test_project_keeps_mpm_material_separate():
    project = AnalysisProject.capture(GEOMETRY)
    project.capture_mpm(INPUTS)
    project.active_study = 'mpm'
    restored = AnalysisProject.from_dict(project.to_dict())
    assert restored.active_study == 'mpm'
    assert restored.mpm_settings() == INPUTS
    assert restored.workspace_settings()[0].young_modulus == GEOMETRY.young_modulus
    assert AnalysisProject.capture(GEOMETRY).mpm_settings() == MPMSettings()


def test_mpm_truba_worker_routes_independently_of_fe_mesh(tmp_path, monkeypatch):
    request = dict(operation='mpm_solve', settings=asdict(replace(GEOMETRY, mesh_type='TET10')),
                   mpm=asdict(INPUTS))
    assert required_capability(request) == 'mpm'
    assert request['operation'] in ANALYSIS_OPERATIONS
    monkeypatch.chdir(tmp_path)
    output = tmp_path / 'output'
    output.mkdir()

    def solve(geometry, options, case, *, remote, on_progress):
        assert remote and options == INPUTS and geometry.mesh_type == 'TET10'
        case.mkdir()
        for name in (PARTICLES_NAME, SUMMARY_NAME, 'mpm_force_displacement.csv', 'mpm_solver.log'):
            (case / name).touch()

    with patch('remote_worker.probe', return_value={'mpm': True, 'mesh_types': ['HEX8']}), \
            patch('mpm_analysis.execute_mpm', side_effect=solve):
        result = _run_operation(request, output)
    assert result['case'] == '.'
    assert (output / PARTICLES_NAME).is_file()
    assert (output / SUMMARY_NAME).is_file()
    assert (output / 'mpm_solver.log').is_file()


@pytest.mark.skipif(local_mpm_python() is None, reason='Local Kratos MPM runtime is not installed')
def test_real_tpms_mpm_produces_platen_reactions_and_particles(tmp_path):
    import pyvista as pv

    execute_mpm(GEOMETRY, INPUTS, tmp_path)
    summary, curve = read_mpm_result(tmp_path)
    assert summary['execution'] == 'Local'
    assert curve[-1, 1] == pytest.approx(INPUTS.stroke_mm)
    assert np.max(curve[:, 2]) > 0 and np.max(curve[:, 3]) > 0
    assert summary['maximum_overlap_mm'] < summary['point_mm']
    cloud = pv.read(Path(tmp_path) / PARTICLES_NAME)
    assert cloud.n_points == summary['points']
    assert np.max(np.abs(cloud.point_data['cauchy_stress_mpa'])) > 0


def test_mpm_workspace_enter_commits_without_running_and_persists(tmp_path):
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget
    from mpm_workspace import MPMWorkspace
    from window_state import WindowStateStore

    app = QApplication.instance() or QApplication([])
    preferences = QSettings(str(tmp_path / 'workspace.ini'), QSettings.Format.IniFormat)
    parent = QWidget()
    workspace = MPMWorkspace(lambda: GEOMETRY, parent, preferences)
    workspace.resize(900, 500)
    workspace.show()
    app.processEvents()
    control = workspace.controls['stroke_mm']
    with patch('mpm_workspace.AnalysisProcess') as start:
        control.field.setFocus()
        control.field.selectAll()
        QTest.keyClicks(control.field, '0.125')
        QTest.keyClick(control.field, Qt.Key.Key_Return)
        assert workspace.collect().stroke_mm == .125
        start.assert_not_called()
    assert workspace.splitter in WindowStateStore.splitters(parent)
    restored = MPMWorkspace(lambda: GEOMETRY, parent, preferences)
    assert restored.collect().stroke_mm == .125


def test_mpm_workspace_submits_via_truba_adapter(tmp_path):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QWidget
    from mpm_workspace import MPMWorkspace
    from truba_remote import run_remote_operation

    app = QApplication.instance() or QApplication([])
    preferences = QSettings(str(tmp_path / 'workspace.ini'), QSettings.Format.IniFormat)
    parent = QWidget()
    workspace = MPMWorkspace(lambda: GEOMETRY, parent, preferences)
    remote = Mock(requested=True)
    remote.require_analysis.return_value = 'allocation', 'authentication'
    workspace.remote_controller = remote
    with patch('mpm_workspace.AnalysisProcess') as start:
        workspace.run()
        start.assert_called_once_with(run_remote_operation, {
            'operation': 'mpm_solve', 'settings': asdict(GEOMETRY), 'mpm': asdict(MPMSettings()),
        }, 'allocation', remote_auth='authentication')
        remote.require_analysis.assert_called_once_with('mpm')
    workspace.dispose()
