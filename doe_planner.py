"""Navigation and saved-plan controls for the DoE workspace."""
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout, QLabel,
    QButtonGroup, QPushButton, QCheckBox, QListWidget, QTabWidget, QSizePolicy,
    QScrollArea, QSplitter,
)

from studio_style import ActionFlowLayout, CONTROL_HEIGHT, NumericLineEdit, SidebarGroupBox


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
        # Preserve the legacy draft when reopening and saving older plans.
        self.saved_draft = replace(s._settings_provider(), imported_geometry_path='',
                                          imported_geometry_name='', test_profile='custom',
                                          specimen_architecture='porous_all')
        self._build_learning_page(s, factor_specs)
        for button in self.findChildren(QPushButton):
            button.setDefault(False)
            button.setAutoDefault(False)

    def _build_learning_page(self, s, factor_specs):
        self.learning_page = QWidget()
        layout = QVBoxLayout(self.learning_page)
        layout.setContentsMargins(2, 1, 2, 1)
        self.learning_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.learning_splitter.setObjectName('deepLearningSettingsAndMatrix')
        self.learning_splitter.setHandleWidth(7)
        self.learning_splitter.setChildrenCollapsible(False)
        layout.addWidget(self.learning_splitter)

        settings_panel = QWidget()
        settings_panel.setMinimumWidth(330)
        settings_layout = QVBoxLayout(settings_panel)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(2)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        settings_layout.addWidget(scroll, 1)
        settings = QWidget()
        scroll.setWidget(settings)
        sidebar = QVBoxLayout(settings)
        sidebar.setContentsMargins(2, 2, 2, 2)
        sidebar.setSpacing(3)

        self.designs_group = SidebarGroupBox('Analysis settings')
        sidebar.addWidget(self.designs_group)
        designs = QVBoxLayout(self.designs_group.content)
        designs.setContentsMargins(4, 2, 4, 4)
        designs.setSpacing(3)
        s.use_model_btn = QPushButton('Use current model')
        s.use_model_btn.setEnabled(False)
        designs.addWidget(s.use_model_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        s.design_list = QListWidget()
        s.design_list.setAccessibleName('Saved designs')
        s.design_list.setToolTip('Check the designs to include in the experiment matrix.')
        s.design_list.setFixedHeight(90)
        s.design_list.currentRowChanged.connect(s._select_design)
        s.design_list.itemChanged.connect(s._refresh_plan)
        designs.addWidget(s.design_list)

        self.variables_group = SidebarGroupBox('Variable settings')
        sidebar.addWidget(self.variables_group)
        variables = QGridLayout(self.variables_group.content)
        variables.setContentsMargins(4, 2, 4, 4)
        variables.setSpacing(3)
        for column, title in enumerate(('Fixed value', 'Min', 'Max'), 1):
            heading = QLabel(title)
            heading.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            variables.addWidget(heading, 0, column)
        variables.setColumnStretch(4, 1)
        s.design_value_controls = {}
        s.factor_controls = {}
        for row, spec in enumerate(factor_specs, 1):
            field, label, lower, upper, _, _ = spec
            s._add_factor_controls(self.variables_group.content, spec)
            enabled, low, high = s.factor_controls[field]
            editor = NumericLineEdit(float(getattr(self.saved_draft, field)), lower, upper, width=54)
            editor.setAccessibleName(f'{label} · Fixed value')
            low.setAccessibleName(f'{label} · Min')
            high.setAccessibleName(f'{label} · Max')
            editor.valueChanged.connect(s._edit_design)
            s.design_value_controls[field] = editor
            for column, control in enumerate((enabled, editor, low, high)):
                variables.addWidget(control, row, column, Qt.AlignmentFlag.AlignLeft)

        self.sampling_group = SidebarGroupBox('Deep Learning parameters')
        sidebar.addWidget(self.sampling_group)
        ml_layout = QFormLayout(self.sampling_group.content)
        ml_layout.setContentsMargins(4, 2, 4, 4)
        ml_layout.setSpacing(3)
        ml_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        s.ml_count_spin = NumericLineEdit(100, 2, 5000)
        s.ml_seed_spin = NumericLineEdit(42, 1, 999999)
        ml_layout.addRow('LHS samples per geometry', s.ml_count_spin)
        ml_layout.addRow('Seed', s.ml_seed_spin)
        s.ml_count_spin.valueChanged.connect(s._refresh_plan)
        s.common_strain_check = QCheckBox('Common final strain (%)')
        s.common_strain_check.setChecked(True)
        s.common_strain_spin = NumericLineEdit(10.0, .1, 95)
        s.common_strain_spin.setAccessibleName(s.common_strain_check.text())
        s.common_strain_check.toggled.connect(s.common_strain_spin.setEnabled)
        ml_layout.addRow(s.common_strain_check, s.common_strain_spin)

        self.material_group = SidebarGroupBox('Solid material')
        sidebar.addWidget(self.material_group)
        materials = QHBoxLayout(self.material_group.content)
        materials.setContentsMargins(4, 2, 4, 4)
        self.material_name = QLabel()
        self.material_name.setTextFormat(Qt.TextFormat.PlainText)
        self.material_name.setWordWrap(True)
        materials.addWidget(self.material_name, 1)
        library = QPushButton('Library')
        library.setFixedHeight(CONTROL_HEIGHT)
        library.setContentsMargins(6, 0, 6, 0)
        library.clicked.connect(s._open_learning_material_library)
        materials.addWidget(library)
        sidebar.addStretch()

        s.ml_plan_label = QLabel()
        s.ml_plan_label.setObjectName('mutedHintLabel')
        s.ml_plan_label.setTextFormat(Qt.TextFormat.PlainText)
        s.ml_plan_label.setWordWrap(True)
        settings_layout.addWidget(s.ml_plan_label)
        s.generate_ml_btn = QPushButton('Generate ML matrix')
        s.generate_ml_btn.setProperty('primary', True)
        s.generate_ml_btn.clicked.connect(s.generate_ml_table)
        settings_layout.addWidget(s.generate_ml_btn, alignment=Qt.AlignmentFlag.AlignRight)
        self.learning_splitter.addWidget(settings_panel)

        matrix_panel = QWidget()
        matrix_panel.setMinimumWidth(220)
        matrix = QVBoxLayout(matrix_panel)
        matrix.setContentsMargins(0, 0, 0, 0)
        matrix.setSpacing(0)
        title = QLabel('Experiment matrix')
        title.setObjectName('sectionSubheader')
        title.setFixedHeight(CONTROL_HEIGHT)
        title.setContentsMargins(4, 0, 0, 0)
        matrix.addWidget(title)
        matrix.addWidget(s._build_experiment_panel(), 1)
        self.learning_splitter.addWidget(matrix_panel)
        self.learning_splitter.setStretchFactor(1, 1)
        self.learning_splitter.setSizes([350, 600])
        self.tabs.addTab(self.learning_page, 'DeepLearning')
        s._size_action_group(s.use_model_btn, s.generate_ml_btn)
