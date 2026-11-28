"""Read solver exports and publish the per-run mechanical workbook."""

import csv
import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory


WORKBOOK_NAME = "Sonuclar.xlsx"
COMPLETED_STATUSES = ("completed", "completed_with_force_balance_warning")


@dataclass(frozen=True)
class ResultSheet:
    name: str
    excel_name: str
    headers: tuple[str, ...]
    rows: tuple[tuple, ...]


@dataclass(frozen=True)
class ResultCurve:
    name: str
    excel_name: str
    sheet: ResultSheet
    x: int
    y: int


def _table(name, excel_name, values):
    return ResultSheet(name, excel_name, ("Parameter", "Value"), tuple(values))


def _csv_value(value):
    if not value.strip():
        return None
    try:
        number = float(value)
    except ValueError:
        return value
    return number if math.isfinite(number) else None


def _read_curve(folder, filenames, name, excel_name):
    for filename in filenames:
        for path in (folder / "04_Graphs" / filename, folder / filename):
            if not path.is_file():
                continue
            with path.open(newline="", encoding="utf-8-sig") as stream:
                reader = csv.reader(stream)
                headers = tuple(next(reader, ()))
                rows = tuple(tuple(_csv_value(value) for value in row)
                             for row in reader if len(row) == len(headers))
            if headers and rows:
                return ResultSheet(name, excel_name, headers, rows)
    return None


def _force_displacement_columns(headers):
    # Standards measure gauge extension or midspan deflection separately from actuator travel.
    x = next((headers.index(key) for key in (
        "Gauge_extension_mm", "Midspan_deflection_mm", "Compression_mm", "Actuator_travel_mm",
    ) if key in headers), None)
    y = next((headers.index(key) for key in ("Compression_Force_N", "Force_N") if key in headers), None)
    return x, y


def axis_label(header):
    name, _, unit = header.rpartition("_")
    if unit in {"mm", "N", "MPa", "J"}:
        return f"{name.replace('_', ' ').capitalize()} ({unit})"
    return header.replace("_", " ").capitalize()


def read_mechanical_report(folder, summary=None):
    """Return the tables and default X/Y series used by Excel and the results window."""
    folder = Path(folder)
    if summary is None:
        try:
            summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            summary = {}
    if not isinstance(summary, dict):
        raise ValueError("Invalid mechanical result summary.")
    metrics = [("Run", folder.name)]
    for key, label in (("status", "Status"), ("solver", "Solver")):
        if summary.get(key):
            metrics.append((label, summary[key]))
    for section in ("graphs", "fields", "reactions", "mesh", "test_profile"):
        values = summary.get(section)
        if isinstance(values, dict):
            metrics.extend((f"{section}.{key}", value) for key, value in values.items()
                           if isinstance(value, (str, int, float, bool)))
    sheets = [_table("Summary", "Özet", metrics)]
    settings = summary.get("settings")
    if isinstance(settings, dict) and settings:
        sheets.append(_table("Analysis settings", "Analiz Ayarları", settings.items()))

    force = _read_curve(folder, ("force_displacement.csv", "history.csv"),
                        "Force–displacement", "Kuvvet-Yer Değiştirme")
    stress = _read_curve(folder, ("stress_strain.csv",), "Stress–strain", "Gerilme-Gerinim")
    curves = []
    if force:
        sheets.append(force)
        x, y = _force_displacement_columns(force.headers)
        if x is not None and y is not None:
            curves.append(ResultCurve(force.name, force.excel_name, force, x, y))
    if stress:
        sheets.append(stress)
        if len(stress.headers) == 2:
            curves.append(ResultCurve(stress.name, stress.excel_name, stress, 0, 1))
    return sheets, curves


def _excel_value(value):
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_mechanical_workbook(folder, summary=None):
    """Write the report atomically so a failed export leaves the previous file intact."""
    from xlsxwriter import Workbook

    folder = Path(folder)
    sheets, curves = read_mechanical_report(folder, summary)
    target = folder / WORKBOOK_NAME
    with TemporaryDirectory(prefix=".rustmech-report-", dir=folder) as temporary:
        staged = Path(temporary) / WORKBOOK_NAME
        with Workbook(staged, {"strings_to_formulas": False, "strings_to_urls": False,
                               "tmpdir": temporary}) as workbook:
            workbook.set_properties({"title": f"RustMech · {folder.name}"})
            header = workbook.add_format({"bold": True, "font_color": "white", "bg_color": "#24486C"})
            for sheet in sheets:
                if len(sheet.rows) >= 1_048_576:
                    raise ValueError(f"{sheet.name} exceeds the Excel row limit.")
                worksheet = workbook.add_worksheet(sheet.excel_name)
                worksheet.hide_gridlines(2)
                worksheet.freeze_panes(1, 0)
                worksheet.write_row(0, 0, sheet.headers, header)
                for column, heading in enumerate(sheet.headers):
                    width = max(16, len(heading) + 4)
                    if sheet.headers == ("Parameter", "Value"):
                        width = 46 if column == 0 else 54
                    worksheet.set_column(column, column, width)
                for index, row in enumerate(sheet.rows, 1):
                    worksheet.write_row(index, 0, [_excel_value(value) for value in row])
                worksheet.autofilter(0, 0, len(sheet.rows), len(sheet.headers) - 1)
            if curves:
                graphs = workbook.add_worksheet("Grafikler")
                graphs.hide_gridlines(2)
                for index, curve in enumerate(curves):
                    source = curve.sheet.excel_name
                    end = len(curve.sheet.rows)
                    chart = workbook.add_chart({"type": "scatter", "subtype": "straight_with_markers"})
                    chart.add_series({"categories": [source, 1, curve.x, end, curve.x],
                                      "values": [source, 1, curve.y, end, curve.y],
                                      "line": {"color": "#247FA6"},
                                      "marker": {"type": "circle", "size": 3}})
                    chart.set_title({"name": curve.excel_name})
                    chart.set_x_axis({"name": axis_label(curve.sheet.headers[curve.x])})
                    chart.set_y_axis({"name": axis_label(curve.sheet.headers[curve.y])})
                    chart.set_legend({"none": True})
                    graphs.insert_chart(1 + 17 * index, 1, chart)
        staged.replace(target)
    return target


def publish_mechanical_workbook(folder, summary=None):
    """Keep solver results available if optional workbook publication fails."""
    try:
        from xlsxwriter.exceptions import XlsxWriterException

        try:
            return write_mechanical_workbook(folder, summary)
        except (OSError, ValueError, csv.Error, XlsxWriterException) as error:
            logging.getLogger(__name__).warning("Excel report for %s could not be written: %s", folder, error)
    except ImportError as error:
        logging.getLogger(__name__).warning("Excel report for %s could not be written: %s", folder, error)
    return None
