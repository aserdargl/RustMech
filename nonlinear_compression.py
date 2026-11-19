"""RustMech: smooth geometry, solid mesh preview and nonlinear MAPDL.

Units: mm, N, MPa. --offline-test never launches ANSYS.
Fixed bottom platen and displacement- or force-driven top platen with configurable contact. Selected material + NLGEOM. Integrated work is external compression work,
not a separately calculated plastic dissipation or energy absorption measure.
"""
from __future__ import annotations

import os

os.environ["QT_API"] = "pyside6"

import argparse
import json
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Event

import numpy as np
import pyvista as pv
from studio_style import CompactSpinBox, CompactComboBox
from PySide6.QtCore import QSignalBlocker, QSize, QTimer, Qt, QUrl
from PySide6.QtGui import QActionGroup, QDesktopServices, QFontDatabase
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTabBar,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from analysis_process import AnalysisProcess, run_single_analysis, run_resume_analysis
from damask_fft import (
    COMPONENTS, MIN_GRID, DamaskAnalysisProcess, sample_periodic_cell,
    validate_periodic_fft,
)
from studio_style import (ActionFlowLayout, CompactDoubleSpinBox, CONTROL_HEIGHT, STUDIO_STYLESHEET,
                          SidebarGroupBox, configure_form, field_label, mesh_precision_stepper,
                          stepper_with_unit)
from sidebar_splitter import SidebarSplitter
from setup_help import GEOMETRY_HELP
from truba_connection import TrubaConnectionDialog
from window_state import show_restored_window
from kratos_settings import KratosSettingsPanel
from solver_profiles import SolverProfileStore, profile_values
from doe_studio import DoEStudioWidget, DoEStudioWindow
from preview_viewport import MESH_PREVIEW_ACTOR, create_preview_viewport
from camera_toolbar import CameraToolbar
from doe_preview import DoEBoundaryPreview
from mesh_preview_worker import run_mesh_preview, shutdown_mesh_preview
from model import (
    ANALYSIS_FIELDS,
    CONTACT_FORMULATIONS,
    CONTACT_NUMERICAL_FIELDS,
    LOADING_MODES,
    PLATEN_CONTACT_MODES,
    GEOMETRY_FIELDS,
    MATERIAL_FIELDS,
    MESH_TYPES,
    MIN_MESH_INPUT_MM,
    ROOT,
    SELF_CONTACT_MODES,
    SOLVER_BACKENDS,
    TETRAHEDRAL_MESH_TYPES,
    Settings,
    load_settings,
    save_settings,
    settings_from_dict,
    validate_geometry,
    validate_settings,
    write_json,
)
from solver_api import (
    MAPDL_CAPABILITIES,
    SOLVER_REGISTRY,
    SolverCapabilities,
    create_solver_backend,
    get_solver_capabilities,
)
from rust_mesh import build_hex_grid as build_hex_grid
from mesh_engine import Preview as Preview, build_preview, generate_volume_mesh, gyroid_field as gyroid_field, make_surface, preview_key
from material_editor import MaterialEditor
from mechanical_analysis import (
    RunPaths as RunPaths,
    offline_check,
    resume_kratos_analysis as resume_kratos_analysis,
    run_analysis as run_analysis,
    run_batch_experiment,
)
from mechanical_results import (
    export_field_results as export_field_results,
    export_geometry as export_geometry,
    export_graphs as export_graphs,
    extract_curve as extract_curve,
    make_compression_platens,
    mesh_quality as mesh_quality,
    save_image as save_image,
)
from structure_library import (
    ALL_STRUCTURES_BY_FAMILY,
    STRUCTURE_FAMILIES,
    TPMS_MORPHOLOGIES,
    describe_structure,
    resolve_family_and_name,
)

PREVIEW_DELAY_MS = 300
MESH_TYPE_LABELS = {
    "HEX20": "Voxel hexahedral (HEX20)",
    "TET10": "Netgen tetrahedral (TET10)",
    "TET4": "Netgen tetrahedral (TET4)",
}


class NoWheelSpinBox(CompactSpinBox):
    """Spinbox that ignores mouse wheel scrolling."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event) -> None:
        event.ignore()


class NoWheelDoubleSpinBox(CompactDoubleSpinBox):
    """Double spinbox that ignores mouse wheel scrolling."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event) -> None:
        event.ignore()


class NumericInputBox(NoWheelDoubleSpinBox):
    """Validated numeric text entry without mouse or keyboard stepping."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setProperty("plainNumericInput", True)

    def stepEnabled(self):
        return QAbstractSpinBox.StepEnabledFlag.StepNone

    def stepBy(self, steps):
        pass


class NoWheelComboBox(CompactComboBox):
    """ComboBox that ignores mouse wheel scrolling."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event) -> None:
        event.ignore()


def _build_fft_preview(settings: Settings, grid: int):
    from mesh_resources import require_memory

    solid = sample_periodic_cell(settings, grid)
    solid_count = int(np.count_nonzero(solid))
    # Allow for VTK's intermediate hexahedra, points and extracted faces.
    require_memory(solid.size * 16 + solid_count * 384, "DAMASK voxel preview")
    spacing = settings.cell_size / grid
    image = pv.ImageData(dimensions=(grid + 1,) * 3, spacing=(spacing,) * 3)
    image.cell_data["solid"] = solid.ravel(order="F").astype(np.uint8)
    # Keep the exposed voxel faces, including the walls of open pores.
    surface = image.threshold(0.5, scalars="solid").extract_surface(
        pass_pointid=False, pass_cellid=False, algorithm="dataset_surface")
    return image.outline(), surface, solid_count


