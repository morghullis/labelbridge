"""Página del pedido: la hoja como «Imprimir página de pedido» de Shopify, con cada pedido nuevo pagado (también los
que no llevan guía, como un retiro en tienda), por su impresora y nunca por la de las guías."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from test_connectors import FakeShopify
from test_orders import _service, _ui_context, iso

from labelbridge import net
from labelbridge.config import PrinterProfile
from labelbridge.connectors.shopify_orders import parse_detail
from labelbridge.labels.order_page import sample_page_order
from labelbridge.models import parse_job

PICKUP = {  # como el #1469# de la tienda: retiro en tienda, sin dirección de envío
    "id": "gid://shopify/Order/3001", "legacyResourceId": "3001", "name": "#3001",
    "createdAt": "2026-10-11T01:22:01Z", "displayFinancialStatus": "PAID", "displayFulfillmentStatus": "UNFULFILLED",
    "note": None, "tags": [], "shippingAddress": None, "shippingLine": {"title": "11865 SW 26 ST, C-41"},
    "billingAddress": {"name": "Omar Bosch", "phone": None},
    "fulfillmentOrders": {"nodes": [{"deliveryMethod": {"methodType": "PICK_UP"},
                                     "assignedLocation": {"name": "11865 SW 26 ST, C-41"}}]},
    "subtotalPriceSet": {"shopMoney": {"amount": "520.0", "currencyCode": "USD"}},
    "totalShippingPriceSet": {"shopMoney": {"amount": "0.0"}}, "totalTaxSet": {"shopMoney": {"amount": "36.4"}},
    "totalDiscountsSet": {"shopMoney": {"amount": "0.0"}},
    "totalPriceSet": {"shopMoney": {"amount": "556.4", "currencyCode": "USD"}},
    "totalReceivedSet": {"shopMoney": {"amount": "556.4"}}, "totalOutstandingSet": {"shopMoney": {"amount": "0.0"}},
    "lineItems": {"nodes": [{"title": "Idde de Yemaya", "variantTitle": "8", "sku": "IDD-045-8", "quantity": 2,
                             "currentQuantity": 2, "originalUnitPriceSet": {"shopMoney": {"amount": "260.0"}}}]},
}


class StoreWithPickup(FakeShopify):
    """El pedido 3001 se retira en la tienda (sin dirección de envío); Shopify da su página completa."""

    def __init__(self, now_iso: str, page: bool = True) -> None:
        super().__init__(now_iso)
        self.page = page
        self.page_requests = 0

    def order(self, number: int, created: str, **extra) -> dict:
        if number == 3001:
            extra.setdefault("shippingAddress", None)
            extra.setdefault("shippingLine", {"title": "11865 SW 26 ST, C-41"})
        return super().order(number, created, **extra)

    def __call__(self, method, url, **kwargs):
        body = kwargs.get("json_body") or {}
        if "LabelBridgeOrderPage" in body.get("query", ""):
            self.page_requests += 1
            data = {"order": {**PICKUP, "createdAt": self.now_iso}} if self.page else {}
            return net.Response(200, json.dumps({"data": data}).encode(), url)
        return super().__call__(method, url, **kwargs)


def _printers(core):
    office = PrinterProfile(alias="oficina", kind="file", output_dir=str(core.home / "oficina"), language="driver",
                            media="letter")
    labels = PrinterProfile(alias="etiquetas", kind="file", output_dir=str(core.home / "etiquetas"), language="zpl")
    core.cfg.data.printers += [office, labels]
    s = core.cfg.data.shopify
    s.shipping_label_printer = "etiquetas"
    s.print_order_page = True
    return office, labels


def _pages(core) -> list[dict]:
    return [job for job in core.store.list() if job["format"] == "order_page"]


# --- lo que lleva ---------------------------------------------------------------------------------

def test_the_page_has_what_shopify_prints_even_for_a_pickup_order():
    d = parse_detail(PICKUP)
    assert (d.name, d.pickup, d.buyer, d.address) == ("#3001", "11865 SW 26 ST, C-41", "Omar Bosch", [])
    assert (d.subtotal, d.taxes, d.total, d.paid, d.due) == (520.0, 36.4, 556.4, 556.4, 0.0)
    item = d.items[0]
    assert (item.title, item.variant, item.sku, item.quantity, item.price) == ("Idde de Yemaya", "8", "IDD-045-8", 2,
                                                                              260.0)
    # Con lo básico del ciclo (si Shopify no dio la página), los productos salen con su nombre.
    basic = parse_detail({"name": "#9", "lineItems": {"nodes": [{"name": "Anillo - 7", "sku": "A7", "quantity": 1}]}})
    assert basic.items[0].title == "Anillo - 7"


def test_it_draws_a_full_letter_page(core):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    from labelbridge.labels.order_page import LETTER_MM, draw_order_page
    from labelbridge.printing.engine import Document

    image = QImage(round(LETTER_MM[0] / 25.4 * 100), round(LETTER_MM[1] / 25.4 * 100), QImage.Format.Format_RGB32)
    painter = QPainter(image)
    draw_order_page(painter, QRectF(0, 0, image.width(), image.height()), 100, parse_detail(PICKUP), "MI TIENDA")
    painter.end()
    dark = sum(1 for y in range(0, image.height(), 4) for x in range(0, image.width(), 4)
               if image.pixelColor(x, y).lightness() < 128)
    assert dark > 400  # hay texto, filas y código de barras
    profile = PrinterProfile(alias="pdf", kind="file", output_dir=str(core.home), language="driver", media="letter")
    pdf = core.engine._as_pdf(Document("order_page", order=sample_page_order()), profile)
    assert pdf.startswith(b"%PDF") and b"/MediaBox [0 0 612.000000 792.000000]" in pdf  # hoja carta


def test_api_jobs_can_ask_for_the_order_page():
    req = parse_job({"id": "x1", "format": "order_page", "order": PICKUP}, require_id=True)
    assert req.format == "order_page" and req.order["name"] == "#3001"
    assert parse_job({"id": "x2", "order": PICKUP}, require_id=True).format == "order"  # sin pedirla: la etiqueta


# --- cuándo y por dónde sale ----------------------------------------------------------------------

def test_a_pickup_order_prints_its_page_and_buys_no_label(core, monkeypatch):
    _printers(core)
    fake = StoreWithPickup(iso(datetime.now(timezone.utc).timestamp()))
    service, s = _service(core, monkeypatch, fake)
    s.print_order_page, s.order_page_printer, s.shipping_label_printer = True, "oficina", "etiquetas"
    service.cycle(service.client(), s)
    pages = _pages(core)
    assert len(pages) == 1 and pages[0]["printer"] == "oficina" and pages[0]["title"] == "Página del pedido #3001"
    assert core.store.read_order(pages[0])["billingAddress"]["name"] == "Omar Bosch"
    assert fake.purchases == []  # retiro en tienda: ninguna guía
    service.cycle(service.client(), s)
    assert len(_pages(core)) == 1  # una vez por pedido


def test_without_its_page_from_shopify_it_prints_the_basics(core, monkeypatch):
    _printers(core)
    fake = StoreWithPickup(iso(datetime.now(timezone.utc).timestamp()), page=False)
    service, s = _service(core, monkeypatch, fake)
    s.print_order_page, s.order_page_printer, s.shipping_label_printer = True, "oficina", "etiquetas"
    service.cycle(service.client(), s)
    pages = _pages(core)
    assert len(pages) == 1 and core.store.read_order(pages[0])["name"] == "#3001"  # lo del ciclo, sin precios


def test_it_never_prints_blindly_or_on_the_label_printer(core, monkeypatch):
    _printers(core)
    fake = StoreWithPickup(iso(datetime.now(timezone.utc).timestamp()))
    service, s = _service(core, monkeypatch, fake)
    s.print_order_page, s.order_page_printer, s.order_label_printer = True, "", ""
    service.cycle(service.client(), s)
    assert _pages(core) == []  # sin impresora elegida: no sale por la predeterminada
    notices = [d for kind, d in core.received if kind == "notify" and d["title"] == "Página del pedido sin impresora"]
    assert len(notices) == 1 and "elige su impresora" in notices[0]["message"]
    s.order_page_printer = "etiquetas"  # la de las guías: tampoco (gastaría etiquetas)
    service.cycle(service.client(), s)
    assert _pages(core) == []
    # La de la etiqueta del pedido no sirve de respaldo (suele ser otra de etiquetas), ni ninguna de etiquetas.
    second = PrinterProfile(alias="rollo-2", kind="file", output_dir=str(core.home / "r2"), language="driver",
                            media="4x6")
    core.cfg.data.printers.append(second)
    s.order_page_printer, s.order_label_printer = "", "rollo-2"
    service.cycle(service.client(), s)
    s.order_page_printer = "rollo-2"
    service.cycle(service.client(), s)
    assert _pages(core) == []
    labels_notice = [d for kind, d in core.received if kind == "notify" and "es de etiquetas" in d["message"]]
    assert len(labels_notice) == 1


def test_print_order_page_from_the_orders_tab_makes_another_copy(core, monkeypatch):
    _printers(core)
    fake = StoreWithPickup(iso(datetime.now(timezone.utc).timestamp()))
    service, s = _service(core, monkeypatch, fake)
    s.order_page_printer = "oficina"
    assert service.print_order_page("3001", "#3001") == 1
    assert service.print_order_page("3001", "#3001") == 1  # otra copia, si la persona la pide
    assert len(_pages(core)) == 2


def test_routes_card_shows_the_page_row_and_its_warnings(core):
    from labelbridge.printing import routing
    from labelbridge.ui.print_routes import PrintRoutesCard

    _printers(core)
    s = core.cfg.data.shopify
    s.order_page_printer = ""
    card = PrintRoutesCard(_ui_context(core))
    combo = card.combos[routing.PAGE]
    assert combo.itemText(0) == "Elige una impresora…" and card.alias(routing.PAGE) == ""
    assert "Elige la impresora" in card.page_note.text()
    combo.setCurrentIndex(combo.findData("etiquetas"))
    assert "impresora de las guías" in card.page_note.text()
    combo.setCurrentIndex(combo.findData("oficina"))
    assert card.page_note.text() == ""
    card.set_page_active(False)
    combo.setCurrentIndex(0)
    assert card.page_note.text() == ""  # apagada: nada que avisar


# --- fotos de los productos -------------------------------------------------------------------------

def _jpeg() -> bytes:
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage

    image = QImage(40, 80, QImage.Format.Format_RGB32)
    image.fill(QColor("#203040"))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPG")
    return bytes(buffer.data())


def test_photos_come_only_from_shopify_and_travel_with_the_job(core):
    from labelbridge.connectors import shopify_orders as so

    assert "preferredContentType: JPG" in so.ORDER_PAGE_QUERY  # las fotos HEIC del iPhone no se pueden imprimir
    order = json.loads(json.dumps(PICKUP))
    nodes = order["lineItems"]["nodes"]
    nodes[0]["image"] = {"url": "https://cdn.shopify.com/s/files/1/x/idde.jpg?v=1"}
    nodes.append({"title": "Otro", "quantity": 1, "image": {"url": "https://evil.example.com/x.jpg"}})
    nodes.append({"title": "Sin foto", "quantity": 1})
    asked: list[str] = []
    jpeg = _jpeg()
    assert so.attach_photos(order, fetch=lambda url: asked.append(url) or jpeg) == 1
    assert asked == ["https://cdn.shopify.com/s/files/1/x/idde.jpg?v=1"]  # nunca otro sitio
    items = parse_detail(order).items
    assert items[0].photo == jpeg and items[1].photo == b"" and items[2].photo == b""

    def broken(url):
        raise net.NetworkError("sin internet")

    order2 = json.loads(json.dumps(PICKUP))
    order2["lineItems"]["nodes"][0]["image"] = {"url": "https://cdn.shopify.com/a.jpg"}
    assert so.attach_photos(order2, fetch=broken) == 0 and "photo" not in order2["lineItems"]["nodes"][0]


def test_the_page_draws_the_photo_next_to_the_product():
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    from labelbridge.labels.order_page import LETTER_MM, MARGIN, PHOTO, draw_order_page

    order = json.loads(json.dumps(PICKUP))
    order["lineItems"]["nodes"][0].update(image={"url": "https://cdn.shopify.com/a.jpg"},
                                          photo=__import__("base64").b64encode(_jpeg()).decode())
    dpi = 100
    image = QImage(round(LETTER_MM[0] / 25.4 * dpi), round(LETTER_MM[1] / 25.4 * dpi), QImage.Format.Format_RGB32)
    painter = QPainter(image)
    draw_order_page(painter, QRectF(0, 0, image.width(), image.height()), dpi, parse_detail(order), "MI TIENDA")
    painter.end()
    px = dpi / 25.4
    x = round((MARGIN + PHOTO / 2) * px)
    column = [image.pixelColor(x, y) for y in range(image.height())]
    assert any(c.blue() > c.red() + 20 and c.lightness() < 100 for c in column)  # el azul oscuro de la foto


# --- lo que encontró la revisión ---------------------------------------------------------------------

def test_a_page_failure_never_stops_buying_labels(core, monkeypatch):
    from labelbridge.connectors import shopify as shopify_module

    _printers(core)
    fake = StoreWithPickup(iso(datetime.now(timezone.utc).timestamp()))
    fake.order = lambda number, created, **extra: FakeShopify.order(fake, number, created, **extra)  # con envío
    service, s = _service(core, monkeypatch, fake)
    s.order_page_printer = "oficina"

    def broken(order, *a, **k):
        raise UnicodeEncodeError("ascii", "é", 0, 1, "boom")

    monkeypatch.setattr(shopify_module, "attach_photos", broken)
    service.cycle(service.client(), s)  # no se cae
    assert len(fake.purchases) == 1 and _pages(core) == []  # la guía se compró igual (va antes que la página)
    service.cycle(service.client(), s)
    assert len(fake.purchases) == 1  # y no se repite nada


def test_old_orders_never_print_in_a_burst(core, monkeypatch):
    _printers(core)
    fake = StoreWithPickup(iso(datetime.now(timezone.utc).timestamp() - 3 * 86400))  # pedidos de hace 3 días
    service, s = _service(core, monkeypatch, fake)
    service.reset_since(hours_back=24 * 30)  # la tienda conectada hace un mes
    s.order_page_printer = "oficina"
    service.cycle(service.client(), s)
    assert _pages(core) == []


def test_without_shopify_page_the_basic_one_shows_no_zero_prices(core, monkeypatch):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    from labelbridge.labels.order_page import draw_order_page

    _printers(core)
    fake = StoreWithPickup(iso(datetime.now(timezone.utc).timestamp()), page=False)
    service, s = _service(core, monkeypatch, fake)
    s.order_page_printer = "oficina"
    service.cycle(service.client(), s)
    order = core.store.read_order(_pages(core)[0])
    assert order["_basic"] is True
    image = QImage(400, 520, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    draw_order_page(painter, QRectF(0, 0, 400, 520), 50, parse_detail(order), basic=True)  # no se cae
    painter.end()


def test_photos_are_capped_deduplicated_and_kept_on_shopify(core):
    from labelbridge.connectors import shopify_orders as so

    order = {"lineItems": {"nodes": [{"title": f"P{i}", "quantity": 1,
                                      "image": {"url": "https://cdn.shopify.com/same.jpg"}} for i in range(12)]}}
    asked: list[str] = []
    assert so.attach_photos(order, fetch=lambda url: asked.append(url) or b"x") == so.MAX_PHOTOS
    assert asked == ["https://cdn.shopify.com/same.jpg"]  # la misma foto, una sola descarga
    with pytest.raises(ValueError):
        so.photo_guard("http://cdn.shopify.com/a.jpg")  # ni sin https
    with pytest.raises(ValueError):
        so.photo_guard("https://192.168.1.1/a.jpg")  # ni a la red local (también tras una redirección)
    slow = {"lineItems": {"nodes": [{"title": f"P{i}", "quantity": 1,
                                     "image": {"url": f"https://cdn.shopify.com/{i}.jpg"}} for i in range(5)]}}

    def fail(url):
        asked.append(url)
        raise net.NetworkError("timeout")

    asked.clear()
    assert so.attach_photos(slow, fetch=fail) == 0 and len(asked) == 1  # la CDN no contesta: no insiste


def test_page_jobs_do_not_tag_the_order_and_fulfilled_orders_get_none(core, monkeypatch):
    _printers(core)
    fake = StoreWithPickup(iso(datetime.now(timezone.utc).timestamp()))
    service, s = _service(core, monkeypatch, fake)
    s.order_page_printer, s.tag_after_print = "oficina", "impreso"
    service._on_event("job", {"source": "shopify", "status": "done", "format": "order_page",
                              "meta": {"order_gid": "gid://shopify/Order/1"}})
    assert list(service._tag_queue) == []
    order = fake.order(3001, fake.now_iso, displayFulfillmentStatus="FULFILLED")
    assert service._print_order_page(service.client(), s, order, {"order_id": "3001", "order_name": "#3001"}) == 0


def test_the_routes_test_button_refuses_label_printers(core, monkeypatch):
    from labelbridge.printing import routing
    from labelbridge.ui import print_routes

    _printers(core)
    errors: list[str] = []
    monkeypatch.setattr(print_routes, "error", lambda _p, _t, text: errors.append(text))
    core.cfg.data.printers.append(PrinterProfile(alias="rollo-2", kind="file", output_dir=str(core.home / "r2"),
                                                 media="4x6"))
    card = print_routes.PrintRoutesCard(_ui_context(core))
    combo = card.combos[routing.PAGE]
    combo.setCurrentIndex(combo.findData("etiquetas"))
    card.test(routing.PAGE)
    assert "guías" in errors[-1]  # la de las guías
    combo.setCurrentIndex(combo.findData("rollo-2"))
    card.test(routing.PAGE)
    assert "etiquetas" in errors[-1] and "es de etiquetas" in card.page_note.text()
