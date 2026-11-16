"""Capture standard-test UI examples with isolated window preferences."""
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PyQt6.QtWidgets import QApplication
from nonlinear_compression import RustMechDialog
from model import Settings
from mesh_engine import build_preview


def main():
    app=QApplication.instance() or QApplication([])
    folder=Path('design/standard-tests');folder.mkdir(parents=True,exist_ok=True)
    with patch('window_state.install_window_state'),patch.object(RustMechDialog,'start_preview',lambda self:None):
        dialog=RustMechDialog(Settings())
        try:
            dialog.resize(1440,960);dialog.show();app.processEvents()
            for name,identifier in (('tension','astm_e8_subsize'),('flexure','iso178_preferred')):
                dialog.test_profile_combo.setCurrentIndex(dialog.test_profile_combo.findData(identifier))
                dialog.architecture_combo.setCurrentIndex(dialog.architecture_combo.findData('porous_centre'))
                dialog.active_geometry=dialog.collect()
                dialog.preview=build_preview(dialog.collect())
                dialog.render_preview(reset=True);app.processEvents()
                dialog.grab().save(str(folder/(name+'.png')))
                dialog.plotter.screenshot(str(folder/(name+'-specimen.png')))
        finally:dialog.dispose();dialog.close()
    print(folder.resolve())


if __name__=='__main__':main()
