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
from functools import partial
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Event

import numpy as np
import pyvista as pv
from studio_style import CompactSpinBox, CompactComboBox
from PySide6.QtCore import QSignalBlocker, QSize, QTimer, Qt, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
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
    QStyleOptionButton,
    QStyleOptionToolButton,
    QStylePainter,
    QTabBar,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from analysis_process import AnalysisProcess, run_single_analysis, run_resume_analysis
from damask_fft import (
    COMPONENTS, MIN_GRID, DamaskAnalysisProcess, fft_dimensions, sample_periodic_cell,
    validate_periodic_fft,
)
from file_dialog import ANALYSIS_RESULTS_FOLDERS, choose_analysis_results_folder
from studio_style import (ActionFlowLayout, CompactDoubleSpinBox, CONTROL_HEIGHT, STUDIO_STYLESHEET,
                          SidebarGroupBox, configure_form, field_label, mesh_precision_stepper,
                          stepper_with_unit)
from sidebar_splitter import SidebarSplitter
from setup_help import GEOMETRY_HELP
from truba_sync import TrubaSession, TrubaSyncWindow
from truba_desktop import connect_truba_desktop, open_truba_desktop
from window_state import show_restored_window
from peridynamic_analysis import run_local_compression, validate_compression
from peridynamic_workspace import PeridynamicResults, PeridynamicSettingsPanel
from kratos_settings import KratosSettingsPanel
from local_kratos import INSTALL_REQUIRED, local_kratos_python
from solver_profiles import MESH_FIELDS, STRUCTURE_FIELDS, SolverProfileStore, profile_values
from preview_viewport import MESH_PREVIEW_ACTOR, create_preview_viewport
from camera_toolbar import CameraToolbar
from mesh_preview_worker import run_mesh_preview, shutdown_mesh_preview
from model import (
    ANALYSIS_FIELDS,
    CONTACT_FORMULATIONS,
    CONTACT_NUMERICAL_FIELDS,
    LOADING_MODES,
    PLATEN_CONTACT_MODES,
    GEOMETRY_FIELDS,
    MATERIAL_FIELDS,
    DERIVED_SOLID_MESH_TYPES,
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
)
from solver_api import (
    KRATOS_QUADRATIC_MESH_TYPES,
    MAPDL_CAPABILITIES,
    SOLVER_REGISTRY,
    SolverCapabilities,
    get_solver_capabilities,
)
from rust_mesh import build_hex_grid as build_hex_grid
from mesh_engine import Preview as Preview, build_preview, generate_volume_mesh, gyroid_field as gyroid_field, make_surface as make_surface, preview_key
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
WORKSPACE_COMMAND_WIDTH = 65
WORKSPACE_COMMAND_HEIGHT = 50
PENDING_STRUCTURE = "Structure or dimensions have changed. Click Apply to update the preview."
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


class _WorkspaceCommandButton(QPushButton):
    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = "\n".join(option.text.split())
        QStylePainter(self).drawControl(QStyle.ControlElement.CE_PushButton, option)


class _WorkspaceCommandMenu(QToolButton):
    def paintEvent(self, event):
        option = QStyleOptionToolButton()
        self.initStyleOption(option)
        option.text = "\n".join(option.text.split())
        QStylePainter(self).drawComplexControl(QStyle.ComplexControl.CC_ToolButton, option)


class _ComputeStatusLabel(QLabel):
    """Elide compute status text when the toolbar has limited space."""

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(0)
        return hint

    def paintEvent(self, event):
        painter = QStylePainter(self)
        bounds = self.contentsRect()
        painter.setClipRect(bounds)
        text = painter.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, bounds.width())
        painter.drawItemText(bounds, self.alignment().value, self.palette(), self.isEnabled(),
                             text, self.foregroundRole())


