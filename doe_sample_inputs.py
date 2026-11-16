"""Typed sample fields use the same geometry and material settings as DoE."""
import math
from PySide6.QtWidgets import QLineEdit, QComboBox
from studio_style import CompactComboBox
from structure_library import ALL_STRUCTURES_BY_FAMILY, STRUCTURE_FAMILIES, TPMS_MORPHOLOGIES

FIELDS = (
    ('structure_family', 'Family'), ('structure_name', 'Structure'),
    ('tpms_morphology', 'Morphology'), ('size', 'Cube size (mm)'),
    ('cell_size', 'Cell size (mm)'), ('solid_fraction', 'Density (%)'),
    ('mesh_resolution', 'Mesh size (mm)'), ('local_mesh_size', 'Local mesh size (mm)'),
    ('mesh_type', 'Element type'), ('tetra_strategy', 'Tetra mesher'),
    ('compression', 'Compression (mm)'), ('random_seed', 'Seed'),
    ('material_name', 'Material'), ('material_density', 'Material density (g/cm3)'),
    ('young_modulus', "Young's modulus (MPa)"), ('poisson_ratio', "Poisson's ratio"),
    ('yield_stress', 'Yield stress (MPa)'), ('tangent_modulus', 'Tangent modulus (MPa)'),
    ('substeps', 'Substeps'),
)


def selected_settings(studio):
    if 0 <= studio._selected_design < len(studio._designs):
        return studio._designs[studio._selected_design]
    return studio.planner.draft_settings()


def sample_fields(studio):
    settings = selected_settings(studio)
    choices = {
        'structure_family': list(ALL_STRUCTURES_BY_FAMILY),
        'structure_name': list(dict.fromkeys(name for names in ALL_STRUCTURES_BY_FAMILY.values() for name in names)),
        'tpms_morphology': list(TPMS_MORPHOLOGIES),
        'mesh_type': ['HEX8', 'HEX20', 'TET4', 'TET10'],
        'tetra_strategy': ['auto', 'netgen', 'periodic'],
    }
    result = []
    for key, label in FIELDS:
        value = getattr(settings, key)
        if key in choices:
            control = CompactComboBox()
            labels = {'structure_family': STRUCTURE_FAMILIES, 'tpms_morphology': TPMS_MORPHOLOGIES}.get(key)
            if labels:
                for identifier, caption in labels.items():
                    control.addItem(caption, identifier)
                control.setCurrentIndex(control.findData(value))
            else:
                control.addItems(choices[key])
                control.setCurrentText(str(value))
        else:
            control = QLineEdit(str(value))
            control.setProperty('sampleType', type(value).__name__)
        control.setParent(studio)
        control.hide()
        result.append((key, label, value, control))
    return result


def values_for_widget(widget, text, varying=False):
    text = text.strip()
    if isinstance(widget, QComboBox):
        index = widget.findData(text)
        if index < 0:
            index = widget.findText(text)
        if index < 0:
            raise ValueError('Choose a value from the dropdown.')
        return (widget.itemData(index) if widget.itemData(index) is not None else widget.itemText(index),)
    kind = widget.property('sampleType')
    if kind in ('int', 'float'):
        try:
            value = float(text)
        except ValueError:
            raise ValueError('Enter a finite number.') from None
        if not math.isfinite(value) or kind == 'int' and not value.is_integer():
            raise ValueError('Enter a finite integer.' if kind == 'int' else 'Enter a finite number.')
        return (int(value) if kind == 'int' else value,)
    if not text:
        raise ValueError('Enter a value.')
    return (text,)


def display_value(key, value):
    labels = {'structure_family': STRUCTURE_FAMILIES, 'tpms_morphology': TPMS_MORPHOLOGIES}.get(key, {})
    return labels.get(value, value) if labels else value
