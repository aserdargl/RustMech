"""A collapsible settings column with an always accessible toolbar toggle."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QSplitter


class SidebarSplitter(QSplitter):
    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setChildrenCollapsible(False)
        self.setProperty("expandedSidebarWidth", 300)
        self.splitterMoved.connect(self._sync_toggle)
        self.toggle_button = None

    def bind_toggle(self, button):
        self.toggle_button = button
        button.clicked.connect(self.toggle_sidebar)
        self._sync_toggle()

    def _sync_toggle(self, *_):
        sizes = self.sizes()
        expanded = not sizes or sizes[0] > 0
        if expanded and sizes and sizes[0] >= self.widget(0).minimumWidth():
            self.setProperty("expandedSidebarWidth", sizes[0])
        if self.toggle_button is not None:
            self.toggle_button.setArrowType(
                Qt.ArrowType.LeftArrow if expanded else Qt.ArrowType.RightArrow)
            self.toggle_button.setChecked(expanded)
            action = "Hide settings" if expanded else "Show settings"
            self.toggle_button.setToolTip(action)
            self.toggle_button.setAccessibleName(action)

    def toggle_sidebar(self):
        sizes = self.sizes()
        if len(sizes) != 2:
            return
        total = sum(sizes)
        if sizes[0] > 0:
            self.setProperty("expandedSidebarWidth", sizes[0])
            self.setSizes([0, total])
        else:
            width = max(self.widget(0).minimumWidth(),
                        int(self.property("expandedSidebarWidth") or 300))
            width = min(width, max(0, total - self.widget(1).minimumSizeHint().width()))
            self.setSizes([width, max(0, total - width)])
        self._sync_toggle()

    def setSizes(self, sizes):
        super().setSizes(sizes)
        self._sync_toggle()

    def restoreState(self, state):
        restored = super().restoreState(state)
        # Old saved states disabled collapsing. Only the settings side may close.
        self.setCollapsible(0, True)
        self.setCollapsible(1, False)
        self._sync_toggle()
        return restored
