"""CHT explicit actions, saved preferences, and physical-time playback."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pyvista as pv
from PyQt6.QtCore import QSettings,Qt
from PyQt6.QtTest import QSignalSpy,QTest
from PyQt6.QtWidgets import QApplication,QPushButton

from flow_analysis import FlowSettings
from model import Settings
from nonlinear_compression import RustMechDialog
from solver_profiles import SolverProfileStore
from thermal_analysis import ThermalSettings,export_thermal_case
from window_state import WindowStateStore


class ThermalWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.preferences = QSettings(str(Path(self.temp.name)/"ui.ini"),QSettings.Format.IniFormat)
        self.previous = getattr(self.app,"_rustmech_window_state",None)
        if self.previous: self.app.removeEventFilter(self.previous)
        self.store = WindowStateStore(self.app,self.preferences)
        self.app._rustmech_window_state = self.store
        self.dialogs = []

    def dialog(self):
        with patch.object(RustMechDialog,"start_preview",lambda self:None):
            dialog = RustMechDialog(Settings(),solver_profile_store=SolverProfileStore(self.preferences))
        self.dialogs.append(dialog)
        dialog.show()
        dialog.workspace_navigation.setCurrentIndex(3)
        self.app.processEvents()
        return dialog

    def tearDown(self):
        for dialog in reversed(self.dialogs):
            dialog.close()
            dialog.dispose()
        self.app.removeEventFilter(self.store)
        if self.previous:
            self.app.installEventFilter(self.previous)
            self.app._rustmech_window_state = self.previous
        else: del self.app._rustmech_window_state
        self.temp.cleanup()

    def test_enter_commits_temperature_and_only_run_button_starts(self):
        dialog = self.dialog()
        thermal = dialog.thermal_workspace
        accepted = QSignalSpy(dialog.accepted)
        for button in thermal.findChildren(QPushButton):
            self.assertFalse(button.autoDefault())
            self.assertFalse(button.isDefault())
        with patch("thermal_workspace.AnalysisProcess") as constructor:
            control = thermal.thermal_controls["inlet_temperature_c"]
            control.setFocus()
            control.lineEdit().selectAll()
            QTest.keyClicks(control.lineEdit(),"70")
            QTest.keyClick(control.lineEdit(),Qt.Key.Key_Return)
            self.app.processEvents()
            self.assertEqual(control.value(),70)
            constructor.assert_not_called()
            self.assertEqual(len(accepted),0)
            self.assertFalse(dialog.truba_dialog.isVisible())
            thermal.buttons[1].click()
            constructor.assert_called_once()
            self.assertFalse(thermal.thermal_panel.isEnabled())
            with patch("flow_workspace.stop_flow_case"): thermal.stop.click()
            constructor.return_value.force_stop.assert_called_once()

    def test_thermal_preferences_and_both_splitters_restore(self):
        first = self.dialog()
        first.resize(1260,850)
        workspace = first.thermal_workspace
        workspace.thermal_controls["solid_cp"].setValue(700)
        workspace.splitter.setSizes([340,880])
        workspace.chart_splitter.setSizes([450,220])
        self.app.processEvents()
        expected = (first.frameGeometry(),workspace.splitter.sizes(),workspace.chart_splitter.sizes())
        first.hide()
        first.dispose()
        second = self.dialog()
        self.app.processEvents()
        self.assertEqual(second.frameGeometry(),expected[0])
        self.assertEqual(second.thermal_workspace.splitter.sizes(),expected[1])
        self.assertEqual(second.thermal_workspace.chart_splitter.sizes(),expected[2])
        self.assertEqual(second.thermal_workspace.collect_thermal().solid_cp,700)

    def test_saved_frames_change_actual_temperature_and_playback_stops(self):
        directory = Path(self.temp.name)/"case"
        export_thermal_case(Settings(),FlowSettings(cells_across=12),ThermalSettings(),directory)
        mesh = pv.read(directory/"thermal_mesh.vtu")
        solid = mesh.cell_data["Solid"].astype(bool)
        entries,history = [],[]
        for index in range(2):
            frame = dict(time_s=float(index))
            for region,mask,temp in (("water",~solid,20+10*index),("tpms",solid,20+5*index)):
                grid = mesh.extract_cells(mask)
                grid.cell_data["Temperature_C"] = np.full(grid.n_cells,temp)
                filename = f"{region}{index}.vtu"
                grid.save(directory/filename)
                frame[region] = filename
            entries.append(frame)
            history.append(dict(time_s=float(index),water_mean_C=20+10*index,outlet_bulk_C=20+10*index,
                                tpms_mean_C=20+5*index,tpms_min_C=20+5*index,tpms_max_C=20+5*index,pressure_drop_Pa=1))
        (directory/"thermal_summary.json").write_text(json.dumps(dict(status="completed",frames=entries,history=history,warnings=[])))
        workspace = self.dialog().thermal_workspace
        workspace.load_directory(directory)
        self.assertEqual(workspace.time_slider.value(),1)
        self.assertTrue(np.all(workspace.solid_grid.cell_data["Temperature_C"]==25))
        workspace.time_slider.setValue(0)
        self.assertTrue(np.all(workspace.grid.cell_data["Temperature_C"]==20))
        workspace.play.click()
        self.assertTrue(workspace.play_timer.isActive())
        workspace.advance()
        self.assertEqual(workspace.time_slider.value(),1)
        workspace.advance()
        self.assertFalse(workspace.play_timer.isActive())


if __name__=="__main__": unittest.main()
