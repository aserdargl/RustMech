"""GraphCalc's integrated caption for RustMech's Qt windows."""

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QEvent, QMargins, QObject, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import QAbstractButton, QApplication, QDialog, QHBoxLayout, QMainWindow, QWidget


CAPTION_HEIGHT = 25
FRAME_GRIP = 4
CAPTION_PROPERTY = "rustmechCaption"
WM_GETMINMAXINFO = 0x0024
WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_NCLBUTTONDBLCLK = 0x00A3
HTTRANSPARENT = -1
WS_DLGFRAME = 0x00400000
ACTION_HINTS = (
    ("minimize", Qt.WindowType.WindowMinimizeButtonHint),
    ("maximize", Qt.WindowType.WindowMaximizeButtonHint),
    ("close", Qt.WindowType.WindowCloseButtonHint),
)
RESIZE_HITS = {
    Qt.Edge.LeftEdge: 10, Qt.Edge.RightEdge: 11, Qt.Edge.TopEdge: 12,
    Qt.Edge.TopEdge | Qt.Edge.LeftEdge: 13, Qt.Edge.TopEdge | Qt.Edge.RightEdge: 14,
    Qt.Edge.BottomEdge: 15, Qt.Edge.BottomEdge | Qt.Edge.LeftEdge: 16,
    Qt.Edge.BottomEdge | Qt.Edge.RightEdge: 17,
}


