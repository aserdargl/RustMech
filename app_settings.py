"""Per-user application preferences shared by the desktop workspaces."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from studio_style import CONTROL_HEIGHT, STUDIO_STYLESHEET, CompactSpinBox, stepper_with_unit
from ui_language import install_language


ASK_WORKSPACE_ON_STARTUP = "ui/askWorkspaceOnStartup"
PERIODIC_TABLE_FAMILY_COLORS = "ui/periodicTableFamilyColors"
PERIODIC_TABLE_FONT_SIZE = "ui/periodicTableFontSize"
PERIODIC_TABLE_FONT_RANGE = (12, 26)
PERIODIC_TABLE_DEFAULT_FONT_SIZE = 18


def periodic_table_font_size(preferences):
    minimum, maximum = PERIODIC_TABLE_FONT_RANGE
    return min(maximum, max(minimum, preferences.value(PERIODIC_TABLE_FONT_SIZE,
                                                       PERIODIC_TABLE_DEFAULT_FONT_SIZE, type=int)))


def show_application_settings(parent, preferences, *, on_periodic_colors_changed=None,
                              on_periodic_font_changed=None):
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

    font_size = CompactSpinBox()
    font_size.setObjectName("periodicTableFontSize")
    font_size.setRange(*PERIODIC_TABLE_FONT_RANGE)
    font_size.setValue(periodic_table_font_size(preferences))
    label = QLabel("Periodic Table Font Size")
    label.setBuddy(font_size)
    font_row = QHBoxLayout()
    font_row.addWidget(label)
    font_row.addWidget(stepper_with_unit(font_size, "px"))
    font_row.addStretch()
    layout.addLayout(font_row)

    def save_font_size(size):
        preferences.setValue(PERIODIC_TABLE_FONT_SIZE, size)
        preferences.sync()
        if on_periodic_font_changed is not None:
            on_periodic_font_changed(size)

    font_size.valueChanged.connect(save_font_size)

    close = QPushButton("Close")
    close.setAutoDefault(False)
    close.setFixedHeight(CONTROL_HEIGHT)
    close.clicked.connect(dialog.accept)
    layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)

    install_language().localize_tree(dialog)
    dialog.exec()
