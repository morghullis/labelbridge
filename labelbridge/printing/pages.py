"""Fuentes de páginas (PDF, imagen, etiqueta generada) y ajuste a la etiqueta física.

Todo trabaja en píxeles del dispositivo: el llamador indica tamaño destino y DPI.
"""

from __future__ import annotations

import os
import tempfile
from typing import Callable

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QImage, QPainter, QTransform
from PySide6.QtPdf import QPdfDocument, QPdfDocumentRenderOptions

from . import PermanentPrintError

MM_PER_INCH = 25.4
PT_PER_MM = 72.0 / MM_PER_INCH
MAX_RENDER_SIDE = 4000  # px; evita consumir cientos de MB con páginas enormes


def flatten(image: QImage) -> QImage:
    """Imagen opaca sobre blanco (las transparencias no deben imprimirse negras)."""
    out = QImage(image.size(), QImage.Format.Format_RGB32)
    out.fill(Qt.GlobalColor.white)
    painter = QPainter(out)
    painter.drawImage(0, 0, image)
    painter.end()
    return out


class PageSource:
    vector = False

    def count(self) -> int:
        return 1

    def size_mm(self, index: int) -> tuple[float, float] | None:
        return None

    def pixel_size(self, index: int) -> tuple[int, int]:
        raise NotImplementedError

    def render(self, index: int, width: int, height: int, crisp: bool) -> QImage:
        raise NotImplementedError

    def paint(self, painter: QPainter, rect: QRectF, dpi: float) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class PdfSource(PageSource):
    def __init__(self, data: bytes) -> None:
        fd, self._path = tempfile.mkstemp(suffix=".pdf", prefix="labelbridge-")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        self._doc = QPdfDocument(None)
        error = self._doc.load(self._path)
        if error != QPdfDocument.Error.None_:
            self.close()
            reason = {
                QPdfDocument.Error.IncorrectPassword: "está protegido con contraseña",
                QPdfDocument.Error.UnsupportedSecurityScheme: "usa un cifrado no soportado",
            }.get(error, f"no es válido ({error.name})")
            raise PermanentPrintError(f"El PDF {reason}")
        if self._doc.pageCount() < 1:
            self.close()
            raise PermanentPrintError("El PDF no tiene páginas")

    def count(self) -> int:
        return self._doc.pageCount()

    def size_mm(self, index: int) -> tuple[float, float]:
        size = self._doc.pagePointSize(index)
        return size.width() / PT_PER_MM, size.height() / PT_PER_MM

    def pixel_size(self, index: int) -> tuple[int, int]:
        w, h = self.size_mm(index)
        return round(w / MM_PER_INCH * 72), round(h / MM_PER_INCH * 72)

    def render(self, index: int, width: int, height: int, crisp: bool) -> QImage:
        flags = QPdfDocumentRenderOptions.RenderFlag.Annotations
        if crisp:
            flags |= (
                QPdfDocumentRenderOptions.RenderFlag.TextAliased
                | QPdfDocumentRenderOptions.RenderFlag.ImageAliased
                | QPdfDocumentRenderOptions.RenderFlag.PathAliased
            )
        options = QPdfDocumentRenderOptions()
        options.setRenderFlags(flags)
        image = self._doc.render(index, QSize(max(1, width), max(1, height)), options)
        if image.isNull():
            raise PermanentPrintError(f"No se pudo dibujar la página {index + 1} del PDF")
        return flatten(image)

    def close(self) -> None:
        doc = getattr(self, "_doc", None)
        if doc is not None:
            doc.close()
            self._doc = None  # type: ignore[assignment]
        try:
            os.unlink(self._path)
        except OSError:
            pass


class ImageSource(PageSource):
    def __init__(self, data: bytes) -> None:
        image = QImage.fromData(data)
        if image.isNull():
            raise PermanentPrintError("La imagen no es válida o su formato no está soportado")
        self._image = flatten(image)

    def pixel_size(self, index: int) -> tuple[int, int]:
        return self._image.width(), self._image.height()

    def render(self, index: int, width: int, height: int, crisp: bool) -> QImage:
        if (width, height) == (self._image.width(), self._image.height()):
            return self._image
        mode = Qt.TransformationMode.FastTransformation if crisp else Qt.TransformationMode.SmoothTransformation
        return self._image.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, mode)


