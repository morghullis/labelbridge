"""Pedidos en detalle, pedidos manuales e instrucciones (Pedidos → «Ver pedido», «Nueva guía manual» y el asistente)."""

from __future__ import annotations

import threading

import pytest

from labelbridge.connectors import shopify_orders as so
from labelbridge.connectors.shopify import ShopifyClient, ShopifyError, ShopifyService
from labelbridge.connectors.shopify_orders import ManualItem, ManualOrders, Recipient

# --- instrucciones en la nota ----------------------------------------------------------------------

def test_instructions_never_touch_the_customer_note():
    note = so.with_instructions("Por favor envolver para regalo", "Caja negra\nTarjeta «Feliz cumpleaños»")
    assert note.startswith("Por favor envolver para regalo") and so.NOTE_MARK in note
    assert so.split_note(note) == ("Por favor envolver para regalo", "Caja negra\nTarjeta «Feliz cumpleaños»")
    more = so.with_instructions(note, "Firma al entregar", append=True)
    assert so.split_note(more)[1] == "Caja negra\nTarjeta «Feliz cumpleaños»\nFirma al entregar"
    assert so.with_instructions(more, "") == "Por favor envolver para regalo"  # borrarlas deja la del cliente
    assert so.split_note(None) == ("", "") and so.with_instructions("", "Solo esto") == f"{so.NOTE_MARK}\nSolo esto"
    with pytest.raises(ValueError):
        so.with_instructions("", "x" * (so.MAX_INSTRUCTIONS + 1))


# --- pedido manual ---------------------------------------------------------------------------------

ANA = Recipient(name="Ana María López", address1="123 Main St", city="Miami", zip="33101", province="FL",
                email="ana@example.com", phone="+1 305 555 0100")


def test_manual_order_is_paid_free_tagged_and_ships():
    items = [ManualItem(quantity=2, variant_id="gid://shopify/ProductVariant/9"),
             ManualItem(quantity=1, title="Reemplazo de anillo", weight=1.5)]
    order = so.order_input(ANA, items, "Reemplazo del pedido #1023")
    assert order["financialStatus"] == "PAID" and order["tags"] == [so.MANUAL_TAG]
    assert all(line["requiresShipping"] and line["priceSet"]["shopMoney"]["amount"] == "0.00"
               for line in order["lineItems"])
    assert order["lineItems"][0]["variantId"].endswith("/9") and "title" not in order["lineItems"][0]
    assert order["lineItems"][1]["weight"] == {"value": 1.5, "unit": "OUNCES"}
    assert order["shippingLines"][0]["priceSet"]["shopMoney"]["amount"] == "0.00"
    address = order["shippingAddress"]
    assert (address["firstName"], address["lastName"], address["provinceCode"]) == ("Ana", "María López", "FL")
    assert order["email"] == "ana@example.com" and so.split_note(order["note"])[1] == "Reemplazo del pedido #1023"
    single = so.order_input(Recipient(name="Madonna", address1="1 St", city="LA", zip="90001", province="CA"),
                            items[1:], "")
    assert single["shippingAddress"]["lastName"] == "Madonna" and "firstName" not in single["shippingAddress"]
    assert "note" not in single and "email" not in single


def test_manual_order_problems_are_explained():
    ok = [ManualItem(quantity=1, title="Muestra")]
    assert so.manual_problem(ANA, ok) == ""
    assert so.manual_problem(Recipient(name="", address1="x", city="y", zip="1"), ok)
    no_state = Recipient(name="Ana", address1="x", city="Miami", zip="33101", country="US")
    assert "estado" in so.manual_problem(no_state, ok)
    assert so.manual_problem(Recipient(name="Ana", address1="x", city="Madrid", zip="28001", country="ES"), ok) == ""
    assert "código de dos letras" in so.manual_problem(Recipient(name="A", address1="x", city="y", zip="1",
                                                                 country="USA"), ok)
    bad_email = Recipient(**{**ANA.__dict__, "email": "ana@"})
    assert "email" in so.manual_problem(bad_email, ok)
    assert so.manual_problem(ANA, [])
    assert so.manual_problem(ANA, [ManualItem(quantity=0, title="x")])
    assert so.manual_problem(ANA, [ManualItem(quantity=1)])  # ni producto ni artículo libre


class FakeClient:
    granted_scopes = ""
    graphql_first = ShopifyClient.graphql_first  # la de verdad: prueba las consultas en orden

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.answers: dict[str, dict] = {}
        self.fos: list[dict] = [{"id": "gid://shopify/FulfillmentOrder/1", "status": "OPEN"}]
        self._fallbacks: dict = {}

    def graphql(self, query: str, variables: dict | None = None, *, mutation: bool = False, optional=()) -> dict:
        name = query.split("(")[0].split()[-1]
        self.calls.append((name, variables or {}))
        return self.answers.get(name, {})

    def fulfillment_orders(self, _gid: str) -> list[dict]:
        return self.fos


