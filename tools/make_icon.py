"""Draws the app icon and the startup splash.

    python tools/make_icon.py

Writes netstab/assets/netstab.ico (+ a 256 px PNG) and build/splash.png,
which carries the current version and is shown while the exe unpacks.
The .ico embeds one PNG per size, which Windows Vista+ reads natively.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QImage, QPainter, QPainterPath,
                           QPen)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from netstab import __version__  # noqa: E402

OUT = ROOT / "netstab" / "assets"
SPLASH = ROOT / "build" / "splash.png"
SPLASH_SIZE = (520, 300)
# Where the build puts PyInstaller's status line; keep the drawing clear of it.
SPLASH_TEXT_POS = (32, 276)
SIZES = [16, 24, 32, 48, 64, 128, 256]


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#171a21"))
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 52 * s, 52 * s)

    # A steady ping trace with one blip: "stable network".
    pts = [(36, 150), (84, 150), (104, 92), (128, 196), (150, 120), (164, 150), (220, 150)]
    path = QPainterPath(QPointF(pts[0][0] * s, pts[0][1] * s))
    for x, y in pts[1:]:
        path.lineTo(x * s, y * s)
    pen = QPen(QColor("#f5a524"), max(1.6, 20 * s), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    if size >= 32:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#3ccf6e"))
        p.drawEllipse(QPointF(204 * s, 70 * s), 18 * s, 18 * s)
    p.end()
    return img


def draw_splash() -> QImage:
    w, h = SPLASH_SIZE
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor("#0f1115"))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.setPen(QPen(QColor("#262b36"), 2))
    p.drawRect(1, 1, w - 2, h - 2)
    p.drawImage(32, 40, draw(112))

    p.setPen(QColor("#e6e8ee"))
    p.setFont(QFont("Segoe UI", 34, QFont.Bold))
    p.drawText(QRectF(168, 52, 320, 56), Qt.AlignLeft | Qt.AlignVCenter, "net")
    net_w = p.fontMetrics().horizontalAdvance("net")
    p.setPen(QColor("#f5a524"))
    p.drawText(QRectF(168 + net_w, 52, 320, 56), Qt.AlignLeft | Qt.AlignVCenter, "stab")
    p.setPen(QColor("#8a92a6"))
    p.setFont(QFont("Segoe UI", 12))
    p.drawText(QRectF(170, 110, 320, 28), Qt.AlignLeft | Qt.AlignVCenter,
               "Test your connection before you queue.")

    # A ping trace across the card, echoing the icon.
    trace = [(32, 214), (180, 214), (204, 186), (230, 238), (254, 200), (270, 214), (488, 214)]
    path = QPainterPath(QPointF(*trace[0]))
    for x, y in trace[1:]:
        path.lineTo(x, y)
    p.setPen(QPen(QColor("#f5a524"), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPath(path)

    # Top-right: the bottom line belongs to the status text, which can be long.
    p.setPen(QColor("#555c6b"))
    p.setFont(QFont("Segoe UI", 9))
    p.drawText(QRectF(0, 12, w - 18, 20), Qt.AlignRight | Qt.AlignVCenter,
               f"v{__version__} · open source (MIT)")
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def main() -> None:
    QGuiApplication(sys.argv)
    OUT.mkdir(parents=True, exist_ok=True)
    pngs = [png_bytes(draw(n)) for n in SIZES]
    header = struct.pack("<HHH", 0, 1, len(SIZES))
    offset = 6 + 16 * len(SIZES)
    entries, data = b"", b""
    for n, png in zip(SIZES, pngs):
        dim = 0 if n >= 256 else n  # 0 means 256 in the ICO format
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        data += png
    (OUT / "netstab.ico").write_bytes(header + entries + data)
    (OUT / "netstab.png").write_bytes(pngs[-1])
    SPLASH.parent.mkdir(exist_ok=True)
    draw_splash().save(str(SPLASH))
    print("wrote", OUT / "netstab.ico", "and", SPLASH)


if __name__ == "__main__":
    main()
