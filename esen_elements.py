"""Periodic-table selection and atomic percentages for MD preparation."""

from decimal import Decimal

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QValidator
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSplitter, QToolButton, QVBoxLayout, QWidget,
)

from studio_style import CONTROL_HEIGHT, CompactDoubleSpinBox, stepper_with_unit
from ui_language import translate


# A dot reserves an empty group. Lanthanides and actinides are shown below.
PERIODS = (
    "H . . . . . . . . . . . . . . . . He",
    "Li Be . . . . . . . . . . B C N O F Ne",
    "Na Mg . . . . . . . . . . Al Si P S Cl Ar",
    "K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr",
    "Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe",
    "Cs Ba . Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn",
    "Fr Ra . Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og",
    ". . La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu",
    ". . Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr",
)


class AlloyElements(QWidget):
    changed = Signal()
    selection_changed = Signal()
    LEFT_COLUMN_ROWS = 5

    def __init__(self, defaults, parent=None):
        super().__init__(parent)
        self.cells = {}
        self.rows = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Orientation.Vertical)
        split.setObjectName("esenMdPeriodicTableAndComposition")
        split.setChildrenCollapsible(False)
        split.setHandleWidth(7)
        layout.addWidget(split)

        table_panel = QWidget()
        table_layout = QVBoxLayout(table_panel)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(4)
        table = QWidget()
        table.setObjectName("mdPeriodicTable")
        table.setAccessibleName("Periodic table")
        grid = QGridLayout(table)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(1)
        for group in range(18):
            grid.setColumnStretch(group, 1)
        for period, symbols in enumerate(PERIODS):
            grid.setRowStretch(period, 1)
            for group, symbol in enumerate(symbols.split()):
                if symbol != ".":
                    self._add_cell(grid, period, group, symbol)
        grid.setRowMinimumHeight(7, 25)
        table_layout.addWidget(table, 1)
        hint = QLabel("Right-click an element to add it.")
        hint.setWordWrap(True)
        table_layout.addWidget(hint)
        split.addWidget(table_panel)

        composition = QWidget()
        composition_layout = QVBoxLayout(composition)
        composition_layout.setContentsMargins(0, 0, 0, 0)
        composition_layout.setSpacing(4)
        self.list_layout = QGridLayout()
        self.list_layout.setVerticalSpacing(2)
        self.list_layout.setHorizontalSpacing(7)
        self.list_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        composition_layout.addLayout(self.list_layout)
        self.total_label = QLabel()
        self.total_label.setObjectName("mdCompositionTotal")
        composition_layout.addWidget(self.total_label)
        composition_layout.addStretch()
        split.addWidget(composition)
        for symbol, share in defaults:
            self.add_element(symbol, share)
        split.setSizes([table_panel.sizeHint().height(), composition.sizeHint().height() + CONTROL_HEIGHT])
        self.update_total()

    def _add_cell(self, grid, period, group, symbol):
        cell = QToolButton()
        cell.setProperty("preserveLabelText", True)
        cell.setText(symbol)
        cell.setAccessibleName(symbol)
        cell.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        cell.setMinimumSize(22, 22)
        cell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        cell.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        action = QAction("Add", cell)
        action.triggered.connect(lambda _checked=False: self.add_element(symbol))
        cell.addAction(action)
        self.cells[symbol] = cell, action
        grid.addWidget(cell, period, group)

    def _mark_added(self, symbol, added):
        cell, action = self.cells[symbol]
        action.setEnabled(not added)
        cell.setProperty("inAlloy", added)
        cell.style().unpolish(cell)
        cell.style().polish(cell)
        cell.update()

    def add_element(self, symbol, share=0):
        if symbol in self.rows:
            return
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(symbol)
        label.setProperty("preserveLabelText", True)
        label.setFixedWidth(26)
        layout.addWidget(label)
        percent = CompactDoubleSpinBox()
        percent.setDecimals(4)
        percent.setRange(0, 100)
        percent.setValue(share)
        percent.setAccessibleName(f"{symbol} (%)")
        label.setBuddy(percent)
        field = stepper_with_unit(percent, "%")
        field.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(field)
        remove = QPushButton("Remove")
        remove.setAutoDefault(False)
        remove.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        remove.setFixedHeight(CONTROL_HEIGHT)
        remove.clicked.connect(lambda _checked=False: self.remove_element(symbol))
        layout.addWidget(remove)
        self._place_row(row, len(self.rows))
        self.rows[symbol] = row, percent
        self._mark_added(symbol, True)
        percent.valueChanged.connect(self.update_total)
        percent.editingFinished.connect(self.update_total)
        percent.lineEdit().textEdited.connect(lambda text: self._show_edit_total(percent, text))
        self.update_total()
        self.selection_changed.emit()

    def _place_row(self, row, index):
        if index < self.LEFT_COLUMN_ROWS:
            self.list_layout.addWidget(row, index, 0)
        else:
            self.list_layout.addWidget(row, index - self.LEFT_COLUMN_ROWS, 1)

    def remove_element(self, symbol):
        row, _ = self.rows.pop(symbol)
        self.list_layout.removeWidget(row)
        row.hide()
        row.deleteLater()
        for index, (remaining, _) in enumerate(self.rows.values()):
            self.list_layout.removeWidget(remaining)
            self._place_row(remaining, index)
        self._mark_added(symbol, False)
        self.cells[symbol][0].setFocus()
        self.update_total()
        self.selection_changed.emit()

    def composition(self):
        return ", ".join(f"{symbol}:{percent.value():.4f}" for symbol, (_, percent) in self.rows.items())

    def _show_edit_total(self, edited, text):
        state, _, _ = edited.validate(text, len(text))
        if state == QValidator.State.Acceptable:
            self._show_total(edited, edited.valueFromText(text))

    def _show_total(self, edited=None, pending=0):
        total = sum((Decimal(f"{pending if percent is edited else percent.value():.4f}")
                     for _, percent in self.rows.values()), Decimal(0))
        text = format(total.normalize(), "f").replace(".", self.locale().decimalPoint())
        self.total_label.setText(f"{translate('Composition total')}  {text} %")
        self.total_label.setStyleSheet("color: #0F172A;" if total == 100 else "color: #991B1B;")

    def update_total(self, *_):
        self._show_total()
        self.changed.emit()
