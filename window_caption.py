"""GraphCalc's integrated caption for RustMech's Qt windows."""

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QEvent, QMargins, QObject, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import QAbstractButton, QDialog, QHBoxLayout, QMainWindow, QWidget


CAPTION_HEIGHT = 25
FRAME_GRIP = 4
CAPTION_PROPERTY = "rustmechCaption"
WM_GETMINMAXINFO = 0x0024
WM_STYLECHANGING = 0x007C
WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_NCLBUTTONDBLCLK = 0x00A3
WM_DWMCOMPOSITIONCHANGED = 0x031E
HTCLIENT = 1
HTCAPTION = 2
HTSYSMENU = 3
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_EX_APPWINDOW = 0x00040000
WS_EX_DLGMODALFRAME = 0x00000001
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWCP_ROUND = 2
FRAME_BORDER_COLOR = 0x00A0A0A0
ACTION_HINTS = (
    ("minimize", Qt.WindowType.WindowMinimizeButtonHint),
    ("maximize", Qt.WindowType.WindowMaximizeButtonHint),
    ("close", Qt.WindowType.WindowCloseButtonHint),
)
FRAME_EVENTS = (
    QEvent.Type.Polish, QEvent.Type.Show, QEvent.Type.WinIdChange,
)
LAYOUT_EVENTS = FRAME_EVENTS + (
    QEvent.Type.Resize, QEvent.Type.WindowStateChange, QEvent.Type.WindowTitleChange,
    QEvent.Type.WindowIconChange, QEvent.Type.WindowActivate, QEvent.Type.WindowDeactivate,
    QEvent.Type.PaletteChange, QEvent.Type.DevicePixelRatioChange,
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


class WindowStyle(ctypes.Structure):
    _fields_ = (("old", wintypes.DWORD), ("new", wintypes.DWORD))


class DwmMargins(ctypes.Structure):
    _fields_ = (("left", ctypes.c_int), ("right", ctypes.c_int),
                ("top", ctypes.c_int), ("bottom", ctypes.c_int))


class WindowsCaptionFrame(QAbstractNativeEventFilter):
    """Provide system movement, resizing and work-area bounds for frameless windows."""

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
        self._set_dwm_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
        self._set_dwm_attribute.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
        self._extend_dwm_frame = ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea
        self._extend_dwm_frame.argtypes = (wintypes.HWND, ctypes.POINTER(DwmMargins))

    def frame_style(self, window, index, style):
        if index == GWL_STYLE:
            style &= ~(WS_CAPTION | WS_THICKFRAME)
            if not window.isFullScreen() and not window.windowFlags() & Qt.WindowType.MSWindowsFixedSizeDialogHint:
                # Keep Win32's move/size loop and leave caption painting to Qt.
                style |= WS_THICKFRAME
        elif index == GWL_EXSTYLE:
            style &= ~WS_EX_DLGMODALFRAME
            # Windows otherwise minimizes owned windows to a classic title strip on the desktop.
            if (window.windowFlags() & Qt.WindowType.WindowMinimizeButtonHint
                    and (window.parentWidget() is not None or window.windowType() == Qt.WindowType.Tool)):
                style |= WS_EX_APPWINDOW
        return style

    def configure(self, window):
        hwnd = int(window.internalWinId())
        if not hwnd:
            return
        for index in (GWL_STYLE, GWL_EXSTYLE):
            style = self._get_style(hwnd, index) & 0xFFFFFFFF
            desired = self.frame_style(window, index, style)
            if desired != style:
                self._set_style(hwnd, index, desired)
        # Qt can set the expected style bits before Windows has recalculated
        # the client rectangle. Force that calculation even when they match.
        if int(window.internalWinId()) != hwnd:
            return
        self._set_position(hwnd, None, 0, 0, 0, 0, 0x0037)  # SWP_FRAMECHANGED without move or resize.
        if int(window.internalWinId()) == hwnd:
            self.configure_dwm(hwnd)

    def configure_dwm(self, hwnd):
        for attribute, value in ((DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND),
                                 (DWMWA_BORDER_COLOR, FRAME_BORDER_COLOR)):
            native_value = wintypes.DWORD(value)
            self._set_dwm_attribute(hwnd, attribute, ctypes.byref(native_value), ctypes.sizeof(native_value))
        # Give DWM a frame to compose without changing Qt's client bounds.
        self._extend_dwm_frame(hwnd, ctypes.byref(DwmMargins(bottom=1)))

    def set_pinned(self, handle, enabled):
        # HWND_TOPMOST / HWND_NOTOPMOST, with no move, resize, activation or owner change.
        return self._set_position(int(handle.winId()), -1 if enabled else -2, 0, 0, 0, 0, 0x0213)

    def nativeEventFilter(self, event_type, message):
        if event_type != b"windows_generic_MSG":
            return False, 0
        native = wintypes.MSG.from_address(int(message))
        if native.message not in (WM_GETMINMAXINFO, WM_STYLECHANGING, WM_NCCALCSIZE, WM_NCHITTEST,
                                  WM_NCLBUTTONDBLCLK, WM_DWMCOMPOSITIONCHANGED):
            return False, 0
        window = QWidget.find(int(native.hWnd or 0))
        caption = window.property(CAPTION_PROPERTY) if window is not None else None
        if caption is None:
            return False, 0
        if native.message == WM_DWMCOMPOSITIONCHANGED:
            self.configure_dwm(native.hWnd)
            return False, 0
        if native.message == WM_STYLECHANGING:
            if native.lParam:
                style = WindowStyle.from_address(native.lParam)
                index = ctypes.c_int(native.wParam).value
                style.new = self.frame_style(window, index, style.new)
            return False, 0
        if window.isFullScreen() and not window.isMaximized():
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
        if native.message == WM_NCCALCSIZE:
            if native.lParam and self._is_zoomed(native.hWnd):
                rect = wintypes.RECT.from_address(native.lParam)
                monitor = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
                if self._monitor_info(self._monitor_from_rect(ctypes.byref(rect), 2), ctypes.byref(monitor)):
                    rect.left, rect.top = monitor.work.left, monitor.work.top
                    rect.right, rect.bottom = monitor.work.right, monitor.work.bottom
            return True, 0
        if window.isMinimized():
            return False, 0
        if native.message == WM_NCLBUTTONDBLCLK:
            can_maximize = window.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint
            return native.wParam == HTCAPTION and not can_maximize, 0
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
            return True, HTCLIENT
        if (window.windowFlags() & Qt.WindowType.WindowSystemMenuHint
                and not window.windowIcon().isNull() and QRect(8, 4, 16, 16).contains(position)):
            return True, HTSYSMENU
        return True, HTCAPTION


class CaptionButton(QAbstractButton):
    def __init__(self, action, label, parent):
        super().__init__(parent)
        self.action = action
        self.setObjectName("WindowCaption" + action.title())
        self.setAccessibleName(label)
        self.setToolTip(label)
        self.setFixedSize(CAPTION_HEIGHT, CAPTION_HEIGHT)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)

    def paintEvent(self, event):
        painter = QPainter(self)
        # A native move or restore can shift the caption without a Qt Leave event.
        hovered = self.isVisible() and self.rect().contains(self.mapFromGlobal(QCursor.pos()))
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
        if self.action == "close":
            ratio = painter.device().devicePixelRatioF()
            center = QPointF(round(self.width() * ratio) / (2 * ratio),
                             round(self.height() * ratio) / (2 * ratio))
            for direction in (-1, 1):
                offset = QPointF(4.5, direction * 4.5)
                painter.drawLine(center - offset, center + offset)
            return
        painter.translate(self.width() / 2, self.height() / 2)
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

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        self.update()

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
        painter.setPen(QColor("#64748B"))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))


