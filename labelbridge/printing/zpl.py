"""Utilidades ZPL: escape de texto y conversión de imágenes a ^GFA (con compresión ACS)."""

from __future__ import annotations

from PySide6.QtGui import QImage


def zpl_text(text: str) -> str:
    """Escapa texto para usar con ^FH_ (los caracteres de control de ZPL van en hex)."""
    clean = " ".join(str(text).split())  # sin saltos de línea ni tabs
    return clean.replace("_", "_5F").replace("^", "_5E").replace("~", "_7E")


def _repeat_prefix(count: int) -> str:
    """Prefijo de repetición ZPL: G..Y = 1..19, g..z = 20..400 (múltiplos de 20)."""
    prefix = ""
    if count >= 20:
        prefix += chr(ord("g") + count // 20 - 1)
        count %= 20
    if count:
        prefix += chr(ord("G") + count - 1)
    return prefix


def _compress_row(hex_row: str) -> str:
    body = hex_row.rstrip("0")
    if not body:
        return ","
    out: list[str] = []
    i, n = 0, len(body)
    while i < n:
        ch = body[i]
        j = i
        while j < n and body[j] == ch:
            j += 1
        run = j - i
        while run > 0:
            chunk = min(run, 400)
            out.append(ch if chunk == 1 else _repeat_prefix(chunk) + ch)
            run -= chunk
        i = j
    if len(body) < len(hex_row):
        out.append(",")
    return "".join(out)


def image_to_gfa(image: QImage, threshold: int = 140, compress: bool = True) -> str:
    """Convierte una imagen (ya del tamaño final en puntos) en un comando ^GFA.

    Negro = punto impreso. El umbral es alto para que el gris claro no imprima.
    """
    gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
    width, height = gray.width(), gray.height()
    bpl = gray.bytesPerLine()
    raw = bytes(gray.constBits())
    table = bytes(0x31 if v < threshold else 0x30 for v in range(256))  # '1' oscuro / '0' claro
    row_bytes = (width + 7) // 8
    pad = b"0" * (row_bytes * 8 - width)
    rows: list[str] = []
    for y in range(height):
        start = y * bpl
        bits = raw[start : start + width].translate(table) + pad
        rows.append(int(bits, 2).to_bytes(row_bytes, "big").hex().upper())
    total = row_bytes * height
    if compress:
        parts: list[str] = []
        previous = None
        for row in rows:
            parts.append(":" if row == previous else _compress_row(row))
            previous = row
        data = "".join(parts)
    else:
        data = "".join(rows)
    return f"^GFA,{total},{total},{row_bytes},{data}^FS"


def raster_label(images: list[QImage], copies: int = 1) -> bytes:
    """Una etiqueta ZPL por imagen. Las imágenes deben venir al tamaño exacto de la etiqueta."""
    out = []
    for image in images:
        out.append(
            "^XA^LH0,0^PON^PW{w}^LL{h}^FO0,0{gfa}^PQ{copies}^XZ".format(
                w=image.width(), h=image.height(), gfa=image_to_gfa(image), copies=max(1, copies)
            )
        )
    return "\n".join(out).encode("ascii")
