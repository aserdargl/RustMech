"""Sizing for the sample workbench; colors and controls inherit RustMech's theme."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton, QLineEdit, QTableView, QVBoxLayout, QSizePolicy
from studio_style import CONTROL_HEIGHT, SidebarGroupBox


def sample_section(title):
    """Use the same section surfaces as the Design experiments planner."""
    panel = SidebarGroupBox(title)
    panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
    layout = QVBoxLayout(panel.content)
    layout.setContentsMargins(6, 6, 6, 6)
    layout.setSpacing(5)
    return panel, layout


def equal_sample_actions(*buttons):
    width = max(button.sizeHint().width() for button in buttons)
    for button in buttons:
        button.setProperty('sampleActionGroup', True)
        button.setFixedSize(width, CONTROL_HEIGHT)


def compact_sample_widgets(widget):
    for button in widget.findChildren(QPushButton):
        button.setAutoDefault(False)
        button.setDefault(False)
        button.setFixedHeight(CONTROL_HEIGHT)
        if not button.property('sampleActionGroup'):
            button.setFixedWidth(button.sizeHint().width())
    for control in widget.findChildren(QLineEdit):
        if control.parentWidget().__class__.__name__ != 'CompactComboBox':
            control.setFixedHeight(CONTROL_HEIGHT)
    for table in widget.findChildren(QTableView):
        table.setShowGrid(True)
        table.setGridStyle(Qt.PenStyle.SolidLine)
        table.verticalHeader().setDefaultSectionSize(27)
        table.horizontalHeader().setFixedHeight(27)