def test_creating_a_manual_order_waits_for_its_shipment():
    client = FakeClient()
    client.answers["LabelBridgeManualOrder"] = {"orderCreate": {
        "order": {"id": "gid://shopify/Order/555", "legacyResourceId": "555", "name": "#1050"}, "userErrors": []}}
    created = ManualOrders(client).create(ANA, [ManualItem(quantity=1, variant_id="gid://shopify/ProductVariant/9")])
    assert created == {"gid": "gid://shopify/Order/555", "legacy": "555", "name": "#1050"}
    variables = client.calls[0][1]
    assert variables["options"] == {"inventoryBehaviour": "DECREMENT_IGNORING_POLICY", "sendReceipt": False,
                                    "sendFulfillmentReceipt": False}
    free = ManualOrders(client).create(ANA, [ManualItem(quantity=1, title="Muestra")], decrement=True)
    assert free["name"] == "#1050" and client.calls[-1][1]["options"]["inventoryBehaviour"] == "BYPASS"

    client.answers["LabelBridgeManualOrder"] = {"orderCreate": {"order": None, "userErrors": [{"message": "Bad zip"}]}}
    with pytest.raises(ShopifyError, match="Bad zip"):
        ManualOrders(client).create(ANA, [ManualItem(quantity=1, title="Muestra")])
    client.answers["LabelBridgeManualOrder"] = {"orderCreate": {"order": {}, "userErrors": []}}
    with pytest.raises(ShopifyError) as unsure:
        ManualOrders(client).create(ANA, [ManualItem(quantity=1, title="Muestra")])
    assert unsure.value.uncertain  # no se sabe si se creó: revisar en Shopify antes de crear otro
    with pytest.raises(ShopifyError):  # con datos incompletos ni se llama a Shopify
        ManualOrders(client).create(Recipient(name="", address1="", city="", zip=""), [])


def test_instructions_are_written_after_reading_the_current_note():
    client = FakeClient()
    client.answers["LabelBridgeOrderNote"] = {"order": {"id": "gid://shopify/Order/7", "note": "Sin moño, gracias"},
                                              "orderUpdate": {"userErrors": []}}
    current = ManualOrders(client).set_instructions("gid://shopify/Order/7", "Caja de regalo")
    assert current == "Caja de regalo"
    written = client.calls[-1][1]["input"]
    assert written["id"] == "gid://shopify/Order/7" and written["note"].startswith("Sin moño, gracias")


def test_orders_are_found_by_name_or_id():
    client = FakeClient()
    client.answers["LabelBridgeOrderLookup"] = {"orders": {"nodes": [{"id": "gid://shopify/Order/42"}]}}
    orders = ManualOrders(client)
    assert orders.resolve("#1023") == "gid://shopify/Order/42" and client.calls[-1][1] == {"query": "name:#1023"}
    assert orders.resolve("1023") == "gid://shopify/Order/42"
    assert orders.resolve("6123456789012") == "gid://shopify/Order/6123456789012"
    assert orders.resolve("gid://shopify/Order/9") == "gid://shopify/Order/9"
    with pytest.raises(ShopifyError):
        orders.resolve("#10 OR status:any")
    client.answers["LabelBridgeOrderLookup"] = {"orders": {"nodes": []}}
    with pytest.raises(ShopifyError, match="no encontró"):
        orders.resolve("#9999")


# --- detalle y productos ---------------------------------------------------------------------------

