"""Conector directo con Shopify (GraphQL Admin API).

Cada N segundos revisa los pedidos actualizados y, según la configuración:
  1. imprime la etiqueta de pedido generada por LabelBridge,
  2. compra la etiqueta de envío con Shopify Shipping (shippingLabelPurchase, API 2026-07+) y la imprime,
  3. imprime una etiqueta cuya URL guardó otro sistema (n8n, tu web...) en un metafield del pedido.

Autenticación: app del Dev Dashboard (client credentials, token de 24 h que se renueva solo)
o token fijo shpat_ de una app antigua creada desde el admin.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode, urlparse

from .. import events as ev
from .. import net
from ..config import ConfigManager, ShopifySettings
from ..i18n import N_, tr
from ..jobstore import JobStore
from ..models import JobRequest
from ..printing.engine import PrintEngine
from ..secret_store import SHOPIFY_ACCESS_TOKEN, SHOPIFY_CLIENT_SECRET, SecretStore
from ..security.policy import PolicyDenied
from .base import BackgroundService
from .shipping_services import AUTO, Route, is_domestic, offered_services, selection
from .shopify_orders import MANUAL_TAG, ManualItem, ManualOrders, OrderDetail, ProductChoice, Recipient
from .tracking import TRACKING_QUERY, Tracking, parse_tracking
from .orders_overview import (
    ACTION_STATES,
    OVERVIEW_QUERY,
    OVERVIEW_QUERY_BASIC,
    OrderRow,
    belongs,
    classify,
    friendly_reason,
    label_events,
    rule_for,
    shipping_problem,
    subtotal_of,
)

log = logging.getLogger(__name__)

PAID_STATUSES = {"PAID", "PARTIALLY_REFUNDED", "AUTHORIZED"}
FETCH_TRIES = 10  # vueltas buscando una guía comprada en Shopify antes de darla por perdida
DELIVERY_CHECK_SECONDS = 1800  # cada cuánto se revisan los envíos en camino (retrasos y problemas)
OPEN_FULFILLMENT = {"UNFULFILLED", "PARTIALLY_FULFILLED", "OPEN", "IN_PROGRESS", "PENDING_FULFILLMENT"}
SHIPPABLE_METHODS = {"SHIPPING", "PICKUP_POINT", None}

ORDERS_QUERY = """
query LabelBridgeOrders($first: Int!, $after: String, $query: String!, $ns: String!, $key: String!) {
  orders(first: $first, after: $after, query: $query, sortKey: UPDATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      legacyResourceId
      name
      createdAt
      updatedAt
      cancelledAt
      displayFinancialStatus
      displayFulfillmentStatus
      tags
      note
      requiresShipping
      totalPriceSet { shopMoney { amount currencyCode } }
      currentSubtotalPriceSet { shopMoney { amount } }
      shippingAddress {
        name firstName lastName company address1 address2 city province provinceCode zip country countryCodeV2 phone
      }
      shippingLine { title }
      lineItems(first: 30) { nodes { name sku quantity currentQuantity } }
      metafield(namespace: $ns, key: $key) { value }
    }
  }
}
"""

SHOP_QUERY = """
query LabelBridgeShop {
  shop { name myshopifyDomain currencyCode }
  currentAppInstallation { accessScopes { handle } }
}
"""

FULFILLMENT_ORDERS_QUERY = """
query LabelBridgeFulfillmentOrders($id: ID!) {
  order(id: $id) {
    id
    fulfillmentOrders(first: 10) {
      nodes { id status deliveryMethod { methodType } }
    }
  }
}
"""

PURCHASE_MUTATION = """
mutation LabelBridgePurchaseLabel($input: ShippingLabelPurchaseInput!) {
  shippingLabelPurchase(shippingLabelPurchase: $input) {
    shippingLabelPurchaseResult { id status }
    userErrors { field code message }
  }
}
"""

LABEL_RESULT_QUERY = """
query LabelBridgeLabelResult($id: ID!) {
  node(id: $id) {
    ... on ShippingLabelPurchaseResult {
      id
      status
      errors { code message }
      shippingLabels {
        id
        trackingInfo { number company url }
        shippingDocuments { documentType format url }
      }
    }
  }
}
"""

# Guías del pedido según su historial: también las compradas a mano en el admin de Shopify (el evento lleva su id).
ORDER_LABELS_QUERY = """
query LabelBridgeOrderLabels($id: ID!) {
  order(id: $id) {
    events(
      first: 20
      sortKey: CREATED_AT
      query: "action:shipping_label_created_success OR action:shipping_label_cancelled_success"
    ) {
      nodes { createdAt message ... on BasicEvent { action arguments } }
    }
  }
}
"""

# Una guía por su id, con sus documentos para imprimir (API 2026-10).
SHIPPING_LABEL_QUERY = """
query LabelBridgeShippingLabel($id: ID!) {
  shippingLabel(id: $id) {
    id
    trackingInfo { number company url }
    shippingDocuments { documentType format url }
  }
}
"""

TAGS_ADD_MUTATION = """
mutation LabelBridgeTagsAdd($id: ID!, $tags: [String!]!) {
  tagsAdd(id: $id, tags: $tags) { userErrors { field message } }
}
"""

# De dónde sale y adónde va un pedido, para preguntar a Shopify qué servicios ofrece en ese envío.
ROUTE_QUERY = """
query LabelBridgeRoute($id: ID!) {
  order(id: $id) {
    shippingAddress { countryCodeV2 }
    fulfillmentOrders(first: 10) {
      nodes { status deliveryMethod { methodType } assignedLocation { countryCode location { id } } }
    }
  }
}
"""
# Sin permiso para leer la sucursal: el país de salida alcanza para separar nacional e internacional.
ROUTE_QUERY_BASIC = ROUTE_QUERY.replace(" location { id }", "")

# Paqueterías de la tienda y sus servicios para esa ruta (read_shipping). Su «name» es el carrierCode de la compra.
SERVICES_QUERY = """
query LabelBridgeServices($origins: [ID!], $countries: [CountryCode!]) {
  availableCarrierServices {
    carrierService {
      name
      availableServicesForCountries(origins: $origins, countryCodes: $countries, restOfWorld: false) { name }
    }
  }
}
"""


# App de LabelBridge en el Dev Dashboard de Shopify: permisos para leer pedidos y para comprar guías.
DEV_DASHBOARD_URL = "https://dev.shopify.com/dashboard"
SCOPES_BASIC = "read_orders"
# Opcionales (sin ellos, lo demás funciona igual): read_products elige productos de la tienda en la guía manual y
# read_inventory muestra sus existencias; read_shipping muestra solo los servicios que Shopify ofrece en cada envío;
# read_customers dice en «Ver pedido» si el cliente ya compró antes.
SCOPES_LABELS = (
    "read_orders,write_orders,read_merchant_managed_fulfillment_orders,write_merchant_managed_fulfillment_orders,"
    "read_products,read_inventory,read_shipping,read_customers"
)
OPTIONAL_SCOPES = {
    "read_products": N_("elegir productos de la tienda en la guía manual"),
    "read_inventory": N_("ver las existencias en la guía manual"),
    "read_shipping": N_("ver solo los servicios que Shopify ofrece en cada envío"),
    "read_customers": N_("saber si el cliente ya compró antes"),
}


class ShopifyError(Exception):
    """uncertain=True: no se sabe si Shopify ejecutó la operación (error 5xx, corte a mitad...).
    `code` identifica el problema sin depender del idioma del mensaje (p. ej. "access_denied")."""

    def __init__(self, message: str, uncertain: bool = False, code: str = "") -> None:
        super().__init__(message)
        self.uncertain = uncertain
        self.code = code


class PurchaseRejected(ShopifyError):
    """Shopify respondió que NO compró la etiqueta (userErrors): se puede reintentar."""


def normalize_shop(shop: str) -> str:
    value = shop.strip().lower()
    value = re.sub(r"^https?://", "", value).split("/")[0]
    if value and "." not in value:
        value += ".myshopify.com"
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-]*\.myshopify\.com", value):
        raise ShopifyError(tr("Usa el dominio interno de la tienda: mi-tienda.myshopify.com"))
    return value


def admin_url(shop: str, path: str = "") -> str:
    """Página del admin de Shopify de la tienda (o el admin genérico si la tienda no es válida)."""
    try:
        handle = normalize_shop(shop).split(".")[0]
    except ShopifyError:
        return "https://admin.shopify.com"
    return f"https://admin.shopify.com/store/{handle}" + (f"/{path}" if path else "")


def package_input(p) -> dict[str, Any]:
    """La caja propia de Ajustes (PackageSettings) como paquete de Shopify Shipping."""
    return {
        "type": p.type or "BOX",
        "dimensions": {"length": float(p.length), "width": float(p.width), "height": float(p.height),
                       "unit": p.dim_unit or "INCHES"},
        "weight": {"value": float(p.weight), "unit": p.weight_unit or "OUNCES"},
    }


def _iso_to_ts(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return 0.0


def _ts_to_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def extract_urls(value: str) -> list[str]:
    """El metafield puede ser una URL, una lista JSON de URLs o un objeto con 'url'/'urls'."""
    text = (value or "").strip()
    if not text:
        return []
    candidates: list[Any] = [text]
    if text[:1] in "[{\"":
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                candidates = [parsed.get("url"), *(parsed.get("urls") or [])]
            elif isinstance(parsed, list):
                candidates = parsed
            else:
                candidates = [parsed]
        except json.JSONDecodeError:
            pass
    urls = []
    for item in candidates:
        if isinstance(item, str) and urlparse(item.strip()).scheme in ("http", "https"):
            urls.append(item.strip())
    return list(dict.fromkeys(urls))


class ShopifyClient:
    def __init__(self, settings: ShopifySettings, secrets: SecretStore) -> None:
        self.shop = normalize_shop(settings.shop)
        self.api_version = settings.api_version.strip() or "2026-10"
        self.auth_mode = settings.auth_mode
        self.client_id = settings.client_id.strip()
        self.secrets = secrets
        self._token = ""
        self._token_expiry = 0.0
        self.granted_scopes = ""
        self._token_lock = threading.Lock()
        self._fallbacks: dict[str, tuple[int, str]] = {}  # graphql_first: qué consulta funcionó, con qué permisos

    # --- autenticación ---------------------------------------------------------------
    def access_token(self, force: bool = False) -> str:
        if self.auth_mode == "token":
            token = self.secrets.get(SHOPIFY_ACCESS_TOKEN)
            if not token:
                raise ShopifyError(tr("Falta el token de acceso de la Admin API (shpat_…)"))
            return token
        with self._token_lock:
            if not force and self._token and time.time() < self._token_expiry - 300:
                return self._token
            return self._new_token()

    def _new_token(self) -> str:
        secret = self.secrets.get(SHOPIFY_CLIENT_SECRET)
        if not self.client_id or not secret:
            raise ShopifyError(tr("Faltan el Client ID y/o el Client secret de la app (Dev Dashboard)"))
        resp = net.request(
            "POST",
            f"https://{self.shop}/admin/oauth/access_token",
            data=urlencode(
                {"grant_type": "client_credentials", "client_id": self.client_id, "client_secret": secret}
            ).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            timeout=20,
            raise_for_status=False,
        )
        if resp.status != 200:
            detail = resp.body[:300].decode("utf-8", "replace")
            if "shop_not_permitted" in detail:
                raise ShopifyError(
                    tr("Shopify no permite client credentials en esta tienda: la app y la tienda deben estar "
                       "en la misma organización del Dev Dashboard (ver docs/SHOPIFY.md)")
                )
            raise ShopifyError(tr("Shopify rechazó las credenciales (HTTP {0}): {1}", resp.status, detail))
        data = resp.json() or {}
        self._token = str(data.get("access_token") or "")
        if not self._token:
            raise ShopifyError(tr("Shopify no devolvió un token de acceso"))
        self._token_expiry = time.time() + int(data.get("expires_in") or 86399)
        self.granted_scopes = str(data.get("scope") or "")
        log.info("Shopify: token renovado (válido 24 h)")
        return self._token

    # --- GraphQL ------------------------------------------------------------------------
    def graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        mutation: bool = False,
        optional: tuple[str, ...] = (),
        _retry: int = 0,
        _refresh: bool = False,
    ) -> dict[str, Any]:
        """mutation=True: nunca se reintenta algo que Shopify pudo haber ejecutado (evita compras dobles).

        optional: campos con datos protegidos de clientes (p. ej. «email»). Si Shopify no los aprobó para la app, los
        devuelve vacíos con un aviso en vez de fallar: se usa el resto de la respuesta."""
        resp = net.request(
            "POST",
            f"https://{self.shop}/admin/api/{self.api_version}/graphql.json",
            headers={"X-Shopify-Access-Token": self.access_token(force=_refresh), "Accept": "application/json"},
            json_body={"query": query, "variables": variables or {}},
            timeout=40,
            raise_for_status=False,
        )
        if resp.status == 401 and self.auth_mode != "token" and not _refresh:
            return self.graphql(query, variables, mutation=mutation, optional=optional, _retry=_retry, _refresh=True)
        if resp.status == 401:
            raise ShopifyError(tr("Token inválido o revocado (HTTP 401)"))
        if resp.status == 402:
            raise ShopifyError(tr("La tienda está pausada o sin plan activo (HTTP 402)"))
        if resp.status == 404:
            raise ShopifyError(tr("Tienda o versión de API no encontrada ({0}, {1})", self.shop, self.api_version))
        if (resp.status == 429 or (resp.status == 503 and not mutation)) and _retry < 3:
            time.sleep(float(resp.headers.get("retry-after", "2") or 2))
            return self.graphql(query, variables, mutation=mutation, optional=optional, _retry=_retry + 1)
        if resp.status >= 400:
            raise ShopifyError(
                tr("HTTP {0}: {1}", resp.status, resp.body[:300].decode('utf-8', 'replace')), uncertain=resp.status >= 500
            )
        payload = resp.json() or {}
        errors = payload.get("errors") or []
        if errors:
            codes = {(e.get("extensions") or {}).get("code") for e in errors}
            if "THROTTLED" in codes and _retry < 3:  # rechazada antes de ejecutarse: reintentar es seguro
                time.sleep(2 + _retry * 2)
                return self.graphql(query, variables, mutation=mutation, optional=optional, _retry=_retry + 1)
            if optional and payload.get("data") and all(
                set(map(str, e.get("path") or [])) & set(optional) for e in errors
            ):
                log.info("Shopify: datos protegidos sin aprobar para la app (llegan vacíos): %s",
                         "; ".join(str(e.get("message", "")) for e in errors)[:300])
                return payload["data"]
            messages = "; ".join(str(e.get("message", e)) for e in errors)
            denied = "access denied" in messages.lower() or "ACCESS_DENIED" in codes
            if denied:
                messages += tr(" (faltan permisos/scopes en la app de Shopify)")
            raise ShopifyError(messages, uncertain="INTERNAL_SERVER_ERROR" in codes, code="access_denied" if denied else "")
        return payload.get("data") or {}

    def graphql_first(
        self, queries: tuple[str, ...], variables: dict[str, Any] | None = None, *, optional: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        """La primera de las consultas que la app puede hacer: las primeras piden permisos que quizá no tiene (y las
        siguientes, menos). Recuerda cuál funcionó para no volver a probar las otras mientras no cambien los permisos."""
        key = queries[0]
        index, scopes = self._fallbacks.get(key, (0, self.granted_scopes))
        if scopes != self.granted_scopes:
            index = 0
        for position in range(index, len(queries)):
            try:
                data = self.graphql(queries[position], variables, optional=optional)
            except ShopifyError as exc:
                if exc.code != "access_denied" or position == len(queries) - 1:
                    raise
                continue
            self._fallbacks[key] = (position, self.granted_scopes)
            return data
        return {}

    # --- operaciones -----------------------------------------------------------------------
    def shop_info(self) -> dict[str, Any]:
        data = self.graphql(SHOP_QUERY)
        scopes = [s["handle"] for s in (data.get("currentAppInstallation") or {}).get("accessScopes", [])]
        return {**(data.get("shop") or {}), "scopes": scopes}

    def orders_updated_since(
        self, since_iso: str, namespace: str, key: str, max_pages: int = 10, after: str | None = None
    ) -> tuple[list[dict], str | None]:
        """Pedidos actualizados desde since_iso (más antiguos primero).

        Devuelve también el cursor para continuar si quedaron páginas sin leer.
        """
        orders: list[dict] = []
        for _ in range(max_pages):
            data = self.graphql(
                ORDERS_QUERY,
                {
                    "first": 10,
                    "after": after,
                    "query": f"updated_at:>='{since_iso}'",
                    "ns": namespace or "custom",
                    "key": key or "label_url",
                },
            )
            connection = data.get("orders") or {}
            orders.extend(connection.get("nodes") or [])
            page = connection.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return orders, None
            after = page.get("endCursor")
        return orders, after

    def order(self, legacy_id: str, namespace: str, key: str) -> dict | None:
        """Un pedido (con los mismos datos que el ciclo) por su número interno."""
        data = self.graphql(
            ORDERS_QUERY,
            {"first": 1, "after": None, "query": f"id:{int(legacy_id)}", "ns": namespace or "custom",
             "key": key or "label_url"},
        )
        nodes = (data.get("orders") or {}).get("nodes") or []
        return nodes[0] if nodes else None

    def orders_overview(self, created_from_iso: str, max_pages: int = 40) -> tuple[list[dict], bool]:
        """Pedidos creados desde esa fecha (los más nuevos primero) para la pestaña Pedidos, hasta 25 × max_pages.
        Devuelve también si quedaron pedidos más viejos sin leer (para avisarlo en pantalla)."""
        orders: list[dict] = []
        after = None
        query = OVERVIEW_QUERY
        pages = 0
        while pages < max_pages:
            try:
                data = self.graphql(query, {"first": 25, "after": after, "query": f"created_at:>='{created_from_iso}'"})
            except ShopifyError as exc:
                if query is OVERVIEW_QUERY and exc.code == "access_denied":
                    query = OVERVIEW_QUERY_BASIC  # la app no puede leer órdenes de preparación: sin esa parte
                    continue
                raise
            pages += 1
            connection = data.get("orders") or {}
            orders.extend(connection.get("nodes") or [])
            page = connection.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return orders, False
            after = page.get("endCursor")
        return orders, True

    def fulfillment_orders(self, order_gid: str) -> list[dict]:
        data = self.graphql(FULFILLMENT_ORDERS_QUERY, {"id": order_gid})
        return ((data.get("order") or {}).get("fulfillmentOrders") or {}).get("nodes") or []

    def purchase_label(
        self, fulfillment_order_id: str, notify_customer: bool, *, package: dict | None = None,
        total_weight: dict | None = None, preferred: dict | None = None,
    ) -> str:
        """Compra la guía con Shopify Shipping. Sin `package`/`total_weight`, Shopify usa su paquete predeterminado y
        calcula el peso con los productos. Sin `preferred` (paquetería y servicio), Shopify elige: el servicio que pagó
        el cliente o la paquetería preferida de la tienda."""
        ship_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        purchase: dict[str, Any] = {
            "fulfillmentOrderId": fulfillment_order_id,
            "shippingDatetime": ship_at,
            "notifyCustomer": bool(notify_customer),
        }
        if package:
            purchase["packageInfo"] = {"customPackage": package}
        if total_weight:
            purchase["totalWeight"] = total_weight
        if preferred:
            purchase["preferredRateSelection"] = preferred
        data = self.graphql(PURCHASE_MUTATION, {"input": purchase}, mutation=True)
        result = data.get("shippingLabelPurchase") or {}
        errors = result.get("userErrors") or []
        if errors:
            raise PurchaseRejected("; ".join(f"{e.get('code') or ''} {e.get('message')}".strip() for e in errors))
        purchase = result.get("shippingLabelPurchaseResult") or {}
        if not purchase.get("id"):
            raise ShopifyError(tr("Shopify no devolvió el resultado de la compra"), uncertain=True)
        return str(purchase["id"])

    def label_result(self, result_id: str) -> dict[str, Any]:
        return self.graphql(LABEL_RESULT_QUERY, {"id": result_id}).get("node") or {}

    def order_labels(self, order_gid: str) -> dict[str, dict[str, Any]]:
        """Las guías del pedido ({id: {cost, voided…}}), también las compradas a mano en Shopify."""
        return label_events(self.graphql(ORDER_LABELS_QUERY, {"id": order_gid}).get("order") or {})

    def shipping_label(self, label_id: str) -> dict[str, Any]:
        """Una guía con su seguimiento y sus documentos (PDF/ZPL) para imprimir."""
        gid = label_id if str(label_id).startswith("gid://") else f"gid://shopify/ShippingLabel/{label_id}"
        return self.graphql(SHIPPING_LABEL_QUERY, {"id": gid}).get("shippingLabel") or {}

    def route(self, order_gid: str) -> tuple[str, str, str]:
        """(sucursal de salida, país de salida, país de destino) del envío pendiente del pedido («» si no se sabe)."""
        order = self.graphql_first((ROUTE_QUERY, ROUTE_QUERY_BASIC), {"id": order_gid}).get("order") or {}
        destination = str((order.get("shippingAddress") or {}).get("countryCodeV2") or "")
        nodes = (order.get("fulfillmentOrders") or {}).get("nodes") or []
        # La sucursal del envío pendiente (o, si no hay, la de cualquiera: el país de salida es el mismo).
        for fo in sorted(nodes, key=lambda fo: fo.get("status") not in ("OPEN", "IN_PROGRESS")):
            place = fo.get("assignedLocation") or {}
            return str((place.get("location") or {}).get("id") or ""), str(place.get("countryCode") or ""), destination
        return "", "", destination

    def carrier_services(self, origin_id: str, country: str) -> dict[str, set[str]]:
        """Qué servicios ofrece Shopify desde esa sucursal a ese país, por paquetería (permiso read_shipping)."""
        return offered_services(self.graphql(SERVICES_QUERY, {"origins": [origin_id], "countries": [country]}))

    def add_tags(self, order_gid: str, tags: list[str]) -> None:
        data = self.graphql(TAGS_ADD_MUTATION, {"id": order_gid, "tags": tags}, mutation=True)
        errors = (data.get("tagsAdd") or {}).get("userErrors") or []
        if errors:
            raise ShopifyError("; ".join(e.get("message", "") for e in errors))


class ShopifyService(BackgroundService):
    name = "shopify"
    label = "Shopify"

    def __init__(
        self, cfg: ConfigManager, secrets: SecretStore, store: JobStore, engine: PrintEngine, events: ev.Events
    ) -> None:
        super().__init__(events)
        self.cfg = cfg
        self.secrets = secrets
        self.store = store
        self.engine = engine
        self._client: ShopifyClient | None = None
        self._tag_queue: deque[str] = deque()
        self._limit_warned_day = 0.0
        self._cycle_lock = threading.Lock()
        self._deliveries_at = time.time() - DELIVERY_CHECK_SECONDS + 120  # la primera, a los 2 minutos
        self._resets = 0  # «Reprocesar» durante una vuelta: esa vuelta no pisa el cursor nuevo
        self.overview_truncated = False
        events.subscribe(self._on_event)
        engine.url_auth_providers.append(self._auth_headers_for)

    def _auth_headers_for(self, url: str, source: str = "") -> dict[str, str]:
        """Las descargas de documentos de la propia tienda llevan el token (sin guardarlo en la cola), y solo en los
        trabajos que creó LabelBridge desde Shopify: uno de la API local o de n8n nunca lo usa."""
        client = self._client
        if source != "shopify" or client is None or urlparse(url).hostname != client.shop:
            return {}
        try:
            return {"X-Shopify-Access-Token": client.access_token()}
        except ShopifyError:
            return {}

    def enabled(self) -> bool:
        settings = self.cfg.data.shopify
        return settings.enabled and bool(settings.shop.strip())

    def _on_event(self, kind: str, data: Any) -> None:
        if kind != ev.JOB or not isinstance(data, dict):
            return
        if data.get("source") == "shopify" and data.get("status") == "done" and self.cfg.data.shopify.tag_after_print:
            gid = (data.get("meta") or {}).get("order_gid")
            if gid:
                self._tag_queue.append(gid)
                self.wake()

    def client(self) -> ShopifyClient:
        settings = self.cfg.data.shopify
        current = self._client
        if (
            current is None
            or current.shop != normalize_shop(settings.shop)
            or current.auth_mode != settings.auth_mode
            or current.client_id != settings.client_id.strip()
            or current.api_version != (settings.api_version.strip() or "2026-10")
        ):
            self._client = ShopifyClient(settings, self.secrets)
        return self._client

    def _key(self, suffix: str) -> str:
        return f"shopify:{normalize_shop(self.cfg.data.shopify.shop)}:{suffix}"

    # --- registro por pedido (pestaña Pedidos) -------------------------------------------------
    def _ledger_key(self, legacy: str) -> str:
        return self._key(f"ledger:{legacy}")

    def ledger(self, legacy: str) -> dict:
        try:
            return json.loads(self.store.kv_get(self._ledger_key(str(legacy))) or "{}")
        except json.JSONDecodeError:
            return {}

    def ledgers(self) -> dict[str, dict]:
        prefix = self._key("ledger:")
        out: dict[str, dict] = {}
        for key, value in self.store.kv_items(prefix).items():
            try:
                out[key[len(prefix):]] = json.loads(value)
            except json.JSONDecodeError:
                continue
        return out

    def _note(self, legacy: str, state: str, **data: Any) -> None:
        """Anota qué pasó con un pedido (compra hecha, fallida, a mano…) para la pestaña Pedidos."""
        if not legacy:
            return
        entry = {"state": state, "at": time.time(), **{k: v for k, v in data.items() if v not in (None, "")}}
        self.store.kv_set(self._ledger_key(str(legacy)), json.dumps(entry, ensure_ascii=False))
        self.events.emit(ev.ORDERS, {"order": str(legacy), "state": state})

    def since_ts(self) -> float:
        value = self.store.kv_get(self._key("since"))
        if value is None:
            now = time.time()
            self.store.kv_set(self._key("since"), str(now))
            return now
        return float(value)

    def reset_since(self, hours_back: float = 0.0) -> float:
        """Procesar pedidos desde ahora (o desde hace X horas)."""
        self._resets += 1
        since = time.time() - max(0.0, hours_back) * 3600
        self.store.kv_set(self._key("since"), str(since))
        self.store.kv_set(self._key("cursor"), _ts_to_iso(since))
        self.store.kv_set(self._key("resume"), "null")
        self.wake()
        return since

    # --- bucle -----------------------------------------------------------------------------
    def run(self, stop: threading.Event, wake: threading.Event) -> None:
        self.set_status("connecting", tr("Conectando con Shopify…"))
        failures = 0
        while not stop.is_set():
            settings = self.cfg.data.shopify
            try:
                client = self.client()
                summary = self.cycle(client, settings, stop)
                failures = 0
                if time.time() - self._deliveries_at >= DELIVERY_CHECK_SECONDS:
                    self._deliveries_at = time.time()
                    try:
                        self.check_deliveries()
                    except (ShopifyError, net.NetworkError, net.HttpError) as exc:
                        log.warning("Shopify: no se pudieron revisar las entregas: %s", exc)
                self.set_status("ok", tr("Conectado a {0} · revisado a las {1}{2}", client.shop, time.strftime('%H:%M:%S'), summary))
            except (ShopifyError, net.NetworkError, net.HttpError) as exc:
                failures += 1
                self.set_status("error", str(exc))
                log.warning("Shopify: %s", exc)
            except Exception as exc:
                failures += 1
                log.exception("Shopify: error inesperado")
                self.set_status("error", tr("Error: {0}", exc))
            delay = max(15, settings.poll_seconds)
            if failures:
                delay = min(600, delay * 2 ** min(failures, 4))
            if not self.sleep(stop, wake, delay):
                break

    def cycle(self, client: ShopifyClient, settings: ShopifySettings, stop: threading.Event | None = None) -> str:
        # Un solo ciclo a la vez, aunque un reinicio deje un hilo anterior terminando su vuelta.
        with self._cycle_lock:
            return self._cycle(client, settings, stop or threading.Event())

    def _cycle(self, client: ShopifyClient, settings: ShopifySettings, stop: threading.Event) -> str:
        resets = self._resets
        since = self.since_ts()
        cursor = self.store.kv_get(self._key("cursor")) or _ts_to_iso(since)
        try:
            resume = json.loads(self.store.kv_get(self._key("resume")) or "null")
        except json.JSONDecodeError:
            resume = None
        if resume:
            # Muchos pedidos en la última vuelta: seguimos donde quedó la paginación.
            query_from, after = resume["from"], resume["after"]
        else:
            query_from, after = _ts_to_iso(_iso_to_ts(cursor) - 120), None  # margen: relojes e indexación
        orders, next_after = client.orders_updated_since(
            query_from, settings.metafield_namespace, settings.metafield_key, after=after
        )
        queued = 0
        newest = cursor
        for order in orders:
            if stop.is_set():
                return tr(" · detenido")  # sin guardar progreso: la próxima vuelta repite estos pedidos
            queued += self._process_order(client, settings, order, since, stop)
            if order.get("updatedAt", "") > newest:
                newest = order["updatedAt"]
        # Progreso guardado solo cuando todos los pedidos de estas páginas quedaron procesados (y si nadie pidió
        # «Reprocesar» mientras tanto: la próxima vuelta empieza desde ahí).
        if resets == self._resets:
            self.store.kv_set(self._key("cursor"), newest)
            self.store.kv_set(
                self._key("resume"), json.dumps({"from": query_from, "after": next_after} if next_after else None)
            )
        queued += self._check_pending_purchases(client, stop=stop)
        queued += self._fetch_bought_labels(client, stop)
        self._flush_tags(client)
        if queued:
            self.engine.wake()
        if next_after:
            self.wake()  # quedan páginas: la siguiente vuelta empieza enseguida
        return tr(" · {0} etiqueta(s) en cola", queued) if queued else ""

    # --- por pedido ----------------------------------------------------------------------------
    def _process_order(
        self, client: ShopifyClient, s: ShopifySettings, order: dict, since: float, stop: threading.Event
    ) -> int:
        legacy = str(order.get("legacyResourceId") or order.get("id", "").rsplit("/", 1)[-1])
        name = order.get("name") or legacy
        if order.get("cancelledAt"):
            if self.ledger(legacy).get("state") in ACTION_STATES:
                self._note(legacy, "resolved", name=name)  # cancelado: ya no hace falta la guía
            return 0
        if shipping_problem(order):
            # Sin envío o sin a quién y adónde mandarlo (retiro en tienda, venta en el local, un pedido de otro canal
            # sin dirección…): LabelBridge no lo trata (ni aviso, ni etiquetas, ni guía).
            return 0
        tags = [t.lower() for t in order.get("tags") or []]
        if s.required_tag.strip() and s.required_tag.strip().lower() not in tags:
            return 0
        # Pedido manual (Nueva guía manual o el asistente): su guía la compra quien lo creó, con confirmación. Lo
        # automático no lo toca (ni aviso de pedido nuevo, ni etiqueta interna, ni compra).
        manual = MANUAL_TAG.lower() in tags
        paid_ok = not s.only_paid or order.get("displayFinancialStatus") in PAID_STATUSES
        open_ok = not s.only_unfulfilled or order.get("displayFulfillmentStatus") in OPEN_FULFILLMENT
        is_new = _iso_to_ts(order.get("createdAt", "")) >= since
        meta = {"order_gid": order.get("id"), "order_name": name, "order_id": legacy}
        queued = 0
        announced = self._key(f"order:{legacy}:announced")
        fresh = _iso_to_ts(order.get("createdAt", "")) >= self._announce_since()
        if is_new and paid_ok and fresh and not manual and not self.store.seen(announced):
            self.store.mark_seen(announced)
            self._announce(order, s)
        entry = self.ledger(legacy)
        noted = entry.get("state")
        if noted in ACTION_STATES and order.get("displayFulfillmentStatus") not in OPEN_FULFILLMENT:
            # Se resolvió en Shopify (guía comprada ahí, p. ej. la de FedEx, o enviado): su guía se trae y se imprime.
            # También si la compra automática está apagada pero alguien eligió «comprarla en Shopify» en la app.
            if s.buy_shipping_label or entry.get("fetch"):
                self._queue_fetch(legacy, meta)
            self._note(legacy, "resolved", name=name)
            noted = "resolved"

        if s.print_order_label and is_new and paid_ok and open_ok and not manual:
            key = self._key(f"order:{legacy}:label")
            if not self.store.seen(key):
                queued += self._enqueue(
                    JobRequest(
                        external_id=f"pedido-{legacy}",
                        title=tr("Pedido {0}", name),
                        printer=s.order_label_printer,
                        format="order",
                        order=order,
                        meta=meta,
                    )
                )
                self.store.mark_seen(key)

        if s.metafield_label and not manual:
            for url in extract_urls(str((order.get("metafield") or {}).get("value") or "")):
                digest = hashlib.sha1(url.encode()).hexdigest()[:16]
                key = self._key(f"order:{legacy}:mf:{digest}")
                if self.store.seen(key):
                    continue
                queued += self._enqueue(
                    JobRequest(
                        external_id=f"metafield-{legacy}-{digest}",
                        title=tr("Etiqueta {0}", name),
                        printer=s.metafield_printer,
                        url=url,
                        meta=meta,
                    )
                )
                self.store.mark_seen(key)

        if s.buy_shipping_label and is_new and paid_ok and open_ok and not manual:
            rule = rule_for(s.shipping_rules, (order.get("shippingLine") or {}).get("title") or "", subtotal_of(order))
            if rule is not None and not rule.auto:
                # FedEx (y lo que se marque «en Shopify»): Shopify no deja comprarla por API, nunca otra paquetería.
                # Se compra en el pedido con un clic y LabelBridge la imprime sola al verla (_fetch_bought_labels).
                if noted != "manual":
                    self._note(legacy, "manual", name=name, carrier=rule.carrier)
                    self.events.notify(
                        tr("Pedido {0}: compra la guía de {1} en Shopify", name, rule.carrier),
                        tr("Shopify no deja comprar guías de {0} automáticamente: cómprala en el pedido (1 clic) y "
                           "LabelBridge la imprime sola.", rule.carrier),
                        "error", kind="action",
                    )
            else:
                queued += self._buy_labels(client, s, order, meta, stop)
        return queued

    def overview(self, days: int = 30, now: float | None = None) -> list[OrderRow]:
        """Los pedidos de los últimos `days` días, ordenados para la pestaña Pedidos."""
        s = self.cfg.data.shopify
        now = now or time.time()
        orders, self.overview_truncated = self.client().orders_overview(_ts_to_iso(now - days * 86400))
        ledgers = self.ledgers()
        jobs: dict[str, list[dict]] = {}
        for job in self.store.list(limit=3000):
            meta = job.get("meta") or {}
            if job.get("source") == "shopify" and "tracking" in meta and meta.get("order_id"):
                jobs.setdefault(str(meta["order_id"]), []).append(job)
        since = self.since_ts()
        rows = []
        for order in orders:
            legacy = str(order.get("legacyResourceId") or str(order.get("id", "")).rsplit("/", 1)[-1])
            row = classify(order, settings=s, ledger=ledgers.get(legacy), jobs=jobs.get(legacy), since=since, now=now,
                           limits=self.cfg.data.notifications)
            if belongs(row):  # solo pedidos que se envían, con a quién y adónde mandarlos
                rows.append(row)
        return rows

    def check_deliveries(self, now: float | None = None) -> int:
        """Retrasos y problemas de entrega de los últimos 30 días: cada uno avisa una vez (con su sonido) y la
        pestaña Pedidos se actualiza. Devuelve cuántos son nuevos."""
        fresh = 0
        for row in self.overview(days=30, now=now):
            if not row.alert:
                continue
            key = self._key(f"alert:{row.legacy}:{row.alert}")
            if self.store.seen(key):
                continue
            self.store.mark_seen(key)
            fresh += 1
            self.events.notify(tr("{0}: {1}", row.name, row.alert_title), row.alert_reason, "error", kind="delivery")
        if fresh:
            self.events.emit(ev.ORDERS, {"alerts": fresh})
        return fresh

    def _enqueue(self, req: JobRequest) -> int:
        try:
            job, created = self.engine.submit(req, "shopify")
        except PolicyDenied as exc:
            log.error("Shopify: %s rechazado por seguridad: %s", req.title, exc)
            self.events.notify(tr("Etiqueta bloqueada por seguridad"), f"{req.title}: {exc}", "error", kind="action")
            return 0
        if created:
            log.info("Shopify: en cola %s", job["title"])
        return int(created)

    # --- compra de etiquetas Shopify Shipping ----------------------------------------------------
    def _fo_key(self, fo: dict | str) -> str:
        """La marca «ya se pidió la guía» de una orden de preparación (se pone ANTES de comprar)."""
        fo_id = fo["id"] if isinstance(fo, dict) else fo
        return self._key(f"fo:{str(fo_id).rsplit('/', 1)[-1]}:label")

    def _uncertain(self, meta: dict, exc: Exception) -> None:
        """No se sabe si Shopify compró la guía: queda marcada (nunca se repite sola) y se avisa."""
        self._note(meta["order_id"], "uncertain", name=meta["order_name"], reason=str(exc))
        log.error("Shopify: compra de etiqueta sin confirmar para %s: %s", meta["order_name"], exc)
        self.events.notify(
            tr("Revisa la etiqueta en Shopify"),
            tr("{0}: no se pudo confirmar si la etiqueta se compró ({1}). Revísalo en Shopify antes de comprarla a mano.", meta['order_name'], exc),
            "error", kind="action",
        )

    def _buy_labels(
        self, client: ShopifyClient, s: ShopifySettings, order: dict, meta: dict, stop: threading.Event, *,
        options: dict | None = None, fulfillment_orders: list[dict] | None = None, wait: float = 20,
    ) -> int:
        """Compra la guía de cada orden de preparación abierta. Con `options` la pidió una persona desde Pedidos
        (paquete, peso y aviso al cliente elegidos ahí; sin el límite diario de las compras automáticas)."""
        queued = 0
        problem = shipping_problem(order)
        if problem:  # nunca una guía sin sentido (sin envío o sin dirección): quien llama ya lo explica
            log.warning("Shopify: %s no lleva guía: %s", meta.get("order_name"), problem)
            return 0
        manual = options is not None
        if manual:
            package, weight, notify = options.get("package"), options.get("total_weight"), options.get("notify")
            preferred = selection(options.get("service") or AUTO)
        else:
            package = package_input(s.package) if s.package.use_custom and s.package.auto else None
            weight, notify, preferred = None, s.notify_customer, None
        if fulfillment_orders is None:
            fulfillment_orders = client.fulfillment_orders(order["id"])
        for fo in fulfillment_orders:
            if stop.is_set():
                break
            if fo.get("status") not in ("OPEN", "IN_PROGRESS"):
                continue
            method = (fo.get("deliveryMethod") or {}).get("methodType")
            if method not in SHIPPABLE_METHODS:
                continue
            fo_id = str(fo["id"])
            key = self._fo_key(fo_id)
            if self.store.seen(key):
                continue
            attempts_key = key + ":intentos"
            attempts = int(self.store.kv_get(attempts_key) or 0)
            if attempts >= 3:
                continue
            today = self.store.start_of_today()
            if not manual and self.store.count_seen(self._key("fo:"), today) >= max(0, s.max_labels_per_day):
                if self.ledger(meta["order_id"]).get("state") != "limit":
                    self._note(meta["order_id"], "limit", name=meta["order_name"])
                if self._limit_warned_day != today:
                    self._limit_warned_day = today
                    self.events.notify(
                        tr("Límite diario de etiquetas"),
                        tr("Se alcanzó el máximo de {0} etiquetas compradas hoy. No se comprarán más.", s.max_labels_per_day),
                        "error", kind="action",
                    )
                return queued
            self.store.kv_set(attempts_key, str(attempts + 1))
            # La compra se registra ANTES de pedirla: si algo falla a mitad, nunca se compra dos veces.
            self.store.mark_seen(key)
            try:
                result_id = client.purchase_label(fo_id, bool(notify), package=package, total_weight=weight,
                                                  preferred=preferred)
            except PurchaseRejected as exc:
                # Shopify contestó que no compró: se puede volver a intentar (p. ej. tras corregir el paquete).
                self.store.unmark_seen(key)
                self._note(meta["order_id"], "failed", name=meta["order_name"], reason=str(exc))
                log.error("Shopify rechazó la compra de la etiqueta de %s: %s", meta["order_name"], exc)
                if not manual:  # quien la pidió desde la app ya ve el motivo en su ventana
                    self.events.notify(
                        tr("No se pudo comprar la etiqueta"),
                        tr("{0}: {1}. Corrígelo y pulsa «Imprimir guía» en la pestaña Pedidos.", meta['order_name'],
                           friendly_reason(str(exc))),
                        "error", kind="action",
                    )
                continue
            except (ShopifyError, net.NetworkError, net.HttpError) as exc:
                if not getattr(exc, "uncertain", False) and not getattr(exc, "maybe_sent", False):
                    self.store.unmark_seen(key)  # seguro que no llegó a Shopify
                    raise
                self._uncertain(meta, exc)  # no sabemos si Shopify la compró: no se reintenta sola
                continue
            except Exception as exc:  # cualquier otro fallo a mitad de la compra: tampoco se sabe si se pagó
                log.exception("Shopify: fallo inesperado comprando la guía de %s", meta["order_name"])
                self._uncertain(meta, exc)
                continue
            pending = self._pending()
            pending[result_id] = {**meta, "started": time.time(), "fo_key": key}
            self._save_pending(pending)
            self._note(meta["order_id"], "purchasing", name=meta["order_name"])
            log.info("Shopify: compra de etiqueta iniciada para %s", meta["order_name"])
            queued += self._check_pending_purchases(client, wait_seconds=wait, stop=stop)
        return queued

    def _announce_since(self) -> float:
        """Desde cuándo se avisa de pedidos nuevos: desde que esta versión empezó a avisar (al actualizar, los
        pedidos de antes que cambien en Shopify no suenan como nuevos)."""
        value = self.store.kv_get(self._key("announce_since"))
        if value is None:
            value = str(time.time() - 300)
            self.store.kv_set(self._key("announce_since"), value)
        return float(value)

    def _announce(self, order: dict, s: ShopifySettings) -> None:
        """«Pedido nuevo» (con su sonido, según Ajustes): quién, cuánto y por qué paquetería sale."""
        address = order.get("shippingAddress") or {}
        money = (order.get("totalPriceSet") or {}).get("shopMoney") or {}
        parts = [str(address.get("name") or "").strip()]
        try:
            parts.append(f"{float(money.get('amount') or 0):,.2f} {money.get('currencyCode') or ''}".strip())
        except (TypeError, ValueError):
            pass
        if address:
            rule = rule_for(s.shipping_rules, (order.get("shippingLine") or {}).get("title") or "", subtotal_of(order))
            parts.append(tr("sale por {0}", rule.carrier if rule is not None else s.default_carrier))
        self.events.notify(tr("Pedido nuevo {0}", order.get("name") or ""), " · ".join(p for p in parts if p),
                           kind="new_order")

    def buy_label_now(
        self, legacy: str, *, package: dict | None = None, total_weight: dict | None = None, notify: bool | None = None,
        service: str = AUTO, wait: float = 45,
    ) -> dict:
        """«Imprimir guía» desde Pedidos, confirmada por la persona: compra con Shopify Shipping (aunque se haya
        llegado al límite diario o a los intentos automáticos) con el servicio elegido (AUTO: el que elija Shopify),
        espera el resultado y la imprime. Shopify deja el pedido preparado con su número de seguimiento.

        Devuelve el registro del pedido: state purchased (carrier, tracking y, si Shopify ya lo anotó, cost), failed
        (reason) o purchasing."""
        s = self.cfg.data.shopify
        client = self.client()
        options = {"package": package, "total_weight": total_weight, "service": service,
                   "notify": s.notify_customer if notify is None else bool(notify)}
        # Todo dentro del candado del ciclo: la compra automática no puede comprar este pedido al mismo tiempo.
        with self._cycle_lock:
            busy = any(str(info.get("order_id")) == str(legacy) for info in self._pending().values())
            if busy or self.ledger(legacy).get("state") in ("uncertain", "purchasing"):
                raise ShopifyError(tr("Hay una compra de ese pedido en curso o sin confirmar: revísala en Shopify antes de comprar otra."))
            order = client.order(legacy, s.metafield_namespace, s.metafield_key)
            if order is None or str(order.get("legacyResourceId") or "") not in ("", str(legacy)):
                raise ShopifyError(tr("Shopify no encontró ese pedido."))
            if order.get("cancelledAt"):
                raise ShopifyError(tr("Ese pedido está cancelado."))
            problem = shipping_problem(order)
            if problem:
                raise ShopifyError(tr("{0} no puede llevar guía: {1}.", order.get("name") or legacy, problem))
            open_fos = [
                fo for fo in client.fulfillment_orders(order["id"])
                if fo.get("status") in ("OPEN", "IN_PROGRESS")
                and (fo.get("deliveryMethod") or {}).get("methodType") in SHIPPABLE_METHODS
            ]
            if not open_fos:
                raise ShopifyError(tr("Ese pedido no tiene productos pendientes de envío."))
            # Una orden con la marca puesta ya tiene una compra hecha, en curso o dudosa: esa nunca se repite.
            fresh = [fo for fo in open_fos if not self.store.seen(self._fo_key(fo))]
            if not fresh:
                raise ShopifyError(tr("Ese pedido ya tiene una compra hecha o sin confirmar: revísalo en Shopify antes de comprar otra."))
            for fo in fresh:  # la persona decide: se olvidan los intentos automáticos que fallaron
                self.store.kv_set(self._fo_key(fo) + ":intentos", "0")
            meta = {"order_gid": order.get("id"), "order_name": order.get("name") or legacy, "order_id": str(legacy)}
            queued = self._buy_labels(client, s, order, meta, threading.Event(), options=options,
                                      fulfillment_orders=fresh, wait=wait)
        if queued:
            self.engine.wake()
        entry = {**self.ledger(legacy), "queued": queued}
        if entry.get("state") == "purchased" and entry.get("label"):
            try:  # lo que costó, según el historial del pedido (si Shopify ya lo anotó)
                cost = client.order_labels(str(order["id"])).get(str(entry["label"]), {}).get("cost")
            except (ShopifyError, net.NetworkError, net.HttpError):
                cost = None
            if cost:
                entry["cost"] = cost
        return entry

    # --- detalle, instrucciones y pedidos manuales (ver shopify_orders) -------------------------
    @staticmethod
    def order_gid(order: str) -> str:
        """«1234», «#1001» no: el número interno o el gid del pedido → gid."""
        text = str(order).strip()
        if text.startswith("gid://shopify/Order/"):
            return text
        if not text.isdigit():
            raise ShopifyError(tr("Pedido no válido: {0}", text))
        return f"gid://shopify/Order/{text}"

    def resolve_order(self, order: str) -> str:
        """«#1023», «1023» o el número interno → número interno (legacyResourceId)."""
        return ManualOrders(self.client()).resolve(order).rsplit("/", 1)[-1]

    def order_detail(self, order: str) -> OrderDetail:
        return ManualOrders(self.client()).detail(self.order_gid(order))

    def set_instructions(self, order: str, text: str, append: bool = False) -> str:
        """Las instrucciones del pedido (en su nota de Shopify). Devuelve cómo quedaron."""
        return ManualOrders(self.client()).set_instructions(self.order_gid(order), text, append=append)

    def search_products(self, text: str) -> list[ProductChoice]:
        return ManualOrders(self.client()).products(text)

    def shop_currency(self) -> str:
        if not getattr(self, "_currency", ""):
            self._currency = str(self.client().shop_info().get("currencyCode") or "USD")
        return self._currency

    def create_manual_order(self, recipient: Recipient, items: list[ManualItem], note: str = "",
                            decrement: bool = True) -> dict[str, str]:
        """Crea el pedido manual (marcado, pagado y sin cobrar al cliente). La guía se compra después con
        buy_label_now, como cualquier pedido: lo automático no lo toca."""
        created = ManualOrders(self.client()).create(recipient, items, note, decrement=decrement,
                                                     currency=self.shop_currency())
        log.info("Pedido manual creado: %s", created.get("name"))
        return created

    def _pending(self) -> dict[str, dict]:
        try:
            return json.loads(self.store.kv_get(self._key("pending")) or "{}")
        except json.JSONDecodeError:
            return {}

    def _save_pending(self, pending: dict[str, dict]) -> None:
        self.store.kv_set(self._key("pending"), json.dumps(pending))

    def _check_pending_purchases(
        self, client: ShopifyClient, wait_seconds: float = 0, stop: threading.Event | None = None
    ) -> int:
        stop = stop or threading.Event()
        pending = self._pending()
        if not pending:
            return 0
        deadline = time.time() + wait_seconds
        queued = 0
        while pending:
            for result_id, info in list(pending.items()):
                age = time.time() - float(info.get("started", 0))
                try:
                    result = client.label_result(result_id)
                except (ShopifyError, net.NetworkError, net.HttpError) as exc:
                    log.warning("Shopify: no se pudo consultar la compra de %s: %s", info.get("order_name"), exc)
                    result = {}  # se vuelve a mirar en la próxima vuelta; si pasa una hora, queda sin confirmar
                status = result.get("status")
                if status == "PURCHASED":
                    documents, new_jobs = self._enqueue_documents(result, info)
                    queued += new_jobs
                    if documents:
                        pending.pop(result_id)
                        label = (result.get("shippingLabels") or [{}])[0]
                        tracking = label.get("trackingInfo") or {}
                        self._note(
                            info.get("order_id", ""), "purchased", name=info.get("order_name"),
                            carrier=tracking.get("company"), tracking=tracking.get("number"),
                            tracking_url=tracking.get("url"), label=str(label.get("id") or "").rsplit("/", 1)[-1],
                        )
                    elif age > 600:
                        self._note(info.get("order_id", ""), "no_document", name=info.get("order_name"))
                        self.events.notify(
                            tr("Etiqueta comprada sin documento"),
                            tr("{0}: Shopify no entregó el PDF/ZPL. Imprímela desde Shopify.", info.get('order_name')),
                            "error", kind="action",
                        )
                        pending.pop(result_id)
                elif status == "PURCHASE_FAILED":
                    reason = "; ".join(f"{e.get('code') or ''} {e.get('message', '')}".strip()
                                       for e in result.get("errors") or []) or tr("sin detalle")
                    log.error("Shopify: falló la compra de etiqueta de %s: %s", info.get("order_name"), reason)
                    if info.get("fo_key"):
                        self.store.unmark_seen(info["fo_key"])  # Shopify no la compró: se puede volver a comprar
                    self._note(info.get("order_id", ""), "failed", name=info.get("order_name"), reason=reason)
                    self.events.notify(tr("Falló la compra de etiqueta"), f"{info.get('order_name')}: {reason}", "error",
                                       kind="action")
                    pending.pop(result_id)
                elif age > 3600:
                    self._note(info.get("order_id", ""), "uncertain", name=info.get("order_name"))
                    self.events.notify(
                        tr("Compra de etiqueta sin respuesta"),
                        tr("{0}: revisa el pedido en Shopify.", info.get('order_name')),
                        "error", kind="action",
                    )
                    pending.pop(result_id)
            self._save_pending(pending)
            if not pending or time.time() >= deadline or stop.wait(2):
                break
        return queued

    # --- guías compradas en Shopify (FedEx y las que esperaban a una persona) ------------------------
    def _to_fetch(self) -> dict[str, dict]:
        try:
            return json.loads(self.store.kv_get(self._key("fetch")) or "{}")
        except json.JSONDecodeError:
            return {}

    def _queue_fetch(self, legacy: str, meta: dict) -> None:
        items = self._to_fetch()
        items[str(legacy)] = {**meta, "tries": 0}
        self.store.kv_set(self._key("fetch"), json.dumps(items, ensure_ascii=False))

    def _print_bought_labels(self, client: ShopifyClient, meta: dict) -> tuple[int, int, int]:
        """Trae de Shopify las guías activas del pedido y las encola (las que ya están en la cola no se repiten).

        Devuelve (guías activas, guías sin documento, trabajos nuevos)."""
        found = missing = queued = 0
        for label_id, entry in client.order_labels(meta["order_gid"]).items():
            if entry.get("voided") or not label_id.isdigit():
                continue
            found += 1
            if self.store.get_by_external("shopify", f"shipping-{label_id}-0-0"):
                continue  # ya se imprimió (la compró LabelBridge o ya se trajo)
            documents, new = self._enqueue_documents({"shippingLabels": [client.shipping_label(label_id)]}, meta)
            missing += 0 if documents else 1
            queued += new
        return found, missing, queued

    def _fetch_bought_labels(self, client: ShopifyClient, stop: threading.Event | None = None) -> int:
        """Pedidos que esperaban una persona y se prepararon en Shopify: su guía (p. ej. la de FedEx comprada con un
        clic) se imprime sola. Si Shopify todavía no la muestra se reintenta unas vueltas (≈5 min)."""
        items = self._to_fetch()
        if not items:
            return 0
        queued = 0
        for legacy, info in list(items.items()):
            if stop is not None and stop.is_set():
                break
            meta = {k: v for k, v in info.items() if k != "tries"}
            error = ""
            try:
                found, missing, new = self._print_bought_labels(client, meta)
            except (ShopifyError, net.NetworkError, net.HttpError) as exc:
                found, missing, new, error = 0, 0, 0, str(exc)
                log.warning("Shopify: no se pudo traer la guía de %s: %s", meta.get("order_name"), exc)
            queued += new
            if found:
                items.pop(legacy)
                if new:
                    log.info("Shopify: guía comprada en Shopify para %s, en cola", meta.get("order_name"))
                if missing:
                    self._no_document(legacy, meta)
                continue
            info["tries"] = int(info.get("tries", 0)) + 1
            if info["tries"] >= FETCH_TRIES:
                items.pop(legacy)  # sin guía: se envió de otra forma
                if error:
                    self._no_document(legacy, meta)
        self.store.kv_set(self._key("fetch"), json.dumps(items, ensure_ascii=False))
        return queued

    def _no_document(self, legacy: str, meta: dict) -> None:
        self._note(legacy, "no_document", name=meta.get("order_name"))  # Pedidos → «Guía sin PDF»
        self.events.notify(
            tr("Guía sin imprimir"),
            tr("{0}: LabelBridge no pudo traer de Shopify la guía comprada. Imprímela desde el pedido en Shopify.",
               meta.get("order_name")),
            "error", kind="action",
        )

    def print_order_labels(self, legacy: str, name: str) -> int:
        """«Imprimir guía» de un pedido cuya guía LabelBridge no tiene (p. ej. se compró en Shopify): se trae de ahí."""
        meta = {"order_gid": f"gid://shopify/Order/{int(legacy)}", "order_name": name, "order_id": str(legacy)}
        found, missing, queued = self._print_bought_labels(self.client(), meta)
        if not found:
            raise ShopifyError(tr("Ese pedido no tiene una guía activa en Shopify."))
        if missing and not queued:
            raise ShopifyError(tr("Shopify no entregó el PDF de esa guía: imprímela desde el pedido en Shopify."))
        if queued:
            self.engine.wake()
        return queued

    def reprint_label(self, legacy: str, name: str) -> int:
        """«Imprimir guía» de un pedido que ya tiene guía: la última que imprimió LabelBridge (con su formulario de
        aduana, si tiene) vuelve a la cola; si se compró en Shopify, se trae de ahí. Devuelve los trabajos en cola."""
        latest: dict[str, list[dict]] = {}
        for job in self.store.list(limit=3000):
            meta = job.get("meta") or {}
            external = str(job.get("external_id") or "")
            if (job.get("source") != "shopify" or str(meta.get("order_id")) != str(legacy) or "tracking" not in meta
                    or not external.startswith("shipping-")):
                continue
            latest.setdefault(external.split("-")[1], []).append(job)
        if not latest:
            return self.print_order_labels(legacy, name)
        jobs = max(latest.values(), key=lambda group: max(job.get("created_at") or 0 for job in group))
        queued = sum(1 for job in sorted(jobs, key=lambda job: job.get("external_id") or "")
                     if self.store.reprint(job["id"]))
        if not queued:
            raise ShopifyError(tr("No se pudo volver a poner la guía en la cola de impresión."))
        self.engine.wake()
        return queued

    def tracking(self, legacy: str) -> Tracking:
        """«Rastrear»: los envíos del pedido con su estado y los movimientos que informó la paquetería."""
        order = self.client().graphql(TRACKING_QUERY, {"id": self.order_gid(legacy)}).get("order")
        if not order:
            raise ShopifyError(tr("Shopify no encontró ese pedido."))
        return parse_tracking(order)

    def buy_in_shopify(self, legacy: str, name: str, carrier: str = "FedEx") -> None:
        """La persona eligió una paquetería que se compra en Shopify (FedEx): el pedido queda esperándola y, cuando
        Shopify lo marca preparado, LabelBridge trae la guía y la imprime sola."""
        if self.ledger(legacy).get("state") not in ("purchasing", "uncertain", "purchased"):
            self._note(legacy, "manual", name=name, carrier=carrier, fetch=1)

    def shipping_route(self, legacy: str) -> Route:
        """Si el envío es nacional y qué servicios ofrece Shopify para él (con read_shipping). Sin ese permiso, la
        lista muestra todos los servicios del tipo de envío."""
        client = self.client()
        origin, origin_country, destination = client.route(self.order_gid(legacy))
        route = Route(domestic=is_domestic(origin_country, destination), destination=destination)
        if origin and destination:
            try:
                route.offered = client.carrier_services(origin, destination) or None
            except ShopifyError as exc:
                if exc.code != "access_denied":
                    raise
        return route

    def _enqueue_documents(self, result: dict, info: dict) -> tuple[int, int]:
        """Encola los documentos con URL. Devuelve (documentos con URL, trabajos nuevos)."""
        documents_with_url = 0
        queued = 0
        printer = self.cfg.data.shopify.shipping_label_printer
        order_name = info.get("order_name", "")
        for label_index, label in enumerate(result.get("shippingLabels") or []):
            tracking = label.get("trackingInfo") or {}
            documents = sorted(label.get("shippingDocuments") or [], key=lambda d: d.get("documentType") != "LABEL")
            for doc_index, doc in enumerate(documents):
                url = doc.get("url")
                if not url:
                    continue
                documents_with_url += 1
                kind = "aduana" if doc.get("documentType") == "CUSTOMS_FORM" else "envío"
                fmt = "zpl" if str(doc.get("format", "")).upper() == "ZPL" else "pdf"
                size = {"label_size": self.cfg.data.shopify.label_size} if kind == "envío" else {}
                label_id = str(label.get("id", "")).rsplit("/", 1)[-1]
                queued += self._enqueue(
                    JobRequest(
                        external_id=f"shipping-{label_id}-{label_index}-{doc_index}",
                        title=tr("Etiqueta de envío {0}", order_name) if kind == "envío" else tr("Etiqueta de aduana {0}", order_name),
                        printer=printer,
                        format=fmt,
                        url=url,
                        meta={
                            **{k: v for k, v in info.items() if k != "started"},
                            "tracking": tracking.get("number"),
                            "carrier": tracking.get("company"),
                            **size,
                        },
                    )
                )
            log.info(
                "Shopify: etiqueta comprada %s · %s %s",
                order_name,
                tracking.get("company") or "",
                tracking.get("number") or "",
            )
        return documents_with_url, queued

    def _flush_tags(self, client: ShopifyClient) -> None:
        tag = self.cfg.data.shopify.tag_after_print.strip()
        while self._tag_queue:
            gid = self._tag_queue.popleft()
            if not tag:
                continue
            try:
                client.add_tags(gid, [tag])
            except ShopifyError as exc:
                log.warning("Shopify: no se pudo etiquetar el pedido (%s). ¿Falta el scope write_orders?", exc)
            except (net.NetworkError, net.HttpError) as exc:  # sin red: se vuelve a intentar en la próxima vuelta
                self._tag_queue.appendleft(gid)
                log.warning("Shopify: sin conexión para etiquetar el pedido: %s", exc)
                return

    # --- prueba de conexión (desde la UI) ---------------------------------------------------------
    def test_connection(self) -> dict[str, Any]:
        client = ShopifyClient(self.cfg.data.shopify, self.secrets)
        info = client.shop_info()
        scopes = set(info.get("scopes") or [])
        needed = {"read_orders": N_("leer pedidos")}
        settings = self.cfg.data.shopify
        if settings.buy_shipping_label:
            needed.update(
                {
                    "write_orders": N_("comprar etiquetas"),
                    "write_merchant_managed_fulfillment_orders": N_("comprar etiquetas"),
                }
            )
        if settings.tag_after_print:
            needed["write_orders"] = N_("agregar tags")
        implied = {s.replace("write_", "read_") for s in scopes if s.startswith("write_")}
        missing = [f"{scope} ({tr(why)})" for scope, why in needed.items() if scope not in scopes | implied]
        info["missing"] = missing
        info["optional_missing"] = [f"{scope} ({tr(why)})" for scope, why in OPTIONAL_SCOPES.items()
                                    if scope not in scopes | implied]
        self._client = client  # desde ya, con su token nuevo: con los permisos que se acaban de aprobar
        return info
