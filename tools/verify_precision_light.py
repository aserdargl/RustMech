"""Exercise the native workspace and capture it without launching a solver."""
from __future__ import annotations

import json
import argparse
import multiprocessing
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from PyQt6.QtCore import QPoint, QSettings, QTimer, Qt
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QToolButton
    from mesh_convergence import MeshConvergenceDialog
    from nonlinear_compression import RustMechDialog
    from model import Settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="design/precision-light")
    output = ROOT / parser.parse_args().output
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    from window_state import WindowStateStore
    # Verification must not overwrite the user's saved desktop preferences.
    app._rustmech_window_state = WindowStateStore(
        app, QSettings(str(output / "window-preferences.ini"), QSettings.Format.IniFormat)
    )
    settings = Settings(cell_size=3, mesh_type="TET10", mesh_resolution=0.7,
                        local_mesh_size=0.35, tetra_strategy="netgen")
    dialog = RustMechDialog(settings)
    dialog.resize(1440, 1024)
    dialog.show()
    started = time.monotonic()
    result = {"passed": False, "solver_launched": False, "checks": []}
    exit_code = 1

    def check(name, condition):
        result["checks"].append({"name": name, "passed": bool(condition)})
        if not condition:
            raise AssertionError(name)

    def capture(name):
        QTest.qWait(100)
        app.processEvents()
        dialog.plotter.render()
        app.processEvents()
        path = output / name
        # Native VTK OpenGL windows are not included in QWidget.grab(). Capture
        # their actual framebuffer and place it at the widget's screen position.
        pixmap = dialog.grab()
        if dialog.plotter.interactor.isVisible():
            framebuffer = dialog.plotter.screenshot(return_img=True).copy(order="C")
            image = QImage(framebuffer.data, framebuffer.shape[1], framebuffer.shape[0],
                           framebuffer.strides[0], QImage.Format.Format_RGB888).copy()
            painter = QPainter(pixmap)
            position = dialog.plotter.interactor.mapTo(dialog, QPoint(0, 0))
            painter.drawImage(position, image)
            painter.end()
        if not pixmap.save(str(path)):
            raise RuntimeError(f"Could not save {path}")

    def inspect():
        nonlocal exit_code
        if dialog.preview is None or dialog.preview.mesh is None:
            if dialog.preview is not None and not dialog._mesh_requested:
                dialog.mode.setCurrentIndex(1)
            if dialog.failed_key is None and time.monotonic() - started < 180:
                return
            result["error"] = dialog.status.text()
            capture("preview-error.png")
        else:
            timer.stop()
            try:
                check("geometry and analysis tabs", dialog.setup_tabs.count() == 2)
                navigation = dialog.workspace_navigation
                dialog.doe_button.click()
                check("DoE button opens experiment settings", dialog.doe_window.isVisible())
                dialog.doe_window.close()
                QTest.mouseClick(navigation, Qt.MouseButton.LeftButton,
                                 pos=navigation.tabRect(0).center())
                check("workspace navigation opens model", dialog.workspace_tabs.currentIndex() == 0)
                check("workspace navigation stays synchronized", navigation.currentIndex() == 0)
                check("display modes immediately follow workspaces", 0 <=
                      dialog.display_mode_tabs.mapTo(dialog, QPoint()).x() -
                      dialog.doe_button.mapTo(dialog, dialog.doe_button.rect().topRight()).x() <= 15)
                check("platens grouped with View at right", dialog.show_platens_cb.mapTo(dialog, QPoint()).x()
                      > dialog.display_mode_tabs.mapTo(dialog, dialog.display_mode_tabs.rect().topRight()).x())
                check("preview aligned with settings", abs(
                    dialog.plotter.interactor.mapTo(dialog, QPoint()).y() -
                    dialog.setup_tabs.mapTo(dialog, QPoint()).y()) <= 3)
                spin = dialog.controls["size"]
                original_size = spin.value()
                QTest.mouseClick(spin, Qt.MouseButton.LeftButton,
                                 pos=QPoint(spin.width() - 6, 5))
                check("dimension input has no stepper", spin.value() == original_size
                      and spin.buttonSymbols() == spin.ButtonSymbols.NoButtons)
                for key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
                    QTest.keyClick(spin, key)
                check("dimension input ignores stepping keys", spin.value() == original_size)
                spin.setValue(original_size)
                cores = dialog.controls["cores"]
                original_cores = cores.value()
                QTest.mouseClick(cores.btn_down, Qt.MouseButton.LeftButton)
                check("integer stepper arrow decrements", cores.value() == original_cores - cores.singleStep())
                cores.setValue(original_cores)
                check("centered Datenburg numeric values", spin.alignment()
                      == Qt.AlignmentFlag.AlignCenter
                      and spin.width() == 88)
                check("geometry settings round trip", dialog.collect().mesh_type == "TET10"
                      and dialog.collect().local_mesh_size == 0.35
                      and dialog.collect().cell_size == 3)
                original = dialog.preview
                for index in (0, 2, 1):
                    QTest.mouseClick(dialog.display_mode_tabs, Qt.MouseButton.LeftButton,
                                     pos=dialog.display_mode_tabs.tabRect(index).center())
                    check(f"display mode {index}", dialog.mode.currentIndex() == index)
                check("view modes reuse mesh", dialog.preview is original)
                dialog.show_platens_cb.click()
                check("hide platens", "top_platen" not in dialog.plotter.renderer.actors)
                dialog.show_platens_cb.click()
                check("show platens", "top_platen" in dialog.plotter.renderer.actors)
                dialog.perspective_button.setChecked(True)
                check("perspective on", not dialog.plotter.camera.parallel_projection)
                dialog.perspective_button.setChecked(False)
                for preset in ("xy", "-xz", "yz", "iso"):
                    dialog._set_camera_preset(preset)
                dialog.reset_view()
                app.processEvents()
                geometry_scroll = dialog.setup_tabs.widget(0)
                check("material editor below mesh", dialog.material_editor.parentWidget()
                      is dialog.mesh_type_combo.parentWidget().parentWidget()
                      and dialog.material_editor.y() > dialog.mesh_type_combo.parentWidget().geometry().bottom())
                geometry_scroll.ensureWidgetVisible(dialog.material_editor)
                check("material editor in geometry", dialog.material_editor.isVisible())
                capture("material.png")
                dialog.setup_tabs.setCurrentIndex(1)
                capture("analysis.png")
                check("analysis settings accessible", dialog.solver_combo.isVisible())
                check("solver dropdown height", dialog.solver_combo.height() == 25)
                check("primary button height", dialog.apply_button.height() == 25)
                dialog.setup_tabs.setCurrentIndex(0)
                check("material settings retained", dialog.collect().young_modulus == settings.young_modulus)
                geometry_scroll.verticalScrollBar().setValue(0)
                dialog.family_combo.showPopup()
                QTest.qWait(100)
                popup = dialog.family_combo.view().window()
                popup.grab().save(str(output / "dropdown.png"))
                dialog.family_combo.hidePopup()
                methods = dialog.findChild(QToolButton, "headerMenu")
                methods.menu().popup(methods.mapToGlobal(QPoint(0, methods.height())))
                QTest.qWait(100)
                methods.menu().grab().save(str(output / "methods-menu.png"))
                methods.menu().hide()
                convergence = MeshConvergenceDialog(settings, lambda _: None, lambda *_: None, dialog)
                convergence.show()
                QTest.qWait(100)
                check("convergence controls match height", convergence.study.start_size.height() == 25
                      and convergence.study.run_button.height() == 25)
                check("convergence remains minimal", convergence.study.advanced.isHidden())
                convergence.grab().save(str(output / "convergence.png"))
                convergence.study.toggle_advanced()
                QTest.qWait(100)
                check("convergence advanced stepper height", convergence.study.max_levels.height() == 25)
                convergence.grab().save(str(output / "convergence-advanced.png"))
                convergence.study.dispose()
                convergence.close()
                dialog.truba_dialog.show()
                QTest.qWait(100)
                check("TRUBA has not connected", dialog.truba_dialog.process is None)
                check("TRUBA button height", dialog.truba_dialog.connect_button.height() == 25)
                dialog.truba_dialog.grab().save(str(output / "truba.png"))
                dialog.truba_dialog.hide()
                designs_before = len(dialog.doe_studio._designs)
                dialog.doe_button.click()
                dialog.doe_studio.use_model_btn.click()
                check("design added to DoE", len(dialog.doe_studio._designs) == designs_before + 1)
                dialog.doe_window.grab().save(str(output / "experiments.png"))
                dialog.doe_window.close()
                dialog.workspace_tabs.setCurrentIndex(0)
                dialog.setup_tabs.setCurrentIndex(0)
                capture("main-1440.png")
                dialog.setup_tabs.parentWidget().grab().save(str(output / "controls.png"))
                for width, height in ((1280, 850), (1100, 760), (1000, 700)):
                    dialog.resize(width, height)
                    QTest.qWait(100)
                    # Settle native Windows geometry events after changing tabs.
                    dialog.resize(width, height)
                    QTest.qWait(100)
                    check(f"window fits {width}", dialog.width() <= width)
                    check(f"window fits height {height}", dialog.height() <= height)
                    check(f"primary action visible at {width}", dialog.apply_button.isVisible()
                          and dialog.apply_button.mapTo(dialog, dialog.apply_button.rect().bottomRight()).x()
                          < dialog.width())
                    capture(f"main-{width}.png")
                dialog.resize(1440, 1024)
                dialog.reset_view()
                capture("main-1440.png")
                check("mesh metrics populated", dialog._kpi_labels["elements"].text() != "—")
                result["window_size"] = [dialog.width(), dialog.height()]
                result["elements"] = dialog.preview.mesh.grid.n_cells
                result["nodes"] = dialog.preview.mesh.grid.n_points
                result["density"] = {
                    "control_height": dialog.controls["size"].height(),
                    "viewport_top": dialog.plotter.interactor.mapTo(dialog, QPoint(0, 0)).y(),
                    "setup_top": dialog.setup_tabs.mapTo(dialog, QPoint(0, 0)).y(),
                }
                result["passed"] = True
                exit_code = 0
            except Exception as exc:
                import traceback
                result["error"] = str(exc)
                result["traceback"] = traceback.format_exc()
        timer.stop()
        (output / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2), flush=True)
        dialog.reject()
        app.quit()

    timer = QTimer()
    timer.timeout.connect(inspect)
    timer.start(300)
    try:
        app.exec()
    finally:
        dialog.dispose()
        dialog.close()
    return exit_code


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
