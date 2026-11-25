"""Navigation and saved-plan controls for the DoE workspace."""
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QButtonGroup, QPushButton, QCheckBox, QListWidget, QTabWidget, QSizePolicy,
)

from model import MIN_MESH_INPUT_MM
from studio_style import ActionFlowLayout, CompactComboBox, CONTROL_HEIGHT, NumericLineEdit


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
        tabs.currentChanged.connect(lambda index: group.button(index).setChecked(True))
        return group


class SimpleDoEPlanner(QWidget):
    def __init__(self, studio, factor_specs):
        super().__init__()
        s = studio
        self._group_factors = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.navigation = DoENavigation()
        layout.addWidget(self.navigation)
        s.open_plan_btn = QPushButton('Open')
        s.save_plan_btn = QPushButton('Save')
        s.open_plan_btn.clicked.connect(s.open_plan_dialog)
        s.save_plan_btn.clicked.connect(s.save_plan_dialog)
        self.plan_footer = QWidget()
        plan_layout = QHBoxLayout(self.plan_footer)
        plan_layout.setContentsMargins(0, 0, 0, 0)
        plan_layout.setSpacing(4)
        s.plan_file_label = QLabel('Untitled plan')
        s.plan_file_label.setObjectName('mutedHintLabel')
        s.plan_file_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        plan_layout.addWidget(s.plan_file_label, 1)
        for button in (s.open_plan_btn, s.save_plan_btn):
            button.setFixedSize(64, CONTROL_HEIGHT)
            button.setAutoDefault(False)
            plan_layout.addWidget(button)
        self.tabs = s.settings_tabs = QTabWidget()
        self.tabs.tabBar().hide()
        layout.addWidget(self.tabs)
        # Older plans still carry a draft, factor values and sampling options.
        # Keep these available to the existing plan and matrix actions.
        self.saved_draft = base = replace(s._settings_provider(), imported_geometry_path='',
                                          imported_geometry_name='', test_profile='custom',
                                          specimen_architecture='porous_all')
        saved_plan_controls = QWidget(self)
        saved_plan_controls.hide()
        s.design_list = QListWidget(saved_plan_controls)
        s.design_list.currentRowChanged.connect(s._select_design)
        s.design_list.itemChanged.connect(s._refresh_plan)
        s.design_value_controls = {}
        for field, low, high in (('size', 1, 100), ('cell_size', .1, 100), ('solid_fraction', 1, 99)):
            editor = NumericLineEdit(float(getattr(base, field)), low, high)
            editor.setParent(saved_plan_controls)
            editor.valueChanged.connect(s._edit_design)
            s.design_value_controls[field] = editor
        s.factor_controls = {}
        for spec in factor_specs:
            s._add_factor_controls(saved_plan_controls, spec)
        s.sampling_combo = CompactComboBox(saved_plan_controls)
        s.sampling_combo.addItem('Full factorial grid', 'full_factorial')
        s.sampling_combo.addItem('Latin hypercube (LHS)', 'lhs')
        s.sampling_combo.currentIndexChanged.connect(s._refresh_plan)
        s.lhs_count_spin = NumericLineEdit(15, 2, 500)
        s.sampling_seed = NumericLineEdit(42, 1, 999999)
        for control in (s.lhs_count_spin, s.sampling_seed):
            control.setParent(saved_plan_controls)
            control.valueChanged.connect(s._refresh_plan)

        advanced = QWidget()
        advanced_layout = QFormLayout(advanced)
        advanced_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        for field, label in (('mesh_resolution', 'Global mesh size (mm)'), ('local_mesh_size', 'Local mesh size (mm)')):
            editor = NumericLineEdit(float(getattr(base, field)), MIN_MESH_INPUT_MM, 100)
            s.design_value_controls[field] = editor
            editor.valueChanged.connect(s._edit_design)
            advanced_layout.addRow(label, editor)
        s.design_mesh_type = CompactComboBox()
        s.design_mesh_type.addItems(['HEX8', 'HEX20', 'TET4', 'TET10'])
        s.design_mesh_type.currentTextChanged.connect(s._edit_design)
        s.design_mesh_strategy = CompactComboBox()
        for strategy in ('auto', 'netgen', 'periodic'):
            s.design_mesh_strategy.addItem(strategy, strategy)
        s.design_mesh_strategy.currentIndexChanged.connect(s._edit_design)
        advanced_layout.addRow('Element type', s.design_mesh_type)
        advanced_layout.addRow('Tetra mesher', s.design_mesh_strategy)
        s.common_strain_check = QCheckBox('Common final strain (%)')
        s.common_strain_check.setChecked(True)
        s.common_strain_spin = NumericLineEdit(10.0, .1, 95)
        s.common_strain_check.toggled.connect(s.common_strain_spin.setEnabled)
        advanced_layout.addRow(s.common_strain_check, s.common_strain_spin)
        s.preview_factor = CompactComboBox()
        s.preview_bounds_btn = QPushButton('Preview bounds')
        s.preview_bounds_btn.clicked.connect(s._preview_bounds)
        advanced_layout.addRow(s.preview_factor, s.preview_bounds_btn)
        s.library_btn = QPushButton('Geometry library')
        s.library_btn.clicked.connect(s._open_geometry_library)
        advanced_layout.addRow(s.library_btn)
        self.tabs.addTab(advanced, 'Mesh')
        ml = QWidget()
        ml_layout = QFormLayout(ml)
        ml_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        s.ml_count_spin = NumericLineEdit(100, 2, 5000)
        s.ml_seed_spin = NumericLineEdit(42, 1, 999999)
        ml_layout.addRow('LHS samples per geometry', s.ml_count_spin)
        ml_layout.addRow('Seed', s.ml_seed_spin)
        s.ml_plan_label = QLabel()
        s.ml_plan_label.setWordWrap(True)
        ml_layout.addRow(s.ml_plan_label)
        s.generate_ml_btn = QPushButton('Generate ML matrix')
        s.generate_ml_btn.clicked.connect(s.generate_ml_table)
        s.ml_count_spin.valueChanged.connect(s._refresh_plan)
        ml_layout.addRow(s.generate_ml_btn)
        self.tabs.addTab(ml, 'DeepLearning')
        s._size_action_group(s.generate_ml_btn, s.preview_bounds_btn)

        # Compatibility with existing model-transfer actions.
        # The host connects its validated current-model action to this button.
        s.use_model_btn = QPushButton(self)
        s.use_model_btn.hide()
        for button in self.findChildren(QPushButton):
            button.setDefault(False)
            button.setAutoDefault(False)
