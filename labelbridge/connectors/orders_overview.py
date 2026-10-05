"""La pestaña «Pedidos»: los pedidos de Shopify ordenados. Qué necesita atención y por qué, por qué paquetería sale
cada uno y cuánto costó su guía.

Junta lo que dice Shopify (el pedido, sus envíos y las guías compradas o anuladas con su costo, que Shopify anota en el
historial del pedido, también las compradas a mano en el admin) con lo que sabe LabelBridge (qué intentó comprar, qué
falló y qué se imprimió). Todo se calcula aquí, sin pantalla, para poder probarlo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

OVERVIEW_QUERY = """
query LabelBridgeOverview($first: Int!, $after: String, $query: String!) {
  orders(first: $first, after: $after, query: $query, sortKey: CREATED_AT, reverse: true) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      legacyResourceId
      name
      createdAt
      cancelledAt
      displayFinancialStatus
      displayFulfillmentStatus
      tags
      totalPriceSet { shopMoney { amount currencyCode } }
      currentSubtotalPriceSet { shopMoney { amount } }
      shippingAddress { name city provinceCode countryCodeV2 }
      shippingLine { title }
      fulfillments(first: 5) { status createdAt trackingInfo { company number url } }
      fulfillmentOrders(first: 5) { nodes { status deliveryMethod { methodType } } }
      events(
        first: 10
        sortKey: CREATED_AT
        query: "action:shipping_label_created_success OR action:shipping_label_cancelled_success"
      ) {
        nodes { createdAt message ... on BasicEvent { action arguments } }
      }
    }
  }
}
"""

# Sin el permiso de órdenes de preparación (apps solo con read_orders): todo igual menos el retiro en tienda.
OVERVIEW_QUERY_BASIC = OVERVIEW_QUERY.replace(
    "      fulfillmentOrders(first: 5) { nodes { status deliveryMethod { methodType } } }\n", ""
)

PAID_STATUSES = {"PAID", "PARTIALLY_REFUNDED", "AUTHORIZED"}
REFUNDED_STATUSES = {"REFUNDED", "VOIDED"}
# Lo que el registro de LabelBridge marca como pendiente de una persona (ver ShopifyService._note).
ACTION_STATES = {"manual", "failed", "uncertain", "limit", "no_document"}
WAIT_SECONDS = 600  # un pedido nuevo sin guía después de esto ya es raro
TEST_TAGS = ("prueba", "test")

# Pestañas de la pantalla, en orden: (clave, título).
GROUPS = (
    ("action", "Necesitan acción"),
    ("usps", "USPS"),
    ("fedex", "FedEx"),
    ("other", "Otras"),
    ("pickup", "Retiro en tienda"),
    ("all", "Todos"),
)


@dataclass
class OrderRow:
    gid: str
    legacy: str
    name: str
    created: float
    customer: str = ""
    destination: str = ""
    method: str = ""  # método de envío que eligió el cliente (Economy, Express…)
    carrier: str = ""  # USPS, FedEx, UPS, DHL… («» si no se envía)
    group: str = "other"  # usps | fedex | other | pickup
    state: str = "done"  # action | waiting | done | cancelled | info
    status: str = ""  # en pocas palabras
    reason: str = ""  # el porqué y qué hacer (pedidos que necesitan acción)
    action: str = ""  # open | reprint | retry | void
    tracking: list[tuple[str, str, str]] = field(default_factory=list)  # (paquetería, número, enlace)
    cost: float = 0.0  # guías activas
    voided: float = 0.0  # guías anuladas (Shopify devuelve el dinero)
    labels: int = 0  # guías activas
    printed: str = ""  # yes | failed | pending | ""
    print_error: str = ""
    total: float = 0.0
    subtotal: float = 0.0  # valor de las piezas (sin envío ni impuestos)
    currency: str = "USD"
    test: bool = False
    rule_carrier: str = ""  # paquetería según la regla del método de envío

    @property
    def needs_action(self) -> bool:
        return self.state == "action"


# --- piezas -------------------------------------------------------------------------------------

def iso_ts(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


_NUMBER = re.compile(r"\d[\d.,]*")


def parse_amount(message: str) -> float:
    """Importe de un mensaje del historial de Shopify («…por 5,68 $.», «…for $5.68», «2.220,00 $»)."""
    tokens = _NUMBER.findall(message or "")
    if not tokens:
        return 0.0
    token = tokens[-1].strip(".,")
    if "," in token and "." in token:
        decimal = "," if token.rfind(",") > token.rfind(".") else "."
        token = token.replace("." if decimal == "," else ",", "").replace(decimal, ".")
    elif "," in token:
        whole, _, fraction = token.rpartition(",")
        token = f"{whole.replace(',', '')}.{fraction}" if len(fraction) in (1, 2) else token.replace(",", "")
    elif token.count(".") > 1 or (token.count(".") == 1 and len(token.rpartition(".")[2]) == 3):
        token = token.replace(".", "")  # punto de miles («1.234»)
    try:
        return round(float(token), 2)
    except ValueError:
        return 0.0


def carrier_name(company: str) -> str:
    """Nombre corto de la paquetería («United States Postal Service» → «USPS»)."""
    text = (company or "").strip()
    low = text.lower()
    if "fedex" in low or "fed ex" in low:
        return "FedEx"
    if "usps" in low or "postal service" in low:
        return "USPS"
    if re.search(r"\bups\b", low):
        return "UPS"
    if "dhl" in low:
        return "DHL"
    return text


def group_of(carrier: str) -> str:
    return {"USPS": "usps", "FedEx": "fedex"}.get(carrier, "other")


def rule_for(rules: list, method: str, subtotal: float = 0.0):
    """La primera regla de paquetería que se cumple: el método de envío contiene su texto (vacío: cualquiera) y
    las piezas suman más de su valor (0: cualquier valor)."""
    low = (method or "").strip().lower()
    for rule in rules or []:
        text = (getattr(rule, "method", "") or "").strip().lower()
        over = float(getattr(rule, "over", 0) or 0)
        if not text and over <= 0:
            continue  # una regla vacía no decide nada
        if (not text or text in low) and (over <= 0 or subtotal > over):
            return rule
    return None


def subtotal_of(order: dict) -> float:
    money = (order.get("currentSubtotalPriceSet") or order.get("subtotalPriceSet") or {}).get("shopMoney") or {}
    try:
        return float(money.get("amount") or 0)
    except (TypeError, ValueError):
        return 0.0


def rule_text(rule) -> str:
    """La condición de una regla, para explicar el porqué («piezas de más de US$1,000», «método Express»)."""
    parts = []
    if (getattr(rule, "method", "") or "").strip():
        parts.append(f"método «{rule.method.strip()}»")
    if float(getattr(rule, "over", 0) or 0) > 0:
        parts.append(f"piezas de más de {_money(float(rule.over))}")
    return " y ".join(parts)


def label_events(order: dict) -> dict[str, dict[str, Any]]:
    """Guías del pedido según su historial: costo, cuándo y si se anularon."""
    labels: dict[str, dict[str, Any]] = {}
    for node in ((order.get("events") or {}).get("nodes") or []):
        action = node.get("action")
        args = node.get("arguments") or []
        key = str(args[0]) if args else str(node.get("createdAt"))
        if action == "shipping_label_created_success":
            entry = labels.setdefault(key, {"voided": False})
            entry.update(cost=parse_amount(node.get("message", "")), at=iso_ts(node.get("createdAt")))
        elif action == "shipping_label_cancelled_success":
            entry = labels.setdefault(key, {"cost": parse_amount(node.get("message", "")), "at": iso_ts(node.get("createdAt"))})
            entry["voided"] = True
    return labels


def print_state(jobs: list[dict]) -> tuple[str, str]:
    """(yes | failed | pending | «», error) de los trabajos de impresión de la guía del pedido."""
    if not jobs:
        return "", ""
    if any(j.get("status") == "done" for j in jobs):
        return "yes", ""
    latest = max(jobs, key=lambda j: j.get("updated_at") or j.get("created_at") or 0)
    if latest.get("status") == "failed":
        return "failed", str(latest.get("error") or "")
    if latest.get("status") == "cancelled":
        return "", ""
    return "pending", ""


def _destination(address: dict) -> str:
    country = address.get("countryCodeV2") or ""
    parts = [p for p in (address.get("city"), address.get("provinceCode")) if p]
    if country and country != "US":
        parts.append(country)
    return ", ".join(str(p).strip() for p in parts)


def _money(value: float) -> str:
    return f"US${value:,.2f}"


# --- clasificación ------------------------------------------------------------------------------

def classify(
    order: dict,
    *,
    settings,
    ledger: dict | None = None,
    jobs: list[dict] | None = None,
    since: float = 0.0,
    now: float = 0.0,
) -> OrderRow:
    """Un pedido de Shopify → una fila: paquetería, estado y, si hace falta, el porqué y qué hacer."""
    legacy = str(order.get("legacyResourceId") or str(order.get("id", "")).rsplit("/", 1)[-1])
    address = order.get("shippingAddress") or {}
    money = (order.get("totalPriceSet") or {}).get("shopMoney") or {}
    method = ((order.get("shippingLine") or {}).get("title") or "").strip()
    created = iso_ts(order.get("createdAt"))
    tags = [str(t).lower() for t in order.get("tags") or []]
    row = OrderRow(
        gid=str(order.get("id", "")),
        legacy=legacy,
        name=str(order.get("name") or legacy),
        created=created,
        customer=str(address.get("name") or "").strip(),
        destination=_destination(address),
        method=method,
        total=float(money.get("amount") or 0),
        subtotal=subtotal_of(order),
        currency=str(money.get("currencyCode") or "USD"),
        test=any(word in tag for tag in tags for word in TEST_TAGS),
    )
    ledger = ledger or {}
    labels = label_events(order)
    active = [entry for entry in labels.values() if not entry.get("voided")]
    row.labels = len(active)
    row.cost = round(sum(entry.get("cost", 0.0) for entry in active), 2)
    row.voided = round(sum(entry.get("cost", 0.0) for entry in labels.values() if entry.get("voided")), 2)
    fulfillments = order.get("fulfillments") or []
    row.tracking = [
        (carrier_name(info.get("company") or ""), str(info.get("number") or ""), str(info.get("url") or ""))
        for f in fulfillments
        if f.get("status") == "SUCCESS"
        for info in (f.get("trackingInfo") or [])
        if info.get("number")
    ]
    shipped = any(f.get("status") == "SUCCESS" for f in fulfillments)
    row.printed, row.print_error = print_state(jobs or [])

    methods = {
        (fo.get("deliveryMethod") or {}).get("methodType")
        for fo in ((order.get("fulfillmentOrders") or {}).get("nodes") or [])
    }
    if "PICK_UP" in methods:
        kind = "pickup"
    elif "LOCAL" in methods and not methods & {"SHIPPING", "PICKUP_POINT"}:
        kind = "local"
    elif methods & {"SHIPPING", "PICKUP_POINT"} or (not methods and address):
        kind = "ship"
    else:
        kind = "none"
    rule = rule_for(getattr(settings, "shipping_rules", []), method, row.subtotal)
    row.rule_carrier = rule.carrier if rule else getattr(settings, "default_carrier", "USPS")
    if kind == "ship":
        row.carrier = row.tracking[0][0] if row.tracking else (ledger.get("carrier") or row.rule_carrier)
        row.group = group_of(row.carrier)
    elif kind == "pickup":
        row.carrier, row.group = "", "pickup"
    else:
        row.carrier, row.group = "", "other"

    def act(status: str, reason: str, action: str) -> OrderRow:
        row.state, row.status, row.reason, row.action = "action", status, reason, action
        return row

    def keep(state: str, status: str) -> OrderRow:
        row.state, row.status = state, status
        return row

    financial = order.get("displayFinancialStatus") or ""
    if order.get("cancelledAt") or financial in REFUNDED_STATUSES:
        word = "Cancelado" if order.get("cancelledAt") else "Reembolsado"
        if active:
            return act(
                f"{word} con guía activa",
                f"{word}, pero su guía de {_money(row.cost)} sigue activa: anúlala en Shopify para recuperar el dinero.",
                "void",
            )
        return keep("cancelled", word + (" · guía anulada" if row.voided else ""))
    if kind == "pickup":
        return keep("info", "Retirado en tienda" if shipped else "Retiro en tienda")
    if kind == "local":
        return keep("info", "Entrega local")
    if kind == "none":
        return keep("info", "Sin envío")

    s = settings
    if active:
        if ledger.get("state") == "no_document" and row.printed != "yes":
            return act(
                "Guía sin PDF",
                "Shopify compró la guía pero no entregó el PDF para imprimir: imprímela desde el pedido en Shopify.",
                "open",
            )
        if row.printed == "failed":
            return act(
                "Guía sin imprimir",
                f"La guía se compró pero no se imprimió: {row.print_error or 'error de la impresora'}. Revisa la "
                "impresora y reimprímela.",
                "reprint",
            )
        if row.printed == "pending":
            return keep("waiting", "Imprimiendo la guía…")
        if row.printed == "yes":
            return keep("done", "Guía comprada e impresa")
        return keep("done", "Guía comprada en Shopify")
    if shipped:
        if row.tracking:
            return keep("done", "Enviado con seguimiento")
        if created >= since:
            return act(
                "Preparado sin guía",
                "Shopify lo marcó como preparado sin guía ni seguimiento (el cliente recibió el aviso de envío sin "
                "número). Desactiva la preparación automática en Shopify → Configuración → Pago.",
                "open",
            )
        return keep("info", "Preparado sin seguimiento")
    if getattr(s, "required_tag", "").strip() and s.required_tag.strip().lower() not in tags:
        return keep("info", f"Sin el tag «{s.required_tag.strip()}»: LabelBridge no lo procesa")
    if financial not in PAID_STATUSES:
        return keep("waiting", "Esperando el pago")
    state = ledger.get("state")
    if (rule is not None and not rule.auto) or state == "manual":
        carrier = row.carrier or "Esta paquetería"
        why = f" por la regla de {rule_text(rule)}" if rule is not None and rule_text(rule) else ""
        return act(
            f"Comprar guía {carrier} a mano",
            f"Sale por {carrier}{why} (piezas: {_money(row.subtotal)}). Shopify no deja comprar esta guía "
            "automáticamente: cómprala a mano en el pedido en Shopify.",
            "open",
        )
    if state == "failed":
        return act(
            "No se pudo comprar la guía",
            f"No se pudo comprar la guía: {ledger.get('reason') or 'Shopify no dio el motivo'}. Corrígelo y "
            "pulsa «Reintentar compra».",
            "retry",
        )
    if state == "uncertain":
        return act(
            "Compra sin confirmar",
            "No se sabe si la guía se compró (se cortó la conexión). Revísalo en Shopify antes de comprarla a mano.",
            "open",
        )
    if state == "limit":
        return act(
            "Límite diario de guías",
            f"Se alcanzó el máximo de {getattr(s, 'max_labels_per_day', 0)} guías por día. Súbelo en Shopify → "
            "Máximo por día o pulsa «Reintentar compra» mañana.",
            "retry",
        )
    if not getattr(s, "buy_shipping_label", False):
        return act(
            "Falta la guía",
            "La compra automática de guías está desactivada: cómprala a mano en Shopify o actívala en la pestaña "
            "Shopify.",
            "open",
        )
    if created < since:
        return act(
            "Falta la guía",
            "Es de antes de conectar LabelBridge, por eso no se compró sola. Pulsa «Reintentar compra» o cómprala a "
            "mano en Shopify.",
            "retry",
        )
    if now - created < WAIT_SECONDS:
        return keep("waiting", "Comprando la guía…")
    return act(
        "Falta la guía",
        "Sigue sin guía: revisa que LabelBridge esté conectado a Shopify y pulsa «Reintentar compra».",
        "retry",
    )


@dataclass
class Summary:
    orders: int = 0
    action: int = 0
    labels: int = 0
    cost: float = 0.0
    voided: float = 0.0
    by_carrier: dict[str, tuple[int, float]] = field(default_factory=dict)  # paquetería → (guías, costo)
    groups: dict[str, int] = field(default_factory=dict)  # pestaña → pedidos
    auto_fulfill: int = 0  # pedidos preparados solos sin guía (ajuste de Shopify)


def summarize(rows: list[OrderRow]) -> Summary:
    summary = Summary(orders=len(rows))
    for key, _title in GROUPS:
        summary.groups[key] = 0
    for row in rows:
        summary.groups["all"] += 1
        summary.groups[row.group] = summary.groups.get(row.group, 0) + 1
        if row.needs_action:
            summary.action += 1
            summary.groups["action"] += 1
        if row.status == "Preparado sin guía":
            summary.auto_fulfill += 1
        summary.voided += row.voided
        if row.labels:
            summary.labels += row.labels
            summary.cost += row.cost
            count, cost = summary.by_carrier.get(row.carrier or "Otra", (0, 0.0))
            summary.by_carrier[row.carrier or "Otra"] = (count + row.labels, round(cost + row.cost, 2))
    summary.cost = round(summary.cost, 2)
    summary.voided = round(summary.voided, 2)
    return summary


def in_group(row: OrderRow, group: str) -> bool:
    if group == "all":
        return True
    if group == "action":
        return row.needs_action
    return row.group == group


CSV_COLUMNS = (
    "Pedido", "Fecha", "Cliente", "Destino", "Método de envío", "Paquetería", "Seguimiento", "Costo de la guía (USD)",
    "Guías anuladas (USD)", "Impresa", "Estado", "Qué hacer",
)


def csv_rows(rows: list[OrderRow]) -> list[list[str]]:
    printed = {"yes": "Sí", "failed": "Falló", "pending": "En cola", "": "—"}
    out = []
    for row in rows:
        out.append([
            row.name,
            datetime.fromtimestamp(row.created).strftime("%Y-%m-%d %H:%M") if row.created else "",
            row.customer,
            row.destination,
            row.method,
            row.carrier,
            " ".join(number for _carrier, number, _url in row.tracking),
            f"{row.cost:.2f}",
            f"{row.voided:.2f}",
            printed.get(row.printed, row.printed),
            row.status,
            row.reason,
        ])
    return out
