"""Separate fixed properties from variable rows with one value per cell."""

from PySide6.QtCore import QByteArray, QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QPushButton, QStyledItemDelegate,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from studio_style import CompactComboBox
from doe_sample_inputs import display_value, values_for_widget
from doe_sample_filter import SampleTableFilter
from doe_sample_style import sample_section, compact_sample_widgets, equal_sample_actions


class SampleChoiceDelegate(QStyledItemDelegate):
    def __init__(self, table, sources, variable=False):
        super().__init__(table)
        self.table, self.sources, self.variable = table, sources, variable

    def createEditor(self, parent, option, index):
        key = index.siblingAtColumn(0).data(Qt.ItemDataRole.UserRole)
        source = self.sources.get(key, (None, None, None, None))[3]
        if isinstance(source, QComboBox):
            editor = CompactComboBox(parent)
            if self.variable:
                editor.addItem("")
            for row in range(source.count()):
                editor.addItem(source.itemText(row), source.itemData(row))
            editor.setFixedHeight(22)
            editor.activated.connect(lambda *args: self.commitData.emit(editor))
            return editor
        return super().createEditor(parent, option, index)

    def setEditorData(self, editor, index):
        if isinstance(editor, QComboBox):
            value = str(index.data(Qt.ItemDataRole.EditRole) or "")
            selected = editor.findData(value)
            editor.setCurrentIndex(selected if selected >= 0 else editor.findText(value))
        else:
            super().setEditorData(editor, index)

    def displayText(self, value, locale):
        from ui_language import translate
        return translate(super().displayText(value, locale))

    def setModelData(self, editor, model, index):
        if isinstance(editor, QComboBox):
            value = editor.currentData() if editor.currentData() is not None else editor.currentText()
            model.setData(index, value, Qt.ItemDataRole.EditRole)
        else:
            super().setModelData(editor, model, index)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect.adjusted(1, 1, -1, -1))


