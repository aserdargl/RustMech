"""Datenburg control sources ported to PySide6, kept local for independent builds.

Sources: shell/compact_gradient_button.py, shell/popup_style.py,
shell/widget_combo_controls.py, shell/widget_numeric_controls.py and
ui/graphcalc_settings_styles.py. Numeric values remain native Qt models;
the visible arrow widgets and their geometry are Datenburg's actual widgets.
"""
from pathlib import Path
from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPalette, QValidator, QWheelEvent
from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QStyledItemDelegate, QVBoxLayout, QWidget
from shiboken6 import isValid

_ASSETS = Path(__file__).resolve().parent / "assets" / "ui"
def qss_asset_url(name):
    return (_ASSETS / name.replace("spin-", "stepper-")).as_posix()

BUTTON_FRAME_RADIUS = 4

BUTTON_FRAME_BORDER = "#A6B8CA"

BUTTON_FRAME_HIGHLIGHT = "#3978BE"

_BUTTON_STYLE = """
@button@ {
    color: #0F172A; padding: 0; border: 1px solid @border@; border-radius: @radius@px;
    background: #F1F5F9;
}
@button@[primary="true"], @button@:checked {
    color: white; border-color: #1E4D8D;
    background: #285EA6;
}
@button@:hover { border-color: @highlight@; }
@button@:focus { border: 2px solid @highlight@; }
@button@:pressed { background: #D7E6F6; color: #0F172A; }
@button@:disabled {
    background: #E9EDF2; color: #526174; border-color: #CBD5E1;
}
"""

def compact_gradient_button_stylesheet(selector: str, base_css: str = "") -> str:
    """Build the standard face for a scoped Qt button selector."""
    style = (
        _BUTTON_STYLE.replace("@button@", selector)
        .replace("@border@", BUTTON_FRAME_BORDER)
        .replace("@radius@", str(BUTTON_FRAME_RADIUS))
        .replace("@highlight@", BUTTON_FRAME_HIGHLIGHT)
    )
    return style + f"{selector} {{{base_css}}}" if base_css else style

POPUP_SURFACE = "#F8FAFC"

POPUP_BORDER = "#334155"

POPUP_TEXT = "#111827"

POPUP_SELECTION = "#E2E8F0"

POPUP_SELECTION_TEXT = "#0F172A"

COMBO_ITEM_PADDING = "2px"

COMBO_ITEM_MIN_HEIGHT = "17px"

def compact_combo_popup_stylesheet(selectors: tuple[str, ...] = ("QComboBox",)) -> str:
    return _combo_view_stylesheet(tuple(f"{selector} QAbstractItemView" for selector in selectors))

def _combo_view_stylesheet(view_selectors: tuple[str, ...]) -> str:
    views = ",\n".join(view_selectors)
    items = ",\n".join(f"{view}::item" for view in view_selectors)
    selected = ",\n".join(f"{view}::item:selected" for view in view_selectors)
    return f"""
{views} {{
    background: {POPUP_SURFACE};
    color: {POPUP_TEXT};
    border: 1px solid {POPUP_BORDER};
    selection-background-color: {POPUP_SELECTION};
    selection-color: {POPUP_SELECTION_TEXT};
    outline: none;
}}

{items} {{
    background: transparent;
    color: {POPUP_TEXT};
    min-height: {COMBO_ITEM_MIN_HEIGHT};
    padding: {COMBO_ITEM_PADDING};
}}

{selected} {{
    background: {POPUP_SELECTION};
    color: {POPUP_SELECTION_TEXT};
}}
""".strip()

COMPACT_COMBO_POPUP_STYLE = (
    _combo_view_stylesheet(("QAbstractItemView",))
    + f"""
QListView {{
    background: {POPUP_SURFACE};
    color: {POPUP_TEXT};
}}
"""
)

def apply_compact_combo_popup_style(combo: QComboBox | None):
    if combo is None or not isValid(combo):
        return
    try:
        view = combo.view()
    except RuntimeError:
        return
    if view is None:
        return
    if not isValid(view):
        return
    if view.styleSheet() != COMPACT_COMBO_POPUP_STYLE:
        view.setStyleSheet(COMPACT_COMBO_POPUP_STYLE)
    palette = view.palette()
    palette.setColor(QPalette.ColorRole.Base, QColor(POPUP_SURFACE))
    palette.setColor(QPalette.ColorRole.Text, QColor(POPUP_TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(POPUP_SELECTION))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(POPUP_SELECTION_TEXT))
    if view.palette() != palette:
        view.setPalette(palette)
    viewport = view.viewport()
    if viewport is not None:
        if not viewport.autoFillBackground():
            viewport.setAutoFillBackground(True)
        if viewport.palette() != palette:
            viewport.setPalette(palette)

