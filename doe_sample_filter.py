"""Small, independent search and column selection for each sample table."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QWidget

from studio_style import CompactComboBox


class SampleTableFilter(QWidget):
    changed = Signal()

    def __init__(self, columns, turkish=False, parent=None):
        super().__init__(parent)
        self.turkish = turkish
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.column = CompactComboBox()
        self.column.setToolTip("Aranacak sutun" if turkish else "Search column")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Bu tabloda ara..." if turkish else "Search this table...")
        self.column.setFixedHeight(24)
        self.search.setFixedHeight(24)
        layout.addWidget(self.column)
        layout.addWidget(self.search, 1)
        self.set_columns(columns)
        self.column.currentIndexChanged.connect(lambda *args: self.changed.emit())
        self.search.textChanged.connect(lambda *args: self.changed.emit())

    def set_columns(self, columns):
        selected = self.column.currentData()
        self.column.blockSignals(True)
        self.column.clear()
        self.column.addItem("Tum sutunlar" if self.turkish else "All columns", -1)
        for index, label in enumerate(columns):
            self.column.addItem(label, index)
        self.column.setCurrentIndex(max(0, self.column.findData(selected)))
        self.column.blockSignals(False)

    def matches(self, values):
        column = self.column.currentData()
        text = self.search.text().strip().casefold()
        if column is not None and 0 <= column < len(values):
            values = [values[column]]
        return not text or any(text in str(value).casefold() for value in values)