class WindowCaption(QWidget):
    def __init__(self, window, flags, native_frame):
        super().__init__(window)
        self._native_frame = native_frame
        self.outline = WindowOutline(window) if native_frame is None else None
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
        self.actions = {}
        for action, label, callback in (
            ("minimize", self.tr("Minimize"), window.showMinimized),
            ("maximize", self.tr("Maximize"), self.toggle_maximized),
            ("close", self.tr("Close"), window.close),
        ):
            button = CaptionButton(action, label, self)
            button.clicked.connect(callback)
            controls.addWidget(button)
            self.actions[action] = button
        self.maximize = self.actions["maximize"]

    def sync_actions(self):
        flags = self.window().windowFlags()
        for action, hint in ACTION_HINTS:
            self.actions[action].setVisible(bool(flags & hint))
        label = self.tr("Restore") if self.window().isMaximized() else self.tr("Maximize")
        self.maximize.setAccessibleName(label)
        self.maximize.setToolTip(label)
        self.layout().activate()

    def repaint_actions(self):
        self.pin.update()
        for button in self.actions.values():
            button.update()

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
        if not window.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint:
            return
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
        if event.button() == Qt.MouseButton.LeftButton and self.maximize.isVisible():
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
        if self._native_frame is None and not window.isVisible():
            window.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        caption = WindowCaption(window, flags, self._native_frame)
        window.setProperty(CAPTION_PROPERTY, caption)
        return caption

    def _update_layout(self, window, caption):
        fullscreen = window.isFullScreen() and not window.isMaximized()
        grip = 0 if fullscreen or window.isMaximized() else FRAME_GRIP
        top = 0 if fullscreen else CAPTION_HEIGHT
        target_margins = caption.base_margins + QMargins(grip, grip + top, grip, grip)
        if window.contentsMargins() != target_margins:
            window.setContentsMargins(target_margins)
            if window.layout() is not None:
                window.layout().activate()
        caption.setGeometry(grip, grip, max(0, window.width() - 2 * grip), CAPTION_HEIGHT)
        caption.setVisible(not fullscreen)
        caption.sync_actions()
        caption.raise_()
        if caption.outline is not None:
            caption.outline.setGeometry(window.rect())
            caption.outline.setVisible(bool(grip))
            caption.outline.raise_()
            caption.outline.update()
        caption.update()
        caption.repaint_actions()

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind not in LAYOUT_EVENTS and kind not in (QEvent.Type.Move, QEvent.Type.MouseButtonPress):
            return False
        if not isinstance(watched, (QDialog, QMainWindow)) or not watched.isWindow():
            return False
        caption = watched.property(CAPTION_PROPERTY)
        if caption is None:
            if kind not in (QEvent.Type.Polish, QEvent.Type.Show) or watched.windowType() not in (
                Qt.WindowType.Window, Qt.WindowType.Dialog, Qt.WindowType.Sheet, Qt.WindowType.Tool
            ):
                return False
            # A native viewport or another Qt service may have polished the
            # widget before caption installation. Show is the final fallback.
            caption = self._attach(watched)
        if kind in LAYOUT_EVENTS:
            self._update_layout(watched, caption)
            if kind in FRAME_EVENTS and watched.internalWinId():
                if self._native_frame is not None:
                    self._native_frame.configure(watched)
                else:
                    handle = watched.windowHandle()
                    if handle is not None and not handle.flags() & Qt.WindowType.FramelessWindowHint:
                        handle.setFlag(Qt.WindowType.FramelessWindowHint)
                if caption.pin.isChecked():
                    caption.set_pinned(True)
        elif kind == QEvent.Type.Move:
            caption.repaint_actions()
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