class VectorSource(PageSource):
    """Página dibujada con QPainter (etiqueta de pedido). Tamaño físico fijo."""

    vector = True

    def __init__(self, size_mm: tuple[float, float], painter_fn: Callable[[QPainter, QRectF, float], None]) -> None:
        self._size_mm = size_mm
        self._painter_fn = painter_fn

    def size_mm(self, index: int) -> tuple[float, float]:
        return self._size_mm

    def pixel_size(self, index: int) -> tuple[int, int]:
        return round(self._size_mm[0] / MM_PER_INCH * 203), round(self._size_mm[1] / MM_PER_INCH * 203)

    def paint(self, painter: QPainter, rect: QRectF, dpi: float) -> None:
        self._painter_fn(painter, rect, dpi)

    def render(self, index: int, width: int, height: int, crisp: bool) -> QImage:
        image = QImage(max(1, width), max(1, height), QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        dpi = width / (self._size_mm[0] / MM_PER_INCH)
        image.setDotsPerMeterX(round(dpi / 0.0254))
        image.setDotsPerMeterY(round(dpi / 0.0254))
        painter = QPainter(image)
        self.paint(painter, QRectF(0, 0, width, height), dpi)
        painter.end()
        return image


# --- ajuste a la etiqueta ----------------------------------------------------------

def decide_rotation(src_w: float, src_h: float, dst_w: float, dst_h: float, mode: str) -> int:
    if mode in ("90", "180", "270"):
        return int(mode)
    if mode == "0":
        return 0
    src_landscape = src_w > src_h * 1.02
    dst_landscape = dst_w > dst_h * 1.02
    src_portrait = src_h > src_w * 1.02
    dst_portrait = dst_h > dst_w * 1.02
    if (src_landscape and dst_portrait) or (src_portrait and dst_landscape):
        return 90
    return 0


def content_bbox(image: QImage, threshold: int = 245) -> QRect | None:
    """Rectángulo con contenido no blanco (para recortar etiquetas dentro de una hoja carta/A4)."""
    gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
    width, height, bpl = gray.width(), gray.height(), gray.bytesPerLine()
    raw = bytes(gray.constBits())
    table = bytes(1 if v < threshold else 0 for v in range(256))
    top = bottom = None
    left, right = width, -1
    for y in range(height):
        row = raw[y * bpl : y * bpl + width].translate(table)
        first = row.find(1)
        if first < 0:
            continue
        if top is None:
            top = y
        bottom = y
        left = min(left, first)
        right = max(right, row.rfind(1))
    if top is None or bottom is None:
        return None
    return QRect(left, top, right - left + 1, bottom - top + 1)


def render_fitted(
    source: PageSource,
    index: int,
    target_w: int,
    target_h: int,
    dpi: float,
    rotate: str = "auto",
    crop: str = "auto",
    crisp: bool = True,
    target_mm: tuple[float, float] | None = None,
) -> QImage:
    """Dibuja la página centrada y escalada (manteniendo proporción) en un lienzo del tamaño destino.

    Si la página es mucho más grande que la etiqueta (p. ej. una etiqueta 4x6 dentro de una hoja
    carta), se recorta automáticamente al área con contenido.
    """
    canvas = QImage(max(1, target_w), max(1, target_h), QImage.Format.Format_RGB32)
    canvas.fill(Qt.GlobalColor.white)

    size_mm = source.size_mm(index)
    page_w, page_h = size_mm if size_mm else source.pixel_size(index)

    # Zona de la página a imprimir, en fracciones (x, y, ancho, alto).
    fx, fy, fw, fh = 0.0, 0.0, 1.0, 1.0
    if crop == "auto" and size_mm and target_mm and page_w * page_h > 1.6 * target_mm[0] * target_mm[1]:
        probe_ppm = 100 / MM_PER_INCH
        probe_w, probe_h = max(1, round(page_w * probe_ppm)), max(1, round(page_h * probe_ppm))
        box = content_bbox(source.render(index, probe_w, probe_h, crisp))
        if box is not None:
            pad = round(1.5 * probe_ppm)
            box = box.adjusted(-pad, -pad, pad, pad).intersected(QRect(0, 0, probe_w, probe_h))
            fx, fy = box.x() / probe_w, box.y() / probe_h
            fw, fh = box.width() / probe_w, box.height() / probe_h

    region_w, region_h = page_w * fw, page_h * fh
    angle = decide_rotation(region_w, region_h, target_w, target_h, rotate)
    rot_w, rot_h = (region_h, region_w) if angle in (90, 270) else (region_w, region_h)
    scale = min(target_w / rot_w, target_h / rot_h)
    out_w, out_h = max(1, round(rot_w * scale)), max(1, round(rot_h * scale))
    pre_w, pre_h = (out_h, out_w) if angle in (90, 270) else (out_w, out_h)

    # Página completa a la escala en la que la zona elegida mide exactamente pre_w x pre_h.
    full_w, full_h = max(1, round(pre_w / fw)), max(1, round(pre_h / fh))
    limit = MAX_RENDER_SIDE * 2
    if max(full_w, full_h) > limit:
        factor = limit / max(full_w, full_h)
        full_w, full_h = max(1, round(full_w * factor)), max(1, round(full_h * factor))
    full = source.render(index, full_w, full_h, crisp)
    if (fx, fy, fw, fh) != (0.0, 0.0, 1.0, 1.0):
        region = QRect(round(fx * full_w), round(fy * full_h), round(fw * full_w), round(fh * full_h))
        image = full.copy(region.intersected(full.rect()))
    else:
        image = full
    if (image.width(), image.height()) != (pre_w, pre_h):
        mode = Qt.TransformationMode.FastTransformation if crisp else Qt.TransformationMode.SmoothTransformation
        image = image.scaled(pre_w, pre_h, Qt.AspectRatioMode.IgnoreAspectRatio, mode)

    if angle:
        image = image.transformed(QTransform().rotate(angle))
    painter = QPainter(canvas)
    painter.drawImage((target_w - image.width()) // 2, (target_h - image.height()) // 2, image)
    painter.end()
    return canvas
