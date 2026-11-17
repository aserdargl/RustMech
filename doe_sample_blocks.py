"""Draggable, numbered naming tokens with native Qt model ordering."""

from PySide6.QtCore import QMimeData, QModelIndex, QPersistentModelIndex, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QStyledItemDelegate, QStyle

C_BG_PANEL, C_BG_SOFT, C_BORDER, C_BTN_BLUE = "#FFFFFF", "#F3F3F3", "#D1DBE5", "#1E4D8D"


class NamingBlockDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(140, 28)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.setPen(QColor(C_BTN_BLUE if selected else C_BORDER))
        painter.setBrush(QColor(C_BG_SOFT if selected else C_BG_PANEL))
        painter.drawRoundedRect(QRectF(rect), 8, 8)
        badge = rect.adjusted(2, 2, 0, 0)
        badge.setSize(QSize(18, 18))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(C_BTN_BLUE))
        painter.drawRoundedRect(QRectF(badge), 7, 7)
        painter.setPen(QColor(C_BG_PANEL))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(index.row() + 1))
        text_rect = rect.adjusted(24, 0, -2, 0)
        font = option.font
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(C_BTN_BLUE))
        label = index.data(Qt.ItemDataRole.DisplayRole) or ""
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, text_rect.width()))
        painter.restore()


class NamingBlocks(QListWidget):
    orderChanged = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("NamingBlocks")
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setGridSize(QSize(140, 28))
        self.setItemDelegate(NamingBlockDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropOverwriteMode(False)
        self.setDropIndicatorShown(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumHeight(60)
        self._drag_index = QPersistentModelIndex()
        self._drop_slot = None
        self.model().rowsMoved.connect(self._order_changed)

    def _order_changed(self, *args):
        self.doItemsLayout()
        self.viewport().update()
        self.orderChanged.emit()

    def move_block(self, source, target):
        if source == target or not (0 <= source < self.count() and 0 <= target < self.count()):
            return
        destination = target + 1 if target > source else target
        if self.model().moveRow(QModelIndex(), source, QModelIndex(), destination):
            self.setCurrentRow(target)
            self.viewport().update()

    def startDrag(self, supported_actions):
        # Own the drag completely: native icon movement can leave floating item
        # coordinates behind even when the model rows have already been moved.
        if not self.currentIndex().isValid():
            return
        self._drag_index = QPersistentModelIndex(self.currentIndex())
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData("application/x-gitterburg-naming-block", b"move")
        drag.setMimeData(mime)
        rect = self.visualItemRect(self.currentItem())
        drag.setPixmap(self.viewport().grab(rect))
        drag.setHotSpot(rect.center() - rect.topLeft())
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self._drag_index = QPersistentModelIndex()
            self._drop_slot = None
            self.doItemsLayout()
            self.viewport().update()

    def dragEnterEvent(self, event):
        if event.source() is self:
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def _insertion_at(self, point):
        if not self.count():
            return 0
        rects = [self.visualItemRect(self.item(row)) for row in range(self.count())]
        if point.y() < rects[0].top():
            return 0
        if point.y() > rects[-1].bottom():
            return self.count()
        row = min(range(self.count()), key=lambda index:
                  (rects[index].center().y() - point.y()) ** 2 +
                  (rects[index].center().x() - point.x()) ** 2)
        return row + int(point.x() >= rects[row].center().x())

    def dragMoveEvent(self, event):
        if event.source() is not self:
            event.ignore()
            return
        self._drop_slot = self._insertion_at(event.position().toPoint())
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        self.viewport().update()

    def dragLeaveEvent(self, event):
        self._drop_slot = None
        self.viewport().update()
        event.accept()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._drop_slot is None or not self.count():
            return
        at_end = self._drop_slot == self.count()
        row = self.count() - 1 if at_end else self._drop_slot
        rect = self.visualItemRect(self.item(row))
        x = rect.right() if at_end else rect.left()
        painter = QPainter(self.viewport())
        painter.setPen(QPen(QColor(C_BTN_BLUE), 3))
        painter.drawLine(x, rect.top() + 3, x, rect.bottom() - 3)
        painter.end()

    def dropEvent(self, event):
        if event.source() is not self:
            event.ignore()
            return
        source = self._drag_index.row() if self._drag_index.isValid() else self.currentRow()
        slot = self._insertion_at(event.position().toPoint())
        target = slot - 1 if slot > source else slot
        self._drop_slot = None
        self.move_block(source, target)
        self.doItemsLayout()
        self.viewport().update()
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
