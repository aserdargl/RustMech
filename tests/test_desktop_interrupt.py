"""Exercise Ctrl+C across the Windows desktop process and its preview worker."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


PROBE = textwrap.dedent("""
    import ctypes
    from pathlib import Path
    import sys
    from threading import Event
    from types import SimpleNamespace

    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtWidgets import QApplication, QDialog

    import desktop_entry
    from app_identity import configure_application
    from mesh_preview_worker import _DEFAULT_WORKER, run_mesh_preview
    from model import Settings

    folder = Path(sys.argv.pop())
    desktop_entry.__file__ = str(folder / 'desktop_entry.py')

    def main():
        app = QApplication.instance()
        settings = QSettings(str(folder / 'windows.ini'), QSettings.Format.IniFormat)
        configure_application(app, settings=settings)
        run_mesh_preview(Settings(size=1, cell_size=1), None, None, Event())
        (folder / 'scratch.txt').write_text(_DEFAULT_WORKER._scratch.name, encoding='utf-8')

        dialog = QDialog()
        dialog.setObjectName('InterruptProbe')
        timer = QTimer(dialog)
        timer.setSingleShot(True)

        def interrupt():
            if not ctypes.windll.kernel32.GenerateConsoleCtrlEvent(0, 0):
                raise RuntimeError('Could not send Ctrl+C to the test console')

        timer.timeout.connect(interrupt)
        timer.start(50)
        dialog.exec()
        assert not dialog.isVisible()
        assert _DEFAULT_WORKER._process.is_alive(), 'Ctrl+C killed the preview worker'
        store = app._rustmech_window_state
        assert settings.contains(store.key(dialog) + '/geometry')
        return 0

    sys.modules['nonlinear_compression'] = SimpleNamespace(QApplication=QApplication, main=main)
    sys.exit(desktop_entry.run())
""")


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows console Ctrl+C")
def test_ctrl_c_closes_qt_window_and_reaps_idle_preview_worker(tmp_path):
    env = dict(os.environ)
    env.pop("QT_QPA_PLATFORM", None)
    result = subprocess.run(
        [sys.executable, "-c", PROBE, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        capture_output=True,
        text=True,
        timeout=45,
    )
    log = tmp_path / "RustMech.log"
    output = result.stdout + result.stderr + (log.read_text(encoding="utf-8") if log.exists() else "")
    assert result.returncode == 130, output
    for error in ("KeyboardInterrupt", "nativeEventFilter", "Traceback"):
        assert error not in output, output
    scratch = Path((tmp_path / "scratch.txt").read_text(encoding="utf-8"))
    assert not scratch.exists()
