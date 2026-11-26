"""Exercise real Qt selection, clipboard shortcuts, and editable table cells."""
import csv
import io
import unittest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QTableWidget, QTableWidgetItem
from ui_copy import copy_table, install_copy_support


class CopySupportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        install_copy_support(cls.app)

    def setUp(self):
        self.table = QTableWidget(3, 3)
        self.table.setHorizontalHeaderLabels(['Name', 'Value', 'Unit'])
        for r, values in enumerate((('Steel', '200000', 'MPa'), ('Titanium', '110', 'GPa'), ('A\tB', 'multi\nline', ''))):
            for c, value in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(value))
        self.table.show()
        self.table.setFocus()
        self.app.processEvents()

    def tearDown(self):
        self.table.close()

    def select(self, *cells):
        self.table.clearSelection()
        for row, col in cells:
            self.table.selectionModel().select(self.table.model().index(row, col), QItemSelectionModel.SelectionFlag.Select)

    def test_ctrl_c_copies_only_selected_rectangle(self):
        self.select((0, 0), (0, 1), (1, 0), (1, 1))
        QTest.keyClick(self.table, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.app.clipboard().text(), 'Steel\t200000\nTitanium\t110')

    def test_sparse_selection_keeps_blank_cells(self):
        self.select((0, 0), (1, 1))
        copy_table(self.table)
        self.assertEqual(self.app.clipboard().text(), 'Steel\t\n\t110')

    def test_copy_all_headers_visual_order_and_special_characters(self):
        self.table.horizontalHeader().moveSection(2, 0)
        self.table.hideColumn(1)
        copy_table(self.table, all_rows=True)
        data = list(csv.reader(io.StringIO(self.app.clipboard().text()), delimiter='\t'))
        self.assertEqual(data, [['Unit', 'Name'], ['MPa', 'Steel'], ['GPa', 'Titanium'], ['', 'A\tB']])
        self.table.showColumn(1)
        copy_table(self.table, all_rows=True)
        data = list(csv.reader(io.StringIO(self.app.clipboard().text()), delimiter='\t'))
        self.assertEqual(data[-1], ['', 'A\tB', 'multi\nline'])

    def test_cell_editor_keeps_native_text_copy(self):
        self.table.editItem(self.table.item(0, 0))
        self.app.processEvents()
        editor = self.table.findChild(QLineEdit)
        self.assertIsNotNone(editor)
        editor.setSelection(1, 3)
        QTest.keyClick(editor, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.app.clipboard().text(), 'tee')

    def test_dynamic_label_can_select_and_copy_text(self):
        label = QLabel('Results: 250 MPa')
        label.show()
        self.app.processEvents()
        self.assertTrue(label.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse)
        self.assertTrue(label.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByKeyboard)
        label.setSelection(9, 3)
        QTest.keyClick(label, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.app.clipboard().text(), '250')
        label.close()

    def test_select_all_and_empty_selection(self):
        self.app.clipboard().setText('keep')
        copy_table(self.table)
        self.assertEqual(self.app.clipboard().text(), 'keep')
        QTest.keyClick(self.table, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(self.table.selectedIndexes()), 9)


if __name__ == '__main__':
    unittest.main()
