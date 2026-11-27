"""Per-user application preferences shared by the desktop workspaces."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QPushButton, QVBoxLayout

from studio_style import CONTROL_HEIGHT, STUDIO_STYLESHEET
from ui_language import install_language


ASK_WORKSPACE_ON_STARTUP = "ui/askWorkspaceOnStartup"


def show_application_settings(parent, preferences):
    dialog = QDialog(parent)
    dialog.setObjectName("applicationSettings")
    dialog.setWindowTitle("Settings")
    dialog.setStyleSheet(STUDIO_STYLESHEET)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(12, 10, 12, 12)

    ask_workspace = QCheckBox("Ask which workspace to open at startup")
    ask_workspace.setChecked(preferences.value(ASK_WORKSPACE_ON_STARTUP, False, type=bool))

    def save_startup_preference(enabled):
        preferences.setValue(ASK_WORKSPACE_ON_STARTUP, enabled)
        preferences.sync()

    ask_workspace.toggled.connect(save_startup_preference)
    layout.addWidget(ask_workspace)

    close = QPushButton("Close")
    close.setAutoDefault(False)
    close.setFixedHeight(CONTROL_HEIGHT)
    close.clicked.connect(dialog.accept)
    layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)

    install_language().localize_tree(dialog)
    dialog.exec()
