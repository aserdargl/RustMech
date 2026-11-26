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
    import shutil
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
        locked_report = None
        if kind == 'main':
            import nonlinear_compression as studio
            from window_caption import CAPTION_PROPERTY
            create_viewport = studio.create_preview_viewport
            def check_frame(owner, **kwargs):
                if not isinstance(owner, studio.RustMechDialog):
                    return create_viewport(owner, **kwargs)
                assert owner.property(CAPTION_PROPERTY) is not None
                viewport = create_viewport(owner, **kwargs)
                if app.platformName() == 'windows':
                    import ctypes
                    from window_caption import GWL_STYLE, WS_CAPTION
                    style = ctypes.windll.user32.GetWindowLongW(int(owner.winId()), GWL_STYLE)
                    assert not (style & WS_CAPTION), 'viewport created a native title'
                return viewport
            with patch.object(studio.RustMechDialog, 'start_preview', lambda self: None), patch.object(
                studio, 'create_preview_viewport', check_frame
            ):
                dialog = studio.RustMechDialog(Settings())
            dialog.doe_window.open_workspace()
            for plotter in (dialog.plotter, dialog.flow_workspace.plotter,
                            dialog.thermal_workspace.plotter):
                plotter.add_mesh(pv.Sphere())
                plotter.add_axes()
                plotter.render()
            dialog._kratos_result_dialog = results_dialog(folder, dialog)
            dialog._kratos_result_dialog.show()
            if action == 'caption_close_locked_preview':
                from mesh_preview_worker import _DEFAULT_WORKER
                _DEFAULT_WORKER.warmup()
                scratch = _DEFAULT_WORKER._scratch
                locked_report = (scratch / 'worker-crash.txt').open('ab')
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
                elif action == 'caption_close_locked_preview':
                    from window_caption import CaptionButton
                    caption = dialog.property(CAPTION_PROPERTY)
                    next(button for button in caption.findChildren(CaptionButton)
                         if button.action == 'close').click()
                else:
                    getattr(dialog, action)()
                assert dialog.plotter._closed, 'viewport was not released'
                assert not dialog.isVisible(), 'dialog did not close'
                assert store.settings.contains(store.key(dialog) + '/geometry'), 'window state was not saved'
            except Exception as error:
                failures.append(str(error))
                dialog.reject()
            finally:
                if locked_report is not None:
                    locked_report.close()
        QTimer.singleShot(350, finish)
        dialog.exec()
        if kind == 'main':
            dialog.dispose()  # Repeated cleanup must remain safe.
        if locked_report is not None:
            if scratch.exists():
                shutil.rmtree(scratch)
            assert _DEFAULT_WORKER._process is None
            assert _DEFAULT_WORKER._scratch is None
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

    def test_main_caption_closes_with_a_locked_preview_report(self):
        self.probe('main', 'caption_close_locked_preview')

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
