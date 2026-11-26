"""Independent DoE workspace behavior and per-user window restoration."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QWidget

from model import Settings
from studio_style import STUDIO_STYLESHEET
from window_state import WindowStateStore


class DoEWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = getattr(self.app, "_rustmech_window_state", None)
        if self.previous is not None:
            self.app.removeEventFilter(self.previous)
        self.store = WindowStateStore(self.app, QSettings(
            str(Path(self.temp.name) / "windows.ini"), QSettings.Format.IniFormat))
        self.app._rustmech_window_state = self.store
        self.windows = []
        viewport = patch('preview_viewport.create_preview_viewport',
                         side_effect=lambda *_, **kw: MagicMock(interactor=QWidget()))
        viewport.start()
        self.addCleanup(viewport.stop)

    def tearDown(self):
        for window in self.windows:
            window.close()
        self.app.removeEventFilter(self.store)
        if self.previous is not None:
            self.app._rustmech_window_state = self.previous
            self.app.installEventFilter(self.previous)
        else:
            del self.app._rustmech_window_state
        self.temp.cleanup()

    def test_geometry_maximized_state_and_both_splitters_restore(self):
        from nonlinear_compression import RustMechDialog
        from solver_profiles import SolverProfileStore

        with patch.object(RustMechDialog, "start_preview", lambda self: None), \
                patch("nonlinear_compression.create_preview_viewport",
                      side_effect=lambda *_, **kw: MagicMock(interactor=QWidget())), \
                patch("remote_controller.saved_session", return_value=None):
            host = RustMechDialog(Settings(), solver_profile_store=SolverProfileStore(self.store.settings))
        self.windows.append(host)
        first = host.doe_window
        first.setStyleSheet(STUDIO_STYLESHEET)
        first.open_workspace()
        first.resize(1060, 720)
        first.move(90, 80)
        self.app.processEvents()
        first.doe.splitter.setSizes([360, 660])
        first.doe.input_navigation.buttons.button(2).click()
        self.app.processEvents()
        first.lhs.splitter.setSizes([440, 580])
        self.app.processEvents()
        frame = first.frameGeometry()
        doe_sizes = first.doe.splitter.sizes()
        lhs_sizes = first.lhs.splitter.sizes()
        first.showMaximized()
        self.app.processEvents()
        first.close()

        from new_doe_page import DoEStudioWindow
        second = DoEStudioWindow(host, self.store.settings)
        second.setStyleSheet(STUDIO_STYLESHEET)
        self.windows.append(second)
        second.open_workspace()
        self.assertTrue(second.isMaximized())
        second.showNormal()
        self.app.processEvents()
        self.assertEqual(second.frameGeometry(), frame)
        self.assertEqual(second.doe.splitter.sizes(), doe_sizes)
        second.doe.input_navigation.buttons.button(2).click()
        self.app.processEvents()
        self.assertEqual(second.lhs.splitter.sizes(), lhs_sizes)
