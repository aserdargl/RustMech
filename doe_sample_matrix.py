"""Expand explicit sample values and append validated rows to the existing DoE queue."""
from dataclasses import asdict, replace
from itertools import product
import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget, QHeaderView, QAbstractItemView, QSizePolicy,
)
from doe_sample_parameters import SampleBatchParameters
from doe_sample_filter import SampleTableFilter
from doe_sample_inputs import selected_settings, display_value
from doe_sample_rules import load_rules, save_rule, sample_name, unique_name
from doe_sample_style import compact_sample_widgets, sample_section, equal_sample_actions
from model import validate_geometry, settings_from_dict


class SampleCombinationPage(QWidget):
    def __init__(self, studio, naming):
        super().__init__()
        self.studio, self.naming = studio, naming
        state = load_rules('combinations').get('doe', {})
        self.base = settings_from_dict(state['base']) if state.get('base') else selected_settings(studio)
        self._rows = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setObjectName('doeSampleCombinationSplitter')
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(3)
        layout.addWidget(self.splitter, 1)
        self.parameter_editor = SampleBatchParameters(naming.sources, state.get('parameters', {}))
        self.parameter_editor.changed.connect(self.invalidate)
        self.splitter.addWidget(self.parameter_editor)
        panel, body = sample_section('4. Expanded combination matrix')
        self.matrix_filter = SampleTableFilter([])
        self.matrix_filter.changed.connect(self.filter_matrix)
        body.addWidget(self.matrix_filter)
        self.matrix = QTableWidget()
        self.matrix.setObjectName('doeSampleMatrix')
        self.matrix.setAlternatingRowColors(True)
        self.matrix.verticalHeader().hide()
        self.matrix.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.matrix.setMinimumHeight(100)
        self.matrix.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        body.addWidget(self.matrix)
        self.splitter.addWidget(panel)
        self.splitter.setSizes([360, 180])
        buttons = QHBoxLayout()
        self.build_button = QPushButton('Build matrix')
        self.build_button.clicked.connect(self.build_matrix)
        self.transfer_button = QPushButton('Add to ML matrix')
        self.transfer_button.setProperty('primary', True)
        self.transfer_button.setObjectName('primaryLaunchBtn')
        self.transfer_button.clicked.connect(self.transfer)
        self.transfer_button.setEnabled(False)
        for button in (self.build_button, self.transfer_button):
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.status = QLabel('Add parameters, set fixed values and enter one value per variable cell.')
        self.status.setObjectName('mutedHintLabel')
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.status)
        compact_sample_widgets(self)
        equal_sample_actions(self.build_button, self.transfer_button)
        equal_sample_actions(self.parameter_editor.to_variable, self.parameter_editor.to_fixed)
        equal_sample_actions(self.parameter_editor.add_column_button, self.parameter_editor.remove_column_button)
        self._fit_minimums()

    def showEvent(self, event):
        self._fit_minimums()
        super().showEvent(event)

    def _fit_minimums(self):
        editor = self.parameter_editor
        for index in range(editor.splitter.count()):
            panel = editor.splitter.widget(index)
            panel.setMinimumHeight(panel.layout().minimumSize().height())
        editor.setMinimumHeight(editor.layout().minimumSize().height())
        self.setMinimumHeight(self.layout().minimumSize().height())

    def _status(self, text):
        self.status.setText(text)
        self.status.setToolTip(text)

    def invalidate(self, *_):
        self._rows = None
        self.transfer_button.setEnabled(False)
        self.matrix.setRowCount(0)
        self._status('Settings changed; rebuild the matrix.')

    def load_selected(self):
        if self.studio._active_mode is not None:
            return
        self.base = selected_settings(self.studio)
        for row, (key, _, _, _) in enumerate(self.naming.sources):
            self.parameter_editor.fixed.item(row, 1).setText(str(getattr(self.base, key)))
            item = self.parameter_editor.available.item(row, 1)
            item.setText(str(display_value(key, getattr(self.base, key))))
            item.setData(Qt.ItemDataRole.UserRole, str(getattr(self.base, key)))
        self.naming.values = {key: getattr(self.base, key) for key, _, _, _ in self.naming.sources}
        for row, (key, _, _, _) in enumerate(self.naming.sources):
            self.naming.available.item(row, 2).setText(str(display_value(key, getattr(self.base, key))))
        self.naming.update_preview()
        self.invalidate()

    def build_matrix(self):
        from doe_studio import MAX_DOE_ROWS, generate_doe_rows
        self.invalidate()
        try:
            axes = []
            for key, label, _, _ in self.naming.sources:
                try:
                    axes.append((key, self.parameter_editor.values(key)))
                except ValueError as error:
                    raise ValueError(f'{label}: {error}') from error
            count = math.prod(len(values) for _, values in axes)
            if count > MAX_DOE_ROWS:
                raise ValueError(f'Too many combinations; maximum is {MAX_DOE_ROWS}.')
            rows = []
            existing = self.studio.rows
            used = {row.exp_id for row in existing}
            for combination in product(*(values for _, values in axes)):
                settings = replace(self.base, **dict(zip((key for key, _ in axes), combination)))
                from structure_library import ALL_STRUCTURES_BY_FAMILY, TPMS_MORPHOLOGIES
                if settings.structure_name not in ALL_STRUCTURES_BY_FAMILY.get(settings.structure_family, ()):
                    raise ValueError('Structure does not belong to the selected family.')
                if settings.tpms_morphology not in TPMS_MORPHOLOGIES:
                    raise ValueError('Unknown morphology.')
                errors = validate_geometry(settings, include_mesh=False)
                if errors:
                    raise ValueError(f'Combination {len(rows) + 1}: {errors[0]}')
                row = generate_doe_rows([settings], {}, 1, 42)[0]
                name = sample_name(self.naming.rule(), asdict(settings)) or f'EXP-{len(used) + 1:04d}'
                row.exp_id = unique_name(name, used)
                used.add(row.exp_id)
                rows.append(row)
            keys = list(dict.fromkeys([field['key'] for field in self.naming.rule()['fields']] +
                                      [key for key, values in axes if len(values) > 1]))
            self.matrix.setColumnCount(len(keys) + 1)
            labels = dict((key, label) for key, label, _, _ in self.naming.sources)
            headers = [labels[key] for key in keys] + ['Sample name']
            self.matrix.setHorizontalHeaderLabels(headers)
            self.matrix.setRowCount(len(rows))
            for index, row in enumerate(rows):
                values = asdict(row.to_settings(self.base))
                for column, value in enumerate([display_value(key, values[key]) for key in keys] + [row.exp_id]):
                    self.matrix.setItem(index, column, QTableWidgetItem(str(value)))
            self.matrix.horizontalHeader().setStretchLastSection(True)
            self.matrix_filter.set_columns(headers)
            self._rows = rows
            self.transfer_button.setEnabled(self.studio._active_mode is None)
            self._status(f'{len(rows)} combinations. Ready to add to the ML matrix.')
            self.filter_matrix()
            return True
        except (ValueError, TypeError, OverflowError) as error:
            self._status(str(error))
            return False

    def filter_matrix(self):
        for row in range(self.matrix.rowCount()):
            values = [self.matrix.item(row, column).text() for column in range(self.matrix.columnCount())]
            self.matrix.setRowHidden(row, not self.matrix_filter.matches(values))

    def transfer(self):
        from doe_studio import MAX_DOE_ROWS
        if self.studio._active_mode is not None or not self.build_matrix():
            return
        studio = self.studio
        existing = studio.rows
        if len(existing) + len(self._rows) > MAX_DOE_ROWS:
            self._status(f'Too many experiments; maximum is {MAX_DOE_ROWS}.')
            return
        studio._rows.extend(self._rows)
        studio._refresh_table_view()
        studio.settings_tabs.setCurrentWidget(studio.planner.learning_page)
        self.save_preferences()

    def restore_state(self, state):
        if not state:
            return
        self.base = settings_from_dict(state['base'])
        old = self.parameter_editor
        self.parameter_editor = SampleBatchParameters(self.naming.sources, state.get('parameters', {}))
        self.parameter_editor.changed.connect(self.invalidate)
        self.splitter.replaceWidget(0, self.parameter_editor)
        old.deleteLater()
        compact_sample_widgets(self.parameter_editor)
        self._fit_minimums()
        self.invalidate()

    def state(self):
        return {'base': asdict(self.base), 'parameters': self.parameter_editor.settings()}

    def save_preferences(self):
        save_rule('doe', self.state(), 'combinations')
