"""Exercise Gitterburg's real menu and export worker, then RustMech's reader.

Uses a temporary pool and a known closed specimen. No solver is started.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SENDER = r'''
import time
from unittest.mock import patch
import pyvista as pv
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
import main_ui
from application_composition import build_default_main_window_dependencies
from services.region_project import generate_region_model, generate_region_project, replace_region_in_project_model
from services.design_exchange import list_designs
from ui_components.main_window_state import MainWindowInitialState
from ui_components.topology_viewport import TopologyViewport
main_ui.GitterburgApp._initialize_preview_viewport = lambda _: None
main_ui.GitterburgApp.check_for_updates_silent = lambda _: None
main_ui.GitterburgApp._schedule_left_panel_state_save = lambda _: None
main_ui.GitterburgApp._save_left_panel_state = lambda _: None
TopologyViewport.enter = lambda _: None
app = QApplication([])
window = main_ui.GitterburgApp(build_default_main_window_dependencies(
    generate_region_model, generate_region_project, replace_region_in_project_model), MainWindowInitialState())
errors = []
window._report_error = lambda *args: errors.append(str(args))
window.show()
window.current_model = None
window.current_mesh = pv.Box(bounds=(0, 4, 0, 3, 0, 6)).triangulate()
window.current_model_is_visual_preview = False
action = next(a for a in window.sticky_output_btn.menu().actions() if a.objectName() == 'sendToRustMechAction')
with patch('ui_components.rustmech_transfer_mixin.QInputDialog.getText', return_value=('End-to-end specimen', True)):
    action.trigger()
deadline = time.monotonic() + 20
while not list_designs()[0] and not errors and time.monotonic() < deadline:
    app.processEvents()
    QTest.qWait(20)
assert not errors, errors
assert len(list_designs()[0]) == 1, 'Transfer did not complete'
window.close()
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gitterburg", default=str(ROOT.parent / "Gitterburg"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="rustmech-pool-e2e-") as temporary:
        environment = dict(os.environ, RUSTMECH_DESIGN_POOL=str(Path(temporary) / "pool"))
        result = subprocess.run([sys.executable, "-c", SENDER], cwd=args.gitterburg,
                                env=environment, capture_output=True, text=True, timeout=40)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        os.environ["RUSTMECH_DESIGN_POOL"] = environment["RUSTMECH_DESIGN_POOL"]
        from design_exchange import list_designs
        from design_pool import load_pool_surface
        entries, errors = list_designs()
        assert not errors and len(entries) == 1
        metadata, surface = load_pool_surface(entries[0]["id"])
        assert metadata["name"] == "End-to-end specimen"
        assert abs(surface.volume - 72) < 1e-6
        print(json.dumps(dict(passed=True, actual_gitterburg_menu=True,
                              actual_export_worker=True, rustmech_surface_validated=True,
                              volume_mm3=surface.volume, solver_started=False)))


if __name__ == "__main__":
    main()
