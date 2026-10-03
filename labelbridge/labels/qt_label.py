"""Dibuja la etiqueta de pedido con QPainter (impresora con driver, PDF, vista previa, ZPL raster)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFont, QFontMetricsF, QPainter

from ..printing.barcode import code128_widths
from .order import OrderLabelData

FONT_FAMILY = "Arial"
BLACK = Qt.GlobalColor.black


class _Canvas:
    def __init__(self, painter: QPainter, rect: QRectF, dpi: float) -> None:
        self.p = painter
        self.mm = dpi / 25.4
        self.left = rect.left() + 3 * self.mm
        self.top = rect.top() + 3 * self.mm
        self.width = rect.width() - 6 * self.mm
        self.bottom = rect.bottom() - 3 * self.mm

    def font(self, size_mm: float, bold: bool = False) -> QFont:
        font = QFont(FONT_FAMILY)
        font.setPixelSize(max(6, round(size_mm * self.mm)))
        font.setBold(bold)
        return font

    def text(
        self,
        y: float,
        text: str,
        size_mm: float,
        *,
        bold: bool = False,
        max_lines: int = 1,
        x: float | None = None,
        width: float | None = None,
        align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft,
        shrink: bool = True,
    ) -> float:
        """Escribe texto (reduce la letra si no cabe) y devuelve la nueva coordenada y."""
        if not text:
            return y
        x = self.left if x is None else x
        width = self.width if width is None else width
        size = size_mm
        while True:
            font = self.font(size, bold)
            metrics = QFontMetricsF(font)
            if max_lines == 1:
                fits = metrics.horizontalAdvance(text) <= width
            else:
                bounds = metrics.boundingRect(QRectF(0, 0, width, 1e6), Qt.TextFlag.TextWordWrap, text)
                fits = bounds.height() <= metrics.lineSpacing() * max_lines + 1
            if fits or not shrink or size <= size_mm * 0.62:
                break
            size *= 0.92
        self.p.setFont(font)
        line_h = metrics.lineSpacing()
        if max_lines == 1:
            shown = metrics.elidedText(text, Qt.TextElideMode.ElideRight, width)
            self.p.drawText(QRectF(x, y, width, line_h), align | Qt.AlignmentFlag.AlignVCenter, shown)
            return y + line_h
        bounds = metrics.boundingRect(QRectF(0, 0, width, 1e6), Qt.TextFlag.TextWordWrap, text)
        height = min(bounds.height(), line_h * max_lines)
        self.p.drawText(QRectF(x, y, width, height), align | Qt.TextFlag.TextWordWrap, text)
        return y + height

    def rule(self, y: float, thickness_mm: float = 0.5) -> float:
        thickness = max(1.0, round(thickness_mm * self.mm))
        self.p.fillRect(QRectF(self.left, round(y), self.width, thickness), BLACK)
        return y + thickness

    def barcode(self, x: float, y: float, width: float, height: float, value: str) -> float:
        widths = code128_widths(value)
        modules = sum(widths) + 20  # zona de silencio de 10 módulos por lado
        module = max(1, int(width // modules))
        total = module * sum(widths)
        cursor = round(x + (width - total) / 2)
        top = round(y)
        bar_h = round(height)
        for index, w in enumerate(widths):
            if index % 2 == 0:
                self.p.fillRect(cursor, top, w * module, bar_h, BLACK)
            cursor += w * module
        return y + height

    def qr(self, x: float, y: float, size: float, data: str) -> bool:
        try:
            import segno
        except ImportError:
            return False
        try:
            matrix = [list(row) for row in segno.make(data, error="m", micro=False).matrix_iter(scale=1, border=0)]
        except Exception:
            return False
        cell = max(1, int(size // len(matrix)))
        ox, oy = round(x + (size - cell * len(matrix)) / 2), round(y)
        for r, row in enumerate(matrix):
            for c, value in enumerate(row):
                if value:
                    self.p.fillRect(ox + c * cell, oy + r * cell, cell, cell, BLACK)
        return True


def draw_order_label(painter: QPainter, rect: QRectF, dpi: float, d: OrderLabelData, show_items: bool = True) -> None:
    painter.save()
    painter.fillRect(rect, Qt.GlobalColor.white)
    painter.setPen(BLACK)
    c = _Canvas(painter, rect, dpi)
    mm = c.mm
    compact = rect.height() < 70 * mm or rect.width() < 70 * mm
    if compact:
        _draw_compact(c, d)
    else:
        _draw_full(c, d, show_items)
    painter.restore()


def _draw_compact(c: _Canvas, d: OrderLabelData) -> None:
    mm = c.mm
    y = c.top
    y = c.text(y, d.order_name, 5.5, bold=True)
    y = c.text(y, d.ship_name, 3.4, bold=True)
    city = d.address_lines[-2] if len(d.address_lines) >= 2 else ""
    y = c.text(y, city, 2.8)
    remaining = c.bottom - y - 1 * mm
    if d.barcode and remaining > 6 * mm:
        c.barcode(c.left, y + 1 * mm, c.width, min(remaining, 12 * mm), d.barcode)


def _draw_full(c: _Canvas, d: OrderLabelData, show_items: bool) -> None:
    mm = c.mm
    y = c.top

    # Remitente + fecha
    if d.sender_name or d.sender_lines:
        c.text(y, d.created, 2.6, x=c.left + c.width * 0.55, width=c.width * 0.45, align=Qt.AlignmentFlag.AlignRight)
        y = c.text(y, d.sender_name or "Remitente", 3.2, bold=True, width=c.width * 0.58)
        for line in d.sender_lines[:3]:
            y = c.text(y, line, 2.6)
    else:
        y = c.text(y, d.created, 2.6, align=Qt.AlignmentFlag.AlignRight)
    y = c.rule(y + 1.2 * mm, 0.6) + 1.5 * mm

    # Destinatario
    y = c.text(y, "ENVIAR A:", 2.6, bold=True)
    y = c.text(y, d.ship_name or "(sin nombre)", 6.2, bold=True, max_lines=2)
    if d.company:
        y = c.text(y, d.company, 3.8, bold=True)
    for line in d.address_lines:
        y = c.text(y, line, 4.3, max_lines=2)
    if d.phone:
        y = c.text(y + 0.5 * mm, f"Tel. {d.phone}", 3.4)
    y = c.rule(y + 1.5 * mm, 0.6) + 1.5 * mm

    # Pedido + código de barras + QR
    qr_size = 21 * mm if d.qr_url else 0
    left_w = c.width - (qr_size + 3 * mm if qr_size else 0)
    block_top = y
    c.text(y, "PEDIDO", 2.6, bold=True, width=left_w)
    if d.shipping_method:
        c.text(y, d.shipping_method, 2.8, x=c.left + left_w * 0.4, width=left_w * 0.6, align=Qt.AlignmentFlag.AlignRight)
    y = c.text(y + 2.6 * mm, d.order_name, 8.0, bold=True, width=left_w)
    if d.barcode:
        y = c.barcode(c.left, y + 1 * mm, left_w, 12 * mm, d.barcode)
        y = c.text(y + 0.5 * mm, d.barcode, 2.8, width=left_w, align=Qt.AlignmentFlag.AlignHCenter)
    if qr_size:
        c.qr(c.left + c.width - qr_size, block_top + 1 * mm, qr_size, d.qr_url)
        y = max(y, block_top + qr_size + 2 * mm)
    y = c.rule(y + 1.5 * mm, 0.6) + 1.5 * mm

    # Productos (lo que quepa) y nota
    note_h = 0.0
    if d.note:
        note_h = 9 * mm
    footer_h = 4 * mm if d.total else 0
    if show_items and d.items:
        y = c.text(y, f"PRODUCTOS ({d.item_count})", 2.8, bold=True)
        line_h = QFontMetricsF(c.font(3.3)).lineSpacing()
        space = c.bottom - note_h - footer_h - y
        max_rows = max(0, int(space // line_h))
        rows = d.items if len(d.items) <= max_rows else d.items[: max(0, max_rows - 1)]
        for qty, text in rows:
            y = c.text(y, f"{qty} × {text}", 3.3, shrink=False)
        hidden = len(d.items) - len(rows)
        if hidden > 0:
            y = c.text(y, f"+ {hidden} producto(s) más", 3.0, bold=True, shrink=False)
    if d.note:
        c.text(max(y + 1 * mm, c.bottom - note_h - footer_h), f"Nota: {d.note}", 2.9, max_lines=2)
    if d.total:
        c.text(c.bottom - footer_h, f"Total: {d.total}", 3.0, bold=True, align=Qt.AlignmentFlag.AlignRight)
