"""Molecular dynamics preparation and TRUBA submission window."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime
from math import cbrt, isclose, prod
from pathlib import Path
import re

from PySide6.QtCore import QSettings, QSignalBlocker, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from app_settings import show_application_settings
from esen_elements import AlloyElements
import esen_jobs as jobs
import esen_md as md
from esen_md_project import MdProject, load_md_project, resolve_md_path, save_md_project, store_md_path
from esen_meam_catalog import LAMMPS_MEAM, download_meam_source, load_nist_catalog, matching_sources
from file_dialog import QFileDialog
from studio_style import (
    ActionFlowLayout, CompactComboBox, CompactDoubleSpinBox, CompactSpinBox, CONTROL_HEIGHT,
    STUDIO_STYLESHEET, configure_form, field_label, stepper_with_unit,
)
from ui_language import translate


WORKSPACE_TITLE = "Molecular Dynamics"
DEFAULT_MD_PROJECT = MdProject()
ACTIVE_TRUBA_STATES = frozenset({"Submitting", "Submitted", "Queued", "Running", "Cancellation requested"})
PANEL_PADDING = 7

ESEN_STYLE = """
QDialog#esenWorkspace { background: #F5F5F3; }
QFrame[esenCard="true"] { background: #FCFCFB; border: 1px solid #D5DBE2; border-radius: 7px; }
QFrame[esenCard="paper"] { background: #FFFFFF; border: 1px solid #D5DBE2; border-radius: 7px; }
#esenSection { background: #E5ECF4; border: 1px solid #D5DBE2; border-radius: 5px;
               color: #0F172A; font-size: 13px; padding: 0 5px; }
#esenWorkspace QPlainTextEdit, #esenWorkspace QListWidget {
    background: #FFFFFF; border: 1px solid #DCE3EC; border-radius: 3px;
}
#esenWorkspace QPlainTextEdit { font-family: Consolas; font-size: 12px; }
#esenFieldLabel { color: #475569; font-size: 11px; }
#esenWorkspace QPushButton { padding: 0 7px; }
#mdPeriodicTable QToolButton { padding: 0; font-size: 11px; min-height: -1px; max-height: -1px; }
#mdPeriodicTable QToolButton::menu-indicator { image: none; width: 0; height: 0; }
#mdPeriodicTable QToolButton[inAlloy="true"] { background: #BFDBFE; border-color: #93C5FD; }
#mdPeriodicTable QToolButton:hover, #mdPeriodicTable QToolButton:focus { border-color: #2563EB; }
#mdPeriodicTable QToolButton:pressed { background: #93C5FD; }
#esenWorkspace QPushButton#esenPrepare {
    color: #FFFFFF; font-weight: 600; padding: 0 12px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3475C3, stop:1 #285EA6);
    border: 1px solid #214D89; border-top-color: #548CCA;
    border-bottom: 3px solid #173D70; border-radius: 4px;
}
#esenWorkspace QPushButton#esenPrepare:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #367CCA, stop:1 #2D66B0);
}
#esenWorkspace QPushButton#esenPrepare:focus { border-color: #0F172A; }
#esenWorkspace QPushButton#esenPrepare:pressed {
    background: #245692; border-top: 3px solid #173D70; border-bottom-width: 1px;
}
#esenWorkspace QPushButton#esenPrepare:disabled {
    color: #808080; background: #EBEBEB; border-color: #D6D6D6;
}
#esenRun { color: #FFFFFF; background: #285EA6; }
#esenRun:hover { background: #245692; }
#esenRun:focus { border-color: #0F172A; }
#esenRun:pressed { background: #1E477C; }
#esenRun:disabled { background: #EBEBEB; color: #808080; border-color: #D6D6D6; }
"""


def panel(title="", *, paper=False, scrollable=False, header_action=None):
    card = QFrame()
    card.setProperty("esenCard", "paper" if paper else "true")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(PANEL_PADDING, PANEL_PADDING, PANEL_PADDING, PANEL_PADDING)
    layout.setSpacing(5)
    if title:
        header = QLabel(title)
        header.setObjectName("esenSection")
        header.setFixedHeight(CONTROL_HEIGHT)
        if header_action is None:
            layout.addWidget(header)
        else:
            header_row = QHBoxLayout()
            header_row.setSpacing(4)
            header_row.addWidget(header, 1)
            header_row.addWidget(header_action)
            layout.addLayout(header_row)
    if scrollable:
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(card)
        return scroll, layout
    return card, layout


def command(text, slot=None, *, role="", hint=""):
    button = QPushButton(text)
    button.setAutoDefault(False)
    button.setDefault(False)
    button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    button.setProperty("managesLocalizedWidth", True)
    button.setFixedHeight(CONTROL_HEIGHT)
    if role:
        button.setObjectName(role)
    if hint:
        button.setToolTip(hint)
    if slot is not None:
        button.clicked.connect(slot)
    return button


def button_row(*buttons):
    layout = ActionFlowLayout(equal_width=True)
    for button in buttons:
        layout.addWidget(button)
    return layout


def compact_combo(options):
    box = CompactComboBox()
    for label, value in options:
        box.addItem(label, value)
    return box


def code_view(name):
    edit = QPlainTextEdit()
    edit.setReadOnly(True)
    edit.setAccessibleName(name)
    edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    return edit


class EsenWorkspace(QDialog):
    # The class and object names are part of the saved window-state key.
    def __init__(self, truba, parent=None, *, history=None, standalone=False):
        super().__init__(parent)
        self.setObjectName("esenWorkspace")
        self.setWindowTitle(WORKSPACE_TITLE)
        self.setWindowFlag(Qt.WindowType.WindowMinMaxButtonsHint)
        self.setStyleSheet(STUDIO_STYLESHEET + ESEN_STYLE)
        self.resize(1440, 920)
        self.truba = truba
        self.standalone = standalone
        self.history_root = history if history is not None else jobs.jobs_directory()
        self.preferences = QSettings("RustMech", "RustMech")
        self.md_default_plans_root = self.history_root.parent / "md-preparations"
        saved_folder = self.preferences.value("esen/md/projectFolder", "", type=str)
        self.md_project_folder = None
        self.md_project_dirty = False
        saved_aliases = self.preferences.value("esen/md/meamAliases", {})
        self.md_aliases = saved_aliases if isinstance(saved_aliases, dict) else {}
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="md")
        self.md_task = None
        self.md_prepared = None
        self.md_download_task = None
        self.md_catalog_task = None
        self.md_nist_sources = None
        self.md_catalog_failed = False
        self.remote_task = None
        self._active_truba_jobs = {}
        self._disposed = False

        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("esenWorkspaceTabs")
        self.tabs.tabBar().setExpanding(False)
        if standalone:
            corner = QWidget()
            actions = QHBoxLayout(corner)
            actions.setContentsMargins(0, 0, 0, 0)
            actions.setSpacing(4)
            hpc = command("HPC", self._open_truba_connection,
                          hint="Open the Connection tab in the TRUBA application")
            settings = command("Settings", lambda: show_application_settings(self, self.preferences))
            actions.addWidget(hpc)
            actions.addWidget(settings)
            self.tabs.setCornerWidget(corner)
        shell.addWidget(self.tabs)
        self.tabs.addTab(self._build_md(), WORKSPACE_TITLE)
        self.tabs.addTab(self._build_md_truba(), "TRUBA Submission")

        self.md_save_timer = QTimer(self)
        self.md_save_timer.setSingleShot(True)
        self.md_save_timer.setInterval(250)
        self.md_save_timer.timeout.connect(self._save_md_project)
        if saved_folder:
            self._open_md_project(Path(saved_folder))
        self.md_elements.changed.connect(self._schedule_md_project_save)
        self.md_crystal.currentIndexChanged.connect(self._schedule_md_project_save)
        self.md_order.textChanged.connect(self._schedule_md_project_save)
        for field in self._md_project_number_fields():
            field.valueChanged.connect(self._schedule_md_project_save)

        language = getattr(QApplication.instance(), "_rustmech_language", None)
        if language is not None:
            for button in (self.md_preview_button, self.md_prepare, self.md_run,
                           self.md_retry_sources, self.md_pick_project,
                           *(button for _, _, button in self.md_source_groups.values()),
                           *self.md_file_buttons, *self.md_plan_buttons):
                language.localize(button)
            language.changed.connect(self._localize_md)
        self._localize_md()
        self._load_active_jobs()

        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(5000)
        self.poll_timer.timeout.connect(self._poll_remote)
        self.poll_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_md_plans()
        if self.md_nist_sources is None and not self.md_catalog_failed:
            self._load_md_sources()

    def _open_truba_connection(self):
        from truba_desktop import open_truba_desktop

        try:
            open_truba_desktop()
        except OSError as error:
            QMessageBox.warning(self, "HPC", translate(str(error)))

    def _build_md(self):
        page = QScrollArea()
        page.setFrameShape(QFrame.Shape.NoFrame)
        page.setWidgetResizable(True)
        columns = QWidget()
        page.setWidget(columns)
        outer = QHBoxLayout(columns)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(7)
        sections = QSplitter(Qt.Orientation.Vertical)
        sections.setObjectName("esenMdSettingsAndResults")
        sections.setChildrenCollapsible(False)
        sections.setHandleWidth(7)

        alloy, alloy_layout = panel("Alloy elements", scrollable=True)
        alloy.setObjectName("esenMdAlloyElements")
        self.md_elements = AlloyElements(DEFAULT_MD_PROJECT.elements)
        alloy_layout.addWidget(self.md_elements, 1)

        alloy.setFixedWidth(alloy.widget().minimumSizeHint().width() + alloy.verticalScrollBar().sizeHint().width())
        outer.addWidget(alloy)
        outer.addWidget(sections, 1)

        self.md_preview_button = command("Preview model", self._md_preview)
        inputs, settings_layout = panel("Package settings")
        self.md_form = QWidget()
        form = QFormLayout(self.md_form)
        form.setContentsMargins(0, 0, 0, 0)
        configure_form(form)
        settings_layout.addWidget(self.md_form)
        self.md_settings_form = form
        sections.addWidget(inputs)

        self.md_project_path = QLineEdit(str(self.md_project_folder or ""))
        self.md_project_path.setReadOnly(True)
        self.md_pick_project = command("Choose folder…", self._choose_md_project)
        project_row = QHBoxLayout()
        project_row.setSpacing(4)
        project_row.addWidget(self.md_project_path, 1)
        project_row.addWidget(self.md_pick_project)
        self._form_row(form, "Project folder", self.md_project_path, field_layout=project_row)

        self.md_file_buttons = []
        for key, title in (("library", "MEAM library"), ("parameters", "MEAM parameters")):
            field = QLineEdit()
            field.setReadOnly(True)
            field.setText(str(self.preferences.value(f"esen/md/{key}", "")))
            field.setToolTip(field.text())
            setattr(self, f"md_{key}", field)
            pick = command("Browse…", lambda _checked=False, k=key, label=title: self._pick_md_meam(k, label))
            sources = command("Sources", hint="Find the MEAM library and parameters supplied together for your alloy.")
            menu = QMenu(sources)
            for label, url in (
                ("LAMMPS potential files", "https://github.com/lammps/lammps/tree/develop/potentials"),
                ("NIST potentials by element system", "https://www.ctcms.nist.gov/potentials/system/"),
                ("LAMMPS MEAM file guide", "https://docs.lammps.org/stable/pair_meam.html"),
            ):
                menu.addAction(label, lambda _checked=False, link=url: QDesktopServices.openUrl(QUrl(link)))
            sources.setMenu(menu)
            sources.setAccessibleName("MEAM library sources" if key == "library" else "MEAM parameter sources")
            self.md_file_buttons.extend((pick, sources))
            row = QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(field, 1)
            row.addWidget(pick)
            row.addWidget(sources)
            self._form_row(form, title, field, field_layout=row)

        self.md_order = QLineEdit(str(self.preferences.value("esen/md/order", "")))
        self.md_order.setPlaceholderText("Example: Hf Nb Ta Ti Zr")
        self.md_order.setToolTip("Enter the element order used by indices in the MEAM parameter file.")
        self._form_row(form, "MEAM element order", self.md_order)
        self.md_crystal = compact_combo((("BCC single crystal", "single"), ("BCC polycrystal", "poly")))
        self._form_row(form, "Crystal type", self.md_crystal)

        self.md_dimensions = []
        for (axis, label), default in zip((("x", "Box length X"), ("y", "Box length Y"), ("z", "Box length Z")),
                                          DEFAULT_MD_PROJECT.dimensions):
            dimension = CompactDoubleSpinBox()
            dimension.setDecimals(5)
            dimension.setRange(0.00001, 10_000)
            dimension.setSingleStep(0.1)
            dimension.setValue(self.preferences.value(f"esen/md/box/{axis}", default, type=float))
            self._form_row(form, label, dimension, field_layout=stepper_with_unit(dimension, "nm"))
            dimension.editingFinished.connect(
                lambda field=dimension, key=axis: self._save_md_draft_setting(f"box/{key}", field.value())
            )
            self.md_dimensions.append(dimension)
        self.md_grains = CompactSpinBox()
        self.md_grains.setRange(2, md.MAX_GRAINS)
        self.md_grains.setValue(DEFAULT_MD_PROJECT.grains)
        self.md_grains.setToolTip("Number of Voronoi grains in the periodic box.")
        self._form_row(form, "Grain count", self.md_grains)
        self.md_lattice = CompactDoubleSpinBox()
        self.md_lattice.setDecimals(4)
        self.md_lattice.setRange(0.0001, 100.0)
        self.md_lattice.setValue(DEFAULT_MD_PROJECT.lattice)
        self._form_row(form, "Lattice parameter", self.md_lattice,
                       field_layout=stepper_with_unit(self.md_lattice, "Å"))
        self.md_temperature = CompactSpinBox()
        self.md_temperature.setRange(1, 10000)
        self.md_temperature.setValue(DEFAULT_MD_PROJECT.temperature)
        self._form_row(form, "Temperature", self.md_temperature,
                       field_layout=stepper_with_unit(self.md_temperature, "K"))
        self.md_seed = CompactSpinBox()
        self.md_seed.setRange(1, 900_000_000)
        self.md_seed.setValue(DEFAULT_MD_PROJECT.seed)
        self._form_row(form, "Random seed", self.md_seed)
        self.md_elements.changed.connect(self._md_preview)
        for stepper in self._md_project_number_fields():
            stepper.setFixedWidth(128)  # Leave room for the full nine-digit seed.
            stepper.editingFinished.connect(self._md_preview)
        self.md_order.editingFinished.connect(self._save_md_order)

        self.md_note = QLabel()
        self.md_note.setTextFormat(Qt.TextFormat.PlainText)
        self.md_note.setWordWrap(True)
        self.md_note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        settings_layout.addWidget(self.md_note)
        settings_layout.addStretch()
        self.md_prepare = command("Prepare Package", self._start_md, role="esenPrepare")
        settings_layout.addWidget(self.md_prepare, alignment=Qt.AlignmentFlag.AlignRight)

        card, layout = panel("Model preview", paper=True, header_action=self.md_preview_button)
        self.md_preview = code_view("Model preview")
        self.md_preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.md_preview.setMinimumHeight(125)
        layout.addWidget(self.md_preview, 1)
        sections.addWidget(card)

        sources = QSplitter(Qt.Orientation.Vertical)
        sources.setObjectName("esenMdPotentialSources")
        sources.setChildrenCollapsible(False)
        sources.setHandleWidth(7)
        self._build_md_sources(sources)
        sources.setSizes([165, 165])
        sections.addWidget(sources)
        sections.setSizes([inputs.sizeHint().height(), 190, 337])
        self.md_crystal.currentIndexChanged.connect(self._md_preview)
        return page

    def _build_md_truba(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(8, 8, 8, 8)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setObjectName("esenMdPlansAndTruba")
        split.setChildrenCollapsible(False)
        split.setHandleWidth(7)
        outer.addWidget(split)

        card, layout = panel("Job settings")
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        configure_form(form)
        self.md_queue = compact_combo([(name, name) for name in jobs.MODULES])
        queue_index = self.md_queue.findData(self.preferences.value("esen/md/queue", jobs.DEFAULT_PARTITION))
        self.md_queue.setCurrentIndex(max(0, queue_index))
        self._form_row(form, "TRUBA queue", self.md_queue)
        layout.addLayout(form)
        self.md_run = command("Submit to TRUBA", self._run_md, role="esenRun")
        layout.addLayout(button_row(self.md_run))
        self.md_remote_note = QLabel()
        self.md_remote_note.setTextFormat(Qt.TextFormat.PlainText)
        self.md_remote_note.setWordWrap(True)
        self.md_remote_note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.md_remote_note)
        layout.addStretch()

        split.addWidget(self._build_md_plans())
        split.addWidget(card)
        split.setSizes([630, 450])
        return page

    def _build_md_sources(self, row):
        self.md_source_groups = {}
        self.md_retry_sources = command("Retry NIST catalog", self._load_md_sources)
        for provider, title in (("nist", "NIST MEAM potentials"), ("lammps", "LAMMPS MEAM potentials")):
            entries = QListWidget()
            entries.setAccessibleName(title)
            entries.setMinimumHeight(90)
            entries.setWordWrap(True)
            entries.itemClicked.connect(self._select_md_source)
            entries.itemActivated.connect(self._select_md_source)
            open_source = command(
                "Open source", lambda _checked=False, source=entries: self._open_md_source(source.currentItem())
            )
            card, layout = panel(title, paper=True, header_action=open_source)
            status = QLabel()
            status.setWordWrap(True)
            status_row = QHBoxLayout()
            status_row.addWidget(status, 1)
            if provider == "nist":
                status_row.addWidget(self.md_retry_sources)
            layout.addLayout(status_row)
            layout.addWidget(entries, 1)
            entries.currentItemChanged.connect(
                lambda current, _previous, button=open_source: button.setEnabled(current is not None)
            )
            row.addWidget(card)
            self.md_source_groups[provider] = entries, status, open_source
        self.md_elements.selection_changed.connect(self._refresh_md_sources)

    def _load_md_sources(self):
        if self.md_catalog_task is not None or self._disposed:
            return
        self.md_catalog_failed = False
        self.md_catalog_task = self.executor.submit(load_nist_catalog)
        self._refresh_md_sources()

    def _check_md_sources(self):
        if self.md_catalog_task is None or not self.md_catalog_task.done():
            return
        task, self.md_catalog_task = self.md_catalog_task, None
        try:
            self.md_nist_sources = task.result()
        except Exception:
            self.md_catalog_failed = True
        self._refresh_md_sources()

    def _refresh_md_sources(self):
        selected = self.md_elements.rows.keys()
        self.md_retry_sources.setVisible(self.md_catalog_failed)
        for provider, catalog in (("nist", self.md_nist_sources or ()), ("lammps", LAMMPS_MEAM)):
            entries, status, open_source = self.md_source_groups[provider]
            matches = matching_sources(catalog, selected)
            previous = entries.currentItem()
            previous_source = previous.data(Qt.ItemDataRole.UserRole) if previous is not None else None
            entries.clear()
            for entry in matches:
                elements = "–".join(sorted(entry.elements))
                item = QListWidgetItem(f"{elements}\n{entry.name}")
                item.setToolTip(item.text())
                item.setData(Qt.ItemDataRole.UserRole, entry)
                entries.addItem(item)
                if entry == previous_source:
                    entries.setCurrentItem(item)
            if matches and entries.currentItem() is None:
                entries.setCurrentRow(0)
            open_source.setEnabled(bool(matches))

            if provider == "nist" and self.md_catalog_failed:
                message = "NIST catalog unavailable."
            elif provider == "nist" and self.md_nist_sources is None:
                message = "Loading NIST catalog…"
            elif not selected:
                message = "Add elements to filter MEAM potentials."
            elif not matches:
                message = "No MEAM potentials cover all selected elements."
            elif len(matches) == 1:
                message = "1 MEAM match"
            else:
                message = "{count} MEAM matches"
            status.setText(translate(message).format(count=len(matches)))

    @staticmethod
    def _open_md_source(item):
        if item is not None:
            QDesktopServices.openUrl(QUrl(item.data(Qt.ItemDataRole.UserRole).url))

    @property
    def md_plans_root(self):
        if self.md_project_folder is not None:
            return self.md_project_folder / "md-preparations"
        return self.md_default_plans_root

    def _choose_md_project(self):
        initial = str(self.md_project_folder or Path.home())
        folder = QFileDialog.getExistingDirectory(self, translate("Choose project folder"), initial)
        return self._open_md_project(Path(folder)) if folder else False

    def _open_md_project(self, path):
        if not self._save_md_project(commit_edits=True):
            return False
        try:
            path = path.resolve()
            if not path.is_dir():
                raise ValueError("Choose an existing project folder.")
            project = load_md_project(path)
            if project is None:
                project = self._capture_md_project(path) if self.md_project_folder is None else DEFAULT_MD_PROJECT
                save_md_project(path, project)
            if any(symbol not in self.md_elements.cells for symbol, _ in project.elements):
                raise ValueError("Invalid Molecular Dynamics project settings.")
            library = resolve_md_path(path, project.library)
            parameters = resolve_md_path(path, project.parameters)
        except (OSError, ValueError) as error:
            message = translate("Could not open the Molecular Dynamics project.")
            self.md_note.setText(f"{message}\n{translate(str(error))}")
            return False
        self.md_project_folder = path
        self.md_project_path.setText(str(path))
        self.preferences.setValue("esen/md/projectFolder", str(path))
        self.md_prepared = None
        self._apply_md_project(project, library, parameters)
        self._refresh_md_plans()
        missing = [Path(filename).name for filename in (library, parameters)
                   if filename and not Path(filename).is_file()]
        self.md_note.setText(translate("MEAM files missing: {files}").format(files=", ".join(missing)) if missing else "")
        return True

    def _md_project_number_fields(self):
        return (*self.md_dimensions, self.md_grains, self.md_lattice, self.md_temperature, self.md_seed)

    def _capture_md_project(self, folder):
        return MdProject(
            elements=self.md_elements.percentages(), crystal=self.md_crystal.currentData(),
            dimensions=tuple(field.value() for field in self.md_dimensions), grains=self.md_grains.value(),
            lattice=self.md_lattice.value(), temperature=self.md_temperature.value(), seed=self.md_seed.value(),
            library=store_md_path(folder, self.md_library.text()),
            parameters=store_md_path(folder, self.md_parameters.text()),
            order=self.md_order.text(), aliases=self.md_aliases,
        )

    def _apply_md_project(self, project, library, parameters):
        numeric_fields = self._md_project_number_fields()
        with ExitStack() as signals:
            for widget in (self.md_elements, self.md_crystal, self.md_order, *numeric_fields):
                signals.enter_context(QSignalBlocker(widget))
            if self.md_elements.percentages() != project.elements:
                for symbol in tuple(self.md_elements.rows):
                    self.md_elements.remove_element(symbol, focus=False)
                for symbol, percentage in project.elements:
                    self.md_elements.add_element(symbol, percentage)
            self.md_crystal.setCurrentIndex(self.md_crystal.findData(project.crystal))
            values = (*project.dimensions, project.grains, project.lattice, project.temperature, project.seed)
            for field, value in zip(numeric_fields, values):
                field.setValue(value)
            self._set_md_meam_file("library", library)
            self._set_md_meam_file("parameters", parameters)
            self._set_md_meam_aliases(project.aliases)
            self.md_order.setText(project.order)
        self._md_preview()
        self._refresh_md_sources()

    def _schedule_md_project_save(self, *_):
        if self.md_project_folder is not None:
            self.md_project_dirty = True
            self.md_save_timer.start()

    def _save_md_project(self, *, commit_edits=False):
        if commit_edits:
            for field in self._md_project_number_fields():
                field.interpretText()
            for _, percentage in self.md_elements.rows.values():
                percentage.interpretText()
        self.md_save_timer.stop()
        if not self.md_project_dirty:
            return True
        try:
            save_md_project(self.md_project_folder, self._capture_md_project(self.md_project_folder))
        except (OSError, ValueError) as error:
            message = translate("Could not save the Molecular Dynamics project.")
            self.md_note.setText(f"{message}\n{translate(str(error))}")
            return False
        self.md_project_dirty = False
        return True

    def _select_md_source(self, item):
        if self.md_download_task is not None or self.md_task is not None or self._disposed:
            return
        source = item.data(Qt.ItemDataRole.UserRole)
        if ((self.md_project_folder is None or not self.md_project_folder.is_dir())
                and not self._choose_md_project()):
            return
        if not source.elements.issuperset(self.md_elements.rows):
            self.md_note.setText(translate("Choose a MEAM potential for the restored elements."))
            return
        self.md_download_task = self.executor.submit(download_meam_source, source, self.md_project_folder)
        self._set_md_inputs_enabled(False)
        self.md_note.setText(translate("Downloading MEAM files for {name}…").format(name=source.name))

    def _check_md_download(self):
        if self.md_download_task is None or not self.md_download_task.done():
            return
        task, self.md_download_task = self.md_download_task, None
        self._set_md_inputs_enabled(True)
        try:
            downloaded = task.result()
        except Exception as error:
            message = translate("Could not download the MEAM files.")
            self.md_note.setText(f"{message}\n{translate(str(error))}")
            return
        self.md_prepared = None
        self._set_md_meam_file("library", downloaded.library)
        self._set_md_meam_file("parameters", downloaded.parameters)
        self._set_md_meam_aliases(downloaded.aliases)
        self.md_order.setText(" ".join(downloaded.order))
        self._save_md_order()
        self._schedule_md_project_save()
        self.md_note.setText(translate("MEAM files saved in {path}").format(path=downloaded.library.parent))

    def _build_md_plans(self):
        card, layout = panel("Saved MD plans", paper=True)
        card.setMinimumWidth(220)
        self.md_add_plan = command("Add package…", self._add_md_plan)
        self.md_open_plan = command("Open folder", self._open_md_plan)
        self.md_plan_buttons = (self.md_add_plan, self.md_open_plan)
        layout.addLayout(button_row(*self.md_plan_buttons))

        self.md_plan_hint = QLabel()
        self.md_plan_hint.setTextFormat(Qt.TextFormat.PlainText)
        self.md_plan_hint.setWordWrap(True)
        layout.addWidget(self.md_plan_hint)
        self.md_plans = QListWidget()
        self.md_plans.setAccessibleName("Saved MD plans")
        self.md_plans.setWordWrap(True)
        self.md_plans.setMinimumHeight(95)
        self.md_plans.currentItemChanged.connect(self._show_md_plan)
        layout.addWidget(self.md_plans, 1)

        self.md_plan_file = compact_combo([("Model details", ""), *((name, name) for name in md.PLAN_FILES)])
        self.md_plan_file.setAccessibleName("MD package file")
        self.md_plan_file.currentIndexChanged.connect(self._show_md_plan)
        layout.addWidget(self.md_plan_file, alignment=Qt.AlignmentFlag.AlignLeft)
        self.md_plan_source = code_view("MD package file")
        self.md_plan_source.setMinimumHeight(100)
        self.md_plan_source.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        layout.addWidget(self.md_plan_source, 2)
        self._show_md_plan()
        return card

    def _refresh_md_plans(self, selected=None):
        previous = self.md_plans.currentItem()
        if selected is None and previous is not None:
            selected = previous.data(Qt.ItemDataRole.UserRole)
        imported = self.preferences.value("esen/md/importedPlans", [], type=list)
        folders = {Path(path) for path in imported}
        for root in {self.md_plans_root, self.md_default_plans_root}:
            if root.is_dir():
                folders.update(root.glob("md-bcc-*"))
        plans = []
        for folder in folders:
            try:
                model = md.read_plan(folder)
                plans.append((folder, model, (folder / md.PLAN_METADATA).stat().st_mtime))
            except (OSError, ValueError):
                continue
        with QSignalBlocker(self.md_plans):
            self.md_plans.clear()
            for folder, model, modified in sorted(plans, key=lambda plan: (-plan[2], str(plan[0]))):
                title = "–".join(model["elements"])
                crystal = translate("BCC polycrystal" if model["grains"] > 1 else "BCC single crystal")
                date = datetime.fromtimestamp(modified).strftime("%Y-%m-%d %H:%M")
                item = QListWidgetItem(f'{title} · {model["temperature"]} K\n'
                                       f'{crystal} · {model["atoms"]:,} {translate("atoms")}\n{date}')
                item.setData(Qt.ItemDataRole.UserRole, folder)
                item.setData(Qt.ItemDataRole.UserRole + 1, model)
                item.setToolTip(str(folder))
                self.md_plans.addItem(item)
                if folder == selected:
                    self.md_plans.setCurrentItem(item)
            if self.md_plans.currentRow() == -1 and self.md_plans.count():
                self.md_plans.setCurrentRow(0)
        self._show_md_plan()

    def _add_md_plan(self):
        folder = QFileDialog.getExistingDirectory(self, translate("Add package…"))
        if not folder:
            return
        try:
            path = Path(folder).resolve()
            md.read_plan(path)
        except (OSError, ValueError) as error:
            self.md_plan_hint.setText(f'{translate("Choose a complete MD package folder.")}\n{error}')
            self.md_plan_hint.show()
            return
        imported = self.preferences.value("esen/md/importedPlans", [], type=list)
        if str(path) not in imported:
            self.preferences.setValue("esen/md/importedPlans", [*imported, str(path)])
        self._refresh_md_plans(path)

    def _show_md_plan(self, *_):
        item = self.md_plans.currentItem()
        self.md_open_plan.setEnabled(item is not None)
        self.md_plan_file.setEnabled(item is not None)
        self.md_run.setEnabled(item is not None)
        self.md_plan_hint.setVisible(item is None)
        if item is None:
            self.md_plan_hint.setText(translate("Prepare a package or add an existing MD package folder."))
            self.md_plan_source.clear()
            return
        filename = self.md_plan_file.currentData()
        if not filename:
            model = item.data(Qt.ItemDataRole.UserRole + 1)
            details = []
            for key, label in (("requested_box_angstrom", "Requested box dimensions"),
                               ("box_angstrom", "Box dimensions")):
                box = model.get(key)
                if (isinstance(box, list) and len(box) == 3
                        and all(type(edge) in (int, float) for edge in box)):
                    details.append(f"{translate(label)}  {self._box_in_nm(box)}")
            if model["grains"] > 1 and type(model.get("estimated_atoms")) is int:
                details.append(f'{translate("Estimated atom count")}  {model["estimated_atoms"]:,}')
            if "target_atoms" in model:
                details.append(f'{translate("Target atom count")}  {model["target_atoms"]}')
            details.extend([
                f'{translate("Prepared atom count")}  {model["atoms"]:,}',
                f'{translate("Grain count")}  {model["grains"]}',
                f'{translate("Temperature")}  {model["temperature"]} K',
                f'{translate("Lattice parameter")}  {model.get("lattice", "—")} Å',
                f'{translate("Random seed")}  {model.get("seed", "—")}',
                f'{translate("Protocol")}  {model.get("protocol", "—")}',
                "",
                translate("Prepared composition"),
                *(f'{element}  {count:,}  ({100 * count / model["atoms"]:.4f} %)'
                  for element, count in zip(model["elements"], model["counts"])),
                "",
                translate("MEAM element order"),
                " ".join(model["meam_order"]),
            ])
            self.md_plan_source.setPlainText("\n".join(details))
            return
        path = item.data(Qt.ItemDataRole.UserRole) / filename
        try:
            with path.open(encoding="utf-8", errors="replace") as source:
                contents = source.read(65_537)
            if len(contents) > 65_536:
                contents = contents[:65_536] + "\n\n" + translate("Open the folder to read the full file.")
        except OSError as error:
            contents = str(error)
        self.md_plan_source.setPlainText(contents)

    def _open_md_plan(self):
        if item := self.md_plans.currentItem():
            path = item.data(Qt.ItemDataRole.UserRole)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                self.md_plan_hint.setText(f'{translate("Could not open the package folder.")}\n{path}')
                self.md_plan_hint.show()

    def _localize_md(self, *_):
        self._size_command_buttons()
        with QSignalBlocker(self.md_elements):
            self.md_elements.update_total()
        self._md_preview()
        self._refresh_md_plans()
        self._refresh_md_sources()

    def _pick_md_meam(self, key, title):
        field = getattr(self, f"md_{key}")
        filename, _ = QFileDialog.getOpenFileName(self, translate(title), field.text(),
                                                  translate("MEAM files (*.meam);;All files (*)"))
        if filename:
            self.md_prepared = None
            self._set_md_meam_file(key, filename)
            self._set_md_meam_aliases({})
            self.md_order.clear()
            try:
                self.md_order.setText(md.documented_meam_order(Path(self.md_library.text()),
                                                              Path(self.md_parameters.text())))
                self.md_note.clear()
            except OSError as error:
                self.md_note.setText(str(error))
            self._save_md_order()
            self._schedule_md_project_save()

    def _set_md_meam_file(self, key, path):
        filename = str(path)
        field = getattr(self, f"md_{key}")
        field.setText(filename)
        field.setToolTip(filename)
        self._save_md_draft_setting(key, filename)

    def _set_md_meam_aliases(self, aliases):
        self.md_aliases = aliases
        self._save_md_draft_setting("meamAliases", aliases)

    def _save_md_order(self):
        self._save_md_draft_setting("order", self.md_order.text())

    def _save_md_draft_setting(self, key, value):
        if self.md_project_folder is None:
            self.preferences.setValue(f"esen/md/{key}", value)

    def _md_model(self):
        for stepper in (*self.md_dimensions, self.md_grains, self.md_lattice, self.md_temperature, self.md_seed):
            stepper.interpretText()
        composition = self.md_elements.composition()
        if not composition:
            raise ValueError(translate("Add at least one element."))
        dimensions = tuple(10 * field.value() for field in self.md_dimensions)
        return md.plan_model(composition, dimensions, self.md_lattice.value(),
                             self.md_temperature.value(), self.md_seed.value(),
                             grains=self.md_grains.value() if self.md_crystal.currentData() == "poly" else 1)

    @staticmethod
    def _box_in_nm(box):
        return " × ".join(f"{edge / 10:.5f}".rstrip("0").rstrip(".") for edge in box) + " nm"

    def _md_preview(self):
        self.md_settings_form.setRowVisible(self.md_grains, self.md_crystal.currentData() == "poly")
        try:
            model = self._md_model()
        except ValueError as error:
            self.md_preview.setPlainText(translate(str(error)))
            return
        lines = [f"{translate('Box dimensions')}   {self._box_in_nm(model.box)}"]
        if model.grains > 1:
            mean_size = cbrt(prod(model.box) / model.grains) / 10
            lines.extend([translate("BCC polycrystal"),
                          f"{translate('Estimated atom count')}   {model.atoms:,}",
                          f"{translate('Grain count')}   {model.grains}",
                          f"{translate('Characteristic grain size')}   {mean_size:.3f} nm",
                          translate("Atom count and composition counts are estimates until Atomsk builds the grain boundaries.")])
        else:
            cell = " × ".join(map(str, model.repeats))
            lines.extend([f"{translate('BCC supercell')}   {cell}",
                          f"{translate('Generated atom count')}   {model.atoms:,}"])
            if any(not isclose(requested, actual) for requested, actual in zip(model.requested_box_angstrom, model.box)):
                lines.append(translate("Box dimensions were rounded to whole BCC unit cells."))
        lines.extend([
            "",
            *(f"{element:<3}  {count:>10,}    {100 * count / model.atoms:8.4f} %"
              for element, count in zip(model.elements, model.counts)),
            "",
            translate("Atomsk → Minimization → Equilibration → Tension and CNA"),
        ])
        self.md_preview.setPlainText("\n".join(lines))

    def _start_md(self):
        if self.md_task is not None or self.md_download_task is not None:
            return
        self._md_preview()
        try:
            if self.md_project_folder is not None and not self.md_project_folder.is_dir():
                raise ValueError("Choose an existing project folder.")
            model = self._md_model()
            atom_symbols = (self.md_aliases.get(element, element) for element in model.elements)
            order = md.meam_order(self.md_order.text(), atom_symbols)
            library, parameters = Path(self.md_library.text()), Path(self.md_parameters.text())
            if not library.is_file() or not parameters.is_file():
                raise ValueError(translate("Choose the MEAM library and parameter files."))
            inputs = (model, library, parameters, order)
            request = (inputs, tuple(path.stat().st_mtime_ns for path in (library, parameters)), self.md_aliases)
            if self.md_prepared is not None and self.md_prepared[0] == request and self.md_prepared[1].is_dir():
                self._finish_md(self.md_prepared[1])
                return
            executable = md.atomsk_executable()
            future = self.executor.submit(md.prepare_job, self.md_plans_root, *inputs, executable,
                                          aliases=self.md_aliases)
        except (OSError, ValueError) as error:
            self.md_note.setText(translate(str(error)))
            return
        self.md_task = future, request
        self._set_md_inputs_enabled(False)
        self.md_note.setText(translate("Generating the structure with Atomsk…"))

    def _check_md(self):
        if self.md_task is None or not self.md_task[0].done():
            return
        future, request = self.md_task
        self.md_task = None
        self._set_md_inputs_enabled(True)
        try:
            folder = future.result()
            self.md_prepared = request, folder
            self._finish_md(folder)
        except Exception as error:
            folder = self.md_prepared[1] if self.md_prepared and self.md_prepared[0] == request else None
            self.md_note.setText(f"{error}\n{folder}" if folder is not None else str(error))

    def _set_md_inputs_enabled(self, enabled):
        for widget in (self.md_elements, self.md_form, self.md_prepare,
                       *(entries for entries, _, _ in self.md_source_groups.values())):
            widget.setEnabled(enabled)

    def _remote_settings(self, partition, auth):
        # MD submission previously inherited these values from saved LAMMPS profiles.
        profile_queue = self.preferences.value("esen/queue")
        if profile_queue is None:
            legacy_queue = self.preferences.value("esen/partition")
            profile_queue = jobs.DEFAULT_PARTITION if legacy_queue == "debug" else legacy_queue
        if profile_queue not in jobs.CPU_QUEUES:
            profile_queue = jobs.DEFAULT_PARTITION
        module = str(self.preferences.value("esen/module", jobs.DEFAULT_MODULE)).strip()
        if partition != profile_queue or module == jobs.DEFAULT_MODULE:
            module = jobs.MODULES[partition]
        launcher = self.preferences.value("esen/remote_mpi", "mpirun")
        return dict(
            partition=partition,
            account=str(self.preferences.value("esen/account", "")).strip() or auth.username,
            module=module,
            executable=str(self.preferences.value("esen/remote_exe", "lmp")).strip(),
            launcher=launcher if launcher in ("mpirun", "srun") else "mpirun",
            resume_input=md.RESTART,
        )

    def _finish_md(self, folder):
        self._refresh_md_plans(folder)
        self.md_note.setText(translate("Package prepared at {path}").format(path=folder))

    def _run_md(self):
        if (item := self.md_plans.currentItem()) is None:
            return
        try:
            auth = self.truba.analysis_auth()
            if auth is None:
                raise ValueError("Open HPC and connect to TRUBA." if self.standalone else
                                 "Connect using the main TRUBA button.")
            if self.remote_task is not None:
                raise ValueError(translate("TRUBA is busy. Try again when the current operation is complete."))
            folder = item.data(Qt.ItemDataRole.UserRole)
            md.read_plan(folder)
            partition = self.md_queue.currentData()
            settings = self._remote_settings(partition, auth)
            ranks, memory, walltime = jobs.full_node_resources(partition, jobs.BASELINES)
            jobs.remote_script(md.ENTRY, ranks, memory=memory, walltime=walltime, **settings)
            self.preferences.setValue("esen/md/queue", partition)
            directory, record = jobs.new_job(folder, md.ENTRY, "remote", None, parent=self.history_root,
                                             batch_folder=folder.name)
            extra = tuple(name for name in md.REQUIRED_PACKAGE_FILES if name != md.ENTRY)
            self._submit_remote(directory, record, (folder, md.ENTRY, extra, False), settings, auth)
        except (OSError, ValueError) as error:
            self.md_remote_note.setText(translate(str(error)))
            return
        self.md_remote_note.setText(translate("Submitting the MD job to TRUBA…"))

    def _size_command_buttons(self):
        for group in (self.md_file_buttons, self.md_plan_buttons):
            width = max(button.sizeHint().width() for button in group) + 2
            for button in group:
                button.setFixedWidth(width)

    @staticmethod
    def _form_row(form, name, field, *, field_layout=None):
        label = field_label(name, field)
        label.setObjectName("esenFieldLabel")
        if isinstance(field, CompactComboBox):
            field.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        if isinstance(field, QLineEdit) and not field.toolTip():
            label.setToolTip(name)
            field.textChanged.connect(field.setToolTip)
            field.setToolTip(field.text())
        form.addRow(label, field if field_layout is None else field_layout)

    def _load_active_jobs(self):
        for path in self.history_root.glob("*/job.json"):
            if not re.fullmatch(r"[0-9a-f]{32}", path.parent.name):
                continue
            try:
                record = jobs.read_job(path.parent)
            except (OSError, ValueError):
                continue
            self._update_tracked_job(path.parent, record)

    def _update_tracked_job(self, directory, record):
        username = record.get("username")
        if (record["target"] != "remote" or not username
                or record.get("remote_run") or record.get("hidden_from_history")):
            self._active_truba_jobs.pop(directory, None)
            return
        exported = jobs.result_directory(directory, record)
        needs_export = (record["state"] == "Completed" and exported is not None
                        and not exported.is_dir() and not record.get("result_export_error"))
        if record["state"] in ACTIVE_TRUBA_STATES or needs_export:
            self._active_truba_jobs[directory] = username
        else:
            self._active_truba_jobs.pop(directory, None)

    def _submit_remote(self, directory, record, files, settings, auth):
        future = self.executor.submit(jobs.submit_full_node, directory, record, files, settings, auth)
        self.remote_task = directory, future, True

    def _tick(self):
        self._check_md_sources()
        self._check_md_download()
        if self.remote_task is not None and self.remote_task[1].done():
            directory, future, submitting = self.remote_task
            self.remote_task = None
            try:
                future.result()
                record = jobs.read_job(directory)
                self._update_tracked_job(directory, record)
            except Exception as error:
                if submitting:
                    self.md_remote_note.setText(str(error))
            else:
                if submitting:
                    if record["state"] in ACTIVE_TRUBA_STATES:
                        self.md_remote_note.setText(translate(
                            "Follow the job in TRUBA. Downloaded results are saved in the package's Results folder."
                            if self.standalone else
                            "Follow the job in TRUBA and open downloaded results from Results."
                        ))
                    else:
                        self.md_remote_note.setText(record.get("detail") or translate("Failed"))
        self._check_md()

    def _poll_remote(self):
        auth = self.truba.analysis_auth()
        if self.remote_task is not None or not auth:
            return
        directory = next((path for path, username in self._active_truba_jobs.items()
                          if username == auth.username), None)
        if directory is None:
            return
        # Rotate tracked jobs so each gets a turn even if an earlier job is still running.
        self._active_truba_jobs[directory] = self._active_truba_jobs.pop(directory)
        try:
            record = jobs.read_job(directory)
        except (OSError, ValueError):
            self._active_truba_jobs.pop(directory, None)
            return

        def refresh():
            if record["state"] not in jobs.TERMINAL:
                jobs.refresh_remote(directory, record, auth)
                if record["state"] in jobs.TERMINAL:
                    jobs.remote_files(directory, record, auth)
            if (record["state"] == "Completed" and jobs.result_directory(directory, record) is not None
                    and not record.get("result_export_error")):
                try:
                    jobs.export_remote_results(directory, record, auth)
                except Exception as error:
                    jobs.save_job(directory, record, result_export_error=str(error))

        self.remote_task = directory, self.executor.submit(refresh), False

    def dispose(self):
        if not self._disposed:
            self._disposed = True
            if self.md_project_folder is not None:
                self._save_md_project(commit_edits=True)
            self.timer.stop()
            self.poll_timer.stop()
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.close()

    def closeEvent(self, event):
        if self.md_project_folder is not None:
            self._save_md_project(commit_edits=True)
        super().closeEvent(event)