def graphcalc_combo_stylesheet() -> str:
    return (
        """
        QComboBox#GraphCalcCombo {
            /* Anchor the list below the field instead of aligning the selected row over it. */
            combobox-popup: 0;
            background: #FFFFFF;
            border: 1px solid #CBD5E1;
            border-radius: 4px;
            color: #0F172A;
            padding: 0 2px;
            margin: 0;
            min-height: 23px;
            max-height: 23px;
            font-size: 11px;
        }
        QComboBox#GraphCalcCombo:hover {
            border-color: #93C5FD;
            background: #FFFFFF;
        }
        QComboBox#GraphCalcCombo:focus {
            border-color: #3B82F6;
            background: #FFFFFF;
        }
        QComboBox#GraphCalcCombo::drop-down {
            subcontrol-origin: padding;
            subcontrol-position: center right;
            width: 18px;
            border: none;
            border-top-right-radius: 4px;
            border-bottom-right-radius: 4px;
            margin: 0;
            background: transparent;
        }
        QComboBox#GraphCalcCombo::drop-down:hover {
            background: #EFF6FF;
        }
        QComboBox#GraphCalcCombo::down-arrow {
            width: 8px;
            height: 8px;
            image: url(%s);
        }
        GraphCalcContentComboBox#GraphCalcCombo {
            padding: 0;
        }
        GraphCalcContentComboBox#GraphCalcCombo::drop-down {
            width: 8px;
        }
        QComboBox#GraphCalcCombo QLineEdit {
            border: none;
            background: transparent;
            padding: 0;
            margin: 0;
            color: #0F172A;
            selection-background-color: #BFDBFE;
        }
        """
        % qss_asset_url("spin-down-blue.svg")
        + compact_combo_popup_stylesheet(("QComboBox#GraphCalcCombo",))
    )

