"""Real surfaces, folder preflight, durable sequential jobs and output routing."""
from concurrent.futures import Future
import csv
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import pyvista as pv
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QApplication, QComboBox, QDialogButtonBox, QLineEdit, QPushButton, QSplitter

from analysis_queue import AnalysisQueueWidget, QueueStore
from doe_studio import ExperimentWorker
from folder_batch import (FACES_COLUMN, PREFLIGHT_COLUMN, RUN_COLUMN, SPECIMEN_COLUMN,
                          FolderBatchDialog, discover_specimens, prepare_specimen,
                          source_digest, specimen_snapshots)
from folder_batch_results import collect_folder_output, run_remote_folder_doe
from geometry_import_dialog import GeometryImportDialog
from imported_geometry import automatic_compression_faces, compression_direction, read_surface
from window_state import WindowStateStore
from model import Settings


def process_mesh(settings):
    return SimpleNamespace(fraction=50, solve_errors=[], grid=SimpleNamespace(n_cells=8))


def process_runner(settings, mesh, output_directory=None):
    path = Path(output_directory) / 'process-result'
    path.mkdir(parents=True)
    (path / 'summary.json').write_text('{"status":"completed"}')
    return dict(status='completed', graphs={'maximum_force_N': 42}, run_folder=str(path))


class FolderBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.surface = pv.Box(bounds=(10, 14, -2, 1, 5, 11)).triangulate().clean()
        self.base = replace(Settings(), mesh_resolution=1., local_mesh_size=.8,
                            self_contact_mode='disabled', cores=1)
        self.previous = getattr(self.app, '_rustmech_window_state', None)
        if self.previous is not None:
            self.app.removeEventFilter(self.previous)
        self.store = WindowStateStore(self.app, QSettings(str(self.root / 'windows.ini'), QSettings.Format.IniFormat))
        self.app._rustmech_window_state = self.store
        self.addCleanup(self.restore_state)
        self.env = patch.dict(os.environ, {'RUSTMECH_DESIGN_POOL': str(self.root / 'pool')})
        self.env.start()
        self.addCleanup(self.env.stop)

    def restore_state(self):
        self.app.removeEventFilter(self.store)
        if self.previous is not None:
            self.app._rustmech_window_state = self.previous
            self.app.installEventFilter(self.previous)
        else:
            del self.app._rustmech_window_state

    def specimen(self, name='part.stl'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        self.surface.save(path)
        return path

    def prepared(self, name='part.stl', direction=(0, 0, 1)):
        return prepare_specimen(self.specimen(name), direction)

    def queue(self):
        widget = AnalysisQueueWidget(lambda: self.base, lambda _: None, lambda *_: {},
                                     queue_path=self.root / 'queue.json')
        self.addCleanup(widget.close)
        self.addCleanup(widget.dispose)
        return widget

    def dialog(self, queue=None):
        dialog = FolderBatchDialog(lambda: self.base, queue or self.queue())
        self.addCleanup(dialog.close)
        self.addCleanup(dialog.dispose)
        return dialog

    def test_axes_offsets_sizes_and_reverse_direction(self):
        for direction, expected in (((1, 0, 0), 4), ((0, 1, 0), 3), ((0, 0, 1), 6), ((0, 0, -1), 6)):
            with self.subTest(direction=direction):
                aligned, height, cells = automatic_compression_faces(self.surface, direction)
                self.assertAlmostEqual(height, expected)
                self.assertAlmostEqual(aligned.volume, self.surface.volume)
                self.assertEqual(set(aligned.cell_data['compression_face']), {0, 1, 2})
                np.testing.assert_allclose(compression_direction(self.surface, *cells), direction)

    def test_rule_transfers_to_differently_sized_rotated_specimen(self):
        first = self.surface.rotate_y(27, inplace=False)
        # Obtain a rule through a manually chosen pair from a known axis-aligned source.
        _, _, cells = automatic_compression_faces(self.surface, (0, 0, 1))
        direction = compression_direction(first, *cells)
        second = first.scale((1.8, 1.8, 1.8), inplace=False).translate((8, -4, 2), inplace=False)
        _, height, _ = automatic_compression_faces(second, direction)
        self.assertAlmostEqual(height, 10.8, places=5)

    def test_curved_ends_invalid_directions_and_wrong_orientation_rejected(self):
        with self.assertRaisesRegex(ValueError, 'planar end'):
            automatic_compression_faces(pv.Sphere().triangulate(), (0, 0, 1))
        with self.assertRaisesRegex(ValueError, 'planar end'):
            automatic_compression_faces(self.surface.rotate_y(23, inplace=False), (0, 0, 1))
        for direction in ((0, 0, 0), (float('nan'), 1, 0), (1, 2)):
            with self.assertRaises(ValueError):
                automatic_compression_faces(self.surface, direction)

    def test_multiple_contact_patches_are_all_marked(self):
        # Multiple coplanar patches use the same geometric rule (connectivity is
        # independently checked by read_surface for actual folder imports).
        surface = self.surface.merge(self.surface.translate((10, 0, 0), inplace=False)).extract_surface(algorithm='dataset_surface').triangulate()
        aligned, _, _ = automatic_compression_faces(surface, (0, 0, 1))
        for role in (1, 2):
            self.assertEqual(np.count_nonzero(aligned.cell_data['compression_face'] == role), 4)

    def test_discovery_natural_order_nested_packages_and_result_exclusion(self):
        for name in ('part10.stl', 'part2.stl', 'Result/old.stl', 'nested/result/old.stl', 'package/geometry.stl'):
            self.specimen(name)
        names = [path.relative_to(self.root).as_posix() for path in discover_specimens(self.root)]
        self.assertEqual(names, ['package/geometry.stl', 'part2.stl', 'part10.stl'])

    def test_folder_picker_shows_supported_files_and_remembers_selected_folder(self):
        self.specimen('part.stl')
        (self.root / 'part.stl').rename(self.root / 'part.STL')
        nested = self.root / 'nested'
        nested.mkdir()
        (nested / 'readme.txt').touch()
        (self.root / 'notes.txt').touch()
        batch = self.dialog()
        batch.folder.setText(str(self.root))
        visits = []

        def visible_names(view, expected, *, exact=False):
            model, root = view.model(), view.rootIndex()
            for _ in range(100):
                names = {model.index(row, 0, root).data() for row in range(model.rowCount(root))}
                if names == expected or (not exact and expected <= names):
                    break
                QTest.qWait(20)
            return names

        def inspect():
            picker = self.app.activeModalWidget()
            try:
                visits.append(Path(picker.directory().absolutePath()))
                if len(visits) == 1:
                    view = picker.findChild(QAbstractItemView, 'treeView')
                    picker.setViewMode(picker.ViewMode.Detail)
                    self.assertTrue(view.isVisible())
                    filters = picker.findChild(QComboBox, 'fileTypeCombo')
                    self.assertEqual(filters.currentText(), 'All files (*)')
                    self.assertTrue({'nested', 'part.STL', 'notes.txt'} <= visible_names(view, {'part.STL', 'notes.txt'}))
                    QTest.keyClick(filters, Qt.Key.Key_Down)
                    expected = {'nested', 'part.STL'}
                    self.assertEqual(visible_names(view, expected, exact=True), expected)
                    address = picker.findChild(QComboBox, 'lookInCombo').lineEdit()
                    address.setText('nested')
                    QTest.keyClick(address, Qt.Key.Key_Return)
                    QTest.keyClick(filters, Qt.Key.Key_Up)
                    self.assertEqual(Path(picker.directory().absolutePath()), nested)
                    self.assertEqual(visible_names(view, {'readme.txt'}), {'readme.txt'})
                    box = picker.findChild(QDialogButtonBox)
                    box.button(QDialogButtonBox.StandardButton.Open).click()
            except Exception as error:
                visits.append(error)
            finally:
                if picker.isVisible():
                    picker.reject()

        QTimer.singleShot(0, inspect)
        batch.choose_folder()
        selected = Path(batch.folder.text())
        batch.folder.clear()
        QTimer.singleShot(0, inspect)
        batch.choose_folder()
        batch.folder.setText(str(self.root))
        QTimer.singleShot(0, inspect)
        batch.choose_folder()

        self.assertEqual(visits, [self.root, nested, self.root])
        self.assertEqual(selected, nested)
        self.specimen('nested/other.stl')
        batch.folder.setText(str(self.root))
        batch.mode.setCurrentIndex(4)
        batch.check_folder()
        self.assertEqual([batch.table.item(row, SPECIMEN_COLUMN).text() for row in range(batch.table.rowCount())],
                         ['nested/other.stl', 'part.STL'])

    def test_preflight_diagnostics_and_source_changes(self):
        item = self.prepared()
        self.assertFalse(item.error)
        self.assertEqual(item.height, 6)
        self.assertTrue(Path(item.cached).is_file())
        pv.Plane().triangulate().save(self.root / 'open.stl')
        failed = prepare_specimen(self.root / 'open.stl', (0, 0, 1))
        self.assertIn('closed', failed.error)
        self.assertFalse(failed.cached)
        Path(item.source).write_bytes(Path(item.source).read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            list(specimen_snapshots(item, self.root, self.base, ['ansys_mapdl']))

    def test_preflight_excludes_tiny_fragments_by_area_before_finding_faces(self):
        dust = pv.Sphere(radius=.1, center=(15, 0, 15))
        chip = pv.Plane(center=(8, 0, 0), i_size=.1, j_size=.1).triangulate()
        path = self.root / 'fragments.vtp'
        self.surface.merge([dust, chip]).save(path)
        digest = source_digest(path)

        item = prepare_specimen(path, (0, 0, 1))

        self.assertFalse(item.error)
        self.assertEqual(item.excluded_components, 2)
        self.assertEqual(item.height, 6)
        self.assertAlmostEqual(read_surface(item.cached).volume, self.surface.volume)
        self.assertEqual(source_digest(path), digest)
        _, metadata = next(specimen_snapshots(item, self.root, self.base, ['ansys_mapdl']))
        self.assertEqual(metadata['excluded_components'], 2)

    def test_pencil_editor_keeps_selected_solid_and_persists_its_faces(self):
        alternate = pv.Box(bounds=(30, 33, 0, 2, 5, 9)).triangulate()
        path = self.root / 'two-components.vtp'
        self.surface.merge(alternate).save(path)
        batch = self.dialog()
        batch.folder.setText(str(self.root))
        batch.check_folder()
        batch.future.result(timeout=10)
        batch.poll()
        self.assertIn('Multiple substantial components', batch.items[0].error)
        self.assertFalse(batch.items[0].cached)
        self.assertFalse(batch.table.horizontalHeaderItem(FACES_COLUMN).icon().isNull())

        def keep_alternate(editor):
            editor.show()
            self.app.processEvents()
            self.assertIsNone(editor.component_index)
            self.assertFalse(editor.confirm.isEnabled())
            editor.component_list.setCurrentRow(int(np.argmin(editor.components.areas)))
            self.assertEqual(editor.surface.bounds.x_min, 30)
            self.assertIn('excludedComponents', editor.plotter.actors)
            self.assertTrue(editor.confirm.isEnabled(), editor.status.text())
            QTest.mouseClick(editor.confirm, Qt.MouseButton.LeftButton)
            return editor.result()

        with patch.object(GeometryImportDialog, 'exec', keep_alternate):
            batch.table.cellWidget(0, FACES_COLUMN).click()
        item = batch.items[0]
        self.assertEqual(item.height, 4, batch.status.text())
        self.assertIn('Excluded components: 1', batch.table.item(0, PREFLIGHT_COLUMN).text())
        restored = prepare_specimen(path, None, batch.saved_faces(path))
        self.assertEqual(restored.cached, item.cached)
        self.assertEqual(restored.cells, item.cells)
        self.assertEqual(restored.component, item.component)

    def test_automatic_faces_on_padded_tpms_keep_height_and_survive_edit(self):
        from skimage.measure import marching_cubes
        from imported_geometry import SurfaceComponents, read_polygonal_surface

        coordinates = np.linspace(0, 4 * np.pi, 41)
        x, y, z = np.meshgrid(coordinates, coordinates, coordinates, indexing='ij', sparse=True)
        field = np.abs(np.sin(x) * np.cos(y) + np.sin(y) * np.cos(z) + np.sin(z) * np.cos(x)) - .35
        points, faces, _, _ = marching_cubes(np.pad(field, 1, constant_values=1), 0, spacing=(.5,) * 3)
        surface = pv.PolyData(points, np.column_stack((np.full(len(faces), 3), faces)).ravel())
        path = self.root / 'tpms.stl'
        surface.merge(pv.Sphere(radius=.08, center=(5, 6, 23))).save(path)
        digest = source_digest(path)
        batch = self.dialog()
        batch.folder.setText(str(self.root))
        batch.check_folder()
        batch.future.result(timeout=10)
        batch.poll()
        specimen = batch.items[0]
        self.assertTrue(specimen.cached, specimen.error)
        self.assertGreater(specimen.excluded_components, 0)
        self.assertEqual(specimen.normalized_direction, (0, 0, 1))
        self.assertIn('End faces flattened', batch.table.item(0, PREFLIGHT_COLUMN).text())
        original = SurfaceComponents(read_polygonal_surface(path)).solid(specimen.component)
        self.assertAlmostEqual(specimen.height, original.bounds.z_max - original.bounds.z_min)
        aligned = read_surface(specimen.cached)
        faces = aligned.faces.reshape(-1, 4)[:, 1:]
        for role, height in ((1, 0), (2, specimen.height)):
            contact = faces[aligned.cell_data['compression_face'] == role]
            self.assertGreater(len(contact), 0)
            np.testing.assert_allclose(aligned.points[contact, 2], height)

        def save(editor):
            editor.show()
            self.app.processEvents()
            self.assertEqual(tuple(editor.selected), specimen.cells)
            self.assertTrue(editor.confirm.isEnabled(), editor.status.text())
            editor.confirm.click()
            return editor.result()

        with patch.object(GeometryImportDialog, 'exec', save):
            batch.table.cellWidget(0, FACES_COLUMN).click()
        self.assertEqual(source_digest(path), digest)
        restored = prepare_specimen(path, None, batch.saved_faces(path))
        self.assertEqual(restored.cached, specimen.cached, batch.status.text())
        self.assertEqual(restored.normalized_direction, (0, 0, 1))

    def test_snapshots_keep_loading_units_material_and_unique_output_names(self):
        first, second = self.prepared('a/same.stl'), self.prepared('b/same.stl')
        jobs = [*specimen_snapshots(first, self.root, self.base, ['ansys_mapdl', 'kratos']),
                *specimen_snapshots(second, self.root, self.base, ['ansys_mapdl'])]
        self.assertEqual(len({job['destination'] for _, job in jobs}), 3)
        for settings, job in jobs:
            self.assertEqual(settings.compression, self.base.compression)
            self.assertEqual(settings.young_modulus, self.base.young_modulus)
            self.assertEqual(settings.mesh_type, 'TET4')
            self.assertTrue(Path(job['destination']).is_relative_to(self.root / 'Result'))

    def test_enqueue_is_atomic_deduplicated_and_does_not_start(self):
        queue = self.queue()
        jobs = list(specimen_snapshots(self.prepared(), self.root, self.base, ['ansys_mapdl']))
        with patch('doe_studio.AnalysisProcess') as process:
            rows = queue.add_folder_snapshots(jobs)
            self.assertEqual(queue.add_folder_snapshots(jobs), [])
            process.assert_not_called()
        self.assertEqual(len(QueueStore(queue.store.path).load()), 1)
        self.assertTrue((self.root / 'Result/toplu_ozet.csv').is_file())
        with (self.root / 'Result/toplu_ozet.csv').open(encoding='utf-8-sig', newline='') as stream:
            csv_rows = list(csv.reader(stream))
        self.assertEqual(len(csv_rows), 2)
        self.assertTrue((Path(rows[0].folder_job['destination']) / 'job.json').is_file())
        with patch.object(queue.store, 'save', side_effect=OSError('disk full')):
            changed = [(replace(jobs[0][0], compression=.3), dict(jobs[0][1], key='changed'))]
            with self.assertRaises(OSError):
                queue.add_folder_snapshots(changed)
        self.assertEqual(len(queue.rows), 1)

    def test_sequential_failure_continue_restart_and_output_routing(self):
        queue = self.queue()
        jobs = [*specimen_snapshots(self.prepared('part1.stl'), self.root, self.base, ['ansys_mapdl']),
                *specimen_snapshots(self.prepared('part2.stl'), self.root, self.base, ['ansys_mapdl'])]
        queue.add_folder_snapshots(jobs)
        calls = []
        def process(target, index, settings, **kwargs):
            calls.append((index, target.__self__._output_directory))
            future = Future()
            future.set_result(dict(r_idx=index, status='Failed: example' if index == 0 else 'Completed', max_force_n=12))
            return future
        with patch('doe_studio.AnalysisProcess', side_effect=process):
            queue.start_batch_solve()
            queue._poll_worker()
            queue._poll_worker()
        self.assertEqual([index for index, _ in calls], [0, 1])
        self.assertEqual(queue.rows_to_run(), [0])
        restored = QueueStore(queue.store.path).load()
        self.assertEqual(restored[1].status, 'Completed')
        self.assertEqual(restored[1].folder_job, queue.rows[1].folder_job)
        report = json.loads((Path(restored[0].folder_job['destination']) / 'job.json').read_text())
        self.assertIn('Failed', report['status'])
        summary = (self.root / 'Result/toplu_ozet.csv').read_text(encoding='utf-8-sig')
        self.assertIn('part1.stl', summary)
        self.assertIn('Completed', summary)

    def test_worker_passes_output_directory_to_solver(self):
        mesh = SimpleNamespace(fraction=50, solve_errors=[], grid=SimpleNamespace(n_cells=8))
        calls = []
        def runner(settings, mesh, **kwargs):
            calls.append(kwargs)
            return dict(status='completed', graphs={'maximum_force_N': 42}, run_folder=str(self.root))
        worker = ExperimentWorker(lambda _: mesh, runner, str(self.root / 'Result'))
        payload = worker._solve_row(0, self.base)
        self.assertEqual(payload['status'], 'Completed')
        self.assertEqual(calls, [dict(output_directory=str(self.root / 'Result'))])

    def test_run_paths_write_human_and_raw_under_selected_folder(self):
        from nonlinear_compression import RunPaths
        paths = RunPaths.create(output_directory=self.root / 'Result' / 'specimen')
        self.assertTrue(paths.raw.is_relative_to(paths.human))
        self.assertTrue((paths.human / '04_Graphs').is_dir())
        self.assertTrue(paths.raw.is_dir())

    def test_ready_rows_checked_and_manual_mode_requires_selection(self):
        dialog = self.dialog()
        self.specimen()
        dialog.folder.setText(str(self.root))
        dialog.mode.setCurrentIndex(4)
        dialog.check_folder()
        self.assertEqual(dialog.table.item(0, RUN_COLUMN).checkState(), Qt.CheckState.Unchecked)
        self.assertIn('Face selection', dialog.table.item(0, PREFLIGHT_COLUMN).text())
        dialog.items[0] = prepare_specimen(self.root / 'part.stl', (0, 0, 1))
        dialog.refresh_table()
        self.assertEqual(dialog.table.item(0, RUN_COLUMN).checkState(), Qt.CheckState.Checked)
        dialog.table.item(0, RUN_COLUMN).setCheckState(Qt.CheckState.Unchecked)
        dialog.refresh_table()
        self.assertEqual(dialog.table.item(0, RUN_COLUMN).checkState(), Qt.CheckState.Unchecked)
        for button in dialog.findChildren(QPushButton):
            self.assertFalse(button.autoDefault())
            self.assertFalse(button.isDefault())

    def test_row_button_saves_only_its_specimen_and_reuses_faces_after_reopening(self):
        first = self.specimen('part1.stl')
        second = self.specimen('part2.stl')
        _, _, selected = automatic_compression_faces(read_surface(second), (1, 0, 0))
        batch = self.dialog()
        batch.folder.setText(str(self.root))
        batch.mode.setCurrentIndex(4)
        batch.check_folder()
        batch.show()
        self.app.processEvents()
        batch.table.selectRow(0)
        button = batch.table.cellWidget(1, FACES_COLUMN)
        self.assertEqual(button.accessibleDescription(), second.name)

        bounds = []

        def save(editor):
            editor.show()
            editor.resize(740, 560)
            editor.move(50, 65)
            self.app.processEvents()
            self.assertIn(second.name, editor.windowTitle())
            self.assertEqual(editor.confirm.text(), 'Save and close')
            self.assertFalse(editor.confirm.isEnabled())
            for role, cell in enumerate(selected):
                editor.set_role(role)
                editor.select_cell(cell)
            self.assertTrue(editor.confirm.isEnabled())
            self.assertTrue(editor.show_platens.isChecked())
            fixed = editor.plotter.actors['fixed_platen']
            moving = editor.plotter.actors['moving_platen']
            self.assertAlmostEqual(fixed.GetBounds()[1], 10)
            self.assertAlmostEqual(moving.GetBounds()[0], 14)
            self.assertNotEqual(fixed.prop.color, moving.prop.color)
            self.assertFalse(fixed.GetPickable())
            self.assertFalse(moving.GetPickable())
            editor.show_platens.click()
            self.assertFalse(fixed.GetVisibility())
            self.assertFalse(moving.GetVisibility())
            self.assertTrue(editor.plotter.actors['selection0'].GetVisibility())
            self.assertTrue(editor.plotter.actors['selection1'].GetVisibility())
            self.assertTrue(editor.confirm.isEnabled())
            editor.set_role(0)
            editor.select_cell(selected[0])
            fixed = editor.plotter.actors['fixed_platen']
            self.assertFalse(fixed.GetVisibility())
            editor.show_platens.click()
            self.assertTrue(fixed.GetVisibility())
            self.assertTrue(moving.GetVisibility())
            with patch('folder_batch.store_surface', side_effect=OSError('Disk unavailable')):
                QTest.mouseClick(editor.confirm, Qt.MouseButton.LeftButton)
            self.assertTrue(editor.isVisible())
            self.assertEqual(editor.status.text(), 'Disk unavailable')
            self.assertIsNone(batch.saved_faces(second))
            bounds.append(editor.frameGeometry())
            QTest.mouseClick(editor.confirm, Qt.MouseButton.LeftButton)
            return editor.result()

        with patch.object(GeometryImportDialog, 'exec', save):
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        self.assertFalse(batch.items[0].cached)
        self.assertEqual(batch.items[1].cells, selected, batch.status.text())
        self.assertEqual(batch.items[1].height, 4)
        self.assertIsNone(batch.saved_faces(first))
        self.assertEqual(batch.saved_faces(second), (source_digest(second), selected))
        self.assertIs(batch.table.cellWidget(1, FACES_COLUMN), button)
        self.assertEqual(batch.table.item(1, RUN_COLUMN).checkState(), Qt.CheckState.Checked)

        def cancel(editor):
            editor.show()
            self.app.processEvents()
            self.assertEqual(tuple(editor.selected), selected)
            self.assertEqual(editor.frameGeometry(), bounds[0])
            editor.set_role(0)
            editor.select_cell(selected[1])
            editor.reject()
            return editor.result()

        with patch.object(GeometryImportDialog, 'exec', cancel):
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        self.assertEqual(batch.items[1].cells, selected)
        self.assertEqual(batch.status.text(), 'Specimen faces saved.')
        batch.close()

        reopened = self.dialog(batch.queue)
        reopened.preferences = QSettings(self.store.settings.fileName(), QSettings.Format.IniFormat)
        reopened.folder.setText(str(self.root))
        reopened.mode.setCurrentIndex(4)
        reopened.check_folder()
        reopened.future.result(timeout=10)
        reopened.poll()
        self.assertIsNone(reopened.future)
        self.assertEqual(reopened.items[1].cells, selected)
        reopened.enqueue_ready()
        self.assertEqual(len(reopened.queue.rows), 1)
        self.assertEqual(reopened.queue.rows[0].design_settings.size, 4)

        self.surface.translate((4, 0, 0), inplace=False).save(second)
        reopened.check_folder()
        reopened.future.result(timeout=10)
        reopened.poll()
        self.assertFalse(reopened.items[1].cached)
        self.assertEqual(reopened.items[1].error, 'Face selection required')

    def test_not_ready_open_surface_can_be_inspected(self):
        path = self.root / 'open.stl'
        # Remove a side wall while keeping both compression planes intact.
        centers = self.surface.cell_centers().points
        self.surface.remove_cells(np.flatnonzero(np.isclose(centers[:, 1], -2))).save(path)
        batch = self.dialog()
        batch.folder.setText(str(self.root))
        batch.check_folder()
        batch.future.result(timeout=10)
        batch.poll()
        self.assertIn('closed', batch.items[0].error)

        def inspect(editor):
            editor.show()
            self.app.processEvents()
            self.assertTrue(editor.isVisible())
            centers = editor.surface.cell_centers().points
            for role, z in enumerate((5, 11)):
                editor.set_role(role)
                editor.select_cell(int(np.flatnonzero(np.isclose(centers[:, 2], z))[0]))
            self.assertFalse(editor.confirm.isEnabled())
            self.assertIn('closed', editor.status.text())
            editor.reject()
            return editor.result()

        with patch.object(GeometryImportDialog, 'exec', autospec=True, side_effect=inspect) as show_editor:
            batch.table.cellWidget(0, FACES_COLUMN).click()
        show_editor.assert_called_once()
        self.assertFalse(batch.items[0].cached)
        self.assertIsNone(batch.saved_faces(path))

    def test_automatic_faces_restore_after_modal_editor_opens(self):
        dialog = self.dialog()
        specimen = self.prepared()
        dialog.root, dialog.base = self.root, self.base
        dialog.items = [specimen]
        dialog.refresh_table()
        dialog.show()
        restored = []

        def restore(editor):
            restored.append((self.app.activeModalWidget() is editor, editor.isVisible()))
            restore_faces(editor)
            restored.append(tuple(editor.selected))
            editor.reject()

        def dismiss():
            editor = self.app.activeModalWidget()
            if isinstance(editor, GeometryImportDialog):
                editor.reject()

        restore_faces = GeometryImportDialog._restore_faces
        fallback = QTimer(dialog, singleShot=True, interval=2500)
        fallback.timeout.connect(dismiss)
        fallback.start()
        with patch.object(GeometryImportDialog, '_restore_faces', restore):
            QTest.mouseClick(dialog.table.cellWidget(0, FACES_COLUMN), Qt.MouseButton.LeftButton)
        fallback.stop()
        self.assertEqual(restored, [(True, True), specimen.cells], dialog.status.text())

    def test_queued_specimens_move_to_right_panel_and_keep_solver_options(self):
        dialog = self.dialog()
        dialog.root, dialog.base = self.root, self.base
        dialog.items = [self.prepared('part1.stl'), self.prepared('part2.stl')]
        dialog.refresh_table()
        dialog.set_busy(False)
        dialog.show()
        self.app.processEvents()
        dialog.table.item(1, RUN_COLUMN).setCheckState(Qt.CheckState.Unchecked)
        dialog.solver.setCurrentIndex(1)
        dialog.enqueue.click()

        self.assertTrue(dialog.table.isRowHidden(0))
        self.assertFalse(dialog.table.isRowHidden(1))
        self.assertEqual(dialog.queue_table.rowCount(), 1)
        self.assertEqual(dialog.queue_table.item(0, 0).text(), 'part1.stl')
        self.assertEqual(dialog.queue_table.item(0, 1).text(), 'ansys_mapdl')
        buttons = [button for button in dialog.findChildren(QPushButton)
                   if button.text() in ('Add to Queue', 'Analysis Queue', 'Close')]
        self.assertEqual(len(buttons), 3)
        self.assertEqual(len({button.size() for button in buttons}), 1)
        dialog.solver.setCurrentIndex(2)
        self.assertFalse(dialog.table.isRowHidden(0))
        dialog.solver.setCurrentIndex(1)
        dialog.queue.table.selectRow(0)
        dialog.queue.delete_selected_rows()
        self.assertFalse(dialog.table.isRowHidden(0))
        self.assertEqual(dialog.queue_table.rowCount(), 0)

    def test_remove_from_right_panel_persists_and_returns_only_the_selected_job(self):
        dialog = self.dialog()
        unrelated = dialog.queue.add_snapshot(self.base)
        dialog.root, dialog.base = self.root, self.base
        dialog.items = [self.prepared('part1.stl'), self.prepared('part2.stl')]
        dialog.refresh_table()
        dialog.set_busy(False)
        dialog.enqueue.click()
        first, second = dialog.queue.rows[1:]
        self.assertFalse(dialog.remove_from_queue.isEnabled())
        dialog.queue_table.selectRow(0)
        self.assertTrue(dialog.remove_from_queue.isEnabled())

        with patch.object(dialog.queue.store, 'save', side_effect=OSError('disk full')):
            dialog.remove_from_queue.click()
        self.assertEqual(dialog.status.text(), 'disk full')
        self.assertTrue(dialog.table.isRowHidden(0))
        self.assertEqual(dialog.queue_table.rowCount(), 2)

        with patch.object(dialog.queue, '_active_mode', 'solve'):
            dialog.remove_from_queue.click()
            self.assertIn('Stop the analysis queue', dialog.status.text())
            self.assertTrue(dialog.table.isRowHidden(0))

        dialog.remove_from_queue.click()
        self.assertEqual([job.exp_id for job in QueueStore(dialog.queue.store.path).load()],
                         [unrelated.exp_id, second.exp_id])
        self.assertEqual(dialog.queue_table.rowCount(), 1)
        self.assertEqual(dialog.queue_table.item(0, 0).text(), 'part2.stl')
        self.assertFalse(dialog.remove_from_queue.isEnabled())
        self.assertFalse(dialog.table.isRowHidden(0))
        self.assertTrue(dialog.table.isRowHidden(1))
        self.assertEqual(dialog.table.selectionModel().selectedRows()[0].row(), 0)
        self.assertTrue((Path(first.folder_job['destination']) / 'job.json').is_file())

        dialog.enqueue.click()
        self.assertTrue(dialog.table.isRowHidden(0))
        self.assertEqual(dialog.queue.rows[-1].folder_job['key'], first.folder_job['key'])

    def test_async_check_and_enter_never_start_or_enqueue(self):
        dialog = self.dialog()
        self.specimen()
        dialog.folder.setText(str(self.root))
        with patch('doe_studio.AnalysisProcess') as process:
            QTest.keyClick(dialog.folder, Qt.Key.Key_Return)
            self.assertFalse(dialog.items)
            dialog.check_folder()
            for _ in range(200):
                if dialog.future is None:
                    break
                QTest.qWait(10)
                dialog.poll()
            self.assertIsNone(dialog.future)
            self.assertTrue(dialog.items[0].cached)
            self.assertEqual(dialog.table.item(0, RUN_COLUMN).checkState(), Qt.CheckState.Checked)
            self.assertFalse(dialog.queue.rows)
            process.assert_not_called()

    def test_remote_output_copy_keeps_original_and_is_recorded(self):
        queue = self.queue()
        row = queue.add_folder_snapshots(list(specimen_snapshots(self.prepared(), self.root, self.base, ['ansys_mapdl'])))[0]
        downloaded = self.root / 'downloaded'
        downloaded.mkdir()
        (downloaded / 'summary.json').write_text('{"status":"completed"}')
        payload = collect_folder_output(row, dict(r_idx=0, status='Completed', run_folder=str(downloaded)))
        target = Path(payload['run_folder'])
        self.assertTrue(target.is_relative_to(Path(row.folder_job['destination'])))
        self.assertTrue((target / 'summary.json').is_file())
        self.assertTrue((downloaded / 'summary.json').is_file())

    def test_solver_incompatibility_rejected_before_any_job_is_saved(self):
        queue = self.queue()
        base = replace(self.base, self_contact_mode='frictional')
        jobs = list(specimen_snapshots(self.prepared(), self.root, base, ['ansys_mapdl', 'kratos']))
        with self.assertRaisesRegex(ValueError, 'Kratos'):
            queue.add_folder_snapshots(jobs)
        self.assertFalse(queue.rows)

    def test_batch_runner_forwards_destination_to_analysis(self):
        from mechanical_analysis import run_batch_experiment
        folder = self.root / 'solver-run'
        folder.mkdir()
        (folder / 'summary.json').write_text('{"status":"completed"}')
        backend = object()
        with patch('mechanical_analysis.run_analysis', return_value=folder) as analysis:
            result = run_batch_experiment(self.base, object(), lambda: backend, output_directory=self.root / 'Result')
        self.assertEqual(analysis.call_args.kwargs, dict(images=False, output_directory=self.root / 'Result'))
        self.assertEqual(result['run_folder'], str(folder))

    def test_actual_spawn_process_keeps_destination_and_returns_output(self):
        from analysis_process import AnalysisProcess
        destination = self.root / 'Result' / 'spawn'
        worker = ExperimentWorker(process_mesh, process_runner, str(destination))
        process = AnalysisProcess(worker._solve_row, 0, self.base)
        deadline = time.monotonic() + 25
        while not process.done() and time.monotonic() < deadline:
            QTest.qWait(20)
        if not process.done():
            process.force_stop()
            self.fail('Folder worker did not finish')
        result = process.result()
        self.assertEqual(result['status'], 'Completed')
        self.assertEqual(result['max_force_n'], 42)
        self.assertTrue((Path(result['run_folder']) / 'summary.json').is_file())
        self.assertTrue(Path(result['run_folder']).is_relative_to(destination))

    def test_remote_raw_outputs_and_summary_paths_are_relocated(self):
        queue = self.queue()
        row = queue.add_folder_snapshots(list(specimen_snapshots(self.prepared(), self.root, self.base, ['ansys_mapdl'])))[0]
        source = self.root / 'download' / 'HumanResults' / 'Run_test'
        raw = self.root / 'download' / 'results' / 'Run_test'
        source.mkdir(parents=True)
        raw.mkdir(parents=True)
        (raw / 'solver.log').write_text('solver diagnostics')
        (source / 'summary.json').write_text(json.dumps(dict(raw=str(raw), result=str(source))))
        with patch('truba_remote.run_remote_doe', return_value=dict(r_idx=0, status='Completed', run_folder=str(source))):
            payload = run_remote_folder_doe(0, self.base, None, True, row.folder_job['destination'])
        copied = Path(payload['run_folder'])
        self.assertTrue((copied / 'Raw' / 'solver.log').is_file())
        report = json.loads((copied / 'summary.json').read_text())
        self.assertEqual(report, dict(raw=str(copied / 'Raw'), result=str(copied)))
        self.assertTrue((raw / 'solver.log').is_file())

    def test_remote_copy_failure_keeps_solver_outcome_and_exposes_error(self):
        with patch('truba_remote.run_remote_doe', return_value=dict(r_idx=0, status='Completed', run_folder=str(self.root / 'missing'))):
            payload = run_remote_folder_doe(0, self.base, None, True, str(self.root / 'Result'))
        self.assertEqual(payload['status'], 'Completed')
        self.assertIn('unavailable', payload['folder_export_error'])

    def test_folder_window_restores_size_position_and_maximized_state(self):
        first = self.dialog()
        first.show()
        self.app.processEvents()
        first.resize(720, 500)
        first.move(40, 50)
        self.app.processEvents()
        splitter = first.findChild(QSplitter, 'folderBatchPanels')
        splitter.setSizes([300, 400])
        sizes = splitter.sizes()
        geometry = first.frameGeometry()
        first.hide()
        second = self.dialog()
        second.show()
        self.app.processEvents()
        self.assertEqual(second.frameGeometry(), geometry)
        self.assertEqual(second.findChild(QSplitter, 'folderBatchPanels').sizes(), sizes)
        second.showMaximized()
        self.app.processEvents()
        normal = second.normalGeometry().size()
        second.hide()
        third = self.dialog()
        third.show()
        self.app.processEvents()
        self.assertTrue(third.isMaximized())
        third.showNormal()
        self.app.processEvents()
        self.assertEqual(third.size(), normal)


if __name__ == '__main__':
    unittest.main()
