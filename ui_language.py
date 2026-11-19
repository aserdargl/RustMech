"""Live EN/TR presentation without translating solver IDs, inputs or saved data."""
from functools import lru_cache
from pathlib import Path
import atexit
from html import escape, unescape
import re
import sys
from string import Formatter
from weakref import WeakKeyDictionary
from shiboken6 import isValid

from PySide6.QtCore import QEvent, QObject, QSettings, QSignalBlocker, QSize, QTranslator, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QAbstractButton, QAbstractItemView, QGroupBox, QHeaderView,
    QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QStyledItemDelegate,
    QTabBar, QTableWidget, QToolButton, QWidget,
)

from ui_translations import FIELD_LABELS, TURKISH, TEMPLATES

LANGUAGE_KEY = 'ui/language'
_REVERSE = {target: source for source, target in TURKISH.items()}


def _template_pattern(source):
    parts = []
    for literal, field, _, _ in Formatter().parse(source):
        parts.append(re.escape(literal))
        if field is not None:
            parts.append(f'(?P<{field}>[^\n]+?)' if field in ('name', 'side') else f'(?P<{field}>.+?)')
    return re.compile('^' + ''.join(parts) + '$', re.DOTALL)


_PATTERNS = [(_template_pattern(source), target) for source, target in TEMPLATES.items()]


@lru_cache(maxsize=8192)
def turkish(text):
    if text in TURKISH:
        return TURKISH[text]
    if text.startswith('<qt>') and text.endswith('</qt>'):
        return '<qt>' + escape(turkish(unescape(text[4:-5]))) + '</qt>'
    if text.startswith('&') and text[1:] in TURKISH:
        return '&' + TURKISH[text[1:]]
    for pattern, target in _PATTERNS:
        match = pattern.fullmatch(text)
        if match:
            return target.format(**{key: FIELD_LABELS.get(value, turkish(value)) if key == 'name' else turkish(value)
                                    for key, value in match.groupdict().items()})
    # Translate separately authored rich-text/compound labels, preserving markup,
    # numerical values, paths and untranslated scientific/solver names.
    parts = re.split(r'(<[^>]+>|\n| · | \| | / )', text)
    if len(parts) > 1:
        return ''.join(part if part.startswith('<') else TURKISH.get(part, part) for part in parts)
    return text


def translate(text):
    text = str(text)
    if not any(character.isalpha() for character in text):
        return text
    app = QApplication.instance()
    manager = getattr(app, '_rustmech_language', None)
    return turkish(text) if manager is not None and not manager._closed and manager.language == 'tr' else text


class _QtTranslator(QTranslator):
    def isEmpty(self):
        return False

    def translate(self, context, source, disambiguation=None, n=-1):
        result = translate(source)
        # PySide converts None to a null QString so Qt can fall back to the
        # source text. An empty Python string is an explicit blank translation.
        return result if result != source else None


class _DisplayDelegate(QStyledItemDelegate):
    def displayText(self, value, locale):
        return translate(super().displayText(value, locale))


