"""Always-visible camera actions anchored below the viewport orientation cube."""
from PySide6.QtCore import QByteArray, QCoreApplication, QEvent, QSize, Qt
from PySide6.QtGui import QIcon, QKeyEvent, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QFrame, QScrollArea, QToolButton, QVBoxLayout

from preview_viewport import ORIENTATION_RIGHT_INSET, orientation_cube_size
from studio_style import CONTROL_HEIGHT


def camera_icon(kind):
    """Small vector camera symbols with explicit axis/side markings."""
    cube = ('<path d="M12 2 22 7v11l-10 5-10-5V7Z M2 7l10 5 10-5 '
            'M12 12v11" fill="#E5EDF5" stroke="#315B87" stroke-width="1.4"/>')
    if kind in ("iso", "xy", "-xy", "xz", "-xz", "yz", "-yz"):
        label = "3D" if kind == "iso" else kind.upper().replace("-", "−")
        drawing = cube + (f'<rect x="0" y="12" width="24" height="11" rx="2" fill="#FAFAFA"/>'
                          f'<text x="12" y="21" text-anchor="middle" font-family="Segoe UI" '
                          f'font-size="10" font-weight="600" fill="#1E4D8D">{label}</text>')
    elif kind == "orthographic":
        drawing = '<path d="M3 3h15v15H3Z M7 7h15v15H7Z M3 3l4 4m11-4 4 4M3 18l4 4" fill="#E5EDF5" stroke="#315B87" stroke-width="1.5"/>'
    elif kind == "perspective":
        drawing = '<path d="M2 2h20v20H2Z M8 8h8v8H8Z M2 2l6 6m14-6-6 6M2 22l6-6m14 6-6-6" fill="#E5EDF5" stroke="#315B87" stroke-width="1.5"/>'
    elif kind == "reset":
        drawing = '<path d="M5 7a9 9 0 1 1-1 9M5 2v6h6" fill="none" stroke="#315B87" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
    elif kind == "geometry":
        drawing = ('<path d="M3 15c0-4 2-6 5-6 2 0 4-5 8-5 3 0 5 3 5 6 '
                   '0 4-3 5-6 6-3 1-3 4-7 4-3 0-5-2-5-5Z" '
                   'fill="#9DC7E9" stroke="#315B87" stroke-width="1.5"/>')
    elif kind in ("mesh", "overlay"):
        wireframe = ('<path d="M3 8l12-4 6 5-1 9-11 2-6-5Z" '
                     'stroke="#315B87" stroke-width="1.4" stroke-linejoin="round"/>'
                     '<path d="M3 8l9 3 3-7m-3 7 9-2m-9 2-9 4m9-4-3 9m3-9 8 7" '
                     'fill="none" stroke="#315B87" stroke-width="1.25"/>')
        if kind == "overlay":
            drawing = ('<path d="M3 14l11-4 7 4v4l-11 4-7-4Z" fill="#9DC7E9" '
                       'stroke="#315B87" stroke-width="1.4" stroke-linejoin="round"/>'
                       f'<g transform="translate(0 -3)" fill="#DCEBF8">{wireframe}</g>')
        else:
            drawing = f'<g fill="#F5F8FC">{wireframe}</g>'
    else:
        drawing = '<path d="M5 2h14v21H5Z" fill="#E5EDF5" stroke="#315B87" stroke-width="1.5"/><path d="M8 7h8M8 11h8M8 15h8M8 19h5" stroke="#315B87" stroke-width="1.5"/>'
    svg = QSvgRenderer(QByteArray(('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">' + drawing + '</svg>').encode()))
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    svg.render(painter)
    painter.end()
    return QIcon(pixmap)


class CameraToolbar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("cameraToolbar")
        self.setAccessibleName("View")
        self.setFixedWidth(44)
        self._stack = QVBoxLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.setSpacing(5)

        self._camera_scroll = QScrollArea(self)
        self._camera_scroll.setObjectName("cameraViewGroup")
        self._camera_scroll.setWidgetResizable(True)
        self._camera_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._camera_scroll.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._camera_panel = QFrame()
        self._camera_panel.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.column = QVBoxLayout(self._camera_panel)
        self.column.setContentsMargins(0, 3, 0, 3)
        self.column.setSpacing(2)
        self.column.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._camera_scroll.setWidget(self._camera_panel)
        self._camera_scroll.verticalScrollBar().rangeChanged.connect(self._balance_camera_margins)
        self._stack.addWidget(self._camera_scroll)

        self.display_modes = QFrame(self)
        self.display_modes.setObjectName("cameraDisplayModes")
        self.display_modes.setAccessibleName("3D display mode")
        self._mode_column = QVBoxLayout(self.display_modes)
        self._mode_column.setContentsMargins(3, 3, 3, 3)
        self._mode_column.setSpacing(2)
        self._stack.addWidget(self.display_modes)

    def add_camera_action(self, action, kind):
        button = self._add_button(self.column, kind)
        action.setIcon(button.icon())
        button.setDefaultAction(action)
        button.setAccessibleName(action.text())

    def add_display_mode(self, kind, name, tooltip):
        button = self._add_button(self._mode_column, kind)
        button.setCheckable(True)
        button.setAccessibleName(name)
        button.setToolTip(tooltip)
        button.installEventFilter(self)
        return button

    def _add_button(self, layout, kind):
        button = QToolButton()
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.setIcon(camera_icon(kind))
        button.setIconSize(QSize(20, 20))
        button.setFixedSize(30, CONTROL_HEIGHT)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        return button

    def _balance_camera_margins(self, minimum, maximum):
        # Mirror the left scrollbar's gutter so the buttons stay centered in the frame.
        scrollbar = self._camera_scroll.verticalScrollBar()
        right_margin = scrollbar.sizeHint().width() if maximum > minimum else 0
        self.column.setContentsMargins(0, 3, right_margin, 3)

    def show_display_modes(self, visible):
        self.display_modes.setVisible(visible)
        self._reposition()

    def attach(self, viewport):
        self.setParent(viewport)
        viewport.installEventFilter(self)
        self.show()
        self._reposition()

    def _reposition(self):
        viewport = self.parentWidget()
        y = orientation_cube_size(viewport) + 5
        mode_height = (0 if self.display_modes.isHidden()
                       else self.display_modes.sizeHint().height() + self._stack.spacing())
        camera_height = self.column.sizeHint().height() + 2 * self._camera_scroll.frameWidth()
        self._camera_scroll.setFixedHeight(min(camera_height, max(0, viewport.height() - y - mode_height)))
        self.adjustSize()
        self.move(max(0, viewport.width() - self.width() - ORIENTATION_RIGHT_INSET), y)
        self.raise_()

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._reposition()
        if isinstance(watched, QToolButton) and event.type() == QEvent.Type.KeyPress:
            # The controls were arranged horizontally before they moved here.
            key = {Qt.Key.Key_Left: Qt.Key.Key_Up, Qt.Key.Key_Right: Qt.Key.Key_Down}.get(event.key())
            if key is not None:
                QCoreApplication.sendEvent(watched, QKeyEvent(event.type(), key, event.modifiers()))
                return True
        return super().eventFilter(watched, event)