class MonitorInfo(ctypes.Structure):
    _fields_ = (("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                ("work", wintypes.RECT), ("flags", wintypes.DWORD))


class MinMaxInfo(ctypes.Structure):
    _fields_ = (("reserved", wintypes.POINT), ("max_size", wintypes.POINT),
                ("max_position", wintypes.POINT), ("min_track", wintypes.POINT),
                ("max_track", wintypes.POINT))


class WindowsCaptionFrame(QAbstractNativeEventFilter):
    """Retain the Windows sizing frame and route title area hits to the OS."""

    def __init__(self):
        super().__init__()
        api = ctypes.windll.user32
        self._get_style = api.GetWindowLongW
        self._get_style.argtypes = (wintypes.HWND, ctypes.c_int)
        self._set_style = api.SetWindowLongW
        self._set_style.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.LONG)
        self._set_position = api.SetWindowPos
        self._set_position.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, wintypes.UINT)
        self._client_to_screen = api.ClientToScreen
        self._client_to_screen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
        self._monitor_from_rect = api.MonitorFromRect
        self._monitor_from_rect.argtypes = (ctypes.POINTER(wintypes.RECT), wintypes.DWORD)
        self._monitor_from_rect.restype = wintypes.HMONITOR
        self._monitor_from_window = api.MonitorFromWindow
        self._monitor_from_window.argtypes = (wintypes.HWND, wintypes.DWORD)
        self._monitor_from_window.restype = wintypes.HMONITOR
        self._monitor_info = api.GetMonitorInfoW
        self._monitor_info.argtypes = (wintypes.HMONITOR, ctypes.POINTER(MonitorInfo))
        self._is_zoomed = api.IsZoomed
        self._is_zoomed.argtypes = (wintypes.HWND,)

    def update_frame(self, window):
        handle = window.windowHandle()
        if handle is None or (window.isFullScreen() and not window.isMaximized()):
            return
        hwnd = int(handle.winId())
        style = self._get_style(hwnd, -16)  # GWL_STYLE
        # Keep the native resize border, system menu and window-state machinery.
        # WS_CAPTION is WS_BORDER | WS_DLGFRAME; clearing the latter suppresses the OS title.
        if style & WS_DLGFRAME:
            self._set_style(hwnd, -16, style & ~WS_DLGFRAME)
            self._set_position(hwnd, None, 0, 0, 0, 0, 0x0037)  # Recalculate the frame in place.

    def set_pinned(self, handle, enabled):
        # HWND_TOPMOST / HWND_NOTOPMOST, with no move, resize, activation or owner change.
        return self._set_position(int(handle.winId()), -1 if enabled else -2, 0, 0, 0, 0, 0x0213)

    def nativeEventFilter(self, event_type, message):
        if event_type != b"windows_generic_MSG":
            return False, 0
        native = wintypes.MSG.from_address(int(message))
        if native.message not in (WM_GETMINMAXINFO, WM_NCCALCSIZE, WM_NCHITTEST, WM_NCLBUTTONDBLCLK):
            return False, 0
        window = QWidget.find(int(native.hWnd or 0))
        if native.message == WM_NCHITTEST and isinstance(window, WindowOutline):
            return True, HTTRANSPARENT
        caption = window.property(CAPTION_PROPERTY) if window is not None else None
        if caption is None or (window.isFullScreen() and not window.isMaximized()):
            return False, 0
        if native.message == WM_GETMINMAXINFO:
            # Windows uses the outer HWND bounds to decide if a window covers the taskbar.
            monitor = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
            if native.lParam and self._monitor_info(self._monitor_from_window(native.hWnd, 2), ctypes.byref(monitor)):
                bounds = MinMaxInfo.from_address(native.lParam)
                bounds.max_position.x = monitor.work.left - monitor.monitor.left
                bounds.max_position.y = monitor.work.top - monitor.monitor.top
                bounds.max_size.x = monitor.work.right - monitor.work.left
                bounds.max_size.y = monitor.work.bottom - monitor.work.top
            # Let Qt still populate the minimum and maximum tracking sizes.
            return False, 0
        if window.isMinimized():
            return False, 0
        if native.message == WM_NCCALCSIZE:
            if native.lParam and self._is_zoomed(native.hWnd):
                # Both NCCALCSIZE forms start with the proposed client RECT.
                rect = wintypes.RECT.from_address(native.lParam)
                monitor = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
                if self._monitor_info(self._monitor_from_rect(ctypes.byref(rect), 2), ctypes.byref(monitor)):
                    rect.left, rect.top = monitor.work.left, monitor.work.top
                    rect.right, rect.bottom = monitor.work.right, monitor.work.bottom
            return True, 0
        if native.message == WM_NCLBUTTONDBLCLK:
            # Preserve the action hints when dragging or double-clicking the native title area.
            return native.wParam == 2 and caption.maximize is None, 0
        origin = wintypes.POINT()
        if not self._client_to_screen(native.hWnd, ctypes.byref(origin)):
            return False, 0
        x = ctypes.c_short(native.lParam & 0xFFFF).value
        y = ctypes.c_short((native.lParam >> 16) & 0xFFFF).value
        ratio = window.devicePixelRatioF()
        point = QPoint(round((x - origin.x) / ratio), round((y - origin.y) / ratio))
        if not window.isMaximized():
            hit = RESIZE_HITS.get(resize_edges(window, point))
            if hit is not None:
                return True, hit
        position = caption.mapFromParent(point)
        if not caption.rect().contains(position) or position.x() >= caption.pin.x():
            return False, 0
        if not window.windowIcon().isNull() and QRect(8, 4, 16, 16).contains(position):
            return True, 3  # HTSYSMENU
        return True, 2  # HTCAPTION


class CaptionButton(QAbstractButton):
    def __init__(self, action, label, parent):
        super().__init__(parent)
        self.action = action
        self.setObjectName("WindowCaption" + action.title())
        self.setAccessibleName(label)
        self.setToolTip(label)
        self.setFixedSize(CAPTION_HEIGHT, CAPTION_HEIGHT)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def paintEvent(self, event):
        painter = QPainter(self)
        hovered = self.underMouse()
        selected = self.isChecked()
        if hovered and self.isDown():
            painter.fillRect(self.rect(), QColor(0, 0, 0, 16))
        color = ("#C42B1C" if hovered and self.isDown() else
                 "#E81123" if hovered or selected else
                 "#1A1A1A" if self.window().isActiveWindow() else "#777777")
        pen = QPen(QColor(color), 2 if hovered or selected else 1)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(12.5, 12.5)
        if self.action == "pin":
            if not selected:
                painter.rotate(-35)
            outline = QPainterPath(QPointF(-2.5, -4.5))
            for x, y in ((2.5, -4.5), (1.5, -3.5), (1.5, -1), (3, 1.5),
                         (-3, 1.5), (-1.5, -1), (-1.5, -3.5)):
                outline.lineTo(x, y)
            outline.closeSubpath()
            painter.drawPath(outline)
            painter.drawLine(QPointF(0, 1.5), QPointF(0, 5.5))
        elif self.action == "close":
            painter.drawLine(QPointF(-4.5, -4.5), QPointF(4.5, 4.5))
            painter.drawLine(QPointF(-4.5, 4.5), QPointF(4.5, -4.5))
        else:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            if self.action == "minimize":
                painter.drawLine(-4, 0, 5, 0)
            elif self.window().isMaximized():
                painter.drawLine(-2, -5, 5, -5)
                painter.drawLine(5, -5, 5, 2)
                painter.drawLine(-2, -5, -2, -4)
                painter.drawRect(-4, -3, 7, 7)
            else:
                painter.drawRect(-4, -5, 9, 9)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()


