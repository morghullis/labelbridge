"""Pestaña Pedidos: cada pedido de Shopify ordenado (qué necesita atención y por qué, por qué paquetería sale y cuánto
costó su guía), las reglas de paquetería y las acciones «Reintentar compra»."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from test_connectors import FakeShopify

from labelbridge.config import ShippingRule, ShopifySettings
from labelbridge.connectors.orders_overview import (
    carrier_name,
    classify,
    csv_rows,
    in_group,
    parse_amount,
    rule_for,
    summarize,
)
from labelbridge.connectors.shopify import ShopifyError, ShopifyService
from labelbridge.secret_store import SHOPIFY_CLIENT_SECRET

NOW = 1_790_000_000.0
SINCE = NOW - 7 * 86400  # LabelBridge conectado hace una semana


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_order(
    number: int = 1500,
    *,
    created: float = NOW - 3600,
    subtotal: float = 300.0,
    method: str = "Economy",
    financial: str = "PAID",
    fulfillment: str = "UNFULFILLED",
    fulfillments: tuple = (),
    labels: tuple = (),
    voided: tuple = (),
    delivery: str = "SHIPPING",
    cancelled: str | None = None,
    tags: tuple = (),
) -> dict:
    events = [
        {"createdAt": iso(created + 30), "message": f"Etiquetas compró una etiqueta de envío por {cost} $.",
         "action": "shipping_label_created_success", "arguments": [label_id, f"shipping_label_{number}.pdf"]}
        for label_id, cost in labels
    ] + [
        {"createdAt": iso(created + 90), "message": f"Has anulado una etiqueta de envío por {cost} $.",
         "action": "shipping_label_cancelled_success", "arguments": [label_id, f"shipping_label_{number}.pdf"]}
        for label_id, cost in voided
    ]
    return {
        "id": f"gid://shopify/Order/{number}",
        "legacyResourceId": str(number),
        "name": f"#{number}#",
        "createdAt": iso(created),
        "cancelledAt": cancelled,
        "displayFinancialStatus": financial,
        "displayFulfillmentStatus": fulfillment,
        "tags": list(tags),
        "totalPriceSet": {"shopMoney": {"amount": str(subtotal + 40), "currencyCode": "USD"}},
        "currentSubtotalPriceSet": {"shopMoney": {"amount": str(subtotal)}},
        "shippingAddress": {"name": "Ana López", "city": "Miami", "provinceCode": "FL", "countryCodeV2": "US"},
        "shippingLine": {"title": method},
        "fulfillments": list(fulfillments),
        "fulfillmentOrders": {"nodes": [{"status": "OPEN", "deliveryMethod": {"methodType": delivery}}]},
        "events": {"nodes": events},
    }


def usps(number: str = "9200190267338800106038", created: float = NOW, **delivery) -> dict:
    """Un envío con guía de USPS; `delivery` = lo que informa la paquetería (displayStatus, inTransitAt…)."""
    return {"status": "SUCCESS", "createdAt": iso(created), "trackingInfo": [
        {"company": "USPS", "number": number, "url": f"https://tools.usps.com/?t={number}"}], **delivery}


def settings(**changes) -> ShopifySettings:
    s = ShopifySettings()
    s.buy_shipping_label = True
    for key, value in changes.items():
        setattr(s, key, value)
    return s


def row_of(order: dict, **kwargs):
    return classify(order, settings=kwargs.pop("settings", settings()), since=SINCE, now=NOW, **kwargs)


# --- piezas -------------------------------------------------------------------------------------

def test_label_costs_and_carrier_names():
    assert parse_amount("Etiquetas compró una etiqueta de envío por 5,68 $.") == 5.68
    assert parse_amount("Labels purchased a shipping label for $5.68.") == 5.68
    assert parse_amount("Se capturó 2.220,00 $ USD") == 2220.0
    assert parse_amount("Bought a label for $1,234.50") == 1234.5
    assert parse_amount("sin importe") == 0.0
    assert carrier_name("United States Postal Service") == "USPS"
    assert carrier_name("FedEx Ground") == "FedEx" and carrier_name("UPS") == "UPS" and carrier_name("DHL Express") == "DHL"
    assert carrier_name("USPS") == "USPS" and carrier_name("Correos") == "Correos"


def test_fedex_rule_follows_the_value_of_the_pieces():
    rules = ShopifySettings().shipping_rules  # por defecto: piezas de más de US$1000 → FedEx, comprada en Shopify
    rule = rule_for(rules, "Economy", 1250)
    assert rule is not None and rule.carrier == "FedEx" and not rule.auto
    assert rule_for(rules, "Express", 1250).carrier == "FedEx"  # con cualquier método
    assert rule_for(rules, "Express", 900) is None  # Express barato: USPS
    assert rule_for(rules, "Express", 1000) is None  # «más de» 1000
    custom = [ShippingRule("", "USPS", True, 0), ShippingRule("express", "UPS", True)]
    assert rule_for(custom, "Express Shipping", 10).carrier == "UPS"  # una regla vacía no decide nada


# --- clasificación --------------------------------------------------------------------------------

def test_expensive_pieces_need_a_fedex_label_by_hand():
    row = row_of(make_order(subtotal=1250, method="Economy"))
    assert row.needs_action and row.group == "fedex" and row.carrier == "FedEx" and row.action == "buy"
    assert row.status == "Comprar guía FedEx en Shopify" and "US$1,250.00" in row.reason
    assert "más de US$1,000.00" in row.reason and "imprime sola" in row.reason


def test_delivery_progress_and_delay_alerts():
    from types import SimpleNamespace

    from labelbridge.connectors.orders_overview import in_group

    def shipped(created: float = NOW - 3 * 86400, limits=None, **delivery):
        order = make_order(labels=(("1", "5,68"),), fulfillments=(usps(created=created, **delivery),), fulfillment="FULFILLED")
        return row_of(order, jobs=[{"status": "done", "updated_at": NOW}], limits=limits)

    on_way = shipped(displayStatus="IN_TRANSIT", inTransitAt=iso(NOW - 86400), estimatedDeliveryAt=iso(NOW + 86400))
    assert on_way.alert == "" and on_way.status.startswith("En camino · llega el")
    delivered = shipped(displayStatus="DELIVERED", deliveredAt=iso(NOW - 3600))
    assert delivered.alert == "" and delivered.status.startswith("Entregado el")
    late = shipped(displayStatus="IN_TRANSIT", inTransitAt=iso(NOW - 4 * 86400), estimatedDeliveryAt=iso(NOW - 2 * 86400))
    assert late.alert == "late" and late.status == "Entrega atrasada" and "2 día(s) de retraso" in late.alert_reason
    attempted = shipped(displayStatus="ATTEMPTED_DELIVERY", inTransitAt=iso(NOW - 86400))
    assert attempted.alert == "problem" and attempted.status == "Intento de entrega fallido"
    not_scanned = shipped(displayStatus="LABEL_PRINTED")  # guía de hace 3 días que USPS todavía no recibe
    assert not_scanned.alert == "no_scan" and "3 día(s)" in not_scanned.alert_reason and "USPS" in not_scanned.alert_reason
    assert shipped(displayStatus="LABEL_PRINTED", created=NOW - 3600).alert == ""  # recién comprada: todavía no
    slow = shipped(displayStatus="IN_TRANSIT", inTransitAt=iso(NOW - 9 * 86400))
    assert slow.alert == "slow" and "9 días" in slow.alert_reason
    relaxed = SimpleNamespace(no_scan_days=5, transit_days=14)  # Ajustes → alertas de entrega
    assert shipped(displayStatus="LABEL_PRINTED", limits=relaxed).alert == ""
    assert shipped(displayStatus="IN_TRANSIT", inTransitAt=iso(NOW - 9 * 86400), limits=relaxed).alert == ""
    summary = summarize([on_way, delivered, late, attempted, not_scanned])
    assert summary.alerts == 3 and summary.groups["delays"] == 3 and in_group(late, "delays")
    waiting = row_of(make_order(subtotal=1500, created=NOW - 3 * 86400))  # FedEx sin comprar desde hace 3 días
    assert waiting.needs_action and waiting.waiting_days == 3


def test_a_cheap_express_goes_by_usps_automatically():
    fresh = row_of(make_order(subtotal=800, method="Express", created=NOW - 60))
    assert fresh.state == "waiting" and fresh.group == "usps" and fresh.status == "Comprando la guía…"
    late = row_of(make_order(subtotal=800, method="Express", created=NOW - 3600))
    assert late.needs_action and late.action == "retry" and "conectado" in late.reason


def test_label_cost_tracking_and_printing():
    order = make_order(labels=(("101", "5,68"),), fulfillments=(usps(),), fulfillment="FULFILLED")
    printed = row_of(order, jobs=[{"status": "done", "updated_at": NOW}])
    assert printed.state == "done" and printed.status == "Guía comprada e impresa"
    assert (printed.cost, printed.labels, printed.carrier, printed.group) == (5.68, 1, "USPS", "usps")
    assert printed.tracking[0][1] == "9200190267338800106038"
    failed = row_of(order, jobs=[{"status": "failed", "error": "Sin papel", "updated_at": NOW}])
    assert failed.needs_action and failed.action == "reprint" and "Sin papel" in failed.reason
    bought_by_hand = row_of(order)  # sin trabajo de impresión: la compraron en el admin de Shopify
    assert bought_by_hand.state == "done" and bought_by_hand.status == "Guía comprada en Shopify"


def test_voided_labels_and_cancelled_orders():
    refunded = row_of(make_order(financial="REFUNDED", labels=(("7", "5,68"),), voided=(("7", "5,68"),)))
    assert refunded.state == "cancelled" and refunded.cost == 0 and refunded.voided == 5.68
    forgotten = row_of(make_order(financial="REFUNDED", labels=(("8", "5,68"),)))
    assert forgotten.needs_action and forgotten.action == "void" and "US$5.68" in forgotten.reason


def test_orders_fulfilled_by_shopify_without_a_label():
    auto = row_of(make_order(fulfillment="FULFILLED", fulfillments=({"status": "SUCCESS", "trackingInfo": []},)))
    assert auto.needs_action and auto.status == "Preparado sin guía" and "preparación automática" in auto.reason
    old = row_of(make_order(created=SINCE - 86400, fulfillment="FULFILLED",
                            fulfillments=({"status": "SUCCESS", "trackingInfo": []},)))
    assert old.state == "info"  # de antes de LabelBridge: no se pide nada


def test_reasons_from_what_labelbridge_tried():
    order = make_order()
    failed = row_of(order, ledger={"state": "failed", "reason": "ADDRESS_INVALID La dirección no existe"})
    assert failed.action == "retry" and "La dirección no existe" in failed.reason
    assert row_of(order, ledger={"state": "uncertain"}).action == "open"
    assert row_of(order, ledger={"state": "limit"}).action == "retry"
    assert "desactivada" in row_of(order, settings=settings(buy_shipping_label=False)).reason
    assert row_of(make_order(financial="PENDING")).state == "waiting"
    pickup = row_of(make_order(delivery="PICK_UP"))
    assert pickup.group == "pickup" and pickup.state == "info"


def test_summary_tabs_and_export():
    rows = [
        row_of(make_order(1, labels=(("1", "5,68"),), fulfillments=(usps(),), fulfillment="FULFILLED"),
               jobs=[{"status": "done"}]),
        row_of(make_order(2, subtotal=2400)),  # FedEx: se compra en Shopify
        row_of(make_order(3, delivery="PICK_UP")),
        row_of(make_order(4, financial="REFUNDED", labels=(("4", "5,68"),), voided=(("4", "5,68"),))),
    ]
    summary = summarize(rows)
    assert (summary.orders, summary.action, summary.labels, summary.cost, summary.voided) == (4, 1, 1, 5.68, 5.68)
    assert summary.by_carrier == {"USPS": (1, 5.68)}
    assert summary.groups["action"] == 1 and summary.groups["fedex"] == 1 and summary.groups["pickup"] == 1
    assert [row.name for row in rows if in_group(row, "action")] == ["#2#"]
    exported = csv_rows(rows)
    assert exported[0][0] == "#1#" and exported[0][7] == "5.68" and exported[0][9] == "Sí"
    assert "FedEx" in exported[1][5] and exported[1][11]


# --- conector -------------------------------------------------------------------------------------

class ExpensiveShopify(FakeShopify):
    """Pedidos con piezas de US$1500: salen por FedEx."""

    def order(self, number: int, created: str, **extra) -> dict:
        extra.setdefault("currentSubtotalPriceSet", {"shopMoney": {"amount": "1500.00"}})
        return super().order(number, created, **extra)


def _service(core, monkeypatch, fake) -> tuple[ShopifyService, ShopifySettings]:
    monkeypatch.setattr("labelbridge.connectors.shopify.net.request", fake)
    s = core.cfg.data.shopify
    s.enabled, s.shop, s.client_id = True, "mi-tienda", "client-id"
    s.print_order_label, s.metafield_label, s.buy_shipping_label = False, False, True
    core.secrets.set(SHOPIFY_CLIENT_SECRET, "secreto")
    service = ShopifyService(core.cfg, core.secrets, core.store, core.engine, core.events)
    service.reset_since(hours_back=1)
    return service, s


def test_fedex_orders_are_never_bought_automatically(core, monkeypatch):
    fake = ExpensiveShopify(iso(datetime.now(timezone.utc).timestamp()))
    service, s = _service(core, monkeypatch, fake)
    service.cycle(service.client(), s)
    assert fake.purchases == []  # ni FedEx (no se puede) ni una USPS por error
    assert service.ledger("3001") ["state"] == "manual" and service.ledger("3001")["carrier"] == "FedEx"
    assert any(kind == "notify" and "FedEx en Shopify" in data["title"] for kind, data in core.received)
    service.cycle(service.client(), s)
    assert sum(1 for kind, data in core.received if kind == "notify" and "FedEx" in data["title"]) == 1  # una vez


class FedexBoughtInShopify(ExpensiveShopify):
    """Pedido de FedEx: primero sin guía; después alguien la compra en el pedido en Shopify (un clic)."""

    def __init__(self, now_iso: str) -> None:
        super().__init__(now_iso)
        self.bought = False
        self.label_error = ""
        self.label_queries = 0

    def order(self, number: int, created: str, **extra) -> dict:
        if self.bought:
            extra.setdefault("displayFulfillmentStatus", "FULFILLED")
        return super().order(number, created, **extra)

    def __call__(self, method, url, **kwargs):
        from labelbridge import net

        query = (kwargs.get("json_body") or {}).get("query", "")
        if "LabelBridgeOrderLabels" in query:
            nodes = [
                {"createdAt": self.now_iso, "message": "Compraste una etiqueta de envío por 38,20 $.",
                 "action": "shipping_label_created_success", "arguments": [7001, "shipping_label_3001.pdf"]},
                {"createdAt": self.now_iso, "message": "Compraste una etiqueta de envío por 5,68 $.",
                 "action": "shipping_label_created_success", "arguments": [7000, "shipping_label_3001.pdf"]},
                {"createdAt": self.now_iso, "message": "Has anulado una etiqueta de envío por 5,68 $.",
                 "action": "shipping_label_cancelled_success", "arguments": [7000, "shipping_label_3001.pdf"]},
            ] if self.bought and kwargs["json_body"]["variables"]["id"].endswith("/3001") else []
            body = {"data": {"order": {"events": {"nodes": nodes}}}}
            return net.Response(200, json.dumps(body).encode(), url)
        if "LabelBridgeShippingLabel" in query:
            self.label_queries += 1
            assert kwargs["json_body"]["variables"]["id"] == "gid://shopify/ShippingLabel/7001"  # nunca la anulada
            if self.label_error:
                body = {"errors": [{"message": self.label_error}]}
            else:
                body = {"data": {"shippingLabel": {
                    "id": "gid://shopify/ShippingLabel/7001",
                    "trackingInfo": {"number": "771234567890", "company": "FedEx", "url": None},
                    "shippingDocuments": [{"documentType": "LABEL", "format": "PDF", "url": "https://cdn.example.com/fedex-3001.pdf"}],
                }}}
            return net.Response(200, json.dumps(body).encode(), url)
        return super().__call__(method, url, **kwargs)


def test_fedex_bought_in_shopify_prints_by_itself(core, monkeypatch):
    fake = FedexBoughtInShopify(iso(datetime.now(timezone.utc).timestamp()))
    service, s = _service(core, monkeypatch, fake)
    service.cycle(service.client(), s)
    assert service.ledger("3001")["state"] == "manual" and fake.label_queries == 0

    fake.bought = True  # la compran en Shopify: el pedido pasa a preparado y su historial tiene la guía
    service.cycle(service.client(), s)
    job = core.store.get_by_external("shopify", "shipping-7001-0-0")
    assert job is not None and job["url"] == "https://cdn.example.com/fedex-3001.pdf" and job["format"] == "pdf"
    assert job["meta"]["carrier"] == "FedEx" and job["meta"]["tracking"] == "771234567890"
    assert job["meta"]["label_size"] == "4x6" and job["meta"]["order_id"] == "3001"
    assert service.ledger("3001")["state"] == "resolved" and fake.purchases == []  # LabelBridge no compró nada

    service.cycle(service.client(), s)  # otra vuelta: no se trae ni se imprime otra vez
    assert fake.label_queries == 1 and len([j for j in core.store.list() if j["source"] == "shopify"]) == 1
    with pytest.raises(ShopifyError):
        service.print_order_labels("3002", "#3002")  # sin guía activa
    assert service.print_order_labels("3001", "#3001") == 0  # «Reimprimir» con la guía ya en la cola: nada nuevo


def test_a_label_shopify_does_not_hand_over_is_reported(core, monkeypatch):
    from labelbridge.connectors import shopify as shopify_module

    fake = FedexBoughtInShopify(iso(datetime.now(timezone.utc).timestamp()))
    fake.label_error = "Field 'shippingLabel' doesn't exist on type 'QueryRoot'"
    service, s = _service(core, monkeypatch, fake)
    service.cycle(service.client(), s)
    fake.bought = True
    for _ in range(shopify_module.FETCH_TRIES):
        service.cycle(service.client(), s)
    assert service.ledger("3001")["state"] == "no_document"  # Pedidos → «Guía sin PDF»: imprimirla desde Shopify
    assert any(kind == "notify" and "Guía sin imprimir" in data["title"] for kind, data in core.received)
    assert service._to_fetch() == {} and fake.purchases == []


def test_buying_from_the_app_after_shopify_rejected_it(core, monkeypatch):
    from labelbridge.connectors import shopify as shopify_module

    fake = FakeShopify(iso(datetime.now(timezone.utc).timestamp()))
    service, s = _service(core, monkeypatch, fake)
    real_purchase = shopify_module.ShopifyClient.purchase_label

    def rejected(self, fo_id, notify, **_options):
        raise shopify_module.PurchaseRejected("TOTAL_WEIGHT_ZERO Non-zero total weight is required for purchase.")

    monkeypatch.setattr(shopify_module.ShopifyClient, "purchase_label", rejected)
    service.cycle(service.client(), s)
    assert service.ledger("3001")["state"] == "failed" and "weight" in service.ledger("3001")["reason"]

    # «Comprar guía…»: la persona pone su caja y el peso, confirma, y se compra, se imprime y Shopify queda al día.
    monkeypatch.setattr(shopify_module.ShopifyClient, "purchase_label", real_purchase)
    s.max_labels_per_day = 0  # el límite diario frena las compras automáticas, no las confirmadas
    box = {"type": "BOX", "dimensions": {"length": 6.0, "width": 4.0, "height": 2.0, "unit": "INCHES"},
           "weight": {"value": 3.0, "unit": "OUNCES"}}
    result = service.buy_label_now("3001", package=box, total_weight={"value": 1.5, "unit": "POUNDS"}, notify=True)
    assert result["state"] == "purchased" and result["queued"] >= 1 and result["tracking"] == "1Z999"
    sent = fake.purchases[-1]
    assert sent["packageInfo"] == {"customPackage": box} and sent["totalWeight"] == {"value": 1.5, "unit": "POUNDS"}
    assert sent["notifyCustomer"] is True and len(fake.purchases) == 1
    assert core.store.get_by_external("shopify", "shipping-5-0-0") is not None  # en la cola de impresión

    service._note("3002", "uncertain")
    with pytest.raises(ShopifyError):
        service.buy_label_now("3002")  # una compra sin confirmar: nunca se compra dos veces
    s.package.use_custom, s.package.auto = True, True  # la caja guardada también en las compras automáticas
    assert shopify_module.package_input(s.package)["dimensions"]["unit"] == "INCHES"


def test_overview_joins_shopify_with_what_labelbridge_did(core, monkeypatch):
    from labelbridge import net
    from labelbridge.models import JobRequest

    now = datetime.now(timezone.utc).timestamp()
    nodes = [
        make_order(1601, created=now - 600, labels=(("9", "7,10"),), fulfillments=(usps(created=now - 500),),
                   fulfillment="FULFILLED"),
        make_order(1602, created=now - 3600, subtotal=1800),
    ]

    def fake(method, url, **kwargs):
        if url.endswith("/admin/oauth/access_token"):
            return net.Response(200, json.dumps({"access_token": "tok", "expires_in": 86399}).encode(), url)
        query = kwargs["json_body"]["query"]
        assert "LabelBridgeOverview" in query and "created_at:>=" in kwargs["json_body"]["variables"]["query"]
        body = {"data": {"orders": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}
        return net.Response(200, json.dumps(body).encode(), url)

    service, _s = _service(core, monkeypatch, fake)
    job, _ = core.store.add(
        JobRequest(external_id="shipping-9-0-0", title="Etiqueta de envío #1601#", content=b"^XA^XZ", format="zpl",
                   meta={"order_id": "1601", "tracking": "9200"}),
        "shopify",
    )
    core.store.set_done(job["id"], "archivo")
    rows = {row.name: row for row in service.overview(days=30)}
    assert rows["#1601#"].status == "Guía comprada e impresa" and rows["#1601#"].cost == 7.1
    assert rows["#1602#"].needs_action and rows["#1602#"].carrier == "FedEx"


# --- pantalla -------------------------------------------------------------------------------------

def _ui_context(core):
    from labelbridge.services import Services
    from labelbridge.ui.context import AppContext, QtBridge

    core.cfg.data.local_api.enabled = False
    services = Services(core.cfg, core.secrets, core.store, core.engine, core.events)
    return AppContext(core.cfg, core.secrets, core.store, core.engine, services, core.events, QtBridge(core.events, core.cfg))


def test_orders_page_puts_what_needs_a_person_first(core, monkeypatch, tmp_path):
    from conftest import wait_for
    from PySide6.QtWidgets import QApplication, QFileDialog

    from labelbridge.ui.pages import orders as orders_page

    ctx = _ui_context(core)
    page = orders_page.OrdersPage(ctx)
    QApplication.processEvents()
    page.reload()  # sin tienda conectada: lo explica y no cuenta nada
    assert page.all_rows == [] and page.action_list.count() == 1

    s = core.cfg.data.shopify
    s.enabled, s.shop, s.buy_shipping_label = True, "mi-tienda", True
    rows = [
        classify(make_order(1, subtotal=1500), settings=s, since=SINCE, now=NOW),  # FedEx: se compra en Shopify
        classify(make_order(2, labels=(("2", "5,68"),), fulfillments=(usps(),), fulfillment="FULFILLED"),
                 settings=s, jobs=[{"status": "done"}], since=SINCE, now=NOW),
        classify(make_order(3, delivery="PICK_UP"), settings=s, since=SINCE, now=NOW),
    ]
    monkeypatch.setattr(ctx.services.shopify, "overview", lambda days=30: rows)
    counts: list[int] = []
    page.attention_changed.connect(counts.append)
    page.reload()
    assert wait_for(lambda: (QApplication.processEvents(), bool(page.all_rows))[1])
    assert counts[-1] == 1 and page.model.rowCount() == 3
    assert isinstance(page.action_list.itemAt(0).widget(), orders_page.ActionItem)
    assert page.stat_labels[0].text() == "US$5.68" and page.tabs["usps"].text().endswith("1")
    page._set_group("fedex")
    assert [row.name for row in page.model.rows] == ["#1#"]
    page._set_group("all")
    page.search.setText("9200190267")  # por número de seguimiento
    assert [row.name for row in page.model.rows] == ["#2#"]

    target = tmp_path / "pedidos.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    monkeypatch.setattr(orders_page, "info", lambda *a, **k: None)
    page.export_csv()
    text = target.read_text(encoding="utf-8-sig")
    assert text.startswith("Pedido,Fecha,Cliente") and "#1#" in text and "5.68" in text


def test_shipping_rules_are_edited_and_saved(core, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from labelbridge.ui.pages import orders as orders_page

    page = orders_page.OrdersPage(_ui_context(core))
    dialog = orders_page.ShippingRulesDialog(page, core.cfg.data.shopify.shipping_rules, "USPS")
    over, method, carrier, mode, _remove = dialog.rows[0]
    assert (over.value(), carrier.currentData(), mode.currentData(), mode.isEnabled()) == (1000.0, "FedEx", False, False)
    dialog._add_row(ShippingRule("", "FedEx", False, 0.0))
    _o, extra_method, extra_carrier, extra_mode, _r = dialog.rows[1]
    extra_method.setText("Overnight")
    extra_carrier.setCurrentIndex(extra_carrier.findData("UPS"))
    extra_mode.setCurrentIndex(0)
    assert extra_mode.isEnabled()  # solo FedEx queda fijo «en Shopify»
    monkeypatch.setattr(orders_page.ShippingRulesDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(orders_page, "ShippingRulesDialog", lambda *a, **k: dialog)
    page.edit_rules()
    saved = core.cfg.data.shopify.shipping_rules
    assert [(r.method, r.carrier, r.auto, r.over) for r in saved] == [("", "FedEx", False, 1000.0), ("Overnight", "UPS", True, 0.0)]


def test_overview_works_without_fulfillment_order_permission(core, monkeypatch):
    from labelbridge import net

    queries: list[str] = []

    def fake(method, url, **kwargs):
        if url.endswith("/admin/oauth/access_token"):
            return net.Response(200, json.dumps({"access_token": "tok", "expires_in": 86399}).encode(), url)
        query = kwargs["json_body"]["query"]
        queries.append(query)
        if "fulfillmentOrders" in query:
            body = {"errors": [{"message": "Access denied for fulfillmentOrders field.", "extensions": {"code": "ACCESS_DENIED"}}]}
        else:
            node = make_order(1701)
            node.pop("fulfillmentOrders")
            body = {"data": {"orders": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [node]}}}
        return net.Response(200, json.dumps(body).encode(), url)

    service, _s = _service(core, monkeypatch, fake)
    rows = service.overview(days=30)
    assert [row.name for row in rows] == ["#1701#"] and rows[0].group == "usps"
    assert len(queries) == 2 and "fulfillmentOrders" not in queries[1]
