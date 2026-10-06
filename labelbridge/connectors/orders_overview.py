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

from ..i18n import N_, date_text, tr

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
      requiresShipping
      totalPriceSet { shopMoney { amount currencyCode } }
      currentSubtotalPriceSet { shopMoney { amount } }
      shippingAddress { name company address1 city provinceCode zip countryCodeV2 }
      shippingLine { title }
      fulfillments(first: 5) {
        status createdAt displayStatus inTransitAt deliveredAt estimatedDeliveryAt
        trackingInfo { company number url }
      }
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
# Países sin código postal (no se exige para que el pedido lleve guía) y países donde el estado es obligatorio.
NO_POSTCODE = {
    "AE", "AG", "AO", "AW", "BF", "BI", "BJ", "BO", "BS", "BW", "BZ", "CD", "CF", "CG", "CI", "CK", "CM", "DJ", "DM",
    "ER", "FJ", "GA", "GD", "GH", "GM", "GQ", "GY", "HK", "IE", "JM", "KI", "KM", "KN", "KP", "LC", "ML", "MO", "MR",
    "MS", "MW", "NR", "NU", "QA", "RW", "SB", "SC", "SL", "SO", "SR", "ST", "SY", "TF", "TK", "TL", "TO", "TT", "TV",
    "TZ", "UG", "VU", "YE", "ZW",
}
NEEDS_PROVINCE = {"US", "CA", "MX", "AU"}
# Lo que el registro de LabelBridge marca como pendiente de una persona (ver ShopifyService._note).
ACTION_STATES = {"manual", "failed", "uncertain", "limit", "no_document"}
WAIT_SECONDS = 600  # un pedido nuevo sin guía después de esto ya es raro
TEST_TAGS = ("prueba", "test")

# Por qué Shopify no compró una guía (código de su respuesta → en palabras de cualquiera).
PURCHASE_ERRORS = {
    "TOTAL_WEIGHT_ZERO": N_("Falta el peso del paquete"),
    "RATES_NOT_FOUND": N_("Shopify no encontró tarifas para este envío: revisa la dirección, la caja y el peso"),
    "CARRIER_NOT_AVAILABLE": N_("La paquetería no está disponible para este envío"),
    "PHONE_NUMBER_NOT_FULLY_QUALIFIED_DESTINATION": N_("Falta el teléfono completo del cliente (con código de país)"),
    "PHONE_NUMBER_NOT_FULLY_QUALIFIED_ORIGIN": N_("Falta el teléfono completo de tu tienda (con código de país)"),
    "TERMS_OF_SERVICE_NOT_ACCEPTED": N_("Acepta los términos de Shopify Shipping comprando una guía en Shopify una vez"),
    "SHIPPING_DATE_IN_THE_PAST": N_("La fecha de envío quedó en el pasado"),
    "FULFILLMENT_ORDER_INVALID": N_("El pedido ya no se puede enviar así (¿ya está preparado o cancelado?)"),
    "PACKAGE_CARRIER_MISMATCH": N_("La caja no corresponde a la paquetería"),
    "PURCHASE_FAILED": N_("La paquetería rechazó la compra: revisa los datos del envío"),
}


# Cómo va el envío, según lo que Shopify sabe de la paquetería.
DELIVERY_STATUS = {
    "CARRIER_PICKED_UP": N_("Recogido por la paquetería"),
    "IN_TRANSIT": N_("En camino"),
    "OUT_FOR_DELIVERY": N_("Sale para entrega hoy"),
    "READY_FOR_PICKUP": N_("Listo para recoger"),
    "PICKED_UP": N_("Recogido por el cliente"),
    "DELIVERED": N_("Entregado"),
}
# Lo que la paquetería informa como problema: (título, qué pasó y qué hacer).
DELIVERY_PROBLEMS = {
    "ATTEMPTED_DELIVERY": (N_("Intento de entrega fallido"),
                           N_("La paquetería intentó entregarlo y no pudo. Revisa el seguimiento y avisa al cliente si hace falta.")),
    "DELAYED": (N_("Envío retrasado"), N_("La paquetería informa que el envío va con retraso.")),
    "FAILURE": (N_("Problema con el envío"), N_("La paquetería reporta un problema con el envío: revisa el seguimiento.")),
    "NOT_DELIVERED": (N_("No se pudo entregar"),
                      N_("La paquetería no pudo entregarlo: revisa el seguimiento y contacta al cliente.")),
}
WAITING_FOR_CARRIER = {"", "LABEL_PURCHASED", "LABEL_PRINTED", "CONFIRMED", "FULFILLED", "SUBMITTED"}


