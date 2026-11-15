"""Generate the app icon (PNG sizes + multi-size .ico) with PySide6.

Design: dark rounded square, vertical 9:16 'phone' frame in red, white play
triangle - matches the app's theme.
"""
import os
import struct
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"D:\AI_Clip_Generator")

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QLinearGradient,
    QBrush,
)
from PySide6.QtWidgets import QApplication

ASSETS = r"D:\AI_Clip_Generator\app\assets"


def draw_icon(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256.0

    # background rounded square with subtle gradient
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0, QColor("#1d2128"))
    grad.setColorAt(1, QColor("#121419"))
    path = QPainterPath()
    path.addRoundedRect(QRectF(4 * s, 4 * s, 248 * s, 248 * s), 52 * s, 52 * s)
    p.fillPath(path, QBrush(grad))

    # vertical 9:16 phone/short shape
    phone_w, phone_h = 118 * s, 210 * s
    px, py = (size - phone_w) / 2, (size - phone_h) / 2
    red = QLinearGradient(px, py, px + phone_w, py + phone_h)
    red.setColorAt(0, QColor("#f0575c"))
    red.setColorAt(1, QColor("#c7353b"))
    phone = QPainterPath()
    phone.addRoundedRect(QRectF(px, py, phone_w, phone_h), 26 * s, 26 * s)
    p.fillPath(phone, QBrush(red))

    # white play triangle
    tri = QPainterPath()
    cx, cy = size / 2, size / 2
    tri.moveTo(cx - 20 * s, cy - 30 * s)
    tri.lineTo(cx - 20 * s, cy + 30 * s)
    tri.lineTo(cx + 28 * s, cy)
    tri.closeSubpath()
    p.setPen(Qt.NoPen)
    p.fillPath(tri, QColor("white"))

    # accent line at top of phone (like a caption bar)
    p.setPen(QPen(QColor(255, 255, 255, 190), 7 * s, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(cx - 28 * s, py + 26 * s), QPointF(cx + 28 * s, py + 26 * s))

    p.end()
    return pm


def write_ico(pngs: dict[int, bytes], out: str) -> None:
    """Assemble a real .ico file embedding PNG-compressed entries."""
    n = len(pngs)
    header = struct.pack("<HHH", 0, 1, n)          # ICO, 1 image, n entries
    offset = 6 + 16 * n
    entries, blobs = [], []
    for size, data in sorted(pngs.items()):
        w = size if size < 256 else 0
        h = w
        entries.append(struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32,
                                   len(data), offset))
        blobs.append(data)
        offset += len(data)
    with open(out, "wb") as f:
        f.write(header + b"".join(entries) + b"".join(blobs))


def main() -> int:
    app = QApplication(sys.argv)
    os.makedirs(ASSETS, exist_ok=True)
    pngs: dict[int, bytes] = {}
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = draw_icon(size)
        path = os.path.join(ASSETS, f"icon_{size}.png")
        pm.save(path, "PNG")
        if size >= 32:
            pngs[size] = open(path, "rb").read()
    write_ico(pngs, os.path.join(ASSETS, "app.ico"))
    # also a default-size png for the window icon
    draw_icon(256).save(os.path.join(ASSETS, "app.png"), "PNG")
    print("ICON_WRITTEN:", os.path.join(ASSETS, "app.ico"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
