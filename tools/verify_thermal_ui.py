"""Run a real transient CHT job through the GUI and verify physical-time playback."""
import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QPoint,QSettings,QTimer
from PySide6.QtGui import QImage,QPainter
from PySide6.QtWidgets import QApplication
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
    dialog.workspace_navigation.setCurrentIndex(3)
    workspace = dialog.thermal_workspace
    for key,value in dict(cells_across=12,inlet_length_mm=1,outlet_length_mm=1).items(): workspace.controls[key].setValue(value)
    for key,value in dict(duration_s=.3,time_step_s=.01,output_interval_s=.03).items(): workspace.thermal_controls[key].setValue(value)
    output = ROOT/"design"/"transient-thermal"
    output.mkdir(parents=True,exist_ok=True)
    report = dict(passed=False)
    start,code = time.monotonic(),1

    def inspect():
        nonlocal code
        if workspace.job is not None:
            if time.monotonic()-start<150: return
            workspace.cancel()
            report["error"] = "Timed out"
        elif not workspace.thermal_report: report["error"] = workspace.status.text()
        else:
            try:
                report["case"] = str(workspace.display_directory)
                report["status"] = workspace.thermal_report["status"]
                for index in range(workspace.view.count()):
                    workspace.view.setCurrentIndex(index)
                    app.processEvents()
                    if "Cannot render" in workspace.status.text(): raise RuntimeError(workspace.status.text())
                workspace.time_slider.setValue(0)
                initial = float(workspace.solid_grid.cell_data["Temperature_C"].mean())
                workspace.time_slider.setValue(workspace.time_slider.maximum())
                final = float(workspace.solid_grid.cell_data["Temperature_C"].mean())
                report["solid_initial_C"],report["solid_final_C"] = initial,final
                workspace.view.setCurrentIndex(2)
                workspace.plotter.view_isometric()
                workspace.plotter.reset_camera()
                app.processEvents()
                pixmap = dialog.grab()
                framebuffer = workspace.plotter.screenshot(return_img=True).copy(order="C")
                image = QImage(framebuffer.data,framebuffer.shape[1],framebuffer.shape[0],framebuffer.strides[0],QImage.Format.Format_RGB888).copy()
                painter = QPainter(pixmap)
                painter.drawImage(workspace.plotter.interactor.mapTo(dialog,QPoint(0,0)),image)
                painter.end()
                pixmap.save(str(output/"temperatures.png"))
                report["passed"] = final>initial and report["status"]=="completed"
                code = 0 if report["passed"] else 1
            except Exception as error: report["error"] = str(error)
        timer.stop()
        (output/"verification.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        print(json.dumps(report,indent=2))
        dialog.close()
        dialog.dispose()
        app.exit(code)

    timer = QTimer()
    timer.setInterval(250)
    timer.timeout.connect(inspect)
    workspace.buttons[1].click()
    timer.start()
    app.exec()
    temp.cleanup()
    return code


if __name__=="__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
