"""FedEx por Shopify Flow: un flujo de la tienda compra la guía FedEx (ninguna app puede por la API) y LabelBridge espera
sin pedirle a nadie que la compre, la imprime al aparecer (o abre el pedido en Shopify si Shopify no le da el PDF, como
hacía la PC con el sistema de la web) y avisa si Flow no la compró."""

from __future__ import annotations

import threading
import time

from PySide6.QtWidgets import QDialog, QLabel, QPushButton
from test_orders import NOW, SINCE, _ui_context, make_order, row_of, settings
from test_print_labels import LabelsInShopify

from labelbridge import events as ev
from labelbridge.config import ShippingRule
from labelbridge.connectors import label_batch as lb
from labelbridge.connectors.orders_overview import FLOW_WAIT_SECONDS
from labelbridge.connectors.shopify import ShopifyService


class FlowShopify(LabelsInShopify):
    """Shopify con la FedEx que compró Flow: sus guías, si entrega el PDF, el estado del pedido y si ya se imprimió."""

    def __init__(self, labels: dict | None = None, *, pdf: bool = True, status: str = "UNFULFILLED") -> None:
        super().__init__(labels or {})
        self.pdf, self.status, self.printed = pdf, status, False

    def shipping_label(self, label_id):
        label = super().shipping_label(label_id)
        if not self.pdf:  # Shopify solo le da el PDF a quien la compró
            label["shippingDocuments"] = [{"documentType": "LABEL", "format": "PDF", "url": None}]
        return label

    def graphql(self, query, variables=None, **_kwargs):
        if "LabelBridgeLabelPrinted" in query:
            return {"shippingLabel": {"id": variables["id"], "printed": self.printed}}
        if "LabelBridgeFulfillments" in query:
            return {"order": {"id": variables["id"], "displayFulfillmentStatus": self.status}}
        raise AssertionError(query)


def _service(core, shopify: FlowShopify, *, auto: bool = True):
    service = ShopifyService(core.cfg, core.secrets, core.store, core.engine, core.events)
    s = core.cfg.data.shopify
    s.shop, s.buy_shipping_label, s.fedex_by_flow = "demo.myshopify.com", auto, True
    service.client = lambda: shopify
    bought: list = []
    service._buy_labels = lambda *args, **kwargs: bought.append(args) or 0  # la compra por la API (USPS…)
    seen: list = []
    core.events.subscribe(lambda kind, data: seen.append((kind, data)))
    return service, bought, seen


def _process(service, shopify, order) -> None:
    service._process_order(shopify, service.cfg.data.shopify, order, SINCE, threading.Event())


def _notices(seen) -> list[dict]:
    return [data for kind, data in seen if kind == ev.NOTIFY]


def test_flow_buys_the_fedex_so_nobody_is_asked_to_and_it_prints_when_it_appears(core):
    shopify = FlowShopify()
    service, bought, seen = _service(core, shopify)
    _process(service, shopify, make_order(91, subtotal=1500))
    entry = service.ledger("91")
    assert entry["state"] == "flow" and entry["carrier"] == "FedEx" and entry["fetch"] == 1
    assert bought == [] and _notices(seen) == []  # ni compra por la API ni «cómprala en Shopify»
    _process(service, shopify, make_order(91, subtotal=1500))  # otra vuelta igual: nada cambia
    assert service.ledger("91")["state"] == "flow" and bought == []
    # Flow la compró: el pedido llega preparado con su guía, que se trae y se imprime sola.
    shopify.labels = {"7001": {"voided": False, "at": NOW}}
    _process(service, shopify, make_order(91, subtotal=1500, fulfillment="FULFILLED", labels=(("7001", 31.5),)))
    assert service.ledger("91")["state"] == "resolved" and service._to_fetch()["91"]["flow"] == 1
    assert service._fetch_bought_labels(shopify) == 1
    assert core.store.get_by_external("shopify", "shipping-7001-0-0") is not None and bought == []


def test_when_flow_is_faster_than_labelbridge_the_label_still_prints_once(core):
    shopify = FlowShopify({"7002": {"voided": False, "at": NOW}})
    service, bought, _seen = _service(core, shopify)
    done = make_order(92, subtotal=2000, fulfillment="FULFILLED", labels=(("7002", 31.5),))
    _process(service, shopify, done)  # la primera vez que LabelBridge lo ve, ya está preparado
    assert service._to_fetch()["92"]["flow"] == 1 and service.ledger("92")["state"] == "resolved"
    assert service._fetch_bought_labels(shopify) == 1
    _process(service, shopify, done)  # otra actualización del pedido: no se vuelve a traer
    assert "92" not in service._to_fetch() and bought == []


