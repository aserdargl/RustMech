"""Kratos-specific numerical controls using the shared compact studio style."""
import math
import os
import re
from analysis_process import AnalysisProcess
from local_kratos import VERSION, install_local_kratos, local_kratos_python
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QLabel, QLineEdit, QPushButton, QSizePolicy
from studio_style import (CompactComboBox, CompactDoubleSpinBox, CompactSpinBox, SidebarGroupBox,
                          configure_form, field_label, stepper_with_unit)
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
        self._form = form
        self._remote_rows = []
        self._install_job = None
        self._install_error = ""
        self._install_poll = QTimer(self, interval=200)
        self._install_poll.timeout.connect(self._poll_install)
        self._install_button = QPushButton("Install local Kratos")
        self._install_button.setObjectName("secondaryActionBtn")
        self._install_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._install_button.setStyleSheet("QPushButton { padding: 0 8px; }")
        self._install_button.setAutoDefault(False)
        self._install_button.clicked.connect(self._start_install)
        self._runtime_status = QLabel()
        self._runtime_status.setObjectName("mutedHintLabel")
        self._runtime_status.setTextFormat(Qt.TextFormat.PlainText)
        self._runtime_status.setWordWrap(True)
        form.addRow(self._install_button)
        form.addRow(self._runtime_status)
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
        self._remote_rows.append(transport)
        for key,title,placeholder in (("truba_winscp_site","WinSCP site","Automatic if only one TRUBA site"),
                                      ("truba_partition","Queue","debug"), ("truba_account","Account","TRUBA username"),
                                      ("truba_walltime","Time limit","HH:MM:SS")):
            control = QLineEdit()
            control.setPlaceholderText(placeholder)
            control.textChanged.connect(lambda *_: self.changed.emit())
            self.fields[key] = control
            form.addRow(field_label(title,control),control)
            self._remote_rows.append(control)
        memory=CompactSpinBox()
        memory.setRange(1,4194304)
        memory.setKeyboardTracking(False)
        memory.valueChanged.connect(lambda *_: self.changed.emit())
        self.fields["truba_memory_mb"]=memory
        memory_row = stepper_with_unit(memory, "MB")
        form.addRow(field_label("Memory", memory), memory_row)
        self._remote_rows.append(memory_row)
        self.recover_button=QPushButton("Retrieve TRUBA job…")
        self.recover_button.setObjectName("secondaryActionBtn")
        self.recover_button.setAutoDefault(False)
        self.recover_button.setDefault(False)
        self.recover_button.clicked.connect(lambda *_: self.recover_requested.emit())
        form.addRow(self.recover_button)
        self._remote_rows.append(self.recover_button)
        self.view_results_button=QPushButton("View latest results")
        self.view_results_button.setObjectName("secondaryActionBtn")
        self.view_results_button.setAutoDefault(False)
        self.view_results_button.setDefault(False)
        self.view_results_button.setEnabled(False)
        self.view_results_button.clicked.connect(lambda *_:self.view_results_requested.emit())
        form.addRow(self.view_results_button)
        hint = QLabel("HEX20/TET10 compression uses displacement control and penalty mortar.\n"
                      "Self-contact: HEX8, penalty mortar, fixed increments. Increase penalty to reduce penetration; refine increments if convergence fails.")
        hint.setObjectName("mutedHintLabel")
        hint.setWordWrap(True)
        form.addRow(hint)
        self.apply_settings(settings)
        self.set_execution_target(False)

    def set_execution_target(self, remote):
        self._remote = remote
        for widget in self._remote_rows:
            self._form.setRowVisible(widget, remote)
        self._refresh_install_status()

    def _refresh_install_status(self):
        ready = local_kratos_python() is not None
        installing = self._install_job is not None
        self._form.setRowVisible(self._runtime_status, not self._remote)
        self._form.setRowVisible(self._install_button, not self._remote and not ready and os.name == "nt")
        self._install_button.setEnabled(not installing)
        if ready:
            status = f"Local Kratos {VERSION} is ready."
        elif installing:
            status = "Installing and checking Kratos…"
        elif self._install_error:
            status = self._install_error
        elif os.name != "nt":
            status = "Local Kratos installation is available on Windows."
        else:
            status = "Local Kratos requires 64-bit Python 3.14 and an internet connection for setup."
        self._runtime_status.setText(status)

    def _start_install(self):
        if self._install_job is not None:
            return
        self._install_error = ""
        try:
            self._install_job = AnalysisProcess(install_local_kratos)
        except (OSError, RuntimeError) as error:
            self._install_error = str(error)
        else:
            self._install_poll.start()
        self._refresh_install_status()

    def _poll_install(self):
        if not self._install_job.done():
            return
        self._install_poll.stop()
        job, self._install_job = self._install_job, None
        try:
            job.result()
        except RuntimeError as error:
            self._install_error = str(error).strip().splitlines()[-1].removeprefix("RuntimeError: ")
        self._refresh_install_status()
        self.changed.emit()

    def stop_install(self):
        if self._install_job is not None:
            self._install_job.force_stop()
            self._install_job = None
        self._install_poll.stop()

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
