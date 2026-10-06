"""«Imprimir guía» en Pedidos: elegir paquetería y servicio, reimprimir, comprar en Shopify (FedEx) y varios pedidos a
la vez. Más los permisos opcionales (existencias, clientes y servicios de Shopify) sin romper lo que ya funcionaba."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

import pytest
from test_connectors import FakeShopify
from test_orders import NOW, make_order, row_of, usps

from labelbridge import net
from labelbridge.connectors import label_batch as lb
from labelbridge.connectors import shipping_services as ss
from labelbridge.connectors import shopify_orders as so
from labelbridge.connectors.shopify import ShopifyClient, ShopifyError, ShopifyService
from labelbridge.models import JobRequest
from labelbridge.secret_store import SHOPIFY_CLIENT_SECRET

# availableCarrierServices tal como lo devolvió una tienda real de EE. UU. (Miami → EE. UU.).
STORE_SERVICES = {"availableCarrierServices": [
    {"carrierService": {"name": "dhl_ecommerce", "availableServicesForCountries": []}},
    {"carrierService": {"name": "dhl_express", "availableServicesForCountries": []}},
    {"carrierService": {"name": "fedex", "availableServicesForCountries": [{"name": "FedEx Ground®"},
                                                                           {"name": "FedEx 2Day®"}]}},
    {"carrierService": {"name": "ups_shipping", "availableServicesForCountries": [
        {"name": "UPS 2nd Day Air A.M.® (commercial addresses only)"}, {"name": "UPS 2nd Day Air®"},
        {"name": "UPS 3 Day Select®"}, {"name": "UPS Next Day Air Saver®"}, {"name": "UPS Next Day Air®"},
        {"name": "UPS Next Day Air® Early"}, {"name": "UPS® Ground"}, {"name": "UPS® Ground Saver"}]}},
    {"carrierService": {"name": "usps", "availableServicesForCountries": [
        {"name": "First Class Mail"}, {"name": "Ground Advantage"}, {"name": "Media Mail"}, {"name": "Priority Mail"},
        {"name": "Priority Mail Express"}]}},
]}


# --- servicios -------------------------------------------------------------------------------------

def test_shopify_names_match_the_service_codes():
    assert ss.normalize("UPS® Ground") == "ground" and ss.normalize("UPS Next Day Air® Early") == "next day air early"
    assert ss.normalize("UPS 2nd Day Air A.M.® (commercial addresses only)") == "2nd day air a.m."
    offered = ss.offered_services(STORE_SERVICES)
    route = ss.Route(domestic=True, destination="US", offered=offered)
    keys = [service.key for service in ss.choices(route)]
    assert keys == ["usps:GroundAdvantage", "usps:Priority", "usps:PriorityExpress", "ups_shipping:03",
                    "ups_shipping:12", "ups_shipping:02", "ups_shipping:13", "ups_shipping:01"]
    assert route.fedex  # la tienda tiene FedEx: se ofrece comprarla en Shopify
    assert ss.selection("usps:Priority") == {"carrierCode": "usps", "serviceCode": "Priority"}
    assert ss.selection(ss.AUTO) is None and ss.service_name(ss.AUTO) == "Automático"


def test_without_read_shipping_the_list_follows_the_destination():
    domestic = [s.key for s in ss.choices(ss.Route(domestic=True))]
    abroad = [s.key for s in ss.choices(ss.Route(domestic=False, destination="MX"))]
    assert "usps:GroundAdvantage" in domestic and "dhl_express:P" not in domestic
    assert abroad and all(ss.by_key(key).international for key in abroad) and "dhl_express:P" in abroad
    assert ss.is_domestic("US", "PR") and ss.is_domestic("US", "US") and not ss.is_domestic("US", "MX")
    assert ss.is_domestic("", "MX")  # sin saber de dónde sale: como nacional
    no_dhl = ss.Route(domestic=False, destination="MX", offered={"ups_shipping": {"worldwide saver"}})
    assert [s.carrier for s in ss.choices(no_dhl)] == ["ups_shipping"] * 4 and not no_dhl.fedex


def test_a_service_shopify_does_not_offer_is_explained_without_charging():
    text = ss.rejection("RATES_NOT_FOUND No rates were found", "ups_shipping:01")
    assert "UPS Next Day Air" in text and "No se cobró nada" in text
    assert ss.rejection("TOTAL_WEIGHT_ZERO weight", "usps:Priority") == "Falta el peso del paquete"
    assert "tarifas" in ss.rejection("RATES_NOT_FOUND", ss.AUTO)  # Automático: el motivo de siempre


# --- compra con el servicio elegido ------------------------------------------------------------------

class StoreWithLabels(FakeShopify):
    """FakeShopify que además sabe el historial de guías del pedido y qué servicios ofrece la tienda."""

    reject_with = ""

    def __call__(self, method, url, **kwargs):
        body = (kwargs.get("json_body") or {})
        query = body.get("query", "")
        if "LabelBridgePurchaseLabel" in query and self.reject_with:
            self.purchases.append(body["variables"]["input"])
            data = {"shippingLabelPurchase": {"shippingLabelPurchaseResult": None, "userErrors": [
                {"field": ["preferredRateSelection"], "code": self.reject_with, "message": "No rates were found"}]}}
            return net.Response(200, json.dumps({"data": data}).encode(), url)
        if "LabelBridgeOrderLabels" in query:
            events = [{"createdAt": self.now_iso, "message": "Etiquetas compró una etiqueta de envío por 5,34 $.",
                       "action": "shipping_label_created_success", "arguments": ["5", "label.pdf"]}]
            return net.Response(200, json.dumps({"data": {"order": {"events": {"nodes": events}}}}).encode(), url)
        if "LabelBridgeRoute" in query:
            order = {"shippingAddress": {"countryCodeV2": "US"}, "fulfillmentOrders": {"nodes": [
                {"status": "OPEN", "assignedLocation": {"countryCode": "US", "location": {"id": "gid://shopify/Location/1"}}}]}}
            return net.Response(200, json.dumps({"data": {"order": order}}).encode(), url)
        if "LabelBridgeServices" in query:
            return net.Response(200, json.dumps({"data": STORE_SERVICES}).encode(), url)
        return super().__call__(method, url, **kwargs)


@pytest.fixture()
def store(core, monkeypatch):
    fake = StoreWithLabels(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    monkeypatch.setattr("labelbridge.connectors.shopify.net.request", fake)
    s = core.cfg.data.shopify
    s.enabled, s.shop, s.client_id, s.buy_shipping_label = True, "mi-tienda", "client-id", True
    core.secrets.set(SHOPIFY_CLIENT_SECRET, "secreto")
    service = ShopifyService(core.cfg, core.secrets, core.store, core.engine, core.events)
    return service, fake


def test_the_chosen_service_is_bought_and_its_cost_reported(store, core):
    service, fake = store
    result = service.buy_label_now("3001", service="usps:GroundAdvantage", wait=5)
    assert result["state"] == "purchased" and result["tracking"] == "1Z999" and result["cost"] == 5.34
    assert fake.purchases[-1]["preferredRateSelection"] == {"carrierCode": "usps", "serviceCode": "GroundAdvantage"}
    assert core.store.get_by_external("shopify", "shipping-5-0-0") is not None  # en la cola de impresión


def test_automatic_lets_shopify_choose(store):
    service, fake = store
    assert service.buy_label_now("3001", wait=5)["state"] == "purchased"
    assert "preferredRateSelection" not in fake.purchases[-1]


def test_a_service_not_offered_is_not_charged_and_can_be_retried(store, core):
    service, fake = store
    fake.reject_with = "RATES_NOT_FOUND"
    result = service.buy_label_now("3001", service="ups_shipping:01", wait=5)
    assert result["state"] == "failed" and "RATES_NOT_FOUND" in result["reason"]
    assert not any(kind == "notify" for kind, _data in core.received)  # el motivo ya se ve en la ventana
    fake.reject_with = ""
    assert service.buy_label_now("3001", service=ss.AUTO, wait=5)["state"] == "purchased"  # otro intento: se puede
    assert len(fake.purchases) == 2


def test_the_route_tells_which_services_shopify_offers(store):
    service, _fake = store
    route = service.shipping_route("3001")
    assert route.domestic and route.destination == "US" and "usps" in route.offered
    assert "ups_shipping:03" in [s.key for s in ss.choices(route)]


def test_without_read_shipping_the_route_still_says_domestic_or_not(core, monkeypatch):
    def fake(method, url, **kwargs):
        if url.endswith("/admin/oauth/access_token"):
            return net.Response(200, json.dumps({"access_token": "tok", "expires_in": 86399}).encode(), url)
        query = kwargs["json_body"]["query"]
        if "LabelBridgeServices" in query or "location { id }" in query:
            body = {"errors": [{"message": "Access denied for availableCarrierServices field.",
                                "extensions": {"code": "ACCESS_DENIED"}}]}
        else:
            body = {"data": {"order": {"shippingAddress": {"countryCodeV2": "MX"}, "fulfillmentOrders": {"nodes": [
                {"status": "OPEN", "assignedLocation": {"countryCode": "US"}}]}}}}
        return net.Response(200, json.dumps(body).encode(), url)

    monkeypatch.setattr("labelbridge.connectors.shopify.net.request", fake)
    s = core.cfg.data.shopify
    s.enabled, s.shop, s.client_id = True, "mi-tienda", "client-id"
    core.secrets.set(SHOPIFY_CLIENT_SECRET, "secreto")
    service = ShopifyService(core.cfg, core.secrets, core.store, core.engine, core.events)
    route = service.shipping_route("3001")
    assert not route.domestic and route.destination == "MX" and route.offered is None


# --- datos protegidos y permisos opcionales -------------------------------------------------------------

def _client_answering(core, monkeypatch, answers):
    calls = []

    def fake(method, url, **kwargs):
        if url.endswith("/admin/oauth/access_token"):
            return net.Response(200, json.dumps({"access_token": "tok", "scope": "read_orders",
                                                 "expires_in": 86399}).encode(), url)
        query = kwargs["json_body"]["query"]
        calls.append(query)
        return net.Response(200, json.dumps(answers(query)).encode(), url)

    monkeypatch.setattr("labelbridge.connectors.shopify.net.request", fake)
    s = core.cfg.data.shopify
    s.shop, s.client_id = "mi-tienda", "client-id"
    core.secrets.set(SHOPIFY_CLIENT_SECRET, "secreto")
    return ShopifyClient(s, core.secrets), calls


def test_unapproved_customer_fields_arrive_empty_instead_of_failing(core, monkeypatch):
    order = {"id": "gid://shopify/Order/7", "name": "#1023", "email": None, "lineItems": {"nodes": []},
             "customer": {"numberOfOrders": "4", "amountSpent": {"amount": "380.50", "currencyCode": "USD"},
                          "createdAt": "2025-02-01T10:00:00Z"}}

    def answers(_query):
        return {"data": {"order": order}, "errors": [
            {"message": "This app is not approved to access the Order object's email field.", "path": ["order", "email"]}]}

    client, _calls = _client_answering(core, monkeypatch, answers)
    detail = so.ManualOrders(client).detail("gid://shopify/Order/7")
    assert (detail.name, detail.email, detail.orders_count, detail.spent) == ("#1023", "", 4, 380.5)
    with pytest.raises(ShopifyError):  # sin marcar esos campos como opcionales, el aviso sí es un error
        client.graphql(so.ORDER_DETAIL_QUERY_CUSTOMER, {"id": "gid://shopify/Order/7"})


def test_without_read_customers_the_detail_is_asked_without_the_customer(core, monkeypatch):
    def answers(query):
        if "customer {" in query:
            return {"data": None, "errors": [{"message": "Access denied for customer field.",
                                              "extensions": {"code": "ACCESS_DENIED"}}]}
        return {"data": {"order": {"id": "gid://shopify/Order/7", "name": "#1023", "lineItems": {"nodes": []}}}}

    client, calls = _client_answering(core, monkeypatch, answers)
    orders = so.ManualOrders(client)
    assert orders.detail("gid://shopify/Order/7").orders_count == 0
    assert orders.detail("gid://shopify/Order/7").name == "#1023"
    assert len(calls) == 3  # la segunda vez va directo a la consulta que funciona
    client.granted_scopes = "read_orders,read_customers"  # permisos nuevos: se vuelve a probar la completa
    orders.detail("gid://shopify/Order/7")
    assert "customer {" in calls[3]


def test_stock_is_shown_when_the_app_can_read_inventory():
    choices = so.parse_products({"products": {"nodes": [{"title": "Anillo", "status": "ACTIVE", "variants": {"nodes": [
        {"id": "gid://shopify/ProductVariant/1", "title": "7", "inventoryQuantity": 3, "inventoryPolicy": "DENY",
         "inventoryItem": {"tracked": True}},
        {"id": "gid://shopify/ProductVariant/2", "title": "8", "inventoryQuantity": 0, "inventoryPolicy": "CONTINUE",
         "inventoryItem": {"tracked": True}},
        {"id": "gid://shopify/ProductVariant/3", "title": "9", "inventoryQuantity": 0,
         "inventoryItem": {"tracked": False}},
        {"id": "gid://shopify/ProductVariant/4", "title": "10"}]}}]}})
    assert [c.stock for c in choices] == [3, 0, None, None]
    assert choices[0].stock_text == "3 en existencia" and "se puede vender igual" in choices[1].stock_text
    assert choices[2].stock_text == "" and choices[3].stock_text == ""


def test_the_connection_test_lists_the_optional_permissions(core, monkeypatch):
    service = ShopifyService(core.cfg, core.secrets, core.store, core.engine, core.events)
    core.cfg.data.shopify.shop = "mi-tienda"
    monkeypatch.setattr(ShopifyClient, "shop_info", lambda self: {"name": "Demo", "scopes": [
        "write_orders", "write_merchant_managed_fulfillment_orders", "read_products"]})
    old = service._client = object()
    info = service.test_connection()
    assert info["missing"] == [] and isinstance(service._client, ShopifyClient) and service._client is not old
    assert [item.split(" ")[0] for item in info["optional_missing"]] == ["read_inventory", "read_shipping",
                                                                         "read_customers"]


# --- reimprimir y comprar en Shopify ---------------------------------------------------------------------

def _shipping_job(core, label: str, doc: int, order: str = "1601") -> dict:
    job, _ = core.store.add(JobRequest(external_id=f"shipping-{label}-0-{doc}", title=f"Guía {label}-{doc}",
                                       content=b"^XA^XZ", format="zpl", meta={"order_id": order, "tracking": "9200"}),
                            "shopify")
    core.store.set_done(job["id"], "archivo")
    return job


class LabelsInShopify:
    """Las guías del pedido según Shopify (las anuladas incluidas) y sus documentos."""

    def __init__(self, labels: dict, fos: list | None = None) -> None:
        self.labels, self.fos, self.fetched = labels, fos or [], []

    def order_labels(self, _gid):
        return self.labels

    def shipping_label(self, label_id):
        self.fetched.append(label_id)
        return {"id": f"gid://shopify/ShippingLabel/{label_id}", "trackingInfo": {"number": "9400", "company": "USPS"},
                "shippingDocuments": [{"documentType": "LABEL", "format": "PDF", "url": f"https://cdn.example.com/{label_id}.pdf"}]}

    def fulfillment_orders(self, _gid):
        return self.fos


def test_reprint_uses_the_labels_still_active_in_shopify(core, monkeypatch):
    service = ShopifyService(core.cfg, core.secrets, core.store, core.engine, core.events)
    _shipping_job(core, "8", 0)  # la guía 8 se anuló en Shopify: nunca se vuelve a imprimir
    label, customs = _shipping_job(core, "9", 0), _shipping_job(core, "9", 1)
    calls: list = []
    original = core.store.reprint
    monkeypatch.setattr(core.store, "reprint", lambda job_id: calls.append(job_id) or original(job_id))
    shopify = LabelsInShopify({"8": {"voided": True, "at": 1.0}, "9": {"voided": False, "at": 2.0}})
    service.client = lambda: shopify
    assert service.reprint_label("1601", "#1601") == 2
    assert calls == [label["id"], customs["id"]]  # la guía activa con su formulario de aduana
    # Comprada en el admin de Shopify y todavía sin imprimir aquí: se trae de Shopify.
    shopify.labels = {"10": {"voided": False, "at": 3.0}}
    assert service.reprint_label("1700", "#1700") == 1 and shopify.fetched == ["10"]
    assert core.store.get_by_external("shopify", "shipping-10-0-0") is not None
    shopify.labels = {"8": {"voided": True}}
    with pytest.raises(ShopifyError, match="activa"):
        service.reprint_label("1601", "#1601")


def test_choosing_fedex_waits_for_shopify_and_prints_it_even_without_auto_buy(core):
    service = ShopifyService(core.cfg, core.secrets, core.store, core.engine, core.events)
    s = core.cfg.data.shopify
    s.shop, s.buy_shipping_label = "demo.myshopify.com", False
    fo = {"id": "gid://shopify/FulfillmentOrder/71", "status": "OPEN", "deliveryMethod": {"methodType": "SHIPPING"}}
    service.client = lambda: LabelsInShopify({}, [fo])
    service.buy_in_shopify("3001", "#3001")
    assert service.ledger("3001")["state"] == "manual" and service.ledger("3001")["carrier"] == "FedEx"
    order = {"id": "gid://shopify/Order/3001", "legacyResourceId": "3001", "name": "#3001",
             "createdAt": "2020-01-01T00:00:00Z", "displayFinancialStatus": "PAID",
             "displayFulfillmentStatus": "FULFILLED", "tags": [], "lineItems": {"nodes": []},
             "shippingAddress": {"name": "Ana", "address1": "1 Main St", "city": "Miami", "provinceCode": "FL",
                                 "zip": "33101", "countryCodeV2": "US"}}
    service._process_order(object(), s, order, service.since_ts(), threading.Event())
    assert "3001" in service._to_fetch() and service.ledger("3001")["state"] == "resolved"
    # Con una compra hecha o en curso, «comprarla en Shopify» sería una segunda guía: no se marca.
    service.store.mark_seen(service._fo_key(fo))
    with pytest.raises(ShopifyError, match="ya tiene una compra"):
        service.buy_in_shopify("3002", "#3002")
    assert service.ledger("3002") == {}


def test_an_order_waiting_for_fedex_is_never_bought_automatically(store, core):
    service, fake = store
    s = core.cfg.data.shopify
    service.reset_since(hours_back=1)
    service._note("3001", "manual", name="#3001", carrier="FedEx", fetch=1)  # la persona eligió FedEx en Shopify
    service.cycle(service.client(), s)
    assert fake.purchases == [] and service.ledger("3001")["state"] == "manual"


def test_in_progress_purchases_never_offer_another_label():
    from labelbridge.config import ShopifySettings

    s = ShopifySettings()
    s.buy_shipping_label = True
    fedex = make_order(51, subtotal=1500)  # la regla manda FedEx…
    row = row_of(fedex, settings=s, ledger={"state": "purchasing"})  # …pero alguien la está comprando aquí
    assert row.state == "waiting" and row.action == "" and lb.plan_for(row)[0] == lb.SKIP
    row = row_of(fedex, settings=s, ledger={"state": "uncertain"})
    assert row.action == "open" and row.status == "Compra sin confirmar"
    fresh = row_of(make_order(52, created=NOW - 60), settings=s)  # la compra automática la está comprando
    assert row.issue != "auto" and fresh.issue == "auto" and lb.plan_for(fresh)[0] == lb.SKIP


def test_unknown_or_fedex_services_are_refused_before_buying(store):
    service, fake = store
    with pytest.raises(ShopifyError, match="desconocido"):
        service.buy_label_now("3001", service="usps:Teleport", wait=5)
    with pytest.raises(ShopifyError, match="FedEx"):
        service.buy_label_now("3001", service=ss.FEDEX, wait=5)
    assert fake.purchases == []


# --- qué hace «Imprimir guía» con cada pedido -------------------------------------------------------------

def test_the_print_button_knows_what_each_order_needs():
    assert lb.plan_for(row_of(make_order(1, labels=(("2", "5,68"),), fulfillments=(usps(),),
                                         fulfillment="FULFILLED")))[0] == lb.REPRINT
    assert lb.plan_for(row_of(make_order(2)))[0] == lb.BUY
    assert lb.plan_for(row_of(make_order(3, subtotal=1500)))[0] == lb.IN_SHOPIFY  # FedEx: en Shopify
    plan, why = lb.plan_for(row_of(make_order(4, delivery="PICK_UP")))
    assert plan == lb.SKIP and "retiro" in why
    assert lb.plan_for(row_of(make_order(5, cancelled="2026-10-01T00:00:00Z")))[0] == lb.SKIP
    plan, why = lb.plan_for(row_of(make_order(6, fulfillments=(usps(),), fulfillment="FULFILLED")))
    assert plan == lb.SKIP and "9200190267338800106038" in why  # enviado con otra guía
    unpaid = row_of(make_order(7, financial="PENDING"))
    assert lb.plan_for(unpaid)[0] == lb.BUY and lb.plan_for(unpaid, batch=True)[0] == lb.SKIP
    doubtful = row_of(make_order(8), ledger={"state": "uncertain"})
    assert lb.plan_for(doubtful)[0] == lb.SKIP and "sin confirmar" in lb.plan_for(doubtful)[1]
    void = row_of(make_order(9, labels=(("3", "5,68"),), cancelled="2026-10-01T00:00:00Z"))
    plan, why = lb.plan_for(void)  # cancelado con la guía activa: no se reimprime, se anula
    assert plan == lb.SKIP and "anúlala" in why


class FakeService:
    def __init__(self) -> None:
        self.bought: list[tuple[str, dict]] = []
        self.reprinted: list[str] = []
        self.answers: dict[str, dict | Exception] = {}

    def reprint_label(self, legacy, name):
        self.reprinted.append(legacy)
        return 1

    def buy_label_now(self, legacy, **options):
        self.bought.append((legacy, options))
        answer = self.answers.get(legacy, {"state": "purchased", "carrier": "USPS", "tracking": f"9400{legacy}",
                                           "cost": 5.25})
        if isinstance(answer, Exception):
            raise answer
        return answer


def _rows():
    return [
        row_of(make_order(11, labels=(("2", "5,68"),), fulfillments=(usps(),), fulfillment="FULFILLED")),
        row_of(make_order(12)),
        row_of(make_order(13)),
        row_of(make_order(14, subtotal=1500)),
        row_of(make_order(15)),
    ]


def test_several_orders_are_printed_with_one_confirmation():
    fake = FakeService()
    fake.answers["13"] = {"state": "failed", "reason": "RATES_NOT_FOUND No rates"}
    fake.answers["15"] = ShopifyError("Ese pedido no tiene productos pendientes de envío.")
    items = lb.make_batch(_rows())
    assert [item.plan for item in items] == [lb.REPRINT, lb.BUY, lb.BUY, lb.IN_SHOPIFY, lb.BUY]
    assert lb.purchases(items) == 3
    seen: list[tuple[int, str]] = []
    options = lb.BatchOptions(service="usps:Priority", notify=True)
    summary = lb.run_batch(fake, items, options, progress=lambda index, item: seen.append((index, item.state)))
    assert fake.reprinted == ["11"] and [legacy for legacy, _o in fake.bought] == ["12", "13", "15"]
    assert fake.bought[0][1] == {"package": None, "total_weight": None, "notify": True, "service": "usps:Priority"}
    assert (summary.bought, summary.reprinted, summary.failed, summary.skipped) == (1, 1, 2, 1)
    assert summary.cost == 5.25 and items[1].detail == "USPS 940012"
    assert "USPS Priority Mail" in items[2].detail and "No se cobró nada" in items[2].detail
    assert items[3].state == "skipped" and "Shopify" in items[3].detail
    assert items[4].state == "failed" and len(summary.problems) == 2
    assert (1, "working") in seen and (1, "done") in seen


def test_a_batch_can_be_stopped_and_has_a_limit():
    fake = FakeService()
    stop = threading.Event()
    items = lb.make_batch(_rows())

    def progress(index, item):
        if item.state == "done" and index == 1:
            stop.set()  # «Detener» mientras compraba la segunda: termina esa y no sigue

    summary = lb.run_batch(fake, items, lb.BatchOptions(), progress=progress, stop=stop)
    assert summary.stopped and [legacy for legacy, _o in fake.bought] == ["12"] and items[2].state == "waiting"
    many = [lb.BatchItem(row_of(make_order(100 + n)), lb.BUY) for n in range(lb.MAX_BATCH + 1)]
    with pytest.raises(ShopifyError, match=str(lb.MAX_BATCH)):
        lb.run_batch(fake, many, lb.BatchOptions())


def test_rows_know_how_they_ship():
    row = row_of(make_order(21, delivery="PICK_UP"))
    assert (row.kind, row.shipped, row.paid) == ("pickup", False, True)
    row = row_of(make_order(22, financial="PENDING", fulfillments=(usps(created=NOW - 60),), fulfillment="FULFILLED"))
    assert (row.kind, row.shipped, row.paid) == ("ship", True, False)


# --- pantallas -------------------------------------------------------------------------------------------

def _page_with_rows(core, monkeypatch, rows):
    from conftest import wait_for
    from PySide6.QtWidgets import QApplication
    from test_orders import _ui_context

    from labelbridge.ui.pages import orders as orders_page

    ctx = _ui_context(core)
    s = core.cfg.data.shopify
    s.enabled, s.shop = True, "mi-tienda"
    page = orders_page.OrdersPage(ctx)
    monkeypatch.setattr(ctx.services.shopify, "overview", lambda days=30: rows)
    page.reload()
    assert wait_for(lambda: (QApplication.processEvents(), bool(page.all_rows))[1])
    return page, ctx


def test_the_print_button_follows_the_selection(core, monkeypatch):
    from PySide6.QtCore import QItemSelectionModel

    from labelbridge.ui.pages import orders as orders_page

    rows = _rows() + [row_of(make_order(16, delivery="PICK_UP"))]
    page, _ctx = _page_with_rows(core, monkeypatch, rows)
    assert not page.print_btn.isEnabled()  # nada elegido
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    page.view.selectionModel().select(page.proxy.index(0, 0), flags)
    assert page.print_btn.text() == "Imprimir guía" and page.details_btn.isEnabled()
    page.view.selectionModel().select(page.proxy.index(1, 0), flags)
    assert page.print_btn.text() == "Imprimir 2 guías" and not page.details_btn.isEnabled()
    told: list[str] = []
    monkeypatch.setattr(orders_page, "info", lambda _parent, _title, text: told.append(text))
    pickup = next(row for row in rows if row.kind == "pickup")
    page.run_action(pickup, "print")
    assert "retiro en tienda" in told[-1]  # sin guía: explica por qué


def test_print_dialog_offers_services_and_fedex_in_shopify(core, monkeypatch):
    from conftest import wait_for
    from PySide6.QtWidgets import QApplication
    from test_orders import _ui_context

    from labelbridge.ui.purchase_dialog import PurchaseDialog

    ctx = _ui_context(core)
    core.cfg.data.shopify.shop = "mi-tienda"
    shopify = ctx.services.shopify
    offered = ss.offered_services(STORE_SERVICES)
    monkeypatch.setattr(shopify, "shipping_route", lambda legacy: ss.Route(True, "US", offered))
    opened: list = []
    monkeypatch.setattr("labelbridge.ui.purchase_dialog.QDesktopServices.openUrl", lambda url: opened.append(url))
    fedex_row = row_of(make_order(31, subtotal=1500))
    dialog = PurchaseDialog(None, ctx, fedex_row)
    assert wait_for(lambda: (QApplication.processEvents(), not dialog.services.loading)[1])
    assert dialog.services.key() == ss.FEDEX and dialog.buy_btn.text() == "Comprar en Shopify"
    keys = [dialog.services.combo.itemData(i) for i in range(dialog.services.combo.count())]
    assert "ups_shipping:03" in keys and "dhl_express:P" not in keys  # solo lo que Shopify ofrece
    dialog.buy_btn.click()  # FedEx: la ventana «Guía FedEx» se encarga (quien abrió esta la abre)
    assert dialog.fedex_requested and dialog.result() == 1 and opened == []
    dialog.close()

    bought: list = []
    monkeypatch.setattr(shopify, "buy_label_now", lambda legacy, **options: bought.append((legacy, options)) or {
        "state": "purchased", "carrier": "USPS", "tracking": "9400", "cost": 5.34})
    dialog = PurchaseDialog(None, ctx, row_of(make_order(32)))
    assert wait_for(lambda: (QApplication.processEvents(), not dialog.services.loading)[1])
    dialog.services.combo.setCurrentIndex(dialog.services.combo.findData("usps:Priority"))
    assert dialog.buy_btn.text() == "Comprar e imprimir"
    dialog.buy_btn.click()
    assert wait_for(lambda: (QApplication.processEvents(), dialog.bought)[1])
    assert bought[0][0] == "32" and bought[0][1]["service"] == "usps:Priority"
    assert "US$5.34" in dialog.statuses["Hint"].text()
    dialog.close()


def test_bulk_dialog_buys_and_reprints_in_one_go(core, monkeypatch):
    from conftest import wait_for
    from PySide6.QtWidgets import QApplication
    from test_orders import _ui_context

    from labelbridge.ui.bulk_print_dialog import BulkPrintDialog

    ctx = _ui_context(core)
    fake = FakeService()
    monkeypatch.setattr(ctx.services.shopify, "reprint_label", fake.reprint_label)
    monkeypatch.setattr(ctx.services.shopify, "buy_label_now", fake.buy_label_now)
    dialog = BulkPrintDialog(None, ctx, _rows())
    assert dialog.go_btn.text() == "Comprar 3 y reimprimir 1"
    dialog.go_btn.click()
    assert wait_for(lambda: (QApplication.processEvents(), not dialog.running)[1])
    assert fake.reprinted == ["11"] and len(fake.bought) == 3 and dialog.finished_any
    assert "3 comprada(s)" in dialog.summary.text() and "US$15.75" in dialog.summary.text()
    assert dialog.table.item(1, 3).text().startswith("✓ USPS")
    dialog.close()


# --- solo pedidos que se envían, con a quién y adónde mandarlos ----------------------------------------

def test_only_orders_that_ship_to_a_full_address_can_take_a_label():
    from labelbridge.connectors.orders_overview import shipping_problem

    full = {"name": "Ana", "address1": "1 Main St", "city": "Miami", "provinceCode": "FL", "zip": "33101",
            "countryCodeV2": "US"}
    assert shipping_problem({"requiresShipping": True, "shippingAddress": full}) == ""
    # Tal como llegan en una tienda real: retiro en tienda y venta en el local (sin dirección) y un pedido de Etsy.
    assert "dirección" in shipping_problem({"requiresShipping": True, "shippingAddress": None})
    assert shipping_problem({"requiresShipping": False, "shippingAddress": full}) == "no requiere envío"
    assert "código postal" in shipping_problem({"shippingAddress": {**full, "zip": " "}})
    assert "estado" in shipping_problem({"shippingAddress": {**full, "provinceCode": ""}})
    assert "nombre" in shipping_problem({"shippingAddress": {**full, "name": ""}})
    assert shipping_problem({"shippingAddress": {**full, "name": "", "company": "Joyería Oyá"}}) == ""
    assert shipping_problem({"shippingAddress": {**full, "countryCodeV2": "PR", "provinceCode": "", "zip": "00623"}}) == ""
    assert shipping_problem({"shippingAddress": {**full, "countryCodeV2": "HK", "provinceCode": "", "zip": ""}}) == ""


def test_the_orders_tab_only_has_orders_that_ship(core, monkeypatch):
    def node(number, **changes):
        order = make_order(number, **{k: v for k, v in changes.items() if k in ("delivery", "cancelled", "labels")})
        if "address" in changes:
            order["shippingAddress"] = changes["address"]
        return order

    nodes = [
        node(41),  # envío normal
        node(42, delivery="PICK_UP", address=None),  # retiro en tienda
        node(43, delivery="RETAIL", address=None),  # venta en el local
        node(44, address=None),  # «envío» sin dirección (p. ej. un pedido de Etsy)
        node(45, cancelled="2026-10-01T00:00:00Z"),  # cancelado sin guía
        node(46, cancelled="2026-10-01T00:00:00Z", labels=(("9", "5,68"),)),  # cancelado con guía activa: anularla
    ]

    def fake(method, url, **kwargs):
        if url.endswith("/admin/oauth/access_token"):
            return net.Response(200, json.dumps({"access_token": "tok", "expires_in": 86399}).encode(), url)
        body = {"data": {"orders": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}
        return net.Response(200, json.dumps(body).encode(), url)

    monkeypatch.setattr("labelbridge.connectors.shopify.net.request", fake)
    s = core.cfg.data.shopify
    s.enabled, s.shop, s.client_id = True, "mi-tienda", "client-id"
    core.secrets.set(SHOPIFY_CLIENT_SECRET, "secreto")
    service = ShopifyService(core.cfg, core.secrets, core.store, core.engine, core.events)
    rows = service.overview(days=30)
    assert sorted(row.name for row in rows) == ["#41#", "#46#"]
    assert next(row for row in rows if row.name == "#46#").action == "void"


def test_no_label_is_ever_bought_without_an_address(store, core):
    service, fake = store
    original = fake.order

    def without_address(number, created, **extra):
        order = original(number, created, **extra)
        order["shippingAddress"] = None  # como un pedido de otro canal sin dirección
        return order

    fake.order = without_address
    with pytest.raises(ShopifyError, match="no puede llevar guía"):
        service.buy_label_now("3001", wait=5)
    s = core.cfg.data.shopify
    s.print_order_label = True
    service.reset_since(hours_back=1)
    service.cycle(service.client(), s)
    assert fake.purchases == [] and core.store.list() == []  # ni guía, ni etiqueta interna
    assert not any(data.get("kind") == "new_order" for kind, data in core.received if kind == "notify")
    plan, why = lb.plan_for(_no_address_row())
    assert plan == lb.SKIP and "código postal" in why


def _no_address_row():
    order = make_order(48)
    order["shippingAddress"] = {**order["shippingAddress"], "zip": ""}
    return row_of(order)


def test_an_order_partly_shipped_and_partly_picked_up_still_takes_its_label():
    order = make_order(49)
    order["fulfillmentOrders"] = {"nodes": [{"status": "OPEN", "deliveryMethod": {"methodType": "PICK_UP"}},
                                            {"status": "OPEN", "deliveryMethod": {"methodType": "SHIPPING"}}]}
    row = row_of(order)
    assert row.kind == "ship" and lb.plan_for(row)[0] == lb.BUY


def test_the_chosen_service_never_changes_by_itself(core):
    from test_orders import _ui_context

    from labelbridge.ui.purchase_dialog import PurchaseDialog

    ctx = _ui_context(core)
    dialog = PurchaseDialog(None, ctx, row_of(make_order(61)))
    dialog.services.combo.setCurrentIndex(dialog.services.combo.findData("ups_shipping:01"))
    only_usps = ss.Route(True, "US", {"usps": {"ground advantage", "priority mail"}})
    dialog.services.fill(only_usps)  # Shopify contesta después: UPS no se ofrece para este envío
    assert dialog.services.key() == "ups_shipping:01" and not dialog.services.available()
    assert not dialog.buy_btn.isEnabled() and "no ofrece" in dialog.services.hint.text()
    dialog.services.combo.setCurrentIndex(dialog.services.combo.findData("usps:Priority"))
    assert dialog.buy_btn.isEnabled()
    dialog.close()


def test_a_batch_leaves_out_what_auto_buy_got_first_and_marks_fedex():
    from labelbridge.connectors.shopify import ShopifyError as Error

    class Racing(FakeService):
        marked: list = []

        def buy_label_now(self, legacy, **options):
            raise Error("Ese pedido ya tiene una compra hecha o sin confirmar")

        def ledger(self, legacy):
            return {"state": "purchased"}

        def buy_in_shopify(self, legacy, name, carrier):
            self.marked.append((legacy, carrier))

    fake = Racing()
    items = lb.make_batch([row_of(make_order(71)), row_of(make_order(72, subtotal=1500))])
    summary = lb.run_batch(fake, items, lb.BatchOptions())
    assert (summary.bought, summary.failed, summary.skipped) == (0, 0, 2)
    assert "compra automática" in items[0].detail and fake.marked == [("72", "FedEx")]


def test_a_purchase_waits_only_for_its_own_result(core):
    import time as clock

    from test_purchase_safety import FakeClient, fo

    service = ShopifyService(core.cfg, core.secrets, core.store, core.engine, core.events)
    s = core.cfg.data.shopify
    s.enabled, s.shop = True, "demo.myshopify.com"
    fake = FakeClient([fo(9)])
    fake.results = {
        "stuck": {"status": "PENDING"},  # otra compra que Shopify todavía no termina
        "gid://shopify/ShippingLabelPurchaseResult/1": {"status": "PURCHASED", "errors": [], "shippingLabels": [{
            "id": "gid://shopify/ShippingLabel/5", "trackingInfo": {"number": "9200", "company": "USPS"},
            "shippingDocuments": [{"documentType": "LABEL", "format": "PDF", "url": "https://cdn.example.com/5.pdf"}]}]},
    }
    fake.order_labels = lambda _gid: {}
    service.client = lambda: fake
    service._save_pending({"stuck": {"order_id": "2", "order_name": "#1002", "started": clock.time()}})
    started = clock.monotonic()
    result = service.buy_label_now("1", wait=30)
    assert result["state"] == "purchased" and clock.monotonic() - started < 10
    assert "stuck" in service._pending()  # la otra sigue esperando su vuelta del ciclo
