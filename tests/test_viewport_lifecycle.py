"""Exercise native Windows OpenGL teardown in isolated desktop processes."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROBE = textwrap.dedent("""
    import sys
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from unittest.mock import patch
    import pyvista as pv
    from shiboken6 import isValid
    from PySide6.QtCore import QCoreApplication, QEvent, QSettings, QTimer
    from PySide6.QtWidgets import QApplication
    from model import Settings
    from window_state import WindowStateStore

    def results_dialog(folder, parent=None):
        import json
        from types import SimpleNamespace
        from kratos_results_view import KratosResultsDialog
        mesh = pv.Sphere()
        mesh.cast_to_unstructured_grid().save(Path(folder) / 'submitted_mesh.vtu')
        (Path(folder) / 'summary.json').write_text(json.dumps({
            'solver_diagnostics': {'local_directory': folder}
        }))
        reader = SimpleNamespace(time_values=[0], nsets=1, has_self_contact=False)
        with patch('kratos_results_view.KratosResults', return_value=reader), patch.object(
            KratosResultsDialog, 'render_result', lambda *_: None
        ):
            dialog = KratosResultsDialog(folder, parent)
        dialog.plotter.add_mesh(mesh)
        return dialog

    app = QApplication([])
    with TemporaryDirectory() as folder:
        store = WindowStateStore(app, QSettings(str(Path(folder) / 'windows.ini'), QSettings.IniFormat))
        app._rustmech_window_state = store
        kind, action = sys.argv[1:]
        if kind == 'main':
            from nonlinear_compression import RustMechDialog
            from doe_studio import DoEStudioWidget
            with patch.object(RustMechDialog, 'start_preview', lambda self: None), patch.object(
                DoEStudioWidget, '_geometry_surface_builder', lambda self: None
            ):
                dialog = RustMechDialog(Settings())
            dialog.doe_window.open_workspace()
            panel = dialog.doe_studio.planner.geometry_panel
            panel._ensure_plotter()
            for plotter in (dialog.plotter, dialog.flow_workspace.plotter,
                            dialog.thermal_workspace.plotter, panel.plotter):
                plotter.add_mesh(pv.Sphere())
                plotter.add_axes()
                plotter.render()
            dialog._kratos_result_dialog = results_dialog(folder, dialog)
            dialog._kratos_result_dialog.show()
        elif kind == 'boundary':
            from doe_preview import DoEBoundaryPreview
            dialog = DoEBoundaryPreview('Density', {'Reference': Settings()}, lambda _: pv.Sphere(), None)
        elif kind == 'library':
            from doe_geometry import GeometryLibraryDialog
            dialog = GeometryLibraryDialog(Settings(), lambda _: pv.Sphere())
        elif kind == 'results':
            dialog = results_dialog(folder)
        else:
            from geometry_import_dialog import GeometryImportDialog
            dialog = GeometryImportDialog(pv.Box().triangulate(), 'test.stl')

        failures = []
        def finish():
            try:
                dialog.resize(980, 720)
                app.processEvents()
                dialog.plotter.render()
                dialog.showMaximized()
                app.processEvents()
                if kind == 'library':
                    dialog._ready_revision = dialog._revision
                if action == 'done':
                    dialog.done(1)
                elif action == 'hide_close':
                    dialog.hide()
                    dialog.close()
                else:
                    getattr(dialog, action)()
                assert dialog.plotter._closed, 'viewport was not released'
                assert not dialog.isVisible(), 'dialog did not close'
                assert store.settings.contains(store.key(dialog) + '/geometry'), 'window state was not saved'
            except Exception as error:
                failures.append(str(error))
                dialog.reject()
        QTimer.singleShot(350, finish)
        dialog.exec()
        if kind == 'main':
            dialog.dispose()  # Repeated cleanup must remain safe.
        if isValid(dialog):
            dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
        app.removeEventFilter(store)
        assert not failures, failures
    print('CLEAN_SHUTDOWN')
""")


@unittest.skipUnless(sys.platform == 'win32', 'requires native Windows WGL')
class ViewportLifecycleTests(unittest.TestCase):
    def probe(self, kind, action):
        env = dict(os.environ)
        env.pop('QT_QPA_PLATFORM', None)
        result = subprocess.run(
            [sys.executable, '-c', PROBE, kind, action], cwd=ROOT, env=env,
            capture_output=True, text=True, errors='replace', timeout=45,
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn('CLEAN_SHUTDOWN', output)
        self.assertNotIn('wglMakeCurrent', output)
        self.assertNotIn('ERR|', output)
        self.assertNotIn('Traceback', output)

    def test_main_dialog_close_reject_accept_and_done_release_all_viewports(self):
        for action in ('close', 'reject', 'accept', 'done', 'hide_close'):
            with self.subTest(action=action):
                self.probe('main', action)

    def test_secondary_dialogs_release_viewports_before_native_windows_hide(self):
        for kind in ('boundary', 'library', 'import'):
            for action in ('close', 'accept', 'hide_close'):
                with self.subTest(kind=kind, action=action):
                    self.probe(kind, action)

    def test_results_dialog_releases_viewport_before_automatic_deletion(self):
        for action in ('close', 'accept', 'hide_close'):
            with self.subTest(action=action):
                self.probe('results', action)


if __name__ == '__main__':
    unittest.main()
