"""Shared control sizing and appearance for the RustMech workspace."""

from html import escape
from pathlib import Path
from decimal import Decimal

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QValidator
from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QLabel, QLayout, QListView, QSizePolicy, QStyle, QStyleOptionGroupBox, QStylePainter, QVBoxLayout, QWidget


CONTROL_HEIGHT = 25
FORM_LABEL_WIDTH = 118
FORM_FIELD_MIN_WIDTH = 94
NUMERIC_FIELD_WIDTH = 88
FORM_COLUMN_GAP = 4


class CompactComboBox(QComboBox):
    """Use compact list rows and size the popup to its full item contents."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setView(QListView(self))
        self.view().setTextElideMode(Qt.TextElideMode.ElideNone)

    def showPopup(self):
        view = self.view()
        view.ensurePolished()
        margins = view.contentsMargins()
        width = (view.sizeHintForColumn(self.modelColumn())
                 + margins.left() + margins.right() + 2 * view.frameWidth()
                 + view.verticalScrollBar().sizeHint().width())
        # Recompute on every opening, including after model/font/DPI changes.
        screen = self.screen().availableGeometry()
        view.setMinimumWidth(min(max(self.width(), width), screen.width()))
        super().showPopup()


class _SectionCaption(QLabel):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = "#505050" if self.isEnabled() else "#808080"
        pen = QPen(QColor(color), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        x, y = self.width() - 15, self.height() / 2
        path = QPainterPath()
        if self.parentWidget()._expanded:
            path.moveTo(x - 4, y - 2)
            path.lineTo(x, y + 2)
            path.lineTo(x + 4, y - 2)
        else:
            path.moveTo(x - 2, y - 4)
            path.lineTo(x + 2, y)
            path.lineTo(x - 2, y + 4)
        painter.drawPath(path)


class SidebarGroupBox(QGroupBox):
    """A compact collapsible section with a consistently right-aligned chevron."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setProperty("sidebarSection", True)
        self._caption = _SectionCaption(self.title(), self)
        self._caption.setObjectName("sidebarSectionHeader")
        self._caption.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._caption.setTextFormat(Qt.TextFormat.PlainText)
        self._expanded = True
        self._header_pressed = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.content = QWidget(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.content)
        self.setAccessibleName(self.title())
        self.setAccessibleDescription("Expanded")

    def set_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        if expanded != self._expanded:
            if not expanded:
                self._expanded_heights = (self.minimumHeight(), self.maximumHeight())
                self.setFixedHeight(self.header_rect().bottom() + 6)
            else:
                low, high = self._expanded_heights
                self.setMinimumHeight(low)
                self.setMaximumHeight(high)
            self._expanded = expanded
        self.content.setVisible(expanded)
        self.setAccessibleDescription("Expanded" if expanded else "Collapsed")
        self.updateGeometry()
        self.update()
        self._caption.update()

    def mousePressEvent(self, event):
        self._header_pressed = (event.button() == Qt.MouseButton.LeftButton
                                and self.header_rect().contains(event.position().toPoint()))
        if self._header_pressed:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._header_pressed and event.button() == Qt.MouseButton.LeftButton:
            self._header_pressed = False
            if self.header_rect().contains(event.position().toPoint()):
                self.set_expanded(not self._expanded)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        self.setCursor(Qt.CursorShape.PointingHandCursor
                       if self.header_rect().contains(event.position().toPoint())
                       else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event):
        if (self.hasFocus()
                and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter)):
            if not event.isAutoRepeat():
                self.set_expanded(not self._expanded)
            event.accept()
        else:
            super().keyPressEvent(event)

    def header_rect(self):
        return QRect(5, 4, max(0, self.width() - 10), CONTROL_HEIGHT)

    def setTitle(self, title):
        super().setTitle(title)
        if hasattr(self, "_caption"):
            self._caption.setText(title)
            self.setAccessibleName(title)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._caption.setGeometry(self.header_rect())

    def paintEvent(self, event):
        option = QStyleOptionGroupBox()
        self.initStyleOption(option)
        option.text = ""
        option.subControls &= ~QStyle.SubControl.SC_GroupBoxLabel
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_GroupBox, option)
        painter.end()


