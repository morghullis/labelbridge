"""Genera assets/icon.ico (multi-tamaño) y assets/icon.png a partir del icono vectorial de la app."""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from labelbridge.app import _ensure_qt_plugin_path  # noqa: E402

_ensure_qt_plugin_path()

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from labelbridge.ui.icons import app_icon_pixmap  # noqa: E402

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def png_bytes(size: int) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    app_icon_pixmap(size).save(buffer, "PNG")
    return bytes(data)


def main() -> None:
    QApplication.instance() or QApplication([sys.argv[0]])
    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    images = [png_bytes(size) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, payload = b"", b""
    for size, image in zip(SIZES, images):
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(image), offset + len(payload))
        payload += image
    (assets / "icon.ico").write_bytes(header + entries + payload)
    (assets / "icon.png").write_bytes(png_bytes(512))
    print("Iconos generados en", assets)


if __name__ == "__main__":
    main()
