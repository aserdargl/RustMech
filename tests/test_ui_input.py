"""Wheel gestures scroll pages without changing focused settings."""
import unittest

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QScrollArea, QSlider, QSpinBox,
    QVBoxLayout, QWidget,
)

from ui_input import install_settings_wheel_guard


class WheelInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        install_settings_wheel_guard(cls.app)

    def test_focused_and_unfocused_controls_scroll_without_changing(self):
        page = QScrollArea()
        body = QWidget()
        layout = QVBoxLayout(body)
        controls = [QSpinBox(), QDoubleSpinBox(), QComboBox(), QSlider(Qt.Orientation.Horizontal)]
        controls[2].addItems(['Auto', 'Manual', 'Other'])
        for control in controls:
            layout.addWidget(control)
        body.setFixedSize(400, 1000)
        page.setWidget(body)
        page.resize(460, 300)
        page.show()
        self.app.processEvents()
        try:
            for control in controls:
                if isinstance(control, QComboBox):
                    control.setCurrentIndex(1)
                    value = control.currentIndex
                else:
                    control.setValue(10)
                    value = control.value
                for focused in (False, True):
                    page.verticalScrollBar().setValue(0)
                    control.setFocus() if focused else control.clearFocus()
                    before = value()
                    # The child line editor must also be protected.
                    target = control.lineEdit() if isinstance(control, QSpinBox) else control
                    position = target.rect().center()
                    event = QWheelEvent(QPointF(position), QPointF(target.mapToGlobal(position)),
                                        QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
                                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
                    QApplication.sendEvent(target, event)
                    QTest.qWait(20)
                    self.assertEqual(value(), before)
                    self.assertGreater(page.verticalScrollBar().value(), 0)
        finally:
            page.close()
