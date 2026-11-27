"""Shared controls and result readouts for the TPMS compression method."""

import csv
from dataclasses import asdict
import json
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QTextBrowser, QVBoxLayout, QWidget,
)

from model import Settings
from peridynamic_analysis import CompressionOptions, POISSON_RATIO
from structure_library import TPMS_FUNCTIONS, TPMS_MORPHOLOGIES
from studio_style import (CONTROL_HEIGHT, CompactComboBox, CompactDoubleSpinBox, CompactSpinBox,
                          SidebarGroupBox, configure_form, field_label)
from ui_language import translate, turkish

GEOMETRY_KEYS = ("structure_name", "tpms_morphology", "size", "cell_size", "solid_fraction")


class PeridynamicSettingsPanel(QWidget):
    changed = Signal()

    def __init__(self, model, preferences):
        super().__init__()
        self.preferences = preferences
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        default = Settings()
        defaults = dict(
            structure_name=model.structure_name if model.structure_name in TPMS_FUNCTIONS else default.structure_name,
            tpms_morphology=model.tpms_morphology, size=model.size, cell_size=model.cell_size,
            solid_fraction=model.solid_fraction, young_modulus=model.young_modulus,
            **asdict(CompressionOptions()),
        )
        saved = {}
        if preferences is not None:
            try:
                saved = json.loads(preferences.value("peridynamics/settings", "{}", type=str))
            except (TypeError, ValueError):
                pass
        if isinstance(saved, dict):
            defaults.update(saved)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.plan_button = QPushButton("Fracture methods roadmap")
        self.plan_button.setAutoDefault(False)
        self.plan_button.setCheckable(True)
        layout.addWidget(self.plan_button, alignment=Qt.AlignmentFlag.AlignLeft)
        plan = QTextBrowser()
        plan.setMinimumHeight(240)
        plan.setOpenExternalLinks(True)
        plan_path = Path(__file__).resolve().parent / "assets" / "help" / "tpms_fracture_roadmap.md"
        plan.setMarkdown(plan_path.read_text(encoding="utf-8"))
        plan.hide()
        self.plan_button.toggled.connect(plan.setVisible)
        layout.addWidget(plan)

        geometry_group = SidebarGroupBox("Structure")
        geometry_form = QFormLayout(geometry_group.content)
        configure_form(geometry_form)
        layout.addWidget(geometry_group)
        self.controls = {}
        for key, label, choices in (
            ("structure_name", "TPMS type", {name: name for name in TPMS_FUNCTIONS}),
            ("tpms_morphology", "Morphology", TPMS_MORPHOLOGIES),
        ):
            control = CompactComboBox()
            control.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
            for value, name in choices.items():
                control.addItem(name, value)
            control.setCurrentIndex(max(0, control.findData(defaults[key])))
            geometry_form.addRow(field_label(label, control), control)
            self.controls[key] = control
        form = QFormLayout()
        configure_form(form)
        layout.addLayout(form)
        for key, label, minimum, maximum, decimals, step in (
            ("size", "Specimen size / mm", 0.01, 10000, 3, 0.25),
            ("cell_size", "TPMS period / mm", 0.01, 10000, 3, 0.25),
            ("solid_fraction", "Solid fraction / %", 1, 99, 2, 1),
            ("young_modulus", "Elastic modulus / MPa", 0.001, 10_000_000, 3, 100),
            ("resolution", "Particles per axis", 12, 72, None, 4),
            ("horizon_ratio", "Horizon / spacing", 2.5, 4, 2, 0.25),
            ("critical_stretch_percent", "Critical bond stretch / %", 0.01, 20, 3, 0.1),
            ("compression_percent", "Compression / %", 0.1, 35, 2, 1),
            ("increments", "Load increments", 2, 80, None, 2),
        ):
            control = CompactSpinBox() if decimals is None else CompactDoubleSpinBox()
            if decimals is not None:
                control.setDecimals(decimals)
            control.setRange(minimum, maximum)
            control.setSingleStep(step)
            control.setKeyboardTracking(False)
            value = defaults.get(key)
            if isinstance(value, (int, float)) and minimum <= value <= maximum:
                control.setValue(int(value) if decimals is None else value)
            if key == "young_modulus":
                control.setFixedWidth(control.fontMetrics().horizontalAdvance("10000000.000") + 26)
            target_form = geometry_form if key in GEOMETRY_KEYS else form
            target_form.addRow(field_label(label, control), control)
            self.controls[key] = control

        self._applied_geometry = self.geometry_values()
        if preferences is not None:
            try:
                applied = json.loads(preferences.value("peridynamics/appliedGeometry", "null", type=str))
                if isinstance(applied, dict) and applied.keys() == self._applied_geometry.keys():
                    self._applied_geometry = applied
            except (TypeError, ValueError):
                pass
        self.structure_apply_button = QPushButton("Apply")
        button = self.structure_apply_button
        button.setObjectName("structureApplyBtn")
        button.setAutoDefault(False)
        button.setFixedSize(max(button.fontMetrics().horizontalAdvance(text)
                                for text in ("Apply", turkish("Apply"))) + 24, CONTROL_HEIGHT)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(button)
        geometry_form.addRow(actions)
        button.clicked.connect(self.apply_geometry)
        for control in self.controls.values():
            signal = control.currentIndexChanged if isinstance(control, QComboBox) else control.valueChanged
            signal.connect(self.changed)

        self.spacing = QLabel()
        self.spacing.setWordWrap(True)
        layout.addWidget(self.spacing, alignment=Qt.AlignmentFlag.AlignTop)
        limitation = QLabel("Brittle model · Poisson ratio 0.25 · frictionless contact. "
                            "Calibrate the critical stretch for the material before comparing loads with experiments.")
        limitation.setObjectName("mutedHintLabel")
        limitation.setWordWrap(True)
        layout.addWidget(limitation, alignment=Qt.AlignmentFlag.AlignTop)
        self.changed.connect(self.update_spacing)
        self.update_spacing()

    def geometry_values(self):
        values = self.values()
        return {key: values[key] for key in GEOMETRY_KEYS}

    def has_pending_geometry(self):
        return self.geometry_values() != self._applied_geometry

    def apply_geometry(self):
        for key in GEOMETRY_KEYS:
            control = self.controls[key]
            if not isinstance(control, QComboBox):
                control.interpretText()
        self._applied_geometry = self.geometry_values()
        self.save()
        self.changed.emit()

    def values(self):
        return {key: control.currentData() if isinstance(control, QComboBox) else control.value()
                for key, control in self.controls.items()}

    def inputs(self):
        values = self.values() | self._applied_geometry
        options = CompressionOptions(**{key: values.pop(key) for key in asdict(CompressionOptions())})
        return Settings(**values, poisson_ratio=POISSON_RATIO, material_model="linear_elastic"), options

    def update_spacing(self):
        self.structure_apply_button.setEnabled(self.has_pending_geometry())
        settings, options = self.inputs()
        spacing = settings.size / options.resolution
        self.spacing.setText(translate("Spacing {spacing} mm · Horizon {horizon} mm").format(
            spacing=f"{spacing:.4g}", horizon=f"{options.horizon_ratio * spacing:.4g}"))

    def save(self):
        if self.preferences is not None:
            self.preferences.setValue("peridynamics/settings", json.dumps(self.values()))
            self.preferences.setValue("peridynamics/appliedGeometry", json.dumps(self._applied_geometry))
            self.preferences.sync()


