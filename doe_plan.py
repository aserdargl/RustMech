"""Versioned, atomic storage for a complete DoE plan; never executes saved jobs."""
from dataclasses import asdict, fields
import json
import math
from pathlib import Path

from PySide6.QtCore import QIODevice, QSaveFile

from model import Settings, settings_from_dict
from structure_library import ALL_STRUCTURES_BY_FAMILY, TPMS_MORPHOLOGIES

PLAN_FORMAT = 'rustmech-doe-plan'
PLAN_VERSION = 1
PLAN_FILTER = 'RustMech DoE plan (*.rustmech-doe.json);;JSON files (*.json)'
FACTOR_LIMITS = {'size': (1, 100), 'cell_size': (.1, 100), 'solid_fraction': (1, 99)}


def _number(value, low=-math.inf, high=math.inf, *, integer=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not low <= value <= high
            or (integer and not isinstance(value, int))):
        raise ValueError('The plan contains an invalid numeric value.')
    return value


def _boolean(value):
    if not isinstance(value, bool):
        raise ValueError('The plan contains an invalid checkbox value.')
    return value


def _settings(data):
    if not isinstance(data, dict):
        raise ValueError('Missing geometry settings.')
    for name, default in asdict(Settings()).items():
        if name not in data:
            continue
        value = data[name]
        if name == 'hardening_curve':
            if not isinstance(value, (list, tuple)):
                raise ValueError('Invalid material curve.')
            for point in value:
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    raise ValueError('Invalid material curve point.')
                for coordinate in point:
                    _number(coordinate)
        elif default is None:
            if value is not None:
                if name in ('automatic_cores', 'automatic_contact_parameters'):
                    _boolean(value)
                elif name == 'material_card':
                    if not isinstance(value, dict):
                        raise ValueError('Invalid material card.')
                else:
                    _number(value, integer=name in ('minimum_substeps', 'maximum_substeps'))
        elif isinstance(default, bool):
            _boolean(value)
        elif isinstance(default, str):
            if not isinstance(value, str):
                raise ValueError(f'Invalid text setting: {name}.')
        else:
            _number(value, integer=isinstance(default, int))
    settings = settings_from_dict(data)
    if (settings.structure_family not in ALL_STRUCTURES_BY_FAMILY
            or settings.structure_name not in ALL_STRUCTURES_BY_FAMILY[settings.structure_family]
            or settings.tpms_morphology not in TPMS_MORPHOLOGIES):
        raise ValueError('The plan contains an unknown geometry type.')
    return settings


def _factors(data):
    if not isinstance(data, dict) or set(data) - FACTOR_LIMITS.keys():
        raise ValueError('The plan contains an unknown experiment variable.')
    result = {}
    for name, limits in data.items():
        if not isinstance(limits, (list, tuple)) or len(limits) != 3:
            raise ValueError('Invalid experiment variable limits.')
        low, high = FACTOR_LIMITS[name]
        result[name] = (_number(limits[0], low, high), _number(limits[1], low, high),
                        _number(limits[2], 2, 50, integer=True))
    return result


def decode_plan(payload):
    """Validate the entire document before the current workspace is changed."""
    from doe_studio import ExperimentRow, MAX_DOE_ROWS
    try:
        if (not isinstance(payload, dict) or payload.get('format') != PLAN_FORMAT
                or type(payload.get('version')) is not int or payload['version'] != PLAN_VERSION):
            raise ValueError('Unsupported RustMech DoE plan format or version.')
        entries = payload['designs']
        if not isinstance(entries, list) or len(entries) > MAX_DOE_ROWS:
            raise ValueError('Invalid saved design list.')
        designs = [dict(settings=_settings(entry['settings']), enabled=_boolean(entry['enabled']),
                        factors=_factors(entry['factors'])) for entry in entries]
        options = payload['options']
        if options['sampling'] not in ('full_factorial', 'lhs'):
            raise ValueError('Unknown sampling method.')
        for name, low, high in (('lhs_count', 2, 500), ('seed', 1, 999999),
                                ('ml_count', 2, 5000), ('ml_seed', 1, 999999)):
            _number(options[name], low, high, integer=True)
        _boolean(options['common_strain_enabled'])
        _number(options['common_strain'], .1, 95)
        selection = payload['selection']
        _number(selection['design'], -1, len(designs) - 1, integer=True)
        _number(selection['matrix'], 0, 1, integer=True)
        _number(selection['tab'], 0, 4, integer=True)
        editor = payload['variable_editor']
        if not isinstance(editor, dict) or set(editor) != FACTOR_LIMITS.keys():
            raise ValueError('Invalid saved variable editor.')
        for name, state in editor.items():
            _boolean(state['enabled'])
            _factors({name: (state['min'], state['max'], state['levels'])})
        matrices = {}
        for key in ('doe', 'ml'):
            rows = payload['matrices'][key]
            if not isinstance(rows, list) or len(rows) > MAX_DOE_ROWS:
                raise ValueError('Invalid saved experiment matrix.')
            matrices[key] = []
            for raw in rows:
                values = dict(raw)
                snapshot = values.get('design_settings')
                values['design_settings'] = _settings(snapshot) if snapshot is not None else None
                row = ExperimentRow(**values)
                for field in fields(row):
                    value = getattr(row, field.name)
                    if field.name == 'design_settings':
                        continue
                    if field.name == 'enabled':
                        _boolean(value)
                    elif field.name in ('exp_id', 'structure_family', 'structure_name',
                                        'tpms_morphology', 'status', 'run_folder', 'started_at'):
                        if not isinstance(value, str):
                            raise ValueError('Invalid experiment text field.')
                    elif value is not None:
                        _number(value, integer=field.name in ('random_seed', 'hex8_elements', 'attempts'))
                if any(getattr(row, name) is None for name in
                       ('size', 'cell_size', 'solid_fraction', 'mesh_resolution', 'compression', 'random_seed')):
                    raise ValueError('Missing experiment dimensions.')
                matrices[key].append(row)
        from doe_sample_rules import validate_sample_state
        sample_settings = validate_sample_state(payload.get('sample_settings', {}))
        return dict(sample_settings=sample_settings, designs=designs, options=dict(options), selection=dict(selection),
                    draft=_settings(payload['draft']), variable_editor=editor, matrices=matrices)
    except (KeyError, TypeError, AttributeError, OverflowError) as error:
        raise ValueError('The DoE plan is incomplete or malformed.') from error


def read_plan(path):
    path = Path(path)
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('This DoE plan exceeds the 64 MB size limit.')
    return decode_plan(json.loads(path.read_text(encoding='utf-8-sig')))


def write_plan(path, payload):
    decode_plan(payload)
    encoded = (json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
    if len(encoded) > 64 * 1024 * 1024:
        raise ValueError('This DoE plan exceeds the 64 MB size limit.')
    target = QSaveFile(str(path))
    if not target.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError(target.errorString())
    if target.write(encoded) != len(encoded):
        target.cancelWriting()
        raise OSError('Could not write the complete DoE plan.')
    if not target.commit():
        raise OSError(target.errorString())
