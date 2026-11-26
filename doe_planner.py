"""Navigation and saved-plan controls for the DoE workspace."""
from dataclasses import replace

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout, QLabel,
    QButtonGroup, QPushButton, QCheckBox, QListWidget, QTabWidget, QSizePolicy,
    QScrollArea, QSplitter, QStackedWidget,
)

from studio_style import ActionFlowLayout, CONTROL_HEIGHT, NumericLineEdit, SidebarGroupBox


def geometry_group(settings):
    return (settings.structure_family, settings.structure_name,
            settings.tpms_morphology if settings.structure_family != 'lattice' else '',
            settings.imported_geometry_path, settings.test_profile)


class DoENavigation(QWidget):
    """Show tab selections as command buttons while retaining the tab model."""

    def __init__(self, tabs):
        super().__init__()
        self.setObjectName('doeNavigation')
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        ActionFlowLayout(self, equal_width=True)
        tabs.tabBar().hide()
        self.buttons = QButtonGroup(self)
        for index in range(tabs.count()):
            button = QPushButton(tabs.tabText(index))
            button.setCheckable(True)
            button.setAutoDefault(False)
            self.buttons.addButton(button, index)
            self.add_button(button)
        self.buttons.button(tabs.currentIndex()).setChecked(True)
        self.buttons.idClicked.connect(tabs.setCurrentIndex)
        tabs.currentChanged.connect(lambda index: self.buttons.button(index).setChecked(True))

    def add_button(self, button):
        button.setFixedHeight(CONTROL_HEIGHT)
        layout = self.layout()
        previous = layout.itemAt(layout.count() - 1)
        layout.addWidget(button)
        if previous is not None:
            QWidget.setTabOrder(previous.widget(), button)


class _LearningModeNavigation(DoENavigation):
    def __init__(self, tabs):
        super().__init__(tabs)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        layout = self.layout()
        count = layout.count()
        width = max(layout.itemAt(i).sizeHint().width() for i in range(count))
        return QSize(count * width + (count - 1) * layout.spacing(), CONTROL_HEIGHT)


class SimpleDoEPlanner(QWidget):
    def __init__(self, studio, factor_specs):
        super().__init__()
        s = studio
        self._group_factors = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
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
        # Preserve the legacy draft when reopening and saving older plans.
        self.saved_draft = replace(s._settings_provider(), imported_geometry_path='',
                                          imported_geometry_name='', test_profile='custom',
                                          specimen_architecture='porous_all')
        self._build_learning_page(s, factor_specs)
        self.learning_page = self.legacy_learning_page
        if s.learning_doe is not None:
            self.learning_page = QTabWidget()
            self.learning_page.addTab(s.learning_doe, 'LHS')
            self.learning_page.addTab(self.legacy_learning_page, 'DataSet')
            self.mode_navigation = _LearningModeNavigation(self.learning_page)
            settings_navigation = QStackedWidget()
            settings_navigation.addWidget(s.learning_doe.input_navigation)
            settings_navigation.addWidget(self.input_navigation)
            self.learning_page.currentChanged.connect(settings_navigation.setCurrentIndex)
            navigation = QHBoxLayout()
            navigation.setContentsMargins(2, 0, 2, 0)
            navigation.addWidget(self.mode_navigation, alignment=Qt.AlignmentFlag.AlignTop)
            navigation.addWidget(settings_navigation, 1)
            layout.addLayout(navigation)
        else:
            layout.addWidget(self.input_navigation)
        layout.addWidget(self.learning_page, 1)
        for button in self.findChildren(QPushButton):
            button.setDefault(False)
            button.setAutoDefault(False)

    def _build_learning_page(self, s, factor_specs):
        self.legacy_learning_page = QWidget()
        layout = QVBoxLayout(self.legacy_learning_page)
        layout.setContentsMargins(2, 1, 2, 1)
        self.learning_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.learning_splitter.setObjectName('deepLearningSettingsAndMatrix')
        self.learning_splitter.setHandleWidth(7)
        self.learning_splitter.setChildrenCollapsible(False)
        layout.addWidget(self.learning_splitter)

        settings_panel = QWidget()
        settings_layout = QVBoxLayout(settings_panel)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(2)
        self.input_tabs = QTabWidget()
        self.input_tabs.setMinimumWidth(320)
        analysis = self._add_settings_tab('Analysis settings')
        factor_settings = self._add_settings_tab('Variable settings')
        self.input_navigation = DoENavigation(self.input_tabs)
        settings_layout.addWidget(self.input_tabs, 1)

        designs = QVBoxLayout()
        analysis.addLayout(designs)
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

        self.variables_group = QWidget()
        factor_settings.addWidget(self.variables_group)
        factor_settings.addStretch()
        variables = QGridLayout(self.variables_group)
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
            s._add_factor_controls(self.variables_group, spec)
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
        settings_layout.addWidget(self.sampling_group)
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
        analysis.addWidget(self.material_group)
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
        analysis.addStretch()

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
        matrix.addWidget(s._build_experiment_panel(actions_below=True), 1)
        self.learning_splitter.addWidget(matrix_panel)
        self.learning_splitter.setStretchFactor(1, 1)
        self.learning_splitter.setSizes([350, 600])
        s._size_action_group(s.use_model_btn, s.generate_ml_btn)

    def _add_settings_tab(self, title):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(3)
        self.input_tabs.addTab(scroll, title)
        return layout

    def show_legacy_learning(self):
        if self.learning_page is not self.legacy_learning_page:
            self.learning_page.setCurrentWidget(self.legacy_learning_page)
