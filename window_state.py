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
        if isinstance(window, QFileDialog):
            return f"windows/QFileDialog/{window.acceptMode().name}/{window.fileMode().name}"
        return f"windows/{type(window).__name__}/{window.objectName() or 'default'}"

    @staticmethod
    def splitters(window):
        if isinstance(window, QFileDialog):
            # Qt owns the picker splitter. Its shortcuts pane is hidden.
            return []
        # QObject descendants also include owned top-level windows. Their
        # layouts belong exclusively to their own preferences, not the owner.
        return [splitter for splitter in window.findChildren(QSplitter)
                if splitter.window() is window]

    def save(self, window):
        key = self.key(window)
        # Qt saveGeometry retains normal bounds even when maximized/minimized.
        self.settings.setValue(f"{key}/geometry", window.saveGeometry())
        for index, splitter in enumerate(self.splitters(window)):
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
        for index, splitter in enumerate(self.splitters(window)):
            name = splitter.objectName() or str(index)
            state = self.settings.value(f"{key}/splitters/{name}")
            width = self.settings.value(f"{key}/splitters/{name}/expandedSidebarWidth")
            if width is not None:
                splitter.setProperty("expandedSidebarWidth", int(width))
            if state is not None:
                # Qt restores orientation too. Never let a stale numeric key
                # turn a horizontal settings column into a vertical panel.
                probe = QSplitter()
                valid = probe.restoreState(state)
                if valid and probe.orientation() == splitter.orientation():
                    splitter.restoreState(state)
                probe.deleteLater()
        if isinstance(window, QMainWindow):
            state = self.settings.value(f"{key}/docks")
            if state is not None:
                window.restoreState(state)

    def eventFilter(self, window, event):
        kind = event.type()
        if kind not in (QEvent.Type.Show, QEvent.Type.Close, QEvent.Type.Hide):
            return False
        if (isinstance(window, (QDialog, QMainWindow)) and window.isWindow()
                and not isinstance(window, QMessageBox)):
            if isinstance(window, QFileDialog) and not window.testOption(QFileDialog.Option.DontUseNativeDialog):
                return False
            if kind == QEvent.Type.Show and not window.property("preferencesRestored"):
                window.setProperty("preferencesRestored", True)
                self.restore(window)
            elif kind in (QEvent.Type.Close, QEvent.Type.Hide) and window.property("preferencesRestored"):
                self.save(window)
        return False


def install_window_state(app):
    if not hasattr(app, "_rustmech_window_state"):
        app._rustmech_window_state = WindowStateStore(app)


def show_restored_window(window):
    """Reopen a hidden maximized Qt window without losing its normal bounds."""
    store = getattr(QApplication.instance(), "_rustmech_window_state", None)
    if not window.isVisible() and window.isMaximized() and store is not None:
        geometry = store.settings.value(f"{store.key(window)}/geometry")
        if geometry is not None:
            # On Windows, showing an already maximized hidden widget makes Qt
            # replace normalGeometry with the screen bounds. Reset that native
            # state before restoring the actual saved normal/maximized geometry.
            window.showNormal()
            window.restoreGeometry(geometry)
        else:
            window.show()
    else:
        window.show()
    if window.isMinimized():
        window.showNormal()
    window.raise_()
    window.activateWindow()
