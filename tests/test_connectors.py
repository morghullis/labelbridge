"""API local y Shopify (respuestas simuladas)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from labelbridge import net
from labelbridge.connectors.local_api import LocalApiService
from labelbridge.connectors.shopify import ShopifyService, extract_urls, normalize_shop
from labelbridge.secret_store import LOCAL_API_KEY, SHOPIFY_CLIENT_SECRET


def _http(method: str, url: str, body=None, key: str | None = None):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


# --- API local ----------------------------------------------------------------------------

def test_local_api_accepts_jobs_with_key(core):
    core.secrets.set(LOCAL_API_KEY, "clave-secreta")
    core.cfg.data.local_api.port = 0
    service = LocalApiService(core.cfg, core.secrets, core.store, core.engine, core.events)
    service.start()
    base = f"http://127.0.0.1:{service.bound_port}"
    try:
        status, health = _http("GET", f"{base}/api/v1/health")
        assert status == 200 and health["ok"] is True

        status, body = _http("POST", f"{base}/api/v1/jobs", {"id": "a1", "content": "^XA^XZ"})
        assert status == 401

        status, body = _http("POST", f"{base}/api/v1/jobs", {"jobs": [{"id": "a1", "content": "^XA^XZ"}]}, "clave-secreta")
        assert status == 202 and body["accepted"][0]["duplicate"] is False
        status, body = _http("POST", f"{base}/api/v1/jobs", {"id": "a1", "content": "^XA^XZ"}, "clave-secreta")
        assert body["accepted"][0]["duplicate"] is True

        status, body = _http("POST", f"{base}/api/v1/jobs", {"id": "bad"}, "clave-secreta")
        assert status == 400 and "exactamente uno" in body["error"]

        status, job = _http("GET", f"{base}/api/v1/jobs/a1", key="clave-secreta")
        assert status == 200 and job["external_id"] == "a1"
        status, printers = _http("GET", f"{base}/api/v1/printers", key="clave-secreta")
        assert printers["default"] == "archivo"
    finally:
        service.stop()


# --- Shopify ---------------------------------------------------------------------------------

def test_shopify_helpers():
    assert normalize_shop("Mi-Tienda") == "mi-tienda.myshopify.com"
    assert normalize_shop("https://mi-tienda.myshopify.com/admin") == "mi-tienda.myshopify.com"
    with pytest.raises(Exception):
        normalize_shop("www.mitienda.com")
    assert extract_urls("https://x.com/a.pdf") == ["https://x.com/a.pdf"]
    assert extract_urls('["https://x.com/a.pdf", "nope", "https://x.com/b.zpl"]') == ["https://x.com/a.pdf", "https://x.com/b.zpl"]
    assert extract_urls('{"url": "https://x.com/c.pdf"}') == ["https://x.com/c.pdf"]


class FakeShopify:
    """Simula el endpoint de token y la Admin API GraphQL."""

    def __init__(self, now_iso: str) -> None:
        self.calls: list[str] = []
        self.purchases: list[dict] = []
        self.now_iso = now_iso

    def order(self, number: int, created: str, **extra) -> dict:
        base = {
            "id": f"gid://shopify/Order/{number}",
            "legacyResourceId": str(number),
            "name": f"#{number}",
            "createdAt": created,
            "updatedAt": self.now_iso,
            "cancelledAt": None,
            "displayFinancialStatus": "PAID",
            "displayFulfillmentStatus": "UNFULFILLED",
            "tags": [],
            "shippingAddress": {"name": "Cliente", "address1": "Calle 1", "city": "CDMX", "zip": "01000"},
            "lineItems": {"nodes": [{"name": "Producto", "sku": "P1", "quantity": 1}]},
            "metafield": None,
        }
        base.update(extra)
        return base

    def __call__(self, method, url, **kwargs):
        if url.endswith("/admin/oauth/access_token"):
            self.calls.append("token")
            return net.Response(200, json.dumps({"access_token": "tok", "scope": "read_orders", "expires_in": 86399}).encode(), url)
        body = kwargs["json_body"]
        query = body["query"]
        name = query.split("query ")[-1].split("(")[0].split("{")[0].strip() if "query " in query else query.split("mutation ")[-1].split("(")[0].strip()
        self.calls.append(name)
        if name == "LabelBridgeOrders":
            data = {
                "orders": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        self.order(3001, self.now_iso),
                        self.order(3002, "2020-01-01T00:00:00Z", metafield={"value": "https://cdn.example.com/label-3002.pdf"}),
                        self.order(3003, self.now_iso, displayFinancialStatus="PENDING"),
                    ],
                }
            }
        elif name == "LabelBridgeFulfillmentOrders":
            data = {"order": {"fulfillmentOrders": {"nodes": [{"id": "gid://shopify/FulfillmentOrder/77", "status": "OPEN", "deliveryMethod": {"methodType": "SHIPPING"}}]}}}
        elif name == "LabelBridgePurchaseLabel":
            self.purchases.append(body["variables"]["input"])
            data = {"shippingLabelPurchase": {"shippingLabelPurchaseResult": {"id": "gid://shopify/ShippingLabelPurchaseResult/9", "status": "PENDING_PURCHASE"}, "userErrors": []}}
        elif name == "LabelBridgeLabelResult":
            data = {
                "node": {
                    "id": "gid://shopify/ShippingLabelPurchaseResult/9",
                    "status": "PURCHASED",
                    "errors": [],
                    "shippingLabels": [
                        {
                            "id": "gid://shopify/ShippingLabel/5",
                            "trackingInfo": {"number": "1Z999", "company": "UPS", "url": None},
                            "shippingDocuments": [{"documentType": "LABEL", "format": "ZPL", "url": "https://cdn.example.com/ups-1Z999.zpl"}],
                        }
                    ],
                }
            }
        else:
            data = {}
        return net.Response(200, json.dumps({"data": data}).encode(), url)


def test_shopify_cycle_enqueues_labels(core, monkeypatch):
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fake = FakeShopify(now_iso)
    monkeypatch.setattr("labelbridge.connectors.shopify.net.request", fake)
    s = core.cfg.data.shopify
    s.enabled, s.shop, s.client_id = True, "mi-tienda", "client-id"
    s.print_order_label = True
    s.metafield_label = True
    s.buy_shipping_label = True
    core.secrets.set(SHOPIFY_CLIENT_SECRET, "secreto")
    service = ShopifyService(core.cfg, core.secrets, core.store, core.engine, core.events)
    service.reset_since(hours_back=1)

    service.cycle(service.client(), s)
    titles = sorted(j["title"] for j in core.store.list())
    assert titles == ["Etiqueta #3002", "Etiqueta de envío #3001", "Pedido #3001"]
    shipping = next(j for j in core.store.list() if j["external_id"].startswith("shipping-"))
    assert shipping["format"] == "zpl" and shipping["url"].endswith(".zpl")
    assert shipping["meta"]["tracking"] == "1Z999"
    assert shipping["meta"]["label_size"] == "4x6"  # la guía sale a 4×6 in en cualquier papel
    assert len(fake.purchases) == 1 and fake.purchases[0]["fulfillmentOrderId"].endswith("/77")
    assert fake.calls[0] == "token"

    # Segunda vuelta: nada duplicado y no se compra otra etiqueta.
    service.cycle(service.client(), s)
    assert len(core.store.list()) == 3
    assert len(fake.purchases) == 1


def test_shopify_daily_limit(core, monkeypatch):
    from datetime import datetime, timezone

    fake = FakeShopify(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    monkeypatch.setattr("labelbridge.connectors.shopify.net.request", fake)
    s = core.cfg.data.shopify
    s.enabled, s.shop, s.client_id = True, "mi-tienda", "client-id"
    s.print_order_label = False
    s.buy_shipping_label = True
    s.max_labels_per_day = 0
    core.secrets.set(SHOPIFY_CLIENT_SECRET, "secreto")
    service = ShopifyService(core.cfg, core.secrets, core.store, core.engine, core.events)
    service.reset_since(hours_back=1)
    service.cycle(service.client(), s)
    assert fake.purchases == []
    assert any(kind == "notify" and "Límite" in data["title"] for kind, data in core.received)


def test_local_api_closes_connection_when_body_unread(core):
    import http.client

    core.secrets.set(LOCAL_API_KEY, "k")
    core.cfg.data.local_api.port = 0
    service = LocalApiService(core.cfg, core.secrets, core.store, core.engine, core.events)
    service.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", service.bound_port, timeout=5)
        conn.request("POST", "/api/v1/jobs", body=json.dumps({"id": "x", "content": "y" * 5000}), headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 401 and resp.getheader("Connection") == "close"
        conn.close()
    finally:
        service.stop()


def test_shopify_resumes_pagination(core, monkeypatch):
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fake = FakeShopify(now_iso)
    pages = {None: ([3101, 3102], "c1"), "c1": ([3103], None)}
    seen_after = []

    def orders_updated_since(self, since_iso, ns, key, max_pages=10, after=None):
        seen_after.append(after)
        numbers, nxt = pages[after]
        return [fake.order(n, now_iso) for n in numbers], nxt

    monkeypatch.setattr("labelbridge.connectors.shopify.net.request", fake)
    monkeypatch.setattr("labelbridge.connectors.shopify.ShopifyClient.orders_updated_since", orders_updated_since)
    s = core.cfg.data.shopify
    s.enabled, s.shop, s.client_id, s.print_order_label = True, "mi-tienda", "id", True
    core.secrets.set(SHOPIFY_CLIENT_SECRET, "secreto")
    service = ShopifyService(core.cfg, core.secrets, core.store, core.engine, core.events)
    service.reset_since(hours_back=1)
    service.cycle(service.client(), s)
    service.cycle(service.client(), s)
    assert seen_after == [None, "c1"]
    assert sorted(j["external_id"] for j in core.store.list()) == ["pedido-3101", "pedido-3102", "pedido-3103"]


def test_adhoc_printer_ports_are_restricted(core):
    from labelbridge.printing import PermanentPrintError
    from labelbridge.printing.engine import resolve_printer

    assert resolve_printer("tcp://192.168.1.20:9100", core.cfg).port == 9100
    with pytest.raises(PermanentPrintError):
        resolve_printer("tcp://192.168.1.20:25", core.cfg)


def _purchase_service(core, monkeypatch, purchase_behaviour):
    from datetime import datetime, timezone

    from labelbridge.connectors import shopify as shopify_module

    fake = FakeShopify(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    monkeypatch.setattr("labelbridge.connectors.shopify.net.request", fake)
    calls = []

    def purchase(self, fo_id, notify):
        calls.append(fo_id)
        raise purchase_behaviour(shopify_module)

    monkeypatch.setattr(shopify_module.ShopifyClient, "purchase_label", purchase)
    s = core.cfg.data.shopify
    s.enabled, s.shop, s.client_id = True, "mi-tienda", "client-id"
    s.print_order_label, s.metafield_label, s.buy_shipping_label = False, False, True
    core.secrets.set(SHOPIFY_CLIENT_SECRET, "secreto")
    service = ShopifyService(core.cfg, core.secrets, core.store, core.engine, core.events)
    service.reset_since(hours_back=1)
    return service, s, calls


def test_uncertain_purchase_is_never_retried(core, monkeypatch):
    service, s, calls = _purchase_service(
        core, monkeypatch, lambda m: m.ShopifyError("HTTP 502: Bad Gateway", uncertain=True)
    )
    service.cycle(service.client(), s)
    service.reset_since(hours_back=1)  # aunque se reprocese, no se vuelve a comprar
    service.cycle(service.client(), s)
    assert len(calls) == 1
    assert any(kind == "notify" and "Revisa" in data["title"] for kind, data in core.received)


def test_rejected_purchase_can_be_retried(core, monkeypatch):
    service, s, calls = _purchase_service(
        core, monkeypatch, lambda m: m.PurchaseRejected("RATES_NOT_FOUND No hay tarifas")
    )
    service.cycle(service.client(), s)
    service.reset_since(hours_back=1)
    service.cycle(service.client(), s)
    assert len(calls) == 2


def test_progress_not_saved_when_processing_fails(core, monkeypatch):
    from datetime import datetime, timezone

    from labelbridge.connectors import shopify as shopify_module

    fake = FakeShopify(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    monkeypatch.setattr("labelbridge.connectors.shopify.net.request", fake)
    s = core.cfg.data.shopify
    s.enabled, s.shop, s.client_id, s.print_order_label = True, "mi-tienda", "id", True
    core.secrets.set(SHOPIFY_CLIENT_SECRET, "secreto")
    service = ShopifyService(core.cfg, core.secrets, core.store, core.engine, core.events)
    service.reset_since(hours_back=1)
    cursor_before = core.store.kv_get(service._key("cursor"))

    def boom(*args, **kwargs):
        raise shopify_module.ShopifyError("fallo a mitad")

    monkeypatch.setattr(ShopifyService, "_process_order", boom)
    with pytest.raises(shopify_module.ShopifyError):
        service.cycle(service.client(), s)
    assert core.store.kv_get(service._key("cursor")) == cursor_before


def test_redirect_to_other_host_drops_credentials():
    import http.server
    import threading

    seen: dict = {}

    class Target(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen["headers"] = dict(self.headers)
            body = b"%PDF-1.4 ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    target = http.server.HTTPServer(("127.0.0.1", 0), Target)
    threading.Thread(target=target.serve_forever, daemon=True).start()

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(302)
            # "localhost" ≠ "127.0.0.1": otro host para el cliente
            self.send_header("Location", f"http://localhost:{target.server_address[1]}/label.pdf")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    origin = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=origin.serve_forever, daemon=True).start()
    try:
        resp = net.request(
            "GET",
            f"http://127.0.0.1:{origin.server_address[1]}/x",
            headers={"Authorization": "Bearer secreto", "X-Shopify-Access-Token": "shpat_x"},
        )
    finally:
        origin.shutdown()
        target.shutdown()
    assert resp.body == b"%PDF-1.4 ok"
    lowered = {k.lower() for k in seen["headers"]}
    assert "authorization" not in lowered and "x-shopify-access-token" not in lowered


def test_powershell_values_are_encoded():
    from labelbridge.system.windows_printers import _ps_value

    hostile = "Zebra\u2019 $(Start-Process calc) \u2019'"
    expression = _ps_value(hostile)
    assert "Start-Process" not in expression and "\u2019" not in expression and expression.count("'") == 2


def test_raw_datatype_for_xps_drivers():
    from labelbridge.printing.outputs import raw_datatype

    assert raw_datatype(True) == "XPS_PASS" and raw_datatype(False) == "RAW"
