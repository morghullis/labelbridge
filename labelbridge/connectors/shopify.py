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
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode, urlparse

from .. import errors, net
from .. import events as ev
from ..config import ConfigManager, ShopifySettings
from ..i18n import N_, language, tr
from ..jobstore import JobStore
from ..models import JobRequest
from ..printing.engine import PrintEngine
from ..secret_store import SHOPIFY_ACCESS_TOKEN, SHOPIFY_CLIENT_SECRET, SecretStore
from ..security.policy import PolicyDenied
from . import packaging as pk
from .base import BackgroundService
from .shipping_services import AUTO, FEDEX, Route, by_key, is_domestic, offered_services, selection
from .shopify_orders import MANUAL_TAG, ManualItem, ManualOrders, OrderDetail, ProductChoice, Recipient
from .store_report import (
    HISTORY_WEEKS,
    REPORT_QUERY,
    REPORT_VERSION,
    REPORT_QUERY_NO_CUSTOMERS,
    SESSIONS_QUERY,
    TIMEZONE_QUERY,
    Funnel,
    WeeklyReport,
    build_report,
    money_text,
    parse_sessions,
    sessions_shopifyql,
    store_timezone,
    week_start,
)
from .tracking import TRACKING_QUERY, Tracking, parse_tracking
from .orders_overview import (
    ACTION_STATES,
    FLOW_WAIT_SECONDS,
    HELD_FULFILLMENT,
    OVERVIEW_QUERY,
    OVERVIEW_QUERY_BASIC,
    REFUNDED_STATUSES,
    OrderRow,
    belongs,
    carrier_name,
    classify,
    flow_buys,
    flow_rule,
    friendly_reason,
    label_events,
    rule_for,
    shipping_problem,
    subtotal_of,
)

log = logging.getLogger(__name__)

PAID_STATUSES = {"PAID", "PARTIALLY_REFUNDED", "AUTHORIZED"}
FETCH_TRIES = 10  # vueltas buscando una guía comprada en Shopify antes de darla por perdida
FLOW_PRINT_CHECK_SECONDS = 120  # cada cuánto se mira si ya se imprimió en Shopify la guía FedEx de Flow sin PDF
FLOW_PRINT_WATCH_SECONDS = 3 * 86400  # y durante cuánto (después queda «Guía sin PDF» para revisarla)
DELIVERY_CHECK_SECONDS = 1800  # cada cuánto se revisan los envíos en camino (retrasos y problemas)
REPORT_CHECK_SECONDS = 1800  # cada cuánto se mira si ya toca el reporte semanal
REPORT_READY_HOUR = 8  # el lunes desde esta hora (de la tienda) se prepara el reporte de la semana que terminó
REPORTS_KEPT = 26  # medio año de reportes guardados
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

# Si ya se imprimió desde Shopify (la FedEx que compró Shopify Flow cuando Shopify no entrega su PDF a otras apps).
LABEL_PRINTED_QUERY = """
query LabelBridgeLabelPrinted($id: ID!) {
  shippingLabel(id: $id) { id printed }
}
"""

TAGS_ADD_MUTATION = """
mutation LabelBridgeTagsAdd($id: ID!, $tags: [String!]!) {
  tagsAdd(id: $id, tags: $tags) { userErrors { field message } }
}
"""

# Preparar o deshacer la preparación de un pedido en Shopify (Pedidos → «Marcar preparado» / «Cancelar preparación»).
FULFILLMENTS_QUERY = """
query LabelBridgeFulfillments($id: ID!) {
  order(id: $id) {
    id name displayFulfillmentStatus
    fulfillments(first: 20) { id status trackingInfo { company number } }
    fulfillmentOrders(first: 10) { nodes { id status deliveryMethod { methodType } } }
  }
}
"""

FULFILL_MUTATION = """
mutation LabelBridgeFulfill($fulfillment: FulfillmentInput!) {
  fulfillmentCreate(fulfillment: $fulfillment) {
    fulfillment { id status }
    userErrors { field message }
  }
}
"""

