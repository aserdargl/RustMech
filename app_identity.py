"""Shared desktop identity for source launches and the Windows executable."""

import sys
from pathlib import Path

from PySide6.QtGui import QIcon


WINDOWS_APP_ID = "RustMech.Desktop"
ICON_PATH = Path(__file__).resolve().parent / "assets" / "ui" / "rustmech.ico"


def configure_application(app):
    """Set identity before creating windows; child windows inherit the icon."""
    from datenburg_widgets import install_button_shadows
    install_button_shadows(app)
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
    app.setApplicationName("RustMech")
    app.setApplicationDisplayName("RustMech")
    app.setWindowIcon(QIcon(str(ICON_PATH)))
