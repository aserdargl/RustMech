"""Flow navigation, explicit actions, cancellation and saved layout contracts."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QPushButton

from model import Settings
from nonlinear_compression import RustMechDialog
from solver_profiles import SolverProfileStore
from window_state import WindowStateStore


class FlowWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.temp.name)/"ui.ini"),QSettings.Format.IniFormat)
        self.previous_store = getattr(self.app,"_rustmech_window_state",None)
        if self.previous_store: self.app.removeEventFilter(self.previous_store)
        self.store = WindowStateStore(self.app,self.settings)
        self.app._rustmech_window_state = self.store
        self.dialogs = []

    def dialog(self):
        with patch.object(RustMechDialog,"start_preview",lambda self:None):
            dialog = RustMechDialog(Settings(),solver_profile_store=SolverProfileStore(self.settings))
        self.dialogs.append(dialog)
        dialog.show()
        dialog.workspace_navigation.setCurrentIndex(2)
        self.app.processEvents()
        return dialog

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.close()
            dialog.dispose()
        self.app.removeEventFilter(self.store)
        if self.previous_store:
            self.app.installEventFilter(self.previous_store)
            self.app._rustmech_window_state = self.previous_store
        else:
            del self.app._rustmech_window_state
        self.temp.cleanup()

    def test_enter_only_commits_and_explicit_click_starts(self):
        dialog = self.dialog()
        flow = dialog.flow_workspace
        self.assertEqual(dialog.workspace_tabs.currentIndex(),2)
        self.assertFalse(dialog.display_mode_group.isVisible())
        accepted = QSignalSpy(dialog.accepted)
        for button in flow.findChildren(QPushButton):
            self.assertFalse(button.autoDefault())
            self.assertFalse(button.isDefault())
        with patch("flow_workspace.AnalysisProcess") as constructor:
            speed = flow.controls["inlet_speed"]
            speed.setFocus()
            speed.lineEdit().selectAll()
            QTest.keyClicks(speed.lineEdit(),"0.02")
            QTest.keyClick(speed.lineEdit(),Qt.Key.Key_Return)
            self.app.processEvents()
            self.assertAlmostEqual(speed.value(),.02)
            constructor.assert_not_called()
            self.assertEqual(accepted.count(),0)
            self.assertIsNone(dialog.sync_window)
            flow.buttons[1].click()
            constructor.assert_called_once()
            self.assertTrue(flow.stop.isEnabled())
            self.assertFalse(flow.input_panel.isEnabled())
            with patch("flow_workspace.stop_flow_case"):
                flow.stop.click()
            constructor.return_value.force_stop.assert_called_once()
            self.assertIsNone(flow.job)

    def test_flow_preferences_and_splitter_restore_with_main_window(self):
        first = self.dialog()
        first.resize(1220,800)
        flow = first.flow_workspace
        flow.controls["inlet_speed"].setValue(.0123)
        flow.splitter.setSizes([330,850])
        self.app.processEvents()
        expected_sizes,expected_geometry = flow.splitter.sizes(),first.frameGeometry()
        first.hide()
        second = self.dialog()
        self.app.processEvents()
        self.assertEqual(second.frameGeometry(),expected_geometry)
        self.assertEqual(second.flow_workspace.splitter.sizes(),expected_sizes)
        self.assertAlmostEqual(second.flow_workspace.collect().inlet_speed,.0123)
        second.workspace_navigation.setCurrentIndex(0)
        self.app.processEvents()
        self.assertTrue(second.display_mode_group.isVisible())

    def test_display_mode_buttons_track_view_selection(self):
        dialog = self.dialog()
        dialog.workspace_navigation.setCurrentIndex(0)
        buttons = dialog.display_mode_buttons
        self.assertEqual(len({(button.width(), button.height()) for button in buttons.buttons()}), 1)
        with patch.object(dialog, "start_preview"), patch.object(dialog, "render_preview"):
            QTest.mouseClick(buttons.button(1), Qt.MouseButton.LeftButton)
            self.assertEqual(dialog.mode.currentIndex(), 1)
            QTest.keyClick(buttons.button(1), Qt.Key.Key_Right)
            self.assertEqual(dialog.mode.currentIndex(), 2)
            dialog.mode.setCurrentIndex(0)
            self.assertEqual(buttons.checkedId(), 0)


if __name__ == "__main__":
    unittest.main()
