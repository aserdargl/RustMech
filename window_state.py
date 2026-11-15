"""Per-user window geometry and layout preferences for native Qt windows."""

from PySide6.QtCore import QEvent, QObject, QPoint, QSettings
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMainWindow, QMessageBox, QSplitter


class WindowStateStore(QObject):
    def __init__(self, app, settings=None):
        super().__init__(app)
        self.settings = settings if settings is not None else QSettings("RustMech", "RustMech")
        app.installEventFilter(self)

    @staticmethod
    def key(window):
        return f"windows/{type(window).__name__}/{window.objectName() or 'default'}"

    def save(self, window):
        key = self.key(window)
        # Qt saveGeometry retains normal bounds even when maximized/minimized.
        self.settings.setValue(f"{key}/geometry", window.saveGeometry())
        for index, splitter in enumerate(window.findChildren(QSplitter)):
            name = splitter.objectName() or str(index)
            self.settings.setValue(f"{key}/splitters/{name}", splitter.saveState())
            width = splitter.property("expandedSidebarWidth")
            if width is not None:
                self.settings.setValue(f"{key}/splitters/{name}/expandedSidebarWidth", width)
        if isinstance(window, QMainWindow):
            self.settings.setValue(f"{key}/docks", window.saveState())
        self.settings.sync()

    def restore(self, window):
        key = self.key(window)
        geometry = self.settings.value(f"{key}/geometry")
        if geometry is not None:
            window.restoreGeometry(geometry)
        # restoreGeometry adjusts saved screen/DPI information. Also constrain
        # normal windows to today's available desktop, including taskbars.
        if not window.isMaximized() and not window.isMinimized():
            frame = window.frameGeometry()
            screen = QApplication.screenAt(frame.center()) or QApplication.primaryScreen()
            if screen is not None:
                area = screen.availableGeometry()
                window.resize(min(window.width(), area.width()), min(window.height(), area.height()))
                frame = window.frameGeometry()
                window.move(QPoint(
                    max(area.left(), min(frame.left(), area.right() - frame.width() + 1)),
                    max(area.top(), min(frame.top(), area.bottom() - frame.height() + 1)),
                ))
        for index, splitter in enumerate(window.findChildren(QSplitter)):
            name = splitter.objectName() or str(index)
            state = self.settings.value(f"{key}/splitters/{name}")
            width = self.settings.value(f"{key}/splitters/{name}/expandedSidebarWidth")
            if width is not None:
                splitter.setProperty("expandedSidebarWidth", int(width))
            if state is not None:
                splitter.restoreState(state)
        if isinstance(window, QMainWindow):
            state = self.settings.value(f"{key}/docks")
            if state is not None:
                window.restoreState(state)

    def eventFilter(self, window, event):
        if (isinstance(window, (QDialog, QMainWindow)) and window.isWindow()
                and not isinstance(window, (QFileDialog, QMessageBox))):
            if event.type() == QEvent.Type.Show and not window.property("preferencesRestored"):
                window.setProperty("preferencesRestored", True)
                self.restore(window)
            elif event.type() in (QEvent.Type.Close, QEvent.Type.Hide) and window.property("preferencesRestored"):
                self.save(window)
        return False


def install_window_state(app):
    if not hasattr(app, "_rustmech_window_state"):
        app._rustmech_window_state = WindowStateStore(app)
