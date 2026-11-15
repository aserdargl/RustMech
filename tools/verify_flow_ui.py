"""Exercise the real UI/process/solver path and save a reviewable screenshot."""
from pathlib import Path
import json
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QPoint, QSettings, QTimer
from PySide6.QtGui import QImage,QPainter
from PySide6.QtWidgets import QApplication
from unittest.mock import patch
from model import ROOT,Settings
from nonlinear_compression import RustMechDialog
from solver_profiles import SolverProfileStore
from window_state import WindowStateStore


def main():
    app = QApplication.instance() or QApplication([])
    temp = tempfile.TemporaryDirectory()
    preferences = QSettings(str(Path(temp.name)/"settings.ini"),QSettings.Format.IniFormat)
    app._rustmech_window_state = WindowStateStore(app,preferences)
    with patch.object(RustMechDialog,"start_preview",lambda self:None):
        dialog = RustMechDialog(Settings(),solver_profile_store=SolverProfileStore(preferences))
    dialog.show()
    dialog.workspace_navigation.setCurrentIndex(2)
    flow = dialog.flow_workspace
    flow.controls["cells_across"].setValue(12)
    flow.controls["iterations"].setValue(500)
    flow.empty.setChecked(True)
    output = ROOT/"design"/"cylindrical-flow"
    output.mkdir(parents=True,exist_ok=True)
    start = time.monotonic()
    report = {"passed":False}
    code = 1

    def inspect():
        nonlocal code
        if flow.job is not None:
            if time.monotonic()-start < 120: return
            flow.cancel()
            report["error"] = "Timed out"
        elif flow.grid is None:
            report["error"] = flow.status.text()
        else:
            try:
                report["case"] = str(flow.display_directory)
                report["summary"] = json.loads((flow.display_directory/"flow_summary.json").read_text())
                # Exercise every visualization, including streamline integration.
                for view in range(flow.view.count()):
                    flow.view.setCurrentIndex(view)
                    app.processEvents()
                    if "Cannot render" in flow.status.text(): raise RuntimeError(flow.status.text())
                # Also inspect a TPMS case when available from pipeline verification.
                candidates = sorted((ROOT/"build").glob("flow-validation-*/tpms24/flow_result.vtu"),key=lambda p:p.stat().st_mtime)
                if candidates: flow.load_directory(candidates[-1].parent)
                flow.view.setCurrentIndex(2)
                flow.plotter.view_yz()
                flow.plotter.reset_camera()
                app.processEvents()
                pixmap = dialog.grab()
                framebuffer = flow.plotter.screenshot(return_img=True).copy(order="C")
                image = QImage(framebuffer.data,framebuffer.shape[1],framebuffer.shape[0],framebuffer.strides[0],QImage.Format.Format_RGB888).copy()
                painter = QPainter(pixmap)
                painter.drawImage(flow.plotter.interactor.mapTo(dialog,QPoint(0,0)),image)
                painter.end()
                pixmap.save(str(output/"pressure.png"))
                report["passed"] = report["summary"]["status"]=="converged"
                code = 0 if report["passed"] else 1
            except Exception as error:
                report["error"] = str(error)
        timer.stop()
        (output/"verification.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        print(json.dumps({key:value for key,value in report.items() if key!="summary"},indent=2))
        dialog.close()
        dialog.dispose()
        app.exit(code)

    timer = QTimer()
    timer.setInterval(200)
    timer.timeout.connect(inspect)
    flow.buttons[1].click()
    timer.start()
    app.exec()
    temp.cleanup()
    return code


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
