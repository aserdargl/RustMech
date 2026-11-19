"""Numeric entry precision, compact display and digit limits in real Qt controls."""
import unittest

from PySide6.QtCore import QLocale, Qt
from PySide6.QtGui import QValidator
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from studio_style import CompactDoubleSpinBox, CompactSpinBox, STUDIO_STYLESHEET, stepper_with_unit
from mesh_convergence_style import CONVERGENCE_STYLESHEET


class NumericControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.spin = CompactDoubleSpinBox()
        self.spin.setLocale(QLocale(QLocale.Language.English))
        self.spin.setRange(-1e12, 1e12)
        self.spin.setKeyboardTracking(False)

    def tearDown(self):
        self.spin.close()

    def enter(self, text):
        self.spin.lineEdit().selectAll()
        QTest.keyClicks(self.spin.lineEdit(), text)
        QTest.keyClick(self.spin, Qt.Key.Key_Return)

    def test_display_has_no_padding_or_float_noise(self):
        for value, expected in ((6, "6"), (6.5, "6.5"), (6.5423423, "6.5423423"),
                                (200000, "200000"), (12345.1234567, "12345.1234567"),
                                (0.0000001, "0.0000001"), (-6.5, "-6.5")):
            with self.subTest(value=value):
                self.spin.setValue(value)
                self.assertEqual(self.spin.text(), expected)

    def test_entered_fraction_is_preserved(self):
        self.enter("6.5423423")
        self.assertAlmostEqual(self.spin.value(), 6.5423423, places=10)
        self.assertEqual(self.spin.text(), "6.5423423")
        self.enter("6.5000")
        self.assertEqual(self.spin.text(), "6.5")

    def test_display_decimals_preserve_unedited_values_and_allow_precise_input(self):
        self.spin.setValue(0.123456)
        self.spin.setDisplayDecimals(3)
        self.assertEqual(self.spin.text(), "0.123")
        QTest.keyClick(self.spin, Qt.Key.Key_Return)
        self.assertAlmostEqual(self.spin.value(), 0.123456)
        self.spin.setSingleStep(0.05)
        self.spin.stepUp()
        self.assertAlmostEqual(self.spin.value(), 0.173456)
        self.assertEqual(self.spin.text(), "0.173")
        self.enter("0.321234")
        self.assertAlmostEqual(self.spin.value(), 0.321234)
        self.assertEqual(self.spin.text(), "0.321")
        self.spin.setLocale(QLocale(QLocale.Language.Turkish))
        self.spin.setDisplayDecimals(6)
        self.assertEqual(self.spin.text(), "0,321234")

    def test_unit_sits_one_pixel_outside_the_stepper(self):
        spin = CompactDoubleSpinBox()
        row = stepper_with_unit(spin, "mm")
        try:
            row.resize(220, 32)
            row.show()
            self.app.processEvents()
            unit = row.layout().itemAt(1).widget()
            self.assertEqual(unit.text(), "mm")
            self.assertEqual(unit.x() - spin.geometry().right() - 1, 1)
            self.assertNotIn("mm", spin.text())
        finally:
            row.close()

    def test_twelve_digits_and_rejection_of_thirteenth(self):
        self.enter("6.12345678901")
        self.assertEqual(self.spin.text(), "6.12345678901")
        self.enter("6.123456789012")
        self.assertEqual(self.spin.text(), "6.12345678901")
        self.enter("1234567890123")
        self.assertEqual(self.spin.text(), "123456789012")
        self.assertEqual(self.spin.validate("1234567890123", 13)[0], QValidator.State.Invalid)

    def test_suffix_and_sign_do_not_use_digit_budget(self):
        self.spin.setSuffix(" mm")
        self.enter("-6.12345678901")
        self.assertEqual(self.spin.text(), "-6.12345678901 mm")
        self.assertAlmostEqual(self.spin.value(), -6.12345678901, places=11)

    def test_paste_respects_digit_limit(self):
        clipboard = self.app.clipboard()
        previous = clipboard.text()
        try:
            self.spin.setValue(6)
            self.spin.lineEdit().selectAll()
            clipboard.setText("1234567890123")
            QTest.keyClick(self.spin.lineEdit(), Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
            self.assertEqual(self.spin.text(), "6")
            clipboard.setText("6.5423423")
            QTest.keyClick(self.spin.lineEdit(), Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
            QTest.keyClick(self.spin, Qt.Key.Key_Return)
            self.assertEqual(self.spin.text(), "6.5423423")
        finally:
            clipboard.setText(previous)

    def test_locale_and_stepper(self):
        self.spin.setLocale(QLocale(QLocale.Language.Turkish))
        self.enter("6,5423423")
        self.assertEqual(self.spin.text(), "6,5423423")
        self.assertAlmostEqual(self.spin.value(), 6.5423423, places=10)
        self.spin.setValue(6)
        self.spin.setSingleStep(0.5)
        self.spin.stepUp()
        self.assertEqual(self.spin.text(), "6,5")
        self.spin.stepDown()
        self.assertEqual(self.spin.text(), "6")

    def test_shared_integer_and_decimal_appearance_and_arrow_hit_targets(self):
        parent = QWidget()
        parent.setObjectName("meshConvergenceStudy")
        parent.setStyleSheet(STUDIO_STYLESHEET + CONVERGENCE_STYLESHEET)
        try:
            for index, kind in enumerate((CompactSpinBox, CompactDoubleSpinBox)):
                with self.subTest(kind=kind.__name__):
                    spin = kind(parent)
                    spin.move(0, index * 30)
                    spin.setRange(0, 10)
                    spin.setValue(5)
                    parent.show()
                    self.app.processEvents()
                    self.assertEqual(spin.height(), 25)
                    self.assertEqual(spin.alignment(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                    self.assertEqual(spin.field.alignment(), spin.alignment())
                    self.assertEqual(spin.property("stepperFamily"), "DatenburgStepper")
                    self.assertFalse(spin.keyboardTracking())
                    margins = spin.lineEdit().textMargins()
                    self.assertEqual((margins.left(), margins.top(), margins.right(), margins.bottom()), (0, 0, 0, 0))
                    # A nested workspace theme must not change the shared font.
                    self.assertEqual(spin.lineEdit().font().pixelSize(), 11)
                    for arrow, expected in ((spin.btn_up, 6), (spin.btn_down, 5)):
                        self.assertEqual(arrow.width(), 13)
                        self.assertEqual(arrow.height(), 10)
                        QTest.mouseClick(arrow, Qt.MouseButton.LeftButton)
                        self.assertEqual(spin.value(), expected)
        finally:
            parent.close()


if __name__ == "__main__":
    unittest.main()
