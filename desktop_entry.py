"""Windowed executable entry point with persistent diagnostics."""

import multiprocessing
import signal
import sys
import traceback


def smoke_test(studio):
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from PySide6.QtCore import QSettings, QTimer
    from window_state import WindowStateStore

    app = studio.QApplication.instance() or studio.QApplication([])
    from app_identity import configure_application
    configure_application(app)
    preferences = TemporaryDirectory(prefix="rustmech-smoke-")
    app._rustmech_window_state = WindowStateStore(app, QSettings(
        str(Path(preferences.name) / "windows.ini"), QSettings.Format.IniFormat))
    dialog = studio.RustMechDialog(studio.load_settings())
    result = {"passed": False, "licensed_solve_executed": False}
    result["application_name"] = app.applicationName()
    result["window_title"] = dialog.windowTitle()
    ticks = 0

    def inspect():
        nonlocal ticks
        ticks += 1
        try:
            preview = dialog.preview
            if preview is not None and preview.mesh is None and not dialog._mesh_requested:
                # The preview builds geometry first and meshes only on request.
                # Ask for the FE mesh the same way the FE mesh view does.
                result["geometry_preview_ready"] = True
                dialog.mode.setCurrentIndex(1)
                return
            if preview is not None and preview.mesh is not None:
                measure(preview.mesh)
                result["passed"] = True
            elif ticks < 300 and dialog.failed_key is None:
                return
            else:
                result["error"] = dialog.status.text() or "Timed out waiting for the FE mesh preview."
        except Exception:
            # Finish with a report instead of retrying the same failure forever.
            result["passed"] = False
            result["error"] = traceback.format_exc()
        timer.stop()
        studio.write_json(studio.ROOT / "exe_smoke_test.json", result)
        dialog.reject()

    def measure(mesh):
        result.update(
            elements=mesh.grid.n_cells,
            nodes=mesh.grid.n_points,
            removed_elements=mesh.removed_grid.n_cells,
            cores=dialog.collect().cores,
            geometry_apply=dialog.geometry_apply_button.text(),
            solver_backend=dialog.collect().solver_backend,
            bottom_platen_friction_coeff=dialog.collect().bottom_platen_friction_coeff,
            top_platen_friction_coeff=dialog.collect().top_platen_friction_coeff,
            self_contact_mode=dialog.collect().self_contact_mode,
            self_friction_coeff=dialog.collect().self_friction_coeff,
            contact_formulation=dialog.collect().contact_formulation,
            platens_visible=dialog.show_platens_cb.isChecked(),
            available_solvers=[
                dialog.solver_combo.itemData(i)
                for i in range(dialog.solver_combo.count())
            ],
        )
        dialog.main_splitter.setSizes([550, 740])
        app.processEvents()
        w_wide = dialog.main_splitter.sizes()[0]
        dialog.main_splitter.setSizes([240, 1060])
        app.processEvents()
        w_narrow = dialog.main_splitter.sizes()[0]
        dialog.main_splitter.setSizes([350, 990])
        sidebar_sizes = dialog.main_splitter.sizes()
        # The toolbar button selects a method and opens a modal menu now.
        # Exercise the splitter directly instead of blocking on that menu.
        dialog.main_splitter.toggle_sidebar()
        sidebar_hidden = dialog.main_splitter.sizes()[0] == 0
        dialog.main_splitter.toggle_sidebar()
        result["sidebar_collapse_verified"] = bool(
            sidebar_hidden
            and dialog.main_splitter.sizes() == sidebar_sizes
            and dialog.main_splitter.sizes()[0] > 0
        )
        preview_height = sum(dialog.preview_splitter.sizes())
        short_height = preview_height // 2
        details_height = preview_height // 5
        dialog.preview_splitter.setSizes(
            [preview_height - details_height, details_height]
        )
        app.processEvents()
        h_tall = dialog.preview_splitter.sizes()[0]
        dialog.preview_splitter.setSizes(
            [short_height, preview_height - short_height]
        )
        app.processEvents()
        h_short = dialog.preview_splitter.sizes()[0]
        dialog.preview_splitter.setSizes([580, 150])
        result.update(
            splitter_resize_verified=bool(
                w_wide - w_narrow >= 100 and h_tall - h_short >= 50
            ),
            main_splitter_range=[w_narrow, w_wide],
            preview_splitter_range=[h_short, h_tall],
        )
        dialog.apply_geometry()
        dialog.plotter.screenshot(str(studio.ROOT / "exe_preview.png"))

    timer = QTimer()
    timer.timeout.connect(inspect)
    timer.start(200)
    dialog.exec()
    dialog.dispose()
    app.removeEventFilter(app._rustmech_window_state)
    preferences.cleanup()
    return 0 if result["passed"] else 1


def run():
    from pathlib import Path

    root = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent
    )
    log = (root / "RustMech.log").open("a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log
    gui_mode = not any(arg in sys.argv[1:] for arg in ("--offline-test", "-h", "--help"))
    interrupted = False
    close_requested = False
    interrupt_timer = None
    previous_handler = None

    def request_close(_signum, _frame):
        nonlocal interrupted, close_requested
        # SIGINT can arrive inside a PySide callback. Keep Qt calls in its event loop.
        interrupted = close_requested = True

    def close_on_interrupt():
        nonlocal close_requested
        if close_requested and any(window.isVisible() for window in app.topLevelWidgets()):
            close_requested = False
            app.closeAllWindows()

    try:
        if gui_mode:
            previous_handler = signal.signal(signal.SIGINT, request_close)
        if "--offline-test" not in sys.argv:
            from mesh_preview_worker import warmup_mesh_preview

            warmup_mesh_preview()
        import nonlinear_compression as studio

        if gui_mode:
            if interrupted:
                return 130
            from PySide6.QtCore import QTimer

            app = studio.QApplication.instance() or studio.QApplication([sys.argv[0]])
            interrupt_timer = QTimer(app)
            interrupt_timer.timeout.connect(close_on_interrupt)
            interrupt_timer.start(100)
        if "--smoke-test" in sys.argv:
            result = smoke_test(studio)
        else:
            result = studio.main()
        return 130 if interrupted else result
    except KeyboardInterrupt:
        return 130
    except Exception:
        traceback.print_exc()
        if "--offline-test" not in sys.argv and "--smoke-test" not in sys.argv:
            from PySide6.QtWidgets import QApplication, QMessageBox

            _app = QApplication.instance() or QApplication([])
            from app_identity import configure_application
            configure_application(_app)
            QMessageBox.critical(
                None,
                "RustMech",
                f"Program could not complete.\nSee: {root / 'RustMech.log'}",
            )
        return 1
    finally:
        if interrupt_timer is not None:
            interrupt_timer.stop()
        if previous_handler is not None:
            signal.signal(signal.SIGINT, previous_handler)
        log.flush()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(run())
