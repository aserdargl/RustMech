"""Portable sample names and per-user RustMech naming preferences."""
import json
import re
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication


def preferences():
    store = getattr(QApplication.instance(), "_rustmech_window_state", None)
    return store.settings if store is not None else QSettings("RustMech", "RustMech")


def load_rules(path=None):
    try:
        value = json.loads(preferences().value("doe/sampleSettings/" + str(path or "rules"), "{}"))
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def save_rule(context, rule, path=None):
    rules = load_rules(path)
    rules[context] = rule
    settings = preferences()
    settings.setValue("doe/sampleSettings/" + str(path or "rules"), json.dumps(rules))
    settings.sync()


def sample_name(rule, values):
    from doe_sample_inputs import display_value
    values = {key: display_value(key, value) for key, value in values.items()}
    parts = []
    for field in rule.get("fields", []):
        key = field["key"]
        if key not in values or values[key] is None or str(values[key]).strip() == "":
            continue
        value = values[key]
        text = format(value, ".12g") if isinstance(value, float) else str(value).strip()
        text = text.replace(".", rule.get("decimal", "."))
        if rule.get("labels"):
            text = f"{field.get('label', key)}={text}"
        text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
        text = re.sub(r"\s+", "_", text).strip(" .")
        if text:
            parts.append(text)
    name = rule.get("separator", "-").join(parts).strip(" .")[:180].rstrip(" .")
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        name = "_" + name
    return name


def unique_name(name, existing):
    used = {str(item).casefold() for item in existing}
    candidate, index = name, 2
    while candidate.casefold() in used:
        candidate = f"{name}-{index:02d}"
        index += 1
    return candidate


def validate_sample_state(state):
    """Validate optional plan data before constructing any table widgets."""
    from doe_sample_inputs import FIELDS
    from doe_plan import _settings
    if not isinstance(state, dict):
        raise ValueError('Invalid sample settings.')
    if not state:
        return {}
    keys = {key for key, _ in FIELDS}
    rule = state.get('rule', {})
    if (not isinstance(rule, dict) or not isinstance(rule.get('fields', []), list)
            or len(rule.get('fields', [])) > len(keys)
            or rule.get('separator', '-') not in ('-', '_', '')
            or rule.get('decimal', '.') not in ('.', '_', '')
            or not isinstance(rule.get('labels', False), bool)):
        raise ValueError('Invalid sample naming rule.')
    seen = set()
    for field in rule.get('fields', []):
        if (not isinstance(field, dict) or field.get('key') not in keys
                or field['key'] in seen or not isinstance(field.get('label', ''), str)):
            raise ValueError('Invalid sample naming field.')
        seen.add(field['key'])
    combinations = state.get('combinations', {})
    if not isinstance(combinations, dict) or 'base' not in combinations:
        raise ValueError('Invalid sample combination settings.')
    _settings(combinations['base'])
    parameters = combinations.get('parameters', {})
    if not isinstance(parameters, dict):
        raise ValueError('Invalid sample parameter settings.')
    columns = parameters.get('value_columns', 3)
    axes = parameters.get('axes', {})
    if type(columns) is not int or not 1 <= columns <= 64 or not isinstance(axes, dict) or set(axes) - keys:
        raise ValueError('Invalid sample value columns.')
    for axis in axes.values():
        if (not isinstance(axis, dict) or not isinstance(axis.get('vary', False), bool)
                or not isinstance(axis.get('fixed', ''), str)
                or not isinstance(axis.get('values', []), list)
                or len(axis.get('values', [])) > columns
                or any(not isinstance(value, str) for value in axis.get('values', []))):
            raise ValueError('Invalid sample axis.')
    return state
