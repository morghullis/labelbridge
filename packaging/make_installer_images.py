"""Imágenes del instalador (Inno Setup) con la marca: el panel lateral de la bienvenida y del final (la máscara dorada
sobre el fondo oscuro de la app) y el logo pequeño de la esquina de las demás páginas.

Inno elige el tamaño según la escala de pantalla de Windows (100 % a 250 %), así que se genera cada uno. Sin textos:
sirven igual para el instalador en inglés y en español.

Uso: python packaging/make_installer_images.py   (la compilación lo ejecuta antes de Inno Setup)
Salida: build/installer/wizard-<escala>.bmp y build/installer/small-<escala>.bmp
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from labelbridge.app import _ensure_qt_plugin_path  # noqa: E402

_ensure_qt_plugin_path()

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QRadialGradient  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

OUT = ROOT / "build" / "installer"
EMBLEM = ROOT / "assets" / "mascot" / "emblem.png"
# (escala, panel lateral, logo pequeño): tamaños que pide Inno Setup 6 con WizardStyle=modern.
SIZES = (
    (100, (164, 314), (55, 58)),
    (125, (192, 386), (64, 68)),
    (150, (246, 459), (83, 80)),
    (175, (273, 556), (92, 97)),
    (200, (328, 628), (110, 106)),
    (225, (355, 700), (119, 123)),
    (250, (410, 797), (138, 140)),
)


def _emblem() -> QImage:
    image = QImage(str(EMBLEM))
    if image.isNull():
        raise FileNotFoundError(EMBLEM)
    return image


def wizard_image(width: int, height: int, emblem: QImage) -> QImage:
    """Panel lateral: fondo oscuro con un brillo dorado suave, la máscara arriba y el nombre debajo."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    background = QLinearGradient(0, 0, width * 0.35, height)
    background.setColorAt(0.0, QColor("#1A2236"))
    background.setColorAt(0.55, QColor("#0F1626"))
    background.setColorAt(1.0, QColor("#05070D"))
    p.fillRect(image.rect(), background)
    center = QPointF(width / 2, height * 0.36)
    glow = QRadialGradient(center, width * 0.62)
    glow.setColorAt(0.0, QColor(226, 176, 74, 70))
    glow.setColorAt(1.0, QColor(226, 176, 74, 0))
    p.fillRect(image.rect(), glow)
    mask_w = width * 0.74
    mask_h = mask_w * emblem.height() / emblem.width()
    p.drawImage(QRectF(center.x() - mask_w / 2, center.y() - mask_h / 2, mask_w, mask_h), emblem)
    line_y = height * 0.66
    p.setPen(QColor(226, 176, 74, 150))
    p.drawLine(QPointF(width * 0.3, line_y), QPointF(width * 0.7, line_y))
    font = QFont("Georgia")
    font.setPixelSize(max(12, round(width * 0.115)))
    font.setBold(True)
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 104)
    p.setFont(font)
    p.setPen(QColor("#F3D38A"))
    p.drawText(QRectF(0, line_y + height * 0.025, width, height * 0.1), Qt.AlignmentFlag.AlignHCenter, "LabelBridge")
    p.end()
    return image


def small_image(width: int, height: int, emblem: QImage) -> QImage:
    """Logo de la esquina: la máscara en un medallón oscuro con aro dorado, sobre blanco (fondo de esas páginas)."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#FFFFFF"))
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    side = min(width, height) * 0.96
    disc = QRectF((width - side) / 2, (height - side) / 2, side, side)
    fill = QRadialGradient(disc.center(), side / 2)
    fill.setColorAt(0.0, QColor("#243049"))
    fill.setColorAt(1.0, QColor("#05070D"))
    p.setBrush(fill)
    ring = QLinearGradient(disc.topLeft(), disc.bottomRight())
    ring.setColorAt(0.0, QColor("#FCEABB"))
    ring.setColorAt(0.5, QColor("#8E6522"))
    ring.setColorAt(1.0, QColor("#EBCB78"))
    pen = p.pen()
    pen.setBrush(ring)
    pen.setWidthF(max(1.5, side * 0.045))
    p.setPen(pen)
    p.drawEllipse(disc.adjusted(pen.widthF() / 2, pen.widthF() / 2, -pen.widthF() / 2, -pen.widthF() / 2))
    mask_w = side * 0.72
    mask_h = mask_w * emblem.height() / emblem.width()
    p.drawImage(QRectF(disc.center().x() - mask_w / 2, disc.center().y() - mask_h / 2 + side * 0.03, mask_w, mask_h), emblem)
    p.end()
    return image


def main() -> list[Path]:
    QApplication.instance() or QApplication([sys.argv[0]])
    OUT.mkdir(parents=True, exist_ok=True)
    emblem = _emblem()
    written = []
    for scale, (ww, wh), (sw, sh) in SIZES:
        for name, image in ((f"wizard-{scale}.bmp", wizard_image(ww, wh, emblem)), (f"small-{scale}.bmp", small_image(sw, sh, emblem))):
            path = OUT / name
            if not image.convertToFormat(QImage.Format.Format_RGB888).save(str(path), "BMP"):
                raise OSError(f"No se pudo guardar {path}")
            written.append(path)
    print(f"{len(written)} imágenes del instalador en {OUT}")
    return written


if __name__ == "__main__":
    main()
