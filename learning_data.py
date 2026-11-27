"""Read DoE inputs and assemble a portable, geometry-grouped training dataset."""

import csv
import hashlib
import json
from math import isfinite
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile


METHODS = {
    "gp": "Gaussian Process",
    "catboost": "CatBoost",
    "xgboost": "XGBoost",
    "mlp": "MLP",
    "cnn": "3D CNN",
    "gnn": "Graph Neural Network",
}
SPATIAL = {"cnn", "gnn"}
LEGACY_GEOMETRY = {
    "Family": "structure_family", "Structure": "structure_name",
    "Morphology": "tpms_morphology", "Cube size (mm)": "size",
    "Cell / Pore (mm)": "cell_size", "Target Density (%)": "solid_fraction",
    "Seed": "random_seed",
}
LEGACY_INPUTS = {*LEGACY_GEOMETRY, "Compression (mm)", "Mesh Size (mm)"}
LEGACY_TARGETS = (
    "Max Force (N)", "SEA (J/g)", "EA (J)", "Vol. EA (J/cm³)", "Effective E* (MPa)",
    "Poisson Ratio (ν*)", "Max Von Mises (MPa)", "Actual Density (%)", "Mass (g)",
)
GEOMETRY_KEYS = (
    "structure_family", "structure_name", "tpms_morphology", "random_seed", "size", "size_x", "size_y",
    "cell_size", "solid_fraction", "test_profile", "specimen_architecture", "core_fraction",
    "imported_geometry_path", "imported_geometry_role",
)
DATA_PREFIXES = {"settings", "options", "flow", "thermal", "mpm"}
TRAINING_SHEET = "Training Data"


def _number(value):
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if isfinite(number) else None


def read_dataset(path):
    path = Path(path)
    if path.suffix.lower() == ".xlsx":
        return _read_excel_dataset(path)
    if path.suffix.lower() != ".csv":
        raise ValueError("Select a CSV or Excel (.xlsx) training dataset.")
    previous_limit = csv.field_size_limit(64 * 1024 * 1024)
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            header = reader.fieldnames or []
            if "Status" not in header or len(set(header)) != len(header):
                raise ValueError("Select a RustMech training or DoE results CSV with unique columns and a Status column.")
            columns = [key for key in header if not key.startswith("table.")]
            rows = [{key: row[key] for key in columns}
                    for row in reader if (row.get("Status") or "").strip().lower() == "completed"]
            return columns, rows
    except csv.Error as error:
        raise ValueError(f"Invalid training CSV: {error}") from error
    finally:
        csv.field_size_limit(previous_limit)


def _read_excel_dataset(path):
    try:
        from openpyxl import load_workbook
        from openpyxl.utils.exceptions import InvalidFileException
    except ImportError as error:
        raise ValueError("Excel import requires openpyxl. Install the RustMech base requirements.") from error

    try:
        with path.open("rb") as stream:
            workbook = load_workbook(stream, read_only=True, data_only=True, keep_links=False)
            try:
                sheets = sorted(workbook.worksheets, key=lambda sheet: sheet.title != TRAINING_SHEET)
                for sheet in sheets:
                    records = sheet.values
                    header = next((row for row in records if any(value is not None for value in row)), ())
                    fields = {i: value.strip() for i, value in enumerate(header)
                              if isinstance(value, str) and value.strip()}
                    if "Status" not in fields.values() or len(set(fields.values())) != len(fields):
                        continue
                    status = next(i for i, name in fields.items() if name == "Status")
                    columns = {i: name for i, name in fields.items() if not name.startswith("table.")}
                    rows = [{name: str(row[i]) if i < len(row) and row[i] is not None else None
                             for i, name in columns.items()}
                            for row in records if status < len(row)
                            and str(row[status]).strip().lower() == "completed"]
                    return list(columns.values()), rows
            finally:
                workbook.close()
    except (BadZipFile, InvalidFileException, ParseError, KeyError, ValueError) as error:
        raise ValueError(f"Invalid training workbook: {error}") from error
    raise ValueError("The Excel workbook needs a worksheet with unique training column headers and a Status column.")


def target_columns(columns, rows):
    candidates = [key for key in columns if key in LEGACY_TARGETS or key.startswith("result.")]
    return [key for key in candidates if any(_number(row.get(key)) is not None for row in rows)]


