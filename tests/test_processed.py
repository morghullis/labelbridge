"""Pedidos procesados = «Preparado» en Shopify: filtro (por defecto, todos), marcar preparado y cancelar la preparación
desde la app; y el diseño nuevo (menú por secciones, Ajustes por pestañas, Inicio como resumen del día)."""

from __future__ import annotations

import pytest
from conftest import wait_for
from PySide6.QtWidgets import QApplication
from test_orders import _ui_context, make_order, row_of, usps

from labelbridge.connectors.orders_overview import in_status
from labelbridge.connectors.shopify import ShopifyError, ShopifyService


class FulfillmentClient:
    """Shopify falso para preparar y deshacer la preparación de un pedido."""

    def __init__(self, fos=("OPEN",), fulfillments=(), labels=None) -> None:
        self.fos = [{"id": f"gid://shopify/FulfillmentOrder/{n}", "status": s, "deliveryMethod": {"methodType": "SHIPPING"}}
                    for n, s in enumerate(fos, start=1)]
        self.fulfillments = [{"id": f"gid://shopify/Fulfillment/{n}", "status": s} for n, s in enumerate(fulfillments, 1)]
        self.labels = labels or {}
        self.mutations: list[tuple[str, dict]] = []

    def graphql(self, query, variables=None, *, mutation=False, optional=()):
        if mutation:
            name = "create" if "fulfillmentCreate" in query else "cancel"
            self.mutations.append((name, variables))
            key = "fulfillmentCreate" if name == "create" else "fulfillmentCancel"
            return {key: {"fulfillment": {"id": "gid://shopify/Fulfillment/9", "status": "SUCCESS"}, "userErrors": []}}
        return {"order": {"id": variables["id"], "fulfillments": self.fulfillments,
                          "fulfillmentOrders": {"nodes": self.fos}}}

    def order_labels(self, _gid):
        return self.labels


def _service(core, client) -> ShopifyService:
    service = ShopifyService(core.cfg, core.secrets, core.store, core.engine, core.events)
    core.cfg.data.shopify.shop = "demo.myshopify.com"
    service.client = lambda: client
    return service


def test_an_order_is_marked_prepared_in_shopify(core):
    client = FulfillmentClient(fos=("OPEN", "CLOSED"))
    service = _service(core, client)
    assert service.mark_fulfilled("1459", "#1459") == 1
    name, variables = client.mutations[-1]
    assert name == "create" and variables["fulfillment"] == {
        "lineItemsByFulfillmentOrder": [{"fulfillmentOrderId": "gid://shopify/FulfillmentOrder/1"}],
        "notifyCustomer": False}  # sin guía y sin avisar al cliente
    client.fos = [{"id": "x", "status": "CLOSED"}]
    with pytest.raises(ShopifyError, match="ya está preparado"):
        service.mark_fulfilled("1459", "#1459")


def test_never_while_a_label_purchase_is_in_flight(core):
    client = FulfillmentClient()
    service = _service(core, client)
    service._note("1459", "uncertain", name="#1459")
    with pytest.raises(ShopifyError, match="sin confirmar"):
        service.mark_fulfilled("1459", "#1459")
    assert client.mutations == []


def test_cancelling_the_preparation_brings_it_back_unless_a_label_is_active(core):
    client = FulfillmentClient(fulfillments=("SUCCESS", "CANCELLED"))
    service = _service(core, client)
    assert service.cancel_fulfillment("1468", "#1468") == 1
    assert client.mutations == [("cancel", {"id": "gid://shopify/Fulfillment/1"})]
    client.labels = {"77": {"voided": False, "cost": 5.34}}  # con guía: se anula en Shopify (devuelve el dinero)
    with pytest.raises(ShopifyError, match="anúlala"):
        service.cancel_fulfillment("1468", "#1468")
    assert len(client.mutations) == 1


