"""Genera las imágenes que usa la hoja de estilos (assets/ui): hoy, la palomita de las casillas marcadas.

Qt dibuja la casilla con el estilo (fondo y borde) y pone encima esta imagen; con @2x se ve nítida en pantallas de
alta densidad. Correr de nuevo si cambia el diseño:  python devtools/theme_assets.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from labelbridge.app import _ensure_qt_plugin_path  # noqa: E402  (las carpetas con «;» confunden a Qt)

_ensure_qt_plugin_path()

from PySide6.QtCore import QByteArray, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

OUT = ROOT / "assets" / "ui"

CHECK = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" stroke="#FFFFFF" '
         'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 8.5 6.8 11.2 12 5.2"/></svg>')


def render(svg: str, size: int, path: Path) -> None:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    if not image.save(str(path)):
        raise OSError(f"No se pudo guardar {path}")


def main() -> int:
    QGuiApplication(sys.argv)
    OUT.mkdir(parents=True, exist_ok=True)
    render(CHECK, 16, OUT / "check.png")
    render(CHECK, 32, OUT / "check@2x.png")
    print(f"Listo: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