def _geometry(row):
    geometry = {}
    for column in ("Geometry settings (JSON)", "Design settings (JSON)"):
        if row.get(column):
            snapshot = json.loads(row[column])
            if not isinstance(snapshot, dict):
                raise ValueError("Invalid geometry snapshot in the dataset.")
            geometry.update({key: snapshot[key] for key in GEOMETRY_KEYS if key in snapshot})
            break
    for column, key in {**LEGACY_GEOMETRY, **{f"settings.{key}": key for key in GEOMETRY_KEYS}}.items():
        if key not in geometry and row.get(column) not in ("", None):
            geometry[key] = row[column]
    if not all(key in geometry for key in ("structure_family", "structure_name", "size", "cell_size", "solid_fraction")):
        raise ValueError("A completed sample is missing the geometry needed for a grouped split.")
    for key in ("size", "size_x", "size_y", "cell_size", "solid_fraction", "core_fraction"):
        if key in geometry:
            geometry[key] = _number(geometry[key])
    if any(geometry[key] is None for key in ("size", "cell_size", "solid_fraction")):
        raise ValueError("A completed sample has an invalid geometry size, period or density.")
    for axis in ("size_x", "size_y"):
        if geometry.get(axis) is None:
            geometry[axis] = geometry["size"]
    geometry["test_profile"] = geometry.get("test_profile") or "custom"
    geometry["structure_family"] = str(geometry["structure_family"]).strip().lower()
    geometry["tpms_morphology"] = str(geometry.get("tpms_morphology") or "sheet").strip().lower()
    geometry["specimen_architecture"] = geometry.get("specimen_architecture") or "solid"
    seed = _number(geometry.get("random_seed"))
    geometry["random_seed"] = 42 if seed is None else int(seed)
    return geometry


def _feature_column(key):
    prefix, _, field = key.partition(".")
    return (key in LEGACY_INPUTS or key in {"grid", "void_ratio"} or
            (prefix in DATA_PREFIXES and not field.startswith("truba_") and
             not field.endswith(("_path", "_name", "_bashrc")) and
             field not in {"distribution", "automatic_cores", "cores", "imported_geometry_role"}) or
            key in {"settings.structure_name", "settings.material_name"})


def training_request(dataset, target, methods, epochs, patience):
    columns, completed = dataset
    if target not in target_columns(columns, completed):
        raise ValueError("Choose a numerical result column.")
    if not methods or any(method not in METHODS for method in methods):
        raise ValueError("Choose a supported training method.")
    eligible = [row for row in completed if _number(row.get(target)) is not None]
    if len({row.get("Operation") for row in eligible}) > 1:
        raise ValueError("Use a dataset for a single analysis operation.")
    features = [key for key in columns if _feature_column(key) and len({row.get(key) for row in eligible}) > 1]
    # Fix the schema across all model comparisons and use the same eligible samples.
    categorical = [key for key in features
                   if any(row.get(key) not in ("", None) and _number(row[key]) is None for row in eligible)]
    spatial = bool(SPATIAL.intersection(methods))
    groups, samples = {}, []
    for index, row in enumerate(eligible, 1):
        geometry = _geometry(row)
        imported = geometry.pop("imported_geometry_path", "")
        if imported and not row.get("Geometry group"):
            raise ValueError("Imported geometries require an explicit Geometry group for a grouped split.")
        if spatial and imported:
            raise ValueError("3D CNN and Graph Neural Network require geometries generated by RustMech.")
        group = (row.get("Geometry group") or "").strip()
        if not group:
            geometry_key = {key: value for key, value in geometry.items() if value is not None}
            geometry_key.pop("imported_geometry_role", None)
            if geometry["structure_family"] != "porous":
                geometry_key.pop("random_seed")
            if geometry["test_profile"] == "custom":
                geometry_key.pop("specimen_architecture", None)
                geometry_key.pop("core_fraction", None)
            elif geometry["specimen_architecture"] == "solid":
                for key in ("structure_family", "structure_name", "tpms_morphology",
                            "random_seed", "cell_size", "solid_fraction", "size_x", "size_y"):
                    geometry_key.pop(key, None)
                geometry_key.pop("core_fraction", None)
            group = json.dumps(geometry_key, sort_keys=True, allow_nan=False)
        group_id = groups.setdefault(group, len(groups))
        values = [str(row.get(key) or "") if key in categorical else _number(row.get(key)) for key in features]
        samples.append({"sample": row.get("Sample") or row.get("Exp ID") or str(index),
                        "group": group_id, "values": values, "target": _number(row[target]),
                        **({"geometry": geometry} if spatial else {})})
    if len(groups) < 5:
        raise ValueError(f"At least 5 distinct geometry groups are required. Found {len(groups)} for this target.")
    if not features and any(method not in SPATIAL for method in methods):
        raise ValueError("The table models require at least one varying input column.")
    return dict(operation="learning_train", target=target, methods=list(methods), features=features,
                categorical=categorical, samples=samples, epochs=epochs, patience=patience,
                dataset_sha256=hashlib.sha256(json.dumps(samples, sort_keys=True).encode()).hexdigest())
