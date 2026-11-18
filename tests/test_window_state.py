"""Exercise window preferences with an isolated per-test settings file."""
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMainWindow, QMenu, QSplitter, QVBoxLayout, QWidget

from window_caption import CAPTION_PROPERTY, CaptionButton, install_window_captions
from window_state import WindowStateStore


class WindowStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.temp.name) / "windows.ini"), QSettings.Format.IniFormat)
        self.store = WindowStateStore(self.app, self.settings)
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.close()
        self.app.removeEventFilter(self.store)
        self.temp.cleanup()

    def window(self, name="main"):
        window = QDialog()
        window.setObjectName(name)
        layout = QVBoxLayout(window)
        splitter = QSplitter()
        splitter.addWidget(QWidget())
        splitter.addWidget(QWidget())
        layout.addWidget(splitter)
        self.windows.append(window)
        return window, splitter

    def test_recreated_window_restores_geometry_and_splitter(self):
        first, splitter = self.window()
        first.resize(640, 420)
        first.move(80, 90)
        first.show()
        self.app.processEvents()
        splitter.setSizes([170, 440])
        expected_frame = first.frameGeometry()
        expected_sizes = splitter.sizes()
        first.hide()
        second, restored = self.window()
        second.show()
        self.app.processEvents()
        self.assertEqual(second.frameGeometry(), expected_frame)
        self.assertEqual(restored.sizes(), expected_sizes)

    def test_maximized_state_keeps_normal_bounds(self):
        first, _ = self.window()
        first.resize(600, 400)
        first.show()
        self.app.processEvents()
        expected_size = first.size()
        first.showMaximized()
        self.app.processEvents()
        first.hide()
        second, _ = self.window()
        second.show()
        self.app.processEvents()
        self.assertTrue(second.isMaximized())
        second.showNormal()
        self.app.processEvents()
        self.assertEqual(second.size(), expected_size)

    def test_secondary_window_preferences_are_separate_and_visible(self):
        first, _ = self.window("secondary")
        first.resize(520, 360)
        first.show()
        first.move(-20000, -20000)
        first.hide()
        second, _ = self.window("secondary")
        second.show()
        self.app.processEvents()
        self.assertEqual(second.size().width(), 520)
        self.assertTrue(self.app.primaryScreen().availableGeometry().intersects(second.frameGeometry()))
        other, _ = self.window("main")
        self.assertIsNone(self.settings.value(f"{self.store.key(other)}/geometry"))

    def test_owner_does_not_save_or_restore_secondary_splitters(self):
        owner, own_splitter = self.window()
        own_splitter.setObjectName("settings")
        child = QDialog(owner)
        child.setObjectName("experiments")
        layout = QVBoxLayout(child)
        secondary = QSplitter(Qt.Orientation.Vertical)
        secondary.setObjectName("experimentPlan")
        secondary.addWidget(QWidget())
        secondary.addWidget(QWidget())
        layout.addWidget(secondary)
        self.windows.append(child)
        self.store.save(owner)
        key = self.store.key(owner)
        self.assertIsNone(self.settings.value(f"{key}/splitters/experimentPlan"))
        # Even old contaminated parent preferences must not reach the child.
        wrong = QSplitter(Qt.Orientation.Horizontal)
        self.settings.setValue(f"{key}/splitters/experimentPlan", wrong.saveState())
        self.store.restore(owner)
        self.assertEqual(secondary.orientation(), Qt.Orientation.Vertical)

    def test_wrong_orientation_record_cannot_rotate_settings_column(self):
        window, splitter = self.window()
        splitter.setObjectName("settings")
        wrong = QSplitter(Qt.Orientation.Vertical)
        wrong.addWidget(QWidget())
        wrong.addWidget(QWidget())
        self.settings.setValue(f"{self.store.key(window)}/splitters/settings", wrong.saveState())
        self.store.restore(window)
        self.assertEqual(splitter.orientation(), Qt.Orientation.Horizontal)

    def test_named_layout_survives_insertion_of_other_splitters(self):
        first, splitter = self.window()
        splitter.setObjectName("settings")
        first.show()
        self.app.processEvents()
        splitter.setSizes([170, 440])
        expected = splitter.sizes()
        first.hide()
        second = QDialog()
        second.setObjectName("main")
        second.resize(first.size())
        layout = QVBoxLayout(second)
        added = QSplitter(Qt.Orientation.Vertical, second)
        added.setObjectName("newPlan")
        added.hide()
        restored = QSplitter(Qt.Orientation.Horizontal)
        restored.setObjectName("settings")
        restored.addWidget(QWidget())
        restored.addWidget(QWidget())
        layout.addWidget(restored)
        self.windows.append(second)
        second.show()
        self.app.processEvents()
        self.assertEqual(restored.orientation(), Qt.Orientation.Horizontal)
        self.assertEqual(restored.sizes(), expected)

    def test_caption_preserves_window_actions_modality_and_saved_geometry(self):
        attribute = Qt.ApplicationAttribute.AA_DontUseNativeDialogs
        native_dialogs_disabled = self.app.testAttribute(attribute)
        install_window_captions(self.app)
        service = self.app._rustmech_caption_service
        self.addCleanup(self.app.setAttribute, attribute, native_dialogs_disabled)
        self.addCleanup(delattr, self.app, "_rustmech_caption_service")
        self.addCleanup(self.app.removeEventFilter, service)
        if service._native_frame is not None:
            self.addCleanup(self.app.removeNativeEventFilter, service._native_frame)
        self.addCleanup(service.deleteLater)

        main = QMainWindow()
        main.setCentralWidget(QWidget())
        main.resize(640, 440)
        main.move(80, 90)
        self.windows.append(main)
        main.show()
        self.app.processEvents()
        caption = main.property(CAPTION_PROPERTY)
        buttons = caption.findChildren(CaptionButton)
        self.assertEqual([button.action for button in buttons], ["pin", "minimize", "maximize", "close"])
        self.assertEqual([button.x() for button in buttons], list(range(caption.width() - 100, caption.width(), 25)))
        self.assertTrue(all(button.width() == button.height() == 25 for button in buttons))
        self.assertGreaterEqual(main.centralWidget().y(), caption.geometry().bottom() + 1)
        normal = main.geometry()
        buttons[1].click()
        self.app.processEvents()
        self.assertTrue(main.isMinimized())
        main.showNormal()
        self.app.processEvents()
        self.assertEqual(main.geometry(), normal)

        dialog = QDialog(main)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.addWidget(QWidget())
        self.windows.append(dialog)
        dialog.show()
        self.app.processEvents()
        dialog_caption = dialog.property(CAPTION_PROPERTY)
        dialog_buttons = dialog_caption.findChildren(CaptionButton)
        self.assertEqual([button.action for button in dialog_buttons], ["pin", "close"])
        dialog_caption.pin.click()
        self.assertTrue(dialog_caption.pin.isChecked())
        self.assertFalse(caption.pin.isChecked())
        self.assertEqual(dialog.windowModality(), Qt.WindowModality.WindowModal)
        dialog_buttons[-1].click()
        self.assertFalse(dialog.isVisible())

        caption.maximize.click()
        self.app.processEvents()
        self.assertTrue(main.isMaximized())
        self.assertTrue(caption.isVisible())
        main.hide()
        restored = QMainWindow()
        self.windows.append(restored)
        restored.show()
        self.app.processEvents()
        self.assertTrue(restored.isMaximized())
        restored.property(CAPTION_PROPERTY).maximize.click()
        self.app.processEvents()
        self.assertEqual(restored.geometry(), normal)

        picker = QFileDialog(restored)
        self.windows.append(picker)
        picker.show()
        self.app.processEvents()
        self.assertTrue(picker.property(CAPTION_PROPERTY).isVisible())
        menu = QMenu(restored)
        menu.ensurePolished()
        self.assertIsNone(menu.property(CAPTION_PROPERTY))


if __name__ == "__main__":
    unittest.main()
