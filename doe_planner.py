"""Compact method selectors for the Experimental Design workspace."""
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QWidget

from studio_style import CONTROL_HEIGHT


class DoENavigation(QWidget):
    def __init__(self, pages):
        super().__init__()
        self.setObjectName('doeNavigation')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.buttons = QButtonGroup(self)
        for index, title in enumerate(("DoE", "LHS")):
            button = QPushButton(title)
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setProperty("managesLocalizedWidth", True)
            button.setFixedSize(48, CONTROL_HEIGHT)
            self.buttons.addButton(button, index)
            layout.addWidget(button)
        self.buttons.button(pages.currentIndex()).setChecked(True)
        self.buttons.idClicked.connect(pages.setCurrentIndex)
        pages.currentChanged.connect(lambda index: self.buttons.button(index).setChecked(True))
