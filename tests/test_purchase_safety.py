"""Compras de guías (dinero real): nunca dos veces la misma, ni con «Comprar guía…» a la vez que el ciclo automático."""

from __future__ import annotations

import threading
import time

import pytest

from labelbridge.connectors.shopify import ShopifyError, ShopifyService

ORDER = {"id": "gid://shopify/Order/1", "legacyResourceId": "1", "name": "#1001",
         "shippingAddress": {"name": "Ana López", "address1": "123 Main St", "city": "Miami", "provinceCode": "FL",
                             "zip": "33101", "countryCodeV2": "US"}}


def fo(number: int) -> dict:
    return {"id": f"gid://shopify/FulfillmentOrder/{number}", "status": "OPEN", "deliveryMethod": {"methodType": "SHIPPING"}}


class FakeClient:
    """Shopify simulado: cuenta las compras y puede quedarse «a mitad» de una."""

    shop = "demo.myshopify.com"

    def __init__(self, fulfillment_orders: list[dict]) -> None:
        self.fos = fulfillment_orders
        self.purchases: list[str] = []
        self.in_purchase, self.release = threading.Event(), threading.Event()
        self.hold_first = False
        self.results: dict[str, dict] = {}

    def fulfillment_orders(self, _gid):
        return [dict(f) for f in self.fos]

    def order(self, _legacy, _ns, _key):
        return dict(ORDER)

    def purchase_label(self, fo_id, notify, package=None, total_weight=None, preferred=None):
        self.purchases.append(fo_id)
        if self.hold_first and len(self.purchases) == 1:
            self.in_purchase.set()
            self.release.wait(5)  # la compra del ciclo sigue en camino
        return f"gid://shopify/ShippingLabelPurchaseResult/{len(self.purchases)}"

    def label_result(self, result_id):
        outcome = self.results.get(result_id, {"status": "PENDING"})
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def service(core):
    s = core.cfg.data.shopify
    s.enabled, s.shop, s.buy_shipping_label = True, "demo.myshopify.com", True
    svc = ShopifyService(core.cfg, core.secrets, core.store, core.engine, core.events)
    return svc


META = {"order_gid": ORDER["id"], "order_name": "#1001", "order_id": "1"}


def test_buying_by_hand_while_the_cycle_buys_never_pays_twice(service, core):
    fake = FakeClient([fo(9)])
    fake.hold_first = True
    service.client = lambda: fake

    def cycle():
        with service._cycle_lock:
            service._buy_labels(fake, core.cfg.data.shopify, ORDER, META, threading.Event(), wait=0)

    outcome: dict = {}

    def click():
        try:
            outcome["ok"] = service.buy_label_now("1", wait=0)
        except ShopifyError as exc:
            outcome["error"] = str(exc)

    automatic = threading.Thread(target=cycle)
    automatic.start()
    assert fake.in_purchase.wait(5)
    manual = threading.Thread(target=click)
    manual.start()
    time.sleep(0.3)  # «Comprar guía…» espera al ciclo
    fake.release.set()
    automatic.join(5)
    manual.join(5)
    assert fake.purchases == ["gid://shopify/FulfillmentOrder/9"]  # una sola compra
    assert "en curso" in outcome["error"]


def test_an_unconfirmed_purchase_is_never_bought_again_by_hand(service):
    fake = FakeClient([fo(77), fo(78)])
    service.client = lambda: fake
    service.store.mark_seen(service._fo_key(fo(77)))  # compra dudosa de la 77 (la marca queda puesta)
    service._note("1", "failed", name="#1001", reason="la 78 falló")  # el registro del pedido dice «failed»
    result = service.buy_label_now("1", wait=0)
    assert fake.purchases == ["gid://shopify/FulfillmentOrder/78"] and result["state"] == "purchasing"
    with pytest.raises(ShopifyError):  # ahora las dos tienen la marca: nada que comprar
        service.buy_label_now("1", wait=0)


