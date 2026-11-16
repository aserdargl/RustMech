"""Check custom camera/widget cleanup with a real Windows OpenGL context."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


PROBE = textwrap.dedent("""
    import sys
    import pyvista as pv
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout
    from preview_viewport import create_preview_viewport, align_camera

    app = QApplication([])
    owner = QDialog()
    layout = QVBoxLayout(owner)
    plotter = create_preview_viewport(owner, simplify_mesh_during_drag=True)
    layout.addWidget(plotter)
    plotter.add_mesh(pv.Sphere())
    plotter.add_axes()
    owner.show()
    app.processEvents()
    plotter.render()
    align_camera(plotter, (1, 0, 0))
    assert plotter._camera_transition.timer.isActive()
    if sys.argv[1] in ('delete_owner', 'delete_plotter'):
        target = owner if sys.argv[1] == 'delete_owner' else plotter
        target.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    else:
        plotter.close()
        assert not plotter._camera_transition.timer.isActive(), 'camera still running'
        plotter.close()  # Cleanup must be idempotent.
        owner.close()
    assert plotter._closed
    assert not plotter.orientation_cube.GetEnabled()
    assert plotter.orientation_cube.GetInteractor() is None, 'cube still attached'
    assert plotter.orientation_cube.GetCurrentRenderer() is None
    QTest.qWait(550)  # Deliver any camera timer or render events left behind.
    print('CLEAN_SHUTDOWN')
""")


@unittest.skipUnless(sys.platform == 'win32', 'requires native Windows WGL')
class PreviewTeardownTests(unittest.TestCase):
    def test_close_and_owner_deletion_during_camera_animation(self):
        for action in ('close', 'delete_owner', 'delete_plotter'):
            with self.subTest(action=action):
                env = dict(os.environ)
                env.pop('QT_QPA_PLATFORM', None)
                result = subprocess.run(
                    [sys.executable, '-c', PROBE, action],
                    cwd=Path(__file__).resolve().parents[1], env=env,
                    capture_output=True, text=True, errors='replace', timeout=30,
                )
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, output)
                self.assertIn('CLEAN_SHUTDOWN', output)
                for error in ('wglMakeCurrent', 'ERR|', 'Traceback'):
                    self.assertNotIn(error, output)