class LanguageManager(QObject):
    changed = Signal(str)

    def __init__(self, app, preferences):
        super().__init__(app)
        self.app = app
        self.preferences = preferences
        self.language = str(preferences.value(LANGUAGE_KEY, 'en')).lower()
        if self.language not in ('en', 'tr'):
            self.language = 'en'
        self._sources = WeakKeyDictionary()
        self._busy = False
        self._closed = False
        self.qt_translator = _QtTranslator(self)
        app.installTranslator(self.qt_translator)
        app.installEventFilter(self)
        app.aboutToQuit.connect(self.shutdown)
        atexit.register(self.shutdown)

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        if isValid(self) and isValid(self.app):
            self.app.removeEventFilter(self)
            if isValid(self.qt_translator):
                self.app.removeTranslator(self.qt_translator)

    def set_language(self, language):
        if self._closed or not isValid(self.preferences):
            return
        language = str(language).lower()
        if language not in ('en', 'tr'):
            raise ValueError('Unsupported interface language.')
        self.language = language
        self.preferences.setValue(LANGUAGE_KEY, language)
        self.preferences.sync()
        for widget in self.app.allWidgets():
            self.localize(widget)
            widget.update()
        self.changed.emit(language)

    def _property(self, obj, name, getter, setter):
        current = getter()
        if not current:
            return
        sources = self._sources.setdefault(obj, {})
        previous = sources.get(name)
        if previous and current == previous[1]:
            source = previous[0]
        elif current.startswith('<qt>') and current.endswith('</qt>'):
            inner = unescape(current[4:-5])
            source = '<qt>' + escape(_REVERSE.get(inner, inner)) + '</qt>'
        else:
            source = _REVERSE.get(current, current)
        translated = turkish(source) if self.language == 'tr' else source
        sources[name] = (source, translated)
        if translated != current:
            setter(translated)

    def localize(self, widget):
        if self._busy or widget.property('languageSelector'):
            return
        self._busy = True
        try:
            self._property(widget, 'toolTip', widget.toolTip, widget.setToolTip)
            self._property(widget, 'accessibleName', widget.accessibleName, widget.setAccessibleName)
            if widget.isWindow():
                self._property(widget, 'windowTitle', widget.windowTitle, widget.setWindowTitle)
            if isinstance(widget, (QLabel, QAbstractButton)):
                self._property(widget, 'text', widget.text, widget.setText)
                if (isinstance(widget, QAbstractButton) and not isinstance(widget, QToolButton)
                        and not widget.property('managesLocalizedWidth')):
                    if widget.minimumWidth() == widget.maximumWidth():
                        sources = self._sources.setdefault(widget, {})
                        needed = widget.fontMetrics().horizontalAdvance(widget.text()) + 24
                        if self.language == 'tr':
                            sources.setdefault('originalWidth', widget.width())
                            widget.setFixedWidth(max(widget.width(), needed))
                        elif 'originalWidth' in sources:
                            widget.setFixedWidth(sources.pop('originalWidth'))
            if isinstance(widget, QGroupBox):
                self._property(widget, 'title', widget.title, widget.setTitle)
            if isinstance(widget, QLineEdit):
                # Usernames, passwords, numeric inputs and file paths are never rewritten.
                self._property(widget, 'placeholder', widget.placeholderText, widget.setPlaceholderText)
            if isinstance(widget, QTabBar):
                for index in range(widget.count()):
                    self._property(widget, f'tab/{index}', lambda i=index: widget.tabText(i),
                                   lambda value, i=index: widget.setTabText(i, value))
            if isinstance(widget, QMenu):
                for action in widget.actions():
                    self._property(action, 'text', action.text, action.setText)
            if isinstance(widget, QMessageBox):
                for name in ('text', 'informativeText', 'detailedText'):
                    self._property(widget, name, getattr(widget, name), getattr(widget, 'set' + name[0].upper() + name[1:]))
            if isinstance(widget, QHeaderView) and isinstance(widget.parentWidget(), QTableWidget):
                table = widget.parentWidget()
                for index in range(table.columnCount()):
                    item = table.horizontalHeaderItem(index)
                    if item is not None:
                        self._property(table, f'header/{index}', item.text, item.setText)
            if isinstance(widget, QAbstractItemView) and not isinstance(widget, QHeaderView):
                if type(widget.itemDelegate()) is QStyledItemDelegate:
                    widget.setItemDelegate(_DisplayDelegate(widget))
        finally:
            self._busy = False

    def localize_tree(self, root):
        self.localize(root)
        for widget in root.findChildren(QWidget):
            self.localize(widget)

    def localize_plotter(self, plotter):
        """Relabel existing result bars without rebuilding data or moving the camera."""
        changed = False
        for actor in plotter.scalar_bars.values():
            before = actor.GetTitle()
            if isinstance(before, str):
                self._property(actor, 'title', actor.GetTitle, actor.SetTitle)
                changed |= before != actor.GetTitle()
        if changed:
            plotter.render()

    def localize_figure(self, figure):
        for axis in figure.axes:
            self._property(axis, 'xlabel', axis.get_xlabel, axis.set_xlabel)
            self._property(axis, 'ylabel', axis.get_ylabel, axis.set_ylabel)
            legend = axis.get_legend()
            if legend is not None:
                for text in legend.get_texts():
                    self._property(text, 'text', text.get_text, text.set_text)
        if figure.canvas is not None:
            figure.canvas.draw_idle()

    def eventFilter(self, obj, event):
        if self._closed or sys.is_finalizing() or QApplication.closingDown():
            return False
        if isinstance(obj, QWidget) and event.type() in (QEvent.Type.Show, QEvent.Type.Paint):
            self.localize(obj)
        return False


def install_language(app=None, preferences=None):
    app = app or QApplication.instance()
    if app is None:
        raise RuntimeError('Create QApplication before installing interface languages.')
    manager = getattr(app, '_rustmech_language', None)
    if manager is not None and preferences is None:
        return manager
    preferences = preferences if preferences is not None else QSettings('RustMech', 'RustMech')
    if manager is None:
        manager = LanguageManager(app, preferences)
        app._rustmech_language = manager
    elif manager.preferences.fileName() != preferences.fileName():
        manager.preferences = preferences
        manager.set_language(str(preferences.value(LANGUAGE_KEY, 'en')).lower())
    return manager


class LanguageSelector(QWidget):
    """Shared compact selector for the upper-right corner of each workspace."""
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        from studio_style import CompactComboBox, CONTROL_HEIGHT
        self.manager = manager
        self.setObjectName('languageSelector')
        self.setProperty('languageSelector', True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.combo = CompactComboBox()
        self.combo.setProperty('languageSelector', True)
        self.combo.setProperty('flagSelector', True)
        self.combo.setFixedSize(58, CONTROL_HEIGHT)
        self.combo.setIconSize(QSize(24, 16))
        assets = Path(__file__).resolve().parent / 'assets' / 'ui'
        self.combo.addItem(QIcon(str(assets / 'flag-en.svg')), 'English', 'en')
        self.combo.addItem(QIcon(str(assets / 'flag-tr.svg')), 'Türkçe', 'tr')
        self.combo.setAccessibleName('Interface language')
        self.combo.currentIndexChanged.connect(lambda index: manager.set_language(self.combo.currentData()) if index >= 0 else None)
        layout.addWidget(self.combo)
        manager.changed.connect(self.sync)
        self.sync()

    def sync(self, *_):
        blocker = QSignalBlocker(self.combo)
        self.combo.setCurrentIndex(self.combo.findData(self.manager.language))
        self.combo.setToolTip('Arayüz dili: Türkçe' if self.manager.language == 'tr' else 'Interface language: English')
        del blocker
