"""Real surfaces, folder preflight, durable sequential jobs and output routing."""
from concurrent.futures import Future, ThreadPoolExecutor
import csv
from dataclasses import replace
import json
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
from stat import S_IFDIR, S_IFREG
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pyvista as pv
from PySide6.QtCore import QEvent, QLocale, QSettings, Qt, QTimer
from PySide6.QtGui import QHelpEvent, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QApplication, QComboBox, QDialogButtonBox, QLineEdit, QPushButton

from analysis_queue import AnalysisQueueWidget, QueueStore
from doe_studio import ExperimentWorker
from folder_batch import (ANALYSIS_COLUMN, FACES_COLUMN, PREFLIGHT_COLUMN, RUN_COLUMN, SPECIMEN_COLUMN,
                          FolderBatchDialog, discover_specimens, prepare_specimen,
                          source_digest, specimen_snapshots)
from folder_batch_results import (LEGACY_BATCH_DIRECTORY, batch_directory, collect_folder_output,
                                  relocate_queue_row, run_remote_folder_doe, write_folder_reports)
from geometry_import_dialog import GeometryImportDialog
from imported_geometry import automatic_compression_faces, compression_direction, read_surface
from window_state import WindowStateStore
from model import Settings
from truba_remote import RemoteContext


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
        dialog = FolderBatchDialog(lambda: self.base, process_mesh, process_runner,
                                   SimpleNamespace(auth=lambda: None),
                                   lambda root: (queue or self.queue()).migrate_batch_workspace(root))
        self.addCleanup(dialog.close)
        self.addCleanup(dialog.dispose)
        return dialog

    def finish_preparation(self, dialog):
        self.assertIsNotNone(dialog.future)
        # poll() delivers both validation success and failure on the UI thread.
        for _ in range(1000):
            dialog.poll()
            if dialog.future is None:
                return
            QTest.qWait(10)
        self.fail('Batch preparation did not finish')

    def simulate_upload(self, dialog):
        auth = SimpleNamespace(username='test-user')
        context = RemoteContext(cores=2, session_id='123')
        dialog.remote_controller = SimpleNamespace(auth=lambda: auth, context=lambda: context,
                                                  require=Mock(return_value=(context, auth)),
                                                  require_analysis=Mock(return_value=(context, auth)))
        dialog.remote_destination = auth.username, PurePosixPath('project/batch')

        def stage(active_auth, sources, *, settings_by_geometry, relative):
            self.assertEqual(list(sources), list(settings_by_geometry))
            self.assertIs(active_auth, auth)
            self.assertEqual(relative, PurePosixPath('project/batch'))
            return {source: dict(username=auth.username, sha256=sha256(Path(source).read_bytes()).hexdigest(),
                                 directory=relative.as_posix())
                    for source in sources}

        upload = patch('truba_files.stage_batch_geometries', side_effect=stage)
        self.addCleanup(upload.stop)
        return upload.start()

    def test_reopened_folder_batch_restores_inputs_and_local_choices(self):
        batch = self.dialog()
        batch.folder.setText(str(self.root))
        batch.mode.setCurrentIndex(1)
        batch.mesh_resolution.setValue(.6)
        batch.local_mesh_size.setValue(.3)
        batch.execution_target.setCurrentIndex(batch.execution_target.findData("local"))
        batch.solver.setCurrentIndex(1)
        batch.mesh_type.setCurrentText("TET10")
        batch.execution_target.setCurrentIndex(batch.execution_target.findData("truba"))
        batch.reject()

        restarted = self.dialog()
        self.assertEqual(restarted.folder.text(), str(self.root))
        self.assertEqual(restarted.mode.currentIndex(), 1)
        self.assertAlmostEqual(restarted.mesh_resolution.value(), .6)
        self.assertAlmostEqual(restarted.local_mesh_size.value(), .3)
        self.assertEqual(restarted.execution_target.currentData(), "truba")
        self.assertIsNone(restarted.future)
        restarted.execution_target.setCurrentIndex(restarted.execution_target.findData("local"))
        self.assertEqual(restarted.solver.currentIndex(), 1)
        self.assertEqual(restarted.mesh_type.currentText(), "TET10")

    def test_truba_picker_navigates_and_restores_window_size(self):
        from truba_folder_picker import TrubaFolderPicker

        parent = self.dialog()
        auth = SimpleNamespace(username='test-user')
        root = PurePosixPath()
        selected = root / 'Compression 1'
        with ThreadPoolExecutor(max_workers=1) as executor, patch(
                'truba_folder_picker.list_batch_folders',
                side_effect=lambda auth, directory: ['Compression 1'] if directory == root else []):
            picker = TrubaFolderPicker(parent, auth, executor, root)
            picker.show()
            self.assertFalse(picker.use.isEnabled())
            picker.future.result(timeout=5)
            picker._poll()
            self.assertEqual(picker.folders.item(0).text(), selected.name)
            picker.folders.itemActivated.emit(picker.folders.item(0))
            picker.future.result(timeout=5)
            picker._poll()
            self.assertEqual(picker.directory, selected)
            self.assertTrue(picker.address.text().endswith('/RustMech/Compression 1'))
            picker.resize(601, 381)
            picker.use.click()
            self.assertEqual(picker.result(), picker.DialogCode.Accepted)

            reopened = TrubaFolderPicker(parent, auth, executor, selected)
            self.addCleanup(reopened.close)
            reopened.show()
            self.assertEqual(reopened.size(), picker.size())

    def test_mesh_display_precision_is_saved_without_rounding_batch_values(self):
        batch = self.dialog()
        self.assertEqual(batch.mesh_precision.value(), 3)
        for control in (batch.mesh_resolution, batch.local_mesh_size):
            control.setLocale(QLocale(QLocale.Language.English))
            control.setValue(0.123456)
            self.assertEqual(control.text(), '0.123')
        QTest.mouseClick(batch.mesh_precision.btn_up, Qt.MouseButton.LeftButton)
        self.assertEqual(batch.mesh_resolution.text(), '0.1235')
        self.assertEqual(batch.local_mesh_size.text(), '0.1235')
        batch.reject()

        restarted = self.dialog()
        self.assertEqual(restarted.mesh_precision.value(), 4)
        self.assertAlmostEqual(restarted.mesh_resolution.value(), 0.123456)
        self.assertAlmostEqual(restarted.local_mesh_size.value(), 0.123456)

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
        for name in ('part10.stl', 'part2.stl', 'Result/old.stl', 'nested/result/old.stl', 'package/geometry.stl',
                     'BatchAnaylsis/Prepared/cached.stl', 'BatchAnalysis/Results/generated.stl'):
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
            self.assertTrue(Path(job['destination']).is_relative_to(batch_directory(self.root) / 'Results'))

    def test_enqueue_is_atomic_deduplicated_and_does_not_start(self):
        queue = self.queue()
        jobs = list(specimen_snapshots(self.prepared(), self.root, self.base, ['ansys_mapdl']))
        with patch('doe_studio.AnalysisProcess') as process:
            rows = queue.add_folder_snapshots(jobs)
            self.assertEqual(queue.add_folder_snapshots(jobs), [])
            process.assert_not_called()
        self.assertEqual(len(QueueStore(queue.store.path).load()), 1)
        with (batch_directory(self.root) / 'Results/toplu_ozet.csv').open(encoding='utf-8-sig', newline='') as stream:
            csv_rows = list(csv.reader(stream))
        self.assertEqual(len(csv_rows), 2)
        self.assertTrue((Path(rows[0].folder_job['destination']) / 'job.json').is_file())
        with patch.object(queue.store, 'save', side_effect=OSError('disk full')):
            changed = [(replace(jobs[0][0], compression=.3), dict(jobs[0][1], key='changed'))]
            with self.assertRaises(OSError):
                queue.add_folder_snapshots(changed)
        self.assertEqual(len(queue.rows), 1)

    def test_legacy_batch_workspace_moves_and_restores_queued_paths(self):
        queue = self.queue()
        prepared = self.prepared()
        row = queue.add_folder_snapshots(specimen_snapshots(prepared, self.root, self.base, ['ansys_mapdl']))[0]
        workspace = batch_directory(self.root)
        legacy = self.root / LEGACY_BATCH_DIRECTORY
        row.run_folder = str(Path(row.folder_job['destination']) / 'Run_example')
        workspace.rename(legacy)
        old_row = relocate_queue_row(row, [(workspace, legacy)])
        Path(old_row.run_folder).mkdir()
        report = Path(old_row.run_folder) / 'summary.json'
        report.write_text(json.dumps({'run_folder': old_row.run_folder}), encoding='utf-8')
        queue.rows[:] = [old_row]
        queue.store.save(queue.rows)
        write_folder_reports(queue.rows)

        # A write failure must leave the original files and queue valid.
        with patch.object(queue.store, '_write_rows', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                queue.store.migrate_batch_workspaces(queue.rows)
        self.assertFalse(workspace.exists())
        self.assertTrue(Path(old_row.design_settings.imported_geometry_path).is_file())
        self.assertEqual(json.loads(report.read_text(encoding='utf-8'))['run_folder'], old_row.run_folder)

        # Merge a non-conflicting corrected directory and migrate at startup.
        workspace.mkdir()
        extra = workspace / 'keep.txt'
        extra.write_text('keep', encoding='utf-8')
        reopened = self.queue()
        self.assertFalse(legacy.exists())
        self.assertEqual(extra.read_text(encoding='utf-8'), 'keep')
        restored, = QueueStore(queue.store.path).load()
        self.assertEqual(restored.exp_id, row.exp_id)
        self.assertEqual(restored.folder_job, row.folder_job)
        self.assertEqual(restored.run_folder, row.run_folder)
        self.assertEqual(restored.design_settings.imported_geometry_path, prepared.cached)
        self.assertTrue(Path(prepared.preview).is_file())
        migrated_report = Path(restored.run_folder) / 'summary.json'
        self.assertEqual(json.loads(migrated_report.read_text(encoding='utf-8'))['run_folder'], row.run_folder)
        summary = workspace / 'Results' / 'toplu_ozet.csv'
        self.assertIn(row.run_folder, summary.read_text(encoding='utf-8-sig'))
        self.assertFalse(reopened.store.migrate_batch_workspaces(reopened.rows))

        # Recover if a previous process moved the directory before saving the queue.
        reopened.store.save([old_row])
        recovered = self.queue()
        self.assertEqual(recovered.rows[0].design_settings.imported_geometry_path, prepared.cached)
        self.assertEqual(recovered.rows[0].run_folder, row.run_folder)

    def test_check_folder_moves_unqueued_legacy_workspace(self):
        self.specimen()
        legacy = self.root / LEGACY_BATCH_DIRECTORY
        (legacy / 'Images').mkdir(parents=True)
        (legacy / 'Images' / 'existing.png').write_bytes(b'stored image')
        dialog = self.dialog()
        dialog.folder.setText(str(self.root))
        dialog.mode.setCurrentIndex(4)
        dialog.check_folder()
        self.assertFalse(legacy.exists())
        self.assertEqual((batch_directory(self.root) / 'Images' / 'existing.png').read_bytes(), b'stored image')
        self.assertTrue((batch_directory(self.root) / 'Results').is_dir())

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
        summary = (batch_directory(self.root) / 'Results/toplu_ozet.csv').read_text(encoding='utf-8-sig')
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

    def test_ready_rows_wait_for_selection_and_manual_mode_requires_faces(self):
        dialog = self.dialog()
        self.specimen()
        dialog.folder.setText(str(self.root))
        dialog.mode.setCurrentIndex(4)
        dialog.check_folder()
        self.assertEqual(dialog.table.item(0, RUN_COLUMN).checkState(), Qt.CheckState.Unchecked)
        self.assertIn('Face selection', dialog.table.item(0, PREFLIGHT_COLUMN).text())
        dialog.items[0] = prepare_specimen(self.root / 'part.stl', (0, 0, 1))
        dialog.refresh_table()
        self.assertEqual(dialog.table.item(0, RUN_COLUMN).checkState(), Qt.CheckState.Unchecked)
        self.assertFalse(dialog.send.isEnabled())
        dialog.table.item(0, RUN_COLUMN).setCheckState(Qt.CheckState.Checked)
        dialog.refresh_table()
        self.assertEqual(dialog.table.item(0, RUN_COLUMN).checkState(), Qt.CheckState.Checked)
        self.assertTrue(dialog.send.isEnabled())
        self.assertIsNone(dialog.runner)
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
        self.assertEqual(batch.table.item(1, RUN_COLUMN).checkState(), Qt.CheckState.Unchecked)

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

        reopened = self.dialog()
        reopened.preferences = QSettings(self.store.settings.fileName(), QSettings.Format.IniFormat)
        reopened.folder.setText(str(self.root))
        reopened.mode.setCurrentIndex(4)
        reopened.check_folder()
        reopened.future.result(timeout=10)
        reopened.poll()
        self.assertIsNone(reopened.future)
        self.assertEqual(reopened.items[1].cells, selected)
        self.assertEqual(reopened.items[1].height, 4)

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

    def test_truba_send_stages_frozen_settings_then_explicit_run_dispatches(self):
        for slots in (1, 2):
            with self.subTest(slots=slots):
                dialog = self.dialog()
                dialog.root = self.root
                dialog.base = replace(self.base, mesh_type='TET10', self_contact_mode='frictional')
                dialog.items = [self.prepared()]
                dialog.refresh_table()
                dialog.mesh_type.setCurrentText('TET10')
                dialog.table.item(0, RUN_COLUMN).setCheckState(Qt.CheckState.Checked)
                self.assertEqual(dialog.execution_target.currentData(), 'truba')
                self.assertEqual(dialog.solver.currentText(), 'Kratos')
                self.assertEqual(dialog.send.text(), 'Send to TRUBA')
                self.assertFalse(dialog.run.isEnabled())
                dialog.send.click()
                self.assertIn('TRUBA is disconnected', dialog.status.text())
                self.assertIsNone(dialog.future)

                upload = self.simulate_upload(dialog)
                context = RemoteContext(cores=2, parallel_jobs=slots, session_id='123')
                auth = dialog.remote_controller.auth()
                dialog.remote_controller.context = lambda: context
                dialog.remote_controller.require_analysis.return_value = (context, auth)
                with patch('truba_files.stage_batch_geometries', side_effect=OSError('Upload interrupted')):
                    dialog.send.click()
                    self.finish_preparation(dialog)
                self.assertIn('Upload interrupted', dialog.status.text())
                self.assertFalse(dialog.run.isEnabled())

                result = Future()
                with (patch('doe_studio.AnalysisProcess', return_value=result) as serial,
                      patch('truba_parallel.AnalysisProcess', return_value=result) as parallel):
                    dialog.send.click()
                    self.assertFalse(dialog.run.isEnabled())
                    self.finish_preparation(dialog)
                    serial.assert_not_called()
                    parallel.assert_not_called()
                    self.assertTrue(dialog.run.isEnabled())
                    self.assertIn('uploaded to TRUBA', dialog.status.text())
                    dialog.mesh_resolution.setValue(.9)
                    self.assertFalse(dialog.run.isEnabled())
                    dialog.send.click()
                    self.finish_preparation(dialog)
                    self.assertEqual(upload.call_count, 2, dialog.status.text())
                    self.assertTrue(dialog.run.isEnabled(), dialog.status.text())
                    values, = upload.call_args.kwargs['settings_by_geometry'][dialog.items[0].cached]
                    self.assertEqual(values['mesh_resolution'], .9)
                    self.assertEqual(values['local_mesh_size'], .8)
                    self.assertEqual(values['mesh_type'], 'TET10')
                    self.assertEqual(values['kratos_contact_method'], 'penalty')
                    self.assertEqual(values['self_contact_mode'], 'disabled')

                    dialog.remote_controller.require_analysis.side_effect = RuntimeError('TRUBA connection unavailable')
                    dialog.run.click()
                    self.assertIn('TRUBA connection unavailable', dialog.status.text())
                    self.assertTrue(dialog.run.isEnabled())
                    serial.assert_not_called()
                    parallel.assert_not_called()

                    dialog.remote_controller.require_analysis.side_effect = None
                    dialog.run.click()
                    dispatch = serial if slots == 1 else parallel
                    dispatch.assert_called_once()
                    target, index, settings, _, solve, destination, staged = dispatch.call_args.args
                    self.assertIs(target, run_remote_folder_doe)
                    self.assertEqual((index, settings.solver_backend, settings.mesh_resolution, solve),
                                     (0, 'kratos', .9, True))
                    self.assertEqual(staged, dict(username=auth.username,
                                                 sha256=sha256(Path(dialog.items[0].cached).read_bytes()).hexdigest(),
                                                 directory='project/batch'))
                    self.assertFalse(dialog.run.isEnabled())
                    result.set_result(dict(r_idx=0, status='Completed'))
                    dialog.runner._poll_worker()
                    dialog.poll()
                    self.assertIn('Completed 1/1', dialog.status.text())
                    self.assertTrue((Path(destination) / 'job.json').exists())
                    self.assertTrue(dialog.run.isEnabled())

    def test_batch_browser_restores_nested_folder_and_dispatches_to_selected_queue(self):
        dialog = self.dialog()
        upload = self.simulate_upload(dialog)
        dialog.remote_destination = None
        auth = dialog.truba_auth()
        folder = PurePosixPath('project/batch')
        directories = {
            PurePosixPath(): [('project', S_IFDIR)],
            folder.parent: [('batch', S_IFDIR)],
            folder: [('prepared.vtp', S_IFREG)],
        }

        def listing(_, path):
            return [SimpleNamespace(filename=name, st_mode=kind, st_size=1, st_mtime=None)
                    for name, kind in directories[path]]

        def finished_listing(window):
            browser = window.truba_browser
            browser.task[0].result(timeout=5)
            window.poll()
            self.assertTrue(browser.loaded)

        with patch('truba_files.list_batch_directory', side_effect=listing):
            dialog.show()
            finished_listing(dialog)
            browser = dialog.truba_browser
            for _ in range(2):
                browser.files.setCurrentItem(browser.files.topLevelItem(0))
                browser.open_folder.click()
                finished_listing(dialog)
            self.assertEqual(dialog.remote_destination, (auth.username, folder))
            self.assertEqual(browser.files.topLevelItem(0).text(0), 'prepared.vtp')
            dialog.files_splitter.setSizes([325, 550])
            sizes = dialog.files_splitter.sizes()
            dialog.close()
            dialog.dispose()

            reopened = self.dialog()
            reopened.remote_controller = dialog.remote_controller
            reopened.show()
            finished_listing(reopened)
            self.assertEqual(reopened.remote_destination, (auth.username, folder))
            self.assertEqual(reopened.files_splitter.sizes(), sizes)
            self.assertEqual(reopened.script.currentData(), 'auto')
            self.assertEqual(upload.call_count, 0)

            reopened.root, reopened.base = self.root, self.base
            reopened.items = [self.prepared()]
            reopened.refresh_table()
            reopened.table.item(0, RUN_COLUMN).setCheckState(Qt.CheckState.Checked)
            reopened.send.click()
            self.finish_preparation(reopened)
            finished_listing(reopened)
            self.assertEqual(upload.call_args.kwargs['relative'], folder)
            context = RemoteContext(partition='smp', cores=4, parallel_jobs=2)
            reopened.remote_controller.require_batch_queue = Mock(return_value=(context, auth))
            reopened.script.setCurrentIndex(reopened.script.findData('smp'))
            result = Future()
            with patch('truba_parallel.AnalysisProcess', return_value=result) as process:
                reopened.run.click()
                self.assertEqual(process.call_args.args[3], replace(context, parallel_jobs=1))
                self.assertEqual(process.call_args.args[6]['directory'], folder.as_posix())
                reopened.remote_controller.require.assert_not_called()
                self.assertEqual(reopened.remote_controller.require_batch_queue.call_args.args[0], 'smp')
                result.set_result(dict(r_idx=0, status='Completed'))
                reopened.runner._poll_worker()
                reopened.poll()
                self.assertIn('project/batch/results', reopened.status.text())
                finished_listing(reopened)

    def test_local_run_uses_current_mesh_fields_and_ignores_remote_mode(self):
        queue = self.queue()
        existing = queue.add_snapshot(self.base)
        dialog = self.dialog(queue)
        dialog.remote_controller.requested = True
        dialog.execution_target.setCurrentIndex(dialog.execution_target.findData('local'))
        dialog.root, dialog.base = self.root, self.base
        dialog.items = [self.prepared('part1.stl'), self.prepared('part2.stl')]
        dialog.refresh_table()
        dialog.solver.setCurrentIndex(1)
        dialog.mesh_resolution.setValue(.6)
        dialog.local_mesh_size.setValue(.2)
        dialog.mesh_type.setCurrentText('TET10')
        dialog.table.item(1, RUN_COLUMN).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(dialog.run.text(), 'Run')
        self.assertTrue(dialog.send.isHidden())
        result = Future()
        with patch('doe_studio.AnalysisProcess', return_value=result) as process:
            QTest.keyClick(dialog.local_mesh_size, Qt.Key.Key_Return)
            process.assert_not_called()
            dialog.run.click()
            self.finish_preparation(dialog)
            process.assert_called_once()
            target, _, settings = process.call_args.args
            self.assertEqual(target.__name__, '_solve_row')
            self.assertEqual(settings.imported_geometry_name, 'part2.stl')
            self.assertEqual((settings.mesh_resolution, settings.local_mesh_size, settings.mesh_type),
                             (.6, .2, 'TET10'))
            result.set_result(dict(r_idx=0, status='Completed'))
            dialog.runner._poll_worker()
            dialog.poll()
        self.assertIn('Completed', dialog.table.item(1, ANALYSIS_COLUMN).text())
        self.assertEqual(dialog.table.item(0, ANALYSIS_COLUMN).text(), '')
        self.assertEqual([row.exp_id for row in QueueStore(queue.store.path).load()], [existing.exp_id])

    def test_batch_action_buttons_stay_compact_and_below_the_specimens(self):
        dialog = self.dialog()
        dialog.show()
        for width in (640, 920):
            dialog.resize(width, 520)
            self.app.processEvents()
            self.assertEqual(dialog.send.size(), dialog.run.size())
            self.assertLess(dialog.run.width(), dialog.files_splitter.width() // 3)
            self.assertGreater(dialog.run.mapTo(dialog, dialog.run.rect().topLeft()).y(),
                               dialog.table.mapTo(dialog, dialog.table.rect().bottomRight()).y())

    def test_send_rejects_a_changed_source_before_staging(self):
        dialog = self.dialog()
        upload = self.simulate_upload(dialog)
        dialog.root, dialog.base = self.root, self.base
        dialog.items = [self.prepared('part1.stl'), self.prepared('part2.stl')]
        dialog.refresh_table()
        Path(dialog.items[1].source).write_bytes(b'changed')

        for index in range(2):
            dialog.table.item(index, RUN_COLUMN).setCheckState(Qt.CheckState.Checked)
        dialog.send.click()
        self.finish_preparation(dialog)

        self.assertIn('Source changed after preparation', dialog.status.text())
        upload.assert_not_called()
        self.assertFalse(dialog.run.isEnabled())
        self.assertTrue(dialog.send.isEnabled())

    def test_header_checkbox_tracks_ready_specimens(self):
        dialog = self.dialog()
        dialog.root, dialog.base = self.root, self.base
        failed = self.specimen('open.stl')
        pv.Plane().triangulate().save(failed)
        dialog.items = [self.prepared('part1.stl'), self.prepared('part2.stl'),
                        prepare_specimen(failed, (0, 0, 1))]
        dialog.refresh_table()
        dialog.update_actions()
        dialog.show()
        self.app.processEvents()
        header = dialog.table.horizontalHeader()
        checkbox = dialog.select_all
        self.assertEqual(dialog.table.horizontalHeaderItem(RUN_COLUMN).text(), '')
        self.assertTrue(checkbox.isVisible())
        self.assertTrue(header.viewport().rect().contains(checkbox.geometry()))
        row_checks = [dialog.table.item(row, RUN_COLUMN) for row in range(3)]
        row_checks[0].setCheckState(Qt.CheckState.Checked)
        self.assertEqual(checkbox.checkState(), Qt.CheckState.PartiallyChecked)
        # Click the indicator, then use the keyboard to clear the ready rows.
        QTest.mouseClick(checkbox, Qt.MouseButton.LeftButton, pos=checkbox.rect().center())
        self.assertEqual(checkbox.checkState(), Qt.CheckState.Checked)
        self.assertEqual([item.checkState() for item in row_checks],
                         [Qt.CheckState.Checked] * 2 + [Qt.CheckState.Unchecked])
        QTest.keyClick(checkbox, Qt.Key.Key_Space)
        self.assertEqual(checkbox.checkState(), Qt.CheckState.Unchecked)
        self.assertFalse(dialog.send.isEnabled())

    def test_async_check_and_enter_never_start_or_send(self):
        dialog = self.dialog()
        self.specimen()
        dialog.folder.setText(str(self.root))
        with patch('doe_studio.AnalysisProcess') as process:
            QTest.keyClick(dialog.folder, Qt.Key.Key_Return)
            self.assertFalse(dialog.items)
            dialog.check_folder()
            dialog.future.result(timeout=10)
            dialog.poll()
            self.assertIsNone(dialog.future)
            self.assertTrue(dialog.items[0].cached)
            self.assertEqual(dialog.table.item(0, RUN_COLUMN).checkState(), Qt.CheckState.Unchecked)
            self.assertFalse(dialog.send.isEnabled())
            self.assertIsNone(dialog.runner)
            dialog.table.item(0, RUN_COLUMN).setCheckState(Qt.CheckState.Checked)
            QTest.keyClick(dialog.folder, Qt.Key.Key_Return)
            self.assertTrue(dialog.send.isEnabled())
            self.assertIsNone(dialog.runner)
            process.assert_not_called()

    def test_hover_preview_is_saved_beside_prepared_geometry_and_replaced_after_edit(self):
        path = self.specimen('nested/sample.stl')
        batch = self.dialog()
        batch.folder.setText(str(self.root))
        batch.check_folder()
        batch.future.result(timeout=15)
        batch.poll()
        initial = batch.items[0]
        workspace = batch_directory(self.root)
        self.assertEqual(Path(initial.cached).parent, workspace / 'Prepared')
        self.assertEqual(Path(initial.preview).parent, workspace / 'Images', initial.preview_error)
        self.assertTrue((workspace / 'Results').is_dir())
        button = batch.table.cellWidget(0, FACES_COLUMN)
        self.assertEqual(button.preview_path, initial.preview)
        batch.show()
        self.app.processEvents()

        _, _, faces = automatic_compression_faces(read_surface(path), (1, 0, 0))

        def save(editor):
            editor.show()
            self.app.processEvents()
            for role, cell in enumerate(faces):
                editor.set_role(role)
                editor.select_cell(cell)
            editor.confirm.click()
            return editor.result()

        with patch.object(GeometryImportDialog, 'exec', save):
            button.click()
        revised = batch.items[0]
        self.assertNotEqual(revised.preview, initial.preview, revised.preview_error)
        self.assertEqual(button.preview_path, revised.preview)
        self.assertEqual(len(list((workspace / 'Prepared').glob('*.json'))), 1)
        position = button.rect().center()
        self.app.sendEvent(button, QHelpEvent(QEvent.Type.ToolTip, position, button.mapToGlobal(position)))
        self.assertTrue(batch.face_preview.isVisible())
        self.assertEqual(batch.face_preview.image.pixmap().toImage(), QPixmap(revised.preview).toImage())
        self.app.sendEvent(button, QEvent(QEvent.Type.Leave))
        self.assertFalse(batch.face_preview.isVisible())
        self.assertEqual(batch.saved_faces(path), (source_digest(path), faces))

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

    def test_local_run_spawns_worker_and_writes_batch_results(self):
        dialog = self.dialog()
        dialog.execution_target.setCurrentIndex(dialog.execution_target.findData('local'))
        dialog.root, dialog.base = self.root, self.base
        dialog.items = [self.prepared()]
        dialog.refresh_table()
        dialog.table.item(0, RUN_COLUMN).setCheckState(Qt.CheckState.Checked)
        dialog.run.click()
        self.finish_preparation(dialog)
        deadline = time.monotonic() + 25
        while dialog.running and time.monotonic() < deadline:
            QTest.qWait(20)
        if dialog.running:
            dialog.stop_batch()
            self.fail('Folder worker did not finish')
        self.assertIn('Completed 1/1', dialog.status.text())
        row, = dialog.runner.rows
        self.assertEqual(row.max_force_n, 42)
        self.assertTrue((Path(row.run_folder) / 'summary.json').is_file())
        self.assertTrue(Path(row.run_folder).is_relative_to(row.folder_job['destination']))
        with dialog.runner._dataset_path.open(encoding='utf-8-sig', newline='') as stream:
            summary, = csv.DictReader(stream)
        self.assertEqual(summary['job_id'], row.exp_id)
        self.assertEqual(summary['status'], 'Completed')

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
        geometry = first.frameGeometry()
        first.hide()
        second = self.dialog()
        second.show()
        self.app.processEvents()
        self.assertEqual(second.frameGeometry(), geometry)
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
