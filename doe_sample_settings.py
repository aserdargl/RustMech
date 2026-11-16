"""Sample naming and explicit combination matrices inside the DoE workspace."""
from PyQt6.QtCore import QByteArray, QEvent, QRect, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget, QTabWidget,
    QSplitter, QStyledItemDelegate, QStyle, QStyleOptionViewItem,
)
from studio_style import CONTROL_HEIGHT, CompactComboBox
from doe_sample_rules import load_rules, sample_name, save_rule
from doe_sample_inputs import sample_fields, display_value


class _NamingCheckDelegate(QStyledItemDelegate):
    """Center the indicator without the standard item text spacing."""

    def paint(self, painter, option, index):
        check_option = QStyleOptionViewItem(option)
        self.initStyleOption(check_option, index)
        background = QStyleOptionViewItem(check_option)
        background.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        style = option.widget.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, background, painter, option.widget)
        width = style.pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth, check_option, option.widget)
        height = style.pixelMetric(QStyle.PixelMetric.PM_IndicatorHeight, check_option, option.widget)
        check_option.rect = QRect(option.rect.x() + (option.rect.width() - width) // 2,
                                  option.rect.y() + (option.rect.height() - height) // 2, width, height)
        check_option.state &= ~(QStyle.StateFlag.State_On | QStyle.StateFlag.State_Off)
        check_option.state |= (QStyle.StateFlag.State_On if index.data(Qt.ItemDataRole.CheckStateRole) in (Qt.CheckState.Checked, Qt.CheckState.Checked.value)
                               else QStyle.StateFlag.State_Off)
        style.drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck,
                            check_option, painter, option.widget)

    def editorEvent(self, event, model, option, index):
        if not index.flags() & Qt.ItemFlag.ItemIsEnabled:
            return False
        if event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() != Qt.MouseButton.LeftButton or not option.rect.contains(event.position().toPoint()):
                return False
        elif event.type() == QEvent.Type.KeyPress:
            if event.key() not in (Qt.Key.Key_Space, Qt.Key.Key_Select):
                return False
        else:
            return False
        checked = index.data(Qt.ItemDataRole.CheckStateRole) in (Qt.CheckState.Checked, Qt.CheckState.Checked.value)
        return model.setData(index, Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked,
                             Qt.ItemDataRole.CheckStateRole)