def test_order_detail_shows_what_each_order_has():
    detail = so.parse_detail({
        "id": "gid://shopify/Order/7", "legacyResourceId": "7", "name": "#1023", "createdAt": "2026-10-05T10:00:00Z",
        "displayFinancialStatus": "PAID", "displayFulfillmentStatus": "UNFULFILLED", "email": "c@x.com",
        "note": f"Envolver\n\n{so.NOTE_MARK}\nCaja negra", "tags": ["vip", so.MANUAL_TAG],
        "customer": {"displayName": "María López"},
        "shippingAddress": {"name": "María López", "address1": "Av. Uno 1", "city": "Miami", "provinceCode": "FL",
                            "zip": "33101", "country": "United States", "phone": "+13055550100"},
        "shippingLine": {"title": "Express"},
        "customAttributes": [{"key": "Mensaje de regalo", "value": "¡Felicidades!"}, {"key": "_interno", "value": "x"}],
        "subtotalPriceSet": {"shopMoney": {"amount": "120.00"}},
        "totalShippingPriceSet": {"shopMoney": {"amount": "10.00"}},
        "totalTaxSet": {"shopMoney": {"amount": "8.40"}},
        "totalDiscountsSet": {"shopMoney": {"amount": "0"}},
        "totalPriceSet": {"shopMoney": {"amount": "138.40", "currencyCode": "USD"}},
        "lineItems": {"nodes": [
            {"title": "Anillo Oyá", "variantTitle": "Talla 7", "sku": "AN-7", "quantity": 2, "currentQuantity": 1,
             "requiresShipping": True, "originalUnitPriceSet": {"shopMoney": {"amount": "60.00"}},
             "image": {"url": "https://cdn.shopify.com/a.jpg"},
             "customAttributes": [{"key": "Grabado", "value": "Ana"}, {"key": "_oculto", "value": "1"}]},
        ]},
        "fulfillments": [{"trackingInfo": [{"company": "USPS", "number": "9400", "url": "https://t"}]}],
    })
    assert (detail.name, detail.customer, detail.method, detail.manual) == ("#1023", "María López", "Express", True)
    assert detail.customer_note == "Envolver" and detail.instructions == "Caja negra"
    assert detail.items[0].quantity == 1 and detail.items[0].properties == [("Grabado", "Ana")]  # lo que queda por enviar
    assert detail.attributes == [("Mensaje de regalo", "¡Felicidades!")]
    assert detail.address[0] == "María López" and "Miami FL 33101" in detail.address
    assert (detail.total, detail.taxes, detail.tracking) == (138.40, 8.40, [("USPS", "9400", "https://t")])
    text = detail.as_text()
    assert "1 × Anillo Oyá (Talla 7) · SKU AN-7 · Grabado: Ana" in text and "Instrucciones: Caja negra" in text


def test_product_search_lists_each_variant():
    choices = so.parse_products({"products": {"nodes": [
        {"title": "Collar Ochún", "status": "ACTIVE",
         "featuredMedia": {"preview": {"image": {"url": "https://cdn.shopify.com/c.jpg"}}},
         "variants": {"nodes": [{"id": "gid://shopify/ProductVariant/1", "title": "Default Title", "sku": "CO-1",
                                 "price": "80.00"}]}},
        {"title": "Pulsera", "status": "ACTIVE", "variants": {"nodes": [
            {"id": "gid://shopify/ProductVariant/2", "title": "Oro", "sku": "", "price": "40"},
            {"id": "gid://shopify/ProductVariant/3", "title": "Plata", "sku": "PU-P", "price": "35"}]}},
        {"title": "Viejo", "status": "ARCHIVED", "variants": {"nodes": [{"id": "gid://shopify/ProductVariant/4"}]}},
    ]}})
    assert [c.title for c in choices] == ["Collar Ochún", "Pulsera · Oro", "Pulsera · Plata"]
    assert choices[0].image.endswith("c.jpg") and choices[2].price == 35.0
    assert so.search_terms("anillo oro") == "anillo* oro*" and so.search_terms("  ") == ""
    assert so.search_terms('x" OR tag:(*') == "x* OR* tag*"


# --- lo automático no toca los pedidos manuales ------------------------------------------------------

def test_the_automatic_cycle_leaves_manual_orders_alone(core):
    s = core.cfg.data.shopify
    s.enabled, s.shop, s.buy_shipping_label, s.print_order_label = True, "demo.myshopify.com", True, True
    service = ShopifyService(core.cfg, core.secrets, core.store, core.engine, core.events)
    service.reset_since(hours_back=1)
    bought = []

    class Client:
        def fulfillment_orders(self, _gid):
            bought.append("consulted")
            return [{"id": "gid://shopify/FulfillmentOrder/1", "status": "OPEN",
                     "deliveryMethod": {"methodType": "SHIPPING"}}]

        def purchase_label(self, *args, **kwargs):
            bought.append("bought")
            return "gid://shopify/ShippingLabelPurchaseResult/1"

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    order = {"id": "gid://shopify/Order/5", "legacyResourceId": "5", "name": "#1050", "createdAt": now,
             "displayFinancialStatus": "PAID", "displayFulfillmentStatus": "UNFULFILLED", "tags": [so.MANUAL_TAG],
             "shippingLine": {"title": "Envío manual (LabelBridge)"}, "lineItems": {"nodes": []}}
    queued = service._process_order(Client(), s, order, service.since_ts(), threading.Event())
    assert queued == 0 and bought == []  # ni guía, ni etiqueta interna
    assert not any(kind == "notify" for kind, _data in core.received)  # ni aviso de «pedido nuevo»


def test_order_detail_works_without_fulfillment_permissions():
    class Client(FakeClient):
        def graphql(self, query, variables=None, *, mutation=False, optional=()):
            if "fulfillments" in query:
                raise ShopifyError("Access denied for fulfillments", code="access_denied")
            return {"order": {"id": "gid://shopify/Order/7", "name": "#7", "lineItems": {"nodes": []}}}

    assert ManualOrders(Client()).detail("gid://shopify/Order/7").name == "#7"
