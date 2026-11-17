"""Active inclusion snapshots parameters; the library retains preview validation."""
from concurrent.futures import Future
from dataclasses import replace
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from doe_geometry import ActiveGeometryPanel, GeometryLibraryDialog
from model import Settings
from structure_library import ALL_STRUCTURES_BY_FAMILY
from window_state import WindowStateStore


class GeometryLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_active_add_does_not_request_preview_volume_mesh_or_analysis(self):
        from doe_studio import DoEStudioWidget
        mesh = MagicMock(side_effect=AssertionError("DoE inclusion must not mesh"))
        solve = MagicMock(side_effect=AssertionError("DoE inclusion must not solve"))
        builder_provider = MagicMock(side_effect=AssertionError("Add must not request preview"))
        with patch("preview_viewport.create_preview_viewport", return_value=MagicMock(interactor=QWidget())):
            studio = DoEStudioWidget(Settings, mesh, solve)
            panel = ActiveGeometryPanel(Settings, builder_provider)
            panel.geometry_added.connect(studio.add_design)
            try:
                panel.timer.stop()
                # No completed preview exists; Add commits text that has not lost focus.
                panel.selected_settings()
                panel.values['cell_size'].lineEdit().setText('3.5')
                panel._add()
                self.assertEqual(len(studio._designs), 1)
                self.assertEqual(studio._designs[0].cell_size, 3.5)
                self.assertIsNone(panel._future)
                builder_provider.assert_not_called()
                mesh.assert_not_called()
                solve.assert_not_called()
                self.assertIsNone(studio._active_future)
                self.assertIsNone(studio._active_mode)
                # An unavailable preview also does not prevent parameter inclusion.
                panel.status.setText('Preview unavailable: test error')
                panel.values['solid_fraction'].setValue(40)
                panel._add()
                self.assertEqual(len(studio._designs), 2)
                self.assertEqual(studio._designs[1].solid_fraction, 40)
                builder_provider.assert_not_called()
            finally:
                panel.dispose()
                panel.close()
                studio.dispose()
                studio.close()

    def test_mesh_settings_do_not_block_active_add_or_experiment_tables(self):
        from doe_studio import DoEStudioWidget
        current = replace(Settings(), mesh_type='TET4', mesh_resolution=.1, local_mesh_size=.5)
        with patch("preview_viewport.create_preview_viewport", return_value=MagicMock(interactor=QWidget())):
            mesh = MagicMock(side_effect=AssertionError('unexpected mesh'))
            studio = DoEStudioWidget(lambda: current, mesh)
            builder = MagicMock(side_effect=AssertionError('unexpected preview'))
            panel = ActiveGeometryPanel(lambda: current, builder)
            panel.geometry_added.connect(studio.add_design)
            try:
                panel.timer.stop()
                panel._add()
                self.assertTrue(panel.add_button.isEnabled())
                self.assertEqual(len(studio._designs), 1)
                self.assertEqual(studio._designs[0].local_mesh_size, .5)
                with patch('doe_studio.QMessageBox.warning') as warning:
                    studio.generate_table()
                    studio.generate_ml_table()
                    warning.assert_not_called()
                self.assertEqual(len(studio._matrix_rows[0]), 1)
                self.assertEqual(len(studio._matrix_rows[1]), 1)
                mesh.assert_not_called()
                builder.assert_not_called()
                panel.values['cell_size'].setValue(80)
                panel._add()
                self.assertFalse(panel.add_button.isEnabled())
                self.assertEqual(len(studio._designs), 1)
            finally:
                panel.dispose()
                panel.close()
                studio.dispose()
                studio.close()

    def test_active_panel_includes_current_structure_while_old_preview_finishes(self):
        current = Settings()
        pending = Future()
        executor = MagicMock()
        executor.submit.return_value = pending
        with patch("preview_viewport.create_preview_viewport", return_value=MagicMock(interactor=QWidget())), \
             patch("doe_geometry.ThreadPoolExecutor", return_value=executor):
            panel = ActiveGeometryPanel(lambda: current, lambda: lambda _: None)
            added = []
            panel.geometry_added.connect(added.append)
            try:
                panel.show()
                panel.timer.stop()
                panel.refresh()
                old_snapshot = panel._future[0]
                panel.structure.setCurrentText("Neovius")
                panel.morphology.setCurrentIndex(panel.morphology.findData("network_a"))
                expected = replace(current, structure_name="Neovius", tpms_morphology="network_a")
                self.assertEqual(panel.selected_settings(), expected)
                self.assertNotEqual(old_snapshot, expected)
                pending.set_result(SimpleNamespace(n_cells=12))
                panel.refresh()
                self.assertTrue(panel.add_button.isEnabled())
                self.assertEqual(panel._ready, old_snapshot)
                panel._add()
                self.assertEqual(added, [expected])
                for family in ("lattice", "porous", "tpms"):
                    panel.family.setCurrentIndex(panel.family.findData(family))
                    self.assertEqual([panel.structure.itemText(i) for i in range(panel.structure.count())],
                                     list(ALL_STRUCTURES_BY_FAMILY[family]))
                    panel.structure.setCurrentIndex(panel.structure.count() - 1)
                    selected = panel.selected_settings()
                    self.assertEqual(selected.structure_family, family)
                    self.assertEqual(selected.structure_name, ALL_STRUCTURES_BY_FAMILY[family][-1])
                    self.assertEqual(panel.morphology.isEnabled(), family != "lattice")
                    if family == "lattice":
                        self.assertEqual(selected.tpms_morphology, "sheet")
                # A new source selection replaces the draft; polling the same source does not.
                current = replace(current, structure_name="IWP", tpms_morphology="network_b")
                self.assertEqual(panel.selected_settings(), current)
                current = replace(current, imported_geometry_path="specimen.stl")
                self.assertEqual(panel.selected_settings(), current)
                for combo in (panel.family, panel.structure, panel.morphology):
                    self.assertFalse(combo.isEnabled())
                    self.assertGreaterEqual(combo.minimumWidth(), 100)
                    self.assertLessEqual(combo.maximumWidth(), 200)
            finally:
                panel.dispose()
                panel.close()

    def test_active_panel_adds_current_dimensions_without_waiting_for_preview(self):
        current = Settings()
        with patch("preview_viewport.create_preview_viewport", return_value=MagicMock(interactor=QWidget())):
            panel = ActiveGeometryPanel(lambda: current, lambda: lambda _: SimpleNamespace(n_cells=12))
            added = []
            panel.geometry_added.connect(added.append)
            try:
                panel.show()
                panel.timer.stop()
                panel.refresh()
                panel._future[1].result(timeout=5)
                panel.refresh()
                self.assertTrue(panel.add_button.isEnabled())
                panel.plotter.enable_lightkit.assert_called_once()
                self.assertTrue(panel.plotter.add_mesh.call_args.kwargs["split_sharp_edges"])
                self.assertTrue(panel.splitter.widget(0).isAncestorOf(panel.plotter.interactor))
                self.assertTrue(panel.splitter.widget(1).isAncestorOf(panel.values["size"]))
                # Inclusion uses the latest dimensions, even before the preview updates.
                current = replace(current, size=current.size + 1)
                panel.add_button.click()
                self.assertEqual(added, [current])
                panel.refresh()
                # The panel's editable dimensions are a draft until Add.
                panel.values["size"].setValue(8)
                panel.values["cell_size"].setValue(4)
                panel.values["solid_fraction"].setValue(35)
                self.assertTrue(panel.add_button.isEnabled())
                self.assertNotEqual(panel.selected_settings(), current)
                while panel._future is not None:
                    panel._future[1].result(timeout=5)
                    panel.refresh()
                self.assertTrue(panel.add_button.isEnabled())
                panel.values["size"].setFocus()
                QTest.keyClick(panel.values["size"], Qt.Key.Key_Return)
                self.assertEqual(len(added), 1)
                panel.add_button.click()
                self.assertEqual(added[-1], replace(current, size=8, cell_size=4, solid_fraction=35))
                self.assertEqual(len(added), 2)
                # Invalid drafts must not leave the old preview addable.
                panel.values["cell_size"].setValue(80)
                panel.refresh()
                self.assertFalse(panel.add_button.isEnabled())
                self.assertIn("must not exceed", panel.status.text())
            finally:
                panel.dispose()
                panel.close()

    def test_active_apply_commits_dimensions_and_builds_only_on_click(self):
        executor = MagicMock()
        pending = Future()
        executor.submit.return_value = pending
        builder = MagicMock()
        with patch("preview_viewport.create_preview_viewport", return_value=MagicMock(interactor=QWidget())), \
             patch("doe_geometry.ThreadPoolExecutor", return_value=executor):
            panel = ActiveGeometryPanel(Settings, lambda: builder)
            try:
                panel.show()
                panel.timer.stop()
                panel.refresh()
                pending.set_result(SimpleNamespace(n_cells=12))
                panel.refresh()
                executor.submit.reset_mock()
                panel.values["size"].setValue(8)
                panel.values["cell_size"].setValue(2)
                panel.refresh()
                panel.values["size"].setFocus()
                QTest.keyClick(panel.values["size"], Qt.Key.Key_Return)
                panel.refresh()
                executor.submit.assert_not_called()
                panel.values["cell_size"].lineEdit().setText("3.5")
                executor.submit.return_value = Future()
                panel.apply_button.click()
                executor.submit.assert_called_once()
                snapshot = executor.submit.call_args.args[1]
                self.assertEqual(snapshot.size, 8)
                self.assertEqual(snapshot.cell_size, 3.5)
                builder.progress_status.return_value = 'TRUBA · session · running · Generating specimen surface'
                panel.refresh()
                self.assertIn('Generating specimen surface', panel.status.text())
                self.assertEqual(panel.apply_button.size(), panel.add_button.size())
                self.assertIs(panel.apply_button.parentWidget(), panel.add_button.parentWidget())
                self.app.processEvents()
                self.assertEqual(panel.apply_button.y(), panel.add_button.y())
                self.assertLess(panel.apply_button.x(), panel.add_button.x())
                self.assertFalse(panel.apply_button.autoDefault())
                self.assertFalse(panel.apply_button.isDefault())
            finally:
                panel.dispose()
                panel.close()

    def test_current_preview_required_and_preferences_restored(self):
        with TemporaryDirectory() as folder:
            previous = getattr(self.app, "_rustmech_window_state", None)
            if previous is not None:
                self.app.removeEventFilter(previous)
            store = WindowStateStore(self.app, QSettings(str(Path(folder) / "windows.ini"), QSettings.Format.IniFormat))
            self.app._rustmech_window_state = store
            windows = []
            try:
                with patch("preview_viewport.create_preview_viewport") as factory:
                    def viewport(_):
                        return MagicMock(interactor=QWidget())
                    factory.side_effect = viewport
                    first = GeometryLibraryDialog(Settings(), lambda _: None)
                    windows.append(first)
                    first.show()
                    first._debounce.stop()
                    self.app.processEvents()
                    self.assertFalse(first.confirm.isEnabled())
                    first.accept()
                    self.assertTrue(first.isVisible())
                    future = Future()
                    future.set_result(SimpleNamespace(n_cells=12))
                    first._future = (first._revision, future)
                    first._poll()
                    self.assertTrue(first.confirm.isEnabled())
                    first.plotter.enable_lightkit.assert_called_once()
                    self.assertEqual(first.plotter.add_mesh.call_args.kwargs["ambient"], 0.22)
                    self.assertEqual(first.splitter.orientation(), Qt.Orientation.Vertical)
                    self.assertTrue(first.splitter.widget(0).isAncestorOf(first.plotter.interactor))
                    self.assertTrue(first.splitter.widget(1).isAncestorOf(first.values["size"]))
                    self.assertLess(first.plotter.interactor.mapTo(first, first.plotter.interactor.rect().bottomLeft()).y(),
                                    first.values["size"].mapTo(first, first.values["size"].rect().topLeft()).y())
                    first.structure.setCurrentText("Neovius")
                    first._debounce.stop()
                    self.assertFalse(first.confirm.isEnabled())
                    # Completion from the previous choice must not enable inclusion.
                    first._future = (first._revision - 1, future)
                    first._poll()
                    self.assertFalse(first.confirm.isEnabled())
                    first._future = (first._revision, future)
                    first._poll()
                    self.assertEqual(first.selected_settings().structure_name, "Neovius")
                    first.values["size"].setFocus()
                    QTest.keyClick(first.values["size"], Qt.Key.Key_Return)
                    self.assertTrue(first.isVisible())
                    first.resize(700, 620)
                    first.move(80, 90)
                    self.app.processEvents()
                    first.splitter.setSizes([300, 220])
                    splitter_sizes = first.splitter.sizes()
                    frame = first.frameGeometry()
                    first.reject()
                    second = GeometryLibraryDialog(Settings(), lambda _: None)
                    windows.append(second)
                    second.show()
                    second._debounce.stop()
                    self.app.processEvents()
                    self.assertEqual(second.frameGeometry(), frame)
                    self.assertEqual(second.splitter.sizes(), splitter_sizes)
                    second.values["cell_size"].setValue(80)
                    second._debounce.stop()
                    second._start_preview()
                    self.assertFalse(second.confirm.isEnabled())
                    self.assertIn("must not exceed", second.status.text())
            finally:
                for window in windows:
                    window.reject()
                    window.deleteLater()
                self.app.removeEventFilter(store)
                if previous is not None:
                    self.app._rustmech_window_state = previous
                    self.app.installEventFilter(previous)
                else:
                    del self.app._rustmech_window_state