class PeridynamicResults(QWidget):
    step_changed = Signal()

    def __init__(self, preferences):
        super().__init__()
        self.preferences = preferences
        self.folder = None
        self.report = None
        self.chart = None
        self.layers = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        bar = QHBoxLayout()
        self.step = CompactSpinBox()
        self.step.setKeyboardTracking(False)
        self.step.setAccessibleName(translate("Load step"))
        label = QLabel("Load step")
        label.setBuddy(self.step)
        bar.addWidget(label)
        bar.addWidget(self.step)
        self.metric = CompactComboBox()
        self.metric.setAccessibleName("Result chart")
        self.metric.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.metric.addItem("Force–displacement", "force")
        self.metric.addItem("Damage by layer", "layers")
        self.metric.currentIndexChanged.connect(self._draw_chart)
        bar.addWidget(self.metric)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        bar.addWidget(self.summary, 1)
        self.folder_button = QPushButton("Open folder")
        self.folder_button.setMinimumWidth(76)
        self.folder_button.setAutoDefault(False)
        self.folder_button.clicked.connect(self.open_folder)
        bar.addWidget(self.folder_button)
        layout.addLayout(bar)
        self.run_label = QLabel()
        self.run_label.setWordWrap(True)
        layout.addWidget(self.run_label)
        self.step.valueChanged.connect(self._update_step)
        self.hide()
        if preferences is not None:
            saved = preferences.value("peridynamics/latestResultFolder", "", type=str)
            if saved:
                try:
                    self.load(Path(saved))
                except (OSError, ValueError, KeyError, TypeError):
                    pass

    def load(self, folder):
        folder = Path(folder)
        report = json.loads((folder / "status.json").read_text(encoding="utf-8"))
        if (report.get("method") != "quasi_static_pmb_v1" or not report.get("frames")
                or not (folder / "frames" / "step_000.vtp").is_file()):
            raise ValueError("Invalid peridynamic result.")
        layers = {}
        with (folder / "layer_damage.csv").open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                layers.setdefault(int(row["layer"]), []).append(
                    (float(row["displacement_mm"]), float(row["mean_damage"]) * 100))
        if not layers or any(len(layer) != len(report["frames"]) for layer in layers.values()):
            raise ValueError("Peridynamic layer history does not match the saved load steps.")
        if self.chart is None:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure

            self.chart = FigureCanvasQTAgg(Figure(figsize=(5.5, 1.6), layout="tight"))
            self.chart.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.chart.setFixedHeight(155)
            self.layout().addWidget(self.chart)
        self.folder, self.report, self.layers = folder, report, layers
        rows = report["frames"]
        with QSignalBlocker(self.step):
            self.step.setRange(0, len(rows) - 1)
            self.step.setValue(len(rows) - 1)
        settings = report["settings"]
        title = translate("{geometry} · {size} mm · {particles} particles · peak {peak} N").format(
            geometry=settings["structure_name"], size=f"{settings['size']:g}",
            particles=f"{report['particles']:,}", peak=f"{report['peak_force_n']:.4g}")
        if report["state"] == "incomplete":
            title += " · " + translate("Stopped before convergence")
        failure = report["first_bond_break"]
        if failure is not None:
            title += "\n" + translate("First bond failure {force} N at {travel} mm").format(
                force=f"{failure['force_n']:.4g}", travel=f"{failure['displacement_mm']:.4g}")
        self.run_label.setText(title)
        self._draw_chart()
        self._update_step()
        if self.preferences is not None:
            self.preferences.setValue("peridynamics/latestResultFolder", str(folder))

    def frame_path(self):
        return self.folder / "frames" / f"step_{self.step.value():03}.vtp"

    def open_folder(self):
        if self.folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder)))

    def _draw_chart(self):
        if self.report is None:
            return
        figure = self.chart.figure
        figure.clear()
        axes = figure.add_subplot()
        rows = self.report["frames"]
        if self.metric.currentData() == "layers":
            for layer, data in sorted(self.layers.items()):
                valid = data[:-1] if not rows[-1]["converged"] else data
                travel, damage = zip(*valid)
                line, = axes.plot(travel, damage, marker=".", linewidth=1.5,
                                  label=translate("Layer {value}").format(value=layer))
                if not rows[-1]["converged"]:
                    axes.plot(*data[-1], "x", color=line.get_color())
            axes.set_ylabel(translate("Mean bond damage / %"), fontsize=9)
            axes.legend(loc="best", fontsize=7, ncols=min(3, max(1, len(self.layers))))
        else:
            stable = [row for row in rows if row["converged"]]
            axes.plot([row["displacement_mm"] for row in stable], [row["top_force_n"] for row in stable],
                      color="#2563eb", marker=".", linewidth=1.5)
            if not rows[-1]["converged"]:
                axes.plot(rows[-1]["displacement_mm"], rows[-1]["top_force_n"], "x", color="#b91c1c")
            axes.set_ylabel(translate("Top platen force / N"), fontsize=9)
        axes.set_xlabel(translate("Compression displacement / mm"), fontsize=9)
        axes.tick_params(labelsize=8)
        axes.grid(alpha=0.2)
        self.marker = axes.axvline(rows[self.step.value()]["displacement_mm"], color="#64748b",
                                   alpha=0.75, linestyle="--", linewidth=1)
        self.chart.draw_idle()

    def _update_step(self):
        if self.report is None:
            return
        row = self.report["frames"][self.step.value()]
        status = translate("{travel} mm · {force} N · {broken} broken bonds · max damage {damage}").format(
            travel=f"{row['displacement_mm']:.4g}", force=f"{row['top_force_n']:.4g}",
            broken=f"{row['broken_bonds']:,}", damage=f"{row['max_damage']:.1%}")
        layer = row.get("highest_damage_layer")
        if layer is not None:
            status += " · " + translate("layer {layer} mean damage {damage}").format(
                layer=layer, damage=f"{row['highest_layer_mean_damage']:.1%}")
        if not row["converged"]:
            status += " · " + translate("Unconverged step")
        self.summary.setText(status)
        self.marker.set_xdata([row["displacement_mm"]] * 2)
        self.chart.draw_idle()
        self.step_changed.emit()
