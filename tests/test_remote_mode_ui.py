"""Explicit opt-in, capability filtering and absence of local computation paths."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch

from PySide6.QtCore import QCoreApplication,QEvent,QPoint,QSettings,Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QWidget

from model import Settings
from nonlinear_compression import RustMechDialog
from solver_profiles import SolverProfileStore
from window_state import WindowStateStore
from truba_remote import run_remote_operation,run_remote_mechanics,run_remote_preview,run_remote_level,run_remote_doe


class RemoteModeUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        session_patch = patch('remote_controller.saved_session', return_value=None)
        session_patch.start()
        self.addCleanup(session_patch.stop)
        self.temp=tempfile.TemporaryDirectory()
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

    def enable(self,**capabilities):
        with patch.object(self.controller,"refresh"):
            self.controller.toggle.setChecked(True)
        self.controller.capabilities=dict(mesh=True,netgen=True,kratos=True,flow=True,thermal=True,
                                         ansys_mapdl=False,mesh_types=["HEX8","HEX20","TET4","TET10"],reasons={})
        self.controller.capabilities.update(capabilities)
        self.controller.changed.emit()
        self.app.processEvents()

    def test_startup_and_opening_panel_never_enable_or_connect(self):
        self.assertFalse(self.controller.requested)
        with patch.object(self.window.truba_dialog,"start_connection") as connect,patch.object(self.controller,"refresh") as refresh:
            self.window.truba_button.click()
            self.app.processEvents()
            connect.assert_not_called()
            refresh.assert_not_called()
        self.assertFalse(self.controller.toggle.isChecked())
        field=self.controller.controls["cores"]
        field.setFocus()
        QTest.keyClick(field,Qt.Key.Key_Return)
        self.assertFalse(self.controller.requested)

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
        from dataclasses import asdict
        from remote_controller import RemoteController
        from truba_remote import RemoteContext
        record = dict(job_id="321",state="RUNNING",ready=True,
                      context=asdict(RemoteContext(cores=8,walltime="72:00:00")))
        with patch("remote_controller.saved_session",return_value=record), patch("remote_controller.refresh_session") as refresh:
            restored = RemoteController(self.window,self.preferences)
            try:
                self.app.processEvents()
                self.assertFalse(restored.requested)
                self.assertFalse(restored.ready)
                self.assertEqual(restored.context().session_id,"321")
                self.assertEqual(restored.context().cores,8)
                self.assertFalse(restored.controls["cores"].isEnabled())
                refresh.assert_not_called()
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

    def test_group_order_and_full_panel_positions_survive_resize(self):
        panel = self.window.truba_dialog
        panel.show()
        self.app.processEvents()
        sections = [panel.settings_layout.itemAt(i).widget()
                    for i in range(panel.settings_layout.count())]
        self.assertEqual([section.title() for section in sections], [
            'Server and VPN', 'Credentials and password storage',
            'CPU, RAM and resource session', 'Advanced · remote runtime'])
        self.assertLess(sections[0].height(), 65)
        self.assertIs(panel.connect_button.parentWidget(), panel.username.parentWidget())
        self.assertIs(self.controller.controls['cores'].parentWidget(), self.controller.resource_panel)
        self.assertIs(self.controller.controls['runtime_python'].parentWidget(), self.controller.advanced_panel)
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

    def test_remote_high_order_mesh_preview_does_not_enable_unsupported_solve(self):
        self.enable()
        combo = self.window.mesh_type_combo
        for mesh_type in ('HEX20', 'TET10'):
            index = combo.findData(mesh_type)
            self.assertTrue(combo.model().item(index).isEnabled())
            combo.setCurrentIndex(index)
            settings = self.window.collect()
            self.controller.require('mesh', settings)
            with self.assertRaisesRegex(RuntimeError, 'HEX8, TET4'):
                self.controller.require('kratos', settings)
            self.assertFalse(self.window.apply_button.isEnabled())

    def test_automatic_solver_cores_follow_remote_allocation_and_restore_locally(self):
        # Exercise manual allocation independently of the default Auto policy.
        queue = self.controller.controls['partition']
        queue.setCurrentIndex(queue.findData('debug'))
        self.enable()
        self.assertTrue(self.window.automatic_cores.isChecked())
        self.assertEqual(self.window.controls['cores'].value(), 1)
        self.controller.require('kratos', self.window.collect())
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

    def test_unconfirmed_remote_cancel_keeps_workspace_open_and_job_available(self):
        job=MagicMock()
        job._generic_remote_target=True
        job.force_stop.side_effect=RuntimeError("Connection unavailable")
        self.window.analysis_job=job
        self.window.reject()
        self.assertTrue(self.window.isVisible())
        self.assertIs(self.window.analysis_job,job)
        self.window.analysis_job=None

    def test_mechanics_cfd_and_cht_dispatch_only_remote_operations(self):
        self.enable()
        self.window.preview=SimpleNamespace(mesh=object(),remote_source="/arf/scratch/tester/RustMech/"+"a"*32+"/output/preview")
        with patch.object(self.window,"refresh_validity"),patch("nonlinear_compression.save_settings"),patch("nonlinear_compression.AnalysisProcess") as start:
            self.window.apply_button.setEnabled(True)
            self.window.apply()
            self.assertIs(start.call_args.args[0],run_remote_mechanics)
            self.window.force_stop_analysis()
        for workspace,module,operation in ((self.window.flow_workspace,"flow_workspace","flow_solve"),
                                            (self.window.thermal_workspace,"thermal_workspace","thermal_solve")):
            with patch(module+".AnalysisProcess") as start,patch("flow_workspace.stop_flow_case") as local_cancel:
                workspace.run()
                self.assertIs(start.call_args.args[0],run_remote_operation)
                self.assertEqual(start.call_args.args[1]["operation"],operation)
                workspace.cancel()
                local_cancel.assert_not_called()

    def test_convergence_and_doe_do_not_call_local_mesh_generator(self):
        self.enable()
        from mesh_convergence import MeshConvergenceDialog
        local=MagicMock()
        dialog=MeshConvergenceDialog(self.window.collect(),local,local,self.window)
        study=dialog.study
        study.selected=[0,1,2]
        with patch("mesh_convergence.AnalysisProcess") as start:
            study._launch(study.level_settings(.2),"test")
            self.assertIs(start.call_args.args[0],run_remote_level)
            local.assert_not_called()
            study.force_stop()
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


if __name__=="__main__": unittest.main()