def test_without_the_pdf_the_order_opens_in_shopify_and_clears_once_printed_there(core):
    shopify = FlowShopify({"7003": {"voided": False, "at": NOW}}, pdf=False)
    service, _bought, seen = _service(core, shopify)
    done = make_order(93, subtotal=1500, fulfillment="FULFILLED", labels=(("7003", 31.5),))
    _process(service, shopify, done)
    service._fetch_bought_labels(shopify)
    entry = service.ledger("93")
    assert entry["state"] == "no_document" and entry["flow"] == 1
    assert [data["url"] for kind, data in seen if kind == ev.OPEN_URL] == ["https://admin.shopify.com/store/demo/orders/93"]
    notice = _notices(seen)[-1]
    assert notice["title"] == "Guía FedEx de #93# lista" and "Imprimir etiqueta" in notice["message"]
    flow = settings(fedex_by_flow=True)
    row = row_of(done, settings=flow, ledger=entry)
    assert row.action == "open" and row.status == "Imprimir guía FedEx en Shopify"
    service._check_flow_prints(shopify)  # todavía no se imprimió en Shopify
    assert service.ledger("93")["state"] == "no_document"
    shopify.printed = True
    core.store.kv_set(service._key("flow_prints_at"), "0")  # sin esperar los 2 min entre revisiones
    service._check_flow_prints(shopify)
    entry = service.ledger("93")
    assert entry["state"] == "resolved"
    assert row_of(done, settings=flow, ledger=entry).status == "Guía impresa desde Shopify"


def test_if_flow_does_not_buy_it_a_person_is_asked_after_15_minutes(core):
    shopify = FlowShopify(status="UNFULFILLED")
    service, _bought, seen = _service(core, shopify)
    service._note("94", "flow", name="#94#", carrier="FedEx", fetch=1, created=time.time() - 60)
    service._check_flow_waits(shopify)
    assert service.ledger("94")["state"] == "flow" and _notices(seen) == []  # todavía le toca a Flow
    service._note("94", "flow", name="#94#", carrier="FedEx", fetch=1, created=time.time() - FLOW_WAIT_SECONDS - 5)
    service._check_flow_waits(shopify)
    entry = service.ledger("94")
    assert entry["state"] == "manual" and entry["flow_late"] == 1 and entry["fetch"] == 1
    notice = _notices(seen)[-1]
    assert notice["title"] == "Pedido #94#: Shopify Flow no compró la FedEx" and notice["kind"] == "action"
    row = row_of(make_order(94, subtotal=1500), settings=settings(fedex_by_flow=True), ledger=entry)
    assert row.action == "buy" and row.reason.startswith("Shopify Flow no la compró en 15 min.")
    # Flow sí la compró, pero la vuelta no lo vio: se trae igual.
    service._note("95", "flow", name="#95#", carrier="FedEx", fetch=1, created=time.time() - FLOW_WAIT_SECONDS - 5)
    shopify.status = "FULFILLED"
    service._check_flow_waits(shopify)
    assert service.ledger("95")["state"] == "resolved" and service._to_fetch()["95"]["flow"] == 1


def test_flow_fedex_works_even_with_labelbridge_auto_buy_off(core):
    shopify = FlowShopify()
    service, bought, _seen = _service(core, shopify, auto=False)
    _process(service, shopify, make_order(96, subtotal=1500))
    assert service.ledger("96")["state"] == "flow" and bought == []


def test_without_flow_fedex_still_asks_a_person_and_usps_is_still_bought(core):
    shopify = FlowShopify()
    service, bought, seen = _service(core, shopify)
    core.cfg.data.shopify.fedex_by_flow = False
    _process(service, shopify, make_order(97, subtotal=1500))
    assert service.ledger("97")["state"] == "manual" and "compra la guía de FedEx" in _notices(seen)[-1]["title"]
    core.cfg.data.shopify.fedex_by_flow = True
    _process(service, shopify, make_order(98, subtotal=200))  # menos de US$1000: USPS, la compra LabelBridge
    assert len(bought) == 1 and service.ledger("98") == {}