def test_a_broken_pending_purchase_does_not_block_the_others(service, core):
    fake = FakeClient([fo(9)])
    fake.results = {
        "bad": ShopifyError("HTTP 500", uncertain=True),
        "good": {"status": "PURCHASED", "errors": [], "shippingLabels": [{
            "id": "gid://shopify/ShippingLabel/5", "trackingInfo": {"number": "9200", "company": "USPS", "url": None},
            "shippingDocuments": [{"documentType": "LABEL", "format": "PDF", "url": "https://cdn.example.com/5.pdf"}]}]},
    }
    service._save_pending({
        "bad": {**META, "order_id": "2", "order_name": "#1002", "started": time.time() - 4000},
        "good": {**META, "started": time.time()},
    })
    assert service._check_pending_purchases(fake) == 1  # la buena se imprime
    assert service._pending() == {}  # la mala, tras una hora sin respuesta, queda «sin confirmar»
    assert service.ledger("2")["state"] == "uncertain" and service.ledger("1")["state"] == "purchased"


def test_a_failed_purchase_can_be_bought_again(service):
    fake = FakeClient([fo(9)])
    service.client = lambda: fake
    with service._cycle_lock:
        service._buy_labels(fake, service.cfg.data.shopify, ORDER, META, threading.Event(), wait=0)
    assert service.store.seen(service._fo_key(fo(9)))
    fake.results = {"gid://shopify/ShippingLabelPurchaseResult/1": {
        "status": "PURCHASE_FAILED", "errors": [{"code": "RATES_NOT_FOUND", "message": "no rates"}]}}
    service._check_pending_purchases(fake)
    assert service.ledger("1")["state"] == "failed" and not service.store.seen(service._fo_key(fo(9)))
    service.buy_label_now("1", wait=0)  # Shopify no la compró: la persona puede comprarla
    assert len(fake.purchases) == 2


def test_an_unexpected_error_mid_purchase_counts_as_unconfirmed(service):
    fake = FakeClient([fo(9)])

    def explode(*_args, **_kwargs):
        raise ValueError("respuesta rara")

    fake.purchase_label = explode
    with service._cycle_lock:
        service._buy_labels(fake, service.cfg.data.shopify, ORDER, META, threading.Event(), wait=0)
    assert service.ledger("1")["state"] == "uncertain" and service.store.seen(service._fo_key(fo(9)))


def test_reprocess_during_a_cycle_is_kept(service, monkeypatch):
    class Client(FakeClient):
        def orders_updated_since(self, *_args, **_kwargs):
            service.reset_since(hours_back=48)  # «Reprocesar» mientras la vuelta está en curso
            return [], None

    service.reset_since()
    reset_at = service.store.kv_get(service._key("cursor"))
    service.cycle(Client([]), service.cfg.data.shopify)
    assert service.store.kv_get(service._key("cursor")) != reset_at  # quedó el cursor de «Reprocesar» (48 h)
    assert service.store.kv_get(service._key("cursor")) < reset_at


def test_truncated_http_responses_become_network_errors(monkeypatch):
    import http.client

    from labelbridge import net

    class Opener:
        def open(self, *_args, **_kwargs):
            raise http.client.IncompleteRead(b"par")

    monkeypatch.setattr(net, "_get_opener", lambda guard=None: Opener())
    with pytest.raises(net.NetworkError) as caught:
        net.request("POST", "https://demo.myshopify.com/admin/api/2026-10/graphql.json", json_body={})
    assert caught.value.maybe_sent  # pudo haberse procesado: la compra queda «sin confirmar», nunca se repite


def test_orders_tab_falls_back_without_fulfillment_permission_in_english(service, monkeypatch):
    from labelbridge import i18n
    from labelbridge.connectors.orders_overview import OVERVIEW_QUERY_BASIC
    from labelbridge.connectors.shopify import ShopifyClient

    asked = []

    def graphql(self, query, variables=None, **_kwargs):
        asked.append(query)
        if query is not OVERVIEW_QUERY_BASIC:
            raise ShopifyError("Access denied for fulfillmentOrders" + i18n.tr(" (faltan permisos/scopes en la app de Shopify)"),
                               code="access_denied")
        return {"orders": {"pageInfo": {"hasNextPage": False}, "nodes": [dict(ORDER)]}}

    monkeypatch.setattr(ShopifyClient, "graphql", graphql)
    client = ShopifyClient(service.cfg.data.shopify, service.secrets)
    i18n.set_language("en")
    try:
        orders, truncated = client.orders_overview("2026-09-01T00:00:00Z")
    finally:
        i18n.set_language("es")
    assert [o["name"] for o in orders] == ["#1001"] and not truncated and asked[-1] is OVERVIEW_QUERY_BASIC
