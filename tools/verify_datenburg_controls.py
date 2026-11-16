"""Render the original PyQt6 widgets and their PySide6 ports side by side.

Each panel is built in its own application/runtime. No source checkout is
required by the shipped RustMech app; this is a development comparison only.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def render(reference, output):
    if reference:
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton
        from shell.widget_numeric_controls import CompactButtonStepper, CompactCoordinateStepper
        from shell.widget_combo_controls import NoWheelComboBox
        from shell.compact_gradient_button import compact_gradient_button_stylesheet
        from ui.graphcalc_settings_styles import graphcalc_combo_stylesheet
        integer = lambda: CompactButtonStepper(0, 99, 8)
        decimal = lambda: CompactCoordinateStepper(0, 99, 6.5, decimals=1)
        combo = NoWheelComboBox
    else:
        sys.path.insert(0, str(ROOT))
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton
        from studio_style import CompactSpinBox, CompactDoubleSpinBox, CompactComboBox
        from datenburg_widgets import compact_gradient_button_stylesheet, graphcalc_combo_stylesheet
        def integer():
            control = CompactSpinBox()
            control.setRange(0, 99)
            control.setValue(8)
            return control
        def decimal():
            control = CompactDoubleSpinBox()
            control.setRange(0, 99)
            control.setValue(6.5)
            return control
        combo = CompactComboBox
    app = QApplication([])
    panel = QWidget()
    panel.setObjectName("fixture")
    panel.setStyleSheet('QWidget { font-family: "Segoe UI"; font-size: 13px; } QWidget#fixture { background: #F3F3F3; }')
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(12, 12, 12, 12)
    layout.setSpacing(8)
    widgets = [integer(), decimal()]
    for widget in widgets:
        widget.setFixedWidth(88)
        layout.addWidget(widget)
    dropdown = combo()
    dropdown.setObjectName("GraphCalcCombo")
    dropdown.setStyleSheet(graphcalc_combo_stylesheet())
    dropdown.addItems(["Gyroid", "Diamond", "Neovius"])
    dropdown.setFixedSize(150, 25)
    layout.addWidget(dropdown)
    button = QPushButton("Apply")
    button.setFixedSize(150, 25)
    button.setStyleSheet(compact_gradient_button_stylesheet("QPushButton"))
    button.setAutoDefault(False)
    layout.addWidget(button)
    panel.setFixedSize(180, 160)
    panel.show()
    panel.setFocus(Qt.FocusReason.OtherFocusReason)
    app.processEvents()
    output.mkdir(parents=True, exist_ok=True)
    panel.grab().save(str(output / "normal.png"))
    diagnostics = []
    for widget in widgets:
        field = widget.field
        diagnostics.append(dict(font=field.font().toString(), alignment=field.alignment().value,
                                frame=field.hasFrame(), geometry=[field.x(),field.y(),field.width(),field.height()],
                                text=field.text(), contents=[field.contentsMargins().left(),field.contentsMargins().right()],
                                palette=field.palette().color(field.foregroundRole()).name()))
    (output / "metrics.json").write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
    for index, widget in enumerate(widgets):
        panel.grab(widget.geometry()).save(str(output / f"stepper-{index}.png"))
    dropdown.grab().save(str(output / "dropdown.png"))
    button.grab().save(str(output / "button.png"))
    dropdown.showPopup()
    QTest.qWait(180)
    app.processEvents()
    view = dropdown.view()
    rect = view.visualRect(dropdown.model().index(0,0))
    (output / "popup-metrics.json").write_text(json.dumps(dict(
        font=view.font().toString(), frame_width=view.frameWidth(),
        geometry=[view.x(),view.y(),view.width(),view.height()],
        row=[rect.x(),rect.y(),rect.width(),rect.height()], delegate=type(view.itemDelegate()).__name__
        , rows=[[view.visualRect(dropdown.model().index(i,0)).y(), view.visualRect(dropdown.model().index(i,0)).height()] for i in range(dropdown.count())]
    ), indent=2), encoding="utf-8")
    dropdown.view().window().grab().save(str(output / "popup.png"))
    dropdown.hidePopup()
    for widget in (*widgets, dropdown, button):
        widget.setEnabled(False)
    app.processEvents()
    panel.grab().save(str(output / "disabled.png"))
    panel.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render", choices=("reference", "port"))
    parser.add_argument("--output", type=Path, default=ROOT / "design" / "datenburg-exact")
    parser.add_argument("--reference-root", type=Path, default=ROOT.parent / "Datenburg")
    args = parser.parse_args()
    if args.render:
        if args.render == "reference":
            sys.path.insert(0, str(args.reference_root))
        render(args.render == "reference", args.output)
        return
    args.output.mkdir(parents=True, exist_ok=True)
    for mode, executable in (("reference", args.reference_root / ".venv" / "Scripts" / "python.exe"),
                             ("port", Path(sys.executable))):
        subprocess.run([str(executable), str(Path(__file__).resolve()), "--render", mode,
                        "--output", str(args.output / mode), "--reference-root", str(args.reference_root)], check=True)
    from PIL import Image, ImageChops
    report = {}
    for name in ("normal", "stepper-0", "stepper-1", "dropdown", "button", "popup", "disabled"):
        original = Image.open(args.output / "reference" / f"{name}.png").convert("RGB")
        port = Image.open(args.output / "port" / f"{name}.png").convert("RGB")
        same_size = original.size == port.size
        bounds = ImageChops.difference(original, port).getbbox() if same_size else None
        report[name] = dict(reference_size=original.size, port_size=port.size,
                            identical=same_size and bounds is None, difference_bounds=bounds)
        comparison = Image.new("RGB", (original.width + port.width + 12, max(original.height, port.height)), "#F3F3F3")
        comparison.paste(original, (0, 0))
        comparison.paste(port, (original.width + 12, 0))
        comparison.save(args.output / f"comparison-{name}.png")
    (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not all(item["identical"] for item in report.values()):
        raise SystemExit("Datenburg control comparison failed; inspect report.json and comparison images.")


if __name__ == "__main__":
    main()
