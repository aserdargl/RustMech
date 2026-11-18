"""Interaction contracts of the real Datenburg control port."""
import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QAbstractSpinBox, QToolButton, QWidget

from datenburg_widgets import POPUP_BORDER
from studio_style import CompactComboBox, CompactDoubleSpinBox, CompactSpinBox, STUDIO_STYLESHEET
from kratos_settings import ToleranceSpinBox


class DatenburgControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_real_frameless_arrow_column_and_single_step_signal(self):
        for kind in (CompactSpinBox, CompactDoubleSpinBox):
            with self.subTest(kind=kind.__name__):
                control = kind()
                try:
                    control.setRange(0, 10)
                    control.setValue(5)
                    control.show()
                    self.app.processEvents()
                    self.assertFalse(control._native_field.isVisible())
                    self.assertFalse(control.findChildren(QToolButton))
                    self.assertEqual(control._button_col.width(), 14)
                    self.assertEqual(control.field.height(), 25)
                    self.assertEqual(control.field.font().pixelSize(), 11)
                    spy = QSignalSpy(control.valueChanged)
                    QTest.mouseClick(control.btn_up, Qt.MouseButton.LeftButton)
                    self.assertEqual(control.value(), 6)
                    self.assertEqual(spy.count(), 1)
                    control.setValue(10)
                    self.assertFalse(control.btn_up.isEnabled())
                    control.setReadOnly(True)
                    self.assertFalse(control.btn_down.isEnabled())
                finally:
                    control.close()

    def test_plain_numeric_inputs_hide_the_whole_arrow_column(self):
        control = CompactDoubleSpinBox()
        try:
            control.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            control.show()
            self.app.processEvents()
            self.assertTrue(control._button_col.isHidden())
            self.assertEqual(control.field.width(), control.width())
        finally:
            control.close()

    def test_enter_commits_scientific_tolerance_and_suffix(self):
        tolerance = ToleranceSpinBox()
        try:
            tolerance.setRange(1e-12, 1)
            tolerance.field.setText('2e-8')
            QTest.keyClick(tolerance, Qt.Key.Key_Return)
            self.assertAlmostEqual(tolerance.value(), 2e-8, places=12)
            self.assertEqual(tolerance.text(), '2e-08')
        finally:
            tolerance.close()

    def test_popup_keeps_source_row_metrics_and_complete_labels(self):
        parent = QWidget()
        parent.setStyleSheet(STUDIO_STYLESHEET)
        control = CompactComboBox(parent)
        control.addItems(['Gyroid', 'A much longer structure name', 'Diamond'])
        control.setCurrentIndex(2)
        control.setFixedWidth(100)
        effect = Qt.UIEffect.UI_AnimateCombo
        animated = self.app.isEffectEnabled(effect)
        self.app.setEffectEnabled(effect, False)
        try:
            parent.move(parent.screen().availableGeometry().topLeft() + QPoint(40, 40))
            parent.show()
            QTest.mouseClick(control, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            self.assertTrue(control.view().isVisible())
            self.assertEqual(control.height(), 25)
            self.assertEqual(control.view().gridSize().height(), 17)
            self.assertGreaterEqual(control.view().width(), control.view().sizeHintForColumn(0))
            # Qt can share the field's bottom border with the popup's top edge.
            field_bottom = control.mapToGlobal(QPoint(0, control.height() - 1)).y()
            popup_top = control.view().window().frameGeometry().top()
            self.assertIn(popup_top, (field_bottom, field_bottom + 1))
            popup = control.view().grab().toImage()
            width, height = popup.width(), popup.height()
            edges = ((0, height // 2), (width - 1, height // 2),
                     (width // 2, 0), (width // 2, height - 1))
            for x, y in edges:
                self.assertEqual(popup.pixelColor(x, y).name(), POPUP_BORDER)
            QTest.keyClick(control.view(), Qt.Key.Key_Up)
            QTest.keyClick(control.view(), Qt.Key.Key_Return)
            self.assertEqual(control.currentIndex(), 1)
        finally:
            control.hidePopup()
            parent.close()
            self.app.setEffectEnabled(effect, animated)


if __name__ == '__main__':
    unittest.main()
