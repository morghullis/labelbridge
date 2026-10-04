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
from ..jobstore import JobStore
from ..models import JobRequest
from ..printing.engine import PrintEngine
from ..secret_store import SHOPIFY_ACCESS_TOKEN, SHOPIFY_CLIENT_SECRET, SecretStore
from ..security.policy import PolicyDenied
from .base import BackgroundService

log = logging.getLogger(__name__)

PAID_STATUSES = {"PAID", "PARTIALLY_REFUNDED", "AUTHORIZED"}
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
      totalPriceSet { shopMoney { amount currencyCode } }
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

TAGS_ADD_MUTATION = """
mutation LabelBridgeTagsAdd($id: ID!, $tags: [String!]!) {
  tagsAdd(id: $id, tags: $tags) { userErrors { field message } }
}
"""


# App de LabelBridge en el Dev Dashboard de Shopify: permisos para leer pedidos y para comprar guías.
DEV_DASHBOARD_URL = "https://dev.shopify.com/dashboard"
SCOPES_BASIC = "read_orders"
SCOPES_LABELS = (
    "read_orders,write_orders,read_merchant_managed_fulfillment_orders,write_merchant_managed_fulfillment_orders"
)


class ShopifyError(Exception):
    """uncertain=True: no se sabe si Shopify ejecutó la operación (error 5xx, corte a mitad...)."""

    def __init__(self, message: str, uncertain: bool = False) -> None:
        super().__init__(message)
        self.uncertain = uncertain


class PurchaseRejected(ShopifyError):
    """Shopify respondió que NO compró la etiqueta (userErrors): se puede reintentar."""


def normalize_shop(shop: str) -> str:
    value = shop.strip().lower()
    value = re.sub(r"^https?://", "", value).split("/")[0]
    if value and "." not in value:
        value += ".myshopify.com"
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-]*\.myshopify\.com", value):
        raise ShopifyError("Usa el dominio interno de la tienda: mi-tienda.myshopify.com")
    return value


