"""Always-visible camera actions anchored below the viewport orientation cube."""
from PyQt6.QtCore import QByteArray, QEvent, QSize, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QFrame, QToolButton, QVBoxLayout


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
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(4, 4, 4, 4)
        self.column.setSpacing(3)

    def add_camera_action(self, action, kind):
        action.setIcon(camera_icon(kind))
        button = QToolButton(self)
        button.setDefaultAction(action)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setAccessibleName(action.text())
        button.setIconSize(QSize(20, 20))
        button.setFixedSize(30, 25)
        self.column.addWidget(button)

    def attach(self, viewport):
        self.setParent(viewport)
        viewport.installEventFilter(self)
        self.show()
        self._reposition()

    def _reposition(self):
        viewport = self.parentWidget()
        self.adjustSize()
        # The orientation marker occupies (0.90, 0.84, 0.99, 0.99) in VTK.
        x = round(viewport.width() * 0.945 - self.width() / 2)
        y = round(viewport.height() * 0.16) + 8
        self.move(max(0, min(x, viewport.width() - self.width())), y)
        self.raise_()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._reposition()
        return super().eventFilter(watched, event)
