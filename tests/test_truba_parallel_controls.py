"""Parallel/renewal preferences, Enter behavior and existing window restoration."""
from unittest.mock import patch
import os
import unittest

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from remote_controller import RemoteController
from studio_style import CONTROL_HEIGHT
from tests import test_remote_mode_ui as helpers


class ParallelControlTests(unittest.TestCase):
    setUpClass = classmethod(helpers.RemoteModeUiTests.setUpClass.__func__)
    setUp = helpers.RemoteModeUiTests.setUp
    tearDown = helpers.RemoteModeUiTests.tearDown

    def test_parallel_and_renewal_preferences_restore_and_enter_never_allocates(self):
        controller = self.controller
        panel = controller.resource_window
        panel.show()
        self.app.processEvents()
        field = controller.controls['parallel_jobs']
        self.assertEqual(field.height(), CONTROL_HEIGHT)
        self.assertLessEqual(field.width(), 100)
        self.window.language_manager.set_language('tr')
        self.app.processEvents()
        self.assertEqual(controller.auto_renew.text(), 'Süre dolmadan yedek kaynak hazırla')
        self.window.language_manager.set_language('en')
        field.setValue(4)
        controller.auto_renew.setChecked(False)
        with patch.object(controller, 'session_command') as allocate:
            field.setFocus()
            QTest.keyClick(field, Qt.Key.Key_Return)
            self.app.processEvents()
            allocate.assert_not_called()
        with patch('remote_controller.fetch_catalog') as fetch:
            restored = RemoteController(self.window, self.preferences)
            try:
                self.assertEqual(restored.context().parallel_jobs, 4)
                self.assertFalse(restored.context().auto_renew)
                fetch.assert_not_called()
            finally:
                restored.shutdown()
                restored.scroll.deleteLater()
                restored.advanced_panel.deleteLater()

    # Qt's offscreen platform reports a frame origin that disagrees with the
    # saved normal geometry, so a hide/show cycle shifts the panel there only.
    # The real Windows platform keeps the bounds; tools/run_tests.py gui runs it.
    test_expanded_panel_keeps_saved_geometry = unittest.skipIf(
        QGuiApplication.platformName() == 'offscreen' or os.environ.get('QT_QPA_PLATFORM', '').startswith('offscreen'),
        'window frame geometry needs a real window system',
    )(helpers.RemoteModeUiTests.test_expanded_truba_panel_keeps_geometry_after_hide_show)


if __name__ == '__main__':
    unittest.main()