def admin_url(shop: str, path: str = "") -> str:
    """Página del admin de Shopify de la tienda (o el admin genérico si la tienda no es válida)."""
    try:
        handle = normalize_shop(shop).split(".")[0]
    except ShopifyError:
        return "https://admin.shopify.com"
    return f"https://admin.shopify.com/store/{handle}" + (f"/{path}" if path else "")


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

    # --- autenticación ---------------------------------------------------------------
    def access_token(self, force: bool = False) -> str:
        if self.auth_mode == "token":
            token = self.secrets.get(SHOPIFY_ACCESS_TOKEN)
            if not token:
                raise ShopifyError("Falta el token de acceso de la Admin API (shpat_…)")
            return token
        if not force and self._token and time.time() < self._token_expiry - 300:
            return self._token
        secret = self.secrets.get(SHOPIFY_CLIENT_SECRET)
        if not self.client_id or not secret:
            raise ShopifyError("Faltan el Client ID y/o el Client secret de la app (Dev Dashboard)")
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
                    "Shopify no permite client credentials en esta tienda: la app y la tienda deben estar "
                    "en la misma organización del Dev Dashboard (ver docs/SHOPIFY.md)"
                )
            raise ShopifyError(f"Shopify rechazó las credenciales (HTTP {resp.status}): {detail}")
        data = resp.json() or {}
        self._token = str(data.get("access_token") or "")
        if not self._token:
            raise ShopifyError("Shopify no devolvió un token de acceso")
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
        _retry: int = 0,
        _refresh: bool = False,
    ) -> dict[str, Any]:
        """mutation=True: nunca se reintenta algo que Shopify pudo haber ejecutado (evita compras dobles)."""
        resp = net.request(
            "POST",
            f"https://{self.shop}/admin/api/{self.api_version}/graphql.json",
            headers={"X-Shopify-Access-Token": self.access_token(force=_refresh), "Accept": "application/json"},
            json_body={"query": query, "variables": variables or {}},
            timeout=40,
            raise_for_status=False,
        )
        if resp.status == 401 and self.auth_mode != "token" and not _refresh:
            return self.graphql(query, variables, mutation=mutation, _retry=_retry, _refresh=True)
        if resp.status == 401:
            raise ShopifyError("Token inválido o revocado (HTTP 401)")
        if resp.status == 402:
            raise ShopifyError("La tienda está pausada o sin plan activo (HTTP 402)")
        if resp.status == 404:
            raise ShopifyError(f"Tienda o versión de API no encontrada ({self.shop}, {self.api_version})")
        if (resp.status == 429 or (resp.status == 503 and not mutation)) and _retry < 3:
            time.sleep(float(resp.headers.get("retry-after", "2") or 2))
            return self.graphql(query, variables, mutation=mutation, _retry=_retry + 1)
        if resp.status >= 400:
            raise ShopifyError(
                f"HTTP {resp.status}: {resp.body[:300].decode('utf-8', 'replace')}", uncertain=resp.status >= 500
            )
        payload = resp.json() or {}
        errors = payload.get("errors") or []
        if errors:
            codes = {(e.get("extensions") or {}).get("code") for e in errors}
            if "THROTTLED" in codes and _retry < 3:  # rechazada antes de ejecutarse: reintentar es seguro
                time.sleep(2 + _retry * 2)
                return self.graphql(query, variables, mutation=mutation, _retry=_retry + 1)
            messages = "; ".join(str(e.get("message", e)) for e in errors)
            if "access denied" in messages.lower() or "ACCESS_DENIED" in codes:
                messages += " (faltan permisos/scopes en la app de Shopify)"
            raise ShopifyError(messages, uncertain="INTERNAL_SERVER_ERROR" in codes)
        return payload.get("data") or {}

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

    def fulfillment_orders(self, order_gid: str) -> list[dict]:
        data = self.graphql(FULFILLMENT_ORDERS_QUERY, {"id": order_gid})
        return ((data.get("order") or {}).get("fulfillmentOrders") or {}).get("nodes") or []

    def purchase_label(self, fulfillment_order_id: str, notify_customer: bool) -> str:
        ship_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        data = self.graphql(
            PURCHASE_MUTATION,
            {
                "input": {
                    "fulfillmentOrderId": fulfillment_order_id,
                    "shippingDatetime": ship_at,
                    "notifyCustomer": bool(notify_customer),
                }
            },
            mutation=True,
        )
        result = data.get("shippingLabelPurchase") or {}
        errors = result.get("userErrors") or []
        if errors:
            raise PurchaseRejected("; ".join(f"{e.get('code') or ''} {e.get('message')}".strip() for e in errors))
        purchase = result.get("shippingLabelPurchaseResult") or {}
        if not purchase.get("id"):
            raise ShopifyError("Shopify no devolvió el resultado de la compra", uncertain=True)
        return str(purchase["id"])

    def label_result(self, result_id: str) -> dict[str, Any]:
        return self.graphql(LABEL_RESULT_QUERY, {"id": result_id}).get("node") or {}

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
        events.subscribe(self._on_event)
        engine.url_auth_providers.append(self._auth_headers_for)

    def _auth_headers_for(self, url: str) -> dict[str, str]:
        """Las descargas de documentos alojados en la propia tienda llevan el token (sin guardarlo en la cola)."""
        client = self._client
        if client is None or urlparse(url).hostname != client.shop:
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

    def since_ts(self) -> float:
        value = self.store.kv_get(self._key("since"))
        if value is None:
            now = time.time()
            self.store.kv_set(self._key("since"), str(now))
            return now
        return float(value)

    def reset_since(self, hours_back: float = 0.0) -> float:
        """Procesar pedidos desde ahora (o desde hace X horas)."""
        since = time.time() - max(0.0, hours_back) * 3600
        self.store.kv_set(self._key("since"), str(since))
        self.store.kv_set(self._key("cursor"), _ts_to_iso(since))
        self.store.kv_set(self._key("resume"), "null")
        self.wake()
        return since

    # --- bucle -----------------------------------------------------------------------------
    def run(self, stop: threading.Event, wake: threading.Event) -> None:
        self.set_status("connecting", "Conectando con Shopify…")
        failures = 0
        while not stop.is_set():
            settings = self.cfg.data.shopify
            try:
                client = self.client()
                summary = self.cycle(client, settings, stop)
                failures = 0
                self.set_status("ok", f"Conectado a {client.shop} · revisado a las {time.strftime('%H:%M:%S')}{summary}")
            except (ShopifyError, net.NetworkError, net.HttpError) as exc:
                failures += 1
                self.set_status("error", str(exc))
                log.warning("Shopify: %s", exc)
            except Exception as exc:
                failures += 1
                log.exception("Shopify: error inesperado")
                self.set_status("error", f"Error: {exc}")
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
                return " · detenido"  # sin guardar progreso: la próxima vuelta repite estos pedidos
            queued += self._process_order(client, settings, order, since, stop)
            if order.get("updatedAt", "") > newest:
                newest = order["updatedAt"]
        # Progreso guardado solo cuando todos los pedidos de estas páginas quedaron procesados.
        self.store.kv_set(self._key("cursor"), newest)
        self.store.kv_set(
            self._key("resume"), json.dumps({"from": query_from, "after": next_after} if next_after else None)
        )
        queued += self._check_pending_purchases(client, stop=stop)
        self._flush_tags(client)
        if queued:
            self.engine.wake()
        if next_after:
            self.wake()  # quedan páginas: la siguiente vuelta empieza enseguida
        return f" · {queued} etiqueta(s) en cola" if queued else ""

    # --- por pedido ----------------------------------------------------------------------------
    def _process_order(
        self, client: ShopifyClient, s: ShopifySettings, order: dict, since: float, stop: threading.Event
    ) -> int:
        if order.get("cancelledAt"):
            return 0
        tags = [t.lower() for t in order.get("tags") or []]
        if s.required_tag.strip() and s.required_tag.strip().lower() not in tags:
            return 0
        legacy = str(order.get("legacyResourceId") or order.get("id", "").rsplit("/", 1)[-1])
        name = order.get("name") or legacy
        paid_ok = not s.only_paid or order.get("displayFinancialStatus") in PAID_STATUSES
        open_ok = not s.only_unfulfilled or order.get("displayFulfillmentStatus") in OPEN_FULFILLMENT
        is_new = _iso_to_ts(order.get("createdAt", "")) >= since
        meta = {"order_gid": order.get("id"), "order_name": name, "order_id": legacy}
        queued = 0

        if s.print_order_label and is_new and paid_ok and open_ok:
            key = self._key(f"order:{legacy}:label")
            if not self.store.seen(key):
                queued += self._enqueue(
                    JobRequest(
                        external_id=f"pedido-{legacy}",
                        title=f"Pedido {name}",
                        printer=s.order_label_printer,
                        format="order",
                        order=order,
                        meta=meta,
                    )
                )
                self.store.mark_seen(key)

        if s.metafield_label:
            for url in extract_urls(str((order.get("metafield") or {}).get("value") or "")):
                digest = hashlib.sha1(url.encode()).hexdigest()[:16]
                key = self._key(f"order:{legacy}:mf:{digest}")
                if self.store.seen(key):
                    continue
                queued += self._enqueue(
                    JobRequest(
                        external_id=f"metafield-{legacy}-{digest}",
                        title=f"Etiqueta {name}",
                        printer=s.metafield_printer,
                        url=url,
                        meta=meta,
                    )
                )
                self.store.mark_seen(key)

        if s.buy_shipping_label and is_new and paid_ok and open_ok:
            queued += self._buy_labels(client, s, order, meta, stop)
        return queued

    def _enqueue(self, req: JobRequest) -> int:
        try:
            job, created = self.engine.submit(req, "shopify")
        except PolicyDenied as exc:
            log.error("Shopify: %s rechazado por seguridad: %s", req.title, exc)
            self.events.notify("Etiqueta bloqueada por seguridad", f"{req.title}: {exc}", "error")
            return 0
        if created:
            log.info("Shopify: en cola %s", job["title"])
        return int(created)

    # --- compra de etiquetas Shopify Shipping ----------------------------------------------------
    def _buy_labels(
        self, client: ShopifyClient, s: ShopifySettings, order: dict, meta: dict, stop: threading.Event
    ) -> int:
        queued = 0
        for fo in client.fulfillment_orders(order["id"]):
            if stop.is_set():
                break
            if fo.get("status") not in ("OPEN", "IN_PROGRESS"):
                continue
            method = (fo.get("deliveryMethod") or {}).get("methodType")
            if method not in SHIPPABLE_METHODS:
                continue
            fo_id = str(fo["id"])
            key = self._key(f"fo:{fo_id.rsplit('/', 1)[-1]}:label")
            if self.store.seen(key):
                continue
            attempts_key = key + ":intentos"
            attempts = int(self.store.kv_get(attempts_key) or 0)
            if attempts >= 3:
                continue
            today = self.store.start_of_today()
            if self.store.count_seen(self._key("fo:"), today) >= max(0, s.max_labels_per_day):
                if self._limit_warned_day != today:
                    self._limit_warned_day = today
                    self.events.notify(
                        "Límite diario de etiquetas",
                        f"Se alcanzó el máximo de {s.max_labels_per_day} etiquetas compradas hoy. No se comprarán más.",
                        "error",
                    )
                return queued
            self.store.kv_set(attempts_key, str(attempts + 1))
            # La compra se registra ANTES de pedirla: si algo falla a mitad, nunca se compra dos veces.
            self.store.mark_seen(key)
            try:
                result_id = client.purchase_label(fo_id, s.notify_customer)
            except PurchaseRejected as exc:
                # Shopify contestó que no compró: se puede volver a intentar (p. ej. tras corregir el paquete).
                self.store.unmark_seen(key)
                log.error("Shopify rechazó la compra de la etiqueta de %s: %s", meta["order_name"], exc)
                self.events.notify(
                    "No se pudo comprar la etiqueta",
                    f"{meta['order_name']}: {exc}. Corrígelo y usa «Reprocesar» en la pantalla Shopify.",
                    "error",
                )
                continue
            except (ShopifyError, net.NetworkError, net.HttpError) as exc:
                if not getattr(exc, "uncertain", False) and not getattr(exc, "maybe_sent", False):
                    self.store.unmark_seen(key)  # seguro que no llegó a Shopify
                    raise
                # No sabemos si Shopify la compró: no se reintenta sola para no pagar dos veces.
                log.error("Shopify: compra de etiqueta sin confirmar para %s: %s", meta["order_name"], exc)
                self.events.notify(
                    "Revisa la etiqueta en Shopify",
                    f"{meta['order_name']}: no se pudo confirmar si la etiqueta se compró ({exc}). "
                    "Revísalo en Shopify antes de comprarla a mano.",
                    "error",
                )
                continue
            pending = self._pending()
            pending[result_id] = {**meta, "started": time.time()}
            self._save_pending(pending)
            log.info("Shopify: compra de etiqueta iniciada para %s", meta["order_name"])
            queued += self._check_pending_purchases(client, wait_seconds=20, stop=stop)
        return queued

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
                result = client.label_result(result_id)
                status = result.get("status")
                age = time.time() - float(info.get("started", 0))
                if status == "PURCHASED":
                    documents, new_jobs = self._enqueue_documents(result, info)
                    queued += new_jobs
                    if documents:
                        pending.pop(result_id)
                    elif age > 600:
                        self.events.notify(
                            "Etiqueta comprada sin documento",
                            f"{info.get('order_name')}: Shopify no entregó el PDF/ZPL. Imprímela desde Shopify.",
                            "error",
                        )
                        pending.pop(result_id)
                elif status == "PURCHASE_FAILED":
                    reason = "; ".join(e.get("message", "") for e in result.get("errors") or []) or "sin detalle"
                    log.error("Shopify: falló la compra de etiqueta de %s: %s", info.get("order_name"), reason)
                    self.events.notify("Falló la compra de etiqueta", f"{info.get('order_name')}: {reason}", "error")
                    pending.pop(result_id)
                elif age > 3600:
                    self.events.notify(
                        "Compra de etiqueta sin respuesta",
                        f"{info.get('order_name')}: revisa el pedido en Shopify.",
                        "error",
                    )
                    pending.pop(result_id)
            self._save_pending(pending)
            if not pending or time.time() >= deadline or stop.wait(2):
                break
        return queued

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
                label_id = str(label.get("id", "")).rsplit("/", 1)[-1]
                queued += self._enqueue(
                    JobRequest(
                        external_id=f"shipping-{label_id}-{label_index}-{doc_index}",
                        title=f"Etiqueta de {kind} {order_name}",
                        printer=printer,
                        format=fmt,
                        url=url,
                        meta={
                            **{k: v for k, v in info.items() if k != "started"},
                            "tracking": tracking.get("number"),
                            "carrier": tracking.get("company"),
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

    # --- prueba de conexión (desde la UI) ---------------------------------------------------------
    def test_connection(self) -> dict[str, Any]:
        client = ShopifyClient(self.cfg.data.shopify, self.secrets)
        info = client.shop_info()
        scopes = set(info.get("scopes") or [])
        needed = {"read_orders": "leer pedidos"}
        settings = self.cfg.data.shopify
        if settings.buy_shipping_label:
            needed.update(
                {
                    "write_orders": "comprar etiquetas",
                    "write_merchant_managed_fulfillment_orders": "comprar etiquetas",
                }
            )
        if settings.tag_after_print:
            needed["write_orders"] = "agregar tags"
        implied = {s.replace("write_", "read_") for s in scopes if s.startswith("write_")}
        missing = [f"{scope} ({why})" for scope, why in needed.items() if scope not in scopes | implied]
        info["missing"] = missing
        return info