class ActionFlowLayout(QLayout):
    """Place full-width action labels side by side, wrapping when needed."""

    def __init__(self, parent=None, *, equal_width=False):
        super().__init__(parent)
        self._items = []
        self._equal_width = equal_width
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(4)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < self.count() else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < self.count() else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _arrange(self, rect, apply):
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, row_height = area.x(), area.y(), 0
        row_items = []
        uniform_width = max((item.sizeHint().expandedTo(item.minimumSize()).width()
                             for item in self._items if not item.isEmpty()), default=0)

        def place_row():
            offset = (max(0, area.right() + 1 - (x - self.spacing()))
                      if self.alignment() & Qt.AlignmentFlag.AlignRight else 0)
            for item, item_rect in row_items:
                item.setGeometry(item_rect.translated(offset, 0))

        for item in self._items:
            if item.isEmpty():
                continue
            size = item.sizeHint().expandedTo(item.minimumSize())
            if self._equal_width:
                size.setWidth(uniform_width)
            if row_height and x + size.width() > area.right() + 1:
                if apply:
                    place_row()
                row_items.clear()
                x = area.x()
                y += row_height + self.spacing()
                row_height = 0
            if apply:
                row_items.append((item, QRect(QPoint(x, y), size)))
            x += size.width() + self.spacing()
            row_height = max(row_height, size.height())
        if apply:
            place_row()
        return y + row_height - rect.y() + margins.bottom()


class CompactDoubleSpinBox(QDoubleSpinBox):
    """Keep up to twelve entered digits without fixed-width decimal padding."""

    MAX_DIGITS = 12

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setDecimals(self.MAX_DIGITS)
        self.setGroupSeparatorShown(False)
        self.setFixedHeight(CONTROL_HEIGHT)
        self.setFixedWidth(NUMERIC_FIELD_WIDTH)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setAccelerated(True)
        self.setProperty("stepperFamily", "DatenburgStepper")

    def textFromValue(self, value: float) -> str:
        # Significant-digit formatting avoids exposing binary float noise;
        # Decimal expands scientific notation back into ordinary input text.
        text = format(Decimal(format(value, ".12g")), "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        if text == "-0":
            text = "0"
        return text.replace(".", self.locale().decimalPoint())

    def validate(self, text: str, position: int):
        number = text
        if self.prefix() and number.startswith(self.prefix()):
            number = number[len(self.prefix()):]
        if self.suffix() and number.endswith(self.suffix()):
            number = number[:-len(self.suffix())]
        if sum(char.isdigit() for char in number) > self.MAX_DIGITS:
            return QValidator.State.Invalid, text, position
        return super().validate(text, position)


def configure_form(form: QFormLayout) -> None:
    """Keep the label and input columns consistent across setup panels."""
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setHorizontalSpacing(FORM_COLUMN_GAP)
    form.setVerticalSpacing(2)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)


def help_tooltip(text: str) -> str:
    """Let Qt wrap explanatory text without interpreting it as markup."""
    return f"<qt>{escape(text)}</qt>"


def field_label(text: str, control: QWidget, *, tooltip: str = "") -> QLabel:
    """Pair a setup field with a consistent, keyboard-accessible label."""
    if isinstance(control, QAbstractSpinBox):
        control.setFixedWidth(NUMERIC_FIELD_WIDTH)
        control.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    else:
        control.setMinimumWidth(FORM_FIELD_MIN_WIDTH)
        control.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    label = QLabel(text)
    label.setWordWrap(True)
    label.setFixedWidth(FORM_LABEL_WIDTH)
    label.setBuddy(control)
    if tooltip:
        content = help_tooltip(tooltip)
        control.setToolTip(content)
        label.setToolTip(content)
    elif isinstance(control, QComboBox):
        control.setToolTip(control.currentText())
        control.currentTextChanged.connect(control.setToolTip)
    return label


# A single theme covers the main window and every auxiliary dialog.
_UI_ASSETS = Path(__file__).resolve().parent / "assets" / "ui"
STUDIO_STYLESHEET = (_UI_ASSETS / "datenburg.qss").read_text(encoding="utf-8").replace(
    "@ASSETS@", _UI_ASSETS.as_posix()
)