class SampleBatchParameters(QWidget):
    changed = Signal()

    def __init__(self, fields, settings, turkish=False, parent=None):
        super().__init__(parent)
        self.fields = fields
        self.turkish = turkish
        self._search = ""
        self._restoring = True
        self.value_columns = max(1, int(settings.get("value_columns", 3)))
        self._by_key = {field[0]: field for field in fields}
        self.added_keys = set(settings.get("added", settings.get("axes", {})))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("doeSampleParametersColumnsV3")
        self.splitter.setHandleWidth(3)
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter)
        available_panel, available_layout, available_header = self._panel("1  Add parameters")
        self.available_filter = SampleTableFilter(["Parameter", "Source value"])
        available_header.addWidget(self.available_filter, 1)
        self.available = self._table(len(fields), 3)
        self.available.setHorizontalHeaderLabels(["Parameter", "Source value", "Add"])
        self.available.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.available.setColumnWidth(0, 135)
        self.available.setColumnWidth(1, 95)
        self.available.setColumnWidth(2, 40)
        for row, (key, label, value, _) in enumerate(fields):
            self.available.setItem(row, 0, self._label_item(key, label))
            item = self._label_item(key, str(display_value(key, value)))
            item.setData(Qt.ItemDataRole.UserRole, str(value))
            self.available.setItem(row, 1, item)
            check = self._label_item(key, "")
            check.setFlags(check.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Checked if key in self.added_keys else Qt.CheckState.Unchecked)
            self.available.setItem(row, 2, check)
        available_layout.addWidget(self.available)
        self.splitter.addWidget(available_panel)
        fixed_panel, fixed_layout, fixed_header = self._panel("2  Added parameters")
        self.to_variable = QPushButton(self.t("Degisken ekle", "Add Variable"))
        self.to_variable.clicked.connect(self.move_selected_to_variables)
        fixed_header.addWidget(self.to_variable)
        self.fixed = self._table(len(fields), 3)
        self.fixed.setItemDelegate(SampleChoiceDelegate(self.fixed, self._by_key))
        self.fixed.setHorizontalHeaderLabels([self.t("Parametre", "Parameter"), self.t("Deger", "Value"), "Vary"])
        self.fixed.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.fixed.setColumnWidth(2, 75)
        self.fixed.setColumnWidth(0, 135)
        self.fixed.setColumnWidth(1, 95)
        self.fixed_filter = SampleTableFilter([self.t("Parametre", "Parameter"), self.t("Deger", "Value")], turkish)
        self.fixed_filter.changed.connect(lambda: self.filter_rows())
        fixed_layout.insertWidget(0, self.fixed_filter)
        fixed_layout.addWidget(self.fixed)
        self.remove_parameter = QPushButton("Remove parameter")
        self.remove_parameter.clicked.connect(self.remove_selected_parameter)
        fixed_header.addWidget(self.remove_parameter)
        self.splitter.addWidget(fixed_panel)
        variable_panel, variable_layout, variable_header = self._panel("3  Variables")
        self.add_column_button = QPushButton(self.t("+Sutun", "+Column"))
        self.add_column_button.clicked.connect(self.add_value_column)
        self.remove_column_button = QPushButton(self.t("-Sutun", "-Column"))
        self.remove_column_button.clicked.connect(self.remove_value_column)
        self.to_fixed = QPushButton(self.t("Sabit", "Fixed"))
        self.to_fixed.clicked.connect(self.move_selected_to_fixed)
        for button in (self.add_column_button, self.remove_column_button, self.to_fixed):
            variable_header.addWidget(button)
        self.variables = self._table(0, self.value_columns + 2)
        self.variables.setItemDelegate(SampleChoiceDelegate(self.variables, self._by_key, variable=True))
        self.variables.setMinimumHeight(88)
        self.variables.setColumnWidth(0, 150)
        self._update_headers()
        self.variable_filter = SampleTableFilter(self._variable_headers()[:-1], turkish)
        self.variable_filter.changed.connect(lambda: self.filter_rows())
        variable_layout.insertWidget(0, self.variable_filter)
        variable_layout.addWidget(self.variables)
        self.splitter.addWidget(variable_panel)
        saved_axes = settings.get("axes", {})
        for row, (key, label, value, widget) in enumerate(fields):
            self.fixed.setItem(row, 0, self._label_item(key, label))
            previous = saved_axes.get(key, {})
            text = previous.get("fixed", previous.get("text", str(value)))
            if previous.get("vary") and "fixed" not in previous:
                text = str(value)
            self.fixed.setItem(row, 1, self._value_item(text, widget))
            if isinstance(widget, QComboBox):
                self.fixed.openPersistentEditor(self.fixed.item(row, 1))
            check = self._label_item(key, "")
            check.setFlags(check.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Unchecked)
            self.fixed.setItem(row, 2, check)
            if previous.get("vary"):
                values = previous.get("values")
                if values is None:
                    try:
                        values = [str(item) for item in values_for_widget(widget, previous.get("text", ""), True)]
                    except ValueError:
                        values = [previous.get("text", "")]
                self.add_variable(key, values)
        self.splitter.setSizes([300, 340, 400])
        if settings.get("parameter_splitter") and settings.get("parameter_layout") == "three_columns":
            self.splitter.restoreState(QByteArray.fromBase64(settings["parameter_splitter"].encode("ascii")))
        elif settings.get("parameter_splitter") and settings.get("parameter_layout") == "columns":
            legacy = QSplitter()
            legacy.addWidget(QWidget())
            legacy.addWidget(QWidget())
            if legacy.restoreState(QByteArray.fromBase64(settings["parameter_splitter"].encode("ascii"))):
                sizes = legacy.sizes()
                self.splitter.setSizes([max(1, sum(sizes) // 2), *sizes])
            legacy.deleteLater()
        for table, widths in ((self.available, settings.get("available_widths", [])[:2]),
                              (self.fixed, settings.get("widths", [])[:2]),
                              (self.variables, settings.get("variable_widths", []))):
            for column, width in enumerate(widths[:table.columnCount() - 1]):
                if isinstance(width, int):
                    table.setColumnWidth(column, max(60, min(2000, width)))
        self.fixed.itemChanged.connect(self._edited)
        self.available.itemChanged.connect(self._available_changed)
        self.available_filter.changed.connect(self.filter_rows)
        self.variables.itemChanged.connect(self._edited)
        self.fixed.itemSelectionChanged.connect(self._update_actions)
        self.variables.itemSelectionChanged.connect(self._update_actions)
        self._restoring = False
        self.filter_rows("")
        self._update_actions()

        compact_sample_widgets(self)
        equal_sample_actions(self.to_variable, self.to_fixed)
        equal_sample_actions(self.add_column_button, self.remove_column_button)
        for table_filter in (self.available_filter, self.fixed_filter, self.variable_filter):
            table_filter.column.setFixedHeight(self.to_variable.height())

    def t(self, turkish, english):
        return turkish if self.turkish else english

    def _panel(self, title):
        panel, layout = sample_section(title)
        header = QHBoxLayout()
        header.setSpacing(2)
        layout.addLayout(header)
        return panel, layout, header

    @staticmethod
    def _table(rows, columns):
        table = QTableWidget(rows, columns)
        table.setObjectName("NamingAvailable")
        table.setAlternatingRowColors(True)
        table.setMinimumHeight(80)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(24)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.horizontalHeader().setMinimumSectionSize(40)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setSectionResizeMode(columns - 1, QHeaderView.ResizeMode.Stretch)
        return table

    @staticmethod
    def _label_item(key, label):
        item = QTableWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        item.setToolTip(label)
        return item

    @staticmethod
    def _blank():
        item = QTableWidgetItem("")
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        return item

    @staticmethod
    def _value_item(text, widget):
        item = QTableWidgetItem(str(text))
        if hasattr(widget, "count") and hasattr(widget, "itemText"):
            item.setToolTip("; ".join(widget.itemText(index) for index in range(widget.count())))
        return item

    def _update_headers(self):
        self.variables.setHorizontalHeaderLabels(self._variable_headers())
        if hasattr(self, "variable_filter"):
            self.variable_filter.set_columns(self._variable_headers()[:-1])
        for column in range(1, self.value_columns + 1):
            if self.variables.columnWidth(column) == self.variables.horizontalHeader().defaultSectionSize():
                self.variables.setColumnWidth(column, 80)
        self.variables.horizontalHeader().setSectionResizeMode(self.value_columns + 1, QHeaderView.ResizeMode.Stretch)

    def _variable_headers(self):
        return [self.t("Parametre", "Parameter")] + [self.t(f"Deger {index}", f"Value {index}")
                for index in range(1, self.value_columns + 1)] + [""]

    def add_value_column(self):
        self.variables.insertColumn(self.value_columns + 1)
        self.value_columns += 1
        self.variables.horizontalHeader().setSectionResizeMode(self.value_columns, QHeaderView.ResizeMode.Interactive)
        for row in range(self.variables.rowCount()):
            self.variables.setItem(row, self.value_columns, QTableWidgetItem(""))
        self.variables.setColumnWidth(self.value_columns, 80)
        self._update_headers()
        self._open_variable_choices()
        self._edited()

    def remove_value_column(self):
        if self.value_columns <= 1:
            return
        self.variables.removeColumn(self.value_columns)
        self.value_columns -= 1
        self._update_headers()
        self._edited()

    def variable_row(self, key):
        return next((row for row in range(self.variables.rowCount())
                     if self.variables.item(row, 0).data(Qt.ItemDataRole.UserRole) == key), -1)

    def add_parameter(self, key):
        self.added_keys.add(key)
        row = next(row for row, field in enumerate(self.fields) if field[0] == key)
        self.available.item(row, 2).setCheckState(Qt.CheckState.Checked)
        self.filter_rows()

    def _available_changed(self, item):
        if item.column() != 2:
            return
        key = self.fields[item.row()][0]
        if item.checkState() == Qt.CheckState.Checked:
            self.added_keys.add(key)
        else:
            self.added_keys.discard(key)
            row = self.variable_row(key)
            if row >= 0:
                self.variables.removeRow(row)
            with QSignalBlocker(self.fixed):
                self.fixed.item(item.row(), 2).setCheckState(Qt.CheckState.Unchecked)
            self.fixed.item(item.row(), 1).setText(self.available.item(item.row(), 1).data(Qt.ItemDataRole.UserRole))
        self._edited()

    def remove_selected_parameter(self):
        row = self.fixed.currentRow()
        if row >= 0 and not self.fixed.isRowHidden(row):
            self.available.item(row, 2).setCheckState(Qt.CheckState.Unchecked)

    def add_variable(self, key, values=None):
        self.add_parameter(key)
        if self.variable_row(key) >= 0:
            return
        key, label, current, widget = self._by_key[key]
        source_row = next(row for row, field in enumerate(self.fields) if field[0] == key)
        with QSignalBlocker(self.fixed):
            self.fixed.item(source_row, 2).setCheckState(Qt.CheckState.Checked)
        values = list(values) if values is not None else [self.fixed.item(source_row, 1).text()]
        while len(values) > self.value_columns:
            self.add_value_column()
        row = self.variables.rowCount()
        self.variables.insertRow(row)
        self.variables.setItem(row, 0, self._label_item(key, label))
        for column in range(1, self.value_columns + 1):
            self.variables.setItem(row, column, self._value_item(values[column - 1] if column <= len(values) else "", widget))
        self.variables.setItem(row, self.value_columns + 1, self._blank())
        self._open_variable_choices()
        self.variables.setCurrentCell(row, 1)
        self.filter_rows(self._search)
        self._edited()

    def move_selected_to_variables(self):
        row = self.fixed.currentRow()
        if row >= 0 and not self.fixed.isRowHidden(row):
            self.add_variable(self.fields[row][0])

    def _open_variable_choices(self):
        for row in range(self.variables.rowCount()):
            key = self.variables.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if isinstance(self._by_key[key][3], QComboBox):
                for column in range(1, self.value_columns + 1):
                    item = self.variables.item(row, column)
                    if item is not None:
                        self.variables.openPersistentEditor(item)

    def move_selected_to_fixed(self):
        row = self.variables.currentRow()
        if row < 0:
            return
        key = self.variables.item(row, 0).data(Qt.ItemDataRole.UserRole)
        self.variables.removeRow(row)
        self.filter_rows(self._search)
        fixed_row = next(index for index, field in enumerate(self.fields) if field[0] == key)
        with QSignalBlocker(self.fixed):
            self.fixed.item(fixed_row, 2).setCheckState(Qt.CheckState.Unchecked)
        self.fixed.setCurrentCell(fixed_row, 1)
        self._edited()

    def values(self, key):
        row = self.variable_row(key)
        widget = self._by_key[key][3]
        if row < 0:
            fixed_row = next(index for index, field in enumerate(self.fields) if field[0] == key)
            return values_for_widget(widget, self.fixed.item(fixed_row, 1).text(), False)
        result = []
        for column in range(1, self.value_columns + 1):
            text = self.variables.item(row, column).text().strip()
            if not text:
                continue
            for value in values_for_widget(widget, text, False):
                if value not in result:
                    result.append(value)
        if not result:
            raise ValueError(self.t("En az bir deger hucresi doldurun.", "Fill at least one value cell."))
        return tuple(result)

    def filter_rows(self, text=None):
        if text is not None:
            self._search = text
        for row, (key, label, _, _) in enumerate(self.fields):
            item = self.fixed.item(row, 1)
            matches = self.fixed_filter.matches([label, item.text() if item else ""])
            self.fixed.setRowHidden(row, key not in self.added_keys or not matches)
            self.available.setRowHidden(row, not self.available_filter.matches(
                [label, self.available.item(row, 1).text()]))
        for row in range(self.variables.rowCount()):
            values = [self.variables.item(row, column).text() if self.variables.item(row, column) else ""
                      for column in range(self.value_columns + 1)]
            self.variables.setRowHidden(row, not self.variable_filter.matches(values))
        self._update_actions()

    def _update_actions(self):
        row = self.fixed.currentRow()
        self.to_variable.setEnabled(row >= 0 and not self.fixed.isRowHidden(row))
        self.remove_parameter.setEnabled(row >= 0 and not self.fixed.isRowHidden(row))
        self.to_fixed.setEnabled(self.variables.currentRow() >= 0)
        self.remove_column_button.setEnabled(self.value_columns > 1)

    def _edited(self, *args):
        if not self._restoring:
            if args and args[0].tableWidget() is self.fixed and args[0].column() == 2:
                item = args[0]
                key = self.fields[item.row()][0]
                if item.checkState() == Qt.CheckState.Checked:
                    self.add_variable(key)
                else:
                    row = self.variable_row(key)
                    if row >= 0:
                        self.variables.removeRow(row)
            self.filter_rows()
            self.changed.emit()

    def settings(self):
        axes = {}
        for row, (key, _, _, _) in enumerate(self.fields):
            variable_row = self.variable_row(key)
            axes[key] = {"fixed": self.fixed.item(row, 1).text(), "vary": variable_row >= 0,
                         "values": [self.variables.item(variable_row, column).text()
                                    for column in range(1, self.value_columns + 1)] if variable_row >= 0 else []}
        return {"axes": axes, "value_columns": self.value_columns,
                "parameter_layout": "three_columns",
                "added": [key for key, *_ in self.fields if key in self.added_keys],
                "available_widths": [self.available.columnWidth(column) for column in range(2)],
                "widths": [self.fixed.columnWidth(column) for column in range(2)],
                "variable_widths": [self.variables.columnWidth(column) for column in range(self.value_columns + 1)],
                "parameter_splitter": bytes(self.splitter.saveState().toBase64()).decode("ascii")}
