"""Exports of experiment matrices and their published results."""
import csv
import json
from math import isfinite
from pathlib import Path
import re
from uuid import uuid4


REPORT_NAME = "toplu_sonuclar.xlsx"
FINAL_STATES = {"Completed", "Failed", "Stopped", "Skipped"}
TABLES = {
    "fft_solve": ("stiffness_mpa.csv",),
    "mechanical": ("force_displacement.csv", "standardized_stress_strain.csv", "quality_report.csv"),
    "mpm_solve": ("mpm_force_displacement.csv",),
    "peridynamic_solve": ("force_displacement.csv", "layer_damage.csv"),
    "flow_solve": ("axial_profile.csv",),
    "thermal_solve": ("temperature_history.csv",),
}
SUMMARIES = {
    "fft_solve": ("status.json",),
    "mechanical": ("summary.json",),
    "mpm_solve": ("mpm_summary.json",),
    "peridynamic_solve": ("status.json",),
    "flow_solve": ("flow_summary.json",),
    "thermal_solve": ("flow_summary.json", "thermal_summary.json"),
}
TABULAR_FIELDS = {"settings", "options", "stiffness_mpa", "frames"}
NUMERIC = re.compile(r"[+-]?(?:0|[1-9]\d*)(?:\.\d*)?(?:[eE][+-]?\d+)?")


def experiment_name(number):
    return f"EXP-{number:04d}"


def _read_json(path):
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(report, dict):
        raise ValueError(f"Invalid analysis record: {path}")
    return report


def _cell(value):
    if NUMERIC.fullmatch(value):
        number = float(value)
        if isfinite(number):
            return number
    return value


def _metrics(report, prefix=""):
    for name, value in report.items():
        if not prefix and name in TABULAR_FIELDS:
            continue
        label = f"{prefix}.{name}" if prefix else name
        if isinstance(value, dict):
            yield from _metrics(value, label)
        elif isinstance(value, list):
            yield label, json.dumps(value, ensure_ascii=False)
        elif isinstance(value, float) and not isfinite(value):
            yield label, str(value)
        else:
            yield label, value


def _experiment(directory, number, grouped_output):
    name = f"row_{number:05d}"
    case = directory / name
    grouped = grouped_output / name
    if grouped.is_dir():
        return _read_json(grouped / "experiment.json"), grouped
    record = _read_json(case / "experiment.json")
    saved_path = Path(record.get("results") or case)
    if saved_path.is_dir() and saved_path.resolve().is_relative_to(directory.resolve()):
        return record, saved_path
    return record, case


def _result_files(folder, names):
    for name in names:
        for path in sorted(folder.rglob(name)):
            if path.is_file() and "Raw" not in path.relative_to(folder).parts:
                yield path


def _result_rows(folder, operation):
    for path in _result_files(folder, SUMMARIES.get(operation, ())):
        yield [path.name], "table"
        for label, value in _metrics(_read_json(path)):
            yield [label, value], None
    for path in _result_files(folder, TABLES.get(operation, ())):
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream)
            yield [path.name], "table"
            yield next(reader, []), "table"
            for values in reader:
                yield [_cell(value) for value in values], None


def _named_result_files(folder, names, prefix):
    counts = {}
    for path in _result_files(folder, names):
        number = counts.get(path.name, 0) + 1
        counts[path.name] = number
        suffix = f".{number}" if number > 1 else ""
        yield f"{prefix}.{path.stem}{suffix}", path


