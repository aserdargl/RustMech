"""Two compact views of the selected contact faces in source coordinates."""
from hashlib import sha256
from uuid import uuid4

import numpy as np
import pyvista as pv
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout


def store_face_preview(directory, surface, aligned, direction):
    roles = aligned.cell_data['compression_face']
    axis = np.array(direction, dtype=float)
    digest = sha256(b'face-preview-v1')
    for data in (surface.points, surface.faces, roles, axis):
        digest.update(np.ascontiguousarray(data))
    path = directory / f'{digest.hexdigest()}.png'
    if path.is_file():
        return str(path)

    directory.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.stem}.{uuid4().hex}.png')
    colors = np.array(((78, 168, 222), (34, 197, 94), (249, 115, 22)), dtype=np.uint8)[roles]
    axis /= np.linalg.norm(axis)
    side = np.cross(axis, np.eye(3)[np.argmin(abs(axis))])
    side /= np.linalg.norm(side)
    side += .65 * np.cross(axis, side)
    center = np.asarray(surface.center)

    plotter = pv.Plotter(off_screen=True, shape=(1, 2), window_size=(448, 210))
    try:
        for panel, sign in enumerate((-1, 1)):
            plotter.subplot(0, panel)
            plotter.set_background('#FCFCFB')
            plotter.add_mesh(surface, scalars=colors, rgb=True, preference='cell', show_scalar_bar=False)
            plotter.add_axes(line_width=2)
            plotter.camera_position = (center + surface.length * (side + 1.5 * sign * axis), center, axis)
            plotter.enable_parallel_projection()
            plotter.reset_camera()
        plotter.show(screenshot=str(temporary), auto_close=False)
        temporary.replace(path)
    finally:
        plotter.close()
        temporary.unlink(missing_ok=True)
    return str(path)


class FacePreviewPopup(QFrame):
    def __init__(self, parent):
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setObjectName('batchFacePreview')
        self.setStyleSheet('QFrame#batchFacePreview { background: #FCFCFB; border: 1px solid #D5DBE2; border-radius: 4px; }')
        self.anchor = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 6)
        layout.setSpacing(3)
        legend = QHBoxLayout()
        for text in ('Support face', 'Compression face'):
            label = QLabel(text)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            legend.addWidget(label, 1)
        layout.addLayout(legend)
        self.image = QLabel()
        layout.addWidget(self.image)
        parent.installEventFilter(self)

    def show_for(self, anchor, path):
        image = QPixmap(path)
        if image.isNull():
            return False
        self.anchor = anchor
        self.image.setPixmap(image)
        self.adjustSize()
        area = anchor.screen().availableGeometry()
        origin = anchor.mapToGlobal(QPoint(0, anchor.height() + 4))
        x = max(area.left(), min(origin.x(), area.right() - self.width() + 1))
        y = origin.y()
        if y + self.height() > area.bottom() + 1:
            y -= anchor.height() + self.height() + 8
        self.move(x, max(area.top(), y))
        self.show()
        return True

    def hideEvent(self, event):
        self.anchor = None
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.WindowDeactivate, QEvent.Type.Hide, QEvent.Type.Move):
            self.hide()
        return super().eventFilter(watched, event)


class FacePreviewButton(QToolButton):
    def __init__(self, popup):
        super().__init__()
        self.popup = popup
        self.preview_path = ''

    def event(self, event):
        kind = event.type()
        if kind == QEvent.Type.ToolTip and self.preview_path and self.popup.show_for(self, self.preview_path):
            return True
        if kind in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress,
                    QEvent.Type.Hide, QEvent.Type.Move, QEvent.Type.EnabledChange) and self.popup.anchor is self:
            self.popup.hide()
        return super().event(event)