def test_orders_show_that_flow_is_buying_and_never_offer_another_label():
    flow = settings(fedex_by_flow=True)
    fresh = make_order(101, subtotal=1500, created=NOW - 120)
    row = row_of(fresh, settings=flow)
    assert row.state == "waiting" and row.issue == "flow" and row.action == ""
    assert row.status == "Shopify Flow está comprando la guía FedEx…"
    assert lb.plan_for(row) == (lb.SKIP, "Shopify Flow la está comprando en este momento.")
    assert row_of(fresh, settings=flow, ledger={"state": "flow"}).issue == "flow"
    late = make_order(102, subtotal=1500, created=NOW - FLOW_WAIT_SECONDS - 60)
    assert row_of(late, settings=flow).action == "buy"  # ya pasó el tiempo de Flow: «Comprar guía FedEx»
    assert row_of(fresh, settings=settings()).action == "buy"  # sin Flow: la ventana «Guía FedEx», como siempre
    assert row_of(make_order(103, subtotal=200, created=NOW - 120), settings=flow).issue != "flow"


def test_the_flow_guide_uses_the_labelbridge_rule_and_service(core, monkeypatch):
    from labelbridge.ui import flow_setup_dialog as fsd

    rule = fsd.fedex_rule([ShippingRule("", "USPS", True, 0.0), ShippingRule("", "FedEx", False, 1000.0)])
    assert rule is not None and rule.over == 1000.0
    conditions = fsd.flow_conditions(rule)
    assert conditions[0] == ("fulfillmentOrder / deliveryMethod / methodType", "es igual a", "SHIPPING")
    assert ("fulfillmentOrder / order / currentSubtotalPriceSet / shopMoney / amount", "es mayor que", "1000") \
        in conditions
    assert conditions[-1] == ("fulfillmentOrder / order / tags", "no incluye", "LabelBridge-manual")
    express = fsd.flow_conditions(ShippingRule("Express", "FedEx", False, 0.0))
    assert ("fulfillmentOrder / order / shippingLine / title", "contiene", "Express") in express
    opened: list = []
    monkeypatch.setattr(fsd.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    dialog = fsd.FlowSetupDialog(None, "demo.myshopify.com", rule, "FedEx 2Day®")
    next(b for b in dialog.findChildren(QPushButton) if b.text() == "Abrir Shopify Flow").click()
    assert opened == ["https://admin.shopify.com/store/demo/apps/flow"]
    texts = " ".join(item.text() for item in dialog.findChildren(QLabel))
    assert "FedEx 2Day®" in texts and "Fulfillment order ready to fulfill" in texts and "Purchase shipping label" in texts
    dialog.close()


def test_choosing_flow_in_the_rules_saves_it_and_shows_how_to_set_it_up(core, monkeypatch):
    from labelbridge.ui.pages import orders as orders_page

    rules = orders_page.ShippingRulesDialog(None, [ShippingRule("", "FedEx", False, 1000.0)], "USPS",
                                            by_flow=False, shop="demo.myshopify.com")
    assert rules.fedex_signature.isEnabled() and rules.flow_help.isHidden()
    rules.fedex_flow.setCurrentIndex(0)  # «Shopify Flow, automáticamente»: sin firma ni seguro
    assert not rules.fedex_signature.isEnabled() and not rules.fedex_insure.isEnabled()
    assert not rules.flow_help.isHidden()
    rules.close()

    page = orders_page.OrdersPage(_ui_context(core))
    shown: list = []

    def fake_run(dialog):
        shown.append(type(dialog).__name__)
        if isinstance(dialog, orders_page.ShippingRulesDialog):
            dialog.fedex_flow.setCurrentIndex(0)
            return QDialog.DialogCode.Accepted
        return 0

    monkeypatch.setattr(orders_page, "run_dialog", fake_run)
    monkeypatch.setattr(page, "reload", lambda: None)
    page.edit_rules()
    assert core.cfg.data.shopify.fedex_by_flow is True and shown == ["ShippingRulesDialog", "FlowSetupDialog"]
    page.edit_rules()  # ya estaba elegido: la guía no se vuelve a abrir sola
    assert shown[-1] == "ShippingRulesDialog"


def test_the_tray_only_opens_shopify_admin_pages(core, monkeypatch):
    from labelbridge.ui import tray as tray_module

    opened: list = []
    monkeypatch.setattr(tray_module.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    tray = tray_module.Tray(_ui_context(core), lambda: None, lambda: None, lambda: None)
    tray._on_event("open_url", {"url": "https://admin.shopify.com/store/demo/orders/93"})
    tray._on_event("open_url", {"url": "https://evil.example.com/phish"})
    assert opened == ["https://admin.shopify.com/store/demo/orders/93"]
