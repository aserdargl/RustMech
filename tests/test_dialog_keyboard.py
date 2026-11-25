"""Enter commits numeric fields without opening unrelated workspace actions."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QLocale, QSettings, QTimer, Qt
from PySide6.QtTest import QTest, QSignalSpy
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from model import Settings
from nonlinear_compression import RustMechDialog
from solver_profiles import SolverProfileStore
from studio_style import SidebarGroupBox
from window_state import WindowStateStore


class DialogKeyboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_mesh_size_edits_keep_local_at_or_below_global(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        preferences = QSettings(str(Path(directory) / "mesh.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch.object(RustMechDialog, "start_preview", lambda self: None))
        self.enterContext(patch("nonlinear_compression.create_preview_viewport",
                                side_effect=lambda *a, **kw: MagicMock(interactor=QWidget())))
        self.enterContext(patch("remote_controller.saved_session", return_value=None))
        dialog = RustMechDialog(
            Settings(mesh_type="TET4", mesh_resolution=0.2, local_mesh_size=0.1),
            solver_profile_store=SolverProfileStore(preferences),
        )
        self.addCleanup(dialog.dispose)

        global_size = dialog.controls["mesh_resolution"]
        local_size = dialog.controls["local_mesh_size"]

        def enter(control, value):
            control.setLocale(QLocale.c())
            editor = control.lineEdit()
            editor.selectAll()
            QTest.keyClicks(editor, value)
            QTest.keyClick(editor, Qt.Key.Key_Return)

        enter(global_size, "0.07525")
        self.assertEqual(global_size.value(), 0.07525)
        self.assertEqual(local_size.value(), 0.07525)
        self.assertEqual(dialog.collect().local_mesh_size, 0.07525)

        enter(local_size, "0.04")
        enter(global_size, "0.06")
        self.assertEqual(local_size.value(), 0.04)

        enter(local_size, "0.08")
        self.assertEqual(local_size.value(), 0.06)

        dialog.load_from_doe_row(
            Settings(mesh_type="TET4", mesh_resolution=0.03, local_mesh_size=0.05),
            preview=False,
        )
        self.assertEqual(local_size.value(), 0.03)

    def test_enter_commits_mesh_and_only_click_opens_truba(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = getattr(self.app, "_rustmech_window_state", None)
            if previous is not None:
                self.app.removeEventFilter(previous)
            store = WindowStateStore(self.app, QSettings(
                str(Path(directory) / "windows.ini"), QSettings.Format.IniFormat))
            self.app._rustmech_window_state = store
            dialog = None
            try:
                with patch.object(RustMechDialog, "start_preview", lambda self: None):
                    dialog = RustMechDialog(Settings())
                    dialog.show()
                    self.app.processEvents()
                    self.assertEqual(dialog.sidebar_toggle.text(), "Finite Element")
                    groups = dialog.findChildren(SidebarGroupBox)
                    self.assertGreater(len(groups), 5)
                    dialog.main_splitter.setSizes([320, 1000])
                    self.app.processEvents()
                    sidebar_width = dialog.main_splitter.sizes()[0]
                    for group in groups:
                        with self.subTest(section=group.title()):
                            heights = (group.minimumHeight(), group.maximumHeight())
                            group.set_expanded(False)
                            self.app.processEvents()
                            self.assertTrue(group.content.isHidden())
                            self.assertLessEqual(group.height(), 35)
                            self.assertEqual(dialog.main_splitter.sizes()[0], sidebar_width)
                            group.set_expanded(True)
                            self.app.processEvents()
                            self.assertEqual(dialog.main_splitter.sizes()[0], sidebar_width)
                            self.assertFalse(group.content.isHidden())
                            self.assertEqual((group.minimumHeight(), group.maximumHeight()), heights)
                    # Collapse enough sections to remove vertical overflow. The
                    # scrollbar gutter must stay reserved, keeping content stable.
                    setup_scroll = dialog.setup_tabs.widget(0)
                    setup_groups = setup_scroll.findChildren(SidebarGroupBox)
                    viewport_width = setup_scroll.viewport().width()
                    for group in setup_groups:
                        group.set_expanded(False)
                        for _ in range(3):
                            self.app.processEvents()
                        self.assertEqual(dialog.main_splitter.sizes()[0], sidebar_width)
                        self.assertEqual(setup_scroll.viewport().width(), viewport_width)
                    self.assertEqual(setup_scroll.verticalScrollBar().maximum(), 0)
                    for group in setup_groups:
                        group.set_expanded(True)
                        for _ in range(3):
                            self.app.processEvents()
                        self.assertEqual(setup_scroll.viewport().width(), viewport_width)
                    self.assertTrue(dialog.substeps_advanced.isHidden())
                    buttons = [b for b in dialog.findChildren(QPushButton)
                               if b.window() is dialog]
                    clicks = [QSignalSpy(button.clicked) for button in buttons]
                    # Include focus returning from TRUBA: it must not become
                    # the default action after it has been used once either.
                    for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                        for name in ("mesh_resolution", "local_mesh_size"):
                            spin = dialog.controls[name]
                            spin.setFocus()
                            spin.lineEdit().selectAll()
                            QTest.keyClicks(spin.lineEdit(), "0.65")
                            QTest.keyClick(spin.lineEdit(), key)
                            self.app.processEvents()
                            self.assertAlmostEqual(spin.value(), 0.65)
                            self.assertFalse((dialog.sync_window is not None and dialog.sync_window.isVisible()))
                            self.assertIsNone(dialog.esen_workspace)
                            self.assertTrue(dialog.isVisible())
                            self.assertTrue(all(spy.count() == 0 for spy in clicks))
                        with patch("nonlinear_compression.open_truba_desktop") as open_desktop:
                            QTest.mouseClick(dialog.truba_button, Qt.MouseButton.LeftButton)
                            open_desktop.assert_called_once_with()
                        self.assertFalse((dialog.sync_window is not None and dialog.sync_window.isVisible()))
                        QTest.mouseClick(dialog.sync_button, Qt.MouseButton.LeftButton)
                        self.app.processEvents()
                        self.assertTrue(dialog.sync_window.isVisible())
                        dialog.sync_window.hide()
                        clicks = [QSignalSpy(button.clicked) for button in buttons]
                    with patch("esen_jobs.jobs_directory", return_value=Path(directory) / "esen-jobs"), \
                            patch("esen_workspace.QSettings", return_value=store.settings):
                        QTest.mouseClick(dialog.esen_button, Qt.MouseButton.LeftButton)
                        self.app.processEvents()
                        self.assertTrue(dialog.esen_workspace.isVisible())
                        self.assertFalse((dialog.sync_window is not None and dialog.sync_window.isVisible()))
                        dialog.esen_workspace.close()
                    for width in (900, 1440):
                        dialog.resize(width, 800)
                        self.app.processEvents()
                        self.assertEqual(dialog.width(), width)
                        dialog.main_splitter.setSizes([350, width - 350])
                        expected = dialog.main_splitter.sizes()
                        toolbar = dialog.findChild(QWidget, "workspaceCommandBar")
                        self.assertIs(dialog.project_button.parentWidget(), toolbar)
                        self.assertEqual(dialog.brand_title_label.x(), toolbar.layout().contentsMargins().left())
                        self.assertLess(dialog.brand_title_label.geometry().right(), dialog.project_button.x())
                        self.assertLessEqual(abs(dialog.brand_title_label.geometry().center().y()
                                                 - dialog.project_button.geometry().center().y()), 1)
                        self.assertEqual(dialog.project_button.height(), 50)
                        self.assertGreaterEqual(dialog.project_button.width(), 65)
                        self.assertLessEqual(toolbar.minimumSizeHint().width(), toolbar.width())
                        self.assertTrue(dialog.workspace_tabs.widget(0).isAncestorOf(dialog.display_mode_group))
                        menu = dialog.sidebar_toggle.menu()
                        self.assertEqual(
                            [action.text() for action in menu.actions()],
                            ["Finite Element", "FFT Homogenization", "Material Point Method", "Peridynamics"],
                        )
                        opened = QSignalSpy(menu.aboutToShow)
                        button_width = dialog.sidebar_toggle.width()
                        self.assertLess(button_width,
                            dialog.sidebar_toggle.fontMetrics().horizontalAdvance(
                                "Material Point Method") + 32)
                        for action in menu.actions():
                            QTimer.singleShot(30, menu.close)
                            QTest.mouseClick(dialog.sidebar_toggle, Qt.MouseButton.LeftButton)
                            self.assertEqual(dialog.main_splitter.sizes(), expected)
                            action.trigger()
                            self.app.processEvents()
                            if action is dialog.mpm_method_action:
                                self.assertIs(dialog.workspace_tabs.currentWidget(), dialog.mpm_workspace)
                                self.assertTrue(dialog.workspace_actions.actions()[dialog.mpm_workspace_index].isChecked())
                                self.assertTrue(dialog.mpm_workspace.run_button.isVisible())
                                self.assertIsNone(dialog.mpm_workspace.job)
                                dialog.workspace_actions.actions()[0].trigger()
                                continue
                            label = action.text()
                            label = "FFT" if label == "FFT Homogenization" else label
                            self.assertEqual(dialog.sidebar_toggle.text(), label)
                            self.assertEqual(dialog.sidebar_toggle.accessibleName(), action.text())
                            self.assertEqual(dialog.setup_tabs.currentIndex(), action.data())
                            self.assertTrue(action.isChecked())
                            self.assertEqual(dialog.workspace_tabs.currentIndex(), 0)
                            self.assertEqual(dialog.sidebar_toggle.width(), button_width)
                            longest_word = max(dialog.sidebar_toggle.fontMetrics().horizontalAdvance(word)
                                               for word in label.split())
                            self.assertGreaterEqual(button_width, longest_word + 18)
                        self.assertEqual(opened.count(), len(menu.actions()))
                        self.assertFalse(dialog.setup_tabs.tabBar().isVisible())
                        # Selecting settings also recovers a previously collapsed saved sidebar.
                        dialog.main_splitter.setSizes([0, sum(expected)])
                        menu.actions()[0].trigger()
                        self.assertEqual(dialog.main_splitter.sizes(), expected)
                        dialog.setup_tabs.setCurrentIndex(dialog._fft_method_index)
                        dialog._show_geometry_workspace()
                        self.assertEqual(dialog.sidebar_toggle.text(), "Finite Element")
                    self.assertFalse(hasattr(dialog, "folder_batch_dialog"))
                    QTest.mouseClick(dialog.folder_batch_button, Qt.MouseButton.LeftButton)
                    self.assertTrue(dialog.folder_batch_dialog.isVisible())
            finally:
                if dialog is not None:
                    dialog.close()
                    dialog.dispose()
                self.app.removeEventFilter(store)
                if previous is not None:
                    self.app._rustmech_window_state = previous
                    self.app.installEventFilter(previous)
                else:
                    del self.app._rustmech_window_state


if __name__ == "__main__":
    unittest.main()

