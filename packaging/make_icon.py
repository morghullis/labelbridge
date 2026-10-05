"""Genera los iconos de la app: la máscara dorada de Pedro Yoruba Jewelry dentro de una etiqueta de joyería.

Salida: assets/icon.ico (Windows, varios tamaños), assets/icon.icns (Mac, con iconutil), assets/icon.png y
assets/icons/app-<tamaño>.png.
Fuente: assets/brand-logo.png (logo de la tienda). Sin logo se usa el icono genérico de LabelBridge.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from labelbridge.app import _ensure_qt_plugin_path  # noqa: E402

_ensure_qt_plugin_path()

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QBrush,
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QApplication  # noqa: E402

from labelbridge.printing.barcode import code128_widths  # noqa: E402
from labelbridge.ui.icons import generic_icon_pixmap  # noqa: E402

LOGO = ROOT / "assets" / "brand-logo.png"
EMBLEM_BOX = QRect(116, 137, 1074, 876)  # corona + máscara (sin los textos)
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
PNG_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)


def gold(x1: float, y1: float, x2: float, y2: float) -> QLinearGradient:
    gradient = QLinearGradient(x1, y1, x2, y2)
    for stop, color in ((0.0, "#FCEABB"), (0.28, "#D9AE55"), (0.52, "#8E6522"), (0.76, "#EBCB78"), (1.0, "#B5862F")):
        gradient.setColorAt(stop, QColor(color))
    return gradient


def load_emblem() -> QImage:
    """Recorta la máscara y vuelve transparente el fondo negro (el brillo del dorado es la opacidad)."""
    crop = QImage(str(LOGO)).copy(EMBLEM_BOX).convertToFormat(QImage.Format.Format_ARGB32)
    gray = crop.convertToFormat(QImage.Format.Format_Grayscale8)
    levels = bytes(max(0, min(255, (v - 12) * 255 // 58)) for v in range(256))
    alpha = QImage(
        bytes(gray.constBits()).translate(levels), gray.width(), gray.height(), gray.bytesPerLine(),
        QImage.Format.Format_Grayscale8,
    ).copy()
    crop.setAlphaChannel(alpha)
    return crop


def tag_path(cx: float, cy: float, width: float, height: float, chamfer: float, radius: float) -> QPainterPath:
    left, right = cx - width / 2, cx + width / 2
    top, bottom = cy - height / 2, cy + height / 2
    path = QPainterPath()
    path.moveTo(left + chamfer, top)
    path.lineTo(right - chamfer, top)
    path.lineTo(right, top + chamfer)
    path.lineTo(right, bottom - radius)
    path.quadTo(right, bottom, right - radius, bottom)
    path.lineTo(left + radius, bottom)
    path.quadTo(left, bottom, left, bottom - radius)
    path.lineTo(left, top + chamfer)
    path.closeSubpath()
    return path


def render_brand_icon(size: int, emblem: QImage) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    s = size / 1024.0
    small = size <= 32

    # Fondo: cuadrado redondeado negro cálido, como el logo.
    background = QRadialGradient(QPointF(512 * s, 360 * s), 760 * s)
    background.setColorAt(0.0, QColor("#2B2216"))
    background.setColorAt(0.55, QColor("#0D0B08"))
    background.setColorAt(1.0, QColor("#040404"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(background))
    inset = 8 * s if small else 20 * s
    p.drawRoundedRect(QRectF(inset, inset, size - 2 * inset, size - 2 * inset), 210 * s, 210 * s)

    # Etiqueta de joyería ligeramente inclinada.
    p.save()
    p.translate(512 * s, 540 * s)
    p.rotate(-7)
    p.translate(-512 * s, -540 * s)
    cx, cy = 512 * s, 545 * s
    width, height = (700 if small else 640) * s, (820 if small else 770) * s
    path = tag_path(cx, cy, width, height, chamfer=(150 if small else 130) * s, radius=46 * s)
    body = QLinearGradient(0, cy - height / 2, 0, cy + height / 2)
    body.setColorAt(0.0, QColor("#17130E"))
    body.setColorAt(1.0, QColor("#050505"))
    p.setBrush(QBrush(body))
    border = max(1.2, (40 if small else 24) * s)
    p.setPen(QPen(QBrush(gold(cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2)), border))
    p.drawPath(path)

    top = cy - height / 2
    if not small:
        # Agujero con aro dorado y cordón.
        hole = QPointF(cx, top + 96 * s)
        p.setPen(QPen(QBrush(gold(cx - 50 * s, hole.y() - 50 * s, cx + 50 * s, hole.y() + 50 * s)), 13 * s))
        p.setBrush(QColor("#0B0907"))
        p.drawEllipse(hole, 34 * s, 34 * s)
        cord = QPainterPath(QPointF(hole.x() - 20 * s, hole.y() - 26 * s))
        cord.cubicTo(hole.x() - 120 * s, hole.y() - 190 * s, hole.x() - 260 * s, hole.y() - 120 * s, hole.x() - 330 * s, hole.y() - 210 * s)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QBrush(gold(0, 0, 400 * s, 200 * s)), 10 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawPath(cord)

    # Máscara de la marca.
    emblem_w = (600 if small else 500) * s
    emblem_h = emblem_w * emblem.height() / emblem.width()
    emblem_top = top + (95 if small else 165) * s
    p.drawImage(QRectF(cx - emblem_w / 2, emblem_top, emblem_w, emblem_h), emblem)

    # Código de barras dorado: la "etiqueta" de envío.
    if size >= 40:
        bars_top = emblem_top + emblem_h + 34 * s
        bars_h = cy + height / 2 - bars_top - 58 * s
        widths = code128_widths("PY-LABEL")
        total = sum(widths)
        bars_w = 430 * s
        x = cx - bars_w / 2
        module = bars_w / total
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(gold(x, bars_top, x + bars_w, bars_top + bars_h)))
        for index, w in enumerate(widths):
            if index % 2 == 0:
                p.drawRect(QRectF(x, bars_top, w * module, bars_h))
            x += w * module
    p.restore()
    p.end()
    return image


def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def write_ico(path: Path, images: list[tuple[int, bytes]]) -> None:
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, payload = b"", b""
    for size, data in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset + len(payload))
        payload += data
    path.write_bytes(header + entries + payload)


# Iconos de Mac: cada tamaño con su versión @2x (la retícula de Apple deja ~10 % de margen alrededor).
ICNS_SIZES = (16, 32, 128, 256, 512)
MAC_SCALE = 824 / 1024


def mac_image(render, size: int) -> QImage:
    """El icono a ~80 % centrado en un lienzo transparente, como los demás iconos del Dock."""
    canvas = QImage(size, size, QImage.Format.Format_ARGB32)
    canvas.fill(Qt.GlobalColor.transparent)
    inner = max(1, round(size * MAC_SCALE))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    offset = (size - inner) // 2
    painter.drawImage(QRect(offset, offset, inner, inner), render(inner))
    painter.end()
    return canvas


def write_icns(path: Path, render) -> bool:
    """assets/icon.icns con iconutil (solo existe en Mac; en Windows no hace falta)."""
    if not shutil.which("iconutil"):
        return False
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        for size in ICNS_SIZES:
            mac_image(render, size).save(str(iconset / f"icon_{size}x{size}.png"))
            mac_image(render, size * 2).save(str(iconset / f"icon_{size}x{size}@2x.png"))
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(path)], check=True)
    return True


def main() -> None:
    QApplication.instance() or QApplication([sys.argv[0]])
    assets = ROOT / "assets"
    icons_dir = assets / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)
    if LOGO.exists():
        emblem = load_emblem()

        def render(size: int) -> QImage:
            return render_brand_icon(size, emblem)
    else:

        def render(size: int) -> QImage:
            return generic_icon_pixmap(size).toImage()

    write_ico(assets / "icon.ico", [(size, png_bytes(render(size))) for size in ICO_SIZES])
    for size in PNG_SIZES:
        render(size).save(str(icons_dir / f"app-{size}.png"))
    render(512).save(str(assets / "icon.png"))
    mac = write_icns(assets / "icon.icns", render)
    print("Iconos generados en", assets, "(marca)" if LOGO.exists() else "(genérico)", "· con icon.icns" if mac else "")


if __name__ == "__main__":
    main()
