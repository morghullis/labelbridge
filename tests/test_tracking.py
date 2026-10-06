"""«Rastrear» en una ventana de LabelBridge: el estado del envío y los movimientos de la paquetería que guarda Shopify,
sin abrir la web de la paquetería."""

from __future__ import annotations

from datetime import datetime, timezone

from test_orders import make_order, row_of, usps

from labelbridge.connectors.tracking import parse_tracking

# Como lo devolvió una tienda real (guía de USPS anulada) y un envío en camino.
VOIDED = {
    "status": "CANCELLED", "createdAt": "2026-10-05T17:12:12Z", "displayStatus": "LABEL_VOIDED",
    "inTransitAt": None, "deliveredAt": None, "estimatedDeliveryAt": None,
    "trackingInfo": [{"company": "USPS", "number": "92001902673388001062310869", "url": "https://tools.usps.com/x"}],
    "events": {"nodes": [
        {"happenedAt": "2026-10-05T17:12:13Z", "status": "LABEL_PURCHASED", "message": "Shipping label purchased",
         "city": "Hialeah", "province": "FL", "country": "US", "estimatedDeliveryAt": None},
        {"happenedAt": "2026-10-05T17:12:00Z", "status": "CONFIRMED",
         "message": "Shipping Label Created, USPS Awaiting Item", "city": "MIAMI", "province": "FL", "country": "US",
         "estimatedDeliveryAt": None}]},
}
MOVING = {
    "status": "SUCCESS", "createdAt": "2026-10-06T15:00:00Z", "displayStatus": "IN_TRANSIT",
    "inTransitAt": "2026-10-06T22:00:00Z", "deliveredAt": None, "estimatedDeliveryAt": "2026-10-09T00:00:00Z",
    "trackingInfo": [{"company": "United States Postal Service", "number": "9400111206213862301234", "url": ""},
                     {"company": "USPS", "number": "9400111206213862305678", "url": ""}],
    "events": {"nodes": [
        {"happenedAt": "2026-10-06T16:00:00Z", "status": "CARRIER_PICKED_UP", "message": "USPS picked up item",
         "city": "MIAMI", "province": "FL", "country": "US"},
        {"happenedAt": "2026-10-07T09:30:00Z", "status": "IN_TRANSIT", "message": "Arrived at USPS Regional Facility",
         "city": "ORLANDO", "province": "FL", "country": "US"}]},
}


def test_tracking_reads_the_carrier_events_newest_first():
    tracking = parse_tracking({"name": "#1468", "shippingAddress": {"city": "Atlanta", "provinceCode": "GA",
                                                                    "countryCodeV2": "US"},
                               "fulfillments": [VOIDED, MOVING, {"status": "SUCCESS", "trackingInfo": []}]})
    assert tracking.order == "#1468" and tracking.destination == "Atlanta, GA"
    moving, voided = tracking.shipments  # el que está en camino primero; la guía anulada al final
    assert (moving.carrier, moving.number, moving.more) == ("USPS", "9400111206213862301234", ["9400111206213862305678"])
    assert [e.status for e in moving.events] == ["IN_TRANSIT", "CARRIER_PICKED_UP"] and moving.events[0].place == "Orlando, FL"
    assert moving.step == 2 and not moving.problem and moving.headline.startswith("En camino · llega el ")
    eta = datetime.fromtimestamp(moving.eta, timezone.utc)
    assert (eta.month, eta.day) == (10, 9)  # solo la fecha: ese día, no la noche anterior
    assert voided.closed and voided.headline == "Guía anulada" and voided.events[1].place == "Miami, FL"
    assert voided.events[1].title == "Guía creada: la paquetería espera el paquete"


def test_problems_and_deliveries_are_clear():
    attempt = dict(MOVING, displayStatus="ATTEMPTED_DELIVERY")
    shipment = parse_tracking({"name": "#1", "fulfillments": [attempt]}).shipments[0]
    assert shipment.problem and shipment.step == 3 and shipment.headline.startswith("Intento de entrega fallido")
    done = dict(MOVING, displayStatus="DELIVERED", deliveredAt="2026-10-08T18:00:00Z")
    shipment = parse_tracking({"name": "#1", "fulfillments": [done]}).shipments[0]
    assert shipment.step == 4 and shipment.headline.startswith("Entregado el ")
    quiet = dict(MOVING, displayStatus="CONFIRMED", events={"nodes": []}, estimatedDeliveryAt=None)
    shipment = parse_tracking({"name": "#1", "fulfillments": [quiet]}).shipments[0]
    assert shipment.step == 0 and shipment.events == [] and shipment.headline.startswith("Guía creada")


def test_track_opens_a_window_not_the_web(core, monkeypatch):
    from conftest import wait_for
    from PySide6.QtWidgets import QApplication
    from test_orders import _ui_context

    from labelbridge.ui import tracking_dialog
    from labelbridge.ui.pages import orders as orders_page

    ctx = _ui_context(core)
    page = orders_page.OrdersPage(ctx)
    opened_web: list = []
    monkeypatch.setattr(orders_page.QDesktopServices, "openUrl", lambda url: opened_web.append(url))
    windows: list = []
    monkeypatch.setattr(tracking_dialog, "run_dialog", lambda dialog: windows.append(dialog) or 0)
    tracking = parse_tracking({"name": "#2#", "fulfillments": [MOVING]})
    monkeypatch.setattr(ctx.services.shopify, "tracking", lambda legacy: tracking)
    shipped = row_of(make_order(2, labels=(("2", "5,68"),), fulfillments=(usps(),), fulfillment="FULFILLED"))
    page.run_action(shipped, "track")
    assert opened_web == [] and len(windows) == 1  # su propia ventana, sin ir a la web
    dialog = windows[0]
    assert wait_for(lambda: (QApplication.processEvents(), dialog.tracking is not None)[1])
    assert not dialog.pill.isHidden() and dialog.pill.text().startswith("En camino")
    assert dialog.web_btn.isHidden()  # sin enlace de la paquetería: ni el botón opcional
    dialog.close()
