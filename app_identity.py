"""Shared desktop identity for source launches and the Windows executable."""

import sys
from pathlib import Path

from PySide6.QtGui import QIcon


WINDOWS_APP_ID = "RustMech.Desktop"
ICON_PATH = Path(__file__).resolve().parent / "assets" / "ui" / "rustmech.ico"


def configure_application(app, *, name="RustMech", app_id=WINDOWS_APP_ID, settings=None):
    """Set identity before creating windows; child windows inherit the icon."""
    from datenburg_widgets import install_button_shadows
    from file_dialog import install_file_dialogs
    from window_caption import install_window_captions
    from window_state import install_window_state

    install_button_shadows(app)
    install_window_captions(app)
    install_file_dialogs(app)
    install_window_state(app, settings)
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    app.setApplicationName(name)
    app.setApplicationDisplayName(name)
    app.setWindowIcon(QIcon(str(ICON_PATH)))