class RustMechDialog(QDialog):
    """Compact settings editor with geometry preview and on-demand FE meshing."""

    def __init__(
        self,
        settings: Settings,
        preview_builder=build_preview,
        solver_capabilities: SolverCapabilities = MAPDL_CAPABILITIES,
        solver_profile_store=None,
    ):
        app_inst = QApplication.instance()
        if app_inst is not None:
            from window_state import install_window_state
            install_window_state(app_inst)
            from ui_copy import install_copy_support
            install_copy_support(app_inst)
            from ui_input import install_settings_wheel_guard
            install_settings_wheel_guard(app_inst)
            from PySide6.QtGui import QColor, QPalette

            app_inst.setStyle("Fusion")
            pal = QPalette()
            pal.setColor(QPalette.ColorRole.Window, QColor("#f3f3f3"))
            pal.setColor(QPalette.ColorRole.WindowText, QColor("#202020"))
            pal.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
            pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#fafafa"))
            pal.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
            pal.setColor(QPalette.ColorRole.ToolTipText, QColor("#202020"))
            pal.setColor(QPalette.ColorRole.Text, QColor("#202020"))
            pal.setColor(QPalette.ColorRole.Button, QColor("#fafafa"))
            pal.setColor(QPalette.ColorRole.ButtonText, QColor("#202020"))
            pal.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
            pal.setColor(QPalette.ColorRole.Highlight, QColor("#0067c0"))
            pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
            pal.setColor(QPalette.ColorRole.Mid, QColor("#d6d6d6"))
            pal.setColor(QPalette.ColorRole.Dark, QColor("#969696"))
            pal.setColor(QPalette.ColorRole.Light, QColor("#ffffff"))
            app_inst.setPalette(pal)
        super().__init__()
        self.analysis_job = None
        self._latest_background_analysis = None
        self.analysis_poll = QTimer(self)
        self.analysis_poll.setInterval(100)
        self.analysis_poll.timeout.connect(self._poll_analysis)
        self.accepted_settings = None
        self.preview = None
        self._result_preview_folder = None
        self._result_preview_data = None
        self._showing_result_preview = False
        self._mesh_requested = False
        self._analysis_after_mesh = None
        self._fft_method_index = None
        self.controls = {}
        self.solver_profile_store = solver_profile_store if solver_profile_store is not None else SolverProfileStore()
        self._persist_solver_profiles = solver_profile_store is not None
        from ui_language import install_language
        self.language_manager = install_language(QApplication.instance(), self.solver_profile_store.settings)
        self._solver_profiles = {}
        self._active_solver_id = settings.solver_backend
        self._switching_solver = False
        self._solver_profiles_ready = False
        self.builder = preview_builder
        self._custom_capabilities = (
            solver_capabilities
            if solver_capabilities.identifier not in SOLVER_REGISTRY
            else None
        )
        self.solver_capabilities = (
            self._custom_capabilities
            or get_solver_capabilities(settings.solver_backend)
        )
        self._active_solver_id = self.solver_capabilities.identifier
        self.active_geometry = settings
        self._custom_experiment = settings if settings.test_profile == 'custom' else Settings()
        self.imported_geometry_path = settings.imported_geometry_path
        self.imported_geometry_name = settings.imported_geometry_name
        self.imported_geometry_role = settings.imported_geometry_role
        self.imported_geometry_height = settings.size if settings.imported_geometry_path else None
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="gyroid-preview"
        )
        self._fft_preview_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fft-preview")
        self.future = None
        self._fft_preview_request = None
        self._fft_preview_future = None
        self._fft_preview_result = None
        self._fft_preview_error = ""
        self._fft_preview_size = None
        self.mesh_cancelled = Event()
        self._preview_detach = Event()
        self.mesh_progress_updates = SimpleQueue()
        self.worker_key = None
        self.failed_key = None
        self.disposed = False

        self.setWindowTitle(
            "RustMech | Nonlinear Compression",
        )
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        self.setSizeGripEnabled(True)
        self.resize(1440, 960)
        self.setMinimumHeight(600)
        self.setObjectName("rustMechWindow")
        self.setStyleSheet(STUDIO_STYLESHEET)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setObjectName("workspaceTabs")
        # Hidden DoE pages must not impose their preferred height on the model view.
        self.workspace_tabs.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)
        model_workspace = QWidget()
        model_layout = QVBoxLayout(model_workspace)
        model_layout.setContentsMargins(4, 2, 4, 0)
        model_layout.setSpacing(2)

        self.main_splitter = SidebarSplitter()
        self.main_splitter.setObjectName("mechanicSettingsSplitter")
        self.main_splitter.setOpaqueResize(True)
        self.main_splitter.setHandleWidth(3)

        # --- LEFT SIDEBAR PANEL ---
        panel = QFrame()
        panel.setObjectName("sidebarPanel")
        panel.setMinimumWidth(230)
        panel.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        left = QVBoxLayout(panel)
        left.setContentsMargins(5, 0, 5, 4)
        left.setSpacing(2)

        brand_card = QFrame()
        self.brand_header = brand_card
        brand_card.setObjectName("brandHeader")
        brand_card.setProperty('computeState', 'local')
        brand_layout = QHBoxLayout(brand_card)
        brand_layout.setContentsMargins(10, 4, 10, 4)
        brand_layout.setSpacing(12)
        eyebrow = QLabel("RustMech")
        eyebrow.setObjectName("brandEyebrow")
        title = QLabel("Nonlinear Compression")
        self.brand_title_label = title
        title.setObjectName("brandTitle")
        self.brand_subtitle_lbl = QLabel(self.solver_capabilities.display_name)
        self.brand_subtitle_lbl.setObjectName("brandSubtitle")
        unit_badge = QLabel("Units: mm  N  MPa")
        unit_badge.setObjectName("unitBadge")
        brand_layout.addWidget(eyebrow)
        brand_layout.addWidget(title)
        brand_layout.addStretch()
        brand_layout.addWidget(self.brand_subtitle_lbl)
        self.truba_dialog = TrubaConnectionDialog(self)
        self.truba_dialog.job_list.background_updated.connect(self._background_analysis_updated)
        self.truba_button = QPushButton("TRUBA")
        self.truba_button.setObjectName("trubaConnectBtn")
        self.truba_button.setProperty("trubaConnected", False)
        self.truba_button.setToolTip("Connect to TRUBA with your own username and password")
        self.truba_button.clicked.connect(lambda: show_restored_window(self.truba_dialog))
        self.truba_dialog.connection_changed.connect(self._update_truba_button)
        self.about_button = QPushButton("About")
        self.about_button.setObjectName("aboutBtn")
        self.about_button.clicked.connect(self.show_about)
        workspace_buttons = QHBoxLayout()
        workspace_buttons.setSpacing(6)
        for button in (self.truba_button, self.about_button):
            button.setFixedSize(124, CONTROL_HEIGHT)
            button.setAutoDefault(False)
            button.setDefault(False)
            workspace_buttons.addWidget(button)
        brand_layout.addLayout(workspace_buttons)
        brand_layout.addWidget(unit_badge)
        from ui_language import LanguageSelector
        self.language_selector = LanguageSelector(self.language_manager, self)
        brand_layout.addWidget(self.language_selector)
        layout.addWidget(brand_card)

        self.setup_tabs = QTabWidget()
        self.setup_tabs.setObjectName("setupTabs")
        self.setup_tabs.setUsesScrollButtons(True)
        self.setup_tabs.setTabBarAutoHide(True)
        left.addWidget(self.setup_tabs, 1)
        geometry_fields = self._add_setup_page("&Setup")
        analysis_fields = geometry_fields

        from standard_tests import PROFILES, ARCHITECTURES
        test_form = self.add_group(geometry_fields, 'Standard test', settings,
            [('core_fraction', 'Porous centre length', 10, 100, 5, 1)], units={'core_fraction': '%'})
        self.test_profile_combo = NoWheelComboBox()
        self.test_profile_combo.setObjectName('standardTestCombo')
        self.test_profile_combo.addItem('Custom experiment', 'custom')
        for identifier, p in PROFILES.items():self.test_profile_combo.addItem(p.label, identifier)
        self.test_profile_combo.setCurrentIndex(max(0,self.test_profile_combo.findData(settings.test_profile)))
        self.architecture_combo = NoWheelComboBox()
        for identifier, label in ARCHITECTURES.items():self.architecture_combo.addItem(label,identifier)
        self.architecture_combo.setCurrentIndex(max(0,self.architecture_combo.findData(settings.specimen_architecture)))
        self.standard_hint = QLabel()
        self.standard_hint.setWordWrap(True)
        self.standard_hint.setObjectName('mutedHintLabel')
        test_form.insertRow(0,field_label('Test profile',self.test_profile_combo),self.test_profile_combo)
        test_form.insertRow(1,field_label('Specimen interior',self.architecture_combo),self.architecture_combo)
        test_form.addRow(self.standard_hint)
        self._test_form = test_form

        geometry_form = self.add_group(
            geometry_fields,
            "Structure / Dimensions",
            settings,
            [
                ("random_seed", "Random seed", 1, 999999, 1, None),
                ("size", "Cube size", 1, 100, 0.5, 3),
                ("cell_size", "Period / pore scale", 0.1, 100, 0.5, 3),
                ("solid_fraction", "Solid fraction", 1, 99, 1, 2),
            ],
            units={"size": "mm", "cell_size": "mm", "solid_fraction": "%"},
        )
        self.stl_resolution_input = QLineEdit()
        self.stl_resolution_input.setFixedSize(80, CONTROL_HEIGHT)
        self.stl_resolution_input.setPlaceholderText("auto")
        self.stl_resolution_input.setText(
            "auto" if settings.stl_resolution is None else f"{settings.stl_resolution:.12g}")
        self.stl_auto_button = QPushButton("Auto")
        self.stl_auto_button.setAutoDefault(False)
        self.stl_auto_button.setDefault(False)
        self.stl_auto_button.setFixedSize(
            self.stl_auto_button.fontMetrics().horizontalAdvance("Otomatik") + 16, CONTROL_HEIGHT)
        geometry_form.addRow(field_label(
            "STL resolution (mm)", self.stl_resolution_input,
            tooltip="Surface sampling spacing in mm, independent of FE element size. "
                    "Leave empty or enter auto for automatic resolution. "
                    "Auto fills in the current automatically calculated value."))
        self.stl_resolution_input.setFixedWidth(80)
        self.stl_auto_button.clicked.connect(self._fill_auto_stl_resolution)
        mesh_form = self.add_group(
            geometry_fields, "Mesh", settings,
            [
                ("mesh_resolution", "Global mesh size", MIN_MESH_INPUT_MM, 5, 0.05, 3),
                ("local_mesh_size", "Local mesh size", MIN_MESH_INPUT_MM, 5, 0.001, 3),
            ],
            units={"mesh_resolution": "mm", "local_mesh_size": "mm"},
        )
        self.mesh_precision = mesh_precision_stepper(
            self.solver_profile_store.settings, "ui/meshDisplayDecimals",
            self.controls["mesh_resolution"], self.controls["local_mesh_size"],
        )
        mesh_form.addRow(field_label("Mesh decimal places", self.mesh_precision), self.mesh_precision)
        self._mesh_form = mesh_form

        # Insert Family, Structure, and Morphology selectors at the top of Geometry
        self.family_combo = NoWheelComboBox()
        for fam_key, fam_label in STRUCTURE_FAMILIES.items():
            self.family_combo.addItem(fam_label, fam_key)
        self.family_combo.addItem('Import geometry…', 'imported')
        resolved_fam, resolved_name = resolve_family_and_name(
            settings.structure_family, settings.structure_name
        )
        self._generated_family = resolved_fam
        self.family_combo.setCurrentIndex(
            max(0, self.family_combo.findData(resolved_fam))
        )

        self.structure_combo = NoWheelComboBox()

        self.morphology_combo = NoWheelComboBox()
        for morph_key, morph_label in TPMS_MORPHOLOGIES.items():
            self.morphology_combo.addItem(morph_label, morph_key)
        self.morphology_combo.setCurrentIndex(
            max(0, self.morphology_combo.findData(settings.tpms_morphology))
        )

        self._populate_structure_items(resolved_fam, resolved_name)

        self.structure_hint_label = QLabel()
        self.structure_hint_label.setObjectName("mutedHintLabel")
        self.structure_hint_label.setWordWrap(True)

        fam_lbl = field_label(
            "Structure family", self.family_combo, tooltip=GEOMETRY_HELP["structure_family"]
        )
        struct_lbl = field_label(
            "Structure type", self.structure_combo, tooltip=GEOMETRY_HELP["structure_name"]
        )
        morph_lbl = field_label(
            "Morphology", self.morphology_combo, tooltip=GEOMETRY_HELP["tpms_morphology"]
        )

        geometry_form.insertRow(0, fam_lbl, self.family_combo)
        geometry_form.insertRow(1, struct_lbl, self.structure_combo)
        geometry_form.insertRow(2, morph_lbl, self.morphology_combo)
        geometry_form.insertRow(3, self.structure_hint_label)
        geometry_form.setRowVisible(self.structure_hint_label, False)
        self.structure_apply_button = QPushButton("Apply")
        self.structure_apply_button.setObjectName("structureApplyBtn")
        self.structure_apply_button.setAutoDefault(False)
        self.structure_apply_button.setDefault(False)
        self.structure_apply_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.structure_apply_button.setToolTip(
            "Apply the selected structure and geometry settings to the 3D preview."
        )
        self.structure_apply_button.clicked.connect(self.apply_geometry)
        structure_actions = QWidget()
        structure_action_row = QHBoxLayout(structure_actions)
        structure_action_row.setContentsMargins(0, 0, 0, 0)
        structure_action_row.setSpacing(4)
        structure_action_row.addWidget(self.stl_resolution_input)
        structure_action_row.addWidget(self.stl_auto_button)
        structure_action_row.addStretch()
        structure_action_row.addWidget(self.structure_apply_button)
        geometry_form.addRow(structure_actions)
        self.structure_apply_button.ensurePolished()
        structure_action_width = max(80, max(
            self.structure_apply_button.fontMetrics().horizontalAdvance(text)
            for text in ("Auto", "Otomatik", "Apply", "Uygula")
        ) + 24)
        self.stl_auto_button.setFixedSize(structure_action_width, CONTROL_HEIGHT)
        self.structure_apply_button.setFixedSize(structure_action_width, CONTROL_HEIGHT)
        self.clear_import_button = QPushButton('Use generated structure')
        self.clear_import_button.setAutoDefault(False)
        self.clear_import_button.clicked.connect(self.clear_imported_geometry)
        geometry_form.addRow(self.clear_import_button)
        self.import_geometry_label = QLabel(self.imported_geometry_name or 'No imported geometry')
        self.import_geometry_label.setWordWrap(True)
        self.import_geometry_label.setObjectName('mutedHintLabel')
        geometry_form.addRow(self.import_geometry_label)
        if self.imported_geometry_path:
            self.controls['size'].setRange(min(.000001, settings.size), max(100, settings.size))
            self.controls['size'].setValue(settings.size)
        self.controls['size'].setEnabled(not bool(self.imported_geometry_path))
        self._sync_import_controls()
        self.mesh_type_combo = NoWheelComboBox()
        for mesh_type in MESH_TYPES:
            self.mesh_type_combo.addItem(mesh_type, mesh_type)
            self.mesh_type_combo.setItemData(
                self.mesh_type_combo.count() - 1,
                MESH_TYPE_LABELS.get(mesh_type, mesh_type), Qt.ItemDataRole.ToolTipRole,
            )
        self.mesh_type_combo.setCurrentIndex(
            max(0, self.mesh_type_combo.findData(settings.mesh_type))
        )
        mesh_form.addRow(
            field_label("Element type", self.mesh_type_combo, tooltip=GEOMETRY_HELP["mesh_type"]),
            self.mesh_type_combo,
        )
        self.tetra_strategy_combo = NoWheelComboBox()
        for label, value in (("Auto (TPMS cells / Netgen)", "auto"),
                             ("Netgen", "netgen"), ("Periodic cells", "periodic")):
            self.tetra_strategy_combo.addItem(label, value)
        self.tetra_strategy_combo.setCurrentIndex(
            max(0, self.tetra_strategy_combo.findData(settings.tetra_strategy)))
        mesh_form.addRow(field_label("Meshing method", self.tetra_strategy_combo,
                                tooltip=GEOMETRY_HELP["tetra_strategy"]), self.tetra_strategy_combo)
        self.mesh_apply_button = QPushButton("Apply")
        self.mesh_apply_button.setObjectName("meshApplyBtn")
        self.mesh_apply_button.setAutoDefault(False)
        self.mesh_apply_button.setDefault(False)
        self.mesh_apply_button.setFixedWidth(self.structure_apply_button.width())
        self.mesh_apply_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mesh_apply_button.setToolTip("Apply current settings and build the FE mesh.")
        self.mesh_apply_button.clicked.connect(self.apply_mesh)
        mesh_actions = QWidget()
        mesh_action_row = QHBoxLayout(mesh_actions)
        mesh_action_row.setContentsMargins(0, 0, 0, 0)
        mesh_action_row.setSpacing(0)
        mesh_action_row.addStretch()
        mesh_action_row.addWidget(self.mesh_apply_button)
        mesh_form.addRow(mesh_actions)
        self.tetra_strategy_combo.currentIndexChanged.connect(self.geometry_changed)
        self.mesh_type_combo.currentIndexChanged.connect(self._sync_mesh_type)
        self._sync_mesh_type()
        self._geometry_form = geometry_form
        self._morph_label_widget = morph_lbl

        self.family_combo.currentIndexChanged.connect(self._on_family_changed)
        self.family_combo.activated.connect(
            lambda index: self.import_geometry()
            if self.family_combo.itemData(index) == 'imported' else None
        )
        self.structure_combo.currentIndexChanged.connect(self._on_structure_selection_changed)
        self.morphology_combo.currentIndexChanged.connect(self._on_structure_selection_changed)
        self._sync_structure_ui_visibility()

        self.material_editor = MaterialEditor(settings)
        self.controls.update(self.material_editor.controls)
        geometry_fields.addWidget(self.material_editor)

        self.geometry_apply_button = QPushButton("Update preview")
        self.geometry_apply_button.setObjectName("geometryApplyBtn")
        self.geometry_apply_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.geometry_apply_button.setIconSize(QSize(16, 16))
        self.geometry_apply_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.geometry_apply_button.setToolTip(
            "Apply geometry settings and refresh the 3D preview."
        )
        self.geometry_apply_button.clicked.connect(self.apply_geometry)
        geometry_actions = QWidget()
        action_row = ActionFlowLayout(geometry_actions)
        action_row.addWidget(self.geometry_apply_button)
        convergence_button = QPushButton("Mesh Convergence")
        convergence_button.setObjectName("secondaryActionBtn")
        convergence_button.setCursor(Qt.CursorShape.PointingHandCursor)
        convergence_button.clicked.connect(self.open_mesh_convergence)
        action_row.addWidget(convergence_button)
        geometry_fields.addWidget(geometry_actions)

        contact_form = self.add_group(
            analysis_fields,
            "Contact and friction",
            settings,
            [
                (
                    "bottom_platen_friction_coeff",
                    "Bottom platen friction μ",
                    0.0,
                    2.0,
                    0.05,
                    3,
                ),
                (
                    "top_platen_friction_coeff",
                    "Top platen friction μ",
                    0.0,
                    2.0,
                    0.05,
                    3,
                ),
                ("self_friction_coeff", "Self-friction μ_self", 0.0, 2.0, 0.05, 3),
                ("platen_penetration_tolerance_factor", "Platen penetration tol.", 1e-6, .5, .0001, 6),
                (
                    "normal_contact_stiffness_factor",
                    "Normal stiffness (FKN)",
                    0.01,
                    10.0,
                    0.1,
                    3,
                ),
                (
                    "penetration_tolerance_factor",
                    "Penetration tol. (FTOLN)",
                    0.001,
                    0.5,
                    0.01,
                    3,
                ),
                (
                    "contact_damping_factor",
                    "Contact damping (FDMN)",
                    0.0,
                    1.0,
                    0.005,
                    4,
                ),
            ],
        )
        self._contact_form = contact_form
        self.platen_contact_combos = {}
        for side, title in (("bottom", "Bottom contact"), ("top", "Top contact")):
            combo = NoWheelComboBox()
            for key, label in PLATEN_CONTACT_MODES.items():
                combo.addItem(label, key)
            combo.setCurrentIndex(max(0, combo.findData(getattr(settings, f"{side}_platen_contact"))))
            self.platen_contact_combos[side] = combo
            contact_form.addRow(field_label(title, combo), combo)
            combo.currentIndexChanged.connect(self._on_contact_changed)

        self.self_contact_combo = NoWheelComboBox()
        for sc_key, sc_label in SELF_CONTACT_MODES.items():
            self.self_contact_combo.addItem(sc_label, sc_key)
        self.self_contact_combo.setCurrentIndex(
            max(0, self.self_contact_combo.findData(settings.self_contact_mode))
        )
        self_contact_lbl = field_label("Internal self-contact", self.self_contact_combo)

        self.contact_formulation_combo = NoWheelComboBox()
        for cf_key, cf_label in CONTACT_FORMULATIONS.items():
            self.contact_formulation_combo.addItem(cf_label, cf_key)
        self.contact_formulation_combo.setCurrentIndex(
            max(
                0,
                self.contact_formulation_combo.findData(
                    settings.contact_formulation
                ),
            )
        )
        cf_lbl = field_label("Contact algorithm", self.contact_formulation_combo)

        self.contact_hint_label = QLabel()
        self.contact_hint_label.setObjectName("mutedHintLabel")
        self.contact_hint_label.setWordWrap(True)

        contact_form.insertRow(2, self_contact_lbl, self.self_contact_combo)
        contact_form.insertRow(4, cf_lbl, self.contact_formulation_combo)
        contact_form.addRow(self.contact_hint_label)

        self.automatic_contact = QCheckBox("Automatic defaults")
        defaults = Settings()
        auto_contact = settings.automatic_contact_parameters
        if auto_contact is None:
            auto_contact = (all(getattr(settings, name) == getattr(defaults, name)
                                for name in CONTACT_NUMERICAL_FIELDS)
                            and settings.contact_formulation == defaults.contact_formulation)
        self.automatic_contact.setChecked(auto_contact)
        self.automatic_contact.setToolTip(
            "Use the application's recommended numerical contact parameters. "
            "Clear to edit the algorithm, stiffness, penetration tolerances and damping. "
            "Contact types and friction coefficients remain your physical inputs.")
        contact_form.insertRow(0, field_label("Numerical settings", self.automatic_contact), self.automatic_contact)
        self._manual_contact_values = None
        self.automatic_contact.toggled.connect(self._automatic_contact_changed)

        self.self_contact_combo.currentIndexChanged.connect(
            self._on_contact_changed
        )
        self.contact_formulation_combo.currentIndexChanged.connect(
            self._on_contact_changed
        )

        solver_form = self.add_group(
            analysis_fields,
            "Solver and loading",
            settings,
            [
                ("compression", "Compression", 0.001, 99, 0.1, 3),
                ("applied_force", "Compression force", 1e-9, 1e12, 10, 3),
                (
                    "cores",
                    "CPU cores",
                    1,
                    self.solver_capabilities.max_cores,
                    1,
                    None,
                ),
            ],
            units={"compression": "mm", "applied_force": "N"},
        )
        substeps_row = QWidget()
        self._substeps_row = substeps_row
        substeps_layout = QHBoxLayout(substeps_row)
        substeps_layout.setContentsMargins(0, 0, 0, 0)
        substeps_layout.setSpacing(0)
        self.substeps_status = QLabel("Automatic", substeps_row)
        self.substeps_status.hide()
        self.substeps_mode_combo = NoWheelComboBox()
        self.substeps_mode_combo.addItem("Automatic", "automatic")
        self.substeps_mode_combo.addItem("Advanced", "advanced")
        self.substeps_mode_combo.setFixedWidth(110)
        self.substeps_mode_combo.setToolTip(
            "Automatic uses recommended load stepping defaults. Advanced applies the values below.")
        substeps_layout.addWidget(self.substeps_mode_combo)
        substeps_layout.addStretch()
        solver_form.addRow(field_label("Substeps", substeps_row), substeps_row)

        self.substeps_advanced = QWidget()
        advanced_form = QFormLayout(self.substeps_advanced)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        configure_form(advanced_form)
        minimum, maximum = settings.substep_limits
        for key, label, value, hint in (
            ("substeps", "Initial substeps", settings.substeps,
             "Initial load increment = total load / initial substeps."),
            ("minimum_substeps", "Minimum substeps", minimum,
             "Limits the largest load increment: total load / minimum substeps."),
            ("maximum_substeps", "Maximum substeps", maximum,
             "Limits the smallest load increment: total load / maximum substeps."),
        ):
            control = NoWheelSpinBox()
            control.setRange(1, max(100_000_000, value))
            control.setValue(value)
            control.setKeyboardTracking(False)
            self.controls[key] = control
            advanced_form.addRow(field_label(label, control, tooltip=hint), control)
        self.substeps_advanced.hide()
        self.substeps_mode_combo.currentIndexChanged.connect(self._substeps_mode_changed)
        self._restore_substeps_mode(settings)
        solver_form.addRow(self.substeps_advanced)

        # Keep all specimen conditions together, immediately below the mesh.
        mesh_index = geometry_fields.indexOf(mesh_form.parentWidget().parentWidget())
        geometry_fields.insertWidget(mesh_index + 1, solver_form.parentWidget().parentWidget())
        geometry_fields.insertWidget(geometry_fields.indexOf(self.material_editor) + 1,
                                     contact_form.parentWidget().parentWidget())
        self._loading_form = solver_form
        self._solver_group = solver_form.parentWidget().parentWidget()
        self.kratos_panel = KratosSettingsPanel(settings)
        geometry_fields.insertWidget(geometry_fields.indexOf(self._solver_group) + 1, self.kratos_panel)
        self.kratos_panel.hide()
        self.loading_mode_combo = NoWheelComboBox()
        for key, label in LOADING_MODES.items():
            self.loading_mode_combo.addItem(label, key)
        self.loading_mode_combo.setCurrentIndex(max(0, self.loading_mode_combo.findData(settings.loading_mode)))
        solver_form.insertRow(0, field_label("Loading control", self.loading_mode_combo), self.loading_mode_combo)
        self.loading_hint_label = QLabel()
        self.loading_hint_label.setObjectName("mutedHintLabel")
        self.loading_hint_label.setWordWrap(True)
        solver_form.addRow(self.loading_hint_label)
        self.loading_mode_combo.currentIndexChanged.connect(self._on_loading_changed)
        self._sync_loading_ui()

        self.solver_combo = NoWheelComboBox()
        for solver_key, solver_label in SOLVER_BACKENDS.items():
            self.solver_combo.addItem(solver_label, solver_key)
            if solver_key == "openfoam":
                self.solver_combo.model().item(self.solver_combo.count() - 1).setEnabled(False)
        if self._custom_capabilities is not None:
            self.solver_combo.addItem(
                self._custom_capabilities.display_name,
                self._custom_capabilities.identifier,
            )
            self.solver_combo.setCurrentIndex(
                max(
                    0,
                    self.solver_combo.findData(
                        self._custom_capabilities.identifier
                    ),
                )
            )
        else:
            self.solver_combo.setCurrentIndex(
                max(0, self.solver_combo.findData(settings.solver_backend))
            )

        self.solver_hint_label = QLabel()
        self.solver_hint_label.setObjectName("mutedHintLabel")
        self.solver_hint_label.setWordWrap(True)

        solver_lbl = field_label("Solver engine", self.solver_combo)
        solver_form.insertRow(0, solver_lbl, self.solver_combo)
        solver_form.insertRow(1, self.solver_hint_label)

        self.solver_combo.currentIndexChanged.connect(self._on_solver_changed)
        self._sync_solver_ui()
        self.automatic_cores = QCheckBox("Auto")
        auto_cores = settings.automatic_cores
        if auto_cores is None:
            auto_cores = settings.cores == self.solver_capabilities.default_cores
        self.automatic_cores.setChecked(auto_cores)
        self.automatic_cores.setToolTip("Use the selected solver's default CPU count within its license limit.")
        cores = self.controls["cores"]
        core_row, _ = solver_form.getWidgetPosition(cores)
        solver_form.removeWidget(cores)
        core_field = QWidget()
        core_layout = QHBoxLayout(core_field)
        core_layout.setContentsMargins(0, 0, 0, 0)
        core_layout.setSpacing(4)
        core_layout.addWidget(cores)
        core_layout.addWidget(self.automatic_cores)
        core_layout.addStretch(1)
        solver_form.setWidget(core_row, QFormLayout.ItemRole.FieldRole, core_field)
        self._manual_cores = settings.cores
        self.automatic_cores.toggled.connect(self._automatic_cores_changed)
        self._automatic_cores_changed()
        self._automatic_contact_changed()
        self._sync_contact_ui()

        note = QLabel(
            "• Bottom platen at Z = 0 · Top platen at Z = L (−Z compression)\n"
            "• Rigid platen contact with separate Coulomb friction coefficients\n"
            "• Configurable 3D Cell-Wall Self-Contact & Internal Friction (μ_self)\n"
            "• Large displacement (NLGEOM) · voxel HEX8/HEX20 or surface-conforming TET4/TET10"
        )
        note.setObjectName("physicsNoteCard")
        self.physics_note = note
        note.setWordWrap(True)
        analysis_fields.addWidget(note)
        analysis_fields.addStretch()

        self._add_method_pages()
        self.setup_tabs.tabBar().hide()

        self.main_splitter.addWidget(panel)
        self.main_splitter.setCollapsible(0, True)

        preview_tab = QWidget()
        preview_tab.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        right = QVBoxLayout(preview_tab)
        right.setContentsMargins(2, 0, 0, 0)
        right.setSpacing(2)

        # One navigation row for workspaces, display modes and camera controls.
        toolbar_frame = QFrame()
        toolbar_frame.setObjectName("viewportToolbar")
        toolbar_frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        toolbar = QGridLayout(toolbar_frame)
        toolbar.setContentsMargins(5, 1, 5, 1)
        toolbar.setHorizontalSpacing(6)
        toolbar.setColumnStretch(0, 1)
        toolbar.setColumnStretch(2, 1)
        navigation_controls = QWidget()
        navigation = QHBoxLayout(navigation_controls)
        navigation.setContentsMargins(0, 0, 0, 0)
        navigation.setSpacing(2)
        toolbar.addWidget(navigation_controls, 0, 0, Qt.AlignmentFlag.AlignLeft)
        view_controls = QWidget()
        view_toolbar = QHBoxLayout(view_controls)
        view_toolbar.setContentsMargins(0, 0, 0, 0)
        view_toolbar.setSpacing(2)
        toolbar.addWidget(view_controls, 0, 2, Qt.AlignmentFlag.AlignRight)

        from project_workspace import ProjectWorkspace
        self.project_workspace = ProjectWorkspace(self)
        self.project_button = QToolButton()
        self.project_button.setObjectName("headerMenu")
        self.project_button.setText("Project")
        self.project_button.setAccessibleName("Analysis project")
        self.project_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        project_menu = QMenu(self.project_button)
        project_menu.addAction("Open project...", self.project_workspace.open)
        project_menu.addAction("Save project", lambda: self.project_workspace.save())
        project_menu.addAction("Save project as...", lambda: self.project_workspace.save(save_as=True))
        self.project_button.setMenu(project_menu)
        navigation.addWidget(self.project_button)

        self.sidebar_toggle = QToolButton()
        self.sidebar_toggle.setObjectName("headerMenu")
        self.sidebar_toggle.setToolTip("Select a method to open its settings")
        self.sidebar_toggle.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.methods_menu = QMenu(self.sidebar_toggle)
        self.method_actions = QActionGroup(self.methods_menu)
        self.method_actions.setExclusive(True)
        for index, name in enumerate(self.method_names):
            action = self.methods_menu.addAction(name)
            action.setCheckable(True)
            action.setData(index)
            action.setChecked(index == 0)
            self.method_actions.addAction(action)
            action.triggered.connect(lambda checked=False, page=index: self._select_settings_method(page))
        self.sidebar_toggle.setMenu(self.methods_menu)
        self.setup_tabs.currentChanged.connect(self._sync_settings_method)
        self._sync_settings_method(self.setup_tabs.currentIndex())
        navigation.addWidget(self.sidebar_toggle)

        # Keep the indexed navigation model for existing integrations; expose
        # workspace selection through one compact menu instead of four tabs.
        self.workspace_navigation = QTabBar(toolbar_frame)
        self.workspace_navigation.setObjectName("workspaceNavigation")
        self.workspace_navigation.setExpanding(False)
        self.workspace_navigation.setDrawBase(False)
        workspace_names = ("Mechanic", "Experiments (DoE)", "Flow CFD", "Thermal CHT")
        for name in workspace_names:
            self.workspace_navigation.addTab(name)
        self.workspace_navigation.currentChanged.connect(self.workspace_tabs.setCurrentIndex)
        self.workspace_tabs.currentChanged.connect(self.workspace_navigation.setCurrentIndex)
        self.workspace_navigation.hide()
        self.workspace_button = QToolButton()
        self.workspace_button.setObjectName("headerMenu")
        self.workspace_button.setText(workspace_names[0])
        self.workspace_button.setAccessibleName("Select workspace")
        self.workspace_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.workspace_menu = QMenu(self.workspace_button)
        self.workspace_actions = QActionGroup(self.workspace_menu)
        self.workspace_actions.setExclusive(True)
        for index, name in enumerate(workspace_names):
            action = self.workspace_menu.addAction(name)
            action.setCheckable(True)
            action.setData(index)
            action.setChecked(index == 0)
            self.workspace_actions.addAction(action)
            action.setVisible(index != 1)
            action.triggered.connect(lambda checked=False, page=index: self.workspace_tabs.setCurrentIndex(page))
        self.workspace_button.setMenu(self.workspace_menu)
        navigation.addWidget(self.workspace_button)
        self.doe_button = QPushButton("DoE")
        self.doe_button.setToolTip("Open Design of Experiments settings.")
        self.doe_button.clicked.connect(self._show_doe_workspace)
        navigation.addWidget(self.doe_button)
        self.designs_button = QToolButton()
        self.designs_button.setObjectName("headerMenu")
        self.designs_button.setText("Designs")
        self.designs_button.setAccessibleName("Design Pool and analysis queue")
        self.designs_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        designs_menu = QMenu(self.designs_button)
        designs_menu.addAction("Design Pool (Gitterburg)...", self._show_design_pool)
        designs_menu.addAction("Import geometry for flow / thermal...", self.import_flow_geometry)
        designs_menu.addAction("Add current model to analysis queue", self._enqueue_current_model)
        designs_menu.addAction("Analysis queue...", self._show_analysis_queue)
        self.designs_button.setMenu(designs_menu)
        navigation.addWidget(self.designs_button)
        self.folder_batch_button = QPushButton("Batch Analysis")
        self.folder_batch_button.setToolTip("Batch analysis from folder...")
        self.folder_batch_button.clicked.connect(self._show_folder_batch)
        navigation.addWidget(self.folder_batch_button)
        self.esen_workspace = None
        self.esen_button = QPushButton("ESEN")
        self.esen_button.setToolTip("Open the LAMMPS workspace")
        self.esen_button.clicked.connect(self.show_esen_workspace)
        navigation.addWidget(self.esen_button)

        self.display_mode_group = QFrame()
        self.display_mode_group.setObjectName("displayModeGroup")
        self.display_mode_group.setAccessibleName("3D display mode")
        mode_layout = QHBoxLayout(self.display_mode_group)
        mode_layout.setContentsMargins(3, 2, 3, 2)
        mode_layout.setSpacing(5)
        toolbar.addWidget(self.display_mode_group, 0, 1, Qt.AlignmentFlag.AlignCenter)

        self.mode = NoWheelComboBox(toolbar_frame)
        self.mode.setAccessibleName("3D display mode")
        self.mode.addItems(
            ["Smooth geometry", "FE mesh", "Smooth + FE overlay"]
        )
        self.mode.setCurrentIndex(0)
        self.mode.currentIndexChanged.connect(self._display_mode_changed)
        # Retain the mode model for existing render/preview integrations.
        self.mode.hide()
        self.display_mode_buttons = QButtonGroup(self.display_mode_group)
        for index, text in enumerate(("Geometry", "FE Mesh", "Overlay")):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setToolTip(self.mode.itemText(index))
            self.display_mode_buttons.addButton(button, index)
            mode_layout.addWidget(button)
        self.display_mode_buttons.button(self.mode.currentIndex()).setChecked(True)
        self.display_mode_buttons.idClicked.connect(lambda _: self.workspace_tabs.setCurrentIndex(0))
        self.display_mode_buttons.idClicked.connect(self._select_model_view)
        self.mode.currentIndexChanged.connect(lambda index: self.display_mode_buttons.button(index).setChecked(True))

        self.show_platens_cb = QCheckBox("Show platens")
        self.show_platens_cb.setObjectName("legendPlatenPill")
        self.show_platens_cb.setChecked(True)
        self.show_platens_cb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.show_platens_cb.toggled.connect(self.render_preview)
        view_toolbar.addWidget(self.show_platens_cb)

        self.result_preview_controls = QWidget()
        result_controls_layout = QHBoxLayout(self.result_preview_controls)
        result_controls_layout.setContentsMargins(0, 0, 0, 0)
        result_controls_layout.setSpacing(4)
        result_label = QLabel("Result field")
        self.result_preview_field = NoWheelComboBox()
        self.result_preview_field.setAccessibleName("Result field")
        result_label.setBuddy(self.result_preview_field)
        result_controls_layout.addWidget(result_label)
        result_controls_layout.addWidget(self.result_preview_field)
        self.result_preview_field.addItem("Model preview")
        self.result_preview_field.addItem("von Mises stress / MPa", "Von_Mises_MPa")
        self.result_preview_field.addItem("Displacement / mm", "Displacement_Magnitude_mm")
        self.result_preview_field.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.result_preview_field.setMaximumWidth(200)
        self.result_preview_field.currentIndexChanged.connect(self.render_preview)
        self.result_preview_controls.hide()

        # Keep warnings together with the status messages at the bottom.
        self.fragment_status = QLabel()
        self.fragment_status.setObjectName("fragmentWarning")
        self.fragment_status.setWordWrap(True)
        self.fragment_status.setVisible(False)

        self.preview_splitter = QSplitter(Qt.Orientation.Vertical)
        self.preview_splitter.setObjectName("mechanicPreviewSplitter")
        self.preview_splitter.setChildrenCollapsible(True)
        self.preview_splitter.setOpaqueResize(True)
        self.preview_splitter.setHandleWidth(5)

        # 3D Interactive Viewport Container
        viewport_container = QFrame()
        viewport_container.setObjectName("viewportContainer")
        viewport_container.setMinimumSize(120, 80)
        viewport_container.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )
        viewport_layout = QHBoxLayout(viewport_container)
        viewport_layout.setContentsMargins(0, 0, 0, 0)
        viewport_layout.setSpacing(2)

        camera_controls = CameraToolbar(viewport_container)
        self.camera_toolbar = camera_controls
        camera_menu = QMenu(camera_controls)
        for label, preset, tooltip in (
            ("Iso", "iso", "Isometric view"),
            ("XY", "xy", "View the XY plane from +Z"),
            ("−XY", "-xy", "View the XY plane from −Z"),
            ("XZ", "xz", "View the XZ plane from −Y"),
            ("−XZ", "-xz", "View the XZ plane from +Y"),
            ("YZ", "yz", "View the YZ plane from +X"),
            ("−YZ", "-yz", "View the YZ plane from −X"),
        ):
            preset_action = camera_menu.addAction(label)
            camera_controls.add_camera_action(preset_action, preset)
            preset_action.setToolTip(tooltip)
            preset_action.triggered.connect(
                lambda checked=False, view=preset: self._set_camera_preset(view)
            )
        camera_controls.column.addSpacing(3)
        projection_group = QActionGroup(camera_menu)
        projection_group.setExclusive(True)
        self.orthographic_button = camera_menu.addAction("Orthographic")
        self.orthographic_button.setCheckable(True)
        self.orthographic_button.setChecked(True)
        projection_group.addAction(self.orthographic_button)
        self.perspective_button = camera_menu.addAction("Perspective")
        self.perspective_button.setCheckable(True)
        projection_group.addAction(self.perspective_button)
        self.perspective_button.setToolTip("Toggle perspective projection (OFF: orthographic view)")
        self.perspective_button.toggled.connect(self._set_perspective)
        camera_controls.add_camera_action(self.orthographic_button, "orthographic")
        camera_controls.add_camera_action(self.perspective_button, "perspective")
        camera_controls.add_camera_action(camera_menu.addAction("Reset view", self.reset_view), "reset")

        self.plotter = create_preview_viewport(self, simplify_mesh_during_drag=True)
        self.plotter.on_orientation_selected = lambda: self.orthographic_button.setChecked(True)
        self.plotter.enable_parallel_projection()
        self.plotter.interactor.setMinimumSize(100, 70)
        self.plotter.interactor.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )
        viewport_layout.addWidget(self.plotter.interactor, 1)
        self.preview_splitter.addWidget(viewport_container)

        bottom_telemetry_panel = QWidget()
        bottom_telemetry_layout = QVBoxLayout(bottom_telemetry_panel)
        bottom_telemetry_layout.setContentsMargins(0, 2, 2, 0)
        bottom_telemetry_layout.setSpacing(0)

        # Live KPI Metric Row
        self.mesh_kpis = QWidget()
        kpi_row = QHBoxLayout(self.mesh_kpis)
        kpi_row.setContentsMargins(0, 0, 0, 0)
        kpi_row.setSpacing(0)
        self._kpi_labels: dict[str, QLabel] = {}
        for key, title, initial in (
            ("elements", "Elements", "—"),
            ("nodes", "Nodes", "—"),
            ("fraction", "Solid fraction", "—"),
            ("spacing", "Voxel spacing", "—"),
        ):
            card = QFrame()
            card.setObjectName("kpiCard")
            card_layout = QHBoxLayout(card)
            card_layout.setContentsMargins(4, 3, 4, 3)
            card_layout.setSpacing(4)
            t_lbl = QLabel(title)
            t_lbl.setObjectName("kpiTitle")
            t_lbl.setWordWrap(False)
            v_lbl = QLabel(initial)
            v_lbl.setObjectName("kpiValue")
            card.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
            card_layout.addWidget(t_lbl)
            card_layout.addWidget(v_lbl)
            kpi_row.addWidget(card, 0, Qt.AlignmentFlag.AlignLeft)
            self._kpi_labels[key] = v_lbl
        bottom_telemetry_layout.addWidget(self.mesh_kpis, alignment=Qt.AlignmentFlag.AlignLeft)
        self.fft_preview_summary = QLabel()
        self.fft_preview_summary.setObjectName("statsReadout")
        self.fft_preview_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.fft_preview_summary.setWordWrap(True)
        self.fft_preview_summary.hide()
        bottom_telemetry_layout.addWidget(self.fft_preview_summary)

        # Detailed Telemetry Readout Card
        telemetry_card = QFrame()
        telemetry_card.setObjectName("telemetryCard")
        telemetry_layout = QVBoxLayout(telemetry_card)
        telemetry_layout.setContentsMargins(12, 8, 12, 8)
        telemetry_layout.setSpacing(4)

        self.stats = QLabel("Preparing geometry preview...")
        self.stats.setObjectName("statsReadout")
        self.stats.setWordWrap(True)
        telemetry_layout.addWidget(self.stats)
        # Full diagnostics remain available on demand instead of occupying the canvas.
        self.telemetry_details = telemetry_card
        telemetry_card.hide()
        bottom_telemetry_layout.addWidget(telemetry_card)
        self._mesh_details_action = camera_menu.addAction("Mesh diagnostics")
        self._mesh_details_action.setCheckable(True)
        self._mesh_details_action.toggled.connect(self._sync_preview_readout)
        camera_controls.add_camera_action(self._mesh_details_action, "diagnostics")
        camera_controls.attach(self.plotter.interactor)
        self._kpi_labels["spacing"].parentWidget().hide()

        cancel = QPushButton("Cancel")
        cancel.setObjectName("cancelBtn")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)

        self.apply_button = QPushButton("Run analysis")
        self.apply_button.setObjectName("primaryLaunchBtn")
        self.apply_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.apply_button.setIconSize(QSize(16, 16))
        self.apply_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_button.setEnabled(False)
        self.apply_button.setAutoDefault(False)
        self.apply_button.setToolTip("Save settings and start the selected solver.")
        self.apply_button.clicked.connect(self.apply)

        self.force_stop_button = QPushButton("Forced Stop")
        self.force_stop_button.setObjectName("forcedStopBtn")
        self.force_stop_button.setAutoDefault(False)
        self.force_stop_button.setEnabled(False)
        self.force_stop_button.setToolTip("Cancel the active analysis and forcibly terminate its process tree.")
        self.force_stop_button.clicked.connect(self.force_stop_analysis)

        # Status and analysis actions
        self.status_banner = QFrame()
        self.status_banner.setObjectName("statusBanner")
        status_layout = QHBoxLayout(self.status_banner)
        status_layout.setContentsMargins(6, 3, 20, 3)  # Leave room for the window size grip.
        status_layout.setSpacing(4)

        self.status_badge = QLabel("● INITIALIZING")
        self.status_badge.setObjectName("statusBadge")

        self.status = QLabel("Generating geometry in background...")
        self.status.setWordWrap(True)
        readout = QWidget()
        readout_layout = QVBoxLayout(readout)
        readout_layout.setContentsMargins(6, 0, 0, 0)
        readout_layout.setSpacing(2)
        status_readout = QHBoxLayout()
        status_readout.setSpacing(4)
        status_readout.addWidget(self.status_badge)
        status_readout.addWidget(self.status, 1)
        readout_layout.addLayout(status_readout)
        readout_layout.addWidget(self.fragment_status)
        readout.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        kpi_row.addWidget(readout, 1)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        from operation_progress import OperationProgress
        self.local_analysis_progress = OperationProgress(self, provider=lambda: (
            self._local_analysis_in_progress(), self.status.text(),
        ), bar_at_end=True)
        status_layout.addWidget(self.local_analysis_progress, 1)
        self._analysis_action_buttons = (cancel, self.apply_button, self.force_stop_button)
        for button in self._analysis_action_buttons:
            status_layout.addWidget(button)
        self.execution_target = NoWheelComboBox()
        self.execution_target.addItem("Local", "local")
        self.execution_target.addItem("TRUBA", "truba")
        self.execution_target.setAccessibleName("Execution target")
        self.execution_target.setToolTip("Execution target")
        self.execution_target.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        status_layout.addWidget(self.execution_target)
        self.operation_progress = OperationProgress(self, provider=lambda: (
            not self._local_analysis_in_progress() and (
                self.future is not None or self.analysis_job is not None
                or getattr(getattr(self, "remote_controller", None), "closing", False)
            ),
            self.status.text(),
        ))
        self.mesh_progress = self.operation_progress.bar
        self.cancel_mesh_button = QPushButton("Cancel meshing")
        self.cancel_mesh_button.setObjectName("secondaryActionBtn")
        self.cancel_mesh_button.clicked.connect(self.cancel_meshing)
        self.cancel_mesh_button.hide()
        progress_layout = QHBoxLayout()
        progress_layout.addStretch(1)
        progress_layout.addWidget(self.cancel_mesh_button)
        model_layout.addLayout(progress_layout)

        bottom_telemetry_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.preview_splitter.addWidget(bottom_telemetry_panel)
        self.preview_splitter.setStretchFactor(0, 1)
        self.preview_splitter.setStretchFactor(1, 0)
        self.preview_splitter.setSizes([800, 32])
        right.addWidget(self.result_preview_controls, 0, Qt.AlignmentFlag.AlignLeft)
        right.addWidget(self.preview_splitter, 1)

        self.doe_studio = DoEStudioWidget(
            settings_provider=self.collect,
            mesh_generator=generate_volume_mesh,
            batch_solver_runner=run_batch_experiment,
            parent=self,
        )
        self.doe_studio.setObjectName("doeWorkspace")
        self.doe_studio.setMinimumSize(220, 140)
        self.doe_studio.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )
        self.doe_studio.preview_requested.connect(self.load_from_doe_row)
        self.doe_studio.boundary_preview_requested.connect(self._show_doe_bounds)
        self.doe_studio.model_requested.connect(self._show_geometry_workspace)
        self.doe_studio.use_model_btn.clicked.connect(self._add_current_design)
        self.doe_window = DoEStudioWindow(self.doe_studio, self)
        self.design_pool = None
        self.analysis_queue_widget = None
        self.analysis_queue_window = None

        self.main_splitter.addWidget(preview_tab)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([300, 1116])
        model_layout.insertWidget(0, self.main_splitter, 1)
        self.workspace_tabs.addTab(model_workspace, "Mechanic")
        # Keep workspace indices stable for existing integrations. Selecting
        # this entry opens the independent window and returns to the model.
        self.workspace_tabs.addTab(QWidget(), "&Experiments (DoE)")
        from flow_workspace import FlowWorkspace
        self.flow_workspace = FlowWorkspace(self.collect, self, preferences=self.solver_profile_store.settings,viewport_factory=create_preview_viewport)
        self.workspace_tabs.addTab(self.flow_workspace, "&Flow (CFD)")
        from thermal_workspace import ThermalWorkspace
        self.thermal_workspace = ThermalWorkspace(self.collect, self, preferences=self.solver_profile_store.settings,viewport_factory=create_preview_viewport)
        self.workspace_tabs.addTab(self.thermal_workspace, "&Thermal (CHT)")
        def sync_workspace_context(index):
            if index == 1:
                self.workspace_tabs.setCurrentIndex(0)
                self._show_doe_workspace()
                return
            self.workspace_button.setText(workspace_names[index])
            self.workspace_actions.actions()[index].setChecked(True)
            flow_active = index in (2,3)
            for button in self._analysis_action_buttons:
                button.setVisible(not flow_active)
            fft_selected = self.setup_tabs.currentIndex() == self._fft_method_index
            if flow_active:
                title = "Transient Heat Transfer" if index == 3 else "Isothermal Flow"
            else:
                title = "FFT Homogenization" if fft_selected else "Nonlinear Compression"
            self.brand_title_label.setText(title)
            unit_badge.setText("Units: mm  °C  s  Pa" if index==3 else "Units: mm  m/s  Pa" if flow_active else "Units: mm  N  MPa")
            remote = getattr(self,"remote_controller",None)
            self.brand_subtitle_lbl.setText("TRUBA · remote compute" if remote is not None and remote.requested
                                            else self._local_workspace_solver_name())
            if remote is not None:
                self._update_remote_banner()
            self.sidebar_toggle.setVisible(not flow_active)
            self.display_mode_group.setVisible(not flow_active and not fft_selected)
            self.show_platens_cb.setVisible(not flow_active and not fft_selected)
            self._sync_preview_readout()
            self.fft_preview_summary.setVisible(fft_selected)
            camera_controls.setVisible(not flow_active)
        self._sync_workspace_context = sync_workspace_context
        self.workspace_tabs.currentChanged.connect(sync_workspace_context)
        self.workspace_tabs.tabBar().hide()
        layout.addWidget(toolbar_frame)
        layout.addWidget(self.workspace_tabs, 1)
        layout.addWidget(self.status_banner)
        model_layout.addWidget(self.operation_progress)

        for button_type in (QPushButton, QToolButton):
            for button in self.findChildren(button_type):
                if not button.objectName():
                    button.setObjectName("secondaryActionBtn")
                if isinstance(button, QPushButton) and button.window() is self:
                    # Enter commits the active field; it must not invoke an
                    # unrelated dialog action (Qt otherwise selects TRUBA).
                    button.setAutoDefault(False)
                    button.setDefault(False)
        for control_type in (QAbstractSpinBox, QComboBox):
            for control in self.findChildren(control_type):
                control.setFixedHeight(CONTROL_HEIGHT)
                if isinstance(control, QAbstractSpinBox):
                    control.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                    control.setAccelerated(True)
                    control.lineEdit().setTextMargins(0, 0, 0, 0)
                    control.setProperty("stepperFamily", "DatenburgStepper")

        self.ensurePolished()
        self.workspace_tabs.setMinimumSize(0, 0)
        preview_tab.setMinimumWidth(
            120 + right.contentsMargins().left() + right.contentsMargins().right()
        )

        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.timeout.connect(self.start_preview)

        self.poll = QTimer(self)
        self.poll.setInterval(80)
        self.poll.timeout.connect(self.poll_preview)
        self.poll.timeout.connect(self._poll_fft_preview)
        self.poll.start()

        self.stl_resolution_input.textChanged.connect(self.geometry_changed)
        for name, control in self.controls.items():
            if name in GEOMETRY_FIELDS:
                control.valueChanged.connect(self.geometry_changed)
            else:
                control.valueChanged.connect(self.refresh_validity)
                if name == "compression":
                    control.valueChanged.connect(self.render_preview)
                if name in (
                    "bottom_platen_friction_coeff",
                    "top_platen_friction_coeff",
                    "self_friction_coeff",
                ):
                    control.valueChanged.connect(self._sync_contact_ui)

        self.finished.connect(self.dispose)
        self.material_editor.changed.connect(self.refresh_validity)
        self.kratos_panel.changed.connect(self.refresh_validity)
        self.kratos_panel.recover_requested.connect(self.retrieve_truba_job)
        self.kratos_panel.view_results_requested.connect(self.show_kratos_results)
        self._kratos_result_dialog=None
        self._latest_kratos_results=self.solver_profile_store.settings.value("kratos/latestResultFolder","") if self._persist_solver_profiles else ""
        self.kratos_panel.view_results_button.setEnabled(bool(self._latest_kratos_results and Path(self._latest_kratos_results).is_dir()))
        self._solver_profiles_ready = True
        self._sync_solver_ui()
        self._sync_contact_ui()
        from remote_controller import RemoteController
        self.remote_controller = RemoteController(self,self.solver_profile_store.settings)
        self.flow_workspace.remote_controller = self.remote_controller
        self.thermal_workspace.remote_controller = self.remote_controller
        self.doe_studio.remote_controller = self.remote_controller
        self.remote_controller.mode_changed.connect(self._remote_mode_changed)
        self.remote_controller.changed.connect(self._remote_state_changed)
        self.remote_controller.close_ready.connect(self.reject)
        self.execution_target.currentIndexChanged.connect(self._select_execution_target)
        self.test_profile_combo.currentIndexChanged.connect(self._test_profile_changed)
        self.architecture_combo.currentIndexChanged.connect(self._architecture_changed)
        self.controls['core_fraction'].valueChanged.connect(self._architecture_changed)
        self._sync_test_profile_ui()
        self.language_manager.changed.connect(self._refresh_ui_language)
        self._refresh_ui_language()
        self.setMinimumWidth(max(900, self.minimumSizeHint().width()))
        QTimer.singleShot(0, self.start_preview)

    def _refresh_ui_language(self, *_):
        from ui_language import translate
        self.language_manager.localize_tree(self)
        if self.setup_tabs.currentIndex() == self._fft_method_index:
            self._update_fft_preview_summary(self.collect())
        elif self.result_preview_field.currentData() is not None:
            self.render_preview()
        menu_buttons = (
            (self.project_button, (self.project_button.text(),)),
            (self.sidebar_toggle, tuple(translate(name) for name in self.method_button_labels)),
            (self.workspace_button, tuple(action.text() for action in self.workspace_menu.actions()
                                          if action.isVisible())),
            (self.designs_button, (self.designs_button.text(),)),
        )
        command_buttons = (self.doe_button, self.folder_batch_button, self.esen_button,
                           *self.display_mode_buttons.buttons())
        # Allow for the menu arrow and the shared 2 px focus border.
        width = max(
            max(button.fontMetrics().horizontalAdvance(label) + 18
                for button, labels in menu_buttons for label in labels),
            max(button.fontMetrics().horizontalAdvance(button.text()) + 8 for button in command_buttons),
        )
        for button, _ in menu_buttons:
            button.setFixedSize(width, CONTROL_HEIGHT)
        for button in command_buttons:
            button.setProperty("managesLocalizedWidth", True)
            button.setFixedSize(width, CONTROL_HEIGHT)
        # Allow the shared button face's focus border to grow by 1 px on each side.
        width = max(button.sizeHint().width() for button in self._analysis_action_buttons) + 2
        for button in self._analysis_action_buttons:
            button.setFixedSize(width, CONTROL_HEIGHT)
        for widget in self.findChildren(QWidget):
            if hasattr(widget, '_action_groups'):
                for buttons in widget._action_groups:
                    widget._size_action_group(*buttons)
            plotter = getattr(widget, 'plotter', None)
            if plotter is not None:
                self.language_manager.localize_plotter(plotter)
        figure = getattr(self.thermal_workspace, 'figure', None)
        self.flow_workspace.size_action_buttons()
        self.thermal_workspace.size_action_buttons()
        if figure is not None:
            self.language_manager.localize_figure(figure)

    def _test_profile_changed(self, *_):
        from standard_tests import apply_profile
        identifier = self.test_profile_combo.currentData()
        current = replace(self.collect(),test_profile=self.active_geometry.test_profile,
            size=self.controls['size'].value(),
            bottom_platen_contact=self.platen_contact_combos['bottom'].currentData(),
            top_platen_contact=self.platen_contact_combos['top'].currentData())
        self._switching_solver = True
        try:
            if self.active_geometry.test_profile == 'custom' and identifier != 'custom':
                self._custom_experiment = replace(current, test_profile='custom', size=self.controls['size'].value())
            selected = (replace(current, **{name:getattr(self._custom_experiment,name) for name in
                         (*GEOMETRY_FIELDS,'compression','loading_mode','applied_force','bottom_platen_contact','top_platen_contact','self_contact_mode')})
                        if identifier == 'custom' else apply_profile(current,identifier))
            if selected.test_profile=='iso178_preferred' and selected.solver_backend=='kratos':
                selected=replace(selected,mesh_type='HEX8')
            self.imported_geometry_path = selected.imported_geometry_path
            self.imported_geometry_name = selected.imported_geometry_name
            self.imported_geometry_height = selected.size if selected.imported_geometry_path else None
            self.import_geometry_label.setText(selected.imported_geometry_name or 'No imported geometry')
            self.stl_resolution_input.setText(
                "auto" if selected.stl_resolution is None else f"{selected.stl_resolution:.12g}")
            for name in ('size','mesh_resolution','local_mesh_size','cell_size','solid_fraction','random_seed','core_fraction','compression'):
                if name=='size':self.controls[name].setMaximum(max(100,selected.size))
                self.controls[name].setValue(getattr(selected,name))
            for combo,value in ((self.architecture_combo,selected.specimen_architecture),
                (self.mesh_type_combo,selected.mesh_type),(self.tetra_strategy_combo,selected.tetra_strategy),
                (self.loading_mode_combo,selected.loading_mode),
                (self.self_contact_combo,selected.self_contact_mode),
                (self.family_combo,selected.structure_family),
                (self.morphology_combo,selected.tpms_morphology),
                *((combo,getattr(selected,f'{side}_platen_contact')) for side,combo in self.platen_contact_combos.items())):
                combo.blockSignals(True)
                combo.setCurrentIndex(max(0,combo.findData(value)))
                combo.blockSignals(False)
            self._populate_structure_items(selected.structure_family,selected.structure_name)
            self._sync_import_controls()
        finally:
            self._switching_solver = False
        self._sync_test_profile_ui()
        self._sync_contact_ui()
        self.apply_geometry()

    def _architecture_changed(self, *_):
        if self._switching_solver:return
        self._sync_test_profile_ui()
        self.apply_geometry()

    def _sync_test_profile_ui(self):
        from standard_tests import profile, kind
        settings=self.collect()
        p=profile(settings)
        active=p is not None
        self.test_profile_combo.setToolTip(p.label if active else 'Özel deney / Custom experiment')
        self._test_form.setRowVisible(self.architecture_combo,active)
        self._test_form.setRowVisible(self.controls['core_fraction'].parentWidget(),
                                      active and settings.specimen_architecture=='porous_centre')
        self.controls['size'].setEnabled(not active and not bool(self.imported_geometry_path))
        self.family_combo.model().item(self.family_combo.findData('imported')).setEnabled(not active)
        self.clear_import_button.setEnabled(not active)
        for identifier in ('solid','porous_centre','porous_all'):
            item=self.architecture_combo.model().item(self.architecture_combo.findData(identifier))
            item.setEnabled(not active or (identifier=='porous_all' if p.kind=='compression' else identifier!='porous_all'))
        porous=not active or settings.specimen_architecture!='solid'
        self.family_combo.setEnabled(porous)
        for widget in (self.structure_combo,self.morphology_combo,
                *(self.controls[name] for name in ('cell_size','solid_fraction','random_seed'))):
            widget.setEnabled(porous and not bool(self.imported_geometry_path))
        if active:
            dims=' × '.join(f'{v:g}' for v in p.dimensions)
            detail=(f'Gauge: {p.gauge_length:g} mm; solid grips.' if p.kind=='tension' else
                    f'Span: {p.span:g} mm; solid support and loading bands.' if p.kind=='flexure' else 'Porous cellular metal; nominal cube profile.')
            self.standard_hint.setText(f'{dims} mm. {detail}\nNominal virtual-test profile; conformity not certified.'+
                ('\nPorous centre: architected research specimen.' if settings.specimen_architecture=='porous_centre' else ''))
            if p.kind=='flexure' and settings.solver_backend=='kratos':
                self.standard_hint.setText(self.standard_hint.text()+'\nKratos: HEX8 pin/roller support lines; no roller indentation or lift-off.')
        else:self.standard_hint.setText('Free geometry and existing compression setup.')
        for combo in self.platen_contact_combos.values():combo.setEnabled(not active)
        self._sync_loading_ui()
        label=self._geometry_form.labelForField(self.controls['size'].parentWidget())
        if label is not None:label.setText('Specimen height' if active else 'Cube size')
        if self.workspace_tabs.currentIndex()==0:
            self.brand_title_label.setText('Nonlinear Compression' if not active else f'Standard {kind(settings).title()}')
            self.setWindowTitle('RustMech | '+self.brand_title_label.text())

    def _select_execution_target(self, _index):
        self.remote_controller.toggle.setChecked(self.execution_target.currentData() == "truba")
        # The controller can reject a change while an analysis is active.
        self._sync_execution_target()

    def _sync_execution_target(self):
        target = "truba" if self.remote_controller.requested else "local"
        with QSignalBlocker(self.execution_target):
            self.execution_target.setCurrentIndex(self.execution_target.findData(target))

    def _remote_mode_changed(self,enabled):
        if enabled:
            self._local_solver_before_remote = self.solver_combo.currentData()
            if self._persist_solver_profiles: self.solver_profile_store.set_active(self._local_solver_before_remote)
        elif getattr(self,"_local_solver_before_remote",None) is not None:
            self.solver_combo.setCurrentIndex(self.solver_combo.findData(self._local_solver_before_remote))
        self._restart_preview_for_mode = self.future is not None
        self.mesh_cancelled.set()
        self._analysis_after_mesh = None
        self.debounce.stop()
        self.preview = None
        self.failed_key = None
        if not enabled and self.future is None:
            QTimer.singleShot(0,self.start_preview)

    def _remote_state_changed(self):
        controller = self.remote_controller
        self._sync_execution_target()
        controller.apply_solver_combo(self.solver_combo)
        self._sync_solver_ui()
        self.flow_workspace.update_execution_availability()
        self.thermal_workspace.update_execution_availability()
        self.refresh_validity()
        if controller.requested:
            self.brand_subtitle_lbl.setText("TRUBA · remote compute")
        else:
            self.brand_subtitle_lbl.setText(self._local_workspace_solver_name())
        self._update_remote_banner()

    def _update_remote_banner(self):
        controller = getattr(self, 'remote_controller', None)
        if controller is None:
            return
        session = controller.session or {}
        allocated = bool(controller.requested and controller.session_watch
                         and session.get('state') == 'RUNNING' and session.get('job_id'))
        verified = allocated and controller.ready
        state = 'local'
        if controller.closing:
            state, text = 'pending', 'TRUBA · saving work for reconnection…'
        elif verified:
            partition = session.get('allocated_partition') or session.get('context', {}).get('partition', '')
            state, text = 'ready', f'TRUBA · READY · {partition}'
        elif allocated:
            state = 'allocated'
            if not session.get('ready'):
                detail = 'session worker starting'
            elif controller.future is not None:
                detail = controller.runtime_stage or 'checking runtime'
            elif controller.capabilities is None:
                detail = 'runtime not verified'
            else:
                detail = 'runtime unavailable · see runtime details'
            text = f'TRUBA · RUNNING · {detail}'
        elif controller.requested:
            state, text = 'pending', f"TRUBA · {session.get('state', 'checking runtime')}"
        elif session.get('ready') and controller.session_watch:
            state, text = 'pending', 'Local mode · TRUBA resources reserved'
        else:
            local_name = self._local_workspace_solver_name()
            text = f'SSH connected · {local_name}' if self.truba_dialog.connected else local_name
        self.brand_subtitle_lbl.setText(text)
        self.brand_header.setToolTip(
            f"{text}\nJob: {session.get('job_id', '—')} · Nodes: {session.get('allocated_nodes', '—')}\n"
            f"Remaining: {session.get('remaining', '—')}\nApply explicitly starts remote generation when ready.")
        if allocated and not verified:
            self.brand_header.setToolTip(self.brand_header.toolTip() + '\n' + controller.status.text())
        if self.brand_header.property('computeState') != state:
            self.brand_header.setProperty('computeState', state)
            self.brand_header.style().unpolish(self.brand_header)
            self.brand_header.style().polish(self.brand_header)
            self.brand_header.update()

    def show_esen_workspace(self):
        from esen_workspace import EsenWorkspace
        from window_state import show_restored_window

        if self.esen_workspace is None:
            self.esen_workspace = EsenWorkspace(self.truba_dialog, self)
        show_restored_window(self.esen_workspace)

    def show_about(self):
        QMessageBox.about(
            self,
            "About RustMech",
            "<p><b>RustMech</b></p>"
            "<p>Desktop workspace for mechanical, flow, and thermal simulation.</p>"
            "<p>Licensed under GPL-3.0-or-later.</p>"
            '<p><a href="https://github.com/aserdargl/RustMech">RustMech on GitHub</a></p>',
        )

    def _update_truba_button(self, connected: bool) -> None:
        self.truba_button.setText("Connected" if connected else "TRUBA")
        self.truba_button.setProperty("trubaConnected", connected)
        self.truba_button.style().unpolish(self.truba_button)
        self.truba_button.style().polish(self.truba_button)
        self._update_remote_banner()

    def _add_setup_page(self, title: str) -> QVBoxLayout:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        # Reserve the scrollbar gutter even when sections fit vertically.
        # Collapsing sections must not widen controls or reflow their labels.
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        fields = QWidget()
        fields.setObjectName("scrollFields")
        layout = QVBoxLayout(fields)
        layout.setContentsMargins(0, 1, 1, 1)
        layout.setSpacing(3)
        scroll.setWidget(fields)
        self.setup_tabs.addTab(scroll, title)
        return layout

    def _add_method_pages(self) -> None:
        self.method_names = ["Finite Element"]
        self.method_button_labels = self.method_names.copy()
        methods = (
            ("Voxel", "Geo. Voxel", "Geometry-trimmed voxel mesh", "Clip boundary voxels against the reference geometry. Cut-cell element types, small cells and ANSYS compatibility require validation."),
            ("FFT", "FFT", "FFT Homogenization", "Effective stiffness from periodic unit-cell tests."),
            ("MPM", "MPM", "Material Point Method", "Large-deformation platen compression and impact."),
            ("Peridynamics", "Peridynamics", "Peridynamics", "Crack initiation and propagation in mechanical tests."),
        )
        for tab, button_label, name, description in methods:
            self.method_names.append(name)
            self.method_button_labels.append(button_label)
            fields = self._add_setup_page(tab)
            self.setup_tabs.setTabToolTip(self.setup_tabs.count() - 1, name)
            heading = QLabel(name)
            heading.setObjectName("sectionSubheader")
            heading.setWordWrap(True)
            hint = QLabel(description if tab == "FFT" else f"Planned\n{description}")
            hint.setObjectName("mutedHintLabel")
            hint.setWordWrap(True)
            fields.addWidget(heading)
            fields.addWidget(hint)
            if tab == "FFT":
                self._fft_method_index = self.setup_tabs.count() - 1
                self._add_fft_controls(fields)
            fields.addStretch()

    def _add_fft_controls(self, fields) -> None:
        preferences = self.solver_profile_store.settings
        self.fft_grid = CompactSpinBox()
        self.fft_grid.setRange(MIN_GRID, (1 << 31) - 1)  # QSpinBox's native integer range.
        self.fft_grid.setValue(preferences.value("fft/grid", 24, type=int))
        self.fft_grid.valueChanged.connect(self.refresh_validity)
        self.fft_void_ratio = CompactDoubleSpinBox()
        self.fft_void_ratio.setRange(0.0001, 0.1)
        self.fft_void_ratio.setSingleStep(0.001)
        # Read the saved decimal directly to avoid Qt's single-precision conversion.
        try:
            void_ratio = float(preferences.value("fft/voidRatio", "0.01", type=str))
        except (TypeError, ValueError):
            void_ratio = 0.01
        self.fft_void_ratio.setValue(void_ratio if np.isfinite(void_ratio) else 0.01)
        self.fft_void_ratio.valueChanged.connect(self.refresh_validity)
        self.fft_distribution = QLineEdit(preferences.value("fft/distribution", "Ubuntu", type=str))
        self.fft_distribution.setEnabled(os.name == "nt")
        form = QFormLayout()
        configure_form(form)
        for title, control, tooltip in (
            ("Voxels per axis", self.fft_grid, ""),
            (
                "Void / solid stiffness",
                self.fft_void_ratio,
                "Ratio of the Young’s modulus assigned to void voxels to that of the solid "
                "(Eᵥ/Eₛ). For example, 0.01 gives the voids 1% of the solid stiffness. "
                "Lower values better approximate empty space but can make the FFT solver "
                "harder to converge. Higher values can overestimate the cell’s effective stiffness.",
            ),
            ("WSL distribution", self.fft_distribution, ""),
        ):
            form.addRow(field_label(title, control, tooltip=tooltip), control)
        # Reserve room for the full precision of a saved stiffness ratio.
        ratio_width = self.fft_void_ratio.fontMetrics().horizontalAdvance("0.000000000000") + 22
        self.fft_void_ratio.setFixedWidth(max(self.fft_void_ratio.width(), ratio_width))
        fields.addLayout(form)
        note = QLabel("The void phase has the specified fraction of the solid stiffness.")
        note.setObjectName("mutedHintLabel")
        note.setWordWrap(True)
        fields.addWidget(note)

        # Seed new FFT drafts from the startup material once, then keep them independent.
        material = self.material_editor.material_settings()
        if self._persist_solver_profiles:
            try:
                saved_material = json.loads(preferences.value("fft/solidMaterial", "null", type=str))
                if saved_material is not None:
                    material = settings_from_dict(saved_material)
            except (TypeError, ValueError):
                pass
        self.fft_material_editor = MaterialEditor(material, library_settings=preferences, elastic_only=True)
        self.fft_material_editor.changed.connect(self.refresh_validity)
        for key in ("young_modulus", "poisson_ratio"):
            self.fft_material_editor.controls[key].valueChanged.connect(self.refresh_validity)
        fields.addWidget(self.fft_material_editor)

        self.fft_matrix = QPlainTextEdit()
        self.fft_matrix.setReadOnly(True)
        self.fft_matrix.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        matrix_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        matrix_font.setPointSize(8)
        self.fft_matrix.setFont(matrix_font)
        self.fft_matrix.setAccessibleName("Effective stiffness matrix (MPa)")
        self.fft_matrix.setFixedHeight(128)
        self.fft_matrix.hide()
        self.fft_result_heading = QLabel("Last completed FFT result")
        self.fft_result_heading.setObjectName("sectionSubheader")
        self.fft_result_heading.hide()
        fields.addWidget(self.fft_result_heading)
        self.fft_result_summary = QLabel()
        self.fft_result_summary.setWordWrap(True)
        fields.addWidget(self.fft_result_summary)
        self.fft_result_summary.hide()
        fields.addWidget(self.fft_matrix)
        self.fft_results_button = QPushButton("Open FFT results")
        self.fft_results_button.setAutoDefault(False)
        self.fft_results_button.hide()
        self.fft_results_button.clicked.connect(self._open_fft_results)
        fields.addWidget(self.fft_results_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.fft_result_folder = None
        if self._persist_solver_profiles:
            saved = preferences.value("fft/latestResultFolder", "", type=str)
            if saved:
                try:
                    folder = Path(saved)
                    result = json.loads((folder / "status.json").read_text(encoding="utf-8"))
                    if result["state"] == "complete":
                        self._show_fft_results(result, folder)
                except (OSError, ValueError, TypeError, KeyError):
                    pass

    def _open_fft_results(self):
        if self.fft_result_folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.fft_result_folder)))

    def _fft_settings(self, settings: Settings) -> Settings:
        material = self.fft_material_editor.material_settings()
        values = {name: getattr(material, name) for name in MATERIAL_FIELDS}
        values.update(material_model="linear_elastic", hardening_curve=())
        return replace(settings, **values)

    def _show_fft_results(self, result, folder):
        labels = "    " + "".join(f"{label:>8}" for label in COMPONENTS)
        rows = [
            f"{label:>4}" + "".join(f" {value:7.3g}" for value in row)
            for label, row in zip(COMPONENTS, result["stiffness_mpa"])
        ]
        self.fft_matrix.setPlainText("\n".join([labels, *rows]))
        geometry = (result.get("settings") or {}).get("structure_name")
        cell = f"{geometry} · " if geometry else ""
        self.fft_result_summary.setText(
            f"{cell}{result['grid']}³ · ϕ = {result['solid_volume_fraction']:.1%} · "
            f"Eᵥ/Eₛ = {result['void_stiffness_ratio']:.4g} · MPa"
        )
        self.fft_result_folder = folder
        self.fft_result_heading.show()
        self.fft_result_summary.show()
        self.fft_matrix.show()
        self.fft_results_button.show()
        if self._persist_solver_profiles:
            self.solver_profile_store.settings.setValue("fft/latestResultFolder", str(folder))

    def _select_settings_method(self, index: int) -> None:
        self.workspace_tabs.setCurrentIndex(0)
        self.setup_tabs.setCurrentIndex(index)
        sizes = self.main_splitter.sizes()
        if sizes and sizes[0] == 0:
            self.main_splitter.toggle_sidebar()

    def _sync_settings_method(self, index: int) -> None:
        from ui_language import translate
        self.sidebar_toggle.setText(translate(self.method_button_labels[index]))
        self.sidebar_toggle.setAccessibleName(translate(self.method_names[index]))
        for action in self.method_actions.actions():
            action.setChecked(action.data() == index)
        if hasattr(self, "remote_controller"):
            was_fft = not self.fft_preview_summary.isHidden()
            is_fft = index == self._fft_method_index
            if index != 0:
                self._analysis_after_mesh = None
            if self.workspace_tabs.currentIndex() == 0:
                self._sync_workspace_context(0)
            self.refresh_validity()
            if was_fft != is_fft:
                self._fft_preview_size = None
                if is_fft or self.preview is not None or self.result_preview_field.currentData() is not None:
                    self.render_preview(reset=True)
                else:
                    self.plotter.clear()
                    self.plotter.render()

    def _local_workspace_solver_name(self):
        if self.workspace_tabs.currentIndex() in (2, 3):
            return "OpenFOAM · WSL"
        if self.setup_tabs.currentIndex() == self._fft_method_index:
            return "DAMASK · FFT"
        return self.solver_capabilities.display_name

    def _update_status_visuals(
        self, state: str, badge_text: str, pending_geom: bool = False
    ) -> None:
        self.status_banner.setProperty("state", state)
        self.status_badge.setProperty("state", state)
        self.status_badge.setText(badge_text)
        self.geometry_apply_button.setProperty(
            "pending", "true" if pending_geom else "false"
        )
        self.structure_apply_button.setProperty(
            "pending", "true" if pending_geom else "false"
        )
        self.mesh_apply_button.setProperty(
            "pending", "true" if pending_geom else "false"
        )
        for widget in (
            self.status_banner,
            self.status_badge,
            self.geometry_apply_button,
            self.structure_apply_button,
            self.mesh_apply_button,
        ):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def _set_perspective(self, enabled: bool) -> None:
        if self.disposed:
            return
        if hasattr(self.plotter, "_camera_transition"):
            self.plotter._camera_transition.stop()
        if enabled:
            self.plotter.disable_parallel_projection()
        else:
            self.plotter.enable_parallel_projection()
        self.perspective_button.setText("Perspective")
        self.plotter.render()

    def _set_camera_preset(self, plane: str) -> None:
        if hasattr(self.plotter, "_camera_transition"):
            self.plotter._camera_transition.stop()
        if self.disposed:
            return
        negative = plane.startswith("-")
        plane = plane.lstrip("-")
        if plane == "xy":
            self.plotter.view_xy(negative=negative)
        elif plane == "xz":
            self.plotter.view_xz(negative=negative)
        elif plane == "yz":
            self.plotter.view_yz(negative=negative)
        elif plane == "iso":
            self.plotter.view_isometric()
        else:
            return
        self.orthographic_button.setChecked(True)
        self.plotter.enable_parallel_projection()
        self.plotter.reset_camera()
        self.plotter.render()

    def add_group(
        self, layout, title: str, settings: Settings, specs, *, units=None
    ) -> QFormLayout:
        group = SidebarGroupBox(title)
        group.setObjectName("studioGroupCard")
        form = QFormLayout(group.content)
        form.setContentsMargins(1, 4, 1, 3)
        configure_form(form)
        for name, label, low, high, step, decimals in specs:
            control = (
                NumericInputBox()
                if name in ("size", "cell_size", "solid_fraction", "mesh_resolution", "local_mesh_size")
                else NoWheelSpinBox() if decimals is None else NoWheelDoubleSpinBox()
            )
            if name == "cores":
                control.setRange(low, high)
            else:
                control.setRange(
                    min(low, getattr(settings, name)),
                    max(high, getattr(settings, name)),
                )
            control.setSingleStep(step)
            control.setValue(getattr(settings, name))
            control.setKeyboardTracking(False)
            self.controls[name] = control
            row_lbl = field_label(label, control, tooltip=GEOMETRY_HELP.get(name, ""))
            unit = units.get(name) if units else None
            form.addRow(row_lbl, stepper_with_unit(control, unit) if unit else control)
        layout.addWidget(group)
        return form

    def _populate_structure_items(
        self, family_key: str, selected_name: str = ""
    ) -> None:
        self.structure_combo.blockSignals(True)
        self.structure_combo.clear()
        items = ALL_STRUCTURES_BY_FAMILY.get(family_key, ALL_STRUCTURES_BY_FAMILY["tpms"])
        for item in items:
            self.structure_combo.addItem(item, item)
        idx = self.structure_combo.findData(selected_name)
        self.structure_combo.setCurrentIndex(max(0, idx))
        self.structure_combo.blockSignals(False)

    def _on_family_changed(self, *_) -> None:
        family_key = str(self.family_combo.currentData() or "tpms")
        if family_key == 'imported':
            self._sync_import_controls()
            return
        self._generated_family = family_key
        had_import = bool(self.imported_geometry_path)
        self.imported_geometry_path = ''
        self.imported_geometry_name = ''
        self.imported_geometry_height = None
        self._populate_structure_items(family_key)
        self._sync_import_controls()
        self._sync_structure_ui_visibility()
        self.geometry_changed()
        if had_import:
            self.apply_geometry()

    def _on_structure_selection_changed(self, *_) -> None:
        self._sync_structure_ui_visibility()
        self.geometry_changed()

    def _sync_structure_ui_visibility(self) -> None:
        family_key = self._generated_family
        is_porous = family_key == "porous"
        is_lattice = family_key == "lattice"
        if "random_seed" in self.controls:
            self._geometry_form.setRowVisible(self.controls["random_seed"], is_porous)
        self._geometry_form.setRowVisible(self.morphology_combo, not is_lattice)
        if is_lattice:
            self.morphology_combo.blockSignals(True)
            self.morphology_combo.setCurrentIndex(
                max(0, self.morphology_combo.findData("sheet"))
            )
            self.morphology_combo.blockSignals(False)
        self.structure_hint_label.setText(
            describe_structure(
                Settings(
                    structure_family=family_key,
                    structure_name=str(self.structure_combo.currentData() or "Gyroid"),
                    tpms_morphology=str(self.morphology_combo.currentData() or "sheet"),
                    random_seed=int(self.controls["random_seed"].value())
                    if "random_seed" in self.controls
                    else 42,
                )
            )
        )
        self.structure_combo.setToolTip(self.structure_hint_label.text())

    def _on_solver_changed(self, *_) -> None:
        if self._switching_solver:
            return
        solver_id = str(self.solver_combo.currentData() or "ansys_mapdl")
        current = self.collect()
        self._solver_profiles[self._active_solver_id] = profile_values(
            replace(current, solver_backend=self._active_solver_id))
        if self._persist_solver_profiles:
            self.solver_profile_store.save(self._active_solver_id, self._solver_profiles[self._active_solver_id])
        if (
            self._custom_capabilities is not None
            and solver_id == self._custom_capabilities.identifier
        ):
            self.solver_capabilities = self._custom_capabilities
        else:
            self.solver_capabilities = get_solver_capabilities(solver_id)
        self._active_solver_id = solver_id
        if self._persist_solver_profiles and not (getattr(self,"remote_controller",None) is not None and self.remote_controller.requested):
            self.solver_profile_store.set_active(solver_id)
        self._switching_solver = True
        try:
            selected = self.solver_profile_store.load(solver_id, current)
            if solver_id in self._solver_profiles:
                selected = replace(selected, **self._solver_profiles[solver_id])
            self._apply_solver_profile(selected)
        finally:
            self._switching_solver = False
        self._sync_solver_ui()
        self._sync_contact_ui()
        self._sync_loading_ui()
        self._sync_mesh_type()
        self._sync_test_profile_ui()
        self.refresh_validity()

    def _restore_substeps_mode(self, settings):
        defaults = Settings()
        advanced = (settings.substeps != defaults.substeps
                    or settings.substep_limits != defaults.substep_limits)
        self.substeps_mode_combo.setCurrentIndex(1 if advanced else 0)
        self.substeps_advanced.setVisible(advanced)

    def _substeps_mode_changed(self, *_):
        self.substeps_advanced.setVisible(self.substeps_mode_combo.currentData() == "advanced")
        if getattr(self, "_solver_profiles_ready", False):
            self.refresh_validity()

    def _apply_solver_profile(self, settings):
        combos = (
            (self.mesh_type_combo, settings.mesh_type),
            (self.tetra_strategy_combo, settings.tetra_strategy),
            (self.loading_mode_combo, settings.loading_mode),
            (self.self_contact_combo, settings.self_contact_mode),
            (self.contact_formulation_combo, settings.contact_formulation),
            *((combo, getattr(settings, f"{side}_platen_contact"))
              for side, combo in self.platen_contact_combos.items()),
        )
        widgets = list(self.controls.values()) + [combo for combo, _ in combos] + [self.automatic_contact, self.automatic_cores]
        blocked = [(widget, widget.blockSignals(True)) for widget in widgets]
        try:
            self.controls["cores"].setRange(1, self.solver_capabilities.max_cores)
            for name in profile_values(settings):
                if name in self.controls and getattr(settings, name) is not None:
                    self.controls[name].setValue(getattr(settings, name))
            minimum, maximum = settings.substep_limits
            self.controls["minimum_substeps"].setValue(minimum)
            self.controls["maximum_substeps"].setValue(maximum)
            for combo, value in combos:
                combo.setCurrentIndex(max(0, combo.findData(value)))
            automatic = settings.automatic_contact_parameters
            self.automatic_contact.setChecked(True if automatic is None else automatic)
            automatic = settings.automatic_cores
            self.automatic_cores.setChecked(settings.cores == self.solver_capabilities.default_cores if automatic is None else automatic)
            self._manual_cores = settings.cores
            self._manual_contact_values = None
            self.controls["cores"].setEnabled(not self.automatic_cores.isChecked())
            self._restore_substeps_mode(settings)
            self.kratos_panel.apply_settings(settings)
        finally:
            for widget, previous in blocked:
                widget.blockSignals(previous)

    def _sync_solver_ui(self) -> None:
        cap = self.solver_capabilities
        self.brand_subtitle_lbl.setText(self._local_workspace_solver_name())
        if "cores" in self.controls:
            cores_spin = self.controls["cores"]
            cores_spin.blockSignals(True)
            cores_spin.setRange(1, cap.max_cores)
            if cores_spin.value() > cap.max_cores:
                cores_spin.setValue(cap.default_cores)
            cores_spin.blockSignals(False)
            cores_spin.setToolTip(cap.core_limit_description)
        hint_text = cap.ui_hint or cap.core_limit_description
        if not cap.implemented and cap.unavailable_reason:
            hint_text = f"{hint_text}\n{cap.unavailable_reason}"
        self.solver_hint_label.setText(hint_text)
        kratos = cap.identifier == "kratos"
        self.kratos_panel.setVisible(kratos)
        for index in range(self.mesh_type_combo.count()):
            mesh_type = self.mesh_type_combo.itemData(index)
            remote = getattr(self,"remote_controller",None)
            available = mesh_type in cap.supported_mesh_types
            if kratos and self.test_profile_combo.currentData()=='iso178_preferred':available=mesh_type=='HEX8'
            if remote is not None and remote.requested:
                available = remote.ready and mesh_type in remote.capabilities.get("mesh_types",[])
                self.mesh_type_combo.setItemData(index,
                    "Remote mesh preview is supported; Kratos solves HEX8/TET4 only."
                    if available and mesh_type not in cap.supported_mesh_types else "",
                    Qt.ItemDataRole.ToolTipRole)
            if kratos and self.test_profile_combo.currentData()=='iso178_preferred' and mesh_type!='HEX8':available=False
            self.mesh_type_combo.model().item(index).setEnabled(available)
        for combo in self.platen_contact_combos.values():
            for index in range(combo.count()):
                tension=self.test_profile_combo.currentData()=='astm_e8_subsize'
                combo.model().item(index).setEnabled(not kratos or combo.itemData(index) in (("bonded",) if tension else ("frictional", "frictionless")))
        for index in range(self.self_contact_combo.count()):
            self.self_contact_combo.model().item(index).setEnabled(not kratos or self.self_contact_combo.itemData(index) in ('disabled','frictionless','frictional'))
        self.substeps_status.setText("Automatic")
        if hasattr(self, "_loading_form"):
            self._loading_form.labelForField(self._substeps_row).setText("Load stepping" if kratos else "Substeps")
        for name, prefix in (("substeps", "Initial"), ("minimum_substeps", "Minimum"), ("maximum_substeps", "Maximum")):
            field = self.controls[name]
            field.parentWidget().layout().labelForField(field).setText(f"{prefix} {'increments' if kratos else 'substeps'}")
        material_index = self.material_editor.model.findData("multilinear_isotropic")
        self.material_editor.model.setItemText(material_index, "Tabulated isotropic hardening" if kratos else "True stress–strain (MISO)")
        self._solver_group.setTitle("Kratos · loading" if kratos else "Solver and loading")
        if hasattr(self, "physics_note"):
            self.physics_note.setText(
                "Kratos · nonlinear static structural mechanics\n"
                "Shared specimen material and geometry · solver-specific mesh and numerical settings\n"
                "Frictional / frictionless platens · HEX8 / TET4 · HEX8 penalty self-contact"
                if kratos else
                "• Bottom platen at Z = 0 · Top platen at Z = L (−Z compression)\n"
                "• Rigid platen contact with separate Coulomb friction coefficients\n"
                "• Configurable 3D Cell-Wall Self-Contact & Internal Friction (μ_self)\n"
                "• Large displacement (NLGEOM) · voxel HEX8/HEX20 or surface-conforming TET4/TET10")
        if hasattr(self, "automatic_cores") and self.automatic_cores.isChecked():
            self.controls["cores"].setValue(self._automatic_core_count())

    def _automatic_core_count(self):
        count = self.solver_capabilities.default_cores
        remote = getattr(self, "remote_controller", None)
        if remote is not None and remote.requested:
            try:
                count = min(count, remote.context().cores)
            except (ValueError, RuntimeError):
                pass  # Configuration validation explains the unavailable allocation.
        return count

    def _on_contact_changed(self, *_) -> None:
        if self.solver_capabilities.identifier=='kratos' and self.self_contact_combo.currentData()!='disabled':
            method=self.kratos_panel.fields['kratos_contact_method']
            method.setCurrentIndex(method.findData('penalty'))
        self._sync_contact_ui()
        self.refresh_validity()

    def _sync_contact_ui(self) -> None:
        kratos = self.solver_capabilities.identifier == "kratos"
        sc_mode = str(self.self_contact_combo.currentData() or "frictional")
        needs_self_mu = sc_mode == "frictional"
        kinematic=hasattr(self,'test_profile_combo') and self.test_profile_combo.currentData() in ('astm_e8_subsize','iso178_preferred')
        fixed=kratos and (sc_mode!='disabled' or kinematic)
        if hasattr(self,'substeps_advanced'):
            self.substeps_status.setText('Fixed increments' if fixed else 'Automatic')
            self.substeps_status.setToolTip('Kinematic test fixtures and self-contact use the prescribed number of increments; increase increments if convergence fails.' if fixed else 'The solver adapts the load increment to convergence.')
            form=self.substeps_advanced.layout()
            form.labelForField(self.controls['substeps']).setText('Increments' if fixed else 'Initial increments' if kratos else 'Initial substeps')
            for key in ('minimum_substeps','maximum_substeps'):form.setRowVisible(self.controls[key],not fixed)
        if hasattr(self,'kratos_panel'):
            method=self.kratos_panel.fields['kratos_contact_method']
            method.model().item(method.findData('alm')).setEnabled(not kratos or sc_mode=='disabled')

        if "self_friction_coeff" in self.controls:
            self._contact_form.setRowVisible(
                self.controls["self_friction_coeff"], needs_self_mu
            )
        for side, combo in self.platen_contact_combos.items():
            self.controls[f"{side}_platen_friction_coeff"].setEnabled(combo.currentData() == "frictional")
        for key in (
            "normal_contact_stiffness_factor",
            "penetration_tolerance_factor",
            "contact_damping_factor",
        ):
            if key in self.controls:
                self._contact_form.setRowVisible(
                    self.controls[key], not kratos and not self.automatic_contact.isChecked()
                )
        self._contact_form.setRowVisible(self.controls["platen_penetration_tolerance_factor"],
                                         not kratos and not self.automatic_contact.isChecked())
        self._contact_form.setRowVisible(self.contact_formulation_combo,
                                         not kratos and not self.automatic_contact.isChecked())
        self._contact_form.setRowVisible(self.automatic_contact, not kratos)

        mu_s = (
            float(self.controls["self_friction_coeff"].value())
            if "self_friction_coeff" in self.controls
            else 0.15
        )
        self_desc = SELF_CONTACT_MODES.get(sc_mode, sc_mode)
        if needs_self_mu:
            self_desc += f" (μ_self = {mu_s:.2f}" + (", mortar contact)" if kratos else ", CONTA174/TARGE170)")
        self.contact_hint_label.setText(
            "Rigid platens: bottom fixed, top prescribed in −Z. "
            "Set either friction coefficient to 0 for frictionless contact.\n"
            f"Internal walls: {self_desc}"
        )
        if hasattr(self,'test_profile_combo') and self.test_profile_combo.currentData() != 'custom':
            from standard_tests import PROFILES
            p=PROFILES[self.test_profile_combo.currentData()]
            self.contact_hint_label.setText(('Bonded end grips; upper grip moves in +Z.' if p.kind=='tension' else
                'Two fixed cylindrical supports; central loading nose moves in −Z.' if p.kind=='flexure' else
                'Fixed bottom platen; top platen moves in −Z.')+f'\nInternal walls: {self_desc}')
            if kratos and p.kind=='flexure':
                self.contact_hint_label.setText('Idealized support/loading lines (HEX8). Contact method applies to self-contact.\n'+self_desc)

    def _on_loading_changed(self, *_):
        self._sync_loading_ui()
        self.refresh_validity()

    def _sync_loading_ui(self):
        force = self.loading_mode_combo.currentData() == "force"
        compression_row = self.controls["compression"].parentWidget()
        self._loading_form.setRowVisible(compression_row, not force)
        self._loading_form.setRowVisible(self.controls["applied_force"].parentWidget(), force)
        self.loading_hint_label.setText(
            "Top platen force in −Z; displacement is calculated. Force control may stop at buckling."
            if force else "Top platen displacement in −Z; reaction force is calculated (N).")
        active=hasattr(self,'test_profile_combo') and self.test_profile_combo.currentData() != 'custom'
        if active:
            from standard_tests import kind
            from standard_tests import PROFILES
            experiment=PROFILES[self.test_profile_combo.currentData()].kind
            direction='+Z' if experiment=='tension' else '−Z'
            self.loading_hint_label.setText(f'{experiment.title()}: actuator {"force" if force else "travel"} in {direction}. Geometry and fixtures are automatic.')
        label=self._loading_form.labelForField(compression_row)
        if label is not None:label.setText('Actuator travel' if active else 'Compression')

    def _automatic_contact_changed(self, *_):
        automatic = self.automatic_contact.isChecked()
        if automatic:
            if self._manual_contact_values is None:
                self._manual_contact_values = (
                    {key: self.controls[key].value() for key in CONTACT_NUMERICAL_FIELDS},
                    self.contact_formulation_combo.currentData())
            defaults = Settings()
            values = {key: getattr(defaults, key) for key in CONTACT_NUMERICAL_FIELDS}
            algorithm = defaults.contact_formulation
        elif self._manual_contact_values is not None:
            values, algorithm = self._manual_contact_values
            self._manual_contact_values = None
        else:
            self._sync_contact_ui()
            return
        for key, value in values.items():
            self.controls[key].setValue(value)
        self.contact_formulation_combo.setCurrentIndex(self.contact_formulation_combo.findData(algorithm))
        self._sync_contact_ui()
        if hasattr(self, "apply_button"):
            self.refresh_validity()

    def _automatic_cores_changed(self, *_):
        control = self.controls["cores"]
        if self.automatic_cores.isChecked():
            self._manual_cores = control.value()
            control.setValue(self._automatic_core_count())
        else:
            control.setValue(self._manual_cores)
        control.setEnabled(not self.automatic_cores.isChecked())
        if hasattr(self, "apply_button"):
            self.refresh_validity()

    def open_mesh_convergence(self) -> None:
        """Open a mesh study using the current design and analysis settings."""
        from mesh_convergence import MeshConvergenceDialog

        dialog = MeshConvergenceDialog(
            self.collect(), generate_volume_mesh, run_batch_experiment, parent=self,
        )
        try:
            dialog.exec()
            if dialog.selected_settings is not None:
                for name in ("mesh_resolution", "local_mesh_size"):
                    control = self.controls[name]
                    value = getattr(dialog.selected_settings, name)
                    control.setRange(min(control.minimum(), value), max(control.maximum(), value))
                    control.setValue(value)
                selected = dialog.selected_settings
                for name in ANALYSIS_FIELDS:
                    if name in self.controls:
                        self.controls[name].setValue(getattr(selected, name))
                for combo, value in (
                    (self.loading_mode_combo, selected.loading_mode),
                    (self.solver_combo, selected.solver_backend),
                    (self.self_contact_combo, selected.self_contact_mode),
                    (self.contact_formulation_combo, selected.contact_formulation),
                    *((combo, getattr(selected, f"{side}_platen_contact"))
                      for side, combo in self.platen_contact_combos.items()),
                ):
                    combo.setCurrentIndex(max(0, combo.findData(value)))
                self.geometry_changed()
        finally:
            dialog.study.dispose()
            dialog.deleteLater()

    def _stl_resolution_value(self):
        text = self.stl_resolution_input.text().strip()
        if not text or text.lower() == "auto":
            return None
        try:
            return float(text.replace(",", "."))
        except ValueError:
            return float("nan")

    def _fill_auto_stl_resolution(self, *_):
        value = self.collect().automatic_surface_resolution
        self.stl_resolution_input.setText(f"{value:.12g}")

    def collect(self, *, draft=False) -> Settings:
        stl_resolution = self._stl_resolution_value()
        if draft and stl_resolution is not None and not np.isfinite(stl_resolution):
            stl_resolution = None
        settings=Settings(
            stl_resolution=stl_resolution,
            test_profile=self.test_profile_combo.currentData(),
            specimen_architecture=self.architecture_combo.currentData(),
            imported_geometry_path=self.imported_geometry_path,
            imported_geometry_name=self.imported_geometry_name,
            imported_geometry_role=self.imported_geometry_role,
            automatic_contact_parameters=self.automatic_contact.isChecked(),
            automatic_cores=self.automatic_cores.isChecked(),
            loading_mode=self.loading_mode_combo.currentData(),
            bottom_platen_contact=self.platen_contact_combos["bottom"].currentData(),
            top_platen_contact=self.platen_contact_combos["top"].currentData(),
            structure_family=self._generated_family,
            structure_name=str(self.structure_combo.currentData() or "Gyroid"),
            tpms_morphology=str(self.morphology_combo.currentData() or "sheet"),
            solver_backend=str(self.solver_combo.currentData() or "ansys_mapdl"),
            self_contact_mode=str(
                self.self_contact_combo.currentData() or "frictional"
            ),
            contact_formulation=str(
                self.contact_formulation_combo.currentData()
                or "augmented_lagrange"
            ),
            mesh_type=self.mesh_type_combo.currentData(),
            tetra_strategy=self.tetra_strategy_combo.currentData(),
            **{name: control.value() for name, control in self.controls.items()
               if name not in self.material_editor.controls},
            **self.material_editor.numeric_values(),
            **self.material_editor.extra_values(),
            **self.kratos_panel.values(),
        )
        if self.substeps_mode_combo.currentData() == "automatic":
            settings = replace(settings, substeps=Settings().substeps,
                               minimum_substeps=None, maximum_substeps=None)
        if self.imported_geometry_path:
            settings = replace(settings, size=self.imported_geometry_height)
        if draft:
            return settings
        if settings.test_profile != 'custom':
            from standard_tests import profile
            p=profile(settings)
            settings=replace(settings,size=p.dimensions[2],
                bottom_platen_contact='bonded' if p.kind=='tension' else 'frictionless',
                top_platen_contact='bonded' if p.kind=='tension' else 'frictionless')
        if settings.solver_backend=='kratos' and (settings.self_contact_mode!='disabled' or settings.test_profile in ('astm_e8_subsize','iso178_preferred')):
            return replace(settings,minimum_substeps=None,maximum_substeps=None)
        return settings

    def import_geometry(self):
        from geometry_import_dialog import choose_geometry
        try:
            result = choose_geometry(self)
            if result is None:
                return
            path, height, name = result
            self.imported_geometry_path = path
            self.imported_geometry_name = name
            self.imported_geometry_role = "compression"
            self.imported_geometry_height = height
            self.import_geometry_label.setText(name + ' | support / compression faces selected')
            size_control = self.controls['size']
            size_control.setRange(min(size_control.minimum(), height), max(size_control.maximum(), height))
            size_control.setValue(height)
            size_control.setEnabled(False)
            self._sync_import_controls()
            self.mesh_type_combo.setCurrentIndex(self.mesh_type_combo.findData('TET4'))
            self.tetra_strategy_combo.setCurrentIndex(self.tetra_strategy_combo.findData('netgen'))
            self.apply_geometry()
        except Exception as error:
            QMessageBox.warning(self, 'Geometry import', str(error))

    def clear_imported_geometry(self):
        self.imported_geometry_path = ''
        self.imported_geometry_name = ''
        self.imported_geometry_role = 'compression'
        self.imported_geometry_height = None
        self.import_geometry_label.setText('No imported geometry')
        self.controls['size'].setEnabled(True)
        self._sync_import_controls()
        self.apply_geometry()

    def _sync_import_controls(self):
        generated = not bool(self.imported_geometry_path)
        family = self.family_combo.currentData()
        if family in STRUCTURE_FAMILIES:
            self._generated_family = family
        self.family_combo.blockSignals(True)
        self.family_combo.setCurrentIndex(self.family_combo.findData(
            self._generated_family if generated else 'imported'))
        self.family_combo.blockSignals(False)
        self._geometry_import_rows_visible(not generated)
        for widget in (self.structure_combo, self.morphology_combo,
                       self.stl_resolution_input, self.stl_auto_button,
                       *(self.controls[name] for name in ('size', 'cell_size', 'solid_fraction', 'random_seed'))):
            widget.setEnabled(generated)

    def _geometry_import_rows_visible(self, visible):
        form = self.import_geometry_label.parentWidget().layout()
        form.setRowVisible(self.import_geometry_label, visible)
        form.setRowVisible(self.clear_import_button, visible)

    def _sync_mesh_type(self) -> None:
        local_size = self.controls["local_mesh_size"]
        tetrahedral = self.mesh_type_combo.currentData() in TETRAHEDRAL_MESH_TYPES
        self._mesh_form.setRowVisible(local_size.parentWidget(), tetrahedral)
        self._mesh_form.setRowVisible(self.tetra_strategy_combo, tetrahedral)
        if hasattr(self, "geometry_apply_button"):
            self.geometry_changed()

    def preview_settings(self) -> Settings:
        geometry = {
            name: getattr(self.active_geometry, name)
            for name in GEOMETRY_FIELDS
        }
        return replace(self.collect(), **geometry)

    def has_pending_geometry(self) -> bool:
        return any(
            getattr(self.collect(), name)
            != getattr(self.active_geometry, name)
            for name in GEOMETRY_FIELDS
        )

    def geometry_changed(self, *_) -> None:
        self.refresh_validity()

    def apply_geometry(self, *_) -> None:
        settings = self.collect()
        errors = validate_geometry(settings, include_mesh=False)
        if errors:
            self.status.setText("\n".join(errors))
            self._update_status_visuals("error", "● VALIDATION ERROR", True)
            return
        self.active_geometry = settings
        self._mesh_requested = False
        self._analysis_after_mesh = None
        self.result_preview_field.setCurrentIndex(0)
        self.mode.setCurrentIndex(0)
        self.failed_key = None
        self.debounce.stop()
        self.apply_button.setEnabled(False)
        self.doe_studio.use_model_btn.setEnabled(True)
        self.start_preview()

    def apply_mesh(self, *_) -> None:
        self.apply_geometry()
        if not validate_geometry(self.collect()):
            self._mesh_requested = True
            self.mode.setCurrentIndex(1)
            self.start_preview()

    def _select_model_view(self, index):
        if self.mode.currentIndex() == index:
            self.result_preview_field.setCurrentIndex(0)
        else:
            self.mode.setCurrentIndex(index)

    def _display_mode_changed(self, index):
        with QSignalBlocker(self.result_preview_field):
            self.result_preview_field.setCurrentIndex(0)
        if index in (1, 2):
            self._mesh_requested = True
            self.failed_key = None
            self.start_preview()
        self.render_preview()

    def refresh_validity(self, *_) -> None:
        if self._switching_solver:
            return
        settings = self.collect()
        errors = validate_settings(
            settings
        ) + self.solver_capabilities.validate(settings)
        controller = getattr(self,"remote_controller",None)
        remote_error = None
        remote = controller is not None and controller.requested
        if remote:
            from remote_operations import required_capability
            try:
                capability = required_capability(dict(operation="mechanical", settings=dict(
                    solver_backend=settings.solver_backend, test_profile=settings.test_profile)))
                controller.require(capability, settings)
            except (ValueError,RuntimeError) as error: errors.append(str(error))
            try: controller.require("mesh",settings)
            except (ValueError,RuntimeError) as error: remote_error = str(error)
        elif settings.solver_backend == "kratos":
            errors.append("Kratos runs on TRUBA. Select TRUBA or choose ANSYS MAPDL for local execution.")
        geometry_valid = not validate_geometry(settings, include_mesh=False)
        self.geometry_apply_button.setEnabled(geometry_valid)
        self.structure_apply_button.setEnabled(self.geometry_apply_button.isEnabled())
        self.mesh_apply_button.setEnabled(not validate_geometry(settings))
        if remote_error:
            for button in (self.geometry_apply_button,self.structure_apply_button,self.mesh_apply_button): button.setEnabled(False)
        current = (
            self.preview
            and self.preview.key == preview_key(self.collect())
        )
        mesh_errors = self.preview.mesh.solve_errors if current and self.preview.mesh is not None else []
        pending = self.has_pending_geometry()
        self.doe_studio.use_model_btn.setEnabled(geometry_valid)
        method = self.setup_tabs.currentIndex()
        idle = self.analysis_job is None and self.future is None
        if method == self._fft_method_index:
            fft_errors = validate_periodic_fft(self._fft_settings(settings))
            self._request_fft_preview(settings, valid=not fft_errors)
            remote_fft = remote
            self.fft_distribution.setEnabled(os.name == 'nt' and not remote_fft)
            if remote_fft:
                try:
                    controller.require("damask")
                except (ValueError, RuntimeError) as error:
                    fft_errors.append(str(error))
            self.apply_button.setEnabled(idle and not fft_errors)
        else:
            self.apply_button.setEnabled(
                method == 0 and idle and
                bool((current or remote) and not pending and not errors and not mesh_errors)
            )
        if self.analysis_job is not None:
            self._show_analysis_progress(self.analysis_job)
            self._update_status_visuals("busy", "● ANALYSING", False)
            return
        if method == self._fft_method_index:
            ready_text = "Ready to submit DAMASK FFT to TRUBA." if remote_fft else "Ready to run local DAMASK FFT."
            self.status.setText("\n".join(fft_errors) if fft_errors else ready_text)
            state, badge = ("error", "● VALIDATION ERROR") if fft_errors else ("ready", "● READY TO SOLVE")
            self._update_status_visuals(state, badge, False)
            return
        if method != 0:
            self.status.setText("Planned")
            self._update_status_visuals("pending", "● SOLVER INACTIVE", False)
            return
        if errors or mesh_errors:
            self.status.setText("\n".join(errors + mesh_errors))
            badge_label = (
                "● SOLVER INACTIVE"
                if not self.solver_capabilities.implemented
                and not validate_settings(settings)
                and not mesh_errors
                else "● VALIDATION ERROR"
            )
            state_kind = (
                "pending"
                if badge_label == "● SOLVER INACTIVE"
                else "error"
            )
            self._update_status_visuals(state_kind, badge_label, pending)
            return
        if pending:
            self.status.setText(
                "Geometry or mesh settings have changed. Click Apply to update the preview."
            )
            self._update_status_visuals("pending", "● PENDING APPLY", True)
            return
        if remote and idle:
            self.status.setText("Ready to submit analysis to TRUBA.")
            self._update_status_visuals("ready", "● READY TO SOLVE", False)
        elif current:
            self.status.setText(
                f"Run analysis reuses this FE mesh and starts {self.solver_capabilities.display_name}."
                if self.preview.mesh is not None else
                "Geometry ready. FE Mesh builds the analysis mesh; Run analysis builds it before solving."
            )
            self._update_status_visuals("ready", "● READY TO SOLVE", False)

    def schedule_preview(self, *_) -> None:
        self.failed_key = None
        self.apply_button.setEnabled(False)
        self.doe_studio.use_model_btn.setEnabled(not validate_geometry(self.collect(), include_mesh=False))
        self.status.setText("Settings changed — updating geometry preview...")
        self._update_status_visuals(
            "working", "● UPDATING PREVIEW", self.has_pending_geometry()
        )
        self.debounce.start(PREVIEW_DELAY_MS)

    def start_preview(self) -> None:
        if self.disposed:
            return
        settings = self.preview_settings()
        controller = getattr(self,"remote_controller",None)
        if controller is not None and controller.requested:
            try: controller.require("mesh",settings if self._mesh_requested else None)
            except (RuntimeError,ValueError) as error:
                self.status.setText(str(error))
                return
        errors = validate_geometry(settings, include_mesh=self._mesh_requested)
        if errors:
            self.status.setText("\n".join(errors))
            self.apply_button.setEnabled(False)
            self._update_status_visuals(
                "error", "● GEOMETRY ERROR", self.has_pending_geometry()
            )
            return
        key = preview_key(settings)
        if self.future is not None or key == self.failed_key:
            return
        if self.preview and self.preview.key == key and (not getattr(self, '_mesh_requested', False) or self.preview.mesh is not None):
            self.refresh_validity()
            return
        self.worker_key = key
        self.status.setText(
            "Building FE mesh in background…" if getattr(self, '_mesh_requested', False) else "Preparing geometry preview…"
        )
        self._update_status_visuals(
            "working", "● BUILDING FE MESH" if getattr(self, '_mesh_requested', False) else "● BUILDING GEOMETRY", self.has_pending_geometry()
        )
        self.mesh_progress_updates = SimpleQueue()
        self.mesh_cancelled = Event()
        self._preview_detach = Event()
        self.mesh_progress.show()
        self._preview_is_remote = bool(controller is not None and controller.requested)
        if controller is not None and controller.requested:
            from truba_remote import run_remote_preview
            context,auth = controller.require("mesh",settings if self._mesh_requested else None)
            self.cancel_mesh_button.setEnabled(True)
            self.cancel_mesh_button.show()
            self.future = self.executor.submit(run_remote_preview,settings,context,auth,
                                               self.mesh_progress_updates.put,self.mesh_cancelled,
                                               include_mesh=getattr(self, '_mesh_requested', False),
                                               detach_requested=self._preview_detach)
        elif self.builder is build_preview:
            self.cancel_mesh_button.setEnabled(True)
            self.cancel_mesh_button.show()
            self.future = self.executor.submit(
                run_mesh_preview, settings, self.preview, self.mesh_progress_updates.put,
                self.mesh_cancelled,
                include_mesh=getattr(self, '_mesh_requested', False),
            )
        else:
            self.future = self.executor.submit(self.builder, settings, self.preview)

    def cancel_meshing(self) -> None:
        if self.future is None:
            return
        self.mesh_cancelled.set()
        self._analysis_after_mesh = None
        self.debounce.stop()
        self.cancel_mesh_button.setEnabled(False)
        self.status.setText("Cancelling mesh generation…")

    def _show_mesh_stage(self) -> None:
        stage = None
        try:
            while True:
                stage = self.mesh_progress_updates.get_nowait()
        except Empty:
            if stage and not self.mesh_cancelled.is_set():
                self.status.setText(stage)

    def poll_preview(self) -> None:
        if self.future is None:
            return
        if not self.future.done():
            self._show_mesh_stage()
            return
        future, self.future = self.future, None
        key = preview_key(self.preview_settings())
        self.mesh_progress.hide()
        self.cancel_mesh_button.hide()
        if self.mesh_cancelled.is_set():
            controller = getattr(self,"remote_controller",None)
            if getattr(self,"_preview_is_remote",False):
                from concurrent.futures import CancelledError
                try: future.result()
                except CancelledError: pass
                except Exception as error:
                    self.status.setText(f"Remote mesh operation stopped without a confirmed cancellation: {error}. Retrieve the saved job record.")
                    self.failed_key = key
                    return
            self.failed_key = key
            self.refresh_validity()
            self.status.setText("Mesh generation cancelled. Select Update preview to retry.")
            self._update_status_visuals("pending", "● MESH CANCELLED", self.has_pending_geometry())
            controller = getattr(self,"remote_controller",None)
            if controller is not None and getattr(self,"_restart_preview_for_mode",False) and (not controller.requested or controller.ready):
                self._restart_preview_for_mode = False
                self.failed_key = None
                QTimer.singleShot(0,self.start_preview)
            return
        try:
            preview = future.result()
            if preview.key == key:
                first = self.preview is None
                self.preview = preview
                self.render_preview(reset=first)
                self.refresh_validity()
                requested = getattr(self, '_analysis_after_mesh', None)
                if requested is not None and preview.mesh is not None:
                    self._analysis_after_mesh = None
                    if self.collect() == requested and not self.has_pending_geometry():
                        self.apply()
        except Exception as exc:
            self._analysis_after_mesh = None
            if self.worker_key == key:
                self.failed_key = key
                self.status.setText(f"Preview error: {exc}")
                self.apply_button.setEnabled(False)
                self._update_status_visuals(
                    "error", "● PREVIEW ERROR", self.has_pending_geometry()
                )
        if self.worker_key != key and not self.debounce.isActive():
            self.start_preview()
        elif self.preview is not None and self.preview.mesh is None and getattr(self, '_mesh_requested', False) and self.failed_key != key:
            self.start_preview()

    def _request_fft_preview(self, settings, *, valid):
        grid = self.fft_grid.value()
        key = (grid, *(getattr(settings, name) for name in GEOMETRY_FIELDS)) if valid else None
        old_key = self._fft_preview_request[0] if self._fft_preview_request else None
        if key != old_key:
            self._fft_preview_request = (key, settings, grid) if valid else None
            self._fft_preview_result = None
            self._fft_preview_error = ""
            self.render_preview()
        self._update_fft_preview_summary(settings)

    def _poll_fft_preview(self):
        if self.disposed:
            return
        if self._fft_preview_future is not None:
            request, future = self._fft_preview_future
            if not future.done():
                return
            self._fft_preview_future = None
            if request is self._fft_preview_request:
                try:
                    self._fft_preview_result = future.result()
                except Exception as error:
                    self._fft_preview_error = str(error) or type(error).__name__
                if self.setup_tabs.currentIndex() == self._fft_method_index:
                    self._update_fft_preview_summary(self.collect())
                    self.render_preview()
        if (self.setup_tabs.currentIndex() == self._fft_method_index
                and self._fft_preview_request is not None
                and self._fft_preview_result is None and not self._fft_preview_error):
            request = self._fft_preview_request
            _, settings, grid = request
            future = self._fft_preview_executor.submit(_build_fft_preview, settings, grid)
            self._fft_preview_future = (request, future)

    def _update_fft_preview_summary(self, settings):
        from ui_language import translate

        if self._fft_preview_request is None:
            text = "DAMASK voxel preview unavailable. Check the settings below."
        elif self._fft_preview_error:
            text = f"Could not build the DAMASK voxel preview: {self._fft_preview_error}"
        elif self._fft_preview_result is None:
            text = "Preparing DAMASK voxel preview…"
        else:
            grid = self.fft_grid.value()
            _, _, count = self._fft_preview_result
            total = grid ** 3
            ratio = self.fft_void_ratio.value()
            young_modulus = self.fft_material_editor.numeric_values()["young_modulus"]
            text = (f"{grid} × {grid} × {grid} · Voxel spacing: {settings.cell_size / grid:.4g} mm\n"
                    f"Solid voxels: {count:,} ({count / total:.1%}) · Void voxels: {total - count:,}\n"
                    f"Void stiffness: {ratio * 100:.5g}% of solid · Void E: {ratio * young_modulus:.5g} MPa")
        self.fft_preview_summary.setText(translate(text))

    def _render_fft_preview(self, reset):
        camera = self.plotter.camera_position
        self.plotter.clear()
        self.fragment_status.hide()
        if self._fft_preview_result is None:
            self.plotter.render()
            return
        outline, surface, count = self._fft_preview_result
        self.plotter.enable_lightkit()
        self.plotter.set_background("#f0f0f0", top="#ffffff")
        self.plotter.add_mesh(outline, color="#64748b", lighting=False, name="fft_cell_outline")
        if count:
            self.plotter.add_mesh(surface, color="#60a5fa", show_edges=True,
                                  edge_color="#334155", smooth_shading=False, name="fft_solid_voxels")
        cell_size = self._fft_preview_request[1].cell_size
        self._finish_preview_view(camera, reset or cell_size != self._fft_preview_size)
        self._fft_preview_size = cell_size

    def _sync_preview_readout(self, *_):
        fft_selected = self.setup_tabs.currentIndex() == self._fft_method_index
        has_results = self._result_preview_folder is not None
        self.result_preview_controls.setVisible(has_results and not fft_selected)
        model_view = not fft_selected and self.result_preview_field.currentData() is None
        self.mesh_kpis.setVisible(model_view)
        self._mesh_details_action.setVisible(model_view)
        self.telemetry_details.setVisible(model_view and self._mesh_details_action.isChecked())

    def _render_result_preview(self, field, reset):
        from ui_language import translate

        if self._result_preview_data is None:
            folder = self._result_preview_folder
            surface = pv.read(folder / "deformed_shape.vtu").extract_surface(algorithm="dataset_surface")
            fields = ("Von_Mises_MPa", "Displacement_Magnitude_mm")
            if not surface.n_cells or any(name not in surface.point_data for name in fields):
                raise ValueError("The result mesh is empty or missing nodal fields.")
            platens = [
                pv.read(folder / name) if (folder / name).is_file() else None
                for name in ("bottom_platen.stl", "top_platen_deformed.stl")
            ]
            self._result_preview_data = surface, platens

        surface, platens = self._result_preview_data
        camera = self.plotter.camera_position
        self.plotter.clear()
        self.plotter.enable_lightkit()
        self.plotter.set_background("#f0f0f0", top="#ffffff")
        self.plotter.add_mesh(
            surface, scalars=field, preference="point", cmap="viridis",
            name="mechanical_result", lighting=False,
            scalar_bar_args={"title": translate(self.result_preview_field.currentText()),
                             "color": "#1e293b", "vertical": True,
                             "height": 0.36, "position_y": 0.04},
        )
        if self.show_platens_cb.isChecked():
            self._add_preview_platens(platens)
        self.fragment_status.hide()
        self._finish_preview_view(camera, reset)

    def render_preview(self, *_, reset: bool = False) -> None:
        if self.disposed:
            return
        self._sync_preview_readout()
        if self.setup_tabs.currentIndex() == self._fft_method_index:
            self._render_fft_preview(reset)
            return
        field = self.result_preview_field.currentData()
        if field is not None:
            try:
                fit_result = not self._showing_result_preview or self._result_preview_data is None
                self._render_result_preview(field, reset or fit_result)
                self._showing_result_preview = True
                return
            except (OSError, ValueError, RuntimeError) as error:
                with QSignalBlocker(self.result_preview_field):
                    self.result_preview_field.setCurrentIndex(0)
                self._sync_preview_readout()
                self.status.setText(f"Cannot display this result: {error}")
        reset = reset or self._showing_result_preview
        self._showing_result_preview = False
        if not self.preview:
            self.plotter.clear()
            self.plotter.render()
            return
        camera = self.plotter.camera_position
        self.plotter.clear()
        self.plotter.enable_lightkit()
        self.plotter.set_background("#f0f0f0", top="#ffffff")
        mode = self.mode.currentIndex()
        mesh = self.preview.mesh
        if mode in (0, 2) or mesh is None:
            self.plotter.add_mesh(
                self.preview.surface,
                color="#4ea8de",
                smooth_shading=True,
                ambient=0.22,
                specular=0.25,
                specular_power=20,
                opacity=0.35 if mode == 2 else 1,
            )
        removed = None
        if mode in (1, 2) and mesh is not None:
            self.plotter.add_mesh(
                mesh.grid,
                color="#60a5fa",
                show_edges=True,
                edge_color="#334155",
                line_width=1,
                smooth_shading=False,
                name=MESH_PREVIEW_ACTOR,
            )
            removed = mesh.removed_grid
        if self.preview_settings().imported_geometry_path and mode in (0, 2):
            faces = self.preview.surface.cell_data.get('compression_face')
            if faces is not None:
                for role, color in ((1, '#22c55e'), (2, '#f97316')):
                    selected = np.flatnonzero(faces == role)
                    if len(selected):
                        actor = self.plotter.add_mesh(self.preview.surface.extract_cells(selected), color=color,
                                                     name=f'compression_face_{role}', pickable=False, lighting=False)
                        actor.mapper.SetResolveCoincidentTopologyToPolygonOffset()
                        actor.mapper.SetRelativeCoincidentTopologyPolygonOffsetParameters(-2, -2)
        removed_count = removed.n_cells if removed is not None else 0
        if removed_count:
            self.plotter.add_mesh(
                removed,
                color="#dc2626",
                show_edges=True,
                edge_color="#7f1d1d",
                name="excluded_fragments",
            )
        if self.show_platens_cb.isChecked():
            cube_sz = float(self.preview_settings().size)
            from standard_tests import fixture_surfaces
            bottom_platen, top_platen = (fixture_surfaces(self.preview_settings()) if self.preview_settings().test_profile != 'custom' else
                make_compression_platens(cube_sz, 0.0, self.preview.surface.bounds[:4] if self.preview_settings().imported_geometry_path else None))
            self._add_preview_platens((bottom_platen, top_platen))
        self.fragment_status.setVisible(bool(removed_count))
        self.fragment_status.setText(
            f"Excluded: {removed_count} disconnected elements will be removed. "
            "Red pieces are excluded from the ANSYS mesh."
        )
        self._finish_preview_view(camera, reset)

        if mesh is None:
            for name in ("elements", "nodes", "fraction", "spacing"):
                self._kpi_labels[name].setText("—")
            self.stats.setText(f"Geometry: {self.preview.surface.n_cells:,} triangles | FE mesh has not been generated.")
            return
        self._kpi_labels["elements"].setText(f"{mesh.grid.n_cells:,}")
        self._kpi_labels["nodes"].setText(f"{mesh.grid.n_points:,}")
        self._kpi_labels["fraction"].setText(f"{mesh.fraction:.2f}%")
        settings = self.preview_settings()
        spacing = (
            f"{mesh.spacing:.4g} mm ({mesh.subdivisions}³)"
            if mesh.subdivisions is not None
            else f"{settings.local_mesh_size:.4g}–{mesh.spacing:.4g} mm targets"
        )
        self._kpi_labels["spacing"].setText(spacing)
        fragments = ""
        if removed is not None and mesh.subdivisions is not None:
            fragments = (
                f"\nRED = removed fragments: {removed_count:,} elements, "
                f"{removed_count * mesh.spacing**3:.6g} mm³ — excluded from analysis."
            )
        self.stats.setText(
            f"FE size {spacing} | "
            f"{mesh.grid.n_cells:,} {settings.mesh_type} | {mesh.grid.n_points:,} nodes\n"
            f"Solid {mesh.fraction:.6f}% | Face-connected components {mesh.components} | "
            f"Preview and solver use this same FE mesh.{fragments}"
        )

    def _add_preview_platens(self, platens):
        for platen, name, color in zip(platens, ("bottom_platen", "top_platen"), ("#475569", "#64748b")):
            if platen is not None and platen.n_cells:
                self.plotter.add_mesh(
                    platen, color=color, smooth_shading=False, show_edges=True,
                    edge_color="#1e293b", name=name,
                )

    def _finish_preview_view(self, camera, reset):
        self.plotter.add_axes(color="#0f172a", interactive=False)
        self.plotter.show_bounds(
            xtitle="X / mm",
            ytitle="Y / mm",
            ztitle="Z / mm",
            location="outer",
            ticks="outside",
            use_2d=True,
            n_xlabels=3,
            n_ylabels=3,
            n_zlabels=3,
            font_size=10,
            color="#1e293b",
        )
        if reset:
            self.reset_view()
        else:
            self.plotter.camera_position = camera
            self.plotter.reset_camera_clipping_range()
            self.plotter.render()

    def _show_geometry_workspace(self) -> None:
        self.workspace_tabs.setCurrentIndex(0)
        self.setup_tabs.setCurrentIndex(0)
        self.raise_()
        self.activateWindow()

    def _show_doe_workspace(self) -> None:
        self.refresh_validity()
        self.doe_window.open_workspace()

    def _show_design_pool(self):
        if self.design_pool is None:
            from design_pool import DesignPoolDialog
            self.design_pool = DesignPoolDialog(self)
            self.design_pool.geometry_prepared.connect(self._load_pool_geometry)
            self.design_pool.flow_geometry_prepared.connect(self._load_flow_geometry)
        self.design_pool.open_pool()

    def _load_pool_geometry(self, path, height, name):
        settings = replace(self.collect(), imported_geometry_path=path,
                           imported_geometry_name=name, imported_geometry_role="compression", size=height, test_profile="custom",
                           mesh_type="TET4", tetra_strategy="netgen",
                           compression=min(self.controls["compression"].value(), height * 0.1))
        self.controls['size'].setRange(min(self.controls['size'].minimum(), height),
                                       max(self.controls['size'].maximum(), height))
        self.load_from_doe_row(settings)
        self.status.setText(f"{name}: configure material, mesh and solver; Designs > Add current model to analysis queue.")

    def import_flow_geometry(self):
        from file_dialog import QFileDialog
        from imported_geometry import read_surface, prepare_general_surface, store_surface
        if self.project_workspace.busy():
            QMessageBox.information(self, "Geometry import", "Wait for the current operation to finish before replacing geometry.")
            return
        filename, _ = QFileDialog.getOpenFileName(self, "Import flow / thermal geometry (mm)", "",
                                                 "Surface geometry (*.stl *.obj *.ply *.vtp)")
        if not filename:
            return
        try:
            surface, size = prepare_general_surface(read_surface(filename, require_single=False))
            self._load_flow_geometry(store_surface(surface), size, Path(filename).name)
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self, "Geometry import", str(error))

    def _load_flow_geometry(self, path, size, name):
        if self.project_workspace.busy():
            QMessageBox.information(self,"Geometry import","Wait for the current operation to finish before replacing geometry.")
            return
        page = self.workspace_tabs.currentIndex()
        settings = replace(self.collect(), imported_geometry_path=path, imported_geometry_name=name,
                           imported_geometry_role="general", size=size, test_profile="custom",
                           mesh_type="TET4", tetra_strategy="netgen")
        self.controls["size"].setRange(min(self.controls["size"].minimum(), size), max(self.controls["size"].maximum(), size))
        self.load_from_doe_row(settings, preview=False)
        self.active_geometry = settings
        self.preview = None
        self._analysis_after_mesh = None
        self._mesh_requested = False
        self.plotter.clear()
        self.workspace_tabs.setCurrentIndex(3 if page == 3 else 2)
        self.status.setText(f"{name}: imported for flow/thermal. Set channel dimensions and placement; build or run explicitly.")

    def _ensure_analysis_queue(self):
        if self.analysis_queue_widget is None:
            from analysis_queue import AnalysisQueueWidget, AnalysisQueueWindow
            self.analysis_queue_widget = AnalysisQueueWidget(
                self.collect, generate_volume_mesh, run_batch_experiment, self)
            self.analysis_queue_widget.remote_controller = self.remote_controller
            self.analysis_queue_widget.preview_requested.connect(self.load_from_doe_row)
            self.analysis_queue_window = AnalysisQueueWindow(self.analysis_queue_widget, self)
            self.analysis_queue_window.setStyleSheet(STUDIO_STYLESHEET)

    def _show_analysis_queue(self):
        self._ensure_analysis_queue()
        self.analysis_queue_window.open_workspace()

    def _show_folder_batch(self):
        from folder_batch import FolderBatchDialog
        self._ensure_analysis_queue()
        if not hasattr(self, "folder_batch_dialog"):
            self.folder_batch_dialog = FolderBatchDialog(self.collect, self.analysis_queue_widget, self)
        from window_state import show_restored_window
        show_restored_window(self.folder_batch_dialog)

    def _enqueue_current_model(self):
        if self.workspace_tabs.currentIndex() != 0:
            QMessageBox.information(self, "Analysis queue", "Select the Mechanic workspace and configure the model first.")
            return
        try:
            self._ensure_analysis_queue()
            row = self.analysis_queue_widget.add_snapshot(self.collect())
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Analysis queue", str(error))
            return
        self.status.setText(f"{row.exp_id} added to the analysis queue. Analysis has not started.")
        if self.design_pool is not None and self.design_pool.pending:
            self.design_pool.prepare_next()
        else:
            self.analysis_queue_window.open_workspace()

    def _add_current_design(self) -> None:
        self.refresh_validity()
        if self.doe_studio.use_model_btn.isEnabled():
            self.doe_studio.add_design(self.collect())

    def _show_doe_bounds(self, label: str, cases: dict[str, Settings]) -> None:
        surface_builder = make_surface
        detach_requested = None
        if self.remote_controller.requested:
            try:
                from functools import partial
                from truba_remote import run_remote_surface
                context,auth = self.remote_controller.require("mesh")
                detach_requested = Event()
                surface_builder = partial(run_remote_surface, context=context, auth=auth,
                                          cancelled=None, detach_requested=detach_requested)
            except (ValueError,RuntimeError) as error:
                self.status.setText(str(error))
                return
        dialog = DoEBoundaryPreview(label, cases, surface_builder, self, detach_requested=detach_requested)
        dialog.setStyleSheet(STUDIO_STYLESHEET)
        dialog.exec()
        dialog.deleteLater()

    def load_from_doe_row(self, row_settings: Settings, *, preview=True) -> None:
        self.result_preview_field.setCurrentIndex(0)
        for combo,value in ((self.test_profile_combo,row_settings.test_profile),(self.architecture_combo,row_settings.specimen_architecture)):
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0,combo.findData(value)))
            combo.blockSignals(False)
        self.controls['core_fraction'].blockSignals(True)
        self.controls['core_fraction'].setValue(row_settings.core_fraction)
        self.controls['core_fraction'].blockSignals(False)
        self.imported_geometry_path = row_settings.imported_geometry_path
        self.imported_geometry_name = row_settings.imported_geometry_name
        self.imported_geometry_role = row_settings.imported_geometry_role
        self.imported_geometry_height = row_settings.size if row_settings.imported_geometry_path else None
        self.import_geometry_label.setText(self.imported_geometry_name or 'No imported geometry')
        self._sync_import_controls()
        self.family_combo.blockSignals(True)
        self.family_combo.setCurrentIndex(
            max(0, self.family_combo.findData(row_settings.structure_family))
        )
        self.family_combo.blockSignals(False)
        self._sync_import_controls()
        self._populate_structure_items(
            row_settings.structure_family, row_settings.structure_name
        )
        self.morphology_combo.blockSignals(True)
        self.morphology_combo.setCurrentIndex(
            max(0, self.morphology_combo.findData(row_settings.tpms_morphology))
        )
        self.morphology_combo.blockSignals(False)
        self.mesh_type_combo.blockSignals(True)
        self.mesh_type_combo.setCurrentIndex(
            max(0, self.mesh_type_combo.findData(row_settings.mesh_type))
        )
        self.mesh_type_combo.blockSignals(False)
        self.tetra_strategy_combo.blockSignals(True)
        self.tetra_strategy_combo.setCurrentIndex(
            max(0, self.tetra_strategy_combo.findData(row_settings.tetra_strategy)))
        self.tetra_strategy_combo.blockSignals(False)
        for field_name in (
            "size",
            "cell_size",
            "solid_fraction",
            "mesh_resolution",
            "local_mesh_size",
            "compression",
            "random_seed",
        ):
            if field_name in self.controls:
                self.controls[field_name].blockSignals(True)
                self.controls[field_name].setValue(getattr(row_settings, field_name))
                self.controls[field_name].blockSignals(False)
        self.stl_resolution_input.setText(
            "auto" if row_settings.stl_resolution is None else f"{row_settings.stl_resolution:.12g}")
        self._sync_structure_ui_visibility()
        self._sync_mesh_type()
        self._show_geometry_workspace()
        self._sync_test_profile_ui()
        if preview:
            self.apply_geometry()

    def reset_view(self, *_) -> None:
        self._set_camera_preset("iso")

    def _local_analysis_in_progress(self):
        if self.analysis_job is not None:
            return getattr(self.analysis_job, "is_remote", False) is not True
        return self._analysis_after_mesh is not None and self.future is not None

    def apply(self) -> None:
        settings = self.collect()
        self.refresh_validity()
        if not self.apply_button.isEnabled():
            return
        if self.setup_tabs.currentIndex() == self._fft_method_index:
            self._start_fft(self._fft_settings(settings))
            return
        if not self.remote_controller.requested and self.preview.mesh is None:
            self._analysis_after_mesh = settings
            self._mesh_requested = True
            self.failed_key = None
            self.start_preview()
            self.refresh_validity()
            return
        try:
            save_settings(settings)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Cannot save settings", str(exc))
            return
        try:
            if self.remote_controller.requested:
                from truba_remote import run_remote_mechanics
                context,auth = self.remote_controller.require(settings.solver_backend,settings)
                preview = self.preview
                source = ""
                if preview is not None and preview.mesh is not None and preview.key == preview_key(settings):
                    source = getattr(preview, "remote_source", "")
                self.analysis_job = AnalysisProcess(run_remote_mechanics,settings,context,source,remote_auth=auth)
                self.truba_dialog.job_list.submitting = True
            else:
                self.analysis_job = AnalysisProcess(run_single_analysis, settings, self.preview.mesh,
                                                   remote_auth=self.truba_dialog.analysis_auth() if settings.solver_backend == "kratos" else None)
        except Exception as exc:
            QMessageBox.critical(self, "Cannot start analysis", str(exc))
            return
        self.accepted_settings = settings
        self.apply_button.setEnabled(False)
        self.force_stop_button.setEnabled(True)
        self._show_analysis_progress(self.analysis_job)
        self._update_status_visuals("busy", "● ANALYSING", False)
        self.analysis_poll.start()

    def _start_fft(self, settings):
        remote = self.remote_controller.requested
        distribution = self.fft_distribution.text().strip()
        if not remote and os.name == "nt" and not distribution:
            self.status.setText("Enter a WSL distribution for DAMASK.")
            return
        grid, void_ratio = self.fft_grid.value(), self.fft_void_ratio.value()
        try:
            if remote:
                from truba_remote import submit_remote_analysis
                context, auth = self.remote_controller.require("damask")
                request = dict(operation="fft_solve", settings=asdict(settings), grid=grid, void_ratio=void_ratio)
                self.analysis_job = AnalysisProcess(submit_remote_analysis, request, context, remote_auth=auth)
                self.truba_dialog.job_list.submitting = True
            else:
                self.analysis_job = DamaskAnalysisProcess(settings, grid, void_ratio, distribution)
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.setText(str(exc))
            self._update_status_visuals("error", "● FAILED", False)
            return
        if self._persist_solver_profiles:
            self._save_fft_preferences()
        self.refresh_validity()
        self.force_stop_button.setEnabled(True)
        self.analysis_poll.start()

    def _save_fft_preferences(self):
        preferences = self.solver_profile_store.settings
        material = self.fft_material_editor.material_settings()
        preferences.setValue("fft/solidMaterial", json.dumps(
            {key: getattr(material, key) for key in MATERIAL_FIELDS}))
        preferences.setValue("fft/grid", self.fft_grid.value())
        preferences.setValue("fft/voidRatio", self.fft_void_ratio.value())
        preferences.setValue("fft/distribution", self.fft_distribution.text())
        preferences.sync()

    def retrieve_truba_job(self):
        if self.analysis_job is not None or self.remote_controller.future is not None:
            QMessageBox.information(self,"TRUBA job","Wait for the current operation to finish, or stop it first.")
            return
        from file_dialog import QFileDialog
        from truba_credentials import state_directory
        filename,_=QFileDialog.getOpenFileName(self,"Retrieve TRUBA job",str(state_directory()/"jobs"),"TRUBA job record (*.json)")
        if not filename:return
        try:
            record=json.loads(Path(filename).read_text())
            from truba_job_list import tracked_analysis
            if tracked_analysis(record) and record.get("submission_attempted"):
                if record.get("state") == "downloaded":
                    result = json.loads((Path(record["local_directory"]) / "output" / "result.json").read_text(encoding="utf-8"))
                    from truba_remote import _rebase_paths
                    result = _rebase_paths(Path(record["local_directory"]), result)
                    if result.get("operation") in ("mechanical", "fft_solve"):
                        self._show_remote_analysis_result(result, record, open_workspace=True)
                    else:
                        self.status.setText(f'TRUBA results retrieved: {Path(record["local_directory"]) / "output"}')
                else:
                    self.truba_dialog.job_list.sync_background()
                    self.status.setText("See TRUBA jobs for this analysis. Results download automatically when connected.")
                return
            if record.get("kind")=="rustmech_remote":
                from truba_remote import retrieve_remote_operation
                self.analysis_job=AnalysisProcess(retrieve_remote_operation,filename,remote_auth=self.truba_dialog.analysis_auth())
            else:
                self.analysis_job=AnalysisProcess(run_resume_analysis,filename,remote_auth=self.truba_dialog.analysis_auth())
        except Exception as error:
            QMessageBox.warning(self,"TRUBA job",str(error))
            return
        self.apply_button.setEnabled(False)
        self.force_stop_button.setEnabled(True)
        self.status.setText("Waiting for TRUBA job and retrieving results…")
        self.analysis_poll.start()

    def show_kratos_results(self):
        if self._kratos_result_dialog is not None:
            self._kratos_result_dialog.show()
            self._kratos_result_dialog.raise_()
            return
        try:
            from kratos_results_view import KratosResultsDialog
            dialog=KratosResultsDialog(self._latest_kratos_results,self)
        except Exception as error:
            QMessageBox.warning(self,"Kratos results",str(error))
            return
        self._kratos_result_dialog=dialog
        dialog.destroyed.connect(lambda *_:setattr(self,"_kratos_result_dialog",None))
        dialog.show()

    def force_stop_analysis(self) -> None:
        if self.analysis_job is None:
            return
        if isinstance(self.analysis_job, DamaskAnalysisProcess) and self.analysis_job.done():
            self._poll_analysis()
            return
        try:
            self.analysis_job.force_stop()
        except Exception as exc:
            self.status.setText(f"Forced Stop failed: {exc}")
            return
        self.analysis_job = None
        self.truba_dialog.job_list.submitting = False
        self.analysis_poll.stop()
        self.local_analysis_progress.refresh()
        self.force_stop_button.setEnabled(False)
        self.refresh_validity()
        self.status.setText("Analysis cancelled. Analysis and solver processes forcibly terminated.")
        self._update_status_visuals("error", "● CANCELLED", False)

    def _show_analysis_progress(self, job):
        record = job.remote_status()
        if record and record.get("background"):
            self._show_background_analysis(record)
        elif record:
            self.status.setText(f"TRUBA job {record.get('job_id', '—')} · {record.get('state', 'preparing')}")
        elif getattr(job, "background_submission", False) is True:
            self.status.setText("Preparing analysis for TRUBA…")
        else:
            stage = job.progress_status() if hasattr(job, "progress_status") else None
            self.status.setText(stage or "Analysis running...")
        self.local_analysis_progress.refresh()

    def _show_background_analysis(self, record):
        from truba_job_list import background_status
        from truba_remote import analysis_code
        job_id = f" · Slurm #{record['job_id']}" if record.get("job_id") else ""
        self.status.setText(f"{background_status(record)} · {analysis_code(record)}{job_id}")

    def _background_analysis_updated(self, record, result):
        if self.disposed:
            return
        idle = self.analysis_job is None and self.future is None
        if result is not None:
            payload, _ = result
            if payload.get("operation") in ("mechanical", "fft_solve"):
                self._show_remote_analysis_result(payload, record, announce=idle)
        elif idle and self._latest_background_analysis == record.get("request_id"):
            self._show_background_analysis(record)

    def _poll_analysis(self) -> None:
        if self.analysis_job is None:
            return
        if not self.analysis_job.done():
            self._show_analysis_progress(self.analysis_job)
            return
        job, self.analysis_job = self.analysis_job, None
        self.truba_dialog.job_list.submitting = False
        self.analysis_poll.stop()
        self.local_analysis_progress.refresh()
        self.force_stop_button.setEnabled(False)
        self.refresh_validity()
        try:
            folder = job.result()
        except Exception as exc:
            self.status.setText(f"Analysis failed: {exc}")
            self._update_status_visuals("error", "● FAILED", False)
        else:
            if isinstance(job, DamaskAnalysisProcess):
                self._show_fft_results(folder, job.folder)
                self.status.setText(f"Analysis completed. Results: {job.folder}")
                self._update_status_visuals("ready", "● COMPLETE", False)
                return
            if getattr(job, "background_submission", False) is True:
                self._latest_background_analysis = folder["request_id"]
                self._show_background_analysis(folder)
                self.truba_dialog.job_list.refresh()
                self.truba_dialog.job_list.sync_background()
                return
            if isinstance(folder,dict) and folder.get("remote_retrieval"):
                result = folder["result"]
                directory = Path(folder["directory"])/"output"
                operation = result["operation"]
                if operation.startswith("thermal_"):
                    self.thermal_workspace.load_directory(directory)
                    self.workspace_tabs.setCurrentIndex(3)
                elif operation.startswith("flow_"):
                    self.flow_workspace.load_directory(directory)
                    self.workspace_tabs.setCurrentIndex(2)
                elif operation in ("probe","prepare"):
                    self.status.setText(f"Remote runtime report retrieved: {directory}. Check capabilities for the current configuration before computing.")
                elif operation=="preview":
                    from truba_remote import load_remote_preview
                    try:
                        preview = load_remote_preview(directory/"preview",settings=self.preview_settings())
                    except (ValueError, OSError) as error:
                        self.status.setText(f"TRUBA preview retrieved, but cannot be applied to the current geometry: {error}. Results: {directory}")
                    else:
                        self.preview = preview
                        self.render_preview(reset=True)
                        self.refresh_validity()
                        self.status.setText(f"TRUBA preview retrieved: {directory}")
                elif operation=="mechanical":
                    folder = result["run_folder"]
                elif operation=="fft_solve":
                    self._show_remote_analysis_result(result, open_workspace=True)
                else:
                    self.status.setText(f"TRUBA results retrieved: {directory}")
                if operation!="mechanical": return
            self._show_completed_analysis(folder)

    def _show_remote_analysis_result(self, result, record=None, *, announce=True, open_workspace=False):
        folder = result["run_folder"]
        if result.get("operation") == "fft_solve":
            folder = Path(folder)
            try:
                self._show_fft_results(json.loads((folder / "status.json").read_text(encoding="utf-8")), folder)
            except (OSError, ValueError, KeyError, TypeError) as error:
                if announce:
                    self.status.setText(f"Analysis failed: {error}")
                    self._update_status_visuals("error", "● FAILED", False)
                return
            if open_workspace:
                self._select_settings_method(self._fft_method_index)
        self._show_completed_analysis(folder, record, announce=announce)

    def _show_completed_analysis(self, folder, record=None, *, announce=True):
        if announce:
            if record:
                self._show_background_analysis(record)
                self.status.setText(f"{self.status.text()} · {folder}")
            else:
                self.status.setText(f"Analysis completed. Results: {folder}")
            self._update_status_visuals("ready", "● COMPLETED", False)
        fields_folder = Path(folder) / "03_Results"
        if (fields_folder / "deformed_shape.vtu").is_file():
            from ui_language import translate

            self._result_preview_folder = fields_folder
            self._result_preview_data = None
            self.result_preview_field.setToolTip(
                f"{translate('Latest mechanical result · Final load step')}\n{Path(folder)}"
            )
            self._sync_preview_readout()
            if self.result_preview_field.currentData() is not None:
                self.render_preview()
        try:
            report=json.loads((Path(folder)/"summary.json").read_text(encoding="utf-8"))
            if report.get("solver") == "kratos":
                self._latest_kratos_results=str(folder)
                self.kratos_panel.view_results_button.setEnabled(True)
                if self._persist_solver_profiles:
                    self.solver_profile_store.settings.setValue("kratos/latestResultFolder",str(folder))
                    self.solver_profile_store.settings.sync()
        except (OSError,ValueError):pass

    def _detach_remote_jobs(self):
        jobs = []
        job = self.analysis_job
        if getattr(job, 'is_remote', False) is True:
            jobs.append(job)
            self.analysis_job = None
            self.analysis_poll.stop()
        for workspace in (self.flow_workspace, self.thermal_workspace):
            if getattr(workspace.job, 'is_remote', False) is True:
                jobs.append(workspace.job)
                workspace.job = None
                workspace.timer.stop()
        for queue in (self.doe_studio, self.analysis_queue_widget):
            if queue is not None:
                jobs.extend(queue.detach_remote_queue())
        from mesh_convergence import MeshConvergenceWidget
        for study in self.findChildren(MeshConvergenceWidget):
            if getattr(study.future, 'is_remote', False) is True:
                jobs.append(study.future)
                study.future = None
                study.dispose()
        pending = [job.detach_remote() for job in jobs]
        pending.extend(dialog.detach_remote_job() for dialog in self.findChildren(DoEBoundaryPreview))
        return pending

    def _prepare_truba_close(self):
        if self.disposed:
            return True
        controller = self.remote_controller
        if controller.closing:
            return controller.close_confirmed
        if controller.request_close(self._detach_remote_jobs()):
            return True
        self.poll.stop()
        self.setEnabled(False)
        return False

    def reject(self):
        if self._prepare_truba_close(): super().reject()

    def done(self, result):
        if not self._prepare_truba_close():
            return
        # QDialog.done hides its native children before emitting finished.
        # Release WGL resources while their native window handles still exist.
        self.dispose()
        super().done(result)

    def closeEvent(self,event):
        if not self._prepare_truba_close():
            event.ignore()
            return
        self.dispose()
        super().closeEvent(event)

    def dispose(self, *_) -> None:
        if self.disposed:
            return
        if self._solver_profiles_ready and self._persist_solver_profiles:
            draft = self.collect(draft=True)
            self.solver_profile_store.save_workspace(draft)
            self.solver_profile_store.save(self._active_solver_id, profile_values(draft))
            self._save_fft_preferences()
        from concurrent.futures import wait
        detached = self._detach_remote_jobs()
        if getattr(self, "_preview_is_remote", False) and self.future is not None:
            self._preview_detach.set()
            detached.append(self.future)
        if hasattr(self, "remote_controller"):
            controller = self.remote_controller
            controller.runtime_detach.set()
            detached.extend((*controller.close_pending, controller.future,
                             controller.session_future, controller.release_future))
        wait(future for future in detached if future is not None)
        self.force_stop_analysis()
        if hasattr(self,"remote_controller"):
            self.remote_controller.shutdown()
        self.disposed = True
        if self.esen_workspace is not None:
            self.esen_workspace.dispose()
        self.truba_dialog.shutdown()
        self.debounce.stop()
        self.poll.stop()
        self.mesh_cancelled.set()
        self.mesh_progress.hide()
        self.cancel_mesh_button.hide()
        shutdown_mesh_preview()
        if self._kratos_result_dialog is not None:
            self._kratos_result_dialog.close()
        if hasattr(self, "doe_studio"):
            self.doe_studio.dispose()
            self.doe_window.close()
        if self.analysis_queue_widget is not None:
            self.analysis_queue_widget.dispose()
            self.analysis_queue_window.close()
        if hasattr(self, "folder_batch_dialog"):
            self.folder_batch_dialog.dispose()
            self.folder_batch_dialog.close()
        if self.design_pool is not None:
            self.design_pool.close()
            self.design_pool.dispose()
        if hasattr(self, "flow_workspace"):
            self.flow_workspace.dispose()
        if hasattr(self, "thermal_workspace"):
            self.thermal_workspace.dispose()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self._fft_preview_executor.shutdown(wait=False, cancel_futures=True)
        if hasattr(self.plotter, "_camera_transition"):
            self.plotter._camera_transition.stop()
        self.plotter.orientation_cube.SetEnabled(0)
        self.plotter.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline-test",
        action="store_true",
        help="Export geometry/mesh without ANSYS",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="Skip images during offline export",
    )
    args = parser.parse_args()
    if args.offline_test:
        offline_check(load_settings(), images=not args.no_images)
        return 0
    app = QApplication.instance() or QApplication([sys.argv[0]])
    from app_identity import configure_application
    configure_application(app)
    profiles = SolverProfileStore()
    settings = profiles.restore_workspace()
    dialog = RustMechDialog(settings, solver_profile_store=profiles)
    try:
        dialog.exec()
    finally:
        dialog.dispose()
        dialog.close()
        app.processEvents()
    return 0


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        print(
            "Operation failed. Inspect this error and the labelled run folder.",
            file=sys.stderr,
        )
        sys.exit(1)
