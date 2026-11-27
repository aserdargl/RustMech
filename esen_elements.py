"""Periodic-table selection and atomic percentages for MD preparation."""

from decimal import Decimal

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QValidator
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
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


FAMILY_COLORS = {
    "Alkali metals": "#B4233F",
    "Alkaline earth metals": "#B45309",
    "d-block metals": "#2563A4",
    "Other metals": "#64748B",
    "Metalloids": "#827014",
    "Other nonmetals": "#15803D",
    "Halogens": "#0E7490",
    "Noble gases": "#7C3DB4",
    "Lanthanoids": "#BA377E",
    "Actinoids": "#875238",
}
FAMILY_BORDER_STYLE = "\n".join(
    f'QToolButton[elementFamily="{family}"] {{ border-width: 2px; border-color: {color}; }}'
    for family, color in FAMILY_COLORS.items()
) + """
QToolButton[elementFamily]:hover, QToolButton[elementFamily]:focus { border-color: #2563EB; }
"""


def _element_family(period, group, symbol):
    if period >= 7:
        return ("Lanthanoids", "Actinoids")[period - 7]
    if symbol in {"H", "C", "N", "O", "P", "S", "Se"}:
        return "Other nonmetals"
    if symbol in {"B", "Si", "Ge", "As", "Sb", "Te"}:
        return "Metalloids"
    return {0: "Alkali metals", 1: "Alkaline earth metals", 16: "Halogens", 17: "Noble gases"}.get(
        group, "d-block metals" if 2 <= group <= 11 else "Other metals",
    )


class AlloyElements(QWidget):
    changed = Signal()
    selection_changed = Signal()
    ROWS_PER_COLUMN = 5
    CELL_SIZE = 45

    def __init__(self, defaults, parent=None):
        super().__init__(parent)
        self.cells = {}
        self.rows = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        table = QWidget()
        self.table = table
        table.setObjectName("mdPeriodicTable")
        table.setAccessibleName("Periodic Table")
        table.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        grid = QGridLayout(table)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(1)
        for period, symbols in enumerate(PERIODS):
            for group, symbol in enumerate(symbols.split()):
                if symbol != ".":
                    self._add_cell(grid, period, group, symbol)
        grid.setRowMinimumHeight(7, self.CELL_SIZE + 3)
        layout.addWidget(table, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addSpacing(6)
        divider = QFrame()
        divider.setObjectName("mdCompositionDivider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        layout.addSpacing(6)
        self.list_layout = QGridLayout()
        self.list_layout.setVerticalSpacing(2)
        self.list_layout.setHorizontalSpacing(7)
        self.list_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addLayout(self.list_layout)
        self.total_label = QLabel()
        self.total_label.setObjectName("mdCompositionTotal")
        layout.addWidget(self.total_label)
        layout.addStretch()
        for symbol, share in defaults:
            self.add_element(symbol, share)
        self.update_total()

    def set_family_colors_enabled(self, enabled):
        self.table.setStyleSheet(FAMILY_BORDER_STYLE if enabled else "")

    def _add_cell(self, grid, period, group, symbol):
        cell = QToolButton()
        cell.setProperty("preserveLabelText", True)
        cell.setText(symbol)
        cell.setAccessibleName(symbol)
        family = _element_family(period, group, symbol)
        cell.setProperty("elementFamily", family)
        cell.setToolTip(family)
        cell.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        cell.setFixedSize(self.CELL_SIZE, self.CELL_SIZE)
        cell.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        action = QAction("Add", cell)
        action.triggered.connect(lambda _checked=False: self.add_element(symbol))
        cell.addAction(action)
        cell.clicked.connect(action.trigger)
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
        column, line = divmod(index, self.ROWS_PER_COLUMN)
        if column and line == 0 and self.list_layout.itemAtPosition(0, 2 * column - 1) is None:
            divider = QFrame()
            divider.setObjectName("mdCompositionDivider")
            divider.setFixedWidth(1)
            self.list_layout.addWidget(divider, 0, 2 * column - 1, self.ROWS_PER_COLUMN, 1)
        self.list_layout.addWidget(row, line, 2 * column)

    def remove_element(self, symbol, *, focus=True):
        row, _ = self.rows.pop(symbol)
        self.list_layout.removeWidget(row)
        row.hide()
        row.deleteLater()
        for index, (remaining, _) in enumerate(self.rows.values()):
            self.list_layout.removeWidget(remaining)
            self._place_row(remaining, index)
        columns, remainder = divmod(len(self.rows), self.ROWS_PER_COLUMN)
        if columns and remainder == 0:
            divider = self.list_layout.itemAtPosition(0, 2 * columns - 1).widget()
            self.list_layout.removeWidget(divider)
            divider.hide()
            divider.deleteLater()
        self._mark_added(symbol, False)
        if focus:
            self.cells[symbol][0].setFocus()
        self.update_total()
        self.selection_changed.emit()

    def composition(self):
        return ", ".join(f"{symbol}:{percent.value():.4f}" for symbol, (_, percent) in self.rows.items())

    def percentages(self):
        return tuple((symbol, percent.value()) for symbol, (_, percent) in self.rows.items())

    def _show_edit_total(self, edited, text):
        state, _, _ = edited.validate(text, len(text))
        if state == QValidator.State.Acceptable:
            self._show_total(edited, edited.valueFromText(text))

    def _show_total(self, edited=None, pending=0):
        total = sum((Decimal(f"{pending if percent is edited else percent.value():.4f}")
                     for _, percent in self.rows.values()), Decimal(0))
        text = format(total.normalize(), "f").replace(".", self.locale().decimalPoint())
        self.total_label.setText(f"{translate('Composition Total')}  {text} %")
        self.total_label.setStyleSheet("color: #0F172A;" if total == 100 else "color: #991B1B;")

    def update_total(self, *_):
        self._show_total()
        self.changed.emit()
