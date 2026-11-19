"""Compact, geometry-first planning surface for the DoE workspace."""
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout, QLabel,
    QButtonGroup, QPushButton, QCheckBox, QListWidget, QTreeWidget, QTreeWidgetItem,
    QTabWidget, QScrollArea, QSplitter, QSizePolicy,
)

from model import MIN_MESH_INPUT_MM, validate_geometry
from structure_library import STRUCTURE_FAMILIES
from studio_style import ActionFlowLayout, CompactComboBox, CONTROL_HEIGHT, NUMERIC_FIELD_WIDTH, SidebarGroupBox


SELECTED_LABEL_WIDTH = 132
BOUND_SOURCE_WIDTH = 100
LEVELS_WIDTH = 64


def geometry_group(settings):
    return (settings.structure_family, settings.structure_name,
            settings.tpms_morphology if settings.structure_family != 'lattice' else '',
            settings.imported_geometry_path, settings.test_profile)


class DoENavigation(QWidget):
    """Show tab selections as command buttons while retaining the tab model."""

    def __init__(self):
        super().__init__()
        self.setObjectName('doeNavigation')
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        ActionFlowLayout(self, equal_width=True)

    def add_button(self, button):
        button.setFixedHeight(CONTROL_HEIGHT)
        layout = self.layout()
        previous = layout.itemAt(layout.count() - 1)
        layout.addWidget(button)
        if previous is not None:
            QWidget.setTabOrder(previous.widget(), button)

    def add_tabs(self, tabs):
        group = QButtonGroup(self)
        for index in range(tabs.count()):
            button = QPushButton(tabs.tabText(index))
            button.setCheckable(True)
            group.addButton(button, index)
            self.add_button(button)
        group.button(tabs.currentIndex()).setChecked(True)
        group.idClicked.connect(tabs.setCurrentIndex)
        tabs.currentChanged.connect(lambda _: group.button(tabs.currentIndex()).setChecked(True))


