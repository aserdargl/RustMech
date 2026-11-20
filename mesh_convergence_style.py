"""Local PySide port of Datenburg's GraphCalc Edit and Results surfaces.

References: ui/graphcalc_edit_theme.py, ui/graphcalc_settings_styles.py,
graphcalc/graphcalc_results_panel_constants.py and ui/table_visual_tokens.py.
Kept local so RustMech does not depend on a neighbouring checkout.
"""

CONVERGENCE_STYLESHEET = """
QDialog#meshConvergenceDialog, QWidget#meshConvergenceStudy {
    background: #F5F5F3;
}
QWidget#meshConvergenceStudy QWidget#convergenceToolbar,
QWidget#meshConvergenceStudy QWidget#convergenceAdvanced,
QWidget#meshConvergenceStudy QWidget#convergenceScriptPlan {
    background: #FCFCFB; border: 1px solid #D5DBE2; border-radius: 7px;
}
QWidget#meshConvergenceStudy QScrollArea { background: transparent; }
QWidget#meshConvergenceStudy QLabel {
    color: #475569; font-size: 11px; background: transparent; border: none;
}
QWidget#meshConvergenceStudy QComboBox,
QWidget#meshConvergenceStudy QAbstractSpinBox,
QWidget#meshConvergenceStudy QLineEdit {
    background: #FFFFFF; color: #0F172A; font-size: 11px;
    border: 1px solid #CBD5E1; border-radius: 4px;
    padding: 1px; min-height: 19px; max-height: 19px;
    selection-background-color: #BFDBFE;
}
QWidget#meshConvergenceStudy QComboBox { padding: 1px; }
QWidget#meshConvergenceStudy QAbstractSpinBox { padding-right: 14px; }
QWidget#meshConvergenceStudy QComboBox:hover,
QWidget#meshConvergenceStudy QAbstractSpinBox:hover,
QWidget#meshConvergenceStudy QLineEdit:hover { border-color: #93C5FD; background: #FFFFFF; }
QWidget#meshConvergenceStudy QComboBox:focus,
QWidget#meshConvergenceStudy QAbstractSpinBox:focus,
QWidget#meshConvergenceStudy QLineEdit:focus { border-color: #3B82F6; background: #FFFFFF; }
QWidget#meshConvergenceStudy QAbstractSpinBox QLineEdit {
    border: none; background: transparent; padding: 0; margin: 0;
    min-height: 0; max-height: 16777215;
}
QWidget#meshConvergenceStudy QComboBox:disabled,
QWidget#meshConvergenceStudy QAbstractSpinBox:disabled,
QWidget#meshConvergenceStudy QLineEdit:disabled { color: #94A3B8; background: #F1F5F9; }
QWidget#convergenceScriptPlan QAbstractSpinBox { background: #E5E7EB; border-color: #9CA3AF; }
QWidget#convergenceScriptPlan QAbstractSpinBox:hover { background: #ECEFF1; border-color: #6B7280; }
QWidget#convergenceScriptPlan QAbstractSpinBox:focus { background: #F8FAFC; border-color: #2563EB; }
QWidget#convergenceScriptPlan QAbstractSpinBox:disabled { background: #F1F5F9; border-color: #CBD5E1; }
QWidget#meshConvergenceStudy QFrame#convergenceResultsPanel {
    background: #F4FBFF; border: 1px solid #B7D4E8; border-radius: 5px;
}
QWidget#meshConvergenceStudy QWidget#convergenceResultsHeader {
    background: #E5ECF4; border: 1px solid #D5DBE2; border-radius: 5px;
}
QPushButton#convergenceAppendButton { padding: 0 2px; }
QPushButton#convergenceAppendButton:focus { border: 1px solid #3978BE; }
QPushButton#convergenceScriptAdd {
    color: white; background: #285EA6; border: 1px solid #1E4D8D; border-radius: 4px;
}
QPushButton#convergenceScriptAdd:hover { background: #24558F; }
QPushButton#convergenceScriptAdd:pressed { background: #1B467D; }
QPushButton#convergenceScriptDelete {
    color: white; background: #B91C1C; border: 1px solid #991B1B; border-radius: 4px;
}
QPushButton#convergenceScriptDelete:hover { background: #A61B1B; }
QPushButton#convergenceScriptDelete:pressed { background: #7F1D1D; }
QPushButton#convergenceScriptAdd:focus, QPushButton#convergenceScriptDelete:focus {
    border: 2px solid #BFDBFE;
}
QPushButton#convergenceScriptAdd:disabled, QPushButton#convergenceScriptDelete:disabled {
    color: #64748B; background: #E9EDF2; border: 1px solid #CBD5E1;
}
QWidget#meshConvergenceStudy QLabel#convergenceResultsTitle {
    color: #0F172A; font-size: 13px; font-weight: 400;
}
QWidget#meshConvergenceStudy QLabel#convergenceStatus { color: #334155; font-size: 11px; }
QWidget#meshConvergenceStudy QLabel#convergenceStatus[tone="success"] { color: #166534; }
QWidget#meshConvergenceStudy QLabel#convergenceStatus[tone="warning"] { color: #92400E; }
QWidget#meshConvergenceStudy QLabel#convergenceStatus[tone="error"] { color: #991B1B; }
QWidget#meshConvergenceStudy QTableWidget {
    background: #FFFFFF; alternate-background-color: #FFFFFF;
    color: #0F172A; font-size: 12px;
    border: 1px solid #94A3B8; border-radius: 0;
    gridline-color: #E2E8F0;
    selection-background-color: #DCEEFF; selection-color: #0F172A;
}
QWidget#meshConvergenceStudy QTableWidget::item { padding: 1px; border: none; }
QWidget#meshConvergenceStudy QTableWidget::item:selected { background: #DCEEFF; color: #0F172A; }
QWidget#meshConvergenceStudy QHeaderView { background: #F1F0EC; }
QWidget#meshConvergenceStudy QHeaderView::section,
QWidget#meshConvergenceStudy QHeaderView::section:checked {
    background: #F1F0EC; color: #0F172A; font-size: 12px; font-weight: 400;
    padding: 1px; border: none;
    border-right: 1px solid #94A3B8; border-bottom: 1px solid #94A3B8;
}
QWidget#meshConvergenceStudy QTableCornerButton::section {
    background: #F1F0EC; border: none;
    border-right: 1px solid #94A3B8; border-bottom: 1px solid #94A3B8;
}
"""
