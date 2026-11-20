"""Restored and explicitly extended TRUBA studies use the same mesh plan."""

from dataclasses import asdict, replace
import base64
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import QApplication, QWidget

from convergence_execution import Level
from mesh_convergence import MeshConvergenceWidget
from model import Settings
from truba_jobs import HOST, write_record
from truba_remote import RemoteContext, resume_convergence_levels, run_remote_level_pair


class Connection(QWidget):
    connection_changed = Signal(bool)


@pytest.fixture
def study(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    connection = Connection()
    connection.state_dir = tmp_path
    auth = SimpleNamespace(username="owner")
    context = RemoteContext(partition="hamsi", cores=56, memory_mb=136000, parallel_jobs=2)
    controller = MagicMock(requested=True, ready=True, closing=False, capabilities={"mesh_types": ["HEX8"]})
    controller.preferences = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    controller.window.truba_dialog = connection
    controller.auth.return_value = None
    controller.require_convergence.return_value = context, auth
    parent = QWidget()
    parent.remote_controller = controller
    settings = Settings(size=1, cell_size=1, mesh_resolution=.2, solver_backend="kratos",
                        mesh_type="HEX8", self_contact_mode="disabled")
    launch = MagicMock(side_effect=lambda *_args, **_kwargs: MagicMock(done=lambda: False))
    monkeypatch.setattr("mesh_convergence.AnalysisProcess", launch)
    widgets = []

    def open_study():
        widget = MeshConvergenceWidget(settings, None, None, parent)
        widgets.append(widget)
        return widget

    yield open_study, controller, connection, launch, auth, context, settings
    for widget in widgets:
        widget.dispose()
    parent.deleteLater()
    connection.deleteLater()
    app.processEvents()


def test_add_two_repeatedly_while_first_pair_is_pending(study):
    open_study, _, _, launch, _, _, _ = study
    widget = open_study()
    widget.max_levels.setValue(6)
    widget.start()
    assert widget.append_button.isEnabled()
    widget.append_button.click()
    widget.append_button.click()
    assert not widget.append_button.isEnabled()
    assert list(widget.remote_jobs) == [(0, 1), (2, 3), (4, 5)]
    calls = launch.call_args_list
    assert all(call.args[0] is run_remote_level_pair for call in calls)
    assert [level.mesh_resolution for call in calls for level in call.args[1]] == pytest.approx(
        [.2 / widget.factor.value() ** i for i in range(6)])
    plans = [call.kwargs["remote_options"]["study"] for call in calls]
    assert [plan["row"] for plan in plans] == [0, 2, 4]
    assert len({plan["id"] for plan in plans}) == 1
    assert widget.results.item(5, 11).text() == "28"


def save_job(connection, context, settings, job_id, sizes, state, plan=None):
    directory = connection.state_dir / job_id
    levels = [replace(settings, mesh_resolution=size, cores=28) for size in sizes]
    request = dict(settings=asdict(levels[0]), selected=[0, 1, 2], max_elements=5000,
                   convergence_steps=[dict(settings=asdict(level), memory_mb=68000) for level in levels])
    record = dict(host=HOST, kind="rustmech_remote", operation="convergence_level",
                  username="owner", job_id=job_id, state=state, context=asdict(context),
                  script_name=f"RM-{job_id}.slurm", request_id=job_id, source_sha256="hash",
                  local_directory=str(directory), level_resources=[dict(cores=28, memory_mb=68000)] * 2)
    if plan:
        record["convergence_study"] = plan
    path = connection.state_dir / "jobs" / f"{job_id}.json"
    write_record(directory / "request.json", request)
    write_record(path, record)
    write_record(directory / "remote_job.json", record)
    if state == "downloaded":
        result = dict(request_id=job_id, source_sha256="hash", slurm_job_id=job_id,
                      levels=[dict(level=asdict(Level(level, elements=500, metrics=[1, 2, 3],
                                                     status="completed"))) for level in levels])
        write_record(directory / "output" / "result.json", result)
    return path


def test_reopen_shows_only_active_pairs_and_resumes_by_known_id(study):
    open_study, controller, connection, launch, auth, context, settings = study
    plan = dict(id="one-study", row=0, kinds=["Starting mesh", "Refinement"], factor=1.5,
                minimum=.02, max_levels=8)
    save_job(connection, context, settings, "100", [.2, .133], "downloaded", plan)
    path = save_job(connection, context, settings, "101", [.089, .059], "PENDING|Priority",
                    {**plan, "row": 2, "kinds": ["Refinement"] * 2})
    save_job(connection, context, settings, "102", [.2, .15], "CANCELLED by 123")
    widget = open_study()
    launch.assert_not_called()
    assert widget.history_combo.count() == 2
    assert widget.history_combo.currentText().endswith("Slurm 101")
    assert [row for row in range(widget.results.rowCount()) if not widget.results.isRowHidden(row)] == [2, 3]
    assert widget.rows[0].status == "completed"
    assert widget.rows[0].elements == 500
    assert widget.results.item(3, 8).text() == "101"
    assert widget.results.item(3, 9).text() == "RM-101.slurm"
    assert widget.results.item(3, 11).text() == "28"
    assert widget.results.item(3, 12).text() == "66.41"
    assert widget.results.item(3, 14).text() == "hamsi"
    controller.auth.return_value = SimpleNamespace(username="someone-else")
    connection.connection_changed.emit(True)
    launch.assert_not_called()
    controller.auth.return_value = auth
    connection.connection_changed.emit(True)
    launch.assert_called_once_with(resume_convergence_levels, path, remote_auth=auth)
    widget.append_button.click()
    call = launch.call_args
    assert call.args[0] is run_remote_level_pair
    assert [level.mesh_resolution for level in call.args[1]] == pytest.approx([.059 / 1.5, .059 / 1.5**2])
    assert call.kwargs["remote_options"]["study"]["id"] == "one-study"


def test_previously_running_pair_disappears_when_slurm_reports_completion(study):
    open_study, controller, connection, launch, auth, context, settings = study
    path = save_job(connection, context, settings, "100", [.2, .15], "RUNNING")
    worker = MagicMock()
    worker.done.return_value = False
    launch.side_effect = None
    launch.return_value = worker
    controller.auth.return_value = auth
    widget = open_study()
    assert not widget.results.isRowHidden(0)
    record = json.loads(path.read_text())
    record.update(state="downloading", slurm_status="COMPLETED|0:0")
    write_record(path, record)
    worker.remote_status.return_value = record
    widget._poll_remote_batch()
    assert all(widget.results.isRowHidden(row) for row in range(2))
    record = dict(record, state="downloaded")
    write_record(path, record)
    worker.remote_status.return_value = record
    worker.result.return_value = [Level(level.settings, status="completed") for level in widget.rows]
    worker.done.return_value = True
    widget._poll_remote_batch()
    assert widget.history_combo.count() == 0
    assert not widget.export_button.isEnabled()


def test_reopen_with_only_finished_studies_keeps_the_table_empty(study):
    open_study, _, connection, launch, _, context, settings = study
    save_job(connection, context, settings, "100", [.2, .15], "downloaded")
    path = save_job(connection, context, settings, "101", [.2, .15], "interrupted")
    record = json.loads(path.read_text())
    record["slurm_status"] = "TIMEOUT|0:0"
    write_record(path, record)
    widget = open_study()
    assert widget.results.rowCount() == 0
    assert widget.history_combo.count() == 0
    launch.assert_not_called()


def test_pending_truba_study_leaves_local_setup_editable_and_can_be_opened_explicitly(study):
    open_study, controller, connection, launch, _, context, settings = study
    controller.requested = controller.ready = False
    save_job(connection, context, settings, "100", [.15, .1], "PENDING|Priority")
    widget = open_study()
    assert not widget.run_on_truba.isChecked()
    assert widget.run_on_truba.isEnabled()
    assert widget.solver_combo.isEnabled()
    assert widget.linear_solver_combo.isEnabled()
    assert widget.start_size.isEnabled()
    assert widget.run_button.isEnabled()
    widget.start_size.setValue(.25)
    widget._restore_saved_study(1)
    assert widget.saved_jobs
    assert not widget.start_size.isEnabled()
    assert widget.history_combo.isEnabled()
    widget._restore_saved_study(0)
    assert not widget.saved_jobs
    assert not widget.run_on_truba.isChecked()
    assert widget.start_size.value() == pytest.approx(.25)
    assert widget.run_button.isEnabled()
    launch.assert_not_called()
    widget.run_button.click()
    assert launch.call_args.args[0].__name__ == "_solve_level_process"
    assert launch.call_args.args[1].mesh_resolution == pytest.approx(.25)


def test_returning_to_new_study_detaches_only_the_remote_tracker(study):
    open_study, controller, connection, launch, auth, context, settings = study
    save_job(connection, context, settings, "100", [.2, .15], "RUNNING")
    controller.auth.return_value = auth
    widget = open_study()
    worker = widget.future
    widget._restore_saved_study(0)
    worker.detach_remote.assert_called_once()
    worker.force_stop.assert_not_called()
    assert widget.future is None
    assert widget.run_on_truba.isEnabled()
    assert widget.run_button.isEnabled()
    assert widget.history_combo.count() == 2


@pytest.mark.parametrize("accounting", [None, "COMPLETED|0:0"])
def test_interrupted_tracking_resumes_the_same_job_without_marking_the_mesh_failed(study, accounting):
    open_study, controller, connection, launch, auth, context, settings = study
    path = save_job(connection, context, settings, "100", [.2, .15], "RUNNING")
    record = json.loads(path.read_text())
    local_path = connection.state_dir / "100" / "remote_job.json"
    worker = MagicMock()
    worker.done.return_value = False
    launch.side_effect = None
    launch.return_value = worker
    controller.auth.return_value = auth
    widget = open_study()
    record.update(state="interrupted; resume using this job record", last_queue_state="RUNNING",
                  slurm_status=accounting)
    write_record(path, record)
    write_record(local_path, record)
    worker.remote_status.return_value = record
    worker.done.return_value = True
    worker.result.side_effect = ConnectionError("SSH closed")
    widget._poll_remote_batch()
    assert widget.future is None
    assert widget.history_combo.count() == 2
    assert widget.tracking_retry.isActive()
    assert not widget.remote_outcomes
    assert all(row.status == "TRUBA tracking paused" for row in widget.rows)
    assert not any(widget.results.isRowHidden(row) for row in range(2))
    assert widget.results.item(0, 8).text() == "100"
    assert widget.results.item(0, 10).text() == (accounting or "RUNNING")
    next_worker = MagicMock()
    next_worker.done.return_value = False
    launch.return_value = next_worker
    connection.connection_changed.emit(True)
    launch.assert_called_with(resume_convergence_levels, local_path, remote_auth=auth)
    assert widget.future is next_worker


def test_switch_between_previously_submitted_active_studies(study):
    open_study, controller, connection, launch, auth, context, settings = study
    paths = {
        "100": save_job(connection, context, settings, "100", [.2, .15], "RUNNING"),
        "101": save_job(connection, context, settings, "101", [.18, .12], "PENDING|Priority"),
    }
    controller.auth.return_value = auth
    widget = open_study()
    first_worker = widget.future
    assert widget.history_combo.isEnabled()
    index = 3 - widget.history_combo.currentIndex()
    job_id = widget.history_combo.itemData(index)[1].removeprefix("legacy:")
    widget.history_combo.setCurrentIndex(index)
    widget.history_combo.activated.emit(index)
    first_worker.detach_remote.assert_called_once()
    launch.assert_called_with(resume_convergence_levels, paths[job_id], remote_auth=auth)
    assert widget.results.item(0, 8).text() == job_id


def test_legacy_pair_keeps_its_id_when_extended(study):
    open_study, controller, connection, launch, auth, context, settings = study
    save_job(connection, context, settings, "100", [.2, .16], "RUNNING")
    controller.auth.return_value = auth
    widget = open_study()
    assert widget.results.rowCount() == 2
    assert widget.factor.value() == 1.25
    widget.append_button.click()
    submitted = launch.call_args.kwargs["remote_options"]["study"]
    assert submitted["id"] == "legacy:100"
    assert submitted["row"] == 2


def test_restored_manual_plan_reuses_the_uploaded_specimen(study):
    open_study, controller, connection, launch, auth, context, settings = study
    original = connection.state_dir / "original.vtp"
    plan = dict(id="manual-study", automatic=False, minimum=.03, max_levels=6,
                manual_sizes=[.2, .15, .1, .08, .05, .02])
    settings = replace(settings, mesh_type="TET4", local_mesh_size=.1,
                       imported_geometry_path=str(original))
    path = save_job(connection, context, settings, "200", [.2, .15], "RUNNING", plan)
    directory = connection.state_dir / path.stem
    request_path = directory / "request.json"
    request = json.loads(request_path.read_text())
    request["imported_geometry_vtp"] = base64.b64encode(b"original specimen").decode()
    write_record(request_path, request)
    controller.auth.return_value = auth
    widget = open_study()
    widget.append_button.click()
    levels = launch.call_args.args[1]
    assert [level.mesh_resolution for level in levels] == [.1, .08]
    assert [level.local_mesh_size for level in levels] == pytest.approx([.05, .04])
    saved_geometry = directory / "study-geometry.vtp"
    assert saved_geometry.read_bytes() == b"original specimen"
    assert all(level.imported_geometry_path == str(saved_geometry) for level in levels)
    widget.append_button.click()
    assert launch.call_args.args[1].mesh_resolution == .05
    assert not widget.append_button.isEnabled()