class WindowOutline(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def resizeEvent(self, event):
        # A native viewport can make this sibling native too. Leave the interior
        # outside its window region so it cannot cover the viewport or take clicks.
        outline = self.rect()
        self.setMask(QRegion(outline).subtracted(QRegion(outline.adjusted(1, 1, -1, -1))))
        super().resizeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QColor("#64748B" if self.window().isActiveWindow() else "#9CA3AF"))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))


class WindowCaption(QWidget):
    def __init__(self, window, flags, native_frame):
        super().__init__(window)
        self._native_frame = native_frame
        self.outline = WindowOutline(window)
        self.base_margins = window.contentsMargins()
        self.setObjectName("integratedWindowCaption")
        self.setFixedHeight(CAPTION_HEIGHT)
        self._title_font = QFont("Segoe UI", 9)
        controls = QHBoxLayout(self)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(0)
        controls.addStretch()
        self.pin = CaptionButton("pin", self.tr("Keep this window on top"), self)
        self.pin.setCheckable(True)
        self.pin.setChecked(bool(flags & Qt.WindowType.WindowStaysOnTopHint))
        self.pin.clicked.connect(self.set_pinned)
        controls.addWidget(self.pin)
        self.maximize = None
        for action, hint in ACTION_HINTS:
            if not flags & hint:
                continue
            label, callback = {
                "minimize": (self.tr("Minimize"), window.showMinimized),
                "maximize": (self.tr("Maximize"), self.toggle_maximized),
                "close": (self.tr("Close"), window.close),
            }[action]
            button = CaptionButton(action, label, self)
            button.clicked.connect(callback)
            controls.addWidget(button)
            if action == "maximize":
                self.maximize = button

    def set_pinned(self, enabled):
        handle = self.window().windowHandle()
        if handle is None:
            self.pin.setChecked(not enabled)
        elif self._native_frame is not None:
            if not self._native_frame.set_pinned(handle, enabled):
                self.pin.setChecked(not enabled)
        else:
            handle.setFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)

    def toggle_maximized(self):
        window = self.window()
        if window.isMaximized():
            window.showNormal()
        else:
            window.showMaximized()

    def paintEvent(self, event):
        window = self.window()
        painter = QPainter(self)
        painter.fillRect(self.rect(), window.palette().window())
        painter.fillRect(0, self.height() - 1, self.width(), 1, QColor("#D5DBE2"))
        painter.setFont(self._title_font)
        painter.setPen(window.palette().windowText().color())
        left = 8
        icon = window.windowIcon()
        if not icon.isNull():
            icon.paint(painter, left, 4, 16, 16)
            left += 21
        title_rect = QRect(left, 0, max(0, self.pin.x() - left - 5), self.height())
        title = painter.fontMetrics().elidedText(window.windowTitle(), Qt.TextElideMode.ElideRight,
                                                 title_rect.width())
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)

    def mousePressEvent(self, event):
        handle = self.window().windowHandle()
        if event.button() == Qt.MouseButton.LeftButton and handle is not None and handle.startSystemMove():
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.maximize is not None:
            self.toggle_maximized()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class WindowCaptionService(QObject):
    """Add chrome to regular Qt windows before their first native show."""

    def __init__(self, app):
        super().__init__(app)
        self._native_frame = WindowsCaptionFrame() if app.platformName() == "windows" else None
        if self._native_frame is not None:
            app.installNativeEventFilter(self._native_frame)

    def _attach(self, window):
        flags = window.windowFlags()
        if window.property(CAPTION_PROPERTY):
            return
        if self._native_frame is None:
            window.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        caption = WindowCaption(window, flags, self._native_frame)
        window.setProperty(CAPTION_PROPERTY, caption)
        self._update_layout(window, caption)

    def _update_layout(self, window, caption):
        fullscreen = window.isFullScreen() and not window.isMaximized()
        grip = 0 if fullscreen or window.isMaximized() else FRAME_GRIP
        top = 0 if fullscreen else CAPTION_HEIGHT
        target_margins = caption.base_margins + QMargins(grip, grip + top, grip, grip)
        if window.contentsMargins() != target_margins:
            window.setContentsMargins(target_margins)
        caption.setGeometry(grip, grip, max(0, window.width() - 2 * grip), CAPTION_HEIGHT)
        caption.setVisible(not fullscreen)
        caption.raise_()
        caption.outline.setGeometry(window.rect())
        caption.outline.setVisible(bool(grip))
        caption.outline.raise_()
        caption.outline.update()
        if caption.maximize is not None:
            label = self.tr("Restore") if window.isMaximized() else self.tr("Maximize")
            caption.maximize.setAccessibleName(label)
            caption.maximize.setToolTip(label)
        caption.update()
        for button in caption.findChildren(CaptionButton):
            button.update()

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Type.Polish and isinstance(watched, (QDialog, QMainWindow)):
            if watched.isWindow() and watched.windowType() in (
                Qt.WindowType.Window, Qt.WindowType.Dialog, Qt.WindowType.Sheet, Qt.WindowType.Tool
            ):
                self._attach(watched)
        elif isinstance(watched, (QDialog, QMainWindow)) and (caption := watched.property(CAPTION_PROPERTY)):
            if kind in (QEvent.Type.Resize, QEvent.Type.WindowStateChange, QEvent.Type.Show,
                        QEvent.Type.WindowTitleChange, QEvent.Type.WindowIconChange,
                        QEvent.Type.WindowActivate, QEvent.Type.WindowDeactivate, QEvent.Type.PaletteChange,
                        QEvent.Type.WinIdChange):
                self._update_layout(watched, caption)
                if self._native_frame is None and kind in (QEvent.Type.WinIdChange, QEvent.Type.Show):
                    handle = watched.windowHandle()
                    if handle is not None and not handle.flags() & Qt.WindowType.FramelessWindowHint:
                        handle.setFlag(Qt.WindowType.FramelessWindowHint)
                if self._native_frame is not None and kind in (
                    QEvent.Type.WinIdChange, QEvent.Type.Show, QEvent.Type.WindowStateChange
                ):
                    self._native_frame.update_frame(watched)
                if kind == QEvent.Type.Show and caption.pin.isChecked():
                    caption.set_pinned(True)
            elif (self._native_frame is None and kind == QEvent.Type.MouseButtonPress
                  and event.button() == Qt.MouseButton.LeftButton):
                if not watched.isMaximized() and not watched.isFullScreen():
                    edges = resize_edges(watched, event.position())
                    handle = watched.windowHandle()
                    if edges and handle is not None and handle.startSystemResize(edges):
                        return True
        return False


def resize_edges(window, point):
    if window.windowFlags() & Qt.WindowType.MSWindowsFixedSizeDialogHint:
        return Qt.Edge(0)
    edges = Qt.Edge(0)
    if window.minimumWidth() < window.maximumWidth():
        if point.x() < FRAME_GRIP:
            edges |= Qt.Edge.LeftEdge
        elif point.x() >= window.width() - FRAME_GRIP:
            edges |= Qt.Edge.RightEdge
    if window.minimumHeight() < window.maximumHeight():
        if point.y() < FRAME_GRIP:
            edges |= Qt.Edge.TopEdge
        elif point.y() >= window.height() - FRAME_GRIP:
            edges |= Qt.Edge.BottomEdge
    return edges


def install_window_captions(app):
    if not hasattr(app, "_rustmech_caption_service"):
        app.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
        app._rustmech_caption_service = WindowCaptionService(app)
        app.installEventFilter(app._rustmech_caption_service)
