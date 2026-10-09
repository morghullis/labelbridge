"""«Guía FedEx»: Shopify no deja que las apps compren FedEx ni les acepta firma o seguro, así que la guía se compra en
Shopify. LabelBridge abre el pedido, dice qué elegir (servicio, firma, seguro por el valor de las piezas, caja), y en
cuanto Shopify la tiene la imprime y lo confirma."""

from __future__ import annotations

import pytest
from conftest import wait_for
from PySide6.QtWidgets import QApplication
from test_orders import _ui_context, make_order, row_of
from test_print_labels import LabelsInShopify

from labelbridge.config import ShippingPackage
from labelbridge.connectors.shopify import ShopifyError, ShopifyService


def _service(core, labels: dict) -> tuple[ShopifyService, LabelsInShopify]:
    service = ShopifyService(core.cfg, core.secrets, core.store, core.engine, core.events)
    core.cfg.data.shopify.shop = "demo.myshopify.com"
    shopify = LabelsInShopify(labels)
    service.client = lambda: shopify
    return service, shopify


def test_a_label_bought_in_shopify_is_found_and_printed_once(core):
    service, shopify = _service(core, {})
    assert service.collect_shopify_label("3001", "#3001") == {"state": "waiting"}
    service._note("3001", "manual", name="#3001", carrier="FedEx", fetch=1)
    shopify.labels = {"7": {"voided": True, "at": 1.0}, "8": {"voided": False, "at": 2.0}}
    found = service.collect_shopify_label("3001", "#3001")
    assert found["state"] == "printing" and found["tracking"] == "9400" and found["queued"] == 1
    assert shopify.fetched == ["8"] and core.store.get_by_external("shopify", "shipping-8-0-0") is not None
    assert service.ledger("3001")["state"] == "resolved"  # el ciclo ya no la vuelve a traer
    again = service.collect_shopify_label("3001", "#3001")  # otra vuelta: la misma guía no se imprime dos veces
    assert again["state"] == "printing" and again["queued"] == 0


def test_the_guide_says_exactly_what_to_choose_in_shopify(core):
    from labelbridge.ui.fedex_dialog import checklist

    ctx = _ui_context(core)
    s = core.cfg.data.shopify
    s.packages = [ShippingPackage("mi-caja", "", "BOX", 6, 4, 2, "INCHES", 3, "OUNCES")]
    s.shipping_rules[0].package = "mi-caja"  # la regla de FedEx (piezas de más de US$1000) sale en «Mi caja»
    row = row_of(make_order(81, subtotal=1450))
    items = {what: (value, note, copy) for what, value, note, copy in checklist(ctx, row, "FedEx")}
    assert items["Servicio de envío"][0] == "FedEx 2Day®" and items["Firma"][0] == "Firma requerida"
    assert items["Seguro de envío"] == ("US$1,450.00", "el valor de las piezas", "1450.00")  # con botón Copiar
    assert items["Paquete y peso"][:2] == ("Mi caja", "6 × 4 × 2 in · 3 oz")
    s.fedex_signature, s.fedex_insure = "none", False
    items = {what for what, _value, _note, _copy in checklist(ctx, row, "FedEx")}
    assert "Firma" not in items and "Seguro de envío" not in items


@pytest.fixture()
def guide(core, monkeypatch):
    from labelbridge.ui import fedex_dialog

    ctx = _ui_context(core)
    core.cfg.data.shopify.shop = "demo.myshopify.com"
    opened: list = []
    monkeypatch.setattr(fedex_dialog.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    shopify = ctx.services.shopify
    state = {"answers": [{"state": "waiting"}], "marked": []}
    monkeypatch.setattr(shopify, "collect_shopify_label",
                        lambda legacy, name: state["answers"].pop(0) if len(state["answers"]) > 1 else state["answers"][0])
    monkeypatch.setattr(shopify, "buy_in_shopify", lambda legacy, name, carrier: state["marked"].append((legacy, carrier)))
    return ctx, opened, state


def test_one_click_opens_shopify_and_the_label_prints_when_bought(guide):
    from labelbridge.ui.fedex_dialog import FedexDialog

    ctx, opened, state = guide
    dialog = FedexDialog(None, ctx, row_of(make_order(82, subtotal=1500)))
    assert wait_for(lambda: (QApplication.processEvents(), bool(opened))[1])
    assert state["marked"] == [("82", "FedEx")] and opened[0].endswith("/orders/82")
    assert dialog.timer.isActive() and "Esperando" in dialog.status.text()
    state["answers"] = [{"state": "printing", "carrier": "FedEx", "tracking": "771234567890", "queued": 1}]
    dialog._check()
    assert wait_for(lambda: (QApplication.processEvents(), dialog.done)[1])
    assert "771234567890" in dialog.status.text() and not dialog.timer.isActive() and not dialog.track_btn.isHidden()
    dialog.close()


def test_a_label_already_bought_is_printed_without_opening_shopify(guide):
    from labelbridge.ui.fedex_dialog import FedexDialog

    ctx, opened, state = guide
    state["answers"] = [{"state": "printing", "carrier": "FedEx", "tracking": "7799", "queued": 1}]
    dialog = FedexDialog(None, ctx, row_of(make_order(83, subtotal=1500)))
    assert wait_for(lambda: (QApplication.processEvents(), dialog.done)[1])
    assert opened == [] and state["marked"] == []
    dialog.close()


def test_an_order_with_a_purchase_in_progress_is_not_sent_to_shopify(guide, monkeypatch):
    from labelbridge.ui.fedex_dialog import FedexDialog

    ctx, opened, _state = guide

    def busy(legacy, name, carrier):
        raise ShopifyError("Ese pedido ya tiene una compra hecha, en curso o sin confirmar")

    monkeypatch.setattr(ctx.services.shopify, "buy_in_shopify", busy)
    dialog = FedexDialog(None, ctx, row_of(make_order(84, subtotal=1500)))
    assert wait_for(lambda: (QApplication.processEvents(), "ya tiene una compra" in dialog.status.text())[1])
    assert opened == [] and not dialog.timer.isActive()
    dialog.close()


def test_fedex_orders_open_the_guide_from_the_print_button(core, monkeypatch):
    from labelbridge.ui import fedex_dialog
    from labelbridge.ui.pages import orders as orders_page

    page = orders_page.OrdersPage(_ui_context(core))
    guides: list = []
    monkeypatch.setattr(fedex_dialog, "open_fedex", lambda parent, ctx, row, on_other=None: guides.append(row) or
                        type("Window", (), {"finished": type("Signal", (), {"connect": lambda self, f: None})()})())
    fedex = row_of(make_order(85, subtotal=1500))
    page.run_action(fedex, "print")
    page.run_action(fedex, "buy")
    assert [row.name for row in guides] == ["#85#", "#85#"]