class SampleSettingsPage(QWidget):
    def __init__(self, owner):
        from PyQt6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem, QLayout
        from doe_sample_blocks import NamingBlocks
        from doe_sample_style import compact_sample_widgets, sample_section
        super().__init__(owner)
        self.setObjectName("SampleNamingDialog")
        self.studio = owner
        self.context = "doe"
        self.sources = sample_fields(owner)
        self.fields = [field[:3] for field in self.sources]
        self.values = {key: value for key, _, value in self.fields}
        self.field_labels = {key: label for key, label, _ in self.fields}
        tr = False
        self.tr_mode = tr
        self.setWindowTitle(("Sample settings - ") + self.context)
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(4, 4, 4, 4)
        root_layout.setSpacing(6)
        toolbar = QHBoxLayout()
        hint = QLabel('Choose naming parameters, then build combinations from fixed and variable values.')
        hint.setObjectName('mutedHintLabel')
        hint.setWordWrap(True)
        toolbar.addWidget(hint, 1)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("SampleSettingsTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setExpanding(False)
        self.naming_page = QWidget()
        self.batch_page = QWidget()
        self.tabs.addTab(self.naming_page, "Automatic naming")
        self.tabs.addTab(self.batch_page, "Combination Matrix")
        self.load_button = QPushButton("Load selected design")
        self.load_button.clicked.connect(lambda: self.batch.load_selected())
        toolbar.addWidget(self.load_button)
        root_layout.addLayout(toolbar)
        root_layout.addWidget(self.tabs, 1)
        self.batch = None
        outer = QVBoxLayout(self.naming_page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QWidget()
        body.setObjectName("NamingBody")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        layout = QVBoxLayout(body)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("doeSampleNamingColumnsV2")
        self.splitter.setHandleWidth(3)
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter, 1)
        parameter_panel, layout = sample_section('1. Add parameters')
        self.splitter.addWidget(parameter_panel)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search parameters...")
        search_row = QHBoxLayout()
        self.filter_column = CompactComboBox()
        for label, column in (("All columns", -1),
                              ("Parameter", 0), ("Name", 1),
                              ("Value", 2)):
            self.filter_column.addItem(label, column)
        self.filter_column.setMaximumWidth(120)
        search_row.addWidget(self.filter_column)
        search_row.addWidget(self.search, 1)
        layout.addLayout(search_row)
        self.available = QTableWidget(len(self.fields), 5)
        self.available.setObjectName("NamingAvailable")
        self.available.setHorizontalHeaderLabels(["Parameter",
                                                  "Name", "Value",
                                                  "", ""])
        self.available.horizontalHeaderItem(3).setToolTip("Use")
        self.available.setItemDelegateForColumn(3, _NamingCheckDelegate(self.available))
        self.available.setAlternatingRowColors(True)
        self.available.verticalHeader().hide()
        self.available.verticalHeader().setDefaultSectionSize(24)
        self.available.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.available.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.available.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.moveSection(3, 0)
        check_width = self.available.style().pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth) + 4
        header.setMinimumSectionSize(4)
        header.resizeSection(3, check_width)
        header.setStretchLastSection(False)
        self._column_state_path = "naming_layout"
        layout_state = load_rules(self._column_state_path).get(self.context, {})
        widths = layout_state.get("widths", [180, 160, 90])
        if not isinstance(widths, list) or len(widths) != 3:
            widths = [180, 160, 90]
        for column, width in enumerate(widths):
            self.available.setColumnWidth(column, max(40, min(2000, width)) if isinstance(width, int) else 160)
        self.available.setMinimumHeight(100)
        for row, (key, label, value) in enumerate(self.fields):
            item = QTableWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.available.setItem(row, 0, item)
            self.available.setItem(row, 1, QTableWidgetItem(label))
            item = QTableWidgetItem(str(display_value(key, value)))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.available.setItem(row, 2, item)
            for column in range(3):
                self.available.item(row, column).setToolTip(self.available.item(row, column).text())
            check = QTableWidgetItem("")
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Unchecked)
            check.setToolTip("Include in name")
            self.available.setItem(row, 3, check)
            filler = QTableWidgetItem("")
            filler.setFlags(Qt.ItemFlag.NoItemFlags)
            self.available.setItem(row, 4, filler)
        layout.addWidget(self.available, 3)
        self.available_count = QLabel()
        self.available_count.setObjectName("NamingHint")
        self.available_count.hide()
        layout.addWidget(self.available_count)
        self.add_button = QPushButton("+  Add to sequence")
        self.add_button.setObjectName("NamingPrimary")
        self.add_button.clicked.connect(self.add_selected)
        search_row.addWidget(self.add_button)
        sequence_panel, layout = sample_section('2. Naming sequence')
        self.splitter.addWidget(sequence_panel)
        sequence_header = QHBoxLayout()
        self.sequence_count = QLabel()
        self.sequence_count.setObjectName("NamingHint")
        sequence_header.addWidget(self.sequence_count)
        sequence_header.addStretch()
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_selected)
        sequence_header.addWidget(self.remove_button)
        layout.addLayout(sequence_header)
        self.sequence = NamingBlocks()
        self.sequence.setToolTip("Drag blocks to change their order.")
        layout.addWidget(self.sequence, 2)
        self.block_name = QLineEdit()
        self.block_name.setMinimumWidth(70)
        self.block_name.setPlaceholderText("Block name")
        self.block_name.setToolTip("Edit the selected block name")
        sequence_header.insertWidget(1, self.block_name, 1)
        rule = load_rules().get(self.context, {})
        options = QHBoxLayout()
        self.separator = CompactComboBox()
        self.separator.setFixedWidth(70)
        for label, value in (("-", "-"), ("_", "_"), ("None", "")):
            self.separator.addItem(label, value)
        self.separator.setCurrentIndex(max(0, self.separator.findData(rule.get("separator", "-"))))
        options.addWidget(QLabel("Separator"))
        options.addWidget(self.separator)
        self.decimal = CompactComboBox()
        self.decimal.setFixedWidth(70)
        for label, value in ((".", "."), ("_", "_"), ("Remove", "")):
            self.decimal.addItem(label, value)
        self.decimal.setCurrentIndex(max(0, self.decimal.findData(rule.get("decimal", "."))))
        options.addWidget(QLabel("Decimal"))
        options.addWidget(self.decimal)
        options.addStretch()
        self.labels = QCheckBox("Include names")
        self.labels.setChecked(rule.get("labels", False))
        options.addWidget(self.labels)
        layout.addLayout(options)
        self.preview = QLineEdit()
        self.preview.setObjectName("NamingPreview")
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("Add parameters above")
        footer_layout = layout
        footer_layout.addSpacing(6)
        self._heading(footer_layout, "Filename preview")
        footer_layout.addWidget(self.preview)
        buttons = QWidget(self)
        button_layout = QHBoxLayout(buttons)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.addStretch()
        save_button = QPushButton("Save", buttons)
        save_button.setObjectName("NamingPrimary")
        save_button.setProperty('primary', True)
        save_button.setProperty('sampleActionGroup', True)
        save_button.setFixedSize(65, CONTROL_HEIGHT)
        save_button.setAutoDefault(False)
        save_button.setDefault(False)
        save_button.clicked.connect(self.save)
        button_layout.addWidget(save_button)
        self.save_button = save_button
        self.buttons = buttons
        root_layout.addWidget(buttons)
        self.splitter.setSizes([560, 380])
        if layout_state.get('layout') == 'columns' and isinstance(layout_state.get("splitter"), str):
            self.splitter.restoreState(QByteArray.fromBase64(layout_state["splitter"].encode("ascii")))
        self.search.textChanged.connect(self.filter_available)
        self.filter_column.currentIndexChanged.connect(self.refresh_available)
        self.available.itemDoubleClicked.connect(lambda item: self.add_selected() if item.column() not in (1, 3) else None)
        self.available.itemSelectionChanged.connect(self.refresh_available)
        self.available.itemChanged.connect(self.change_available)
        self.sequence.currentRowChanged.connect(self.select_block)
        self.sequence.orderChanged.connect(self.update_preview)
        self.block_name.textEdited.connect(self.rename_block)
        self.separator.currentIndexChanged.connect(self.update_preview)
        self.decimal.currentIndexChanged.connect(self.update_preview)
        self.labels.toggled.connect(self.update_preview)
        for field in rule.get("fields", []):
            if field["key"] in self.values:
                self.add_field(field["key"], field.get("label"))
        self.update_preview()
        self.tabs.currentChanged.connect(self._tab_changed)
        self._tab_changed(1)
        compact_sample_widgets(self)

    def save_preferences(self):
        save_rule(self.context, self.rule())
        save_rule(self.context, {"widths": [self.available.columnWidth(column) for column in range(3)],
                                "layout": "columns",
                                "splitter": bytes(self.splitter.saveState().toBase64()).decode('ascii')},
                  self._column_state_path)
        if self.batch is not None:
            self.batch.save_preferences()

    def hideEvent(self, event):
        self.save_preferences()
        super().hideEvent(event)

    def open_batch(self):
        self.tabs.setCurrentIndex(1)

    def _tab_changed(self, index):
        if index != 1:
            return
        if self.batch is None:
            from doe_sample_matrix import SampleCombinationPage
            self.batch = SampleCombinationPage(self.studio, self)
            layout = QVBoxLayout(self.batch_page)
            layout.setContentsMargins(0, 0, 0, 0)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(self.batch)
            layout.addWidget(scroll)
        self.batch.invalidate()

    @staticmethod
    def _heading(layout, text, object_name="NamingSection"):
        label = QLabel(text)
        label.setObjectName(object_name)
        layout.addWidget(label)

    def add_selected(self):
        row = self.available.currentRow()
        if row >= 0:
            self.add_field(self.available.item(row, 0).data(Qt.ItemDataRole.UserRole))

    def change_available(self, item):
        key = self.available.item(item.row(), 0).data(Qt.ItemDataRole.UserRole)
        if item.column() == 3:
            if item.checkState() == Qt.CheckState.Checked:
                self.add_field(key)
            else:
                for row in range(self.sequence.count()):
                    if self.sequence.item(row).data(Qt.ItemDataRole.UserRole) == key:
                        self.sequence.takeItem(row)
                        break
                self.update_preview()
        elif item.column() == 1:
            for row in range(self.sequence.count()):
                block = self.sequence.item(row)
                if block.data(Qt.ItemDataRole.UserRole) == key:
                    block.setText(item.text())
                    break
            self.update_preview()
            self.select_block()

    def add_field(self, key, label=None):
        from PyQt6.QtWidgets import QListWidgetItem
        if key not in self.values or key in {field["key"] for field in self.rule()["fields"]}:
            return
        if label is None:
            label = next(self.available.item(row, 1).text() for row in range(self.available.rowCount())
                         if self.available.item(row, 0).data(Qt.ItemDataRole.UserRole) == key)
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setData(Qt.ItemDataRole.UserRole + 1, str(self.values[key]))
        item.setToolTip(f"{self.field_labels[key]}: {self.values[key]}")
        self.sequence.addItem(item)
        self.sequence.setCurrentItem(item)
        self.update_preview()

    def remove_selected(self):
        row = self.sequence.currentRow()
        if row >= 0:
            self.sequence.takeItem(row)
            self.update_preview()

    def select_block(self, *args):
        item = self.sequence.currentItem()
        self.block_name.setEnabled(item is not None)
        self.block_name.setText(item.text() if item else "")
        self.remove_button.setEnabled(item is not None)

    def rename_block(self, text):
        item = self.sequence.currentItem()
        if item:
            item.setText(text)
            self.update_preview()

    def filter_available(self, text):
        self.refresh_available()

    def refresh_available(self):
        used = {self.sequence.item(row).data(Qt.ItemDataRole.UserRole): self.sequence.item(row).text()
                for row in range(self.sequence.count())}
        search = self.search.text().casefold()
        count = 0
        self.available.blockSignals(True)
        for row in range(self.available.rowCount()):
            item = self.available.item(row, 0)
            key = item.data(Qt.ItemDataRole.UserRole)
            self.available.item(row, 3).setCheckState(Qt.CheckState.Checked if key in used else Qt.CheckState.Unchecked)
            if key in used:
                self.available.item(row, 1).setText(used[key])
            selected_column = self.filter_column.currentData()
            columns = range(3) if selected_column == -1 else [selected_column]
            hidden = search not in " ".join(self.available.item(row, column).text() for column in columns).casefold()
            self.available.setRowHidden(row, hidden)
            count += not hidden
        self.available.blockSignals(False)
        self.available_count.setText(f"{count} " + ("parametre" if self.tr_mode else "parameters"))
        row = self.available.currentRow()
        self.add_button.setEnabled(row >= 0 and not self.available.isRowHidden(row)
                                   and self.available.item(row, 0).data(Qt.ItemDataRole.UserRole) not in used)

    def rule(self):
        return {"fields": [{"key": self.sequence.item(row).data(Qt.ItemDataRole.UserRole),
                            "label": self.sequence.item(row).text()} for row in range(self.sequence.count())],
                "separator": self.separator.currentData(), "decimal": self.decimal.currentData(),
                "labels": self.labels.isChecked()}

    def update_preview(self, *args):
        self.preview.setText(sample_name(self.rule(), self.values))
        self.sequence_count.setText(f"({self.sequence.count()})")
        self.sequence.viewport().update()
        self.refresh_available()
        self.remove_button.setEnabled(self.sequence.currentItem() is not None)

    def apply_names(self, rows):
        if not self.rule()['fields']:
            return
        from dataclasses import asdict
        from doe_sample_rules import unique_name
        used = set()
        for row in rows:
            values = asdict(row.to_settings(self.studio._settings_provider()))
            row.exp_id = unique_name(sample_name(self.rule(), values) or row.exp_id, used)
            used.add(row.exp_id)

    def state(self):
        return {"rule": self.rule(), "combinations": self.batch.state()}

    def restore_state(self, state):
        if not state:
            return
        rule = state.get("rule", {})
        self.sequence.clear()
        for field in rule.get("fields", []):
            self.add_field(field["key"], field.get("label"))
        self.separator.setCurrentIndex(max(0, self.separator.findData(rule.get("separator", "-"))))
        self.decimal.setCurrentIndex(max(0, self.decimal.findData(rule.get("decimal", "."))))
        self.labels.setChecked(rule.get("labels", False))
        self.batch.restore_state(state.get("combinations", {}))
        self.update_preview()

    def save(self):
        self.save_preferences()