def test_processed_means_prepared_in_shopify():
    prepared = row_of(make_order(1, labels=(("2", "5,68"),), fulfillments=(usps(),), fulfillment="FULFILLED"))
    waiting = row_of(make_order(2))
    assert prepared.fulfilled and in_status(prepared, "processed") and not in_status(prepared, "pending")
    assert not waiting.fulfilled and in_status(waiting, "pending") and in_status(waiting, "all")


def test_the_orders_page_marks_and_unmarks_from_its_menus(core, monkeypatch):
    from labelbridge.ui.pages import orders as orders_page

    ctx = _ui_context(core)
    page = orders_page.OrdersPage(ctx)
    calls: list = []
    monkeypatch.setattr(orders_page, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(orders_page, "run_async", lambda fn, done=None, failed=None: done(fn()) if done else fn())
    monkeypatch.setattr(ctx.services.shopify, "mark_fulfilled", lambda legacy, name, notify=False: calls.append(("fulfill", legacy)))
    monkeypatch.setattr(ctx.services.shopify, "cancel_fulfillment", lambda legacy, name: calls.append(("cancel", legacy)) or 1)
    monkeypatch.setattr(page, "reload", lambda: calls.append("reload"))
    page.run_action(row_of(make_order(31)), "fulfill")
    page.run_action(row_of(make_order(32, fulfillments=(usps(),), fulfillment="FULFILLED")), "unfulfill")
    assert calls == [("fulfill", "31"), "reload", ("cancel", "32"), "reload"]
    told: list = []
    opened: list = []
    monkeypatch.setattr(orders_page, "info", lambda _p, _t, text: told.append(text))
    monkeypatch.setattr(orders_page.QDesktopServices, "openUrl", lambda url: opened.append(url))
    with_label = row_of(make_order(33, labels=(("9", "5,10"),), fulfillments=(usps(),), fulfillment="FULFILLED"))
    page.run_action(with_label, "unfulfill")  # con guía activa: se explica y se abre Shopify para anularla
    assert "anúlala" in told[-1] and opened and ("cancel", "33") not in calls


# --- diseño -------------------------------------------------------------------------------------------

def test_the_sidebar_is_grouped_and_links_open_the_right_settings_tab(core):
    from labelbridge.ui.main_window import NAV, NAV_SECTIONS, MainWindow

    assert sorted(key for _title, keys in NAV_SECTIONS for key in keys) == sorted(key for key, _t, _i in NAV)
    window = MainWindow(_ui_context(core))
    window.navigate("settings/label")
    settings = window.pages["settings"]
    assert window.stack.currentWidget() is settings and settings.tab_stack.currentIndex() == 3
    assert settings.tab_buttons["label"].isChecked()
    window.quitting = True
    window.close()


def test_settings_keep_awake_and_auto_update_are_saved(core):
    from labelbridge.ui.pages.settings import SettingsPage

    page = SettingsPage(_ui_context(core))
    g = core.cfg.data.general
    assert g.keep_awake and g.auto_install_updates  # por defecto: encendidos
    page.keep_awake.setChecked(False)
    page.auto_install.setChecked(False)
    page._save()
    assert not g.keep_awake and not g.auto_install_updates
    page.auto_update.setChecked(False)
    assert not page.auto_install.isEnabled()  # sin buscar versiones, no hay qué instalar


def test_home_sums_up_the_day(core):
    import time

    from labelbridge.ui.pages.dashboard import DashboardPage

    ctx = _ui_context(core)
    core.cfg.data.shopify.enabled = True
    page = DashboardPage(ctx, lambda _key: None)
    now = time.time()
    page.set_orders([row_of(make_order(41, created=now - 60, subtotal=320)),
                     row_of(make_order(42, created=now - 3 * 86400))])
    page.set_attention(2)
    assert wait_for(lambda: (QApplication.processEvents(), page.card_orders.value.text() == "1")[1])
    assert "320" in page.card_orders.detail.text() and page.card_pending.value.text() == "2"
    assert "Hoy, " in page.subtitle.text()
