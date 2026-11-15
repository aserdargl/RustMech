"""Capture the explicit opt-in panel without starting a connection or a job."""
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import MagicMock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QSettings,QTimer
from PySide6.QtWidgets import QApplication,QWidget
from model import ROOT,Settings
from nonlinear_compression import RustMechDialog
from solver_profiles import SolverProfileStore
from window_state import WindowStateStore


def main():
    app=QApplication.instance() or QApplication([])
    temporary=tempfile.TemporaryDirectory()
    preferences=QSettings(str(Path(temporary.name)/"ui.ini"),QSettings.Format.IniFormat)
    app._rustmech_window_state=WindowStateStore(app,preferences)
    with patch.object(RustMechDialog,"start_preview",lambda self:None),patch("nonlinear_compression.create_preview_viewport",side_effect=lambda parent,**kwargs:MagicMock(interactor=QWidget(parent))):
        window=RustMechDialog(Settings(),solver_profile_store=SolverProfileStore(preferences))
    window.start_preview=lambda:None
    panel=window.truba_dialog
    panel.resize(720,790)
    panel.show()
    output=ROOT/"design/truba-remote"
    output.mkdir(parents=True,exist_ok=True)
    status=1
    def inspect():
        nonlocal status
        app.processEvents()
        controller=window.remote_controller
        panel.grab().save(str(output/"mode-panel.png"))
        report=dict(passed=not controller.requested and panel.worker is None and controller.future is None,
                    connection_started=panel.worker is not None,remote_mode=controller.requested,
                    controls_scrollable=controller.scroll.verticalScrollBar().maximum()>0,
                    visible_on_screen=app.primaryScreen().availableGeometry().intersects(panel.frameGeometry()))
        (output/"verification.json").write_text(json.dumps(report,indent=2))
        print(json.dumps(report,indent=2))
        status=0 if report["passed"] else 1
        panel.close()
        window.close()
        window.dispose()
        app.exit(status)
    QTimer.singleShot(350,inspect)
    app.exec()
    temporary.cleanup()
    return status


if __name__=="__main__": raise SystemExit(main())
