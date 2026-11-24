"""Explicit opt-in, capability filtering and absence of local computation paths."""
from concurrent.futures import Future
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch

from PySide6.QtCore import QCoreApplication,QEvent,QPoint,QSettings,Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QWidget,QGroupBox,QMessageBox

from model import Settings
from nonlinear_compression import RustMechDialog
from solver_profiles import SolverProfileStore
from window_state import WindowStateStore
from truba_remote import (run_remote_operation,run_remote_mechanics,run_remote_preview,
                          run_remote_level,run_remote_level_pair,run_remote_doe,submit_remote_analysis)


class RemoteModeUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        session_patch = patch('remote_controller.saved_session', return_value=None)
        session_patch.start()
        self.addCleanup(session_patch.stop)
        self.temp=tempfile.TemporaryDirectory()
        for module in ('truba_session', 'truba_jobs', 'truba_remote', 'truba_connection'):
            state_patch = patch(module + '.state_directory', return_value=Path(self.temp.name) / 'truba')
            state_patch.start()
            self.addCleanup(state_patch.stop)
        self.preferences=QSettings(str(Path(self.temp.name)/"ui.ini"),QSettings.Format.IniFormat)
        self.previous=getattr(self.app,"_rustmech_window_state",None)
        if self.previous: self.app.removeEventFilter(self.previous)
        self.store=WindowStateStore(self.app,self.preferences)
        self.app._rustmech_window_state=self.store
        with patch.object(RustMechDialog,"start_preview",lambda self:None),patch("nonlinear_compression.create_preview_viewport",side_effect=lambda parent,**kwargs:MagicMock(interactor=QWidget(parent))):
            self.window=RustMechDialog(Settings(),solver_profile_store=SolverProfileStore(self.preferences))
        self.window.start_preview=lambda:None
        self.controller=self.window.remote_controller
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.dispose()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.removeEventFilter(self.store)
        if self.previous:
            self.app.installEventFilter(self.previous)
            self.app._rustmech_window_state=self.previous
        else: del self.app._rustmech_window_state
        self.temp.cleanup()

    def enable(self,with_session=True,**capabilities):
        with patch.object(self.controller,"refresh"):
            self.controller.toggle.setChecked(True)
        if with_session:
            from dataclasses import asdict
            self.controller.session = dict(job_id='321', state='RUNNING', ready=True,
                                           context=asdict(self.controller.context()))
            self.controller.session_watch = True
            self.controller.session_next_refresh = float('inf')
        self.controller.capabilities=dict(mesh=True,netgen=True,kratos=True,flow=True,thermal=True,
                                         ansys_mapdl=False,mesh_types=["HEX8","HEX20","TET4","TET10"],reasons={})
        self.controller.capabilities.update(capabilities)
        self.controller.changed.emit()
        self.app.processEvents()

    def test_startup_and_hpc_launch_never_enable_or_connect(self):
        self.assertFalse(self.controller.requested)
        with patch.object(self.window.truba_dialog,"start_connection") as connect, \
                patch.object(self.controller,"refresh") as refresh, \
                patch("nonlinear_compression.open_truba_desktop") as open_desktop:
            self.window.truba_button.click()
            self.app.processEvents()
            open_desktop.assert_called_once_with()
            connect.assert_not_called()
            refresh.assert_not_called()
        self.assertFalse(self.window.truba_dialog.isVisible())
        self.assertFalse(self.controller.toggle.isChecked())
        field=self.controller.controls["cores"]
        field.setFocus()
        QTest.keyClick(field,Qt.Key.Key_Return)
        self.assertFalse(self.controller.requested)

    def test_preview_failure_details_are_selectable_even_when_completion_is_immediate(self):
        from mesh_engine import preview_key

        window = self.window
        settings = Settings(mesh_type="TET10")
        window.preview_settings = lambda: settings
        window._preview_includes_mesh = True
        window._preview_is_remote = False
        window.worker_key = preview_key(settings)
        window.mesh_progress_updates.put("Generating tetrahedra in Netgen…")
        failure = RuntimeError("The mesh worker stopped before returning a preview.")
        failure.add_note("Mesh worker exit code: 37")
        window.future = Future()
        window.future.set_exception(failure)
        window.poll_preview()
        self.app.processEvents()

        details = window.preview_error_details
        self.assertTrue(details.isVisible())
        self.assertTrue(details.isReadOnly())
        self.assertIn("TET10", details.toPlainText())
        self.assertIn("Generating tetrahedra in Netgen…", details.toPlainText())
        self.assertIn("Mesh worker exit code: 37", details.toPlainText())

        start = QTextCursor(details.document())
        end = QTextCursor(start)
        end.movePosition(QTextCursor.MoveOperation.EndOfLine)
        QTest.mousePress(details.viewport(), Qt.MouseButton.LeftButton, pos=details.cursorRect(start).center())
        QTest.mouseMove(details.viewport(), details.cursorRect(end).center())
        QTest.mouseRelease(details.viewport(), Qt.MouseButton.LeftButton, pos=details.cursorRect(end).center())
        self.assertTrue(details.textCursor().hasSelection())
        QTest.keyClick(details, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(details, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.app.clipboard().text(), details.toPlainText())

    def test_truba_quick_connect_uses_saved_credentials_without_opening_panel(self):
        window = self.window
        dialog = window.truba_dialog
        button = window.truba_quick_connect
        status = window.truba_quick_status
        self.assertGreater(button.x(), window.truba_button.geometry().right())
        self.assertFalse(button.icon().isNull())
        self.assertFalse(status.isVisible())
        dialog.username.setText("unsaved-user")
        dialog.password.setText("unsaved-password")
        with patch.object(dialog.credentials, "load", return_value=("saved-user", "saved-password")) as load, \
                patch("truba_connection.ConnectionWorker") as worker:
            button.click()
            self.assertTrue(load.called)
            reads = load.call_count
            button.click()
            self.assertEqual(load.call_count, reads)
            worker.assert_called_once_with("saved-user", "saved-password", dialog.state_dir, dialog)
            worker.return_value.start.assert_called_once()
            self.assertFalse(dialog.isVisible())
            self.assertFalse(button.isEnabled())
            self.assertTrue(status.isVisible())
            self.assertIn("Connecting...", status.text())
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as question:
                dialog._confirm_host_key("fingerprint")
            self.assertIs(question.call_args.args[0], window)
            worker.return_value.decide_host_key.assert_called_once_with(False)
            dialog._state_changed("waiting_for_network")
            self.assertIn("Waiting for VPN", status.text())
            with patch.object(dialog.credentials, "save"), patch.object(dialog, "analysis_auth", return_value=None):
                dialog._state_changed("connected")
            self.assertIn("Connected to TRUBA", status.text())
            dialog._state_changed("authentication_failed")
            dialog._finished()
            self.assertTrue(button.isEnabled())
            self.assertIn("rejected", status.text())

    def test_truba_quick_connect_with_no_saved_credentials_does_not_use_form_values(self):
        dialog = self.window.truba_dialog
        dialog.username.setText("unsaved-user")
        dialog.password.setText("unsaved-password")
        with patch.object(dialog.credentials, "load", return_value=None), \
                patch("truba_connection.ConnectionWorker") as worker:
            self.window.truba_quick_connect.click()
            worker.assert_not_called()
        status = self.window.truba_quick_status
        self.assertTrue(status.isVisible())
        self.assertIn("No saved TRUBA credentials", status.text())
        self.assertFalse(dialog.isVisible())

    def test_connected_analysis_can_submit_without_allocating(self):
        from truba_jobs import RemoteAuth

        window = self.window
        auth = RemoteAuth("tester", "password")
        with patch.object(window.truba_dialog, "analysis_auth", return_value=auth), \
                patch.object(self.controller.session_executor, "submit") as allocate, \
                patch("nonlinear_compression.save_settings"), \
                patch("nonlinear_compression.AnalysisProcess") as start:
            self.controller.toggle.setChecked(True)
            self.assertIsNone(self.controller.session)
            self.assertTrue(window.apply_button.isEnabled(), window.status.text())
            start.return_value.remote_status.return_value = None
            start.return_value.progress_status.return_value = None
            try:
                window.apply()
                self.assertIs(start.call_args.args[0], run_remote_mechanics)
                self.assertEqual(start.call_args.args[2].session_id, "")
                self.assertIs(start.call_args.kwargs["remote_auth"], auth)
                self.assertTrue(window.truba_job_monitor.isVisible())
                self.assertFalse(window.truba_job_monitor.isModal())
                allocate.assert_not_called()
            finally:
                window.analysis_poll.stop()
                window.analysis_job = None

    def test_connected_cfd_and_cht_submit_without_allocating(self):
        from truba_jobs import RemoteAuth

        window = self.window
        with patch.object(window.truba_dialog, "analysis_auth", return_value=RemoteAuth("tester", "password")):
            self.controller.toggle.setChecked(True)
            for workspace, module, operation in (
                (window.flow_workspace, "flow_workspace", "flow_solve"),
                (window.thermal_workspace, "thermal_workspace", "thermal_solve"),
            ):
                with self.subTest(operation=operation), patch(module + ".AnalysisProcess") as start:
                    self.assertTrue(workspace.buttons[1].isEnabled())
                    workspace.run()
                    self.assertIs(start.call_args.args[0], run_remote_operation)
                    self.assertEqual(start.call_args.args[1]["operation"], operation)
                    self.assertEqual(start.call_args.args[2].session_id, "")
                    self.assertTrue(window.truba_job_monitor.isVisible())
                    workspace.cancel()
                    window.truba_job_monitor.close()

    def test_job_monitor_reopens_and_updates_from_saved_slurm_states(self):
        from truba_job_list import TrubaJobMonitor
        from truba_jobs import HOST, write_record

        window = self.window
        monitor = window.truba_job_monitor
        self.assertEqual(monitor.button.property("jobState"), "idle")
        monitor.button.click()
        monitor.setGeometry(70, 80, 640, 310)
        normal = monitor.geometry()
        monitor.close()
        self.assertFalse(monitor.isVisible())

        path = window.truba_dialog.state_dir / "jobs" / "test.json"
        record = dict(host=HOST, operation="mechanical", request_id="a" * 32, job_id="123",
                      geometry_name="Gyroid", background=True)
        for saved, expected in (("PENDING|Priority", "pending"), ("RUNNING|None", "running"),
                                ("OUT_OF_MEMORY|0:125", "error"), ("downloaded", "idle")):
            write_record(path, dict(record, state=saved))
            window.truba_dialog.job_list.refresh()
            self.assertEqual(monitor.button.property("jobState"), expected)
            self.assertFalse(monitor.isVisible())

        monitor.button.click()
        self.assertTrue(monitor.isVisible())
        self.assertEqual(monitor.table.item(0, 0).text(), "RM-AAAAAAAAAAAA")
        self.assertEqual(monitor.table.item(0, 1).text(), "123")
        self.assertEqual(monitor.table.item(0, 3).text(), "downloaded")
        monitor.close()
        reopened = TrubaJobMonitor(window, window.truba_dialog.job_list)
        reopened.open_window()
        self.assertEqual(reopened.geometry(), normal)
        reopened.showMaximized()
        self.app.processEvents()
        reopened.close()
        restored = TrubaJobMonitor(window, window.truba_dialog.job_list)
        restored.open_window()
        self.assertTrue(restored.isMaximized())
        restored.showNormal()
        self.app.processEvents()
        self.assertEqual(restored.geometry(), normal)
        restored.close()

    def test_main_execution_target_stays_in_sync_across_workspaces(self):
        window = self.window
        target = window.execution_target
        self.assertEqual(target.currentData(), "local")
        for index in (0, 2, 3):
            window.workspace_tabs.setCurrentIndex(index)
            self.app.processEvents()
            self.assertTrue(target.isVisible())
            self.assertEqual(window.apply_button.isVisible(), index == 0)
            self.assertIs(window.childAt(target.mapTo(window, target.rect().center())), target)
        with patch.object(window.truba_dialog, "start_connection") as connect:
            QTest.keyClick(target, Qt.Key.Key_Down)
            connect.assert_not_called()
        self.assertTrue(self.controller.requested)
        self.assertTrue(self.controller.toggle.isChecked())
        self.assertFalse(window.thermal_workspace.buttons[1].isEnabled())
        window.flow_workspace.job = MagicMock()
        try:
            QTest.keyClick(target, Qt.Key.Key_Up)
            self.assertEqual(target.currentData(), "truba")
            self.assertTrue(self.controller.requested)
        finally:
            window.flow_workspace.job = None
        self.controller.toggle.setChecked(False)
        self.assertEqual(target.currentData(), "local")
        self.assertTrue(window.thermal_workspace.buttons[1].isEnabled())

    def test_run_submits_remote_mechanics_without_a_preview(self):
        window = self.window
        target = window.execution_target
        QTest.keyClick(target, Qt.Key.Key_Down)
        self.assertFalse(window.apply_button.isEnabled())
        self.enable()
        self.assertIsNone(window.preview)
        self.assertTrue(window.apply_button.isEnabled(), window.status.text())

        try:
            with patch("nonlinear_compression.save_settings"), patch(
                    "nonlinear_compression.AnalysisProcess") as start:
                start.return_value.remote_status.return_value = None
                start.return_value.progress_status.return_value = None
                QTest.mouseClick(window.apply_button, Qt.MouseButton.LeftButton)
                operation, settings, context, source = start.call_args.args
                self.assertIs(operation, run_remote_mechanics)
                self.assertEqual((settings.solver_backend, context.session_id, source), ("kratos", "321", ""))
        finally:
            window.analysis_poll.stop()
            window.analysis_job = None

    def test_local_kratos_requires_installation_and_runs_without_truba_auth(self):
        from mesh_engine import preview_key
        from analysis_process import run_single_analysis
        from local_kratos import INSTALL_REQUIRED

        window = self.window
        window.solver_combo.setCurrentIndex(window.solver_combo.findData("kratos"))
        window.preview = SimpleNamespace(key=preview_key(window.collect()), mesh=SimpleNamespace(solve_errors=[]))
        with patch("nonlinear_compression.local_kratos_python", return_value=None), \
                patch("nonlinear_compression.AnalysisProcess") as start:
            window.apply()
            start.assert_not_called()
        self.assertFalse(window.apply_button.isEnabled())
        self.assertIn(INSTALL_REQUIRED, window.status.text())

        with patch("nonlinear_compression.local_kratos_python", return_value="python.exe"), \
                patch("nonlinear_compression.save_settings"), \
                patch("nonlinear_compression.AnalysisProcess") as start, \
                patch.object(window.truba_dialog, "analysis_auth") as auth:
            try:
                window.apply()
                args, kwargs = start.call_args
                self.assertEqual(args[0], run_single_analysis)
                self.assertEqual(args[1].solver_backend, "kratos")
                self.assertIs(args[2], window.preview.mesh)
                self.assertEqual(kwargs, {})
                auth.assert_not_called()
            finally:
                window.analysis_poll.stop()
                window.analysis_job = None

    def test_fft_submits_assigned_material_without_local_wsl(self):
        window = self.window
        window._select_settings_method(window._fft_method_index)
        self.enable(damask=False)
        self.assertFalse(window.apply_button.isEnabled())
        self.controller.capabilities['damask'] = True
        self.controller.changed.emit()
        self.assertTrue(window.apply_button.isEnabled())
        self.assertFalse(window.fft_distribution.isEnabled())
        window.fft_distribution.clear()
        window.fft_grid.setValue(128)
        window.fft_material_editor.controls['young_modulus'].setValue(125000)
        try:
            with patch('nonlinear_compression.AnalysisProcess') as process, patch(
                    'nonlinear_compression.DamaskAnalysisProcess') as local:
                process.return_value.remote_status.return_value = None
                process.return_value.progress_status.return_value = None
                window.apply()
                local.assert_not_called()
                target, request, _ = process.call_args.args
                self.assertIs(target, submit_remote_analysis)
                self.assertEqual((request['operation'], request['grid']), ('fft_solve', 128))
                self.assertEqual(request['settings']['young_modulus'], 125000)
        finally:
            window.analysis_job = None
            window.analysis_poll.stop()
            self.controller.session = None

    def test_downloaded_fft_job_displays_the_matrix(self):
        import json

        folder = Path(self.temp.name) / 'fft'
        folder.mkdir()
        status = dict(grid=32, solid_volume_fraction=.3, void_stiffness_ratio=.01,
                      stiffness_mpa=[[1000] * 6 for _ in range(6)])
        (folder / 'status.json').write_text(json.dumps(status), encoding='utf-8')
        result = dict(operation='fft_solve', run_folder=str(folder))
        self.window._background_analysis_updated({}, (result, folder.parent))
        self.assertEqual(self.window.fft_result_folder, folder)
        self.assertIn('32³', self.window.fft_result_summary.text())

    def test_enabling_remote_mode_and_runtime_checks_without_session_submit_nothing(self):
        with patch.object(self.controller.executor, 'submit') as submit:
            self.controller.toggle.setChecked(True)
            self.controller.refresh(False)
            self.controller.refresh(True)
            self.controller.connection_changed(True)
            submit.assert_not_called()
        self.assertFalse(self.controller.ready)
        with self.assertRaisesRegex(RuntimeError, 'resource session'):
            self.controller.require('mesh')

    def test_runtime_check_uses_ready_session_id(self):
        from concurrent.futures import Future
        self.enable()
        pending = Future()
        with patch.object(self.controller.executor, 'submit', return_value=pending) as submit:
            self.controller.refresh(False)
        try:
            submit.assert_called_once()
            self.assertEqual(submit.call_args.args[2].session_id, '321')
        finally:
            self.controller.future = None
            self.controller.timer.stop()

    def test_resources_runtime_actions_fit_and_dispatch_in_both_languages(self):
        from concurrent.futures import Future
        panel = self.window.truba_dialog
        controller = self.controller
        page = panel.settings_scroll
        panel.tabs.setCurrentWidget(page)
        panel.show()
        buttons = (controller.check, controller.prepare, controller.recover, controller.stop)
        self.enable()
        for language, button, operation in (('en', controller.check, 'probe'),
                                            ('tr', controller.prepare, 'prepare')):
            with self.subTest(language=language):
                panel.language_manager.set_language(language)
                page.ensureWidgetVisible(button)
                self.app.processEvents()
                self.assertIs(panel.tabs.currentWidget(), page)
                self.assertTrue(page.viewport().rect().contains(
                    button.mapTo(page.viewport(), button.rect().bottomRight())))
                for action in buttons:
                    self.assertEqual(action.size(), button.size())
                    self.assertGreaterEqual(action.width(), action.sizeHint().width())
                controller.session_future = Future()
                with patch.object(controller.executor, 'submit') as submit:
                    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                    submit.assert_not_called()
                self.assertIn('TRUBA', controller.status.text())
                controller.session_future = None
                pending = Future()
                with patch.object(controller.executor, 'submit', return_value=pending) as submit:
                    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                    self.assertEqual(submit.call_args.args[1], {'operation': operation})
                self.assertTrue(controller.stop.isEnabled())
                QTest.mouseClick(controller.stop, Qt.MouseButton.LeftButton)
                self.assertTrue(controller.cancelled.is_set())
                pending.cancel()
                controller.poll()
                self.assertTrue(button.isEnabled())

    def test_default_session_is_four_hours_and_explicit_allocate_enables_remote_mode(self):
        from concurrent.futures import Future
        from remote_controller import allocate_with_limits
        pending = Future()
        self.assertEqual(self.controller.controls['walltime'].text(), '04:00:00')
        with patch.object(self.controller.session_executor, 'submit', return_value=pending) as submit:
            self.controller.session_command('allocate')
        try:
            self.assertIs(submit.call_args.args[0], allocate_with_limits)
            self.assertEqual(submit.call_args.args[1].walltime, '04:00:00')
            self.assertEqual(submit.call_args.args[1].partition, 'auto')
            self.assertTrue(self.controller.requested)
        finally:
            self.controller.session_future = None

    def test_ready_banner_requires_running_allocation_and_verified_runtime(self):
        from dataclasses import asdict
        from truba_remote import RemoteContext
        self.enable(with_session=False)
        self.assertNotEqual(self.window.brand_header.property('computeState'), 'ready')
        self.controller.session = dict(job_id='321', state='RUNNING', ready=True,
                                       allocated_partition='hamsi', allocated_nodes='hamsi001',
                                       context=asdict(RemoteContext(partition='hamsi', cores=28, walltime='72:00:00')))
        self.controller.session_watch = True
        with patch.object(self.window, 'start_preview') as generate:
            self.controller.changed.emit()
            self.app.processEvents()
            generate.assert_not_called()
        self.assertEqual(self.window.brand_header.property('computeState'), 'ready')
        self.assertIn('READY', self.window.brand_subtitle_lbl.text())
        self.controller.session.update(state='PENDING', ready=False)
        self.controller.changed.emit()
        self.assertEqual(self.window.brand_header.property('computeState'), 'pending')

    def test_running_banner_is_green_before_runtime_is_ready_without_enabling_compute(self):
        from concurrent.futures import Future
        self.enable()
        self.controller.capabilities = None
        self.controller.session['ready'] = False
        self.controller.changed.emit()
        self.assertEqual(self.window.brand_header.property('computeState'), 'allocated')
        self.assertIn('RUNNING', self.window.brand_subtitle_lbl.text())
        self.assertIn('worker starting', self.window.brand_subtitle_lbl.text())
        self.assertFalse(self.controller.ready)
        # Verify the painted background as well as the state property.
        self.app.processEvents()
        image = self.window.brand_header.grab().toImage()
        self.assertEqual(image.pixelColor(3, 3).name(), '#e7f3e8')
        self.controller.session['ready'] = True
        self.controller.future = Future()
        self.controller.changed.emit()
        self.assertIn('checking runtime', self.window.brand_subtitle_lbl.text())
        self.assertFalse(self.controller.ready)
        self.controller.future = None
        self.controller.capabilities = dict(mesh=False)
        self.controller.changed.emit()
        self.assertIn('see runtime details', self.window.brand_subtitle_lbl.text())
        self.assertFalse(self.controller.ready)
        self.controller.capabilities = dict(mesh=True)
        self.controller.changed.emit()
        self.assertEqual(self.window.brand_header.property('computeState'), 'ready')
        self.assertTrue(self.controller.ready)
        self.controller.session_watch = False
        self.controller.changed.emit()
        self.assertEqual(self.window.brand_header.property('computeState'), 'pending')

    def test_connection_panel_tracks_slurm_pending_and_running(self):
        from concurrent.futures import Future
        from dataclasses import asdict
        from truba_remote import RemoteContext
        self.assertIn('Slurm: NONE', self.controller.session_state.text())
        self.controller.session_watch = True
        self.controller.session_action = 'watch'
        context = asdict(RemoteContext(partition='hamsi', cores=28))
        for state, ready, explanation in (
                ('PENDING', False, 'not been allocated'),
                ('RUNNING', False, 'worker is starting'),
                ('RUNNING', True, 'worker is ready')):
            completed = Future()
            completed.set_result(dict(job_id='321', state=state, ready=ready,
                                      context=context, reason='Resources' if state == 'PENDING' else '',
                                      allocated_partition='hamsi',
                                      allocated_nodes='hamsi001' if state == 'RUNNING' else ''))
            self.controller.session_future = completed
            self.controller.poll_session()
            self.assertIn('Slurm: ' + state, self.controller.session_state.text())
            self.assertIn(explanation, self.controller.session_state.text())
            self.assertIn('321', self.controller.session_status.text())
        self.assertIn('hamsi001', self.controller.session_status.text())
        self.controller.session_watch = False
        self.controller.update_session_controls()
        self.assertIn('Saved state', self.controller.session_state.text())
        self.assertNotIn('worker is ready', self.controller.session_state.text())
        self.controller.session.update(state='released', ready=False)
        self.controller.update_session_controls()
        self.assertIn('Resources released', self.controller.session_state.text())

    def test_runtime_is_checked_once_when_allocation_becomes_ready(self):
        from concurrent.futures import Future
        from dataclasses import asdict
        from truba_remote import RemoteContext
        self.enable()
        self.controller.capabilities = None
        self.controller.session_watch = True
        completed = Future()
        completed.set_result(dict(job_id='321', state='RUNNING', ready=True,
                                  context=asdict(RemoteContext(partition='hamsi', cores=28, walltime='72:00:00'))))
        self.controller.session_future = completed
        self.controller.session_action = 'watch'
        with patch.object(self.controller, 'refresh') as check:
            self.controller.poll_session()
            self.controller.poll_session()
            check.assert_called_once_with(False, automatic=True)

    def test_running_session_dispatches_automatic_setup_and_retains_failure(self):
        from concurrent.futures import Future
        from remote_controller import ensure_remote_runtime
        self.enable()
        self.controller.capabilities = None
        pending = Future()
        with patch.object(self.controller.executor, 'submit', return_value=pending) as submit:
            self.controller.poll_session()
            self.assertIs(submit.call_args.args[0], ensure_remote_runtime)
            self.assertEqual(submit.call_args.args[1].session_id, '321')
            with self.assertRaisesRegex(RuntimeError, 'automatically'):
                self.controller.require('mesh')
            self.controller.runtime_progress.emit('Building remote mesh kernel')
            self.assertIn('Building remote mesh kernel', self.window.brand_subtitle_lbl.text())
            pending.set_exception(RuntimeError('Package download failed'))
            self.controller.poll()
            self.controller.poll_session()
            submit.assert_called_once()
        self.assertFalse(self.controller.ready)
        with self.assertRaisesRegex(RuntimeError, 'Package download failed'):
            self.controller.require('mesh')

    def test_release_is_available_during_watch_runtime_check_and_remote_preview(self):
        from concurrent.futures import Future
        controller = self.controller
        controller.session = dict(job_id='321', state='RUNNING', ready=True)
        controller.session_watch = True
        pending_watch = Future()
        controller.session_future = pending_watch
        controller.session_action = 'watch'
        controller.future = Future()
        self.window.future = Future()
        self.window._preview_is_remote = True
        released = Future()
        try:
            controller.update_session_controls()
            self.assertTrue(controller.session_release.isEnabled())
            with patch.object(controller.release_executor, 'submit', return_value=released) as submit:
                controller.session_release.click()
            self.assertIs(submit.call_args.args[2], pending_watch)
            self.assertTrue(controller.cancelled.is_set())
            self.assertTrue(self.window.mesh_cancelled.is_set())
            self.assertFalse(controller.session_watch)
            self.assertFalse(controller.session_release.isEnabled())
            pending_watch.set_result(dict(controller.session))
            released.set_result(dict(controller.session, state='released', ready=False))
            controller.poll_session()
            self.assertEqual(controller.session['state'], 'released')
            self.assertIsNone(controller.session_future)
            self.assertIn('Resources released', self.window.truba_dialog.job_list.hint.text())
        finally:
            controller.future = self.window.future = None
            controller.session_future = controller.release_future = None
            controller.session_watch = False
            self.window._preview_is_remote = False

    def test_close_waits_for_submission_without_releasing_resource_session(self):
        from concurrent.futures import Future
        pending = Future()
        session = dict(job_id='321', state='PENDING')
        self.controller.session_future = pending
        self.controller.session_action = 'allocate'
        with patch('remote_controller.release_session') as release:
            self.window.close()
            self.assertTrue(self.window.isVisible())
            self.assertTrue(self.controller.closing)
            self.controller.poll_close()
            self.assertTrue(self.window.isVisible())
            pending.set_result(session)
            self.controller.poll_close()
            self.assertFalse(self.window.isVisible())
            self.assertTrue(self.controller.close_confirmed)
            release.assert_not_called()

    def test_running_saved_session_stays_alive_on_close(self):
        self.controller.session = dict(job_id='321', state='RUNNING', ready=True)
        self.controller.session_watch = True
        with patch('remote_controller.release_session') as release:
            self.window.close()
            self.assertFalse(self.window.isVisible())
            self.assertEqual(self.controller.session['state'], 'RUNNING')
            release.assert_not_called()

    def test_connection_refreshes_saved_session_only_for_its_owner(self):
        from concurrent.futures import Future
        from remote_controller import refresh_session
        self.controller.session = dict(job_id='321', state='RUNNING', ready=True, username='tester')
        pending = Future()
        with patch.object(self.controller.session_executor, 'submit', return_value=pending) as submit:
            owner = SimpleNamespace(username='other')
            with patch.object(self.window.truba_dialog, 'analysis_auth', return_value=owner):
                self.controller.connection_changed(True)
                self.controller.poll_session()
            submit.assert_not_called()
            owner.username = 'tester'
            with patch.object(self.window.truba_dialog, 'analysis_auth', return_value=owner):
                self.controller.connection_changed(True)
                self.assertFalse(self.controller.session['ready'])
                self.controller.poll_session()
            submit.assert_called_once_with(refresh_session, self.controller.session, owner)
        pending.set_result(dict(self.controller.session, ready=True))
        self.controller.poll_session()
        self.assertTrue(self.controller.session_watch)
        self.assertTrue(self.controller.session['ready'])

    def test_saved_debug_queue_accepts_the_four_hour_default(self):
        import json
        from dataclasses import asdict
        from remote_controller import RemoteController
        from truba_remote import RemoteContext
        self.preferences.setValue('remote/context', json.dumps(asdict(RemoteContext(partition='debug', walltime='00:30:00'))))
        restored = RemoteController(self.window, self.preferences)
        try:
            self.assertEqual(restored.context().partition, 'debug')
            self.assertEqual(restored.context().walltime, '04:00:00')
        finally:
            restored.shutdown()
            restored.scroll.deleteLater()
            restored.advanced_panel.deleteLater()

    def test_session_controls_are_explicit_and_enter_only_commits_field(self):
        self.assertEqual(self.controller.controls['partition'].currentData(), 'auto')
        self.assertEqual(self.controller.context().cores, 20)
        self.assertEqual(self.controller.context().memory_mb, 170000)
        self.assertEqual(self.controller.controls["cores"].minimum(), 20)
        self.assertEqual(self.controller.controls["memory_mb"].minimum(), 1 / 1024)
        with patch.object(self.controller,"session_command") as command:
            panel = self.window.truba_dialog
            panel.show()
            queue = self.controller.controls['partition']
            queue.setCurrentIndex(queue.findData('hamsi'))
            field = self.controller.controls["walltime"]
            field.setText("72:00:00")
            field.setFocus()
            QTest.keyClick(field,Qt.Key.Key_Return)
            self.app.processEvents()
            command.assert_not_called()
            self.assertEqual(self.controller.context().walltime,"72:00:00")
            self.controller.allocate.click()
            command.assert_called_once_with("allocate")
        for button in (self.controller.allocate,self.controller.session_refresh,self.controller.session_release):
            self.assertFalse(button.autoDefault())
            self.assertFalse(button.isDefault())

    def test_expired_session_cannot_fall_back_to_new_queue_submission(self):
        self.enable()
        self.controller.session = dict(job_id="321",state="ended",ready=False)
        self.controller.session_watch = True
        self.assertEqual(self.controller.context().session_id,"321")
        with self.assertRaises(RuntimeError): self.controller.require("mesh")

    def test_queue_selection_sets_cpu_gpu_ram_limits_beside_inputs(self):
        from truba_resources import BASELINES
        queue = self.controller.controls['partition']
        cpu = self.controller.controls['cores']
        gpu = self.controller.controls['gpus']
        ram = self.controller.controls['memory_mb']
        for name in ('hamsi', 'barbun', 'orfoz', 'smp', 'barbun-cuda', 'akya-cuda'):
            queue.setCurrentIndex(queue.findData(name))
            limit = BASELINES[name]
            self.assertEqual((cpu.minimum(), cpu.maximum()), (limit.min_cpus, limit.max_cpus))
            self.assertEqual((gpu.minimum(), gpu.maximum()), (limit.min_gpus, limit.max_gpus))
            self.assertEqual(ram.minimum(), 1 / 1024)
            self.assertAlmostEqual(ram.maximum(), limit.memory_ceiling(cpu.value()) / 1024)
            self.assertIn('not specified', self.controller.resource_min['memory_mb'].text())
            self.assertIn(str(limit.max_cpus), self.controller.resource_max['cores'].text())
            self.assertEqual(self.controller.allocate.isEnabled(), limit.min_gpus == 0)
        self.controller.controls['walltime'].setText('72:00:00')
        queue.setCurrentIndex(queue.findData('debug'))
        self.assertEqual(self.controller.controls['walltime'].text(), '04:00:00')

    def test_auto_selects_resources_and_locks_inputs_without_network_on_open(self):
        from truba_resources import parse_catalog
        queue = self.controller.controls['partition']
        with patch('remote_controller.fetch_catalog') as fetch:
            self.window.truba_dialog.show()
            queue.setCurrentIndex(queue.findData('auto'))
            self.assertIn('Auto: Barbun', self.controller.queue_hint.text())
            self.assertEqual(self.controller.context().cores, 20)
            self.assertEqual(self.controller.context().memory_mb, 170000)
            for name in ('cores', 'gpus', 'memory_mb'):
                self.assertFalse(self.controller.controls[name].isEnabled())
            fetch.assert_not_called()
        self.controller.queue_catalog = parse_catalog(
            'PartitionName=hamsi State=UP MaxTime=3-00:00:00 MaxMemPerCPU=3500 DefMemPerCPU=3000',
            'NodeName=hamsi1 Partitions=hamsi CPUEfctv=56 RealMemory=190000')
        queue.setCurrentIndex(queue.findData('hamsi'))
        self.assertTrue(self.controller.controls['cores'].isEnabled())
        self.assertEqual(self.controller.controls['cores'].minimum(), 28)
        self.assertAlmostEqual(self.controller.controls['memory_mb'].maximum(), 98000 / 1024)
        self.controller.controls['memory_mb'].setValue(1.5)
        self.assertEqual(self.controller.context().memory_mb, 1536)

    def test_manual_resource_values_restore_without_network(self):
        from remote_controller import RemoteController
        queue = self.controller.controls['partition']
        queue.setCurrentIndex(queue.findData('hamsi'))
        self.controller.controls['cores'].setValue(35)
        self.controller.controls['memory_mb'].setValue(1.5)
        with patch('remote_controller.fetch_catalog') as fetch:
            restored = RemoteController(self.window, self.preferences)
            try:
                context = restored.context()
                self.assertEqual((context.partition, context.cores, context.gpus, context.memory_mb), ('hamsi', 35, 0, 1536))
                fetch.assert_not_called()
            finally:
                restored.shutdown()
                restored.scroll.deleteLater()

    def test_saved_resource_session_restores_without_contacting_truba(self):
        from concurrent.futures import Future
        from dataclasses import asdict
        import json
        from remote_controller import RemoteController
        from truba_remote import RemoteContext
        record = dict(job_id="321",state="RUNNING",ready=True,
                      context=asdict(RemoteContext(partition='hamsi', cores=28,walltime="72:00:00")))
        with patch("remote_controller.saved_session",return_value=record), patch("remote_controller.refresh_session") as refresh:
            restored = RemoteController(self.window,self.preferences)
            try:
                self.app.processEvents()
                self.assertFalse(restored.requested)
                self.assertFalse(restored.ready)
                self.assertEqual(restored.controls['partition'].currentData(), 'auto')
                self.assertEqual(restored.context().session_id,"321")
                self.assertEqual(restored.context().partition, 'hamsi')
                self.assertEqual(restored.context().cores,28)
                self.assertFalse(restored.controls["cores"].isEnabled())
                refresh.assert_not_called()

                restored.session_watch = True
                with patch.object(restored.executor, 'submit', return_value=Future()) as submit:
                    self.assertTrue(restored.refresh())
                self.assertEqual(submit.call_args.args[2].partition, 'hamsi')
                self.assertEqual(json.loads(self.preferences.value('remote/context'))['partition'], 'auto')
            finally:
                restored.shutdown()
                restored.scroll.deleteLater()

    def test_expanded_truba_panel_keeps_geometry_after_hide_show(self):
        panel = self.window.truba_dialog
        panel.resize(720,790)
        panel.show()
        self.app.processEvents()
        expected = panel.frameGeometry()
        panel.hide()
        panel.setProperty("preferencesRestored",False)
        panel.resize(500,400)
        panel.show()
        self.app.processEvents()
        self.assertEqual(panel.frameGeometry(),expected)

    def test_truba_settings_reopens_maximized_panel_with_saved_normal_bounds(self):
        panel = self.window.truba_dialog
        self.window.truba_settings_button.click()
        self.app.processEvents()
        normal = panel.geometry()
        panel.showMaximized()
        self.app.processEvents()
        panel.hide()
        self.window.truba_settings_button.click()
        self.app.processEvents()
        self.assertTrue(panel.isMaximized())
        panel.showNormal()
        self.app.processEvents()
        self.assertEqual(panel.geometry(), normal)

    def test_group_order_and_full_panel_positions_survive_resize(self):
        panel = self.window.truba_dialog
        panel.show()
        self.app.processEvents()
        groups = [panel.settings_columns.itemAt(i).widget() for i in range(panel.settings_columns.count())]
        self.assertTrue(all(isinstance(group, QGroupBox) for group in groups))
        self.assertEqual([section.title() for section in groups], [
            'Credentials and password storage',
            'CPU, RAM and resource session', 'Advanced · remote runtime'])
        self.assertEqual([panel.tabs.tabText(i) for i in range(panel.tabs.count())],
                         ['Connection', 'Active jobs'])
        self.assertIs(panel.tabs.currentWidget(), panel.settings_scroll)
        self.assertEqual(len({group.y() for group in groups}), 1)
        self.assertEqual(len({group.size().toTuple() for group in groups}), 1)
        for left, right in zip(groups, groups[1:]):
            self.assertLess(left.geometry().right(), right.x())
        self.assertIs(panel.connect_button.parentWidget(), panel.username.parentWidget())
        self.assertIs(self.controller.controls['cores'].parentWidget(), self.controller.resource_panel)
        self.assertIs(self.controller.controls['runtime_python'].parentWidget(), self.controller.advanced_panel)
        self.assertIs(self.controller.check.parentWidget(), self.controller.advanced_panel)
        self.assertIs(self.controller.status.parentWidget(), self.controller.advanced_panel)
        self.assertIs(self.controller.allocate.parentWidget(), self.controller.resource_panel)
        widgets = [panel.username, panel.connect_button, *self.controller.controls.values(),
                   self.controller.allocate, self.controller.check]

        def positions():
            return [(widget.mapTo(panel.settings_scroll.viewport(), QPoint()), widget.size())
                    for widget in widgets]

        expected = positions()
        for size in ((1000, 950), (360, 240), (660, 800)):
            panel.resize(*size)
            self.app.processEvents()
            self.assertEqual(positions(), expected)
        self.controller.controls['partition'].setCurrentIndex(
            self.controller.controls['partition'].findData('hamsi'))
        panel._state_changed('host_key_confirmation')
        self.controller.session = dict(job_id='test', state='PENDING', reason='Waiting for resources')
        self.controller.update_session_controls()
        self.app.processEvents()
        self.assertEqual(positions(), expected)
        scroll = panel.settings_scroll
        self.assertGreater(scroll.horizontalScrollBar().maximum(), 0)
        runtime_python = self.controller.controls['runtime_python']
        scroll.ensureWidgetVisible(runtime_python)
        self.assertTrue(scroll.viewport().rect().contains(
            runtime_python.mapTo(scroll.viewport(), runtime_python.rect().center())))

    def test_only_verified_supported_solvers_and_no_local_fallback(self):
        self.enable(flow=False)
        combo=self.window.solver_combo
        self.assertFalse(combo.model().item(combo.findData("ansys_mapdl")).isEnabled())
        self.assertTrue(combo.model().item(combo.findData("kratos")).isEnabled())
        self.assertFalse(combo.model().item(combo.findData("openfoam")).isEnabled())
        self.assertFalse(self.window.flow_workspace.buttons[1].isEnabled())
        with patch("flow_workspace.AnalysisProcess") as start:
            self.window.flow_workspace.run()
            start.assert_not_called()
        self.controller.capabilities=None
        self.controller.changed.emit()
        with patch("nonlinear_compression.run_mesh_preview") as local:
            RustMechDialog.start_preview(self.window)
            local.assert_not_called()
        self.assertIsNone(self.window.future)

    def test_mesh_preview_uses_remote_worker_not_local_mesher(self):
        self.enable()
        with patch.object(self.window.executor,"submit",return_value=MagicMock()) as submit,patch("nonlinear_compression.run_mesh_preview") as local:
            RustMechDialog.start_preview(self.window)
            self.assertIs(submit.call_args.args[0],run_remote_preview)
            local.assert_not_called()
        self.window.future=None

    def test_remote_high_order_mesh_supports_penalty_compression(self):
        self.enable()
        combo = self.window.mesh_type_combo
        for mesh_type in ('HEX20', 'TET10'):
            index = combo.findData(mesh_type)
            self.assertTrue(combo.model().item(index).isEnabled())
            combo.setCurrentIndex(index)
            settings = self.window.collect()
            self.assertEqual(settings.kratos_contact_method, 'penalty')
            self.controller.require('mesh', settings)
            self.controller.require('kratos', settings)

    def test_automatic_solver_cores_follow_remote_allocation_and_restore_locally(self):
        # Exercise manual allocation independently of the default Auto policy.
        queue = self.controller.controls['partition']
        queue.setCurrentIndex(queue.findData('debug'))
        self.enable()
        self.assertTrue(self.window.automatic_cores.isChecked())
        self.assertEqual(self.window.controls['cores'].value(), 1)
        self.controller.require('kratos', self.window.collect())
        self.controller.session['context']['cores'] = 4
        self.controller.controls['cores'].setValue(4)
        self.controller.capabilities = dict(mesh=True, kratos=True, mesh_types=['HEX8', 'TET4'])
        self.controller.changed.emit()
        self.assertEqual(self.window.controls['cores'].value(), 4)
        self.window.automatic_cores.setChecked(False)
        self.window.controls['cores'].setValue(8)
        with self.assertRaisesRegex(RuntimeError, 'CPU count exceeds'):
            self.controller.require('kratos', self.window.collect())

    def test_disabling_remote_mode_restores_local_solver_preference(self):
        original=self.window.solver_combo.currentData()
        self.enable()
        self.assertEqual(self.window.solver_combo.currentData(),"kratos")
        self.assertEqual(self.preferences.value("solverProfiles/active"),original)
        self.controller.toggle.setChecked(False)
        self.app.processEvents()
        self.assertEqual(self.window.solver_combo.currentData(),original)
        self.assertFalse(self.controller.requested)

    def test_disconnect_restores_local_mode_and_dispatches_local_mesh(self):
        original = self.window.solver_combo.currentData()
        self.enable()
        self.controller.session = dict(job_id='321', state='RUNNING', ready=True)
        self.controller.session_watch = True
        dialog = self.window.truba_dialog
        dialog.worker = MagicMock()
        dialog.connected = True
        dialog._update_controls()
        with patch.object(self.controller, 'refresh') as refresh:
            dialog.disconnect_button.click()
            self.app.processEvents()
            refresh.assert_not_called()
        self.assertFalse(self.controller.requested)
        self.assertFalse(self.controller.toggle.isChecked())
        self.assertFalse(self.controller.session_watch)
        self.assertIsNone(self.controller.capabilities)
        self.assertEqual(self.window.solver_combo.currentData(), original)
        self.assertIn('Local mode', self.controller.status.text())
        self.assertEqual(self.controller.session['job_id'], '321')
        with patch.object(self.controller.session_executor, 'submit') as remote_submit:
            self.controller.poll_session()
            remote_submit.assert_not_called()
        with patch.object(self.window.executor, 'submit', return_value=MagicMock()) as submit, \
                patch.object(self.controller, 'require') as require, \
                patch('nonlinear_compression.run_mesh_preview') as local:
            RustMechDialog.start_preview(self.window)
            self.assertIs(submit.call_args.args[0], local)
            require.assert_not_called()
        self.window.future = None

    def test_disconnect_selects_local_mode_even_with_submitted_remote_analysis(self):
        self.enable()
        job = MagicMock()
        self.window.analysis_job = job
        try:
            self.controller.disconnect()
            self.assertFalse(self.controller.requested)
            self.assertFalse(self.controller.toggle.isChecked())
            self.assertIs(self.window.analysis_job, job)
            job.force_stop.assert_not_called()
        finally:
            self.window.analysis_job = None

    def test_completed_remote_probe_cannot_restore_capabilities_after_disconnect(self):
        from concurrent.futures import Future
        self.enable()
        pending = Future()
        self.controller.future = pending
        self.controller.disconnect()
        pending.set_result((dict(capabilities=dict(mesh=True, kratos=True)), None))
        self.controller.poll()
        self.assertIsNone(self.controller.future)
        self.assertIsNone(self.controller.capabilities)
        self.assertFalse(self.controller.requested)
        self.assertIn('Local mode', self.controller.status.text())

    def test_closing_detaches_active_remote_analyses_without_cancelling_them(self):
        from concurrent.futures import Future
        pending = Future()
        analysis, flow, thermal, first_row, second_row = [MagicMock(is_remote=True) for _ in range(5)]
        jobs = (analysis, flow, thermal, first_row, second_row)
        for job in jobs:
            job.detach_remote.return_value = pending
        self.window.analysis_job = analysis
        self.window.flow_workspace.job = flow
        self.window.thermal_workspace.job = thermal
        self.window.doe_studio._active_future = first_row
        self.window.doe_studio._parallel_futures = {0: (first_row, 0), 1: (second_row, 0)}
        self.window.close()
        self.assertTrue(self.window.isVisible())
        self.assertIsNone(self.window.analysis_job)
        self.assertFalse(self.window.doe_studio._parallel_futures)
        pending.set_result(None)
        self.controller.poll_close()
        self.assertFalse(self.window.isVisible())
        for job in jobs:
            job.detach_remote.assert_called_once_with()
            job.force_stop.assert_not_called()

    def test_mechanics_cfd_and_cht_dispatch_only_remote_operations(self):
        from mesh_engine import preview_key
        self.enable()
        self.window.truba_dialog.active_jobs.study_name.setText('A')
        source = "/arf/scratch/tester/RustMech/"+"a"*32+"/output/preview"
        self.window.preview=SimpleNamespace(mesh=object(), key=preview_key(self.window.collect()), remote_source=source)
        with patch.object(self.window,"refresh_validity"),patch("nonlinear_compression.save_settings"),patch("nonlinear_compression.AnalysisProcess") as start:
            self.window.apply_button.setEnabled(True)
            self.window.apply()
            self.assertIs(start.call_args.args[0],run_remote_mechanics)
            self.assertEqual(start.call_args.args[2].study_name, 'A')
            self.assertEqual(start.call_args.args[3], source)
            self.window.force_stop_analysis()
        for workspace,module,operation in ((self.window.flow_workspace,"flow_workspace","flow_solve"),
                                            (self.window.thermal_workspace,"thermal_workspace","thermal_solve")):
            with patch(module+".AnalysisProcess") as start,patch("flow_workspace.stop_flow_case") as local_cancel:
                workspace.run()
                self.assertIs(start.call_args.args[0],run_remote_operation)
                self.assertEqual(start.call_args.args[1]["operation"],operation)
                self.assertEqual(start.call_args.args[2].study_name, 'A')
                workspace.cancel()
                local_cancel.assert_not_called()

    def test_convergence_execution_toggle_stays_in_sync(self):
        self.window.open_mesh_convergence()
        dialog = self.window._mesh_convergence_dialog
        study = dialog.study
        self.assertFalse(study.run_on_truba.isChecked())
        self.assertTrue(study.run_button.isEnabled())
        with patch.object(self.window.truba_dialog, "start_connection") as connect:
            study.run_on_truba.click()
        connect.assert_not_called()
        self.assertTrue(self.controller.toggle.isChecked())
        self.assertEqual(self.window.execution_target.currentData(), "truba")
        self.assertTrue(study.run_button.isEnabled())

        with patch.object(study, "future", object()):
            study.run_on_truba.click()
        self.assertTrue(study.run_on_truba.isChecked())
        self.assertTrue(self.controller.requested)

        self.window.execution_target.setCurrentIndex(self.window.execution_target.findData("local"))
        self.assertFalse(study.run_on_truba.isChecked())
        self.assertTrue(study.run_button.isEnabled())
        dialog.reject()
        self.assertIsNone(self.window._mesh_convergence_dialog)

    def test_convergence_submits_directly_and_shows_per_job_state_without_allocation(self):
        self.window.open_mesh_convergence()
        dialog = self.window._mesh_convergence_dialog
        study = dialog.study
        study.start_size.setValue(.2)
        study.run_on_truba.click()
        self.assertFalse(self.controller.ready)
        self.assertIsNone(self.controller.session)
        self.assertEqual(study.solver_combo.currentData(), "kratos")
        self.assertFalse(study.truba_resource_info.isHidden())
        queue = self.controller.controls['partition']
        queue.setCurrentIndex(queue.findData('barbun'))
        self.controller.controls['cores'].setValue(40)
        job = MagicMock()
        job.done.return_value = False
        job.remote_status.return_value = {"job_id": "470", "state": "PENDING|Resources",
                                          "script_name": "RM-pair.slurm", "remote_directory": "/scratch/pair",
                                          "level_resources": [{"cores": 20, "memory_mb": 1024}] * 2}
        with patch("mesh_convergence.AnalysisProcess", return_value=job) as submit, \
                patch("remote_controller.create_session") as allocate:
            study.run_button.click()
            allocate.assert_not_called()
        submit.assert_called_once()
        target, levels, context, *_ = submit.call_args.args
        self.assertIs(target, run_remote_level_pair)
        self.assertFalse(context.session_id)
        self.assertEqual(([level.cores for level in levels], context.cores), ([16, 16], 40))
        self.assertFalse(study.mesh_settings.isEnabled())
        self.assertEqual(study.results.rowCount(), 2)
        self.assertIn("preparing submission", study.results.item(0, 7).text())
        self.assertEqual(study.results.item(0, 11).text(), "20")
        self.assertEqual(study.results.item(0, 12).text(), f"{context.memory_mb // 2 / 1024:.2f}")
        self.assertEqual(study.results.item(0, 14).text(), "barbun")
        study._poll()
        for index in range(2):
            self.assertEqual(study.results.item(index, 8).text(), "470")
            self.assertEqual(study.results.item(index, 9).text(), "RM-pair.slurm")
            self.assertIn("/scratch/pair/RM-pair.slurm", study.results.item(index, 9).toolTip())
            self.assertEqual(study.results.item(index, 10).text(), "PENDING|Resources")
            self.assertEqual(study.results.item(index, 11).text(), "20")
            self.assertEqual(study.results.item(index, 12).text(), "1.00")
            self.assertEqual(study.results.item(index, 14).text(), "barbun")
        job.remote_status.return_value["state"] = "RUNNING"
        job.remote_status.return_value["level_progress"] = ["Meshing specimen", "Solving · 42 elements"]
        study._poll()
        self.assertEqual(study.results.item(1, 10).text(), "RUNNING")
        self.assertEqual(study.results.item(0, 7).text(), "TRUBA · Meshing specimen")
        self.assertEqual(study.results.item(1, 7).text(), "TRUBA · Solving · 42 elements")
        study.force_stop()
        self.assertTrue(study.mesh_settings.isEnabled())
        self.assertEqual(study.results.item(1, 7).text(), "Cancelled (Forced Stop)")
        self.assertEqual(study.results.item(1, 8).text(), "470")
        job.force_stop.assert_called_once()
        dialog.reject()

    def test_convergence_script_preview_drives_exact_submission(self):
        from truba_remote import run_remote_level_group
        from PySide6.QtWidgets import QMenu

        self.window.open_mesh_convergence()
        dialog = self.window._mesh_convergence_dialog
        study = dialog.study
        dialog.resize(650, 480)
        self.app.processEvents()
        self.assertTrue(study.script_plan_panel.isVisible())
        self.assertFalse(study.script_add.isEnabled())
        local_geometry = study.script_plan_panel.geometry()
        study.start_size.setValue(.2)
        study.run_on_truba.click()
        self.app.processEvents()
        self.assertEqual(study.script_plan_panel.geometry(), local_geometry)
        self.assertEqual(study.script_queue.currentData(), "barbun")
        self.assertTrue(study.truba_wall_field.isVisible())
        self.assertEqual(study.truba_wall_field.currentText(), "03:00")
        study.truba_wall_field.setCurrentIndex(study.truba_wall_field.findData(120))
        toolbar = study.script_plan_scroll
        self.assertGreaterEqual(toolbar.viewport().height(), study.script_plan_toolbar.sizeHint().height())
        self.assertGreater(study.script_preview.y(), toolbar.geometry().bottom())
        line = study.script_plan_toolbar.layout()
        widgets = [line.itemAt(index).widget() for index in range(line.count() - 1)]
        self.assertEqual(widgets[0].text(), "Slurm script plan")
        for control in widgets[1:]:
            self.assertLessEqual(abs(control.geometry().center().y() - widgets[0].geometry().center().y()), 1)
        self.assertGreater(toolbar.horizontalScrollBar().maximum(), 0)
        toolbar.ensureWidgetVisible(study.script_delete, 4, 0)
        delete_position = study.script_delete.mapTo(toolbar.viewport(), study.script_delete.rect().bottomRight())
        self.assertTrue(toolbar.viewport().rect().contains(delete_position))
        self.assertGreater(study.script_add.x(), study.script_ram.parentWidget().geometry().right())
        self.assertGreater(study.script_delete.x(), study.script_add.geometry().right())
        self.assertEqual(study.script_add.size(), study.script_delete.size())
        study.script_queue.setCurrentIndex(study.script_queue.findData("hamsi"))
        self.assertIn("CPU 56 · GPU 0", study.script_queue.currentText())
        study.script_ram.setValue(120)
        study.script_count.setValue(2)
        job = MagicMock()
        job.done.return_value = False
        job.remote_status.return_value = {}
        with patch("mesh_convergence.AnalysisProcess", return_value=job) as submit:
            study.script_add.click()
            self.assertEqual(study.script_preview.rowCount(), 2)
            self.assertEqual(study.script_preview.item(0, 4).text(), "60.00")
            study.run_on_truba.click()
            self.assertTrue(study.script_plan_panel.isVisible())
            self.assertEqual(study.script_preview.rowCount(), 2)
            self.assertFalse(study.script_add.isEnabled())
            study.run_on_truba.click()
            self.app.processEvents()
            preview = study.script_preview.viewport().grab().toImage()
            for column in range(study.script_preview.columnCount()):
                cell = study.script_preview.item(0, column)
                rect = study.script_preview.visualItemRect(cell)
                self.assertEqual(preview.pixelColor(rect.x() + 6, rect.y() + 4).name(), "#ffffff")
            study.script_choice.setCurrentIndex(study.script_choice.findData(1))
            study.script_count.setValue(1)
            study.script_add.click()
            study.script_add.click()
            self.assertEqual(study.script_preview.rowCount(), 3)  # Hamsi cannot fit four 16-CPU levels.
            cell_rect = study.script_preview.visualItemRect(study.script_preview.item(2, 4))
            QTest.mouseClick(study.script_preview.viewport(), Qt.MouseButton.LeftButton, pos=cell_rect.center())
            self.assertEqual([item.row() for item in study.script_preview.selectionModel().selectedRows()], [2])
            selected = study.script_preview.viewport().grab().toImage()
            for column in range(study.script_preview.columnCount()):
                cell = study.script_preview.item(2, column)
                self.assertTrue(cell.isSelected())
                rect = study.script_preview.visualItemRect(cell)
                fill = selected.pixelColor(rect.x() + 6, rect.y() + 4)
                for actual, expected in zip(fill.getRgb()[:3], (220, 238, 255), strict=True):
                    self.assertLessEqual(abs(actual - expected), 2)
            self.assertTrue(study.script_delete.isEnabled())
            study.script_delete.click()
            self.assertEqual(study.script_preview.rowCount(), 2)
            self.assertFalse(study.script_delete.isEnabled())
            study.script_add.click()
            study._script_plan_menu(study.script_preview.visualItemRect(study.script_preview.item(2, 0)).center())
            menu = study.script_preview.findChild(QMenu)
            self.assertEqual([action.text() for action in menu.actions()], ["Delete"])
            menu.actions()[0].trigger()
            menu.close()
            self.assertEqual(study.script_preview.rowCount(), 2)
            study.script_add.click()
            self.assertEqual(study.script_preview.item(0, 4).text(), "40.00")
            submit.assert_not_called()
            study.run_button.click()
        submit.assert_called_once()
        target, levels, context, *_ = submit.call_args.args
        self.assertIs(target, run_remote_level_group)
        self.assertEqual((len(levels), context.partition, context.cores, context.memory_mb),
                         (3, "hamsi", 56, 120 * 1024))
        self.assertEqual(context.walltime, "02:00:00")
        self.assertEqual(study.results.rowCount(), 3)
        study.force_stop()
        dialog.reject()

    def test_convergence_can_add_and_submit_new_scripts_during_a_run(self):
        from convergence_execution import Level

        self.window.open_mesh_convergence()
        dialog = self.window._mesh_convergence_dialog
        study = dialog.study
        study.start_size.setValue(.2)
        study.run_on_truba.click()
        study.script_queue.setCurrentIndex(study.script_queue.findData("hamsi"))
        study.script_ram.setValue(120)
        jobs = [MagicMock() for _ in range(3)]
        for job in jobs:
            job.done.return_value = False
            job.remote_status.return_value = {}
        with patch("mesh_convergence.AnalysisProcess", side_effect=jobs) as submit:
            study.script_add.click()
            study.run_button.click()
            submit.assert_called_once()
            study_id = submit.call_args.kwargs["remote_options"]["study"]["id"]
            self.assertEqual(study.script_preview.rowCount(), 0)
            self.assertTrue(study.script_add.isEnabled())
            self.assertFalse(study.run_button.isEnabled())

            study.script_add.click()
            self.assertEqual(study.script_preview.rowCount(), 1)
            self.assertTrue(study.run_button.isEnabled())
            submit.assert_called_once()
            jobs[0].done.return_value = True
            jobs[0].result.return_value = Level(
                study.rows[0].settings, elements=100, metrics=[100] * 3, status="completed")
            study._poll()
            self.assertTrue(study.awaiting_script_run)
            self.assertEqual(study.rows[0].status, "completed")
            self.assertEqual(study.script_preview.rowCount(), 1)

            study.run_button.click()
            self.assertEqual(submit.call_count, 2)
            self.assertFalse(study.awaiting_script_run)
            self.assertAlmostEqual(study.rows[1].settings.mesh_resolution, .2 / 1.3)
            self.assertEqual(submit.call_args.kwargs["remote_options"]["study"]["id"], study_id)
            study.script_add.click()
            self.assertEqual(submit.call_count, 2)
            study.run_button.click()
            self.assertEqual(submit.call_count, 3)
            self.assertEqual(tuple(study.remote_jobs.values()), (jobs[1], jobs[2]))
            self.assertAlmostEqual(study.rows[2].settings.mesh_resolution, .2 / 1.3**2)
            self.assertEqual(study.rows[0].status, "completed")
        study.force_stop()
        dialog.reject()

    def test_single_convergence_level_shows_all_barbun_cores(self):
        self.window.open_mesh_convergence()
        dialog = self.window._mesh_convergence_dialog
        study = dialog.study
        study.run_on_truba.click()
        self.enable()
        study.start_size.setValue(.2)
        study.single_truba_mesh.setChecked(True)
        queue = self.controller.controls['partition']
        queue.setCurrentIndex(queue.findData('barbun'))
        with patch("mesh_convergence.AnalysisProcess") as start:
            study.start()
            target, settings, context, *_ = start.call_args.args
            self.assertIs(target, run_remote_level)
            self.assertEqual((settings.cores, context.cores), (40, 40))
            self.assertEqual(study.results.item(0, 11).text(), "40")
            study.force_stop()
        dialog.reject()

    def test_convergence_and_doe_do_not_call_local_mesh_generator(self):
        from mesh_convergence import MeshConvergenceDialog
        local=MagicMock()
        dialog=MeshConvergenceDialog(self.window.collect(),local,local,self.window)
        study=dialog.study
        study.run_on_truba.click()
        self.enable()
        study.start_size.setValue(.2)
        study.single_truba_mesh.setChecked(True)
        with patch("mesh_convergence.AnalysisProcess") as start:
            study.start()
            self.assertIsNotNone(start.call_args, study.status.text())
            self.assertIs(start.call_args.args[0],run_remote_level)
            submitted = start.call_args.args[1]
            self.assertEqual(submitted.solver_backend, "kratos")
            self.assertEqual(submitted.self_contact_mode, self.window.collect().self_contact_mode)
            self.assertEqual(submitted.compression, study.settings.compression)
            self.assertFalse(study.run_on_truba.isEnabled())
            local.assert_not_called()
            study.force_stop()
            self.assertTrue(study.run_on_truba.isEnabled())
        study.dispose()
        dialog.deleteLater()
        doe=self.window.doe_studio
        doe.add_design(self.window.collect())
        doe.add_blank_row()
        with patch("doe_studio.AnalysisProcess") as start:
            doe.start_mesh_precheck()
            self.assertIs(start.call_args.args[0],run_remote_doe)
            self.assertFalse(start.call_args.args[4])
            doe.stop_active_queue()

    def test_doe_new_geometry_is_porous_and_solve_uses_current_remote_solver(self):
        from dataclasses import replace
        doe = self.window.doe_studio
        self.assertEqual(doe.planner.geometry_panel.selected_settings().specimen_architecture, 'porous_all')
        old = replace(self.window.collect(), solver_backend='ansys_mapdl', self_contact_mode='disabled')
        doe.add_design(old)
        doe.add_blank_row()
        self.enable()
        self.assertEqual(self.window.collect().solver_backend, 'kratos')
        with patch('doe_studio.AnalysisProcess') as start:
            doe.start_batch_solve()
            self.assertIsNotNone(start.call_args, [(row.status, row.enabled) for row in doe._rows])
            self.assertIs(start.call_args.args[0], run_remote_doe)
            self.assertEqual(start.call_args.args[2].solver_backend, 'kratos')
            self.assertTrue(start.call_args.args[4])
            doe.stop_active_queue()


if __name__=="__main__": unittest.main()
