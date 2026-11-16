"""Kratos-specific numerical controls using the shared compact studio style."""
import math
import re
from studio_style import CompactSpinBox, CompactComboBox
from PySide6.QtCore import Signal
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QLabel, QLineEdit, QPushButton
from studio_style import CompactDoubleSpinBox, SidebarGroupBox, configure_form, field_label
from solver_profiles import KRATOS_FIELDS


class ToleranceSpinBox(CompactDoubleSpinBox):
    """Keep small tolerances legible and editable in a compact field."""
    def textFromValue(self, value):
        return f"{value:.6g}"

    def valueFromText(self, text):
        return float(text.replace(self.locale().decimalPoint(), "."))

    def validate(self, text, position):
        normalized = text.replace(self.locale().decimalPoint(), ".").strip()
        try:
            value = float(normalized)
        except ValueError:
            partial = normalized in ("", ".", "+", "-") or re.fullmatch(r"[+]?\d*\.?\d+[eE][+-]?", normalized)
            state = QValidator.State.Intermediate if partial else QValidator.State.Invalid
        else:
            state = (QValidator.State.Acceptable if math.isfinite(value) and self.minimum() <= value <= self.maximum()
                     else QValidator.State.Intermediate if math.isfinite(value) and value >= 0
                     else QValidator.State.Invalid)
        return state, text, position


class KratosSettingsPanel(SidebarGroupBox):
    changed = Signal()
    recover_requested = Signal()
    view_results_requested = Signal()

    def __init__(self, settings):
        super().__init__("Kratos · nonlinear solution")
        self.setObjectName("kratosSettingsGroup")
        form = QFormLayout(self.content)
        form.setContentsMargins(1, 4, 1, 3)
        configure_form(form)
        self.fields = {}
        for key, title, choices in (
            ("kratos_linear_solver", "Linear solver", (("Sparse LU", "sparse_lu"), ("AMGCL", "amgcl"), ("Skyline LU", "skyline_lu_factorization"))),
            ("kratos_contact_method", "Contact method", (("ALM mortar", "alm"), ("Penalty mortar", "penalty"))),
        ):
            control = CompactComboBox()
            for label, value in choices:
                control.addItem(label, value)
            control.currentIndexChanged.connect(lambda *_: self.changed.emit())
            self.fields[key] = control
            form.addRow(field_label(title, control), control)
        penalty=CompactDoubleSpinBox()
        penalty.setRange(1.,1e8)
        penalty.setDecimals(0)
        penalty.setKeyboardTracking(False)
        penalty.setToolTip('Penalty stiffness multiplier (E / mesh spacing). Increase to reduce penetration; very large values can impede convergence.')
        penalty.valueChanged.connect(lambda *_:self.changed.emit())
        self.fields['kratos_penalty_factor']=penalty
        form.addRow(field_label('Penalty factor',penalty),penalty)
        self.fields['kratos_contact_method'].currentIndexChanged.connect(
            lambda *_:penalty.setEnabled(self.fields['kratos_contact_method'].currentData()=='penalty'))
        iterations = CompactSpinBox()
        iterations.setRange(1, 1000)
        iterations.setKeyboardTracking(False)
        iterations.valueChanged.connect(lambda *_: self.changed.emit())
        self.fields["kratos_max_iterations"] = iterations
        form.addRow(field_label("Newton iterations", iterations), iterations)
        for key, title, decimals in (
            ("kratos_relative_tolerance", "Relative tolerance", 8),
            ("kratos_absolute_tolerance", "Absolute tolerance", 12),
        ):
            control = ToleranceSpinBox()
            control.setDecimals(decimals)
            control.setRange(10 ** -decimals, 1.0)
            control.setSingleStep(1e-5 if decimals == 8 else 1e-9)
            control.setKeyboardTracking(False)
            control.valueChanged.connect(lambda *_: self.changed.emit())
            self.fields[key] = control
            form.addRow(field_label(title, control), control)
        line_search = QCheckBox("Line search")
        line_search.setEnabled(False)
        line_search.setToolTip("Not qualified with this contact strategy; use Newton-Raphson.")
        line_search.toggled.connect(lambda *_: self.changed.emit())
        self.fields["kratos_line_search"] = line_search
        form.addRow(field_label("Newton strategy", line_search), line_search)
        transport = CompactComboBox()
        for label,value in (("Automatic","auto"),("RustMech SSH","ssh"),("WinSCP saved site","winscp")):
            transport.addItem(label,value)
        transport.currentIndexChanged.connect(lambda *_: self.changed.emit())
        self.fields["truba_transport"] = transport
        form.addRow(field_label("TRUBA connection",transport),transport)
        for key,title,placeholder in (("truba_winscp_site","WinSCP site","Automatic if only one TRUBA site"),
                                      ("truba_partition","Queue","debug"), ("truba_account","Account","TRUBA username"),
                                      ("truba_walltime","Time limit","HH:MM:SS")):
            control = QLineEdit()
            control.setPlaceholderText(placeholder)
            control.textChanged.connect(lambda *_: self.changed.emit())
            self.fields[key] = control
            form.addRow(field_label(title,control),control)
        memory=CompactSpinBox()
        memory.setRange(1,4194304)
        memory.setSuffix(" MB")
        memory.setKeyboardTracking(False)
        memory.valueChanged.connect(lambda *_: self.changed.emit())
        self.fields["truba_memory_mb"]=memory
        form.addRow(field_label("Memory",memory),memory)
        self.recover_button=QPushButton("Retrieve TRUBA job…")
        self.recover_button.setObjectName("secondaryActionBtn")
        self.recover_button.setAutoDefault(False)
        self.recover_button.setDefault(False)
        self.recover_button.clicked.connect(lambda *_: self.recover_requested.emit())
        form.addRow(self.recover_button)
        self.view_results_button=QPushButton("View latest results")
        self.view_results_button.setObjectName("secondaryActionBtn")
        self.view_results_button.setAutoDefault(False)
        self.view_results_button.setDefault(False)
        self.view_results_button.setEnabled(False)
        self.view_results_button.clicked.connect(lambda *_:self.view_results_requested.emit())
        form.addRow(self.view_results_button)
        hint = QLabel("TRUBA · nonlinear static · HEX8 / TET4.\n"
                      "Self-contact: HEX8, penalty mortar, fixed increments. Increase penalty to reduce penetration; refine increments if convergence fails.")
        hint.setObjectName("mutedHintLabel")
        hint.setWordWrap(True)
        form.addRow(hint)
        self.apply_settings(settings)

    def values(self):
        return {key: (control.currentData() if isinstance(control, QComboBox)
                      else control.isChecked() if isinstance(control, QCheckBox)
                      else control.text().strip() if isinstance(control,QLineEdit)
                      else control.value()) for key, control in self.fields.items()}

    def apply_settings(self, settings):
        for key in KRATOS_FIELDS:
            control = self.fields[key]
            blocked = control.blockSignals(True)
            value = getattr(settings, key)
            if isinstance(control, QComboBox):
                control.setCurrentIndex(max(0, control.findData(value)))
            elif isinstance(control, QCheckBox):
                control.setChecked(value)
            elif isinstance(control,QLineEdit):
                control.setText(value)
            else:
                control.setValue(value)
            control.blockSignals(blocked)
        self.fields['kratos_penalty_factor'].setEnabled(self.fields['kratos_contact_method'].currentData()=='penalty')
