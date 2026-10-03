"""Normaliza pedidos de Shopify (webhook REST o GraphQL) a los datos que lleva la etiqueta."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import LabelSettings


@dataclass
class OrderLabelData:
    order_name: str = ""
    order_id: str = ""
    created: str = ""
    ship_name: str = ""
    company: str = ""
    address_lines: list[str] = field(default_factory=list)
    phone: str = ""
    shipping_method: str = ""
    items: list[tuple[int, str]] = field(default_factory=list)
    item_count: int = 0
    note: str = ""
    total: str = ""
    sender_name: str = ""
    sender_lines: list[str] = field(default_factory=list)
    qr_url: str = ""
    barcode: str = ""


def _get(data: Any, *keys: str) -> Any:
    if not isinstance(data, dict):
        return ""
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return ""


def _nodes(connection: Any) -> list[dict[str, Any]]:
    """Lista de una conexión GraphQL ({nodes: []} o {edges: [{node}]}) o una lista REST."""
    if isinstance(connection, list):
        return [c for c in connection if isinstance(c, dict)]
    if isinstance(connection, dict):
        if isinstance(connection.get("nodes"), list):
            return connection["nodes"]
        if isinstance(connection.get("edges"), list):
            return [e.get("node", {}) for e in connection["edges"] if isinstance(e, dict)]
    return []


def _legacy_id(value: Any) -> str:
    text = str(value or "")
    return text.rsplit("/", 1)[-1] if text.startswith("gid://") else text


def _format_date(value: str) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return value[:16]


def _money(order: dict[str, Any]) -> str:
    shop_money = _get(_get(order, "totalPriceSet", "total_price_set"), "shopMoney", "shop_money")
    if shop_money:
        amount = _get(shop_money, "amount")
        currency = _get(shop_money, "currencyCode", "currency_code")
    else:
        amount = _get(order, "total_price", "totalPrice")
        currency = _get(order, "currency", "currencyCode")
    if not amount:
        return ""
    try:
        return f"{float(amount):,.2f} {currency}".strip()
    except (TypeError, ValueError):
        return f"{amount} {currency}".strip()


def barcode_value(order_name: str, order_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9\-./]", "", order_name) or re.sub(r"\D", "", order_id)
    return value[:24]


def normalize_order(
    order: dict[str, Any], labels: LabelSettings | None = None, shop_domain: str = ""
) -> OrderLabelData:
    labels = labels or LabelSettings()
    addr = _get(order, "shipping_address", "shippingAddress") or _get(order, "billing_address", "billingAddress")
    addr = addr if isinstance(addr, dict) else {}

    name = _get(addr, "name") or " ".join(
        p for p in (_get(addr, "first_name", "firstName"), _get(addr, "last_name", "lastName")) if p
    )
    if not name:
        customer = _get(order, "customer")
        name = _get(customer, "displayName") or " ".join(
            p for p in (_get(customer, "first_name", "firstName"), _get(customer, "last_name", "lastName")) if p
        )

    city = _get(addr, "city")
    province = _get(addr, "province_code", "provinceCode") or _get(addr, "province")
    zip_code = _get(addr, "zip")
    city_line = ", ".join(p for p in (city, province) if p)
    if zip_code:
        city_line = f"{city_line} {zip_code}".strip()
    address_lines = [
        line
        for line in (_get(addr, "address1"), _get(addr, "address2"), city_line, _get(addr, "country"))
        if line
    ]

    shipping_method = ""
    shipping_line = _get(order, "shippingLine", "shipping_line")
    if isinstance(shipping_line, dict):
        shipping_method = _get(shipping_line, "title")
    if not shipping_method:
        lines = _nodes(_get(order, "shipping_lines", "shippingLines"))
        shipping_method = _get(lines[0], "title") if lines else ""

    items: list[tuple[int, str]] = []
    count = 0
    for item in _nodes(_get(order, "line_items", "lineItems")):
        try:
            qty = int(_get(item, "currentQuantity", "current_quantity", "quantity") or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        text = _get(item, "name") or " - ".join(
            p for p in (_get(item, "title"), _get(item, "variant_title", "variantTitle")) if p
        )
        sku = _get(item, "sku")
        items.append((qty, f"{text} · {sku}" if sku else str(text)))
        count += qty

    order_id = _legacy_id(_get(order, "legacyResourceId", "legacy_resource_id") or _get(order, "id"))
    order_name = str(_get(order, "name") or (f"#{order['order_number']}" if order.get("order_number") else order_id))

    qr_url = ""
    shop = shop_domain.strip().lower()
    if labels.show_qr and shop and order_id.isdigit():
        handle = shop.replace("https://", "").split(".")[0]
        qr_url = f"https://admin.shopify.com/store/{handle}/orders/{order_id}"

    sender_lines = [line.strip() for line in labels.sender_address.splitlines() if line.strip()]
    return OrderLabelData(
        order_name=order_name,
        order_id=order_id,
        created=_format_date(str(_get(order, "created_at", "createdAt") or "")),
        ship_name=str(name),
        company=str(_get(addr, "company")),
        address_lines=[str(line) for line in address_lines],
        phone=str(_get(addr, "phone") or _get(order, "phone")),
        shipping_method=str(shipping_method),
        items=items,
        item_count=count,
        note=str(_get(order, "note") or "").strip(),
        total=_money(order) if labels.show_price else "",
        sender_name=labels.store_name.strip(),
        sender_lines=sender_lines,
        qr_url=qr_url,
        barcode=barcode_value(order_name, order_id),
    )


def sample_order() -> dict[str, Any]:
    """Pedido ficticio para impresiones de prueba."""
    created = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
    return {
        "id": 5551234567890,
        "name": "#1001",
        "created_at": created,
        "shipping_address": {
            "name": "María Fernanda López",
            "company": "",
            "address1": "Av. Insurgentes Sur 1234",
            "address2": "Depto. 5B, Col. Del Valle",
            "city": "Ciudad de México",
            "province_code": "CDMX",
            "zip": "03100",
            "country": "México",
            "phone": "+52 55 1234 5678",
        },
        "shipping_lines": [{"title": "Envío estándar"}],
        "line_items": [
            {"quantity": 2, "name": "Camiseta básica - M / Negro", "sku": "CAM-M-NEG"},
            {"quantity": 1, "name": "Gorra bordada", "sku": "GOR-01"},
            {"quantity": 3, "name": "Calcetines deportivos (pack)", "sku": "CAL-3P"},
        ],
        "note": "Entregar en recepción. Etiqueta de PRUEBA.",
        "total_price": "749.00",
        "currency": "MXN",
    }
