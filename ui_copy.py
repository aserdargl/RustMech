"""Consistent text selection and spreadsheet-friendly copying across the UI."""
import csv
import io

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QApplication, QComboBox, QGroupBox,
    QLabel, QTableView,
)


def _cell_text(index):
    value = index.data(Qt.ItemDataRole.DisplayRole)
    if value is not None and str(value):
        return str(value)
    checked = index.data(Qt.ItemDataRole.CheckStateRole)
    if checked is not None:
        return "1" if checked == Qt.CheckState.Checked or checked == 2 else "0"
    return "" if value is None else str(value)


def copy_table(table, *, all_rows=False):
    """Copy visible columns in visual order; keep holes in sparse selections."""
    model = table.model()
    selection = table.selectionModel()
    if model is None or selection is None:
        return
    selected = {(index.row(), index.column()) for index in selection.selectedIndexes()}
    if not all_rows and not selected:
        return
    header = table.horizontalHeader()
    columns = [header.logicalIndex(i) for i in range(header.count())
               if not table.isColumnHidden(header.logicalIndex(i))]
    rows = [table.verticalHeader().logicalIndex(i) for i in range(model.rowCount())
            if not table.isRowHidden(table.verticalHeader().logicalIndex(i))]
    if not all_rows:
        columns = [col for col in columns if any(c == col for _, c in selected)]
        rows = [row for row in rows if any(r == row for r, _ in selected)]
    data = []
    if all_rows:
        data.append([str(model.headerData(col, Qt.Orientation.Horizontal) or "") for col in columns])
    for row in rows:
        data.append([_cell_text(model.index(row, col))
                     if all_rows or (row, col) in selected else "" for col in columns])
    if data and columns:
        stream = io.StringIO(newline="")
        csv.writer(stream, delimiter="\t", lineterminator="\n").writerows(data)
        QApplication.clipboard().setText(stream.getvalue().removesuffix("\n"))


def _action(widget, text, callback, shortcut=None):
    action = QAction(text, widget)
    action.triggered.connect(lambda _checked=False: callback())
    if shortcut is not None:
        action.setShortcut(QKeySequence(shortcut))
        # An active cell editor keeps its native text-copy shortcut.
        action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
    widget.addAction(action)
    return action


def configure_copying(widget):
    if isinstance(widget, QLabel):
        widget.setTextInteractionFlags(
            widget.textInteractionFlags()
            | Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
    elif isinstance(widget, QTableView) and not widget.property("copyingConfigured"):
        widget.setProperty("copyingConfigured", True)
        widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        _action(widget, "Copy selection", lambda: copy_table(widget), QKeySequence.StandardKey.Copy)
        _action(widget, "Copy entire table with headers", lambda: copy_table(widget, all_rows=True))
        _action(widget, "Select all", widget.selectAll, QKeySequence.StandardKey.SelectAll)
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
    elif isinstance(widget, (QAbstractButton, QGroupBox, QComboBox)) and not widget.property("copyingConfigured"):
        widget.setProperty("copyingConfigured", True)
        if isinstance(widget, QComboBox):
            if widget.isEditable():
                return  # The line editor already provides text selection and Copy.
            text = widget.currentText
        elif isinstance(widget, QGroupBox):
            text = widget.title
        else:
            text = widget.text
        _action(widget, "Copy text", lambda: QApplication.clipboard().setText(text()))
        if widget.contextMenuPolicy() == Qt.ContextMenuPolicy.DefaultContextMenu:
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)


class CopySupport(QObject):
    def eventFilter(self, widget, event):
        if event.type() in (QEvent.Type.Polish, QEvent.Type.Show):
            configure_copying(widget)
        return False


def install_copy_support(app):
    if not hasattr(app, "_rustmech_copy_support"):
        app._rustmech_copy_support = CopySupport(app)
        app.installEventFilter(app._rustmech_copy_support)
        for widget in app.allWidgets():
            configure_copying(widget)
