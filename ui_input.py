"""Application-wide input rules for settings controls."""
from PyQt6.QtCore import QEvent, QObject, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import (
    QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox, QDial,
    QSlider, QTabBar, QWidget,
)


class SettingsWheelGuard(QObject):
    def eventFilter(self, widget, event):
        if event.type() != QEvent.Type.Wheel or not isinstance(widget, QWidget):
            return False
        control = widget
        while control is not None:
            if isinstance(control, QAbstractScrollArea):
                return False  # Lists, tables and dropdown popups keep scrolling.
            if isinstance(control, (QAbstractSpinBox, QComboBox, QSlider, QDial, QTabBar)):
                # Focus does not grant wheel stepping. Forward to the enclosing
                # page so scrolling over an input still scrolls that page.
                parent = control.parentWidget()
                while parent is not None:
                    if isinstance(parent, QAbstractScrollArea):
                        viewport = parent.viewport()
                        forwarded = QWheelEvent(
                            QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint())),
                            event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                            event.buttons(), event.modifiers(), event.phase(), event.inverted(),
                            Qt.MouseEventSource.MouseEventNotSynthesized, event.pointingDevice(),
                        )
                        QApplication.sendEvent(viewport, forwarded)
                        break
                    parent = parent.parentWidget()
                event.accept()
                return True
            control = control.parentWidget()
        return False


def install_settings_wheel_guard(app):
    if not hasattr(app, "_rustmech_settings_wheel_guard"):
        app._rustmech_settings_wheel_guard = SettingsWheelGuard(app)
        app.installEventFilter(app._rustmech_settings_wheel_guard)
