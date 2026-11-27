"""Per-user application preferences shared by the desktop workspaces."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QPushButton, QVBoxLayout

from studio_style import CONTROL_HEIGHT, STUDIO_STYLESHEET
from ui_language import install_language


ASK_WORKSPACE_ON_STARTUP = "ui/askWorkspaceOnStartup"
PERIODIC_TABLE_FAMILY_COLORS = "ui/periodicTableFamilyColors"


def show_application_settings(parent, preferences, *, on_periodic_colors_changed=None):
    dialog = QDialog(parent)
    dialog.setObjectName("applicationSettings")
    dialog.setWindowTitle("Settings")
    dialog.setStyleSheet(STUDIO_STYLESHEET)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(12, 10, 12, 12)

    def add_checkbox(label, key, default, on_change=None):
        checkbox = QCheckBox(label)
        checkbox.setChecked(preferences.value(key, default, type=bool))

        def save(enabled):
            preferences.setValue(key, enabled)
            preferences.sync()
            if on_change is not None:
                on_change(enabled)

        checkbox.toggled.connect(save)
        layout.addWidget(checkbox)

    add_checkbox("Ask which workspace to open at startup", ASK_WORKSPACE_ON_STARTUP, False)
    add_checkbox("Color periodic table borders by element family", PERIODIC_TABLE_FAMILY_COLORS, True,
                 on_periodic_colors_changed)

    close = QPushButton("Close")
    close.setAutoDefault(False)
    close.setFixedHeight(CONTROL_HEIGHT)
    close.clicked.connect(dialog.accept)
    layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)

    install_language().localize_tree(dialog)
    dialog.exec()
