"""Explain and estimate bilinear hardening from limited tensile datasheet data."""
from math import isfinite
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal as Signal
from PyQt6.QtWidgets import QDialog, QFormLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout

from studio_style import ActionFlowLayout, CompactDoubleSpinBox, configure_form


def estimate_tangent_modulus(young, yield_stress, tensile_strength, elongation_percent):
    """Engineering secant proxy only; fracture elongation is not strain at UTS.

    Inputs and output share a stress unit; elongation is a percentage (20 = 20%).
    This deliberately does not convert a fracture point to true stress/strain.
    """
    if not all(isfinite(v) for v in (young, yield_stress, tensile_strength, elongation_percent)):
        raise ValueError("All inputs must be finite numbers.")
    if young <= 0 or yield_stress <= 0 or tensile_strength < yield_stress:
        raise ValueError("Require E > 0, yield > 0 and tensile strength >= yield.")
    strain_span = elongation_percent / 100 - yield_stress / young
    if strain_span <= 0:
        raise ValueError("Fracture elongation must exceed the elastic yield strain (yield / E).")
    tangent = (tensile_strength - yield_stress) / strain_span
    if not isfinite(tangent) or not 0 <= tangent < young:
        raise ValueError("Estimate must satisfy 0 <= E_t < E. Check units and elongation.")
    return tangent


class TangentModulusHelpDialog(QDialog):
    estimate_selected = Signal(float, float, float)

    def __init__(self, young, yield_stress, unit="MPa", factor=1.0, parent=None):
        super().__init__(parent)
        self.setObjectName("tangentModulusHelp")
        self.setWindowTitle("Tangent modulus — Help")
        self.resize(640, 680)
        self.factor = factor
        self.unit = unit
        self.estimate = None
        layout = QVBoxLayout(self)
        self.guide = QTextBrowser()
        self.guide.setOpenExternalLinks(True)
        guide_path = Path(__file__).resolve().parent / "assets" / "help" / "tangent_modulus.html"
        self.guide.setHtml(guide_path.read_text(encoding="utf-8"))
        layout.addWidget(self.guide, 1)
        form = QFormLayout()
        configure_form(form)
        self.inputs = {}
        for key, label, value in (
            ("young", f"Young's modulus / {unit}", young / factor),
            ("yield", f"Yield strength / {unit}", yield_stress / factor),
            ("uts", f"Tensile strength / {unit}", 0),
            ("elongation", "Fracture elongation / %", 0),
        ):
            control = CompactDoubleSpinBox()
            control.setRange(0, 1e9 / factor if key != "elongation" else 10000)
            control.setKeyboardTracking(False)
            control.setValue(value)
            control.valueChanged.connect(self.recalculate)
            form.addRow(label, control)
            self.inputs[key] = control
        layout.addLayout(form)
        self.result = QLabel()
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.result)
        note = QLabel("Rough engineering proxy, not a calibrated true-strain modulus. "
                      "Use estimate replaces E, initial yield (approximated by datasheet yield) "
                      "and E_t, and selects Bilinear elastoplastic. No failure model is added.")
        note.setWordWrap(True)
        note.setObjectName("mutedHintLabel")
        layout.addWidget(note)
        buttons = ActionFlowLayout()
        self.use_button = QPushButton("Use estimate")
        self.use_button.clicked.connect(self.use_estimate)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        for button in (self.use_button, close):
            button.setAutoDefault(False)
            button.setDefault(False)
            button.setObjectName("secondaryActionBtn")
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.recalculate()

    def recalculate(self, *_):
        try:
            self.estimate = estimate_tangent_modulus(*(self.inputs[k].value()
                for k in ("young", "yield", "uts", "elongation")))
        except ValueError as exc:
            self.estimate = None
            self.result.setText(str(exc))
        else:
            self.result.setText(f"Approximate E_t = {self.estimate:.6g} {self.unit} "
                                f"({100 * self.estimate / self.inputs['young'].value():.4g}% of E)")
        self.use_button.setEnabled(self.estimate is not None)

    def use_estimate(self):
        self.recalculate()
        if self.estimate is not None:
            self.estimate_selected.emit(self.inputs["young"].value() * self.factor,
                                       self.inputs["yield"].value() * self.factor,
                                       self.estimate * self.factor)
            self.accept()

    def keyPressEvent(self, event):
        # Enter commits the focused input; applying/closing requires activation.
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
        else:
            super().keyPressEvent(event)