def write_learning_csv(directory, target, *, states, details, grouped_output=None):
    """Join frozen inputs and published results, with one CSV record per experiment."""
    directory = Path(directory).resolve()
    target = Path(target)
    matrix = directory / "matrix.csv"
    if target.resolve() == matrix:
        raise ValueError("Choose a different file from the saved experiment matrix.")

    with matrix.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        inputs = next(reader, [])
        experiments = list(reader)
    if (len(inputs) < 2 or len(experiments) != len(states)
            or any(len(row) != len(inputs) for row in experiments)):
        raise ValueError("Invalid saved experiment matrix.")

    output = Path(grouped_output) if grouped_output is not None else directory / "truba" / "output"
    metric_columns, table_columns = {}, {}
    results = []
    for index, row in enumerate(experiments):
        sample, operation = row[:2]
        number = int(sample.removeprefix("EXP-"))
        record, folder = _experiment(directory, number, output)
        saved_state = record.get("state")
        state = saved_state if saved_state in FINAL_STATES else states[index]
        error = (record.get("error") or details.get(index)) if state in {"Failed", "Stopped", "Skipped"} else ""
        metrics = {
            f"{name}.{key}": value
            for name, path in _named_result_files(folder, SUMMARIES.get(operation, ()), "result")
            for key, value in _metrics(_read_json(path))
        }
        tables = dict(_named_result_files(folder, TABLES.get(operation, ()), "table"))
        metric_columns.update(dict.fromkeys(metrics))
        table_columns.update(dict.fromkeys(tables))
        results.append((state, error, metrics, tables))

    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow([*inputs, "Status", "Error", *metric_columns, *table_columns])
            for row, (state, error, metrics, tables) in zip(experiments, results, strict=True):
                tabular = {}
                for name, path in tables.items():
                    with path.open(encoding="utf-8-sig", newline="") as table:
                        values = [[_cell(value) for value in line] for line in csv.reader(table)]
                    tabular[name] = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
                writer.writerow([*row, state, error, *(metrics.get(key) for key in metric_columns),
                                 *(tabular.get(key) for key in table_columns)])
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _report_rows(columns, experiments, records):
    levels = [list(dict.fromkeys(values)) for values in zip(*(row[2:] for row in experiments), strict=True)]
    variables = [index for index, values in enumerate(levels) if len(values) > 1]
    yield ["Numune parametreleri"], "section"
    yield ["Analiz", experiments[0][1]], None
    fixed = ((label, values) for label, values in zip(columns, levels, strict=True) if len(values) == 1)
    for label, values in sorted(fixed, key=lambda item: not item[0].startswith("settings.")):
        yield [label, _cell(values[0])], None
    yield ["Değişken parametreler", "Değerler"], "section"
    for index in variables:
        yield [columns[index], *map(_cell, levels[index])], None
    if not variables:
        yield ["Sabit parametrelerle çalışıldı"], None

    for (number, operation, *values), (record, folder, state, detail) in zip(experiments, records, strict=True):
        yield [experiment_name(number), *(f"{columns[i]} = {values[i]}" for i in variables)], "section"
        saved_state = record.get("state")
        yield ["Durum", saved_state if saved_state in FINAL_STATES else state or saved_state], None
        if record.get("slurm_job_id"):
            yield ["Slurm iş numarası", str(record["slurm_job_id"])], None
        if record.get("error") or detail:
            yield ["Açıklama", record.get("error") or detail], None
        yield from _result_rows(folder, operation)


def write_doe_report(directory, *, states=None, details=None, grouped_output=None, only_finished=False):
    """Publish the workbook atomically from the saved matrix and row results."""
    from xlsxwriter import Workbook
    from xlsxwriter.exceptions import XlsxWriterException

    directory = Path(directory).resolve()
    default_output = directory / "truba" / "output"
    with (directory / "matrix.csv").open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        columns = next(reader, [])[2:]
        experiments = [(int(name.removeprefix("EXP-")), *values) for name, *values in reader]
    if not experiments:
        return
    records = []
    for index, row in enumerate(experiments):
        record, folder = _experiment(directory, row[0], grouped_output or default_output)
        if only_finished and record.get("state") not in FINAL_STATES:
            return
        state = states[index] if states is not None else record.get("state", "Sonuç kaydı yok")
        detail = (details or {}).get(index) if state in {"Failed", "Stopped", "Skipped"} else None
        records.append((record, folder, state, detail))

    report = directory / REPORT_NAME
    temporary = directory / f".{REPORT_NAME}.{uuid4().hex}.tmp"
    try:
        with Workbook(temporary, {"constant_memory": True, "strings_to_formulas": False,
                                  "strings_to_urls": False}) as workbook:
            sheet = workbook.add_worksheet("Sonuçlar")
            sheet.set_column("A:A", 38)
            sheet.set_column("B:B", 46)
            sheet.set_column("C:XFD", 19)
            sheet.hide_gridlines(2)
            formats = {
                "section": workbook.add_format({"bold": True, "bg_color": "#18466B", "font_color": "#FFFFFF"}),
                "table": workbook.add_format({"bold": True, "bg_color": "#E8F1F8"}),
            }
            for index, (values, style) in enumerate(_report_rows(columns, experiments, records)):
                if sheet.write_row(index, 0, values, formats.get(style)) == -1:
                    raise ValueError("DoE results exceed the size of one Excel worksheet.")
        temporary.replace(report)
    except XlsxWriterException as error:
        raise OSError(str(error)) from error
    finally:
        temporary.unlink(missing_ok=True)
    return report