class DatenburgComboBox(QComboBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_light_popup_style()

    def _apply_light_popup_style(self):
        apply_compact_combo_popup_style(self)

    def wheelEvent(self, event: QWheelEvent):
        event.ignore()

    def showPopup(self):
        self._apply_light_popup_style()
        super().showPopup()

class _CompactStepperArrow(QLabel):
    """Frameless clickable arrow used in CompactButtonStepper.

    QLabel is used instead of QToolButton because Qt's native style on
    Windows insists on painting a button frame for QToolButton even after
    setAutoRaise + transparent QSS. QLabel has zero native chrome."""
    clicked = Signal()

    def __init__(self, icon: QIcon, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._icon = icon
        self._enabled = True
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(13, 10)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("background: transparent; border: none; padding: 0; margin: 0;")
        self._refresh_pixmap()

    def _refresh_pixmap(self):
        mode = QIcon.Mode.Normal if self._enabled else QIcon.Mode.Disabled
        self.setPixmap(self._icon.pixmap(QSize(9, 9), mode))

    def setEnabled(self, enabled: bool):
        self._enabled = bool(enabled)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if self._enabled else Qt.CursorShape.ArrowCursor
        )
        self._refresh_pixmap()
        super().setEnabled(self._enabled)

    def mousePressEvent(self, event):
        if self._enabled and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class _NumericFieldValidator(QValidator):
    def __init__(self, stepper):
        super().__init__(stepper)
        self.stepper = stepper

    def validate(self, text, position):
        return self.stepper.validate(text, position)


class DatenburgStepperFace:
    """The source composite face around an existing Qt numeric model.

    Qt keeps range/precision/validation/signals. Native spin buttons and painting
    are replaced by Datenburg's field + 14px column of real QLabel arrows.
    """

    def _build_datenburg_face(self):
        self.setProperty("stepperFamily", "DatenburgStepper")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._datenburg_symbols = QAbstractSpinBox.ButtonSymbols.UpDownArrows
        super().setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setFixedHeight(25)
        self.setFixedWidth(80)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setKeyboardTracking(False)
        self.setAccelerated(True)
        self._native_field = super().lineEdit()
        self._native_field.hide()
        self._face = QWidget(self)
        self._face.setObjectName("DatenburgStepperFace")
        self._face.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.field = QLineEdit(self._face)
        self.field.setObjectName("CompactStepperField")
        self.field.setTextMargins(0, 0, 0, 0)
        self.field.setMinimumWidth(0)
        self.field.setFixedHeight(25)
        self.field.setStyleSheet('''QLineEdit {
            background: transparent; border: none; color: #111827;
            font-family: "Segoe UI"; font-size: 11px;
            padding: 0px 4px; margin: 0px; min-height: 25px; max-height: 25px;
            selection-background-color: #BFDBFE;
        }''')
        self.field.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.field.setValidator(_NumericFieldValidator(self))
        self.field.editingFinished.connect(self._commit_datenburg_field)
        self.field.editingFinished.connect(self.editingFinished.emit)
        self._native_field.textChanged.connect(self._sync_datenburg_text)
        self.setFocusProxy(self.field)
        self.setStyleSheet('''
            QAbstractSpinBox {
                background: transparent; border: none; padding: 0px;
                margin: 0px; min-height: 25px; max-height: 25px;
            }
            QWidget#DatenburgStepperFace {
                background: #F3F4F6;
                border: 1px solid #9CA3AF;
                border-radius: 5px;
                outline: none;
                padding: 0px; margin: 0px;
            }
            QWidget#DatenburgStepperFace:hover {
                background: #F7F8FA;
                border-color: #6B7280;
            }
            QLineEdit#CompactStepperField {
                background: transparent;
                border: none;
                color: #111827;
                font-size: 11px;
                padding: 0px;
                margin: 0;
                min-height: 0px; max-height: 16777215px;
                selection-background-color: #BFDBFE;
            }
        ''')
        layout = QHBoxLayout(self._face)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._button_col = QWidget(self._face)
        self._button_col.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._button_col.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._button_col.setStyleSheet("background: transparent; border: none;")
        self._button_col.setFixedWidth(14)
        button_layout = QVBoxLayout(self._button_col)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(0)
        self.btn_up = _CompactStepperArrow(QIcon(str(_ASSETS / "stepper-up-blue.svg")), self._button_col)
        self.btn_down = _CompactStepperArrow(QIcon(str(_ASSETS / "stepper-down-blue.svg")), self._button_col)
        self.btn_up.clicked.connect(self.stepUp)
        self.btn_down.clicked.connect(self.stepDown)
        button_layout.addWidget(self.btn_up)
        button_layout.addWidget(self.btn_down)
        layout.addWidget(self.field, 1)
        layout.addWidget(self._button_col)
        self._datenburg_layout = layout
        self.valueChanged.connect(self._sync_datenburg_text)
        self.valueChanged.connect(self._sync_datenburg_arrows)
        self._sync_datenburg_text()
        self._sync_datenburg_arrows()

    def lineEdit(self):
        return self.field if hasattr(self, "field") else super().lineEdit()

    def text(self):
        return self.field.text() if hasattr(self, "field") else super().text()

    def setValue(self, value):
        super().setValue(value)
        self._sync_datenburg_text()

    def setSuffix(self, suffix):
        super().setSuffix(suffix)
        self._sync_datenburg_text()

    def setPrefix(self, prefix):
        super().setPrefix(prefix)
        self._sync_datenburg_text()

    def setDecimals(self, decimals):
        super().setDecimals(decimals)
        self._sync_datenburg_text()

    def _sync_datenburg_text(self, *_):
        if hasattr(self, "field"):
            self.field.setText(self._native_field.text())

    def _commit_datenburg_field(self):
        if not isValid(self._native_field):
            return
        self._native_field.setText(self.field.text())
        super().interpretText()
        self._sync_datenburg_text()
        self._sync_datenburg_arrows()

    def interpretText(self):
        self._commit_datenburg_field()

    def stepBy(self, steps):
        self._commit_datenburg_field()
        super().stepBy(steps)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._commit_datenburg_field()
            self.editingFinished.emit()
            event.accept()
        else:
            super().keyPressEvent(event)

    def _sync_datenburg_arrows(self, *_):
        if not hasattr(self, "btn_up"):
            return
        flags = self.stepEnabled()
        active = not self.isReadOnly()
        self.btn_up.setEnabled(active and bool(flags & QAbstractSpinBox.StepEnabledFlag.StepUpEnabled))
        self.btn_down.setEnabled(active and bool(flags & QAbstractSpinBox.StepEnabledFlag.StepDownEnabled))

    def setButtonSymbols(self, symbols):
        self._datenburg_symbols = symbols
        super().setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        if hasattr(self, "_button_col"):
            self._button_col.setVisible(symbols != QAbstractSpinBox.ButtonSymbols.NoButtons)
            self._arrange_datenburg_face()

    def buttonSymbols(self):
        return getattr(self, "_datenburg_symbols", QAbstractSpinBox.ButtonSymbols.UpDownArrows)

    def setRange(self, minimum, maximum):
        super().setRange(minimum, maximum)
        self._sync_datenburg_arrows()

    def setMinimum(self, value):
        super().setMinimum(value)
        self._sync_datenburg_arrows()

    def setMaximum(self, value):
        super().setMaximum(value)
        self._sync_datenburg_arrows()

    def setReadOnly(self, read_only):
        super().setReadOnly(read_only)
        if hasattr(self, "field"):
            self.field.setReadOnly(read_only)
        self._sync_datenburg_arrows()

    def _arrange_datenburg_face(self):
        if hasattr(self, "_datenburg_layout"):
            self._face.setGeometry(self.rect())
            self._datenburg_layout.invalidate()
            self._datenburg_layout.setGeometry(self.rect())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange_datenburg_face()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            self._sync_datenburg_arrows()
        elif event.type() == QEvent.Type.LocaleChange:
            self._sync_datenburg_text()

    def paintEvent(self, event):
        self._arrange_datenburg_face()
        # WA_StyledBackground already paints the source QWidget face. Calling
        # native spin painting (or drawing PE_Widget twice) adds native chrome
        # and changes anti-aliased border pixels.


class DatenburgComboDelegate(QStyledItemDelegate):
    """Preserve Datenburg's 17px content + 2px top/bottom padding on Qt 6.11."""
    def displayText(self, value, locale):
        from ui_language import translate
        return translate(super().displayText(value, locale))

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(21)
        return size


def configure_button_shadow(button, enabled=True):
    """Keep the face crisp; elevation is a faint one-pixel external shadow."""
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QGraphicsDropShadowEffect

    button.setProperty("subtleButtonShadowEnabled", bool(enabled))
    effect = button.graphicsEffect()
    if effect is not None and not isinstance(effect, QGraphicsDropShadowEffect):
        return  # Preserve component-owned opacity/other graphics effects.
    if effect is None:
        effect = QGraphicsDropShadowEffect(button)
        button.setGraphicsEffect(effect)
    effect.setBlurRadius(2)
    effect.setOffset(0, 1)
    effect.setColor(QColor(15, 23, 42, 28))
    effect.setEnabled(bool(enabled) and button.isEnabled() and not button.isDown())
    if not button.property("subtleButtonShadowConnected"):
        button.pressed.connect(lambda: _update_button_shadow(button, True))
        button.released.connect(lambda: _update_button_shadow(button, False))
        button.setProperty("subtleButtonShadowConnected", True)


def _update_button_shadow(button, pressed=False):
    effect = button.graphicsEffect()
    if effect is not None:
        effect.setEnabled(bool(button.property("subtleButtonShadowEnabled"))
                          and button.isEnabled() and not pressed)


def install_button_shadows(app):
    """Install once at the composition root, including later-created dialogs."""
    from PySide6.QtCore import QEvent, QObject, Qt
    from PySide6.QtWidgets import QAbstractSpinBox, QPushButton, QToolButton

    if hasattr(app, "_subtle_button_shadow_filter"):
        return

    class ButtonShadowFilter(QObject):
        def eventFilter(self, obj, event):
            if isinstance(obj, (QPushButton, QToolButton)):
                if event.type() == QEvent.Type.Polish:
                    # Arrow columns and auto-raised glyphs remain integrated.
                    parent = obj.parentWidget()
                    in_stepper = False
                    while parent is not None:
                        if isinstance(parent, QAbstractSpinBox) or parent.metaObject().className() in {
                            "CompactButtonStepper", "CompactDoubleButtonStepper"
                        }:
                            in_stepper = True
                            break
                        parent = parent.parentWidget()
                    if (not in_stepper
                            and not (isinstance(obj, QToolButton) and obj.autoRaise())
                            and obj.graphicsEffect() is None
                            and obj.property("_custom_ui_3d_enabled") is not False):
                        configure_button_shadow(obj)
                elif event.type() == QEvent.Type.EnabledChange:
                    if obj.property("subtleButtonShadowConnected"):
                        _update_button_shadow(obj, obj.isDown())
            return False

    guard = ButtonShadowFilter(app)
    app.installEventFilter(guard)
    app._subtle_button_shadow_filter = guard
    for widget in app.allWidgets():
        if isinstance(widget, (QPushButton, QToolButton)) and widget.testAttribute(
            Qt.WidgetAttribute.WA_WState_Polished
        ):
            # Reuse the same eligibility rules for controls created before install.
            guard.eventFilter(widget, QEvent(QEvent.Type.Polish))
