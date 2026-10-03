"""Etiqueta de pedido en ZPL nativo: más nítida y rápida en impresoras Zebra y compatibles."""

from __future__ import annotations

from ..printing.barcode import code128_widths
from ..printing.zpl import zpl_text
from .order import OrderLabelData

# Fuente 0 (CG Triumvirate condensada): ancho medio aprox. 0.55 × altura.
_CHAR_WIDTH = 0.55


class _Zpl:
    def __init__(self, width_mm: float, height_mm: float, dpi: int) -> None:
        self.dpmm = dpi / 25.4
        self.W = round(width_mm * self.dpmm)
        self.H = round(height_mm * self.dpmm)
        self.M = self.dots(3)
        self.IW = self.W - 2 * self.M
        self.out = ["^XA", "^CI28", f"^PW{self.W}", f"^LL{self.H}", "^LH0,0", "^PON"]

    def dots(self, mm: float) -> int:
        return max(1, round(mm * self.dpmm))

    def text(
        self,
        y: int,
        text: str,
        size_mm: float,
        *,
        lines: int = 1,
        x: int | None = None,
        width: int | None = None,
        align: str = "L",
    ) -> int:
        if not text:
            return y
        x = self.M if x is None else x
        width = self.IW if width is None else width
        height = self.dots(size_mm)
        per_line = max(4, int(width / (height * _CHAR_WIDTH)))
        limit = per_line * lines
        clean = " ".join(text.split())
        if len(clean) > limit:
            clean = clean[: limit - 3].rstrip() + "..."
        self.out.append(f"^FO{x},{y}^A0N,{height},{height}^FB{width},{lines},0,{align},0^FH_^FD{zpl_text(clean)}^FS")
        used_lines = min(lines, max(1, -(-len(clean) // per_line)))
        return y + height * used_lines + self.dots(0.8)

    def rule(self, y: int, thickness_mm: float = 0.6) -> int:
        t = self.dots(thickness_mm)
        self.out.append(f"^FO{self.M},{y}^GB{self.IW},{t},{t}^FS")
        return y + t

    def barcode(self, x: int, y: int, width: int, height: int, value: str) -> int:
        widths = code128_widths(value)
        module = max(1, min(4, width // (sum(widths) + 20)))
        start = ">;" if value.isdigit() and len(value) % 2 == 0 else ">:"
        used = module * sum(widths)
        x += max(0, (width - used) // 2)
        self.out.append(f"^FO{x},{y}^BY{module},3,{height}^BCN,{height},N,N,N,N^FD{start}{value}^FS")
        return y + height

    def qr(self, x: int, y: int, size: int, data: str) -> None:
        modules = 37
        try:
            import segno

            modules = segno.make(data, error="m", micro=False).symbol_size(scale=1, border=0)[0]
        except Exception:
            pass
        magnification = max(1, min(10, size // modules))
        self.out.append(f"^FO{x},{y}^BQN,2,{magnification}^FDMA,{data}^FS")

    def finish(self, copies: int = 1) -> bytes:
        self.out.append(f"^PQ{max(1, copies)}")
        self.out.append("^XZ")
        return "\n".join(self.out).encode("utf-8")


def order_to_zpl(
    d: OrderLabelData,
    media_mm: tuple[float, float] = (101.6, 152.4),
    dpi: int = 203,
    show_items: bool = True,
    copies: int = 1,
) -> bytes:
    z = _Zpl(media_mm[0], media_mm[1], dpi)
    if media_mm[0] < 70 or media_mm[1] < 70:
        y = z.M
        y = z.text(y, d.order_name, 5.5)
        y = z.text(y, d.ship_name, 3.4)
        if len(d.address_lines) >= 2:
            y = z.text(y, d.address_lines[-2], 2.8)
        if d.barcode and z.H - z.M - y > z.dots(6):
            z.barcode(z.M, y + z.dots(1), z.IW, min(z.H - z.M - y - z.dots(1), z.dots(12)), d.barcode)
        return z.finish(copies)

    y = z.M
    if d.sender_name or d.sender_lines:
        z.text(y, d.created, 2.6, x=z.M + z.IW // 2, width=z.IW // 2, align="R")
        y = z.text(y, d.sender_name or "Remitente", 3.2, width=int(z.IW * 0.58))
        for line in d.sender_lines[:3]:
            y = z.text(y, line, 2.6)
    else:
        y = z.text(y, d.created, 2.6, align="R")
    y = z.rule(y + z.dots(1)) + z.dots(1.5)

    y = z.text(y, "ENVIAR A:", 2.6)
    y = z.text(y, d.ship_name or "(sin nombre)", 6.2, lines=2)
    if d.company:
        y = z.text(y, d.company, 3.8)
    for line in d.address_lines:
        y = z.text(y, line, 4.3, lines=2)
    if d.phone:
        y = z.text(y + z.dots(0.5), f"Tel. {d.phone}", 3.4)
    y = z.rule(y + z.dots(1.5)) + z.dots(1.5)

    qr_size = z.dots(21) if d.qr_url else 0
    left_w = z.IW - (qr_size + z.dots(3) if qr_size else 0)
    block_top = y
    z.text(y, "PEDIDO", 2.6, width=left_w)
    if d.shipping_method:
        z.text(y, d.shipping_method, 2.8, x=z.M + int(left_w * 0.4), width=int(left_w * 0.6), align="R")
    y = z.text(y + z.dots(2.6), d.order_name, 8.0, width=left_w)
    if d.barcode:
        y = z.barcode(z.M, y + z.dots(1), left_w, z.dots(12), d.barcode)
        y = z.text(y + z.dots(0.8), d.barcode, 2.8, width=left_w, align="C")
    if qr_size:
        z.qr(z.M + z.IW - qr_size, block_top, qr_size, d.qr_url)
        y = max(y, block_top + qr_size + z.dots(2))
    y = z.rule(y + z.dots(1.5)) + z.dots(1.5)

    bottom = z.H - z.M
    note_h = z.dots(9) if d.note else 0
    footer_h = z.dots(4) if d.total else 0
    if show_items and d.items:
        y = z.text(y, f"PRODUCTOS ({d.item_count})", 2.8)
        row_h = z.dots(3.3) + z.dots(0.8)
        max_rows = max(0, (bottom - note_h - footer_h - y) // row_h)
        rows = d.items if len(d.items) <= max_rows else d.items[: max(0, max_rows - 1)]
        for qty, text in rows:
            y = z.text(y, f"{qty} x {text}", 3.3)
        hidden = len(d.items) - len(rows)
        if hidden > 0:
            y = z.text(y, f"+ {hidden} producto(s) más", 3.0)
    if d.note:
        z.text(max(y + z.dots(1), bottom - note_h - footer_h), f"Nota: {d.note}", 2.9, lines=2)
    if d.total:
        z.text(bottom - footer_h, f"Total: {d.total}", 3.0, align="R")
    return z.finish(copies)