class SimpleDoEPlanner(QWidget):
    def __init__(self, studio, factor_specs):
        super().__init__()
        self.studio = s = studio
        self._syncing = False
        self._tree_signature = None
        self._source_signature = None
        self._group_factors = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.navigation = DoENavigation()
        layout.addWidget(self.navigation)
        s.open_plan_btn = QPushButton('Open')
        s.save_plan_btn = QPushButton('Save')
        s.open_plan_btn.clicked.connect(s.open_plan_dialog)
        s.save_plan_btn.clicked.connect(s.save_plan_dialog)
        self.navigation.add_button(s.open_plan_btn)
        self.navigation.add_button(s.save_plan_btn)
        plan_status = QHBoxLayout()
        s.plan_file_label = QLabel('Untitled plan')
        s.plan_file_label.setObjectName('mutedHintLabel')
        s.plan_file_label.setMinimumWidth(140)
        s.plan_file_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        plan_status.addWidget(s.plan_file_label, 1)
        from ui_language import LanguageSelector, install_language
        self.language_selector = LanguageSelector(install_language(), self)
        plan_status.addWidget(self.language_selector)
        layout.addLayout(plan_status)
        self.tabs = s.settings_tabs = QTabWidget()
        self.tabs.tabBar().hide()
        layout.addWidget(self.tabs)
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(4, 4, 4, 4)
        hint = QLabel('Add geometry → select a saved design → choose variables and their limits.')
        hint.setObjectName('mutedHintLabel')
        page_layout.addWidget(hint)
        splitter = s.plan_splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName('doeGeometryPlanSplitterV2')
        splitter.setHandleWidth(3)
        splitter.setChildrenCollapsible(False)
        page_layout.addWidget(splitter, 1)
        splitter.setSizes([280, 300, 520])
        self.tabs.addTab(page, 'Design experiments')

        def section(title):
            group = SidebarGroupBox(title)
            group.setMinimumWidth(270)
            body = QVBoxLayout(group.content)
            body.setContentsMargins(6, 6, 6, 6)
            body.setSpacing(5)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setMinimumWidth(190)
            scroll.setWidget(group)
            splitter.addWidget(scroll)
            return body, scroll

        from doe_geometry import ActiveGeometryPanel
        base = replace(s._settings_provider(), imported_geometry_path='',
                       imported_geometry_name='', test_profile='custom', specimen_architecture='porous_all')
        add_layout, new_scroll = section('1. New geometry')
        self.geometry_panel = ActiveGeometryPanel(lambda: base, s._geometry_surface_builder,
                                                  self, embedded=True)
        self.geometry_panel.setObjectName('doeNewGeometryPanel')
        self.geometry_panel.setMinimumHeight(560)
        self.geometry_panel._sync_source()
        self.draft = self.geometry_panel.splitter.widget(1)
        self.family = s.structure_family_combo = self.geometry_panel.family
        self.structure = s.structure_type_combo = self.geometry_panel.structure
        self.morphology = self.geometry_panel.morphology
        self.values = self.geometry_panel.values
        s.add_structure_btn = self.geometry_panel.add_button
        self.apply_button = self.geometry_panel.apply_button
        self.draft_status = self.geometry_panel.status
        self.geometry_panel.geometry_added.connect(lambda settings: s.add_active_geometry(settings))
        s._size_action_group(s.add_structure_btn, self.apply_button)
        add_layout.addWidget(self.geometry_panel, 1)
        new_scroll.widget().setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        for control in (*self.values.values(), self.family, self.structure, self.morphology):
            signal = control.valueChanged if hasattr(control, 'valueChanged') else control.currentIndexChanged
            signal.connect(self.validate_draft)

        saved_layout, _ = section('2. Saved designs')
        self.tree = QTreeWidget()
        self.tree.setObjectName('doeGroupedDesigns')
        self.tree.setHeaderLabels(['Design · Size / Cell / Density'])
        self.tree.setMinimumHeight(110)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.tree.currentItemChanged.connect(self.select_item)
        self.tree.itemChanged.connect(self.check_item)
        saved_layout.addWidget(self.tree, 1)
        saved_layout.addWidget(s.design_count_badge)
        s.design_list = QListWidget(self)
        s.design_list.hide()  # Stable ordered model used by import/export and the queue.
        s.design_list.currentRowChanged.connect(s._select_design)
        s.design_list.itemChanged.connect(s._refresh_plan)
        s.remove_design_btn = QPushButton('Remove selected')
        s.remove_design_btn.clicked.connect(s._remove_design)
        s._size_action_group(s.remove_design_btn)
        saved_layout.addWidget(s.remove_design_btn, 0, Qt.AlignmentFlag.AlignLeft)

        selected_layout, s.range_scroll = section('3. Selected design / variables')
        s.range_scroll.widget().setMinimumWidth(440)
        self.selection_label = QLabel('Select a saved design to edit its dimensions.')
        self.selection_label.setWordWrap(True)
        selected_layout.addWidget(self.selection_label)
        dimensions_title = QLabel('Dimensions')
        dimensions_title.setObjectName('sectionSubheader')
        selected_layout.addWidget(dimensions_title)
        s.geometry_editor = QWidget()
        edit_form = QGridLayout(s.geometry_editor)
        edit_form.setContentsMargins(0, 0, 0, 0)
        edit_form.setHorizontalSpacing(6)
        edit_form.setVerticalSpacing(4)
        edit_form.setColumnMinimumWidth(0, SELECTED_LABEL_WIDTH)
        edit_form.setColumnStretch(2, 1)
        s.design_value_controls = {}
        for row, (field, label, low, high) in enumerate((
            ('size', 'Size (mm)', 1, 100), ('cell_size', 'Cell size (mm)', .1, 100),
            ('solid_fraction', 'Density (%)', 1, 99),
        )):
            spin = s._make_dspin(low, high, getattr(base, field), .1, 3)
            spin.setFixedSize(NUMERIC_FIELD_WIDTH, CONTROL_HEIGHT)
            s.design_value_controls[field] = spin
            spin.valueChanged.connect(s._edit_design)
            caption = QLabel(label)
            caption.setFixedWidth(SELECTED_LABEL_WIDTH)
            edit_form.addWidget(caption, row, 0, Qt.AlignmentFlag.AlignLeft)
            edit_form.addWidget(spin, row, 1, Qt.AlignmentFlag.AlignLeft)
        selected_layout.addWidget(s.geometry_editor)
        selected_layout.addSpacing(8)
        variables_title = QLabel('Experiment variables')
        variables_title.setObjectName('sectionSubheader')
        selected_layout.addWidget(variables_title)
        note = QLabel('Checked variables apply to this geometry group. Enter limits or copy them from a saved design.')
        note.setWordWrap(True)
        note.setObjectName('mutedHintLabel')
        selected_layout.addWidget(note)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        grid.setColumnMinimumWidth(0, SELECTED_LABEL_WIDTH)
        grid.setColumnMinimumWidth(1, BOUND_SOURCE_WIDTH)
        grid.setColumnMinimumWidth(2, BOUND_SOURCE_WIDTH)
        grid.setColumnMinimumWidth(3, LEVELS_WIDTH)
        grid.setColumnStretch(4, 1)
        for col, title in enumerate(('Vary', 'Min', 'Max', 'Levels')):
            caption = QLabel(title)
            caption.setFixedWidth(SELECTED_LABEL_WIDTH if col == 0 else
                                  LEVELS_WIDTH if col == 3 else BOUND_SOURCE_WIDTH)
            grid.addWidget(caption, 0, col, Qt.AlignmentFlag.AlignLeft)
        s.factor_controls = {}
        self.bound_sources = {}
        for row, spec in enumerate(factor_specs, 1):
            field = spec[0]
            spec = (field, {'size': 'Size (mm)', 'cell_size': 'Cell size (mm)', 'solid_fraction': 'Density (%)'}[field], *spec[2:])
            input_row = row * 3 - 2
            s._add_factor_row(grid, input_row, spec)
            for column, control in enumerate(s.factor_controls[field]):
                control.setFixedSize(SELECTED_LABEL_WIDTH if column == 0 else
                                     LEVELS_WIDTH if column == 3 else NUMERIC_FIELD_WIDTH,
                                     CONTROL_HEIGHT)
                grid.setAlignment(control, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            source_label = QLabel('Copy from design')
            source_label.setObjectName('mutedHintLabel')
            source_label.setFixedWidth(SELECTED_LABEL_WIDTH)
            grid.addWidget(source_label, input_row + 1, 0, Qt.AlignmentFlag.AlignLeft)
            for column, bound in enumerate(('Min', 'Max'), 1):
                combo = self.combo()
                combo.setFixedWidth(BOUND_SOURCE_WIDTH)
                combo.setAccessibleName(f'{spec[1]} {bound} source')
                combo.currentIndexChanged.connect(
                    lambda _, f=field, b=bound, c=combo: self.use_bound(f, b, c))
                self.bound_sources[field, bound] = combo
                grid.addWidget(combo, input_row + 1, column, Qt.AlignmentFlag.AlignLeft)
            grid.setRowMinimumHeight(input_row + 2, 8)
        selected_layout.addLayout(grid)
        selected_layout.addStretch()
        splitter.setSizes([280, 300, 520])
        splitter.setStretchFactor(2, 1)

        sampling = QHBoxLayout()
        sampling.addWidget(QLabel('Sampling'))
        s.sampling_combo = self.combo()
        s.sampling_combo.addItem('Full factorial grid', 'full_factorial')
        s.sampling_combo.addItem('Latin hypercube (LHS)', 'lhs')
        s.sampling_combo.currentIndexChanged.connect(s._refresh_plan)
        sampling.addWidget(s.sampling_combo)
        s.lhs_count_label = QLabel('Runs per design')
        s.lhs_count_spin = s._make_ispin(2, 500, 15)
        s.sampling_seed_label = QLabel('Seed')
        s.sampling_seed = s._make_ispin(1, 999999, 42)
        for control in (s.lhs_count_label, s.lhs_count_spin, s.sampling_seed_label, s.sampling_seed):
            sampling.addWidget(control)
        s.lhs_count_spin.valueChanged.connect(s._refresh_plan)
        sampling.addStretch()
        s.generate_btn = QPushButton('Generate experiments')
        s.generate_btn.setObjectName('primaryLaunchBtn')
        s.generate_btn.clicked.connect(s.generate_table)
        sampling.addWidget(s.generate_btn)
        page_layout.addLayout(sampling)
        s.plan_label = QLabel()
        s.plan_label.setWordWrap(True)
        page_layout.addWidget(s.plan_label)
        s.sampling_details = QLabel()
        s.sampling_details.setWordWrap(True)
        s.sampling_details.setObjectName('mutedHintLabel')
        page_layout.addWidget(s.sampling_details)

        advanced = QWidget()
        advanced_layout = QFormLayout(advanced)
        advanced_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        for field, label in (('mesh_resolution', 'Global mesh size (mm)'), ('local_mesh_size', 'Local mesh size (mm)')):
            spin = s._make_dspin(MIN_MESH_INPUT_MM, 100, getattr(base, field), .05, 3)
            s.design_value_controls[field] = spin
            spin.valueChanged.connect(s._edit_design)
            advanced_layout.addRow(label, spin)
        s.design_mesh_type = self.combo()
        s.design_mesh_type.addItems(['HEX8', 'HEX20', 'TET4', 'TET10'])
        s.design_mesh_type.currentTextChanged.connect(s._edit_design)
        s.design_mesh_strategy = self.combo()
        for strategy in ('auto', 'netgen', 'periodic'):
            s.design_mesh_strategy.addItem(strategy, strategy)
        s.design_mesh_strategy.currentIndexChanged.connect(s._edit_design)
        advanced_layout.addRow('Element type', s.design_mesh_type)
        advanced_layout.addRow('Tetra mesher', s.design_mesh_strategy)
        s.common_strain_check = QCheckBox('Common final strain (%)')
        s.common_strain_check.setChecked(True)
        s.common_strain_spin = s._make_dspin(.1, 95, 10, .5, 2)
        s.common_strain_check.toggled.connect(s.common_strain_spin.setEnabled)
        advanced_layout.addRow(s.common_strain_check, s.common_strain_spin)
        s.preview_factor = self.combo()
        s.preview_bounds_btn = QPushButton('Preview bounds')
        s.preview_bounds_btn.clicked.connect(s._preview_bounds)
        advanced_layout.addRow(s.preview_factor, s.preview_bounds_btn)
        s.library_btn = QPushButton('Geometry library')
        s.library_btn.clicked.connect(s._open_geometry_library)
        advanced_layout.addRow(s.library_btn)
        self.tabs.addTab(advanced, 'Mesh / analysis settings')
        ml = QWidget()
        ml_layout = QFormLayout(ml)
        ml_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        s.ml_count_spin = s._make_ispin(2, 5000, 100)
        s.ml_seed_spin = s._make_ispin(1, 999999, 42)
        ml_layout.addRow('LHS samples per geometry', s.ml_count_spin)
        ml_layout.addRow('Seed', s.ml_seed_spin)
        s.ml_plan_label = QLabel()
        s.ml_plan_label.setWordWrap(True)
        ml_layout.addRow(s.ml_plan_label)
        s.generate_ml_btn = QPushButton('Generate ML matrix')
        s.generate_ml_btn.clicked.connect(s.generate_ml_table)
        s.ml_count_spin.valueChanged.connect(s._refresh_plan)
        ml_layout.addRow(s.generate_ml_btn)
        self.tabs.addTab(ml, 'Machine learning')
        s._size_action_group(s.generate_btn, s.generate_ml_btn, s.preview_bounds_btn)

        # Compatibility with existing model-transfer actions.
        # The host connects its validated current-model action to this button.
        s.use_model_btn = QPushButton(self)
        s.use_model_btn.hide()
        s.common_size_spin = s._make_dspin(1, 100, base.size, .5, 3)
        s.individual_sizes_btn = QPushButton(self)
        s.individual_sizes_btn.setCheckable(True)
        s.individual_sizes_btn.setChecked(True)
        s.size_variable_btn = QPushButton(self)
        s.size_variable_btn.setCheckable(True)
        s.size_variable_btn.toggled.connect(s._toggle_size_variable)
        s.individual_size_label = QLabel(self)
        s.individual_size_spin = s._make_dspin(1, 100, base.size, .5, 3)
        for control in (s.common_size_spin, s.individual_size_spin):
            control.setParent(self)
            control.hide()
        for control in (s.individual_sizes_btn, s.size_variable_btn, s.individual_size_label):
            control.hide()
        self.validate_draft()
        for button in self.findChildren(QPushButton):
            button.setDefault(False)
            button.setAutoDefault(False)

    @staticmethod
    def combo():
        combo = CompactComboBox()
        combo.setFixedSize(150, CONTROL_HEIGHT)
        return combo

    def draft_settings(self):
        return self.geometry_panel.selected_settings()

    def validate_draft(self, *_):
        errors = validate_geometry(self.draft_settings(), include_mesh=False)
        idle = self.studio._active_mode is None
        self.studio.add_structure_btn.setEnabled(not errors and idle)
        self.apply_button.setEnabled(not errors and idle)

    def add_draft(self):
        if self.studio._active_mode is None:
            self.geometry_panel._add()

    def select_item(self, item, previous=None):
        if self._syncing or item is None:
            return
        index = item.data(0, Qt.ItemDataRole.UserRole)
        if index is None and item.childCount():
            index = item.child(0).data(0, Qt.ItemDataRole.UserRole)
        if index is not None:
            self.studio.design_list.setCurrentRow(index)

    def check_item(self, item, column):
        if self._syncing:
            return
        index = item.data(0, Qt.ItemDataRole.UserRole)
        if index is not None:
            self.studio.design_list.item(index).setCheckState(item.checkState(0))

    def use_bound(self, field, bound, combo):
        if self._syncing or combo.currentData() is None:
            return
        index = combo.currentData()
        control = self.studio.factor_controls[field][1 if bound == 'Min' else 2]
        control.setValue(getattr(self.studio._designs[index], field))
        # The chosen object copies a value; subsequent manual entry stays authoritative.
        combo.blockSignals(True)
        combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def sync(self):
        if self._syncing:
            return
        s = self.studio
        self._syncing = True
        try:
            signature = [(geometry_group(d), d.size, d.cell_size, d.solid_fraction,
                          s.design_list.item(i).checkState()) for i, d in enumerate(s._designs)]
            if signature != self._tree_signature:
                self._tree_signature = signature
                self.tree.clear()
                groups = {}
                self._tree_items = {}
                for i, design in enumerate(s._designs):
                    key = geometry_group(design)
                    if key not in groups:
                        caption = ' / '.join((STRUCTURE_FAMILIES.get(key[0], key[0]),
                                              key[1], key[2])).rstrip(' /')
                        groups[key] = QTreeWidgetItem(self.tree, [caption])
                        groups[key].setExpanded(True)
                    item = QTreeWidgetItem(groups[key], [
                        f'D{i+1:02d} · {design.size:g} mm / {design.cell_size:g} mm / {design.solid_fraction:g}%'])
                    item.setToolTip(0, item.text(0))
                    item.setData(0, Qt.ItemDataRole.UserRole, i)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(0, s.design_list.item(i).checkState())
                    self._tree_items[i] = item
            index = s._selected_design
            has_design = 0 <= index < len(s._designs)
            if has_design:
                design = s._designs[index]
                self.tree.setCurrentItem(self._tree_items[index])
                self.selection_label.setText(f'D{index+1:02d} · ' + ' / '.join(str(v) for v in geometry_group(design)[:3] if v))
            else:
                self.selection_label.setText('Add a geometry, then select it here to edit dimensions and variables.')
            members = [i for i, d in enumerate(s._designs)
                       if has_design and geometry_group(d) == geometry_group(s._designs[index])]
            source_signature = (index, tuple(signature[i][:4] for i in members))
            if source_signature != self._source_signature:
                self._source_signature = source_signature
                for (field, bound), combo in self.bound_sources.items():
                    combo.clear()
                    combo.addItem(f'{bound} from…', None)
                    for i in members:
                        combo.addItem(f'D{i+1:02d}: {getattr(s._designs[i], field):g}', i)
            idle = s._active_mode is None
            s.open_plan_btn.setEnabled(idle)
            s.save_plan_btn.setEnabled(idle)
            self.tree.setEnabled(idle)
            self.geometry_panel.setEnabled(idle)
            self.validate_draft()
            imported = has_design and bool(s._designs[index].imported_geometry_path)
            for field, (enabled, low, high, count) in s.factor_controls.items():
                allowed = has_design and idle and not imported and s._designs[index].test_profile == 'custom'
                for control in (enabled, low, high, count):
                    control.show()
                enabled.setEnabled(allowed)
                low.setEnabled(allowed and enabled.isChecked())
                high.setEnabled(allowed and enabled.isChecked())
                count.setEnabled(allowed and enabled.isChecked() and s.sampling_combo.currentData() != 'lhs')
                for bound in ('Min', 'Max'):
                    self.bound_sources[field, bound].setEnabled(allowed and enabled.isChecked())
                s.design_value_controls[field].setEnabled(has_design and idle and not imported
                                                         and s._designs[index].test_profile == 'custom')
            self.validate_draft()
        finally:
            self._syncing = False
