"""«Imprimir guía» en Pedidos, con uno o con varios pedidos elegidos.

Cada pedido se reimprime (ya tiene guía), se compra e imprime con lo elegido (servicio, caja y aviso al cliente), se
compra en Shopify (FedEx: LabelBridge la imprime sola al verla) o se omite con el porqué (retiro en tienda, cancelado,
ya enviado, la compra automática lo está comprando…). Con varios, todo va con una sola confirmación, de uno en uno y
con un tope por tanda. Cada compra toma el candado del ciclo automático: nunca se compra el mismo pedido dos veces.
Aquí, sin pantalla, para poder probarlo.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable

from .. import net
from ..i18n import N_, tr
from .orders_overview import OrderRow
from .shipping_services import AUTO, rejection

log = logging.getLogger(__name__)

MAX_BATCH = 25  # compras por tanda: cada guía cuesta dinero

REPRINT, BUY, IN_SHOPIFY, SKIP = "reprint", "buy", "shopify", "skip"
PLAN_TEXT = {REPRINT: N_("Reimprimir"), BUY: N_("Comprar e imprimir"), IN_SHOPIFY: N_("En Shopify"),
             SKIP: N_("Omitir")}


def plan_for(row: OrderRow, *, batch: bool = False) -> tuple[str, str]:
    """Qué hace «Imprimir guía» con un pedido: (REPRINT | BUY | IN_SHOPIFY | SKIP, el porqué)."""
    if row.action == "void":  # cancelado o reembolsado con la guía activa: no se usa, se anula
        return SKIP, tr("Está cancelado o reembolsado y su guía sigue activa: anúlala en Shopify para recuperar el "
                        "dinero.")
    if row.state == "cancelled":
        return SKIP, tr("Está cancelado o reembolsado: no lleva guía.")
    if row.labels:
        return REPRINT, ""
    if row.kind == "pickup":
        return SKIP, tr("Es para retiro en tienda: no lleva guía.")
    if row.kind == "local":
        return SKIP, tr("Es de entrega local: no lleva guía de paquetería.")
    if row.kind == "none":
        return SKIP, tr("No lleva envío.")
    if row.address_problem:  # nunca una guía sin sentido: sin a quién o adónde mandarlo
        return SKIP, tr("No puede llevar guía: {0}.", row.address_problem)
    if row.shipped:
        if row.tracking:
            return SKIP, tr("Ya se envió con seguimiento {0}.", row.tracking[0][1])
        return SKIP, tr("Shopify ya lo marcó como enviado.")
    if row.issue in ("purchasing", "uncertain"):
        return SKIP, tr("Tiene una compra en curso o sin confirmar: revísala en Shopify antes de comprar otra.")
    if row.issue == "auto":
        return SKIP, tr("La compra automática la está comprando en este momento.")
    if row.issue == "flow":  # FedEx: Shopify Flow la compra sola; comprar otra sería pagar dos
        return SKIP, tr("Shopify Flow la está comprando en este momento.")
    if row.action == "buy":  # FedEx (o una regla «en Shopify»): no se puede comprar por la API
        carrier = row.carrier or row.rule_carrier or "FedEx"
        return IN_SHOPIFY, tr("{0} se compra en Shopify (1 clic) y LabelBridge la imprime sola.", carrier)
    if batch and not row.paid:
        return SKIP, tr("Todavía no está pagado.")
    return BUY, ""


@dataclass
class BatchItem:
    row: OrderRow
    plan: str
    why: str = ""
    state: str = "waiting"  # waiting | working | done | failed | skipped
    detail: str = ""  # el seguimiento, o qué pasó
    cost: float = 0.0


@dataclass
class BatchOptions:
    service: str = AUTO
    package: dict | None = None  # None: el paquete predeterminado de Shopify
    notify: bool = False


@dataclass
class BatchSummary:
    bought: int = 0
    reprinted: int = 0
    failed: int = 0
    skipped: int = 0
    cost: float = 0.0
    stopped: bool = False
    problems: list[str] = field(default_factory=list)


def make_batch(rows: list[OrderRow]) -> list[BatchItem]:
    return [BatchItem(row, *plan_for(row, batch=True)) for row in rows]


def purchases(items: list[BatchItem]) -> int:
    return sum(1 for item in items if item.plan == BUY)


def run_batch(
    shopify, items: list[BatchItem], options: BatchOptions, *,
    progress: Callable[[int, BatchItem], None] | None = None, stop: threading.Event | None = None,
) -> BatchSummary:
    """Hace cada uno en orden. Una falla no frena a los demás: cada pedido es independiente y una compra sin
    confirmar nunca se repite sola. Si la compra automática se adelantó con uno, ese queda omitido (no fallido)."""
    from .shopify import ShopifyError

    summary = BatchSummary()
    report = progress or (lambda _index, _item: None)
    if purchases(items) > MAX_BATCH:
        raise ShopifyError(tr("Elige hasta {0} pedidos para comprar a la vez.", MAX_BATCH))
    for index, item in enumerate(items):
        if stop is not None and stop.is_set():
            summary.stopped = True
            break
        if item.plan not in (REPRINT, BUY):
            if item.plan == IN_SHOPIFY:  # que LabelBridge la imprima sola cuando se compre en Shopify
                try:
                    shopify.buy_in_shopify(item.row.legacy, item.row.name, item.row.carrier or item.row.rule_carrier)
                except Exception as exc:  # ya tiene una compra o no se pudo marcar: solo se informa
                    log.info("Imprimir guías: %s no quedó esperando su guía de Shopify: %s", item.row.name, exc)
            item.state, item.detail = "skipped", item.why
            summary.skipped += 1
            report(index, item)
            continue
        item.state = "working"
        report(index, item)
        try:
            if item.plan == REPRINT:
                shopify.reprint_label(item.row.legacy, item.row.name)
                item.state, item.detail = "done", tr("Otra vez en la cola de impresión")
                summary.reprinted += 1
            else:
                _buy(shopify, item, options, summary)
        except (ShopifyError, net.NetworkError, net.HttpError) as exc:
            ahead = _bought_meanwhile(shopify, item.row.legacy) if item.plan == BUY else ""
            if ahead:  # la compra automática se adelantó: no es un error
                item.state, item.detail = "skipped", ahead
                summary.skipped += 1
            else:
                item.state, item.detail = "failed", str(exc)
                summary.failed += 1
        except Exception as exc:  # cualquier otro fallo: se informa y sigue con el siguiente pedido
            log.exception("Imprimir guías: fallo inesperado con %s", item.row.name)
            item.state, item.detail = "failed", str(exc)
            summary.failed += 1
        if item.state == "failed":
            summary.problems.append(f"{item.row.name}: {item.detail}")
        report(index, item)
    summary.cost = round(sum(item.cost for item in items), 2)
    return summary


def _bought_meanwhile(shopify, legacy: str) -> str:
    """Si la compra automática compró (o está comprando) ese pedido mientras la tanda avanzaba."""
    ledger = getattr(shopify, "ledger", None)
    state = (ledger(legacy) or {}).get("state") if callable(ledger) else None
    if state == "purchased":
        return tr("La compra automática la compró mientras tanto.")
    if state == "purchasing":
        return tr("La compra automática la está comprando.")
    return ""


def _buy(shopify, item: BatchItem, options: BatchOptions, summary: BatchSummary) -> None:
    result = shopify.buy_label_now(item.row.legacy, package=options.package, total_weight=None,
                                   notify=options.notify, service=options.service)
    state = result.get("state")
    if state == "purchased":
        item.state = "done"
        item.detail = " ".join(part for part in (result.get("carrier"), result.get("tracking")) if part) or tr(
            "Guía comprada")
        item.cost = float(result.get("cost") or 0)
        summary.bought += 1
    elif state == "failed":
        item.state, item.detail = "failed", rejection(result.get("reason") or "", options.service)
        summary.failed += 1
    elif state == "uncertain":
        item.state = "failed"
        item.detail = tr("No se pudo confirmar si Shopify la compró: revisa el pedido en Shopify.")
        summary.failed += 1
    else:  # Shopify sigue procesándola: se imprime sola al terminar
        item.state, item.detail = "done", tr("Shopify sigue procesándola: se imprime sola al terminar")
        summary.bought += 1
