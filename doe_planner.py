"""Compact command buttons for the DoE settings tabs."""
from PySide6.QtWidgets import QWidget, QButtonGroup, QPushButton, QSizePolicy

from studio_style import ActionFlowLayout, CONTROL_HEIGHT


class DoENavigation(QWidget):
    """Show tab selections as command buttons while retaining the tab model."""

    def __init__(self, tabs, open_lhs=None):
        super().__init__()
        self.setObjectName('doeNavigation')
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        ActionFlowLayout(self, equal_width=True)
        tabs.tabBar().hide()
        self.buttons = QButtonGroup(self)
        for index in range(tabs.count()):
            self.add_tab(index, tabs.tabText(index))
        if open_lhs is not None:
            self.add_tab(tabs.count(), "LHS")

        def select(index):
            if index == tabs.count():
                open_lhs()
            else:
                tabs.setCurrentIndex(index)

        self.buttons.button(tabs.currentIndex()).setChecked(True)
        self.buttons.idClicked.connect(select)
        tabs.currentChanged.connect(lambda index: self.buttons.button(index).setChecked(True))

    def add_tab(self, index, title):
        button = QPushButton(title)
        button.setCheckable(True)
        button.setAutoDefault(False)
        button.setFixedHeight(CONTROL_HEIGHT)
        self.buttons.addButton(button, index)
        layout = self.layout()
        previous = layout.itemAt(layout.count() - 1)
        layout.addWidget(button)
        if previous is not None:
            QWidget.setTabOrder(previous.widget(), button)
