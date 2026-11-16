"""Regenerate Windows icon sizes from the editable application SVG."""

from io import BytesIO
from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer


def main():
    assets = Path(__file__).resolve().parents[1] / "assets" / "ui"
    renderer = QSvgRenderer(str(assets / "rustmech.svg"))
    if not renderer.isValid():
        raise ValueError("Application SVG could not be loaded")
    canvas = QImage(1024, 1024, QImage.Format.Format_ARGB32)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    renderer.render(painter)
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    canvas.save(buffer, "PNG")
    buffer.close()
    with Image.open(BytesIO(bytes(data))) as source:
        source.resize((256, 256), Image.Resampling.LANCZOS).save(assets / "rustmech.png")
        source.save(assets / "rustmech.ico", sizes=[
            (size, size) for size in (16, 20, 24, 32, 40, 48, 64, 128, 256)
        ])


if __name__ == "__main__":
    main()