def _delivery(row: "OrderRow", fulfillments: list[dict], now: float, limits) -> None:
    """Cómo va la entrega del último envío con seguimiento y si hay un retraso o un problema."""
    shipments = [f for f in fulfillments if f.get("status") == "SUCCESS" and f.get("trackingInfo")]
    if not shipments:
        return
    latest = max(shipments, key=lambda f: iso_ts(f.get("createdAt")))
    status = str(latest.get("displayStatus") or "")
    eta_text = str(latest.get("estimatedDeliveryAt") or "")
    row.delivery, row.eta, row.delivered = status, iso_ts(eta_text), iso_ts(latest.get("deliveredAt"))
    if row.eta and eta_text.endswith(("T00:00:00Z", "T00:00:00+00:00")):
        row.eta += 12 * 3600  # solo la fecha (medianoche UTC): ese día, no la noche anterior en América
    if row.delivered or status in ("DELIVERED", "PICKED_UP", "CANCELED", "LABEL_VOIDED"):
        return
    if status in DELIVERY_PROBLEMS:
        title, reason = DELIVERY_PROBLEMS[status]
        row.alert, row.alert_title, row.alert_reason = "problem", tr(title), tr(reason)
        return
    if row.eta and now > row.eta + 24 * 3600:
        row.alert, row.alert_title = "late", tr("Entrega atrasada")
        row.alert_reason = tr("Debía llegar el {0} y todavía no se entrega ({1} día(s) de retraso).",
                              date_text(row.eta, clock=""), max(1, int((now - row.eta) // 86400)))
        return
    in_transit, shipped_at = iso_ts(latest.get("inTransitAt")), iso_ts(latest.get("createdAt"))
    no_scan = int(getattr(limits, "no_scan_days", 2) or 0)
    if not in_transit and status in WAITING_FOR_CARRIER and no_scan and now - shipped_at > no_scan * 86400:
        row.alert, row.alert_title = "no_scan", tr("La paquetería no lo ha recibido")
        row.alert_reason = tr("La guía se compró hace {0} día(s) y {1} todavía no registra el paquete: ¿ya se entregó en la "
                              "oficina o lo recogieron?", int((now - shipped_at) // 86400), row.carrier or tr("la paquetería"))
        return
    transit = int(getattr(limits, "transit_days", 7) or 0)
    if in_transit and not row.eta and transit and now - in_transit > transit * 86400:
        row.alert, row.alert_title = "slow", tr("Mucho tiempo en camino")
        row.alert_reason = tr("Lleva {0} días en camino sin fecha de entrega: revisa el seguimiento.",
                              int((now - in_transit) // 86400))


def delivery_text(row: "OrderRow") -> str:
    """«En camino · llega el 10/08», «Entregado el 10/07»…"""
    text = tr(DELIVERY_STATUS[row.delivery])
    if row.delivered:
        return tr("{0} el {1}", text, date_text(row.delivered, year=False, clock=""))
    if row.eta:
        return tr("{0} · llega el {1}", text, date_text(row.eta, year=False, clock=""))
    return text


def shipping_problem(order: dict) -> str:
    """Por qué un pedido no puede llevar guía de paquetería («» si puede): tiene que requerir envío y tener a quién y
    adónde mandarlo. Así nunca se compra una guía sin sentido (retiro en tienda, venta en el local, un pedido de otro
    canal sin dirección…). Los pedidos que no la cumplen no están en LabelBridge."""
    if order.get("requiresShipping") is False:
        return tr("no requiere envío")
    address = order.get("shippingAddress") or {}
    if not address:
        return tr("no tiene dirección de envío")
    name = " ".join(str(address.get(key) or "").strip() for key in ("firstName", "lastName")).strip()
    if not (str(address.get("name") or "").strip() or name or str(address.get("company") or "").strip()):
        return tr("falta el nombre de quien recibe")
    if "address1" in address and not str(address.get("address1") or "").strip():
        return tr("falta la dirección")
    if not str(address.get("city") or "").strip():
        return tr("falta la ciudad")
    country = str(address.get("countryCodeV2") or "").strip().upper()
    if not country:
        return tr("falta el país")
    if country in NEEDS_PROVINCE and not str(address.get("provinceCode") or address.get("province") or "").strip():
        return tr("falta el estado")
    if "zip" in address and country not in NO_POSTCODE and not str(address.get("zip") or "").strip():
        return tr("falta el código postal")
    return ""


def friendly_reason(reason: str) -> str:
    """El motivo de Shopify con su código traducido («TOTAL_WEIGHT_ZERO …» → «Falta el peso del paquete»)."""
    text = (reason or "").strip()
    code = text.split(" ", 1)[0].strip(":;,.")
    return tr(PURCHASE_ERRORS[code]) if code in PURCHASE_ERRORS else text

# Pestañas de la pantalla, en orden: (clave, título). Solo hay pedidos que se envían (ver belongs).
GROUPS = (
    ("action", N_("Necesitan acción")),
    ("delays", N_("Retrasos")),
    ("usps", N_("USPS")),
    ("fedex", N_("FedEx")),
    ("other", N_("Otras")),
    ("all", N_("Todos")),
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
    issue: str = ""  # código del problema, sin traducir (auto_fulfilled…)
    problem: str = ""  # por qué Shopify no compró la guía, en palabras de cualquiera
    delivery: str = ""  # cómo va el envío según la paquetería (IN_TRANSIT, DELIVERED…)
    eta: float = 0.0  # fecha estimada de entrega
    delivered: float = 0.0
    alert: str = ""  # retraso o problema de entrega: problem | late | no_scan | slow
    alert_title: str = ""
    alert_reason: str = ""
    waiting_days: int = 0  # días pagado y sin enviar (pedidos que necesitan acción)
    kind: str = "ship"  # ship | pickup | local | none (cómo se entrega)
    address_problem: str = ""  # por qué no puede llevar guía (sin dirección, sin envío…); «» si puede
    country: str = ""  # país de destino (código de dos letras)
    shipped: bool = False  # Shopify ya lo marcó enviado (con o sin guía)
    paid: bool = True

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
        parts.append(tr("método «{0}»", rule.method.strip()))
    if float(getattr(rule, "over", 0) or 0) > 0:
        parts.append(tr("piezas de más de {0}", _money(float(rule.over))))
    return tr(" y ").join(parts)


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
    if latest.get("status") in ("canceled", "cancelled"):  # la cola escribe «canceled»
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
    limits=None,
) -> OrderRow:
    """Un pedido de Shopify → una fila: paquetería, estado y, si hace falta, el porqué y qué hacer.

    `limits` (NotificationSettings) marca los retrasos: días sin que la paquetería reciba el paquete y en camino."""
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
    row.shipped = shipped
    row.paid = (order.get("displayFinancialStatus") or "") in PAID_STATUSES
    row.country = str(address.get("countryCodeV2") or "")
    row.address_problem = shipping_problem(order)
    row.printed, row.print_error = print_state(jobs or [])
    row.carrier = row.tracking[0][0] if row.tracking else ""
    _delivery(row, fulfillments, now or datetime.now().timestamp(), limits)

    methods = {
        (fo.get("deliveryMethod") or {}).get("methodType")
        for fo in ((order.get("fulfillmentOrders") or {}).get("nodes") or [])
    }
    if methods & {"SHIPPING", "PICKUP_POINT"} or (not methods and address):
        kind = "ship"  # algo sale por paquetería (aunque otra parte se retire en la tienda): lleva guía
    elif "PICK_UP" in methods:
        kind = "pickup"
    elif "LOCAL" in methods:
        kind = "local"
    else:
        kind = "none"
    row.kind = kind
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
        if not shipped and created and now:
            row.waiting_days = int(max(0.0, now - created) // 86400)
        return row

    def keep(state: str, status: str) -> OrderRow:
        row.state, row.status = state, status
        if state == "done" and row.alert:
            row.status = row.alert_title
        elif state == "done" and row.delivery in DELIVERY_STATUS:
            row.status = delivery_text(row)
        return row

    financial = order.get("displayFinancialStatus") or ""
    if order.get("cancelledAt") or financial in REFUNDED_STATUSES:
        word = tr("Cancelado") if order.get("cancelledAt") else tr("Reembolsado")
        if active:
            return act(
                tr("{0} con guía activa", word),
                tr("{0}, pero su guía de {1} sigue activa: anúlala en Shopify para recuperar el dinero.", word, _money(row.cost)),
                "void",
            )
        return keep("cancelled", word + (tr(" · guía anulada") if row.voided else ""))
    if kind == "pickup":
        return keep("info", tr("Retirado en tienda") if shipped else tr("Retiro en tienda"))
    if kind == "local":
        return keep("info", tr("Entrega local"))
    if kind == "none":
        return keep("info", tr("Sin envío"))

    s = settings
    if active:
        if ledger.get("state") == "no_document" and row.printed != "yes":
            return act(
                tr("Guía sin PDF"),
                tr("Shopify compró la guía pero no entregó el PDF para imprimir: imprímela desde el pedido en Shopify."),
                "open",
            )
        if row.printed == "failed":
            return act(
                tr("Guía sin imprimir"),
                tr("La guía se compró pero no se imprimió: {0}. Revisa la impresora y reimprímela.", row.print_error or tr('error de la impresora')),
                "reprint",
            )
        if row.printed == "pending":
            return keep("waiting", tr("Imprimiendo la guía…"))
        if row.printed == "yes":
            return keep("done", tr("Guía comprada e impresa"))
        return keep("done", tr("Guía comprada en Shopify"))
    if shipped:
        if row.tracking:
            return keep("done", tr("Enviado con seguimiento"))
        if created >= since:
            row.issue = "auto_fulfilled"
            return act(
                tr("Preparado sin guía"),
                tr("Shopify lo marcó como preparado sin guía ni seguimiento (el cliente recibió el aviso de envío sin "
                   "número). Desactiva la preparación automática en Shopify → Configuración → Pago."),
                "open",
            )
        return keep("info", tr("Preparado sin seguimiento"))
    if getattr(s, "required_tag", "").strip() and s.required_tag.strip().lower() not in tags:
        return keep("info", tr("Sin el tag «{0}»: LabelBridge no lo procesa", s.required_tag.strip()))
    if financial not in PAID_STATUSES:
        return keep("waiting", tr("Esperando el pago"))
    state = ledger.get("state")
    # Una compra en curso o sin confirmar va antes que todo: nunca un botón para comprar otra guía mientras tanto.
    if state == "purchasing":
        row.issue = state
        return keep("waiting", tr("Comprando la guía…"))
    if state == "uncertain":
        row.issue = state
        return act(
            tr("Compra sin confirmar"),
            tr("No se sabe si la guía se compró (se cortó la conexión). Revísalo en Shopify antes de comprarla a mano."),
            "open",
        )
    if (rule is not None and not rule.auto) or state == "manual":
        carrier = row.carrier or tr("Esta paquetería")
        why = tr(" por la regla de {0}", rule_text(rule)) if rule is not None and rule_text(rule) else ""
        return act(
            tr("Comprar guía {0} en Shopify", carrier),
            tr("Sale por {0}{1} (piezas: {2}). Shopify no deja comprar esta guía automáticamente: cómprala en el pedido en Shopify (1 clic) y LabelBridge la imprime sola.", carrier, why, _money(row.subtotal)),
            "buy",
        )
    if state == "failed":
        row.problem = friendly_reason(ledger.get("reason") or "") or tr("Shopify no dio el motivo")
        return act(
            tr("No se pudo comprar la guía"),
            tr("No se pudo comprar la guía: {0}. Pulsa «Imprimir guía», corrige los datos (servicio, caja, peso) y vuelve a intentarlo.", row.problem),
            "retry",
        )
    if state == "limit":
        return act(
            tr("Límite diario de guías"),
            tr("Se alcanzó el máximo de {0} guías por día en las compras automáticas. Pulsa «Imprimir guía» para comprarla igual.", getattr(s, 'max_labels_per_day', 0)),
            "retry",
        )
    if not getattr(s, "buy_shipping_label", False):
        return act(
            tr("Falta la guía"),
            tr("La compra automática de guías está desactivada: pulsa «Imprimir guía» o actívala en la pestaña Shopify."),
            "retry",
        )
    if created < since:
        return act(
            tr("Falta la guía"),
            tr("Es de antes de conectar LabelBridge, por eso no se compró sola. Pulsa «Imprimir guía»."),
            "retry",
        )
    if now - created < WAIT_SECONDS:
        row.issue = "auto"  # la compra automática la está comprando: «Imprimir guía» no compra otra
        return keep("waiting", tr("Comprando la guía…"))
    return act(
        tr("Falta la guía"),
        tr("Sigue sin guía: revisa que LabelBridge esté conectado a Shopify y pulsa «Imprimir guía»."),
        "retry",
    )


def belongs(row: OrderRow) -> bool:
    """Lo que está en LabelBridge: pedidos que se envían por paquetería con a quién y adónde mandarlos. Los cancelados
    solo si todavía tienen una guía activa (para anularla y recuperar el dinero)."""
    return row.kind == "ship" and not row.address_problem and row.state != "cancelled"


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
    alerts: int = 0  # envíos con retraso o problema de entrega


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
        if row.issue == "auto_fulfilled":
            summary.auto_fulfill += 1
        if row.alert:
            summary.alerts += 1
            summary.groups["delays"] += 1
        summary.voided += row.voided
        if row.labels:
            summary.labels += row.labels
            summary.cost += row.cost
            carrier = row.carrier or tr("Otra")
            count, cost = summary.by_carrier.get(carrier, (0, 0.0))
            summary.by_carrier[carrier] = (count + row.labels, round(cost + row.cost, 2))
    summary.cost = round(summary.cost, 2)
    summary.voided = round(summary.voided, 2)
    return summary


def in_group(row: OrderRow, group: str) -> bool:
    if group == "all":
        return True
    if group == "action":
        return row.needs_action
    if group == "delays":
        return bool(row.alert)
    return row.group == group


CSV_COLUMNS = (
    N_("Pedido"), N_("Fecha"), N_("Cliente"), N_("Destino"), N_("Método de envío"), N_("Paquetería"), N_("Seguimiento"), N_("Costo de la guía (USD)"),
    N_("Guías anuladas (USD)"), N_("Impresa"), N_("Estado"), N_("Qué hacer"),
)


def csv_rows(rows: list[OrderRow]) -> list[list[str]]:
    printed = {"yes": tr("Sí"), "failed": tr("Falló"), "pending": tr("En cola"), "": "—"}
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
