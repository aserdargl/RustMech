"""Real transfer packages, immutable jobs, queue execution and window state."""
from concurrent.futures import Future
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pyvista as pv
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QWidget

from analysis_queue import AnalysisQueueWidget, AnalysisQueueWindow, QueueStore
from design_exchange import list_designs, publish_design, read_design
from design_pool import DesignPoolDialog, load_pool_surface
from imported_geometry import align_compression_surface
from model import Settings
from window_state import WindowStateStore


class DesignPoolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.windows = []
        self.env = patch.dict(os.environ, {"RUSTMECH_DESIGN_POOL": str(self.root / "pool")})
        self.env.start()
        self.addCleanup(self.env.stop)
        previous = getattr(self.app, "_rustmech_window_state", None)
        if previous is not None:
            self.app.removeEventFilter(previous)
        store = WindowStateStore(self.app, QSettings(str(self.root / "isolated-windows.ini"), QSettings.Format.IniFormat))
        self.app._rustmech_window_state = store
        def restore_preferences():
            self.app.removeEventFilter(store)
            if previous is not None:
                self.app._rustmech_window_state = previous
                self.app.installEventFilter(previous)
            else:
                del self.app._rustmech_window_state
        self.addCleanup(restore_preferences)
        self.mesh = pv.Box(bounds=(0, 4, 0, 3, 0, 6)).triangulate().clean()

    def send(self, name="Gitter specimen"):
        return publish_design(name, lambda path: self.mesh.save(path), {"source_mode": "Normal"})

    def test_flow_preparation_emits_general_geometry_without_mechanical_picker(self):
        data = self.send()
        dialog = DesignPoolDialog()
        self.windows.append(dialog)
        spy = QSignalSpy(dialog.flow_geometry_prepared)
        future = Future()
        future.set_result(load_pool_surface(data["id"], False))
        dialog.preparation_mode = "flow"
        dialog.future = future
        with patch("geometry_import_dialog.GeometryImportDialog", side_effect=AssertionError("mechanical picker")), patch("design_pool.store_surface", return_value="general.vtp"):
            dialog._poll()
        self.assertEqual(spy.count(), 1)
        self.assertEqual(spy.at(0), ["general.vtp",6.0,data["name"]])
        self.assertFalse(dialog.pending)

    def settings(self):
        data = self.send()
        _, surface = load_pool_surface(data["id"])
        centers = surface.cell_centers().points
        import numpy as np
        bottom = int(np.flatnonzero(np.isclose(centers[:, 2], 0))[0])
        top = int(np.flatnonzero(np.isclose(centers[:, 2], 6))[0])
        aligned, height = align_compression_surface(surface, bottom, top)
        path = self.root / "aligned.vtp"
        aligned.save(path)
        return replace(Settings(), imported_geometry_path=str(path), imported_geometry_name=data["name"],
                       size=height, mesh_type="TET4", tetra_strategy="netgen",
                       mesh_resolution=1, local_mesh_size=.5, compression=.6)

    def widget(self, provider=Settings):
        widget = AnalysisQueueWidget(provider, lambda _: None, lambda *_: {},
                                     queue_path=self.root / "queue.json")
        self.addCleanup(widget.dispose)
        self.addCleanup(widget.close)
        return widget

    def test_atomic_publication_and_independent_geometry(self):
        def interrupted(path):
            path.write_bytes(b"partial")
            self.assertEqual(list_designs()[0], [])
            raise RuntimeError("interrupted")
        with self.assertRaises(RuntimeError):
            publish_design("bad", interrupted)
        self.assertEqual(list_designs()[0], [])
        data = self.send()
        self.mesh.points *= 2
        metadata, surface = load_pool_surface(data["id"])
        self.assertEqual(metadata["name"], "Gitter specimen")
        self.assertAlmostEqual(surface.volume, 72)
        self.assertEqual(len(list_designs()[0]), 1)

    def test_integrity_units_and_invalid_geometry(self):
        data = self.send()
        _, path = read_design(data["id"])
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            load_pool_surface(data["id"])
        self.mesh = pv.Plane().triangulate()
        opened = self.send("open surface")
        with self.assertRaisesRegex(ValueError, "closed"):
            load_pool_surface(opened["id"])
        manifest = path.parent / "manifest.json"
        raw = json.loads(manifest.read_text())
        raw["units"] = "m"
        manifest.write_text(json.dumps(raw))
        self.assertTrue(list_designs()[1])

    def test_queue_snapshots_restart_reorder_and_never_start_on_add(self):
        original = self.settings()
        widget = self.widget(lambda: original)
        with patch("doe_studio.AnalysisProcess") as process:
            first = widget.add_snapshot(original)
            second = widget.add_snapshot(replace(original, material_name="Second material", compression=.3))
            process.assert_not_called()
        widget.table.selectRow(1)
        widget.move_selected(-1)
        self.assertEqual(widget.rows[0].exp_id, second.exp_id)
        original = replace(original, material_name="Changed later", compression=.2)
        restored = QueueStore(self.root / "queue.json").load()
        self.assertEqual(restored[0].design_settings.material_name, "Second material")
        self.assertEqual(restored[1].compression, .6)
        self.assertEqual(restored[1].design_settings.imported_geometry_path, first.design_settings.imported_geometry_path)
        widget.table.selectRow(1)
        widget.delete_selected_rows()
        self.assertEqual(len(QueueStore(self.root / "queue.json").load()), 1)

    def test_only_explicit_run_executes_in_order_and_restores_results(self):
        settings = self.settings()
        widget = self.widget()
        first = widget.add_snapshot(settings)
        second = widget.add_snapshot(replace(settings, compression=.3))
        captured = []
        def process(target, index, snapshot, **kwargs):
            captured.append((index, snapshot.compression))
            result = Future()
            result.set_result(dict(r_idx=index, status="Completed", max_force_n=123))
            return result
        with patch("doe_studio.AnalysisProcess", side_effect=process):
            widget.start_batch_solve()
            widget._poll_worker()
            widget._poll_worker()
        self.assertEqual(captured, [(0, first.compression), (1, second.compression)])
        restored = QueueStore(self.root / "queue.json").load()
        self.assertTrue(all(row.status == "Completed" for row in restored))
        self.assertTrue(all(row.max_force_n == 123 for row in restored))

    def test_interrupted_jobs_are_unchecked_and_concurrent_edits_rejected(self):
        widget = self.widget()
        row = widget.add_snapshot(self.settings())
        row.status = "Solving (ANSYS)..."
        widget.save_queue()
        other = QueueStore(self.root / "queue.json")
        restored = other.load()
        self.assertFalse(restored[0].enabled)
        self.assertIn("Interrupted", restored[0].status)
        other.save(restored)
        with self.assertRaisesRegex(OSError, "another"):
            widget.store.save(widget.rows)

    def test_failed_save_does_not_add_job_or_destroy_invalid_file(self):
        widget = self.widget()
        with patch.object(widget.store, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                widget.add_snapshot(self.settings())
        self.assertFalse(widget.rows)
        widget._storage_ready = False
        (self.root / "queue.json").write_text("broken")
        another = self.widget()
        self.assertTrue(another.storage_error)
        self.assertEqual((self.root / "queue.json").read_text(), "broken")

    def test_pool_refresh_and_enter_do_not_prepare_or_enqueue(self):
        pool = DesignPoolDialog()
        self.addCleanup(pool.dispose)
        self.addCleanup(pool.close)
        pool.show()
        self.send()
        pool.refresh()
        pool.table.selectRow(0)
        spy = QSignalSpy(pool.geometry_prepared)
        QTest.keyClick(pool.table, Qt.Key.Key_Return)
        self.assertIsNone(pool.future)
        self.assertEqual(spy.count(), 0)
        self.assertEqual(pool.table.rowCount(), 1)
        self.assertTrue(pool.prepare.isEnabled())

    def test_closing_pool_discards_a_pending_preparation(self):
        pool = DesignPoolDialog()
        self.addCleanup(pool.dispose)
        pool.show()
        result = Future()
        pool.future = result
        pool.pending = ['next']
        pool.close()
        self.assertIsNone(pool.future)
        self.assertEqual(pool.pending, [])
        self.assertTrue(result.cancelled())

    def test_pool_and_queue_geometry_restore(self):
        previous = getattr(self.app, "_rustmech_window_state", None)
        if previous:
            self.app.removeEventFilter(previous)
        store = WindowStateStore(self.app, QSettings(str(self.root / "windows.ini"), QSettings.Format.IniFormat))
        self.app._rustmech_window_state = store
        try:
            for factory in (DesignPoolDialog, lambda: AnalysisQueueWindow(self.widget())):
                first = factory()
                self.windows.append(first)
                first.show()
                first.resize(980, 650)
                first.move(60, 70)
                self.app.processEvents()
                geometry = first.frameGeometry()
                first.close()
                if isinstance(first, DesignPoolDialog):
                    first.dispose()
                second = factory()
                self.windows.append(second)
                (second.open_pool if isinstance(second, DesignPoolDialog) else second.open_workspace)()
                self.app.processEvents()
                self.assertEqual(second.frameGeometry(), geometry)
                normal = second.size()
                second.showMaximized()
                self.app.processEvents()
                second.close()
                (second.open_pool if isinstance(second, DesignPoolDialog) else second.open_workspace)()
                self.app.processEvents()
                self.assertTrue(second.isMaximized())
                second.showNormal()
                self.app.processEvents()
                self.assertEqual(second.size(), normal)
                second.close()
                if isinstance(second, DesignPoolDialog):
                    second.dispose()
        finally:
            self.app.removeEventFilter(store)
            if previous:
                self.app._rustmech_window_state = previous
                self.app.installEventFilter(previous)
            else:
                del self.app._rustmech_window_state

    def test_main_workspace_prepares_and_enqueues_configured_material(self):
        from nonlinear_compression import RustMechDialog
        from solver_profiles import SolverProfileStore
        settings = self.settings()
        with patch.dict(os.environ, {"RUSTMECH_ANALYSIS_QUEUE": str(self.root / "main-queue.json")}), \
             patch.object(RustMechDialog, "start_preview", lambda _: None), \
             patch("nonlinear_compression.create_preview_viewport",
                   side_effect=lambda parent, **kwargs: MagicMock(interactor=QWidget(parent))):
            preferences = QSettings(str(self.root / "main.ini"), QSettings.Format.IniFormat)
            dialog = RustMechDialog(Settings(), solver_profile_store=SolverProfileStore(preferences))
            try:
                dialog.show()
                dialog._show_design_pool()
                self.assertIsNone(dialog.analysis_queue_widget)
                self.assertIsNone(dialog.analysis_job)
                dialog._load_pool_geometry(settings.imported_geometry_path, settings.size, "From Gitterburg")
                dialog.material_editor.controls['young_modulus'].setValue(180000)
                dialog.controls['mesh_resolution'].setValue(1)
                dialog.controls['local_mesh_size'].setValue(.5)
                dialog.controls['compression'].setValue(.3)
                with patch("doe_studio.AnalysisProcess") as process:
                    dialog._enqueue_current_model()
                    process.assert_not_called()
                self.assertIsNotNone(dialog.analysis_queue_widget)
                self.assertEqual(len(dialog.analysis_queue_widget.rows), 1)
                row = dialog.analysis_queue_widget.rows[0]
                self.assertEqual(row.design_settings.imported_geometry_name, "From Gitterburg")
                self.assertEqual(row.design_settings.young_modulus, 180000)
                self.assertEqual(row.compression, .3)
                dialog.material_editor.controls['young_modulus'].setValue(200000)
                self.assertEqual(row.design_settings.young_modulus, 180000)
            finally:
                dialog.close()
                dialog.dispose()


if __name__ == "__main__":
    unittest.main()
