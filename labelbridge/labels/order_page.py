"""«Página del pedido»: la hoja que sale con cada pedido, como «Imprimir página de pedido» del admin de Shopify.

Shopify no deja que las apps descarguen su impresión, así que LabelBridge la arma con los mismos datos (ver
connectors/shopify_orders.PAGE_QUERIES): el pedido y sus estados, a nombre de quién y cómo se entrega (envío o retiro en
tienda), los productos con su precio, los totales, lo pagado y las notas. Pensada para carta o A4; en otro papel se
dibuja a escala.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter

from ..connectors.shopify_orders import FINANCIAL_TEXT, FULFILLMENT_TEXT, OrderDetail, parse_detail, status_text
from ..i18n import date_text, tr
from ..printing.barcode import code128_widths
from .order import barcode_value

LETTER_MM = (215.9, 279.4)
FONT_FAMILY = "Arial"
INK = QColor("#111111")
MUTED = QColor("#555555")
LINE = QColor("#BBBBBB")
PHOTO_BG = QColor("#F2F2F2")
MARGIN = 14.0  # mm de la hoja carta
PHOTO = 22.0  # mm del cuadro de la foto de cada producto (se ve bien una joya)


def money(value: float, currency: str) -> str:
    return tr("US${0:,.2f}", value) if currency == "USD" else f"{value:,.2f} {currency}"


def page_detail(order: dict) -> OrderDetail:
    """El pedido guardado en el trabajo de impresión (tal como lo dio Shopify) → lo que lleva la página."""
    return parse_detail(order or {})


class _Page:
    """Escribe en «mm de la hoja carta»: en otro papel todo se dibuja a escala."""

    def __init__(self, painter: QPainter, rect: QRectF) -> None:
        self.p = painter
        self.u = rect.width() / LETTER_MM[0]  # píxeles por mm de la hoja
        self.x0 = rect.left() + MARGIN * self.u
        self.y0 = rect.top() + MARGIN * self.u
        self.width = rect.width() - 2 * MARGIN * self.u
        self.bottom = rect.bottom() - MARGIN * self.u

    def mm(self, value: float) -> float:
        return value * self.u

    def font(self, size_mm: float, bold: bool = False) -> QFont:
        font = QFont(FONT_FAMILY)
        font.setPixelSize(max(5, round(size_mm * self.u)))
        font.setBold(bold)
        return font

    def text(self, x: float, y: float, width: float, text: str, size_mm: float, *, bold: bool = False,
             color: QColor = INK, align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft, lines: int = 1) -> float:
        """Escribe (en `lines` renglones como mucho, con «…» si no cabe) y devuelve la y de abajo."""
        if not text:
            return y
        font = self.font(size_mm, bold)
        metrics = QFontMetricsF(font)
        self.p.setFont(font)
        self.p.setPen(color)
        line_h = metrics.lineSpacing()
        if lines == 1:
            shown = metrics.elidedText(text, Qt.TextElideMode.ElideRight, width)
            self.p.drawText(QRectF(x, y, width, line_h), align | Qt.AlignmentFlag.AlignVCenter, shown)
            return y + line_h
        bounds = metrics.boundingRect(QRectF(0, 0, width, 1e6), Qt.TextFlag.TextWordWrap, text)
        height = min(bounds.height(), line_h * lines)
        self.p.drawText(QRectF(x, y, width, height), align | Qt.TextFlag.TextWordWrap, text)
        return y + height

    def rule(self, y: float, color: QColor = LINE, thickness_mm: float = 0.3) -> float:
        self.p.fillRect(QRectF(self.x0, y, self.width, max(1.0, self.mm(thickness_mm))), color)
        return y + max(1.0, self.mm(thickness_mm))

    def photo(self, x: float, y: float, size: float, data: bytes) -> None:
        """La foto del producto, entera y centrada en su cuadro (gris si no hay foto o no se puede leer)."""
        self.p.fillRect(QRectF(x, y, size, size), PHOTO_BG)
        image = QImage.fromData(data) if data else QImage()
        if image.isNull():
            return
        scale = min(size / image.width(), size / image.height())
        w, h = image.width() * scale, image.height() * scale
        self.p.drawImage(QRectF(x + (size - w) / 2, y + (size - h) / 2, w, h), image)

    def barcode(self, x: float, y: float, width: float, height: float, value: str) -> None:
        widths = code128_widths(value)
        module = max(1.0, width / (sum(widths) + 20))
        cursor = x + 10 * module
        for index, w in enumerate(widths):
            if index % 2 == 0:
                self.p.fillRect(QRectF(cursor, y, w * module, height), INK)
            cursor += w * module


def draw_order_page(painter: QPainter, rect: QRectF, dpi: float, d: OrderDetail, store: str = "",
                    printed: datetime | None = None, basic: bool = False) -> None:
    """La página completa. `store`: el nombre de la tienda (arriba a la izquierda). `basic`: Shopify no dio la página
    completa (sin precios): se dice y no se muestran montos en cero."""
    del dpi  # todo se mide en mm de la hoja (ver _Page)
    page = _Page(painter, rect)
    painter.fillRect(rect, Qt.GlobalColor.white)
    x, w = page.x0, page.width
    cur = d.currency or "USD"
    items = [item for item in d.items if item.quantity > 0]  # lo quitado del pedido no se prepara

    # Encabezado: la tienda; el pedido, su fecha y sus estados.
    top = page.y0
    page.text(x, top, w * 0.5, store, 5.2, bold=True)
    y = page.text(x + w * 0.4, top, w * 0.6, tr("Pedido {0}", d.name), 7.0, bold=True,
                  align=Qt.AlignmentFlag.AlignRight)
    created = _when(d.created)
    if created:
        y = page.text(x + w * 0.4, y, w * 0.6, created, 3.4, color=MUTED, align=Qt.AlignmentFlag.AlignRight)
    states = [status_text(FINANCIAL_TEXT, d.financial) if d.financial else "",
              status_text(FULFILLMENT_TEXT, d.fulfillment) if d.fulfillment else "",
              tr("Cancelado") if d.cancelled else ""]
    y = page.text(x + w * 0.4, y + page.mm(1), w * 0.6, " · ".join(s for s in states if s), 3.8, bold=True,
                  align=Qt.AlignmentFlag.AlignRight)
    y = page.rule(y + page.mm(4), INK, 0.5) + page.mm(5)

    # Cliente y entrega (izquierda) · Pago (derecha).
    col = w * 0.58
    left = y
    heading = tr("Retiro en tienda") if d.pickup else (tr("Entrega") if basic else tr("Envío"))
    left = page.text(x, left, col, heading, 3.2, bold=True, color=MUTED) + page.mm(1)
    if d.pickup:
        left = page.text(x, left, col, d.buyer or d.customer, 4.4, bold=True)
        left = page.text(x, left, col, tr("Lo retira en: {0}", d.pickup), 3.6)
    else:
        for index, line in enumerate(d.address or [d.buyer]):
            left = page.text(x, left, col, line, 4.4 if index == 0 else 3.6, bold=index == 0)
        if d.method:
            left = page.text(x, left + page.mm(1), col, tr("Método: {0}", d.method), 3.4, color=MUTED)
    if d.phone:
        left = page.text(x, left, col, tr("Tel. {0}", d.phone), 3.4, color=MUTED)
    right = y
    px = x + col + page.mm(6)
    pw = w - col - page.mm(6)
    right = page.text(px, right, pw, tr("Pago"), 3.2, bold=True, color=MUTED) + page.mm(1)
    right = page.text(px, right, pw, status_text(FINANCIAL_TEXT, d.financial) if d.financial else "—", 4.4,
                      bold=True)
    if d.paid and not basic:
        right = page.text(px, right, pw, tr("Pagado: {0}", money(d.paid, cur)), 3.6)
    if d.due > 0 and not basic:
        right = page.text(px, right, pw, tr("Pendiente: {0}", money(d.due, cur)), 3.6, bold=True)
    y = max(left, right) + page.mm(6)

    # Lo de abajo se mide antes: los productos nunca tapan los totales, las notas ni el pie.
    notes = [(tr("Nota del cliente"), d.customer_note), (tr("Instrucciones"), d.instructions)]
    notes += [(key, value) for key, value in d.attributes[:4] if value]
    notes = [(title, body) for title, body in notes if body]
    foot = page.bottom - page.mm(14)
    notes_h = sum(page.mm(4.5) + page.mm(5.2) * min(3, 1 + len(body) // 95) + page.mm(2) for _t, body in notes)
    totals_h = page.mm(6 if basic else 42)
    items_bottom = foot - page.mm(6) - notes_h - totals_h

    # Productos (con su foto, como en la de Shopify; más chica si son muchos).
    qty_x, price_x, total_x = x + w * 0.60, x + w * 0.70, x + w * 0.84
    qty_w, price_w, total_w = w * 0.09, w * 0.13, w * 0.16
    page.text(x, y, w * 0.58, tr("Producto"), 3.2, bold=True, color=MUTED)
    page.text(qty_x, y, qty_w, tr("Cant."), 3.2, bold=True, color=MUTED, align=Qt.AlignmentFlag.AlignRight)
    if not basic:
        page.text(price_x, y, price_w, tr("Precio"), 3.2, bold=True, color=MUTED, align=Qt.AlignmentFlag.AlignRight)
        page.text(total_x, y, total_w, tr("Total"), 3.2, bold=True, color=MUTED, align=Qt.AlignmentFlag.AlignRight)
    y = page.text(x, y, 1, " ", 3.2)
    y = page.rule(y + page.mm(1.5)) + page.mm(2.5)
    photos = any(item.photo or item.image for item in items)
    compact = len(items) > 4  # muchos productos: filas de un renglón, para que entren más
    photo = PHOTO if not compact else PHOTO * 0.5
    text_x = x + page.mm(photo + 4) if photos else x
    text_w = w * 0.58 - (text_x - x)
    row_h = page.mm(max(photo if photos else 0, 8 if compact else 12) + 4.5)
    shown = 0
    for index, item in enumerate(items):
        more_after = index < len(items) - 1
        if y + row_h > items_bottom - (page.mm(6) if more_after else 0):
            break
        top = y
        if photos:
            page.photo(x, top, page.mm(photo), item.photo)
        y = page.text(text_x, y, text_w, item.title, 4.0, bold=True, lines=1 if compact else 2)
        details = [item.variant, tr("SKU {0}", item.sku) if item.sku else ""]
        if compact:  # la personalización (grabado, talla…) en el mismo renglón
            details += [f"{key}: {value}" for key, value in item.properties[:2]]
        y = page.text(text_x, y, text_w, " · ".join(part for part in details if part), 3.3, color=MUTED)
        for key, value in ([] if compact else item.properties[:2]):
            y = page.text(text_x, y, text_w, f"{key}: {value}", 3.3, color=MUTED)
        if photos:
            y = max(y, top + page.mm(photo))
        page.text(qty_x, top, qty_w, str(item.quantity), 4.0, align=Qt.AlignmentFlag.AlignRight)
        if not basic:
            page.text(price_x, top, price_w, money(item.price, cur), 3.8, align=Qt.AlignmentFlag.AlignRight)
            page.text(total_x, top, total_w, money(item.price * item.quantity, cur), 3.8, bold=True,
                      align=Qt.AlignmentFlag.AlignRight)
        y = page.rule(y + page.mm(2)) + page.mm(2.5)
        shown += 1
    if shown < len(items):
        y = page.text(x, y, w, tr("Y {0} producto(s) más: míralos en el pedido en Shopify.", len(items) - shown),
                      3.6, bold=True) + page.mm(2)

    # Totales, a la derecha (el subtotal es la suma de los productos: con el descuento debajo, la cuenta cierra).
    y += page.mm(1)
    if basic:
        y = page.text(x, y, w, tr("Datos básicos: Shopify no dio los precios a tiempo. Míralos en el pedido en Shopify."),
                      3.4, color=MUTED) + page.mm(2)
    else:
        gross = sum(item.price * item.quantity for item in items) or d.subtotal
        label_x, label_w = x + w * 0.52, w * 0.30
        rows = [(tr("Subtotal"), money(gross, cur), False)]
        if d.discounts:
            rows.append((tr("Descuentos"), "−" + money(d.discounts, cur), False))
        rows.append((tr("Envío"), money(d.shipping, cur), False))
        rows.append((tr("Impuestos"), money(d.taxes, cur), False))
        rows.append((tr("Total"), money(d.total, cur), True))
        if d.paid:
            rows.append((tr("Pagado por el cliente"), money(d.paid, cur), False))
        if d.due > 0:
            rows.append((tr("Falta pagar"), money(d.due, cur), True))
        for name, value, strong in rows:
            size = 4.4 if strong else 3.8
            page.text(label_x, y, label_w, name, size, bold=strong)
            y = page.text(total_x - w * 0.04, y, total_w + w * 0.04, value, size, bold=strong,
                          align=Qt.AlignmentFlag.AlignRight) + page.mm(0.8)

    # Notas del cliente, instrucciones y datos del carrito (mensaje de regalo…): su lugar ya está reservado.
    y += page.mm(4)
    for title, body in notes:
        if y + page.mm(9) > foot - page.mm(3):
            break
        y = page.text(x, y, w, title, 3.2, bold=True, color=MUTED)
        lines = max(1, min(3, int((foot - page.mm(3) - y) // page.mm(5.2))))
        y = page.text(x, y, w, body, 3.8, lines=lines) + page.mm(2)

    # Pie: código de barras del pedido (para buscarlo con un lector) y cuándo se imprimió.
    code = barcode_value(d.name, d.legacy)
    if code:
        page.barcode(x, foot, page.mm(70), page.mm(10), code)
    when = date_text(printed or datetime.now())
    page.text(x + w * 0.45, foot + page.mm(5), w * 0.55, tr("Impreso por LabelBridge · {0}", when), 2.8,
              color=MUTED, align=Qt.AlignmentFlag.AlignRight)


def _when(created: str) -> str:
    if not created:
        return ""
    try:
        return date_text(datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone())
    except ValueError:
        return created[:16]


def sample_page_order() -> dict:
    """Pedido de ejemplo para la impresión de prueba (retiro en tienda, como los que no llevan guía)."""
    return {
        "id": "gid://shopify/Order/1", "legacyResourceId": "1", "name": "#1001",
        "createdAt": datetime.now().astimezone().isoformat(), "displayFinancialStatus": "PAID",
        "displayFulfillmentStatus": "UNFULFILLED", "note": tr("Página de PRUEBA de LabelBridge."),
        "billingAddress": {"name": "María Fernanda López", "phone": "+1 305 555 0100"},
        "fulfillmentOrders": {"nodes": [{"deliveryMethod": {"methodType": "PICK_UP"},
                                         "assignedLocation": {"name": tr("Tu tienda")}}]},
        "subtotalPriceSet": {"shopMoney": {"amount": "520.00", "currencyCode": "USD"}},
        "totalShippingPriceSet": {"shopMoney": {"amount": "0.00"}},
        "totalTaxSet": {"shopMoney": {"amount": "36.40"}},
        "totalDiscountsSet": {"shopMoney": {"amount": "0.00"}},
        "totalPriceSet": {"shopMoney": {"amount": "556.40", "currencyCode": "USD"}},
        "totalReceivedSet": {"shopMoney": {"amount": "556.40"}},
        "totalOutstandingSet": {"shopMoney": {"amount": "0.00"}},
        "lineItems": {"nodes": [{"title": tr("Anillo de ejemplo"), "variantTitle": "8", "sku": "ANI-008", "quantity": 2,
                                 "currentQuantity": 2, "originalUnitPriceSet": {"shopMoney": {"amount": "260.00"}}}]},
    }