def _build_fft_preview(settings: Settings, grid: int):
    from mesh_resources import require_memory

    solid = sample_periodic_cell(settings, grid)
    solid_count = int(np.count_nonzero(solid))
    # Allow for VTK's intermediate hexahedra, points and extracted faces.
    require_memory(solid.size * 16 + solid_count * 384, "DAMASK voxel preview")
    spacing = tuple(length / grid for length in fft_dimensions(settings))
    image = pv.ImageData(dimensions=(grid + 1,) * 3, spacing=spacing)
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
            from window_caption import install_window_captions
            install_window_state(app_inst)
            captions = install_window_captions(app_inst)
            from ui_copy import install_copy_support
            install_copy_support(app_inst)
            from ui_input import install_settings_wheel_guard
            install_settings_wheel_guard(app_inst)

        # QApplication.setStyle/setPalette repolish every live widget, so
        # reapplying them for each new dialog grows with the open windows.
        if app_inst is not None and not app_inst.property("rustmechStyleApplied"):
            from PySide6.QtGui import QColor, QPalette

            app_inst.setProperty("rustmechStyleApplied", True)
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
        self.preview = None
        self._result_preview_folder = None
        self._result_preview_is_historical = False
        self.result_history = None
        self._result_preview_data = None
        self._showing_result_preview = False
        self._mesh_requested = False
        self._analysis_after_mesh = None
        self._fft_method_index = None
        self._peridynamic_method_index = None
        self._peridynamic_preview_folder = None
        self._showing_peridynamic_preview = False
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
        self.active_geometry = (
            self.solver_profile_store.restore_applied_structure(settings)
            if self._persist_solver_profiles else settings
        )
        self._edited_test_profile = settings.test_profile
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
        self._mesh_export_future = None
        self._fft_preview_request = None
        self._fft_preview_future = None
        self._fft_preview_result = None
        self._fft_preview_error = ""
        self._fft_preview_size = None
        self.mesh_cancelled = Event()
        self._preview_detach = Event()
        self.mesh_progress_updates = SimpleQueue()
        self._last_mesh_stage = None
        self.worker_key = None
        self.failed_key = None
        self._preview_failure_reason = ""
        self.disposed = False
        self._mesh_convergence_dialog = None

        self.setWindowTitle("RustMech")
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        captions.prepare_main_window(self)
        self.setSizeGripEnabled(True)
        self.resize(1440, 960)
        self.setMinimumHeight(600)
        self.setObjectName("rustMechWindow")
        self.setStyleSheet(STUDIO_STYLESHEET)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.mpm_workspace_index = None
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

        header = QFrame()
        header.setObjectName("mainWorkspaceHeader")
        header.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(header)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(3, 4, 3, 4)

        self.brand_title_label = QLabel("Nonlinear Compression")
        self.brand_title_label.setObjectName("brandTitle")
        self.brand_title_label.setFixedHeight(CONTROL_HEIGHT)
        unit_badge = QLabel("Units: mm  N  MPa")
        unit_badge.setObjectName("unitBadge")

        brand_card = QFrame()
        self.brand_header = brand_card
        brand_card.setObjectName("brandHeader")
        brand_card.setProperty('computeState', 'local')
        brand_card.setFixedHeight(CONTROL_HEIGHT)
        brand_layout = QHBoxLayout(brand_card)
        brand_layout.setContentsMargins(5, 2, 5, 2)
        self.brand_subtitle_lbl = _ComputeStatusLabel(self.solver_capabilities.display_name)
        self.brand_subtitle_lbl.setObjectName("brandSubtitle")
        self.brand_subtitle_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.brand_subtitle_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.brand_subtitle_lbl.setMaximumWidth(320)
        brand_layout.addWidget(self.brand_subtitle_lbl)
        self.truba_session = TrubaSession(self)
        self.truba_session.background_updated.connect(self._background_analysis_updated)
        self.truba_session.status_changed.connect(self._update_hpc_button)
        self.truba_button = _WorkspaceCommandButton("HPC")
        self.truba_button.setObjectName("primaryLaunchBtn")
        self.truba_button.setAutoDefault(False)
        self.truba_button.setToolTip("Open the Connection tab in the TRUBA application")
        self.truba_button.clicked.connect(self._open_truba_desktop)
        self.truba_connect_button = _WorkspaceCommandButton("Connect")
        self.truba_connect_button.setObjectName("trubaConnectButton")
        self.truba_connect_button.setProperty("state", "disconnected")
        self.truba_connect_button.setAutoDefault(False)
        self.truba_connect_button.setToolTip("Connect to TRUBA using the saved VPN profile and SSH account")
        self.truba_connect_button.clicked.connect(self._connect_truba_desktop)
        self.sync_window = None
        self.sync_button = _WorkspaceCommandButton("SYNC")
        self.sync_button.setObjectName("secondaryActionBtn")
        self.sync_button.setAutoDefault(False)
        self.sync_button.setToolTip("View RustMech analysis files and synchronized results")
        self.sync_button.clicked.connect(self.show_truba_sync)
        self.about_button = _WorkspaceCommandButton("About")
        self.about_button.setObjectName("aboutBtn")
        self.about_button.clicked.connect(self.show_about)
        self._compute_commands = (self.truba_connect_button, self.truba_button, self.sync_button)
        from ui_language import LanguageSelector
        self.language_selector = LanguageSelector(self.language_manager, self)

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
        self._clamp_local_mesh_size()
        for name in ("mesh_resolution", "local_mesh_size"):
            self.controls[name].valueChanged.connect(self._clamp_local_mesh_size)
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
        self._add_structure_apply_row(geometry_form, self.structure_apply_button)
        if self.imported_geometry_path:
            self.controls['size'].setRange(min(.000001, settings.size), max(100, settings.size))
            self.controls['size'].setValue(settings.size)
        self.controls['size'].setEnabled(not bool(self.imported_geometry_path))
        self._sync_import_controls()
        self.mesh_type_combo = NoWheelComboBox()
        for mesh_type in (*MESH_TYPES, *DERIVED_SOLID_MESH_TYPES):
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
        self.mesh_apply_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mesh_apply_button.setToolTip("Apply current settings and build the FE mesh.")
        self.mesh_apply_button.clicked.connect(self.apply_mesh)
        self.mesh_export_button = QPushButton("Export to ANSYS")
        self.mesh_export_button.setAutoDefault(False)
        self.mesh_export_button.setDefault(False)
        self.mesh_export_button.setEnabled(False)
        self.mesh_export_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mesh_export_button.setToolTip("Save the generated FE mesh in an ANSYS-compatible format.")
        self.mesh_export_button.clicked.connect(self.export_mesh_to_ansys)
        self.mesh_export_button.ensurePolished()
        mesh_action_width = max(
            self.mesh_export_button.fontMetrics().horizontalAdvance(label)
            for label in ("Export to ANSYS", "ANSYS'e Aktar")
        ) + 24
        for button in (self.mesh_apply_button, self.mesh_export_button):
            button.setFixedSize(mesh_action_width, CONTROL_HEIGHT)
        mesh_actions = QWidget()
        mesh_action_layout = QVBoxLayout(mesh_actions)
        mesh_action_layout.setContentsMargins(0, 0, 0, 0)
        mesh_action_layout.setSpacing(3)
        mesh_action_layout.addWidget(self.mesh_apply_button, alignment=Qt.AlignmentFlag.AlignRight)
        mesh_action_layout.addWidget(self.mesh_export_button, alignment=Qt.AlignmentFlag.AlignRight)
        mesh_form.addRow(mesh_actions)
        self.tetra_strategy_combo.currentIndexChanged.connect(self.geometry_changed)
        self.mesh_type_combo.currentIndexChanged.connect(self._sync_mesh_type)
        self._sync_mesh_type()
        self._geometry_form = geometry_form

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
        self.geometry_apply_button.clicked.connect(self.schedule_preview)
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

        self._add_method_pages(settings)
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

        command_scroll = QScrollArea()
        command_scroll.setFrameShape(QFrame.Shape.NoFrame)
        command_scroll.setWidgetResizable(True)
        command_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        command_scroll.setFixedHeight(WORKSPACE_COMMAND_HEIGHT)
        command_scroll.horizontalScrollBar().rangeChanged.connect(
            lambda _, maximum: command_scroll.setFixedHeight(WORKSPACE_COMMAND_HEIGHT + (6 if maximum else 0))
        )
        command_bar = QWidget()
        command_bar.setObjectName("workspaceCommandBar")
        navigation = QHBoxLayout(command_bar)
        navigation.setContentsMargins(0, 0, 0, 0)
        navigation.setSpacing(2)
        navigation.addWidget(self.brand_title_label, alignment=Qt.AlignmentFlag.AlignTop)
        command_scroll.setWidget(command_bar)
        header_layout.addWidget(command_scroll)

        from project_workspace import ProjectWorkspace
        self.project_workspace = ProjectWorkspace(self)
        self.project_button = _WorkspaceCommandMenu()
        self.project_button.setObjectName("headerMenu")
        self.project_button.setText("Project")
        self.project_button.setAccessibleName("Analysis project")
        self.project_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        project_menu = QMenu(self.project_button)
        project_menu.addAction("Open project...", self.project_workspace.open)
        project_menu.addAction("Save project", lambda: self.project_workspace.save())
        project_menu.addAction("Save project as...", lambda: self.project_workspace.save(save_as=True))
        project_menu.addSeparator()
        project_menu.addAction("Export mechanical 3D results...", self.project_workspace.export_mechanical_3d)
        self.project_button.setMenu(project_menu)

        self.sidebar_toggle = _WorkspaceCommandMenu()
        self.sidebar_toggle.setObjectName("headerMenu")
        self.sidebar_toggle.setProperty("contextSelector", True)
        method_policy = self.sidebar_toggle.sizePolicy()
        method_policy.setRetainSizeWhenHidden(True)
        self.sidebar_toggle.setSizePolicy(method_policy)
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
        self.mpm_method_action = QAction("Material Point Method", self.methods_menu)
        self.mpm_method_action.triggered.connect(lambda: self.workspace_tabs.setCurrentIndex(self.mpm_workspace_index))
        self.methods_menu.insertAction(self.method_actions.actions()[self._peridynamic_method_index], self.mpm_method_action)
        self.sidebar_toggle.setMenu(self.methods_menu)
        self.setup_tabs.currentChanged.connect(self._sync_settings_method)
        self._sync_settings_method(self.setup_tabs.currentIndex())

        # Keep the indexed navigation model for existing integrations; expose
        # workspace selection through one compact menu.
        self.workspace_navigation = QTabBar(command_bar)
        self.workspace_navigation.setObjectName("workspaceNavigation")
        self.workspace_navigation.setExpanding(False)
        self.workspace_navigation.setDrawBase(False)
        workspace_names = ("Mechanic", "Experimental Design", "Flow CFD", "Thermal CHT", "MPM")
        for name in workspace_names:
            self.workspace_navigation.addTab(name)
        self.workspace_navigation.currentChanged.connect(self.workspace_tabs.setCurrentIndex)
        self.workspace_tabs.currentChanged.connect(self.workspace_navigation.setCurrentIndex)
        self.workspace_navigation.hide()
        self.workspace_button = _WorkspaceCommandMenu()
        self.workspace_button.setObjectName("headerMenu")
        self.workspace_button.setProperty("contextSelector", True)
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
        self.doe_button = _WorkspaceCommandButton("Experimental Design")
        self.doe_button.setToolTip("Open Experimental Design.")
        self.doe_button.clicked.connect(self._show_doe_workspace)
        self.designs_button = _WorkspaceCommandMenu()
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
        self.md_workspace = None
        self.md_button = _WorkspaceCommandButton("Molecular Dynamics")
        self.md_button.setToolTip("Open Molecular Dynamics")
        self.md_button.clicked.connect(self.show_md_workspace)
        self.result_history_button = _WorkspaceCommandButton("Results")
        self.result_history_button.setToolTip("Browse saved analysis results")
        self.result_history_button.clicked.connect(self.show_result_history)
        self._workspace_commands = (
            (self.project_button, self.designs_button),
            (self.sidebar_toggle, self.workspace_button),
            (self.doe_button, self.md_button, self.result_history_button),
        )
        for index, group in enumerate(self._workspace_commands):
            if index:
                divider = QFrame()
                divider.setObjectName("workspaceCommandDivider")
                divider.setFixedSize(1, WORKSPACE_COMMAND_HEIGHT - 10)
                navigation.addWidget(divider)
            for button in group:
                button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                navigation.addWidget(button)
        # Center the compute badge in the space between command groups. Its
        # changing text must not contribute to the toolbar's minimum width.
        compute_slot = QWidget(command_bar)
        compute_slot.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        compute_slot.setMinimumWidth(2 * WORKSPACE_COMMAND_WIDTH)
        compute_layout = QHBoxLayout(compute_slot)
        compute_layout.setContentsMargins(0, 0, 0, 0)
        compute_layout.addWidget(brand_card, alignment=Qt.AlignmentFlag.AlignCenter)
        navigation.addWidget(compute_slot, 1)
        for button in self._compute_commands:
            navigation.addWidget(button)
        navigation.addWidget(self.about_button)
        navigation.addWidget(self.language_selector)
        header_controls = (*(button for group in self._workspace_commands for button in group),
                           *self._compute_commands, self.about_button, self.language_selector.combo)
        for current, following in zip(header_controls, header_controls[1:]):
            QWidget.setTabOrder(current, following)

        self.mode = NoWheelComboBox(preview_tab)
        self.mode.setAccessibleName("3D display mode")
        self.mode.addItems(
            ["Smooth geometry", "FE mesh", "Smooth + FE overlay"]
        )
        self.mode.setCurrentIndex(0)
        self.mode.currentIndexChanged.connect(self._display_mode_changed)
        # Retain the mode model for existing render/preview integrations.
        self.mode.hide()

        self.show_platens_cb = QCheckBox("Show platens")
        self.show_platens_cb.setObjectName("legendPlatenPill")
        self.show_platens_cb.setChecked(True)
        self.show_platens_cb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.show_platens_cb.toggled.connect(self.render_preview)

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
        viewport_container.setMinimumSize(120, 124)
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

        self.plotter = create_preview_viewport(self, show_orientation_cube=True)
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
        telemetry_row = QHBoxLayout()
        telemetry_row.setSpacing(4)
        telemetry_row.addWidget(self.mesh_kpis, 1)
        telemetry_row.addWidget(self.show_platens_cb, 0, Qt.AlignmentFlag.AlignRight)
        bottom_telemetry_layout.addLayout(telemetry_row)
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
        self.display_mode_group = camera_controls.display_modes
        self.display_mode_buttons = QButtonGroup(self.display_mode_group)
        display_modes = (("geometry", "Geometry"), ("mesh", "FE Mesh"), ("overlay", "Overlay"))
        for index, (kind, name) in enumerate(display_modes):
            button = camera_controls.add_display_mode(kind, name, self.mode.itemText(index))
            self.display_mode_buttons.addButton(button, index)
        self.display_mode_buttons.button(self.mode.currentIndex()).setChecked(True)
        self.display_mode_buttons.idClicked.connect(lambda _: self.workspace_tabs.setCurrentIndex(0))
        self.display_mode_buttons.idClicked.connect(self._select_model_view)
        self.mode.currentIndexChanged.connect(lambda index: self.display_mode_buttons.button(index).setChecked(True))
        camera_controls.attach(self.plotter.interactor)
        self._kpi_labels["spacing"].parentWidget().hide()

        cancel = QPushButton("Cancel")
        cancel.setObjectName("cancelBtn")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)

        self.apply_button = QPushButton("Run Analysis")
        self.apply_button.setObjectName("primaryLaunchBtn")
        icon_dir = Path(__file__).resolve().parent / "assets" / "ui"
        play_icon = QIcon(str(icon_dir / "analysis-play-green.svg"))
        play_icon.addFile(str(icon_dir / "analysis-play-green.svg"), mode=QIcon.Mode.Active)
        play_icon.addFile(str(icon_dir / "analysis-play-red.svg"), mode=QIcon.Mode.Disabled)
        self.apply_button.setIcon(play_icon)
        self.apply_button.setIconSize(QSize(16, 16))
        self.apply_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_button.setAutoDefault(False)
        self.apply_button.setToolTip("Save settings and start the selected solver.")
        self.apply_button.clicked.connect(self.apply)

        self.result_button = QPushButton("Result")
        self.result_button.setObjectName("resultBtn")
        self.result_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.result_button.setToolTip("View latest results. Right-click to open the result folder.")
        self.result_button.clicked.connect(self.show_latest_result)
        self.result_button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.result_button.customContextMenuRequested.connect(
            lambda _: self.show_latest_result(open_folder=True)
        )

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
        self.status.setTextFormat(Qt.TextFormat.PlainText)
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
        selectable_text = (Qt.TextInteractionFlag.TextSelectableByMouse
                           | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        for label in (self.status_badge, self.status, self.fragment_status, self.stats, self.fft_preview_summary):
            label.setTextInteractionFlags(selectable_text)
        self.analysis_blocker = QLabel()
        self.analysis_blocker.setObjectName("analysisBlocker")
        self.analysis_blocker.setTextFormat(Qt.TextFormat.PlainText)
        self.analysis_blocker.setTextInteractionFlags(selectable_text)
        self.analysis_blocker.setWordWrap(True)
        self.analysis_blocker.setMinimumHeight(CONTROL_HEIGHT + 4)
        self.analysis_blocker.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        status_layout.addWidget(self.result_button)
        status_layout.addWidget(self.apply_button)
        status_layout.addWidget(self.analysis_blocker, 1)
        self._set_analysis_blocker("Preparing geometry preview…")
        from operation_progress import OperationProgress
        self._analysis_progress_counts = (None, None)
        self.local_analysis_progress = OperationProgress(self, provider=lambda: (
            self._local_analysis_in_progress(), self.status.text(),
            *(self._analysis_progress_counts if self.analysis_job is not None else (None, None)),
        ), bar_at_end=True)
        self.local_analysis_progress.bar.setToolTip("Percentage of completed analysis stages")
        status_layout.addWidget(self.local_analysis_progress, 1)
        self._analysis_action_buttons = (
            cancel, self.apply_button, self.result_button, self.force_stop_button,
        )
        status_layout.addWidget(self.force_stop_button)
        self.execution_target = NoWheelComboBox()
        self.execution_target.addItem("Local", "local")
        self.execution_target.addItem("TRUBA", "truba")
        self.execution_target.setAccessibleName("Execution target")
        self.execution_target.setToolTip("Execution target")
        self.execution_target.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        status_layout.addWidget(self.execution_target)
        self.compute_resources_button = QPushButton("Resources…")
        self.compute_resources_button.setObjectName("secondaryActionBtn")
        self.compute_resources_button.setAutoDefault(False)
        self.compute_resources_button.setVisible(False)
        self.compute_resources_button.clicked.connect(
            lambda: show_restored_window(self.remote_controller.resource_window))
        status_layout.addWidget(self.compute_resources_button)
        status_layout.addWidget(cancel)
        status_layout.addWidget(unit_badge)
        self.operation_progress = OperationProgress(self, provider=lambda: (
            not self._local_analysis_in_progress() and (
                self.future is not None or self.analysis_job is not None
                or getattr(getattr(self, "remote_controller", None), "closing", False)
            ),
            self.status.text(),
        ))
        # Progress rows should only take up space while visible.
        for progress in (self.local_analysis_progress, self.operation_progress):
            policy = progress.sizePolicy()
            policy.setRetainSizeWhenHidden(False)
            progress.setSizePolicy(policy)
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
        preferences = self.solver_profile_store.settings if self._persist_solver_profiles else None
        self.peridynamic_results = PeridynamicResults(preferences)
        self.peridynamic_results.step_changed.connect(self._update_peridynamic_preview)
        right.addWidget(self.peridynamic_results)
        self.preview_error_details = QPlainTextEdit()
        self.preview_error_details.setObjectName("previewErrorDetails")
        self.preview_error_details.setAccessibleName("Preview error details")
        self.preview_error_details.setReadOnly(True)
        self.preview_error_details.setTextInteractionFlags(selectable_text)
        self.preview_error_details.setFixedHeight(6 * self.preview_error_details.fontMetrics().lineSpacing() + 12)
        self.preview_error_details.hide()
        right.addWidget(self.preview_error_details)

        from new_doe_page import DoEStudioWindow
        self.doe_window = DoEStudioWindow(self, preferences)
        self.doe_page = self.doe_window.doe
        self.lhs_page = self.doe_window.lhs
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
        self.workspace_tabs.addTab(QWidget(), "&Experimental Design")
        from flow_workspace import FlowWorkspace
        self.flow_workspace = FlowWorkspace(self.applied_structure_settings, self, preferences=self.solver_profile_store.settings,viewport_factory=create_preview_viewport)
        self.workspace_tabs.addTab(self.flow_workspace, "&Flow (CFD)")
        from thermal_workspace import ThermalWorkspace
        self.thermal_workspace = ThermalWorkspace(self.applied_structure_settings, self, preferences=self.solver_profile_store.settings,viewport_factory=create_preview_viewport)
        self.workspace_tabs.addTab(self.thermal_workspace, "&Thermal (CHT)")
        from mpm_workspace import MPMWorkspace
        self.mpm_workspace = MPMWorkspace(self.applied_structure_settings, self, self.solver_profile_store.settings)
        self.mpm_workspace_index = self.workspace_tabs.addTab(self.mpm_workspace, "MPM")
        def sync_workspace_context(index):
            if index == 1:
                self.workspace_tabs.setCurrentIndex(0)
                self._show_doe_workspace()
                return
            self.workspace_button.setText(workspace_names[index])
            self.workspace_actions.actions()[index].setChecked(True)
            flow_active = index in (2,3)
            mpm_active = index == self.mpm_workspace_index
            independent = flow_active or mpm_active
            for button in self._analysis_action_buttons:
                button.setVisible(not independent)
            self._show_analysis_blocker()
            fft_selected = self.setup_tabs.currentIndex() == self._fft_method_index
            peridynamic_selected = self.setup_tabs.currentIndex() == self._peridynamic_method_index
            if mpm_active:
                title = "MPM TPMS Compression"
            elif flow_active:
                title = "Transient Heat Transfer" if index == 3 else "Isothermal Flow"
            elif peridynamic_selected:
                title = "Peridynamic TPMS Compression"
            else:
                title = "FFT Homogenization" if fft_selected else "Nonlinear Compression"
            self.brand_title_label.setText(title)
            unit_badge.setText("Units: mm  °C  s  Pa" if index==3 else "Units: mm  m/s  Pa" if flow_active else "Units: mm  N  MPa")
            remote = getattr(self,"remote_controller",None)
            self.brand_subtitle_lbl.setText("TRUBA · remote compute" if remote is not None and remote.requested
                                            else self._local_workspace_solver_name())
            if remote is not None:
                self._update_remote_banner()
            self.sidebar_toggle.setVisible(not independent)
            mesh_preview = not independent and not (fft_selected or peridynamic_selected)
            camera_controls.show_display_modes(mesh_preview)
            self.show_platens_cb.setVisible(mesh_preview)
            self._sync_preview_readout()
            self.fft_preview_summary.setVisible(not independent and fft_selected)
            camera_controls.setVisible(not independent)
        self._sync_workspace_context = sync_workspace_context
        self.workspace_tabs.currentChanged.connect(sync_workspace_context)
        self.workspace_tabs.tabBar().hide()
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
        self.poll.timeout.connect(self._poll_mesh_export)
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
        self.mpm_workspace.remote_controller = self.remote_controller
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
        self.truba_session.start()
        QTimer.singleShot(0, self.start_preview)

    def _refresh_ui_language(self, *_):
        from standard_tests import PROFILES
        from ui_language import translate
        self.language_manager.localize_tree(self)
        titles = (
            "Nonlinear Compression", "FFT Homogenization", "Peridynamic TPMS Compression",
            "MPM TPMS Compression", "Isothermal Flow", "Transient Heat Transfer",
            *(f"Standard {profile.kind.title()}" for profile in PROFILES.values()),
        )
        title_label = self.brand_title_label
        title_metrics = title_label.fontMetrics()
        # Preserve the label's styled padding and border when sizing for other titles.
        current_text_width = title_metrics.size(Qt.TextFlag.TextSingleLine, title_label.text()).width()
        title_insets = title_label.sizeHint().width() - current_text_width
        title_label.setFixedWidth(
            max(title_metrics.size(Qt.TextFlag.TextSingleLine, translate(title)).width()
                for title in titles) + title_insets
        )
        self.preview_error_details.setAccessibleName(translate("Preview error details"))
        if self.setup_tabs.currentIndex() == self._fft_method_index:
            self._update_fft_preview_summary()
        elif self.result_preview_field.currentData() is not None:
            self.render_preview()
        selector_labels = {
            self.sidebar_toggle: tuple(translate(name) for name in self.method_button_labels),
            self.workspace_button: tuple(action.text() for action in self.workspace_menu.actions()
                                         if action.isVisible()),
            self.truba_connect_button: (translate("Connect"), translate("Connected")),
        }
        commands = (*(button for group in self._workspace_commands for button in group),
                    *self._compute_commands, self.about_button)
        # Reserve one common width for the longest word and its menu arrow.
        command_width = max(button.fontMetrics().horizontalAdvance(word)
                            + (23 if isinstance(button, QToolButton) else 16)
                            for button in commands
                            for label in selector_labels.get(button, (button.text(),))
                            for word in label.split())
        command_width = max(command_width, WORKSPACE_COMMAND_WIDTH)
        for button in commands:
            button.setProperty("managesLocalizedWidth", True)
            button.setFixedSize(command_width, WORKSPACE_COMMAND_HEIGHT)
        self.compute_resources_button.setProperty("managesLocalizedWidth", True)
        resources_width = self.compute_resources_button.fontMetrics().horizontalAdvance(
            self.compute_resources_button.text()) + 16
        self.compute_resources_button.setFixedSize(resources_width, CONTROL_HEIGHT)
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
        self.language_manager.localize_figure(self.mpm_workspace.figure)

    def _test_profile_changed(self, *_):
        from standard_tests import apply_profile
        identifier = self.test_profile_combo.currentData()
        current = replace(self.collect(),test_profile=self._edited_test_profile,
            size=self.controls['size'].value(),
            bottom_platen_contact=self.platen_contact_combos['bottom'].currentData(),
            top_platen_contact=self.platen_contact_combos['top'].currentData())
        self._switching_solver = True
        try:
            if self._edited_test_profile == 'custom' and identifier != 'custom':
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
        self._edited_test_profile = identifier
        self.geometry_changed()

    def _architecture_changed(self, *_):
        if self._switching_solver:return
        self._sync_test_profile_ui()
        self.geometry_changed()

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

    def _select_execution_target(self, _index):
        self.remote_controller.toggle.setChecked(self.execution_target.currentData() == "truba")
        # The controller can reject a change while an analysis is active.
        self._sync_execution_target()

    def _sync_execution_target(self):
        target = "truba" if self.remote_controller.requested else "local"
        self.compute_resources_button.setVisible(target == 'truba')
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
        self.mpm_workspace.update_execution_availability()
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
            detail = session.get('state') if controller.session_watch else None
            state, text = 'pending', 'TRUBA · ' + (detail or (
                'direct submission' if self.truba_session.connected else 'connect to submit'))
        elif session.get('ready') and controller.session_watch:
            state, text = 'pending', 'Local mode · TRUBA resources reserved'
        else:
            local_name = self._local_workspace_solver_name()
            text = f'SSH connected · {local_name}' if self.truba_session.connected else local_name
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

    def show_md_workspace(self):
        from esen_workspace import EsenWorkspace
        from window_state import show_restored_window

        if self.md_workspace is None:
            self.md_workspace = EsenWorkspace(self.truba_session, self)
        show_restored_window(self.md_workspace)

    def show_about(self):
        QMessageBox.about(
            self,
            "About RustMech",
            "<p><b>RustMech</b></p>"
            "<p>Desktop workspace for mechanical, flow, and thermal simulation.</p>"
            "<p>Licensed under GPL-3.0-or-later.</p>"
            '<p><a href="https://github.com/aserdargl/RustMech">RustMech on GitHub</a></p>',
        )

    def show_truba_sync(self):
        if self.sync_window is None:
            self.sync_window = TrubaSyncWindow(self.truba_session, self.language_manager, self)
        show_restored_window(self.sync_window)

    def show_result_history(self):
        from doe_studio import results_directory
        from esen_jobs import jobs_directory
        from result_history import ResultHistoryDialog, discover_results
        from truba_job_list import unfinished_records

        if self.result_history is None:
            preferences = QApplication.instance()._rustmech_window_state.settings
            records = unfinished_records(self.truba_session.state_dir, include_analysis_history=True)
            remote_folders = {Path(record["local_directory"]) for _, record in records
                              if record.get("state") == "downloaded" and isinstance(record.get("local_directory"), str)
                              and record["local_directory"]}
            analysis_folders = [Path(folder) for folder in preferences.value(ANALYSIS_RESULTS_FOLDERS, [], type=list)]
            results = discover_results(ROOT, results_directory().resolve(),
                                       esen_root=jobs_directory(), remote_folders=remote_folders,
                                       analysis_folders=analysis_folders)
            dialog = ResultHistoryDialog(results, self._open_saved_result, self)
            self.language_manager.localize_tree(dialog)
            dialog.finished.connect(lambda _: setattr(self, "result_history", None))
            self.result_history = dialog
        show_restored_window(self.result_history)

    def _open_saved_result(self, result):
        from ui_language import translate

        path = result.path
        try:
            workplace = {"Flow (CFD)": self.flow_workspace, "Thermal (CHT)": self.thermal_workspace,
                         "MPM": self.mpm_workspace}.get(result.kind)
            if workplace is not None:
                workplace.load_directory(path)
                self.workspace_tabs.setCurrentWidget(workplace)
                return True
            if result.kind == "Peridynamics":
                self.peridynamic_results.load(path)
                self._select_settings_method(self._peridynamic_method_index)
                self.render_preview()
                return True
            if result.kind == "DAMASK FFT":
                report = json.loads((path / "status.json").read_text(encoding="utf-8"))
                self._show_fft_results(report, path, remember=False)
                self._select_settings_method(self._fft_method_index)
                return True
            if result.kind == "Mechanic":
                if result.solver == "kratos" and self.show_kratos_results(folder=path, warn=False):
                    return True
                if (path / "03_Results" / "deformed_shape.vtu").is_file():
                    self._select_settings_method(0)
                    self._set_mechanical_result_preview(path, historical=True)
                    if self._display_mechanical_result():
                        return True
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            # A legacy or incomplete viewer payload can still be browsed on disk.
            pass
        if path.exists() and QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.absolute()))):
            return True
        QMessageBox.warning(self.result_history, translate("Results"),
                            translate("Cannot open saved result: {path}").format(path=path))
        return False

    def _open_truba_desktop(self):
        from ui_language import translate

        try:
            open_truba_desktop()
        except OSError as error:
            QMessageBox.warning(self, "HPC", translate(str(error)))

    def _update_hpc_button(self, status) -> None:
        from ui_language import translate

        connected = status.session is not None
        self.truba_button.setProperty("trubaConnected", connected)
        self.truba_button.style().unpolish(self.truba_button)
        self.truba_button.style().polish(self.truba_button)
        self.truba_connect_button.setText(translate("Connected" if connected else "Connect"))
        self.truba_connect_button.setEnabled(not connected)
        self._update_truba_connect_color(status.connection_state)
        self._update_remote_banner()

    def _update_truba_connect_color(self, state):
        button = self.truba_connect_button
        button.setProperty("state", state)
        button.style().unpolish(button)
        button.style().polish(button)
        button.update()

    def _connect_truba_desktop(self):
        from ui_language import translate

        try:
            connect_truba_desktop()
        except OSError as error:
            self._update_truba_connect_color("warning")
            QMessageBox.warning(self, "HPC", translate(str(error)))
        else:
            self._update_truba_connect_color(self.truba_session.status.connection_state)

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

    def _add_method_pages(self, settings: Settings) -> None:
        self.method_names = ["Finite Element"]
        self.method_button_labels = self.method_names.copy()
        methods = (
            ("FFT", "FFT", "FFT Homogenization", "Effective stiffness from periodic unit-cell tests."),
            ("Peridynamics", "Peridynamics", "Peridynamics", "Brittle fracture in TPMS compression."),
        )
        for tab, button_label, name, description in methods:
            self.method_names.append(name)
            self.method_button_labels.append(button_label)
            fields = self._add_setup_page(tab)
            self.setup_tabs.setTabToolTip(self.setup_tabs.count() - 1, name)
            heading = QLabel(name)
            heading.setObjectName("sectionSubheader")
            heading.setWordWrap(True)
            hint = QLabel(description)
            hint.setObjectName("mutedHintLabel")
            hint.setWordWrap(True)
            fields.addWidget(heading)
            fields.addWidget(hint)
            if tab == "FFT":
                self._fft_method_index = self.setup_tabs.count() - 1
                self._add_fft_geometry_controls(fields, settings)
                self._add_fft_controls(fields)
            elif tab == "Peridynamics":
                self._peridynamic_method_index = self.setup_tabs.count() - 1
                preferences = self.solver_profile_store.settings if self._persist_solver_profiles else None
                self.peridynamic_settings = PeridynamicSettingsPanel(settings, preferences)
                self.peridynamic_settings.changed.connect(self.refresh_validity)
                fields.addWidget(self.peridynamic_settings)
            fields.addStretch()

    def _add_fft_geometry_controls(self, fields, settings: Settings) -> None:
        defaults = Settings()
        outer_lengths = settings.dimensions
        if self._persist_solver_profiles:
            preferences = self.solver_profile_store.settings
            try:
                saved = json.loads(preferences.value("fft/geometry", "null", type=str))
                if saved is not None:
                    settings = settings_from_dict(saved)
                    outer_lengths = fft_dimensions(settings)
            except (TypeError, ValueError):
                pass
        family, name = resolve_family_and_name(settings.structure_family, settings.structure_name)
        if family not in ("tpms", "lattice"):
            family, name = defaults.structure_family, defaults.structure_name

        form = self.add_group(fields, "Structure / Dimensions", settings, ())
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        self.fft_family_combo = NoWheelComboBox()
        for key in ("tpms", "lattice"):
            self.fft_family_combo.addItem(STRUCTURE_FAMILIES[key], key)
        self.fft_family_combo.setCurrentIndex(self.fft_family_combo.findData(family))
        self.fft_structure_combo = NoWheelComboBox()
        self._populate_structure_items(family, name, combo=self.fft_structure_combo)
        self.fft_morphology_combo = NoWheelComboBox()
        for key, label in TPMS_MORPHOLOGIES.items():
            self.fft_morphology_combo.addItem(label, key)
        self.fft_morphology_combo.setCurrentIndex(
            max(0, self.fft_morphology_combo.findData(settings.tpms_morphology)))
        for label, combo, key in (
            ("Structure family", self.fft_family_combo, ""),
            ("Structure type", self.fft_structure_combo, "structure_name"),
            ("Morphology", self.fft_morphology_combo, "tpms_morphology"),
        ):
            label_widget = field_label(label, combo, tooltip=GEOMETRY_HELP.get(key, ""))
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
            combo.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            form.addRow(label_widget, combo)

        self.fft_cell_size = NoWheelDoubleSpinBox()
        self.fft_solid_fraction = NoWheelDoubleSpinBox()
        for label, control, key, minimum, maximum, step, unit in (
            ("Cell size", self.fft_cell_size, "cell_size", 0.1, 100, 0.5, "mm"),
            ("Solid fraction", self.fft_solid_fraction, "solid_fraction", 1, 99, 1, "%"),
        ):
            value = getattr(settings, key)
            if not isinstance(value, (int, float)) or not np.isfinite(value) or value <= 0:
                value = getattr(defaults, key)
            if key == "solid_fraction" and value >= 100:
                value = defaults.solid_fraction
            control.setRange(min(minimum, value), max(maximum, value))
            control.setSingleStep(step)
            control.setValue(value)
            tooltip = GEOMETRY_HELP[key] if key == "solid_fraction" else ""
            form.addRow(field_label(label, control, tooltip=tooltip),
                        stepper_with_unit(control, unit))
            control.setKeyboardTracking(False)
            control.valueChanged.connect(self.refresh_validity)

        self.fft_outer_sizes = {}
        for axis, key, length in zip("XYZ", ("size_x", "size_y", "size"), outer_lengths):
            control = NoWheelDoubleSpinBox()
            control.setDecimals(3)
            control.setRange(0.001, 1_000_000)
            control.setSingleStep(0.5)
            if self._persist_solver_profiles and preferences.contains(f"fft/outer{axis}"):
                length = preferences.value(f"fft/outer{axis}", type=float)
                # Older profiles stored zero for one cell. Resolve it once on import.
                if length == 0:
                    length = self.fft_cell_size.value()
            control.setValue(length if isinstance(length, (int, float)) and np.isfinite(length)
                             and length > 0 else defaults.size)
            self.fft_outer_sizes[key] = control
            control.setKeyboardTracking(False)
            form.addRow(field_label(f"Outer {axis} (mm)", control), control)
            control.valueChanged.connect(self.refresh_validity)

        def family_changed():
            family = self.fft_family_combo.currentData()
            self._populate_structure_items(family, combo=self.fft_structure_combo)
            form.setRowVisible(self.fft_morphology_combo, family == "tpms")
            self.refresh_validity()

        self._applied_fft_geometry = self._fft_geometry_values()
        if self._persist_solver_profiles:
            try:
                draft = json.loads(preferences.value("fft/geometryDraft", "null", type=str))
                if draft is not None:
                    draft = settings_from_dict(draft)
                    family, name = resolve_family_and_name(draft.structure_family, draft.structure_name)
                    if self.fft_family_combo.findData(family) >= 0:
                        self.fft_family_combo.setCurrentIndex(self.fft_family_combo.findData(family))
                        self._populate_structure_items(family, name, combo=self.fft_structure_combo)
                        self.fft_morphology_combo.setCurrentIndex(
                            max(0, self.fft_morphology_combo.findData(draft.tpms_morphology)))
                        controls = dict(self.fft_outer_sizes, cell_size=self.fft_cell_size,
                                        solid_fraction=self.fft_solid_fraction)
                        for key, control in controls.items():
                            value = getattr(draft, key)
                            if isinstance(value, (int, float)) and np.isfinite(value):
                                with QSignalBlocker(control):
                                    control.setValue(value)
            except (TypeError, ValueError):
                pass
        form.setRowVisible(self.fft_morphology_combo, self.fft_family_combo.currentData() == "tpms")
        self.fft_family_combo.currentIndexChanged.connect(family_changed)
        self.fft_structure_combo.currentIndexChanged.connect(self.refresh_validity)
        self.fft_morphology_combo.currentIndexChanged.connect(self.refresh_validity)
        self.fft_structure_apply_button = QPushButton("Apply")
        self._add_structure_apply_row(form, self.fft_structure_apply_button)
        self.fft_structure_apply_button.clicked.connect(self.apply_fft_geometry)

    @staticmethod
    def _add_structure_apply_row(form, button):
        button.setObjectName("structureApplyBtn")
        button.setAutoDefault(False)
        button.setDefault(False)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        width = max(button.fontMetrics().horizontalAdvance(label) for label in ("Apply", "Uygula")) + 24
        button.setFixedSize(max(width, button.minimumWidth()), CONTROL_HEIGHT)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(button)
        form.addRow(row)

    def _fft_geometry_values(self):
        family = self.fft_family_combo.currentData()
        return dict(
            structure_family=family,
            structure_name=self.fft_structure_combo.currentData(),
            tpms_morphology=self.fft_morphology_combo.currentData() if family == "tpms" else "sheet",
            **{key: control.value() for key, control in self.fft_outer_sizes.items()},
            cell_size=self.fft_cell_size.value(),
            solid_fraction=self.fft_solid_fraction.value(),
        )

    def apply_fft_geometry(self):
        for control in (self.fft_cell_size, self.fft_solid_fraction, *self.fft_outer_sizes.values()):
            control.interpretText()
        geometry = self._fft_geometry_values()
        errors = validate_periodic_fft(self._fft_settings(self.collect(), geometry=geometry))
        if errors:
            self.status.setText("\n".join(errors))
            self._update_status_visuals("error", "● VALIDATION ERROR")
            return
        self._applied_fft_geometry = geometry
        if self._persist_solver_profiles:
            self._save_fft_preferences()
        self.refresh_validity()

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

    def _fft_settings(self, settings: Settings, *, geometry=None) -> Settings:
        material = self.fft_material_editor.material_settings()
        values = {name: getattr(material, name) for name in MATERIAL_FIELDS}
        values.update(material_model="linear_elastic", hardening_curve=())
        if geometry is None:
            geometry = self._applied_fft_geometry
        return replace(
            settings, **geometry, **values, stl_resolution=None,
            imported_geometry_path="", imported_geometry_name="", imported_geometry_role="compression",
            test_profile="custom", specimen_architecture="solid", core_fraction=100.0,
            random_seed=Settings().random_seed,
        )

    def _show_fft_results(self, result, folder, *, remember=True):
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
        if remember and self._persist_solver_profiles:
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
            peridynamic = index == self._peridynamic_method_index
            if index != 0:
                self._analysis_after_mesh = None
            if self.workspace_tabs.currentIndex() == 0:
                self._sync_workspace_context(0)
            self.refresh_validity()
            if was_fft != is_fft or peridynamic or self._showing_peridynamic_preview:
                self._fft_preview_size = None
                if (is_fft or peridynamic or self.preview is not None
                        or self.result_preview_field.currentData() is not None):
                    self.render_preview(reset=True)
                else:
                    self.plotter.clear()
                    self.plotter.render()
                if not peridynamic:
                    self._peridynamic_preview_folder = None
                    self._showing_peridynamic_preview = False

    def _local_workspace_solver_name(self):
        if self.workspace_tabs.currentIndex() == self.mpm_workspace_index:
            return "Kratos · MPM"
        if self.workspace_tabs.currentIndex() in (2, 3):
            return "OpenFOAM · WSL"
        if self.setup_tabs.currentIndex() == self._fft_method_index:
            return "DAMASK · FFT"
        if self.setup_tabs.currentIndex() == self._peridynamic_method_index:
            return "Peridynamics · PMB"
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
        self, family_key: str, selected_name: str = "", *, combo=None
    ) -> None:
        if combo is None:
            combo = self.structure_combo
        items = ALL_STRUCTURES_BY_FAMILY.get(family_key, ALL_STRUCTURES_BY_FAMILY["tpms"])
        with QSignalBlocker(combo):
            combo.clear()
            for item in items:
                combo.addItem(item, item)
            combo.setCurrentIndex(max(0, combo.findData(selected_name)))

    def _on_family_changed(self, *_) -> None:
        family_key = str(self.family_combo.currentData() or "tpms")
        if family_key == 'imported':
            self._sync_import_controls()
            return
        self._generated_family = family_key
        self.imported_geometry_path = ''
        self.imported_geometry_name = ''
        self.imported_geometry_height = None
        self._populate_structure_items(family_key)
        self._sync_import_controls()
        self._sync_structure_ui_visibility()
        self.geometry_changed()

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
        remote = getattr(self, "remote_controller", None)
        self.kratos_panel.set_execution_target(remote is not None and remote.requested)
        flexure = kratos and self.test_profile_combo.currentData() == 'iso178_preferred'
        for index in range(self.mesh_type_combo.count()):
            mesh_type = self.mesh_type_combo.itemData(index)
            available = mesh_type in cap.supported_mesh_types
            tooltip = MESH_TYPE_LABELS.get(mesh_type, mesh_type)
            if remote is not None and remote.requested:
                available = remote.ready and mesh_type in remote.capabilities.get("mesh_types",[])
            if flexure and mesh_type != 'HEX8':
                available = False
                tooltip = "Kratos three-point flexure requires HEX8 with aligned support/loading lines."
            elif kratos and mesh_type in KRATOS_QUADRATIC_MESH_TYPES and self.test_profile_combo.currentData() != 'astm_e8_subsize':
                tooltip = "Kratos HEX20/TET10 compression uses displacement control and penalty mortar with self-contact disabled."
            if mesh_type in DERIVED_SOLID_MESH_TYPES:
                available = not (remote is not None and remote.requested) or (
                    remote.ready and mesh_type in remote.capabilities.get("mesh_types", [])
                )
                tooltip = ("Mixed hex and pyramid cells for mesh export."
                           if mesh_type.startswith("HYBRID") else
                           "Voxel-derived solid elements for mesh export.")
            self.mesh_type_combo.setItemData(index, tooltip, Qt.ItemDataRole.ToolTipRole)
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
                "Frictional / frictionless platens · HEX8 / HEX20 / TET4 / TET10 · HEX8 penalty self-contact"
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
            quadratic_platens = kratos and not kinematic and self.mesh_type_combo.currentData() in KRATOS_QUADRATIC_MESH_TYPES
            method.model().item(method.findData('alm')).setEnabled(not kratos or (sc_mode=='disabled' and not quadratic_platens))
            if quadratic_platens:
                method.setCurrentIndex(method.findData('penalty'))

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

        dialog = self._mesh_convergence_dialog
        if dialog is None:
            preferences = self.solver_profile_store.settings if getattr(self, "_persist_solver_profiles", False) else None
            dialog = MeshConvergenceDialog(
                self.applied_structure_settings(), generate_volume_mesh, run_batch_experiment,
                owner=self, preferences=preferences,
            )
            self._mesh_convergence_dialog = dialog
            dialog.finished.connect(self._finish_mesh_convergence)
        if dialog.isMinimized():
            dialog.setWindowState(dialog.windowState() & ~Qt.WindowState.WindowMinimized)
        show_restored_window(dialog)

    def _finish_mesh_convergence(self, _result):
        dialog = self._mesh_convergence_dialog
        self._mesh_convergence_dialog = None
        try:
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
                    (self.mesh_type_combo, selected.mesh_type),
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

    def collect(self, *, draft=False, applied_structure=False) -> Settings:
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
        if applied_structure:
            settings = replace(settings, **{
                name: getattr(self.active_geometry, name) for name in STRUCTURE_FIELDS
            })
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
            self.geometry_changed()
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
        self.geometry_changed()

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

    def _clamp_local_mesh_size(self) -> None:
        global_size = self.controls["mesh_resolution"].value()
        local_size = self.controls["local_mesh_size"]
        if local_size.value() > global_size:
            if local_size.minimum() > global_size:
                local_size.setMinimum(global_size)
            local_size.setValue(global_size)

    def _sync_mesh_type(self) -> None:
        local_size = self.controls["local_mesh_size"]
        tetrahedral = self.mesh_type_combo.currentData() in TETRAHEDRAL_MESH_TYPES
        self._mesh_form.setRowVisible(local_size.parentWidget(), tetrahedral)
        self._mesh_form.setRowVisible(self.tetra_strategy_combo, tetrahedral)
        if hasattr(self, "geometry_apply_button"):
            if hasattr(self, "kratos_panel"):
                self._sync_contact_ui()
            self.geometry_changed()

    def applied_structure_settings(self) -> Settings:
        return self.collect(applied_structure=True)

    def preview_settings(self) -> Settings:
        return replace(self.applied_structure_settings(), **{
            name: getattr(self.active_geometry, name) for name in MESH_FIELDS
        })

    def has_pending_geometry(self, fields=GEOMETRY_FIELDS, *, edited=None) -> bool:
        if edited is None:
            edited = self.collect()
        return any(getattr(edited, name) != getattr(self.active_geometry, name) for name in fields)

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
        if self._persist_solver_profiles:
            self.solver_profile_store.save_workspace(self.collect(draft=True), applied=settings)
            self.solver_profile_store.settings.sync()
        self._mesh_requested = False
        self._analysis_after_mesh = None
        self.result_preview_field.setCurrentIndex(0)
        self.mode.setCurrentIndex(0)
        self.failed_key = None
        self.debounce.stop()
        self._set_analysis_blocker("Preparing geometry preview…")
        self.start_preview()

    def apply_mesh(self, *_) -> None:
        if self.has_pending_geometry(STRUCTURE_FIELDS):
            return
        self.apply_geometry()
        if not validate_geometry(self.collect()):
            self._mesh_requested = True
            self.mode.setCurrentIndex(1)
            self.start_preview()

    def export_mesh_to_ansys(self) -> None:
        if not self.mesh_export_button.isEnabled():
            return
        from file_dialog import QFileDialog
        from ansys_mesh_export import ansys_export_filters, write_ansys_mesh
        from ui_language import translate

        preview = self.preview
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export to ANSYS", "rustmech_mesh",
            ";;".join(map(translate, ansys_export_filters(preview.mesh))),
            default_suffix="cdb",
        )
        if not filename or self.disposed:
            return
        if self.preview is not preview or preview.key != preview_key(self.collect()):
            return
        self._mesh_export_future = self.executor.submit(write_ansys_mesh, preview.mesh, Path(filename))
        self.mesh_export_button.setEnabled(False)
        self.status.setText("Exporting mesh to ANSYS…")

    def _poll_mesh_export(self) -> None:
        future = self._mesh_export_future
        if future is None or not future.done():
            return
        from ui_language import translate

        self._mesh_export_future = None
        self.refresh_validity()
        try:
            path = future.result()
        except Exception as error:
            QMessageBox.warning(self, "ANSYS mesh export failed", str(error))
        else:
            message = translate("ANSYS mesh saved to") + f"\n{path}"
            QMessageBox.information(self, "Export to ANSYS", message)

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

    def _set_analysis_blocker(self, reason: str) -> None:
        self._analysis_blocker_reason = reason
        self.apply_button.setEnabled(not reason)
        self._show_analysis_blocker()

    def _show_analysis_blocker(self) -> None:
        reason = self._analysis_blocker_reason if self.workspace_tabs.currentIndex() in (-1, 0) else ""
        self.analysis_blocker.setText(reason.partition("\n")[0])
        self.analysis_blocker.setToolTip(reason)
        self.analysis_blocker.setVisible(not self._local_analysis_in_progress())

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
        direct = remote and not controller.ready and controller.auth() is not None
        if remote:
            from remote_operations import required_capability
            try:
                capability = required_capability(dict(operation="mechanical", settings=dict(
                    solver_backend=settings.solver_backend, test_profile=settings.test_profile)))
                controller.require_analysis(capability, settings)
            except (ValueError,RuntimeError) as error: errors.append(str(error))
            try: controller.require("mesh",settings)
            except (ValueError,RuntimeError) as error: remote_error = str(error)
        elif settings.solver_backend == "kratos" and local_kratos_python() is None:
            errors.append(INSTALL_REQUIRED)
        geometry_valid = not validate_geometry(settings, include_mesh=False)
        applied_geometry_valid = not validate_geometry(self.applied_structure_settings(), include_mesh=False)
        self.geometry_apply_button.setEnabled(applied_geometry_valid)
        self.structure_apply_button.setEnabled(geometry_valid)
        self.mesh_apply_button.setEnabled(
            not self.has_pending_geometry(STRUCTURE_FIELDS, edited=settings) and not validate_geometry(settings))
        if remote_error:
            self.mesh_apply_button.setEnabled(False)
            if not direct:
                self.geometry_apply_button.setEnabled(False)
                self.structure_apply_button.setEnabled(False)
        current = (
            self.preview
            and self.preview.key == preview_key(settings)
        )
        mesh_errors = self.preview.mesh.solve_errors if current and self.preview.mesh is not None else []
        pending = self.has_pending_geometry(edited=settings)
        self.mesh_export_button.setEnabled(
            bool(current and self.preview.mesh is not None and not pending
                 and self._mesh_export_future is None)
        )
        method = self.setup_tabs.currentIndex()
        idle = self.analysis_job is None and self.future is None
        if method == self._fft_method_index:
            fft_settings = self._fft_settings(settings)
            fft_errors = validate_periodic_fft(fft_settings)
            self._request_fft_preview(fft_settings, valid=not fft_errors)
            remote_fft = remote
            self.fft_distribution.setEnabled(os.name == 'nt' and not remote_fft)
            if remote_fft:
                try:
                    controller.require_analysis("damask")
                except (ValueError, RuntimeError) as error:
                    fft_errors.append(str(error))
            fft_pending = self._fft_geometry_values() != self._applied_fft_geometry
            self.fft_structure_apply_button.setEnabled(fft_pending)
            blockers = [PENDING_STRUCTURE] if fft_pending else fft_errors
        elif method == self._peridynamic_method_index:
            blockers = validate_compression(*self.peridynamic_settings.inputs())
            if remote:
                try:
                    controller.require_analysis("peridynamics")
                except (ValueError, RuntimeError) as error:
                    blockers.append(str(error))
            if self.peridynamic_settings.has_pending_geometry():
                blockers.insert(0, PENDING_STRUCTURE)
        elif method != 0:
            blockers = ["The selected analysis method is not available."]
        else:
            blockers = errors + mesh_errors
            if pending:
                blockers.append("Geometry or mesh settings have changed. Click Apply to update the preview.")
            elif not (current or remote):
                blockers.append(
                    self._preview_failure_reason if self.failed_key is not None and self._preview_failure_reason
                    else "Preparing geometry preview…"
                )
        if self.analysis_job is not None:
            blocker = "Analysis running..."
        elif self.future is not None:
            blocker = "Building FE mesh in background…" if self._mesh_requested else "Preparing geometry preview…"
        else:
            blocker = blockers[0] if blockers else ""
        self._set_analysis_blocker(blocker)
        if self.analysis_job is not None:
            self._show_analysis_progress(self.analysis_job)
            self._update_status_visuals("busy", "● ANALYSING", False)
            return
        if method == self._fft_method_index:
            ready_text = "Ready to submit DAMASK FFT to TRUBA." if remote_fft else "Ready to run local DAMASK FFT."
            self.status.setText("\n".join(blockers) if blockers else ready_text)
            state, badge = ("error", "● VALIDATION ERROR") if blockers else ("ready", "● READY TO SOLVE")
            if fft_pending:
                state, badge = "pending", "● PENDING APPLY"
            self._update_status_visuals(state, badge, False)
            return
        if method == self._peridynamic_method_index:
            ready = "Ready to submit TPMS compression to TRUBA." if remote else "Ready to run local TPMS compression."
            self.status.setText("\n".join(blockers) if blockers else ready)
            state, badge = ("error", "● VALIDATION ERROR") if blockers else ("ready", "● READY TO SOLVE")
            if self.peridynamic_settings.has_pending_geometry():
                state, badge = "pending", "● PENDING APPLY"
            self._update_status_visuals(state, badge, False)
            return
        if method != 0:
            self.status.setText(blockers[0])
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
        message = "Preparing geometry preview…"
        self._set_analysis_blocker(message)
        self.status.setText(message)
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
                if not self._mesh_requested and controller.auth() is not None:
                    self.refresh_validity()
                    return
                self.status.setText(str(error))
                self._show_preview_error_details(str(error))
                if not self.apply_button.isEnabled():
                    self._set_analysis_blocker(str(error))
                return
        errors = validate_geometry(settings, include_mesh=self._mesh_requested)
        if errors:
            self.status.setText("\n".join(errors))
            self._show_preview_error_details("\n".join(errors))
            self._set_analysis_blocker(errors[0])
            self._update_status_visuals(
                "error", "● GEOMETRY ERROR", self.has_pending_geometry()
            )
            return
        key = preview_key(settings)
        if self.future is not None or key == self.failed_key:
            return
        self.preview_error_details.hide()
        self.preview_error_details.clear()
        if self.preview and self.preview.key == key and (not getattr(self, '_mesh_requested', False) or self.preview.mesh is not None):
            self.refresh_validity()
            return
        self.worker_key = key
        message = "Building FE mesh in background…" if self._mesh_requested else "Preparing geometry preview…"
        self.status.setText(message)
        self._update_status_visuals(
            "working", "● BUILDING FE MESH" if getattr(self, '_mesh_requested', False) else "● BUILDING GEOMETRY", self.has_pending_geometry()
        )
        self.mesh_progress_updates = SimpleQueue()
        self._last_mesh_stage = None
        self.mesh_cancelled = Event()
        self._preview_detach = Event()
        self.mesh_progress.show()
        self._preview_includes_mesh = self._mesh_requested
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
        self._set_analysis_blocker(message)

    def cancel_meshing(self) -> None:
        if self.future is None:
            return
        self.mesh_cancelled.set()
        self._analysis_after_mesh = None
        self.debounce.stop()
        self.cancel_mesh_button.setEnabled(False)
        message = "Cancelling mesh generation…"
        self._set_analysis_blocker(message)
        self.status.setText(message)

    def _show_mesh_stage(self) -> None:
        stage = None
        try:
            while True:
                stage = self.mesh_progress_updates.get_nowait()
        except Empty:
            if stage:
                self._last_mesh_stage = stage
                if not self.mesh_cancelled.is_set():
                    self.status.setText(stage)

    def _show_preview_error_details(self, message, *, error=None, settings=None):
        from ui_language import translate

        lines = [translate(message)]
        if settings is not None:
            target = "TRUBA" if self._preview_is_remote else "Local"
            lines.append(translate("Execution target: {target}").format(target=translate(target)))
            if self._preview_includes_mesh:
                lines.append(translate("Mesh: {element}, global size {size:g} mm").format(
                    element=settings.mesh_type, size=settings.mesh_resolution
                ))
                if settings.mesh_type in TETRAHEDRAL_MESH_TYPES:
                    lines.append(translate("Tetrahedral meshing: {method}, local size {size:g} mm").format(
                        method=settings.tetra_strategy, size=settings.local_mesh_size
                    ))
            if self._last_mesh_stage:
                lines.append(translate("Last reported stage: {stage}").format(
                    stage=translate(self._last_mesh_stage)
                ))
        if error is not None:
            lines.extend(("", "".join(traceback.format_exception(error)).rstrip()))
        self.preview_error_details.setPlainText("\n".join(lines))
        self.preview_error_details.show()

    def poll_preview(self) -> None:
        if self.future is None:
            return
        done = self.future.done()
        self._show_mesh_stage()
        if not done:
            return
        future, self.future = self.future, None
        settings = self.preview_settings()
        key = preview_key(settings)
        self.mesh_progress.hide()
        self.cancel_mesh_button.hide()
        if self.mesh_cancelled.is_set():
            controller = getattr(self,"remote_controller",None)
            if getattr(self,"_preview_is_remote",False):
                from concurrent.futures import CancelledError
                try: future.result()
                except CancelledError: pass
                except Exception as error:
                    message = f"Remote mesh operation stopped without a confirmed cancellation: {error}. Retrieve the saved job record."
                    self.failed_key = key
                    self._preview_failure_reason = message
                    self.refresh_validity()
                    self.status.setText(message)
                    self._show_preview_error_details(message, error=error, settings=settings)
                    return
            message = "Mesh generation cancelled. Select Update preview to retry."
            self.failed_key = key
            self._preview_failure_reason = message
            self.refresh_validity()
            self.status.setText(message)
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
                    requested_settings, folder = requested
                    if (self.collect() == requested_settings and not self.has_pending_geometry()
                            and self.apply_button.isEnabled()):
                        self._launch_mechanical_analysis(requested_settings, folder)
        except Exception as exc:
            self._analysis_after_mesh = None
            if self.worker_key == key:
                reason = str(exc).partition("\n")[0] or type(exc).__name__
                message = f"Preview error: {reason}"
                self.failed_key = key
                self._preview_failure_reason = message
                self.status.setText(message)
                self._show_preview_error_details(message, error=exc, settings=settings)
                self._set_analysis_blocker(message)
                self._update_status_visuals(
                    "error", "● PREVIEW ERROR", self.has_pending_geometry()
                )
        if self.worker_key != key and not self.debounce.isActive():
            self.start_preview()
        elif self.preview is not None and self.preview.mesh is None and getattr(self, '_mesh_requested', False) and self.failed_key != key:
            self.start_preview()

    def _request_fft_preview(self, settings, *, valid):
        grid = self.fft_grid.value()
        key = (grid, settings.structure_family, settings.structure_name, settings.tpms_morphology,
               settings.cell_size, settings.dimensions, settings.solid_fraction) if valid else None
        old_key = self._fft_preview_request[0] if self._fft_preview_request else None
        if key != old_key:
            self._fft_preview_request = (key, settings, grid) if valid else None
            self._fft_preview_result = None
            self._fft_preview_error = ""
            self.render_preview()
        self._update_fft_preview_summary()

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
                    self._update_fft_preview_summary()
                    self.render_preview()
        if (self.setup_tabs.currentIndex() == self._fft_method_index
                and self._fft_preview_request is not None
                and self._fft_preview_result is None and not self._fft_preview_error):
            request = self._fft_preview_request
            _, settings, grid = request
            future = self._fft_preview_executor.submit(_build_fft_preview, settings, grid)
            self._fft_preview_future = (request, future)

    def _update_fft_preview_summary(self):
        from ui_language import translate

        if self._fft_preview_request is None:
            text = "DAMASK voxel preview unavailable. Check the settings below."
        elif self._fft_preview_error:
            text = f"Could not build the DAMASK voxel preview: {self._fft_preview_error}"
        elif self._fft_preview_result is None:
            text = "Preparing DAMASK voxel preview…"
        else:
            _, settings, grid = self._fft_preview_request
            _, _, count = self._fft_preview_result
            total = grid ** 3
            ratio = self.fft_void_ratio.value()
            young_modulus = self.fft_material_editor.numeric_values()["young_modulus"]
            spacing = " × ".join(f"{length / grid:.4g}" for length in fft_dimensions(settings))
            text = (f"{grid} × {grid} × {grid} · Voxel spacing: {spacing} mm\n"
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
        cell_size = fft_dimensions(self._fft_preview_request[1])
        self._finish_preview_view(camera, reset or cell_size != self._fft_preview_size)
        self._fft_preview_size = cell_size

    def _sync_preview_readout(self, *_):
        fft_selected = self.setup_tabs.currentIndex() == self._fft_method_index
        peridynamic = self.setup_tabs.currentIndex() == self._peridynamic_method_index
        has_results = self._result_preview_folder is not None
        self.result_preview_controls.setVisible(has_results and not (fft_selected or peridynamic))
        self.peridynamic_results.setVisible(peridynamic and self.peridynamic_results.folder is not None)
        model_view = not (fft_selected or peridynamic) and self.result_preview_field.currentData() is None
        self.mesh_kpis.setVisible(model_view)
        self._mesh_details_action.setVisible(model_view)
        self.telemetry_details.setVisible(model_view and self._mesh_details_action.isChecked())

    def _update_peridynamic_preview(self):
        if self.setup_tabs.currentIndex() == self._peridynamic_method_index:
            self.render_preview()

    def _render_peridynamic_preview(self, reset):
        from ui_language import translate

        self._showing_peridynamic_preview = True
        camera = self.plotter.camera_position
        self.plotter.clear()
        self.fragment_status.hide()
        self.plotter.set_background("#f0f0f0", top="#ffffff")
        results = self.peridynamic_results
        if results.folder is None:
            self.plotter.render()
            return
        try:
            frame = pv.read(results.frame_path())
            self.plotter.add_mesh(frame, scalars="Damage", clim=(0, 1), cmap="YlOrRd",
                                  style="points", point_size=6, render_points_as_spheres=True,
                                  scalar_bar_args={"title": translate("Bond damage")})
            self._finish_preview_view(camera, reset or results.folder != self._peridynamic_preview_folder)
            self._peridynamic_preview_folder = results.folder
        except (OSError, ValueError, RuntimeError) as error:
            self.status.setText(f"Cannot display this result: {error}")
            self.plotter.render()

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
        if self.setup_tabs.currentIndex() == self._peridynamic_method_index:
            self._render_peridynamic_preview(reset)
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
                self.applied_structure_settings, generate_volume_mesh, run_batch_experiment, self)
            self.analysis_queue_widget.remote_controller = self.remote_controller
            self.analysis_queue_widget.preview_requested.connect(self.load_from_doe_row)
            self.analysis_queue_window = AnalysisQueueWindow(self.analysis_queue_widget, self)
            self.analysis_queue_window.setStyleSheet(STUDIO_STYLESHEET)

    def _show_analysis_queue(self):
        self._ensure_analysis_queue()
        self.analysis_queue_window.open_workspace()

    def _enqueue_current_model(self):
        if self.workspace_tabs.currentIndex() != 0:
            QMessageBox.information(self, "Analysis queue", "Select the Mechanic workspace and configure the model first.")
            return
        try:
            self._ensure_analysis_queue()
            row = self.analysis_queue_widget.add_snapshot(self.applied_structure_settings())
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Analysis queue", str(error))
            return
        self.status.setText(f"{row.exp_id} added to the analysis queue. Analysis has not started.")
        if self.design_pool is not None and self.design_pool.pending:
            self.design_pool.prepare_next()
        else:
            self.analysis_queue_window.open_workspace()

    def load_from_doe_row(self, row_settings: Settings, *, preview=True) -> None:
        self.result_preview_field.setCurrentIndex(0)
        self._edited_test_profile = row_settings.test_profile
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
        self._clamp_local_mesh_size()
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
        remote = self.remote_controller.requested
        folder = choose_analysis_results_folder(self)
        if folder is None or remote != self.remote_controller.requested:
            return
        if self.setup_tabs.currentIndex() == self._fft_method_index:
            self._start_fft(self._fft_settings(settings), folder)
            return
        if self.setup_tabs.currentIndex() == self._peridynamic_method_index:
            self._start_peridynamic_compression(folder)
            return
        if not remote and self.preview.mesh is None:
            self._analysis_after_mesh = settings, folder
            self._mesh_requested = True
            self.failed_key = None
            self.start_preview()
            self.refresh_validity()
            return
        self._launch_mechanical_analysis(settings, folder)

    def _launch_mechanical_analysis(self, settings, folder):
        try:
            save_settings(settings)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Cannot save settings", str(exc))
            return
        try:
            if self.remote_controller.requested:
                from truba_remote import run_remote_mechanics
                context,auth = self.remote_controller.require_analysis(settings.solver_backend,settings)
                preview = self.preview
                source = ""
                if preview is not None and preview.mesh is not None and preview.key == preview_key(settings):
                    source = getattr(preview, "remote_source", "")
                self.analysis_job = AnalysisProcess(
                    run_remote_mechanics, settings, context, source, remote_auth=auth,
                    remote_options={"directory": folder / "TRUBA"},
                )
                self.truba_session.submitting = True
            else:
                target = partial(run_single_analysis, result_folder=folder)
                self.analysis_job = AnalysisProcess(target, settings, self.preview.mesh)
        except Exception as exc:
            QMessageBox.critical(self, "Cannot start analysis", str(exc))
            return
        self._set_analysis_blocker("Analysis running...")
        self.force_stop_button.setEnabled(True)
        self._show_analysis_progress(self.analysis_job)
        self._update_status_visuals("busy", "● ANALYSING", False)
        self.analysis_poll.start()

    def _start_fft(self, settings, folder):
        remote = self.remote_controller.requested
        distribution = self.fft_distribution.text().strip()
        if not remote and os.name == "nt" and not distribution:
            self.status.setText("Enter a WSL distribution for DAMASK.")
            return
        grid, void_ratio = self.fft_grid.value(), self.fft_void_ratio.value()
        try:
            if remote:
                from truba_remote import submit_remote_analysis
                context, auth = self.remote_controller.require_analysis("damask")
                request = dict(operation="fft_solve", settings=asdict(settings), grid=grid, void_ratio=void_ratio)
                self.analysis_job = AnalysisProcess(
                    submit_remote_analysis, request, context, remote_auth=auth,
                    remote_options={"directory": folder / "TRUBA"},
                )
                self.truba_session.submitting = True
            else:
                self.analysis_job = DamaskAnalysisProcess(settings, grid, void_ratio, distribution, folder=folder)
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
        for axis, key in zip("XYZ", self.fft_outer_sizes):
            preferences.setValue(f"fft/outer{axis}", self._applied_fft_geometry[key])
        preferences.setValue("fft/geometry", json.dumps(self._applied_fft_geometry))
        preferences.setValue("fft/geometryDraft", json.dumps(self._fft_geometry_values()))
        material = self.fft_material_editor.material_settings()
        preferences.setValue("fft/solidMaterial", json.dumps(
            {key: getattr(material, key) for key in MATERIAL_FIELDS}))
        preferences.setValue("fft/grid", self.fft_grid.value())
        preferences.setValue("fft/voidRatio", self.fft_void_ratio.value())
        preferences.setValue("fft/distribution", self.fft_distribution.text())
        preferences.sync()

    def _start_peridynamic_compression(self, folder):
        settings, options = self.peridynamic_settings.inputs()
        try:
            if self.remote_controller.requested:
                from truba_remote import submit_remote_analysis

                context, auth = self.remote_controller.require_analysis("peridynamics")
                request = dict(operation="peridynamic_solve", settings=asdict(settings), options=asdict(options))
                self.analysis_job = AnalysisProcess(
                    submit_remote_analysis, request, context, remote_auth=auth,
                    remote_options={"directory": folder / "TRUBA"},
                )
                self.truba_session.submitting = True
            else:
                self.analysis_job = AnalysisProcess(run_local_compression, settings, options, folder)
        except (OSError, ValueError, RuntimeError) as error:
            self.status.setText(str(error))
            self._update_status_visuals("error", "● FAILED", False)
            return
        self.peridynamic_settings.save()
        self.refresh_validity()
        self.force_stop_button.setEnabled(True)
        self.analysis_poll.start()

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
                    if result.get("operation") in ("mechanical", "fft_solve", "peridynamic_solve"):
                        self._show_remote_analysis_result(result, record, open_workspace=True)
                    else:
                        self.status.setText(f'TRUBA results retrieved: {Path(record["local_directory"]) / "output"}')
                else:
                    self.truba_session.sync_background()
                    self.status.setText("Track this job in TRUBA WORKS. Results download automatically when connected.")
                return
            auth = self.truba_session.analysis_auth()
            if auth is None:
                from truba_shared_session import DISCONNECTED_MESSAGE
                from ui_language import translate

                raise ConnectionError(translate(DISCONNECTED_MESSAGE))
            if record.get("kind")=="rustmech_remote":
                from truba_remote import retrieve_remote_operation
                self.analysis_job=AnalysisProcess(retrieve_remote_operation,filename,remote_auth=auth)
            else:
                self.analysis_job=AnalysisProcess(run_resume_analysis,filename,remote_auth=auth)
        except Exception as error:
            QMessageBox.warning(self,"TRUBA job",str(error))
            return
        self._set_analysis_blocker("Waiting for TRUBA job and retrieving results…")
        self.force_stop_button.setEnabled(True)
        self.status.setText("Waiting for TRUBA job and retrieving results…")
        self.analysis_poll.start()

    def _latest_mechanical_result(self) -> Path | None:
        if (not self._result_preview_is_historical and self._result_preview_folder is not None
                and (self._result_preview_folder / "deformed_shape.vtu").is_file()):
            return self._result_preview_folder.parent
        candidates = sorted((ROOT / "HumanResults").glob("Run_*"), reverse=True)
        if self._persist_solver_profiles:
            saved = self.solver_profile_store.settings.value("mechanical/latestResultFolder", "", type=str)
            candidates = [Path(path) for path in (saved, self._latest_kratos_results) if path] + candidates
        for folder in dict.fromkeys(candidates):
            if not (folder / "03_Results" / "deformed_shape.vtu").is_file():
                continue
            try:
                report = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(report, dict) and report.get("status") in (
                "completed", "completed_with_force_balance_warning",
            ):
                return folder
        return None

    def show_latest_result(self, *, open_folder=False) -> None:
        from ui_language import translate

        fft_selected = self.setup_tabs.currentIndex() == self._fft_method_index
        if self.setup_tabs.currentIndex() == self._peridynamic_method_index:
            results = self.peridynamic_results
            if results.folder is None:
                QMessageBox.information(self, translate("Result"), translate("No completed analysis results found."))
            elif open_folder:
                results.open_folder()
            else:
                self.render_preview()
            return
        folder = self.fft_result_folder if fft_selected else self._latest_mechanical_result()
        if folder is None or not folder.is_dir():
            QMessageBox.information(self, translate("Result"), translate("No completed analysis results found."))
            return
        if open_folder or fft_selected:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
            return
        if self._result_preview_folder != folder / "03_Results":
            self._show_completed_analysis(folder, announce=False)
        if self._latest_kratos_results == str(folder) and self.show_kratos_results(warn=False):
            return
        if not self._display_mechanical_result():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _display_mechanical_result(self):
        self.workspace_tabs.setCurrentIndex(0)
        field = self.result_preview_field
        with QSignalBlocker(field):
            field.setCurrentIndex(field.findData("Von_Mises_MPa"))
        self.render_preview()
        return field.currentData() is not None

    def show_kratos_results(self, *, warn=True, folder=None) -> bool:
        folder = Path(self._latest_kratos_results) if folder is None else folder
        if self._kratos_result_dialog is not None:
            if self._kratos_result_dialog.folder == folder:
                show_restored_window(self._kratos_result_dialog)
                return True
            self._kratos_result_dialog.close()
        try:
            from kratos_results_view import KratosResultsDialog
            dialog = KratosResultsDialog(folder, self)
        except Exception as error:
            if warn:
                QMessageBox.warning(self, "Kratos results", str(error))
            return False
        self._kratos_result_dialog = dialog
        dialog.finished.connect(lambda _: setattr(self, "_kratos_result_dialog", None))
        show_restored_window(dialog)
        return True

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
        self.truba_session.submitting = False
        self.analysis_poll.stop()
        self.local_analysis_progress.refresh()
        self.force_stop_button.setEnabled(False)
        self.refresh_validity()
        self.status.setText("Analysis cancelled. Analysis and solver processes forcibly terminated.")
        self._update_status_visuals("error", "● CANCELLED", False)

    def _show_analysis_progress(self, job):
        self._analysis_progress_counts = (None, None)
        record = job.remote_status()
        if record and record.get("background"):
            self._show_background_analysis(record)
        elif record:
            self.status.setText(f"TRUBA job {record.get('job_id', '—')} · {record.get('state', 'preparing')}")
        elif getattr(job, "background_submission", False) is True:
            self.status.setText("Preparing analysis for TRUBA…")
        else:
            stage = job.progress_status(with_counts=True) if hasattr(job, "progress_status") else None
            if isinstance(stage, tuple):
                stage, completed, total = stage
                self._analysis_progress_counts = (completed, total)
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
        if record.get("experiment_index") and record.get("background"):
            return
        idle = self.analysis_job is None and self.future is None
        if result is not None:
            payload, directory = result
            if payload.get("operation") == "mpm_solve" and self.mpm_workspace.job is None:
                self._load_remote_mpm(Path(directory) / "output")
            if payload.get("operation") in ("mechanical", "fft_solve", "peridynamic_solve"):
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
        self.truba_session.submitting = False
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
                self.truba_session.sync_background()
                return
            if isinstance(folder, dict) and folder.get("operation") == "peridynamic_solve":
                self._show_remote_analysis_result(folder)
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
                elif operation == "mpm_solve":
                    self._load_remote_mpm(directory, open_workspace=True)
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
                elif operation in ("fft_solve", "peridynamic_solve"):
                    self._show_remote_analysis_result(result, open_workspace=True)
                else:
                    self.status.setText(f"TRUBA results retrieved: {directory}")
                if operation!="mechanical": return
            self._show_completed_analysis(folder)

    def _load_remote_mpm(self, directory, *, open_workspace=False):
        try:
            self.mpm_workspace.load_directory(directory)
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.mpm_workspace.status.setText(str(error))
            self.status.setText(f"Cannot open MPM result: {error}")
        if open_workspace:
            self.workspace_tabs.setCurrentIndex(self.mpm_workspace_index)

    def _show_remote_analysis_result(self, result, record=None, *, announce=True, open_workspace=False):
        folder = result["run_folder"]
        if result.get("operation") == "peridynamic_solve":
            try:
                self.peridynamic_results.load(folder)
            except (OSError, ValueError, KeyError, TypeError) as error:
                if announce:
                    self.status.setText(f"Analysis failed: {error}")
                    self._update_status_visuals("error", "● FAILED", False)
                return
            if open_workspace:
                self._select_settings_method(self._peridynamic_method_index)
            if announce:
                if self.peridynamic_results.report["state"] == "complete":
                    self.status.setText(f"Analysis completed. Results: {folder}")
                    self._update_status_visuals("ready", "● COMPLETED", False)
                else:
                    from ui_language import translate

                    message = translate("TPMS compression stopped before convergence. Partial results: {folder}")
                    self.status.setText(message.format(folder=folder))
                    self._update_status_visuals("pending", "● PARTIAL RESULTS", False)
            return
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
            self._set_mechanical_result_preview(folder)
            if self._persist_solver_profiles:
                self.solver_profile_store.settings.setValue("mechanical/latestResultFolder", str(folder))
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

    def _set_mechanical_result_preview(self, folder, *, historical=False):
        from ui_language import translate

        self._result_preview_folder = Path(folder) / "03_Results"
        self._result_preview_is_historical = historical
        self._result_preview_data = None
        title = "Saved mechanical result · Final load step" if historical else "Latest mechanical result · Final load step"
        self.result_preview_field.setToolTip(f"{translate(title)}\n{folder}")
        self._sync_preview_readout()

    def _detach_remote_jobs(self):
        jobs = []
        job = self.analysis_job
        if getattr(job, 'is_remote', False) is True:
            jobs.append(job)
            self.analysis_job = None
            self.analysis_poll.stop()
        for workspace in (self.flow_workspace, self.thermal_workspace, self.mpm_workspace):
            if getattr(workspace.job, 'is_remote', False) is True:
                jobs.append(workspace.job)
                workspace.job = None
                workspace.timer.stop()
        if self.analysis_queue_widget is not None:
            jobs.extend(self.analysis_queue_widget.detach_remote_queue())
        for planner in (self.doe_page, self.lhs_page):
            job = planner.detach_remote()
            if job is not None:
                jobs.append(job)
        if self._mesh_convergence_dialog is not None:
            jobs.extend(self._mesh_convergence_dialog.study.detach_remote_jobs())
        return [job.detach_remote() for job in jobs]

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
            self.solver_profile_store.save_workspace(draft, applied=self.active_geometry)
            self.solver_profile_store.save(self._active_solver_id, profile_values(draft))
            self._save_fft_preferences()
            self.peridynamic_settings.save()
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
        self.kratos_panel.stop_install()
        if hasattr(self,"remote_controller"):
            self.remote_controller.shutdown()
        self.disposed = True
        if self._mesh_convergence_dialog is not None:
            dialog = self._mesh_convergence_dialog
            dialog.study.force_stop()
            dialog.reject()
        if self.md_workspace is not None:
            self.md_workspace.dispose()
        if self.sync_window is not None:
            self.sync_window.shutdown()
            self.sync_window.close()
        self.remote_controller.resource_window.close()
        self.truba_session.shutdown()
        self.debounce.stop()
        self.poll.stop()
        self.mesh_cancelled.set()
        self.mesh_progress.hide()
        self.cancel_mesh_button.hide()
        shutdown_mesh_preview()
        if self._kratos_result_dialog is not None:
            self._kratos_result_dialog.close()
        if hasattr(self, "doe_window"):
            for planner in (self.doe_page, self.lhs_page):
                planner.cancel()
            self.doe_window.close()
        if self.analysis_queue_widget is not None:
            self.analysis_queue_widget.dispose()
            self.analysis_queue_window.close()
        if self.design_pool is not None:
            self.design_pool.close()
            self.design_pool.dispose()
        if hasattr(self, "flow_workspace"):
            self.flow_workspace.dispose()
        if hasattr(self, "thermal_workspace"):
            self.thermal_workspace.dispose()
        self.mpm_workspace.dispose()
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
    from ui_language import install_language

    configure_application(app)
    profiles = SolverProfileStore()
    language = install_language(app, profiles.settings)
    workspace = _choose_startup_workspace(language)
    if workspace is None:
        return 0
    if workspace == "md":
        from esen_workspace import EsenWorkspace

        session = TrubaSession(app)
        try:
            dialog = EsenWorkspace(session, standalone=True)
            try:
                session.start()
                dialog.exec()
            finally:
                dialog.dispose()
        finally:
            session.shutdown()
            session.deleteLater()
        return 0

    settings = profiles.restore_workspace()
    dialog = RustMechDialog(settings, solver_profile_store=profiles)
    try:
        dialog.exec()
    finally:
        dialog.dispose()
        dialog.close()
        app.processEvents()
    return 0


def _choose_startup_workspace(language):
    dialog = QDialog()
    dialog.setObjectName("startupWorkspace")
    dialog.setWindowTitle("RustMech")
    dialog.setStyleSheet(STUDIO_STYLESHEET)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(12, 10, 12, 12)
    layout.addWidget(QLabel("Which workspace would you like to open?"))

    buttons = QHBoxLayout()
    layout.addLayout(buttons)
    choices = {1: "md", 2: "analysis"}
    controls = []
    for result, label in ((1, "Molecular dynamics"), (2, "Analysis")):
        button = QPushButton(label)
        button.setAutoDefault(False)
        button.setFixedHeight(CONTROL_HEIGHT)
        button.setProperty("managesLocalizedWidth", True)
        button.clicked.connect(partial(dialog.done, result))
        buttons.addWidget(button)
        controls.append(button)
    buttons.addStretch()

    language.localize_tree(dialog)
    width = max(button.sizeHint().width() for button in controls)
    for button in controls:
        button.setFixedWidth(width)
    return choices.get(dialog.exec())


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