CANCEL_FULFILLMENT_MUTATION = """
mutation LabelBridgeCancelFulfillment($id: ID!) {
  fulfillmentCancel(id: $id) {
    fulfillment { id status }
    userErrors { field message }
  }
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
# read_customers dice en «Ver pedido» si el cliente ya compró antes; read_reports trae las visitas al reporte.
SCOPES_LABELS = (
    "read_orders,write_orders,read_merchant_managed_fulfillment_orders,write_merchant_managed_fulfillment_orders,"
    "read_products,read_inventory,read_shipping,read_customers,read_reports"
)
OPTIONAL_SCOPES = {
    "read_products": N_("elegir productos de la tienda en la guía manual"),
    "read_inventory": N_("ver las existencias en la guía manual"),
    "read_shipping": N_("ver solo los servicios que Shopify ofrece en cada envío"),
    "read_customers": N_("saber si el cliente ya compró antes"),
    "read_reports": N_("ver en el reporte semanal las visitas y quién llega a pagar y no compra"),
}


class ShopifyError(Exception):
    """uncertain=True: no se sabe si Shopify ejecutó la operación (error 5xx, corte a mitad...).
    `code` identifica el problema sin depender del idioma del mensaje (p. ej. "access_denied")."""

    def __init__(self, message: str, uncertain: bool = False, code: str = "") -> None:
        super().__init__(message)
        self.uncertain = uncertain
        self.code = code


class PurchaseRejected(ShopifyError):
    """Shopify respondió que NO compró la etiqueta (userErrors): se puede reintentar. `codes`: sus códigos de error."""

    def __init__(self, message: str, codes: list[str] | None = None) -> None:
        super().__init__(message, code=(codes or [""])[0])
        self.codes = list(codes or [])


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


package_input = pk.custom_input  # un empaque propio como paquete de Shopify Shipping (customPackage)


def _domestic(order: dict) -> bool:
    """¿El pedido va dentro de EE. UU. (o a sus territorios)? Sin dirección, como si sí."""
    return is_domestic("US", str((order.get("shippingAddress") or {}).get("countryCodeV2") or ""))


def _iso_to_ts(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except AttributeError:  # sin fecha
        return 0.0
    except ValueError:
        log.warning("Shopify: fecha ilegible %r (el pedido no cuenta como nuevo)", value)
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
        self._token_at = 0.0  # cuándo se pidió el token (los permisos nuevos llegan con un token nuevo)
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
        self._token_at = time.time()
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

    def report_orders(self, created_from_iso: str, max_pages: int = 60) -> tuple[list[dict], bool]:
        """Pedidos creados desde esa fecha con lo que necesita el reporte semanal (hasta 20 × max_pages). Sin acceso a
        los clientes, la consulta va sin ellos. Devuelve también si quedaron pedidos sin leer."""
        orders: list[dict] = []
        after = None
        for _ in range(max_pages):
            data = self.graphql_first(
                (REPORT_QUERY, REPORT_QUERY_NO_CUSTOMERS),
                {"first": 20, "after": after, "query": f"created_at:>='{created_from_iso}'"},
                optional=("customer",),
            )
            connection = data.get("orders") or {}
            orders.extend(connection.get("nodes") or [])
            page = connection.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return orders, False
            after = page.get("endCursor")
        return orders, True

    def shop_timezone(self) -> dict:
        """Zona horaria y moneda de la tienda (las semanas del reporte van en su hora)."""
        return self.graphql(TIMEZONE_QUERY).get("shop") or {}

    def store_sessions(self, since: date, until: date) -> dict[str, Funnel]:
        """Las visitas de personas por día, de `since` a `until` (estadísticas de Shopify; necesita read_reports)."""
        variables = {"query": sessions_shopifyql(since, until)}
        try:
            data = self.graphql(SESSIONS_QUERY, variables)
        except ShopifyError as exc:
            # read_reports pudo agregarse después de pedir el token (dura 24 h): se pide uno nuevo, una vez.
            if exc.code != "access_denied" or self.auth_mode == "token" or time.time() - self._token_at < 300:
                raise
            data = self.graphql(SESSIONS_QUERY, variables, _refresh=True)
        result = data.get("shopifyqlQuery") or {}
        problems = [str(problem) for problem in result.get("parseErrors") or []]
        if problems:
            raise ShopifyError(tr("Shopify no aceptó la consulta de visitas: {0}", "; ".join(problems)[:300]),
                               code="shopifyql")
        return parse_sessions(result.get("tableData"))

    def fulfillment_orders(self, order_gid: str) -> list[dict]:
        data = self.graphql(FULFILLMENT_ORDERS_QUERY, {"id": order_gid})
        return ((data.get("order") or {}).get("fulfillmentOrders") or {}).get("nodes") or []

    def purchase_label(
        self, fulfillment_order_id: str, notify_customer: bool, *, package: dict | None = None,
        total_weight: dict | None = None, preferred: dict | None = None, carrier_package: dict | None = None,
    ) -> str:
        """Compra la guía con Shopify Shipping. `package`: un empaque propio (customPackage); `carrier_package`: uno
        oficial de la paquetería (carrierPackage); sin ninguno, Shopify usa su paquete predeterminado. Sin
        `total_weight`, Shopify calcula el peso con los productos. Sin `preferred` (paquetería y servicio), Shopify
        elige: el servicio que pagó el cliente o la paquetería preferida de la tienda."""
        ship_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        purchase: dict[str, Any] = {
            "fulfillmentOrderId": fulfillment_order_id,
            "shippingDatetime": ship_at,
            "notifyCustomer": bool(notify_customer),
        }
        if carrier_package:
            purchase["packageInfo"] = {"carrierPackage": carrier_package}
        elif package:
            purchase["packageInfo"] = {"customPackage": package}
        if total_weight:
            purchase["totalWeight"] = total_weight
        if preferred:
            purchase["preferredRateSelection"] = preferred
        data = self.graphql(PURCHASE_MUTATION, {"input": purchase}, mutation=True)
        result = data.get("shippingLabelPurchase") or {}
        errors = result.get("userErrors") or []
        if errors and (result.get("shippingLabelPurchaseResult") or {}).get("id"):
            # Rechazo y compra iniciada a la vez: no se sabe si la cobró, así que nunca se reintenta sola.
            raise ShopifyError(tr("Shopify respondió con errores pero empezó la compra: {0}", "; ".join(
                f"{e.get('code') or ''} {e.get('message')}".strip() for e in errors)), uncertain=True)
        if errors:
            raise PurchaseRejected("; ".join(f"{e.get('code') or ''} {e.get('message')}".strip() for e in errors),
                                   [str(e.get("code") or "") for e in errors])
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
        self._report_checked_at = time.time() - REPORT_CHECK_SECONDS + 300  # la primera, a los 5 minutos
        self._shop_zone: tuple[float, dict] | None = None  # (cuándo, zona y moneda de la tienda)
        self._report_lock = threading.Lock()  # un solo reporte del lunes a la vez
        self._resets = 0  # «Reprocesar» durante una vuelta: esa vuelta no pisa el cursor nuevo
        self._tag_problem = ""  # el último motivo por el que no se pudo poner el tag (ya avisado)
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
        except ShopifyError as exc:  # la descarga sigue sin token y falla con 401: que el motivo real quede escrito
            log.warning("Shopify: no se pudo obtener el token para descargar un documento: %s", exc)
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
        except json.JSONDecodeError as exc:
            log.warning("Shopify: historial del pedido %s ilegible (%s): se ignora", legacy, exc)
            return {}

    def ledgers(self) -> dict[str, dict]:
        prefix = self._key("ledger:")
        out: dict[str, dict] = {}
        for key, value in self.store.kv_items(prefix).items():
            try:
                out[key[len(prefix):]] = json.loads(value)
            except json.JSONDecodeError as exc:
                log.warning("Shopify: historial del pedido %s ilegible (%s): se ignora", key[len(prefix):], exc)
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
                if time.time() - self._report_checked_at >= REPORT_CHECK_SECONDS:
                    self._report_checked_at = time.time()
                    threading.Thread(target=self._weekly_report_job, name="reporte-semanal", daemon=True).start()
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
        except json.JSONDecodeError as exc:  # se vuelve a empezar desde el cursor (sin perder pedidos)
            log.warning("Shopify: punto de la paginación ilegible (%s): se sigue desde el cursor", exc)
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
        self._check_flow_waits(client, stop)
        queued += self._fetch_bought_labels(client, stop)
        self._check_flow_prints(client, stop)
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
        status = order.get("displayFulfillmentStatus") or ""
        held = status in HELD_FULFILLMENT  # retenido o programado: nadie puede prepararlo ni comprarle guía todavía
        if (noted in ACTION_STATES and noted != "no_document" and status not in OPEN_FULFILLMENT and not held):
            # Se resolvió en Shopify (guía comprada ahí, p. ej. la de FedEx, o enviado): su guía se trae y se imprime.
            # También si la compra automática está apagada pero alguien eligió «comprarla en Shopify» en la app.
            # «Guía sin PDF» ya se intentó traer: espera a la persona (volver a traerla repetiría el aviso).
            if s.buy_shipping_label or entry.get("fetch"):
                self._queue_fetch(legacy, {**meta, "flow": 1} if noted == "flow" else meta)
            self._note(legacy, "resolved", name=name)
            noted = "resolved"

        method = (order.get("shippingLine") or {}).get("title") or ""
        rule = rule_for(s.shipping_rules, method, subtotal_of(order))
        flow = flow_buys(s, method, subtotal_of(order), order.get("displayFinancialStatus") or "", manual)
        if flow:  # la regla del flujo va primero: Flow compra la FedEx aunque otra regla anterior dijera otra cosa
            rule = flow_rule(s)
        if flow and is_new:
            # FedEx por Shopify Flow: la compra un flujo de la tienda (la API no puede). LabelBridge no le pide a nadie
            # que la compre ni le compra otra: espera la guía, la imprime al aparecer y avisa si no aparece
            # (_check_flow_waits). No depende de la compra automática de LabelBridge: el dinero lo gasta el flujo.
            flow_key = self._key(f"order:{legacy}:flow")
            if (open_ok or held) and not noted:
                # Retenido: Flow la compra al liberarlo (el disparador vuelve a sonar); los 15 min cuentan desde ahora.
                self._note(legacy, "flow", name=name, carrier=rule.carrier, fetch=1,
                           created=time.time() if held else _iso_to_ts(order.get("createdAt", "")))
            elif not open_ok and not held and not noted and not self.store.seen(flow_key):
                # Flow fue más rápido que la primera vuelta: el pedido ya llegó preparado con su guía.
                self._queue_fetch(legacy, {**meta, "flow": 1})
                self._note(legacy, "resolved", name=name, carrier=rule.carrier)
            self.store.mark_seen(flow_key)

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
            raw = str((order.get("metafield") or {}).get("value") or "")
            urls = extract_urls(raw)
            if raw.strip() and not urls:  # tiene algo, pero no una dirección: avisar una vez por pedido
                self._invalid_metafield(legacy, name, raw)
            for url in urls:
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
            if flow:
                pass  # la compra Shopify Flow (arriba): nadie tiene que comprarla, y LabelBridge nunca otra
            elif rule is not None and not rule.auto:
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
            elif noted in ("manual", "flow"):
                pass  # alguien eligió comprarla en Shopify, o Flow la está comprando: la compra automática no la toca
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

    # --- reporte semanal -------------------------------------------------------------------------------
    def _zone(self, now: float) -> dict:
        """Zona horaria y moneda de la tienda (se preguntan una vez al día)."""
        if self._shop_zone is None or now - self._shop_zone[0] > 86400:
            self._shop_zone = (now, self.client().shop_timezone())
        return self._shop_zone[1]

    def weekly_report(self, weeks_back: int = 1, now: float | None = None) -> WeeklyReport:
        """El reporte de la semana de hace `weeks_back` semanas (1: la que terminó; 0: esta, hasta hoy), con los
        pedidos de Shopify y el costo de las guías. Una semana terminada se guarda: la pestaña y el asistente la leen sin
        volver a preguntar (la de esta semana cambia con cada pedido: no se guarda)."""
        now = now or time.time()
        shop = self._zone(now)
        zone = store_timezone(shop)
        since = week_start(now, zone, weeks_back) - timedelta(weeks=HISTORY_WEEKS)
        orders, truncated = self.client().report_orders(_ts_to_iso(since.timestamp()))
        missing = []
        if truncated:  # llegan los más nuevos primero: lo que falta es lo más viejo (el promedio), no la semana
            missing.append(tr("Hubo más pedidos de los que se leen de una vez (1.200): el promedio de las semanas "
                              "anteriores puede quedar corto."))
        if orders and all(not order.get("customer") for order in orders):
            missing.append(tr("Clientes que vuelven: la app no puede ver a los clientes (permiso read_customers y datos "
                              "protegidos aprobados en el Dev Dashboard)."))
        sunday = (week_start(now, zone, weeks_back) + timedelta(days=6)).date()
        last_day = min(sunday, datetime.fromtimestamp(now, zone).date())  # la semana en curso, hasta hoy
        sessions, sessions_note = self._report_sessions(since.date(), last_day, missing)
        report = build_report(orders, now, zone, str(shop.get("currencyCode") or "USD"), weeks_back, missing, sessions,
                              sessions_note)
        # Una semana a medias no se guarda (no reemplaza a la semana entera), ni una en la que las visitas fallaron por
        # un problema pasajero: la próxima vez se vuelve a intentar.
        if report.complete and not sessions_note:
            self._save_report(report)
        return report

    def _report_sessions(self, since: date, until: date, missing: list[str]) -> tuple[dict[str, Funnel] | None, str]:
        """Las visitas por día para el reporte. Sin ellas el reporte sale igual, y dice por qué (nunca en silencio)."""
        try:
            return self.client().store_sessions(since, until), ""
        except ShopifyError as exc:
            if exc.code == "access_denied":
                missing.append(tr("Visitas y pagos sin terminar: agrega el permiso read_reports a la app en el Dev "
                                  "Dashboard (Shopify → Copiar permisos), publica la versión nueva y pulsa «Actualizar»."))
                return None, ""
            problem = exc
        except (net.NetworkError, net.HttpError, TypeError, ValueError, AttributeError) as exc:  # sin red o datos raros
            problem = exc
        log.warning("Shopify: no se pudieron leer las visitas para el reporte: %s", problem)
        missing.append(tr("No se pudieron leer las visitas de Shopify esta vez: {0}", errors.user_message(problem)))
        return None, tr("No se pudieron leer las visitas esta vez (mira la nota al final del reporte).")

    def _save_report(self, report: WeeklyReport) -> None:
        self.store.kv_set(self._key(f"report:{report.start}"), json.dumps(report.to_dict(), ensure_ascii=False))
        saved = sorted(key for key, value in self.store.kv_items(self._key("report:")).items() if value)
        for old in saved[:-REPORTS_KEPT]:  # los más viejos se vacían (kv no borra filas)
            self.store.kv_set(old, "")

    def saved_reports(self) -> list[WeeklyReport]:
        """Los reportes guardados, el más nuevo primero (los ilegibles se saltan y quedan anotados)."""
        reports = []
        for key, value in self.store.kv_items(self._key("report:")).items():
            if not value:
                continue
            try:
                reports.append(WeeklyReport.from_dict(json.loads(value)))
            except (ValueError, TypeError, KeyError) as exc:
                log.warning("Reporte guardado ilegible (%s): %s", key, exc)
        return sorted(reports, key=lambda report: report.start, reverse=True)

    def saved_report(self, weeks_back: int = 1, now: float | None = None) -> WeeklyReport | None:
        """El reporte ya guardado de esa semana (sin volver a pedir los pedidos), si lo hay: solo semanas enteras, en el
        idioma de ahora y armados por esta versión del cálculo (si no, se vuelve a armar)."""
        now = now or time.time()
        start = week_start(now, store_timezone(self._zone(now)), weeks_back).date().isoformat()
        return next((report for report in self.saved_reports()
                     if report.start == start and report.complete and report.language == language()
                     and report.version == REPORT_VERSION), None)

    def weekly_report_if_due(self, now: float | None = None) -> WeeklyReport | None:
        """Desde el lunes a las 8 (hora de la tienda), el reporte de la semana que terminó, con su aviso una sola vez
        (aunque alguien ya lo haya abierto antes de las 8: el aviso se anota aparte)."""
        now = now or time.time()
        zone = store_timezone(self._zone(now))
        local = datetime.fromtimestamp(now, zone)
        if local.weekday() == 0 and local.hour < REPORT_READY_HOUR:
            return None
        start = week_start(now, zone, 1).date().isoformat()
        notified = self._key(f"report-notified:{start}")
        if self.store.kv_get(notified):
            return None
        report = self.saved_report(1, now) or self.weekly_report(1, now)
        self.store.kv_set(notified, "1")
        sales = report.metric("sales")
        self.events.notify(
            tr("Tu reporte semanal está listo"),
            tr("Semana del {0} al {1}: {2} en ventas. Ábrelo en LabelBridge → Reporte.", report.start, report.end,
               money_text(sales.value if sales else None, report.currency)),
            kind="report",
        )
        self.events.emit(ev.REPORT, {"start": report.start})
        return report

    def _weekly_report_job(self) -> None:
        """El reporte del lunes, en su propio hilo: leer semanas de pedidos no frena la compra ni la impresión."""
        if not self._report_lock.acquire(blocking=False):
            return  # ya se está preparando
        try:
            self.weekly_report_if_due()
        except (ShopifyError, net.NetworkError, net.HttpError) as exc:  # se vuelve a probar en 30 min
            log.warning("Shopify: no se pudo preparar el reporte semanal: %s", exc)
        except Exception as exc:  # noqa: BLE001  un error inesperado se anota y se avisa; la impresión sigue igual
            errors.report(tr("No se pudo preparar el reporte semanal"), exc)
        finally:
            self._report_lock.release()

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
        """Compra la guía de cada orden de preparación abierta, con el empaque y el servicio de las reglas. Con
        `options` la pidió una persona desde Pedidos (empaque y servicio —packing—, peso y aviso al cliente elegidos
        ahí; sin el límite diario de las compras automáticas)."""
        queued = 0
        problem = shipping_problem(order)
        if problem:  # nunca una guía sin sentido (sin envío o sin dirección): quien llama ya lo explica
            log.warning("Shopify: %s no lleva guía: %s", meta.get("order_name"), problem)
            return 0
        manual = options is not None
        plan: tuple | None = None  # con qué se compra: se decide con la primera orden que de verdad se compra
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
            if plan is None:
                try:
                    plan = self._plan(s, order, options)
                except pk.PackagingError as exc:  # las reglas piden algo que no se puede: no se compra y se avisa
                    self._packing_problem(meta, str(exc))
                    return queued
            custom, official, preferred, weight, notify = plan
            self.store.kv_set(attempts_key, str(attempts + 1))
            # La compra se registra ANTES de pedirla: si algo falla a mitad, nunca se compra dos veces.
            self.store.mark_seen(key)
            try:
                result_id = self._purchase(client, fo_id, bool(notify), custom=custom, official=official,
                                           total_weight=weight, preferred=preferred)
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
            queued += self._check_pending_purchases(client, wait_seconds=wait, stop=stop, until=result_id)
        return queued

    @staticmethod
    def _plan(s: ShopifySettings, order: dict, options: dict | None) -> tuple:
        """(empaque propio, empaque oficial, servicio, peso, aviso) de la compra: lo elegido en la app (`options`) o,
        en las automáticas, lo que dicen las reglas para ese pedido. PackagingError si no van juntos."""
        if options is not None:
            packing = options.get("packing") or pk.Packing()
            custom = options.get("custom")  # «Otro empaque…»: un empaque solo para esta guía
            weight, notify = options.get("total_weight"), options.get("notify")
        else:
            packing = pk.for_order(s, (order.get("shippingLine") or {}).get("title") or "", subtotal_of(order),
                                   _domestic(order))
            custom, weight, notify = None, None, s.notify_customer
        return (custom or pk.custom_for(s, packing.package), pk.carrier_package(packing.package),
                selection(packing.service), weight, notify)

    def _purchase(
        self, client: ShopifyClient, fo_id: str, notify: bool, *, custom: dict | None,
        official: pk.CarrierPackage | None, total_weight: dict | None, preferred: dict | None,
    ) -> str:
        """Pide la compra con el empaque elegido. Con un empaque oficial (Flat Rate de USPS), Shopify no publica su
        código: se usa el que ya funcionó o se prueban los conocidos en orden. Cada rechazo de Shopify es sin comprar
        ni cobrar nada; si fue por otra cosa (no por el código), no se prueba el siguiente."""
        if official is None:
            return client.purchase_label(fo_id, notify, package=custom, total_weight=total_weight, preferred=preferred)
        memo = self._key(f"carrier_package:{official.key}")
        known = self.store.kv_get(memo) or ""
        last: PurchaseRejected | None = None
        for code in pk.codes_to_try(official, known):
            try:
                result_id = client.purchase_label(fo_id, notify, carrier_package=pk.carrier_input(official, code),
                                                  total_weight=total_weight, preferred=preferred)
            except PurchaseRejected as exc:
                if not pk.package_problem(exc.codes):
                    raise
                log.warning("Shopify no aceptó el empaque %s con el código %r: %s", official.key, code, exc)
                last = exc
                continue
            if code != known:
                self.store.kv_set(memo, code)
                log.info("Shopify aceptó el empaque %s con el código %r: queda anotado", official.key, code)
            return result_id
        if known:  # el que funcionaba ya no: la próxima vez se vuelven a probar todos
            self.store.kv_set(memo, "")
        raise PurchaseRejected(
            tr("Shopify no aceptó el empaque {0} con este servicio para este envío ({1}). No se compró nada: elige otro "
               "empaque o servicio", tr(official.label), friendly_reason(str(last)) if last else tr("sin detalle")),
            last.codes if last else [],
        )

    def _packing_problem(self, meta: dict, reason: str) -> None:
        """Las reglas piden un empaque y un servicio que no van juntos (o un empaque borrado): una vez por pedido."""
        legacy = meta["order_id"]
        key = self._key(f"order:{legacy}:packing")
        if self.store.seen(key):
            return
        self.store.mark_seen(key)
        self._note(legacy, "failed", name=meta["order_name"], reason=reason)
        log.error("Shopify: %s no se compró por el empaque: %s", meta["order_name"], reason)
        self.events.notify(
            tr("No se pudo comprar la etiqueta"),
            tr("{0}: {1} Corrige tus reglas de paquetería y pulsa «Imprimir guía» en la pestaña Pedidos.",
               meta["order_name"], reason),
            "error", kind="action",
        )

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

    def _invalid_metafield(self, legacy: str, name: str, raw: str) -> None:
        key = self._key(f"order:{legacy}:mf:invalid")
        if self.store.seen(key):
            return
        self.store.mark_seen(key)
        s = self.cfg.data.shopify
        log.warning("Shopify: el metafield %s.%s de %s no trae una URL http(s): %r", s.metafield_namespace,
                    s.metafield_key, name, raw[:120])
        self.events.notify(
            tr("Pedido {0}: el metafield no trae una etiqueta", name),
            tr("{0} no tiene una dirección http(s) de un PDF o ZPL, así que no se imprimió nada. Revisa lo que guarda "
               "ahí n8n, tu web o el ERP.", f"{s.metafield_namespace}.{s.metafield_key}"),
            "error", kind="print_error",
        )

    def _flow_is_buying(self, legacy: str, order: dict) -> bool:
        """Shopify Flow está comprando (o compró) la FedEx de este pedido: LabelBridge lo anotó así, o es reciente y
        cumple la regla del flujo aunque la vuelta todavía no lo haya visto (Flow compra en segundos)."""
        if self.ledger(legacy).get("state") == "flow":
            return True
        s = self.cfg.data.shopify
        tags = [str(t).lower() for t in order.get("tags") or []]
        return (flow_buys(s, (order.get("shippingLine") or {}).get("title") or "", subtotal_of(order),
                          order.get("displayFinancialStatus") or "", MANUAL_TAG.lower() in tags)
                and time.time() - _iso_to_ts(order.get("createdAt", "")) < FLOW_WAIT_SECONDS)

    def buy_label_now(
        self, legacy: str, *, package: str | dict | None = pk.RULES, total_weight: dict | None = None,
        notify: bool | None = None, service: str = AUTO, wait: float = 45,
    ) -> dict:
        """«Imprimir guía» desde Pedidos, confirmada por la persona: compra con Shopify Shipping (aunque se haya
        llegado al límite diario o a los intentos automáticos) con el servicio elegido (AUTO: el que elija Shopify),
        espera el resultado y la imprime. Shopify deja el pedido preparado con su número de seguimiento.

        `package`: pk.RULES, el empaque (y, con AUTO, el servicio) que dicen las reglas para ese pedido; la clave de un
        empaque (packaging.choices; None: el de Shopify); o un dict, un empaque solo para esta guía («Otra caja…»). Un
        empaque oficial de USPS va con Priority Mail.

        Devuelve el registro del pedido: state purchased (carrier, tracking y, si Shopify ya lo anotó, cost), failed
        (reason) o purchasing, más el empaque y el servicio con que se pidió (package, service, note). Nunca mientras
        Shopify Flow compra la FedEx de ese pedido."""
        if service == FEDEX:
            raise ShopifyError(tr("Shopify no deja que ninguna app compre FedEx: cómprala en el pedido en Shopify."))
        if service not in ("", AUTO) and by_key(service) is None:
            raise ShopifyError(tr("Servicio de envío desconocido: {0}", service))
        s = self.cfg.data.shopify
        client = self.client()
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
            if self._flow_is_buying(legacy, order):
                raise ShopifyError(tr("Shopify Flow está comprando la guía FedEx de {0}: comprar otra sería pagar dos. "
                                      "Si en {1} min no aparece, LabelBridge te avisa.",
                                      order.get("name") or legacy, FLOW_WAIT_SECONDS // 60))
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
            packing, custom = self._chosen_packing(s, order, package, service)
            options = {"packing": packing, "custom": custom, "total_weight": total_weight,
                       "notify": s.notify_customer if notify is None else bool(notify)}
            meta = {"order_gid": order.get("id"), "order_name": order.get("name") or legacy, "order_id": str(legacy)}
            queued = self._buy_labels(client, s, order, meta, threading.Event(), options=options,
                                      fulfillment_orders=fresh, wait=wait)
        if queued:
            self.engine.wake()
        entry = {**self.ledger(legacy), "queued": queued, "package": packing.package, "service": packing.service,
                 "note": packing.note}
        if entry.get("state") == "purchased" and entry.get("label"):
            try:  # lo que costó, según el historial del pedido (si Shopify ya lo anotó)
                cost = client.order_labels(str(order["id"])).get(str(entry["label"]), {}).get("cost")
            except (ShopifyError, net.NetworkError, net.HttpError):
                cost = None
            if cost:
                entry["cost"] = cost
        return entry

    def planned_packing(self, legacy: str, package: str | dict | None = pk.RULES, service: str = AUTO) -> pk.Packing:
        """El empaque y el servicio con que se compraría la guía de ese pedido (para decirlo antes de confirmar)."""
        s = self.cfg.data.shopify
        order = self.client().order(legacy, s.metafield_namespace, s.metafield_key)
        if order is None:
            raise ShopifyError(tr("Shopify no encontró ese pedido."))
        return self._chosen_packing(s, order, package, service)[0]

    @staticmethod
    def _chosen_packing(s: ShopifySettings, order: dict, package: str | dict | None,
                        service: str) -> tuple[pk.Packing, dict | None]:
        """(empaque y servicio, empaque solo para esta guía) de una compra pedida desde la app."""
        try:
            if isinstance(package, dict):  # «Otro empaque…»
                return pk.Packing(pk.SHOPIFY, service or AUTO), package
            if package == pk.RULES:
                method = (order.get("shippingLine") or {}).get("title") or ""
                rule = rule_for(s.shipping_rules, method, subtotal_of(order))
                if rule is not None and not rule.auto:  # FedEx (o lo marcado «En Shopify»): nunca otra paquetería
                    raise ShopifyError(tr("Según tus reglas, {0} sale por {1} y su guía se compra en Shopify.",
                                          order.get("name") or "", rule.carrier))
                ruled = pk.for_order(s, method, subtotal_of(order), _domestic(order))
                return (ruled if service in ("", AUTO) else pk.fit(s, ruled.package, service)), None
            return pk.fit(s, package or pk.SHOPIFY, service), None
        except pk.PackagingError as exc:
            raise ShopifyError(str(exc)) from None

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
        return self._load_map("pending", N_("las compras de guías en curso"))

    def _load_map(self, name: str, what: str) -> dict[str, dict]:
        """Un registro guardado (compras en curso, guías por traer de Shopify). Si está dañado no se pierde en
        silencio: el texto se guarda aparte, queda anotado y se avisa para revisar los pedidos recientes."""
        key = self._key(name)
        raw = self.store.kv_get(key) or "{}"
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError(f"se esperaba un objeto y llegó {type(value).__name__}")
            return value
        except ValueError as exc:  # json.JSONDecodeError es un ValueError
            backup = f"{key}:danado:{int(time.time())}"
            self.store.kv_set(backup, raw)
            self.store.kv_set(key, "{}")
            log.error("Shopify: el registro %s estaba dañado (%s); se guardó en %s y se empieza vacío", name, exc, backup)
            self.events.notify(
                tr("Revisa los pedidos recientes"),
                tr("Se dañó el registro de {0}: alguna guía ya comprada podría no imprimirse sola. Revisa en Pedidos "
                   "los de las últimas horas.", tr(what)),
                "error", kind="action",
            )
            return {}

    def _save_pending(self, pending: dict[str, dict]) -> None:
        self.store.kv_set(self._key("pending"), json.dumps(pending))

    def _check_pending_purchases(
        self, client: ShopifyClient, wait_seconds: float = 0, stop: threading.Event | None = None,
        until: str | None = None,
    ) -> int:
        """Revisa las compras en curso; con `wait_seconds`, espera (hasta ese tope) a que terminen o, con `until`, a
        que termine esa compra (las demás se siguen revisando en cada vuelta del ciclo)."""
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
            if not pending or (until is not None and until not in pending) or time.time() >= deadline or stop.wait(2):
                break
        return queued

    # --- guías compradas en Shopify (FedEx y las que esperaban a una persona) ------------------------
    def _to_fetch(self) -> dict[str, dict]:
        return self._load_map("fetch", N_("las guías por traer de Shopify"))

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
        flow = bool(meta.get("flow") or self.ledger(legacy).get("flow"))
        self._note(legacy, "no_document", name=meta.get("order_name"), **({"flow": 1} if flow else {}))
        told = self._key(f"order:{legacy}:no_document")
        if self.store.seen(told):
            return  # ya se avisó (y se abrió el pedido) una vez
        self.store.mark_seen(told)
        if flow:
            # La FedEx de Shopify Flow: Shopify solo le da el PDF a quien la compró. Como hacía la PC con el sistema de
            # la web, se abre el pedido en Shopify y basta «Imprimir etiqueta»; al imprimirla allí, el pedido se da por
            # resuelto (_check_flow_prints).
            self.events.emit(ev.OPEN_URL, {"url": admin_url(self.cfg.data.shopify.shop, f"orders/{int(legacy)}")})
            self.events.notify(
                tr("Guía FedEx de {0} lista", meta.get("order_name")),
                tr("Shopify Flow la compró. Se abrió el pedido en Shopify: pulsa «Imprimir etiqueta»."),
                "info", kind="action",
            )
            return
        self.events.notify(  # Pedidos → «Guía sin PDF»
            tr("Guía sin imprimir"),
            tr("{0}: LabelBridge no pudo traer de Shopify la guía comprada. Imprímela desde el pedido en Shopify.",
               meta.get("order_name")),
            "error", kind="action",
        )

    def _check_flow_waits(self, client: ShopifyClient, stop: threading.Event | None = None) -> None:
        """FedEx por Shopify Flow: si pasados FLOW_WAIT_SECONDS el pedido sigue sin guía, Flow no la compró (flujo
        apagado o distinto, sin tarifa…): se le pide a una persona, con «Comprar guía FedEx». Se decide por las guías del
        pedido (no por su estado: un pedido que también se retira en tienda sigue «parcialmente preparado» con su guía)
        y con los datos que todas las apps pueden leer (read_orders). Retenido o programado, la cuenta vuelve a empezar
        al liberarlo; cancelado o reembolsado, ya no se espera nada."""
        now = time.time()
        waiting = sorted((float(entry.get("created") or entry.get("at") or 0), legacy, entry)
                         for legacy, entry in self.ledgers().items() if entry.get("state") == "flow")
        late = [(legacy, entry) for started, legacy, entry in waiting if now - started >= FLOW_WAIT_SECONDS]
        s = self.cfg.data.shopify
        for legacy, entry in late[:10]:  # los que más esperan primero; los revisados vuelven al final de la fila
            if stop is not None and stop.is_set():
                return
            name = entry.get("name") or legacy
            carrier = entry.get("carrier") or "FedEx"
            gid = self.order_gid(legacy)
            try:
                order = client.order(legacy, s.metafield_namespace, s.metafield_key)
                labels = [label_id for label_id, info in client.order_labels(gid).items()
                          if not info.get("voided")] if order else []
            except (ShopifyError, net.NetworkError, net.HttpError) as exc:
                log.warning("Shopify: no se pudo revisar la FedEx de %s: %s", name, exc)
                self._note(legacy, "flow", name=name, carrier=carrier, fetch=1, created=now)  # en 15 min otra vez
                continue
            status = (order or {}).get("displayFulfillmentStatus") or ""
            if not order or order.get("cancelledAt") or order.get("displayFinancialStatus") in REFUNDED_STATUSES:
                self._note(legacy, "resolved", name=name)  # ya no lleva guía
            elif labels:  # Flow la compró y la vuelta no lo vio: se trae y se imprime
                self._queue_fetch(legacy, {"order_gid": gid, "order_name": name, "order_id": str(legacy), "flow": 1})
                self._note(legacy, "resolved", name=name)
            elif status in HELD_FULFILLMENT:
                self._note(legacy, "flow", name=name, carrier=carrier, fetch=1, created=now)
            elif status in OPEN_FULFILLMENT:
                self._note(legacy, "manual", name=name, carrier=carrier, fetch=1, flow_late=1)
                log.info("Shopify: Shopify Flow no compró la FedEx de %s", name)
                self.events.notify(
                    tr("Pedido {0}: Shopify Flow no compró la FedEx", name),
                    tr("Pasaron {0} min y la guía no aparece. Cómprala tú: Pedidos → {1} → «Comprar guía FedEx».",
                       FLOW_WAIT_SECONDS // 60, name),
                    "error", kind="action",
                )
            else:  # preparado sin guía (se envió de otra forma): nada que imprimir
                self._note(legacy, "resolved", name=name)

    def _check_flow_prints(self, client: ShopifyClient, stop: threading.Event | None = None) -> None:
        """La FedEx de Shopify Flow sin PDF se imprime desde Shopify: cuando Shopify la marca impresa, el pedido deja de
        pedir acción en Pedidos. Se mira cada FLOW_PRINT_CHECK_SECONDS, hasta 10 pedidos por vez (los que hace más que
        no se miran primero) y durante unos días."""
        now = time.time()
        if now - float(self.store.kv_get(self._key("flow_prints_at")) or 0) < FLOW_PRINT_CHECK_SECONDS:
            return
        self.store.kv_set(self._key("flow_prints_at"), str(now))
        try:
            checked = json.loads(self.store.kv_get(self._key("flow_prints")) or "{}")
        except json.JSONDecodeError:
            checked = {}
        watching = [(legacy, entry) for legacy, entry in self.ledgers().items()
                    if entry.get("state") == "no_document" and entry.get("flow")
                    and now - float(entry.get("at") or 0) < FLOW_PRINT_WATCH_SECONDS]
        watching.sort(key=lambda item: float(checked.get(item[0]) or 0))
        for legacy, entry in watching[:10]:
            if stop is not None and stop.is_set():
                break
            checked[legacy] = now
            name = entry.get("name") or legacy
            try:
                labels = client.order_labels(self.order_gid(legacy))
                printed = [label_id for label_id, info in labels.items()
                           if not info.get("voided") and str(label_id).isdigit()
                           and (client.graphql(LABEL_PRINTED_QUERY, {"id": f"gid://shopify/ShippingLabel/{label_id}"})
                                .get("shippingLabel") or {}).get("printed")]
            except (ShopifyError, net.NetworkError, net.HttpError) as exc:
                log.info("Shopify: no se pudo ver si la guía de %s ya se imprimió: %s", name, exc)
                continue
            if printed:
                self._note(legacy, "resolved", name=name, printed_in_shopify=1)
                log.info("Shopify: la guía FedEx de %s se imprimió desde Shopify", name)
        still = {legacy for legacy, _entry in watching}
        self.store.kv_set(self._key("flow_prints"),
                          json.dumps({legacy: at for legacy, at in checked.items() if legacy in still}))

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
        """«Imprimir guía» de un pedido que ya tiene guía: las guías que siguen activas en Shopify (nunca una anulada)
        vuelven a la cola con sus documentos (formulario de aduana incluido); las que LabelBridge no tiene se traen de
        Shopify. Devuelve los trabajos en cola."""
        client = self.client()
        meta = {"order_gid": self.order_gid(legacy), "order_name": name, "order_id": str(legacy)}
        active = sorted((float(entry.get("at") or 0), label_id)
                        for label_id, entry in client.order_labels(meta["order_gid"]).items()
                        if not entry.get("voided") and str(label_id).isdigit())
        if not active:
            raise ShopifyError(tr("Ese pedido no tiene una guía activa en Shopify."))
        queued = 0
        for _at, label_id in active:
            jobs = self._label_jobs(label_id)
            if not jobs:  # comprada en Shopify y todavía sin imprimir aquí: se trae de ahí
                _documents, again = self._enqueue_documents({"shippingLabels": [client.shipping_label(label_id)]}, meta)
            else:
                again = sum(1 for job in jobs if self.store.reprint(job["id"]))
                if not again:  # la copia de aquí ya no se puede leer: la misma guía se trae de Shopify, nunca otra
                    again = self._refetch(client, label_id, meta)
            queued += again
        if not queued:
            raise ShopifyError(tr("No se pudo poner la guía en la cola de impresión: imprímela desde el pedido en "
                                  "Shopify."))
        self.engine.wake()
        return queued

    def _refetch(self, client: ShopifyClient, label_id: str, meta: dict) -> int:
        """Vuelve a poner en la cola los documentos de una guía que ya existe, tal como los da Shopify ahora (la copia
        de aquí falta o no se puede leer). Es la misma guía: nunca se compra otra."""
        _documents, queued = self._enqueue_documents({"shippingLabels": [client.shipping_label(label_id)]}, meta,
                                                     reprint=True)
        return queued

    def _label_jobs(self, label_id: str) -> list[dict]:
        """Los trabajos de impresión de una guía (la etiqueta y sus otros documentos), por su número en Shopify."""
        jobs = []
        for label_index in range(3):
            for doc_index in range(4):
                job = self.store.get_by_external("shopify", f"shipping-{label_id}-{label_index}-{doc_index}")
                if job:
                    jobs.append(job)
        return jobs

    def tracking(self, legacy: str) -> Tracking:
        """«Rastrear»: los envíos del pedido con su estado y los movimientos que informó la paquetería."""
        order = self.client().graphql(TRACKING_QUERY, {"id": self.order_gid(legacy)}).get("order")
        if not order:
            raise ShopifyError(tr("Shopify no encontró ese pedido."))
        return parse_tracking(order)

    def buy_in_shopify(self, legacy: str, name: str, carrier: str = "FedEx") -> None:
        """La persona eligió una paquetería que se compra en Shopify (FedEx): el pedido queda esperándola (la compra
        automática ya no lo toca) y, cuando Shopify lo marca preparado, LabelBridge trae la guía y la imprime sola.
        Nunca si el pedido ya tiene una compra hecha, en curso o sin confirmar: sería una segunda guía."""
        client = self.client()
        with self._cycle_lock:  # la compra automática no puede comprarlo a la vez
            busy = any(str(info.get("order_id")) == str(legacy) for info in self._pending().values())
            fos = [] if busy else client.fulfillment_orders(self.order_gid(legacy))
            bought = any(self.store.seen(self._fo_key(fo)) for fo in fos if fo.get("status") in ("OPEN", "IN_PROGRESS"))
            if busy or bought or self.ledger(legacy).get("state") in ("purchasing", "uncertain", "purchased"):
                raise ShopifyError(tr("Ese pedido ya tiene una compra hecha, en curso o sin confirmar: revísalo en "
                                      "Shopify antes de comprar otra guía."))
            if self.ledger(legacy).get("state") == "flow":
                raise ShopifyError(tr("Shopify Flow está comprando la guía FedEx de {0}: comprar otra sería pagar dos. "
                                      "Si en {1} min no aparece, LabelBridge te avisa.", name, FLOW_WAIT_SECONDS // 60))
            self._note(legacy, "manual", name=name, carrier=carrier, fetch=1)

    def mark_fulfilled(self, legacy: str, name: str, notify: bool = False) -> int:
        """«Marcar preparado en Shopify» sin guía (se envió de otra forma): sale de Pendientes y LabelBridge ya no le
        compra guía. Nunca con una compra de guía en curso o sin confirmar. Devuelve cuántos envíos se prepararon."""
        client = self.client()
        gid = self.order_gid(legacy)
        with self._cycle_lock:  # la compra automática no puede comprarle la guía a la vez
            busy = any(str(info.get("order_id")) == str(legacy) for info in self._pending().values())
            if busy or self.ledger(legacy).get("state") in ("purchasing", "uncertain"):
                raise ShopifyError(tr("{0} tiene una compra de guía en curso o sin confirmar: revísala en Shopify "
                                      "antes de cambiar su estado.", name))
            order = (client.graphql(FULFILLMENTS_QUERY, {"id": gid}).get("order") or {})
            open_fos = [fo for fo in (order.get("fulfillmentOrders") or {}).get("nodes") or []
                        if fo.get("status") in ("OPEN", "IN_PROGRESS")]
            if not open_fos:
                raise ShopifyError(tr("{0} ya está preparado en Shopify.", name))
            data = client.graphql(FULFILL_MUTATION, {"fulfillment": {
                "lineItemsByFulfillmentOrder": [{"fulfillmentOrderId": fo["id"]} for fo in open_fos],
                "notifyCustomer": bool(notify)}}, mutation=True)
            errors = (data.get("fulfillmentCreate") or {}).get("userErrors") or []
            if errors:
                raise ShopifyError(tr("Shopify no lo marcó preparado: {0}", "; ".join(str(e.get("message")) for e in errors)))
            # Cerradas, la compra automática ya no las toca; si se cancela la preparación vuelven a esperar su guía.
            if self.ledger(legacy).get("state") in ACTION_STATES:
                self._note(legacy, "resolved", name=name)
        self.events.emit(ev.ORDERS, {"order": str(legacy), "state": "fulfilled"})
        log.info("Shopify: %s marcado preparado a mano", name)
        return len(open_fos)

    def cancel_fulfillment(self, legacy: str, name: str) -> int:
        """«Cancelar preparación»: el pedido vuelve a Pendientes. Si tiene una guía activa no se toca: anularla en
        Shopify (eso devuelve el dinero y deshace la preparación). Devuelve cuántos envíos se cancelaron."""
        client = self.client()
        gid = self.order_gid(legacy)
        active = [label_id for label_id, entry in client.order_labels(gid).items() if not entry.get("voided")]
        if active:
            raise ShopifyError(tr("{0} tiene una guía activa: anúlala en Shopify («Anular etiqueta») y la preparación "
                                  "se cancela con ella, y Shopify te devuelve el dinero de la guía.", name))
        order = (client.graphql(FULFILLMENTS_QUERY, {"id": gid}).get("order") or {})
        done = [f for f in order.get("fulfillments") or [] if f.get("status") == "SUCCESS"]
        if not done:
            raise ShopifyError(tr("{0} no está preparado en Shopify.", name))
        for fulfillment in done:
            data = client.graphql(CANCEL_FULFILLMENT_MUTATION, {"id": fulfillment["id"]}, mutation=True)
            errors = (data.get("fulfillmentCancel") or {}).get("userErrors") or []
            if errors:
                raise ShopifyError(tr("Shopify no canceló la preparación: {0}",
                                      "; ".join(str(e.get("message")) for e in errors)))
        self.events.emit(ev.ORDERS, {"order": str(legacy), "state": "unfulfilled"})
        log.info("Shopify: preparación de %s cancelada a mano", name)
        return len(done)

    def collect_shopify_label(self, legacy: str, name: str) -> dict:
        """La guía que se compró en Shopify para este pedido (p. ej. FedEx): si ya está, se pone en la cola de impresión
        (una sola vez) y se dice cuál es. {"state": "waiting"} mientras Shopify no la tenga; "printing" con paquetería y
        seguimiento cuando sí; "no_document" si Shopify no entrega el PDF."""
        client = self.client()
        gid = self.order_gid(legacy)
        meta = {"order_gid": gid, "order_name": name, "order_id": str(legacy)}
        active = sorted(((float(entry.get("at") or 0), label_id) for label_id, entry in client.order_labels(gid).items()
                         if not entry.get("voided") and str(label_id).isdigit()), reverse=True)
        if not active:
            return {"state": "waiting"}
        label = client.shipping_label(active[0][1])
        documents, queued = self._enqueue_documents({"shippingLabels": [label]}, meta)
        tracking = label.get("trackingInfo") or {}
        if self.ledger(legacy).get("state") in ACTION_STATES:
            self._note(legacy, "resolved", name=name)  # ya no espera a nadie (el ciclo no la vuelve a traer)
        if not documents:
            return {"state": "no_document"}
        if queued:
            self.engine.wake()
        return {"state": "printing", "carrier": carrier_name(str(tracking.get("company") or "")) or "FedEx",
                "tracking": str(tracking.get("number") or ""), "queued": queued}

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

    def _enqueue_documents(self, result: dict, info: dict, reprint: bool = False) -> tuple[int, int]:
        """Encola los documentos con URL. Devuelve (documentos con URL, trabajos nuevos). `reprint`: otra copia de una
        guía que ya se imprimió (con otro id de trabajo, para que no se descarte como repetida)."""
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
                title = tr("Etiqueta de envío {0}", order_name) if kind == "envío" else tr("Etiqueta de aduana {0}", order_name)
                queued += self._enqueue(
                    JobRequest(
                        external_id=f"shipping-{label_id}-{label_index}-{doc_index}"
                        + (f"-reimpresion-{int(time.time() * 1000)}" if reprint else ""),
                        title=tr("{0} (reimpresión)", title) if reprint else title,
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
            except ShopifyError as exc:  # no se reintenta (es un permiso, no la red): se avisa una vez por motivo
                log.warning("Shopify: no se pudo etiquetar el pedido (%s). ¿Falta el scope write_orders?", exc)
                if str(exc) != self._tag_problem:
                    self._tag_problem = str(exc)
                    self.events.notify(tr("No se pudo agregar el tag «{0}»", tag), tr(
                        "{0}. La guía se imprimió igual; revisa que la app tenga el permiso write_orders o quita el tag "
                        "en Shopify → Filtros.", exc), "error")
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
