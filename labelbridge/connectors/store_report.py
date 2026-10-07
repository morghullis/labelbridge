"""Reporte semanal de la tienda: los números de la semana (de los pedidos de Shopify y de lo que LabelBridge sabe del
envío), lo que hay que arreglar, lo que va bien, la comparación con referencias del sector y el plan para la semana.

Aquí solo se calcula: sin pantalla ni red (la consulta la hace ShopifyService.weekly_report). La pantalla «Reporte» y
el asistente muestran lo mismo. Las semanas van de lunes a domingo en la hora de la tienda.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Callable

from ..i18n import N_, decimal, tr
from .benchmarks import BENCHMARKS
from .orders_overview import TEST_TAGS, iso_ts, label_events, subtotal_of

# Una página: 20 pedidos con sus productos, guías y preparaciones (cabe holgado en el costo máximo de Shopify).
REPORT_QUERY = """
query LabelBridgeReport($first: Int!, $after: String, $query: String!) {
  orders(first: $first, after: $after, query: $query, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      legacyResourceId
      createdAt
      cancelledAt
      test
      tags
      sourceName
      currentSubtotalPriceSet { shopMoney { amount currencyCode } }
      totalDiscountsSet { shopMoney { amount } }
      totalShippingPriceSet { shopMoney { amount } }
      totalRefundedSet { shopMoney { amount } }
      customer { numberOfOrders }
      shippingAddress { provinceCode countryCodeV2 }
      lineItems(first: 20) { nodes { title quantity discountedTotalSet { shopMoney { amount } } } }
      fulfillments(first: 3) { createdAt deliveredAt estimatedDeliveryAt }
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
# Sin acceso a los clientes (datos protegidos): todo igual, sin saber quién vuelve a comprar.
REPORT_QUERY_NO_CUSTOMERS = REPORT_QUERY.replace("      customer { numberOfOrders }\n", "")
TIMEZONE_QUERY = "query LabelBridgeShop { shop { ianaTimezone timezoneOffsetMinutes currencyCode } }"

HISTORY_WEEKS = 4  # el promedio con el que se compara la semana
SHIP_TARGET_HOURS = 48  # meta propia: preparado en 2 días
LATE_GRACE_HOURS = 24  # una entrega cuenta como atrasada un día después de la fecha estimada
CHANNELS = {
    "web": N_("Tienda en línea"),
    "shopify_draft_order": N_("Pedido manual"),
    "pos": N_("Punto de venta"),
    "iphone": N_("App de Shopify"),
    "android": N_("App de Shopify"),
}


# --- cada pedido, en números ---------------------------------------------------------------------------------
@dataclass
class OrderFacts:
    created: float
    cancelled: bool
    net: float  # piezas después de descuentos y devoluciones (sin envío ni impuestos)
    discount: float
    refunded: float
    shipping_charged: float
    label_cost: float  # guías activas compradas en Shopify (también las de LabelBridge)
    units: int
    returning: bool | None  # None: no se sabe (sin permiso de clientes o compra como invitado)
    channel: str
    region: str
    products: list[tuple[str, float, int]] = field(default_factory=list)  # (producto, venta, unidades)
    ship_hours: float | None = None  # horas hasta la primera preparación
    late: bool = False


def _money(order: dict, field_name: str) -> float:
    money = (order.get(field_name) or {}).get("shopMoney") or {}
    try:
        return float(money.get("amount") or 0)
    except (TypeError, ValueError):
        return 0.0


def facts_of(order: dict, now: float) -> OrderFacts | None:
    """Los números de un pedido; None para los pedidos de prueba (no cuentan en las ventas)."""
    tags = [str(tag).lower() for tag in order.get("tags") or []]
    if order.get("test") or any(word in tag for tag in tags for word in TEST_TAGS):
        return None
    created = iso_ts(order.get("createdAt"))
    labels = label_events(order)
    products = []
    units = 0
    for item in ((order.get("lineItems") or {}).get("nodes") or []):
        quantity = int(item.get("quantity") or 0)
        units += quantity
        products.append((str(item.get("title") or "").strip() or tr("(sin nombre)"), _money(item, "discountedTotalSet"),
                         quantity))
    if "customer" in order:
        customer = order.get("customer") or {}
        orders_count = customer.get("numberOfOrders")
        returning = (int(orders_count) > 1) if orders_count not in (None, "") else None
    else:
        returning = None
    fulfillments = order.get("fulfillments") or []
    first = min((iso_ts(f.get("createdAt")) for f in fulfillments if f.get("createdAt")), default=0.0)
    address = order.get("shippingAddress") or {}
    country = str(address.get("countryCodeV2") or "")
    region = str(address.get("provinceCode") or "") if country in ("US", "") else country
    source = str(order.get("sourceName") or "")
    return OrderFacts(
        created=created,
        cancelled=bool(order.get("cancelledAt")),
        net=subtotal_of(order),
        discount=_money(order, "totalDiscountsSet"),
        refunded=_money(order, "totalRefundedSet"),
        shipping_charged=_money(order, "totalShippingPriceSet"),
        label_cost=round(sum(e.get("cost", 0.0) for e in labels.values() if not e.get("voided")), 2),
        units=units,
        returning=returning,
        channel=tr(CHANNELS[source]) if source in CHANNELS else (tr("Otra app") if source.isdigit() or not source
                                                                 else source),
        region=region,
        products=products,
        ship_hours=(first - created) / 3600 if first and created else None,
        late=any(_late(f, now) for f in fulfillments),
    )


def _late(fulfillment: dict, now: float) -> bool:
    estimated = iso_ts(fulfillment.get("estimatedDeliveryAt"))
    if not estimated:
        return False
    delivered = iso_ts(fulfillment.get("deliveredAt"))
    limit = estimated + LATE_GRACE_HOURS * 3600
    return delivered > limit if delivered else now > limit


# --- una semana -------------------------------------------------------------------------------------------------
@dataclass
class WeekNumbers:
    orders: int = 0
    sales: float = 0.0
    cancelled: int = 0
    units: int = 0
    aov: float | None = None
    units_per_order: float | None = None
    returning_share: float | None = None  # % de pedidos de clientes que ya habían comprado
    refund_share: float | None = None  # % de lo vendido que se reembolsó
    discount_share: float | None = None  # % de pedidos con descuento
    shipped_on_time: float | None = None  # % preparados en SHIP_TARGET_HOURS (de los que ya debían estarlo)
    median_ship_hours: float | None = None
    shipping_margin: float | None = None  # envío cobrado − costo de guías
    late: int = 0
    daily_sales: list[float] = field(default_factory=lambda: [0.0] * 7)
    daily_orders: list[int] = field(default_factory=lambda: [0] * 7)


def _share(part: float, whole: float) -> float | None:
    return round(part * 100 / whole, 1) if whole else None


def week_numbers(facts: list[OrderFacts], start: datetime, now: float) -> WeekNumbers:
    """Los números de los pedidos creados en la semana que empieza en `start` (lunes 00:00, hora de la tienda)."""
    begin, end = start.timestamp(), (start + timedelta(days=7)).timestamp()
    week = [f for f in facts if begin <= f.created < end]
    valid = [f for f in week if not f.cancelled]
    numbers = WeekNumbers(orders=len(valid), cancelled=len(week) - len(valid))
    numbers.sales = round(sum(f.net for f in valid), 2)
    numbers.units = sum(f.units for f in valid)
    for f in valid:
        day = (datetime.fromtimestamp(f.created, start.tzinfo) - start).days
        if 0 <= day < 7:
            numbers.daily_sales[day] = round(numbers.daily_sales[day] + f.net, 2)
            numbers.daily_orders[day] += 1
    if not valid:
        return numbers
    numbers.aov = round(numbers.sales / numbers.orders, 2)
    numbers.units_per_order = round(numbers.units / numbers.orders, 2)
    known = [f for f in valid if f.returning is not None]
    numbers.returning_share = _share(sum(1 for f in known if f.returning), len(known))
    refunded = sum(f.refunded for f in valid)
    numbers.refund_share = _share(refunded, numbers.sales + refunded)
    numbers.discount_share = _share(sum(1 for f in valid if f.discount > 0), len(valid))
    due = [f for f in valid if f.ship_hours is not None or now - f.created >= SHIP_TARGET_HOURS * 3600]
    on_time = [f for f in due if f.ship_hours is not None and f.ship_hours <= SHIP_TARGET_HOURS]
    numbers.shipped_on_time = _share(len(on_time), len(due))
    hours = [f.ship_hours for f in valid if f.ship_hours is not None]
    numbers.median_ship_hours = round(statistics.median(hours), 1) if hours else None
    if any(f.label_cost or f.shipping_charged for f in valid):
        numbers.shipping_margin = round(sum(f.shipping_charged - f.label_cost for f in valid), 2)
    numbers.late = sum(1 for f in valid if f.late)
    return numbers


# --- el reporte ---------------------------------------------------------------------------------------------------
@dataclass
class Metric:
    key: str
    title: str
    unit: str  # money | count | percent | hours | number
    value: float | None
    previous: float | None
    average: float | None  # de las HISTORY_WEEKS semanas anteriores
    better: str = "up"  # up | down: qué dirección es buena

    @property
    def change(self) -> float | None:
        """Cambio contra la semana anterior: % para montos y cantidades, puntos para los porcentajes."""
        if self.value is None or self.previous is None:
            return None
        if self.unit == "percent":
            return round(self.value - self.previous, 1)
        return round((self.value - self.previous) * 100 / self.previous, 1) if self.previous else None


@dataclass
class Finding:
    key: str
    severity: str  # critical | watch | strength
    title: str
    evidence: str
    actions: list[str] = field(default_factory=list)
    weight: float = 0.0  # para ordenar: lo de más impacto, primero


@dataclass
class Comparison:
    key: str
    title: str
    store: float | None
    reference: float
    unit: str
    source: str
    url: str
    checked: str
    detail: str
    better: str = "up"
    note: str = ""  # p. ej. por qué no hay dato de la tienda

    @property
    def verdict(self) -> str:
        """above | below | near (±10 % de la referencia) | unknown."""
        if self.store is None or not self.reference:
            return "unknown"
        ratio = self.store / self.reference
        if 0.9 <= ratio <= 1.1:
            return "near"
        return "above" if ratio > 1 else "below"


@dataclass
class WeeklyReport:
    start: str  # lunes, AAAA-MM-DD (hora de la tienda)
    end: str  # domingo
    complete: bool  # la semana ya terminó
    currency: str
    generated_at: float
    metrics: list[Metric]
    daily_sales: list[float]
    daily_orders: list[int]
    top_products: list[tuple[str, float, int, float]]  # (producto, venta, unidades, % de la venta)
    channels: list[tuple[str, int]]
    regions: list[tuple[str, int]]
    findings: list[Finding]
    strengths: list[Finding]
    comparisons: list[Comparison]
    plan: list[str]
    missing: list[str] = field(default_factory=list)  # datos que faltaron y por qué

    def metric(self, key: str) -> Metric | None:
        return next((m for m in self.metrics if m.key == key), None)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for item, metric in zip(data["metrics"], self.metrics):
            item["change"] = metric.change
        for item, comparison in zip(data["comparisons"], self.comparisons):
            item["verdict"] = comparison.verdict
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WeeklyReport":
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        known["metrics"] = [Metric(**{k: v for k, v in m.items() if k != "change"}) for m in data.get("metrics", [])]
        known["findings"] = [Finding(**f) for f in data.get("findings", [])]
        known["strengths"] = [Finding(**f) for f in data.get("strengths", [])]
        known["comparisons"] = [Comparison(**{k: v for k, v in c.items() if k != "verdict"})
                                for c in data.get("comparisons", [])]
        for key in ("top_products", "channels", "regions"):
            known[key] = [tuple(item) for item in data.get(key, [])]
        return cls(**known)


def store_timezone(shop: dict) -> tzinfo:
    """La zona de la tienda; si el sistema no la conoce (Windows sin tzdata), la diferencia horaria de hoy."""
    name = str(shop.get("ianaTimezone") or "")
    if name:
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(name)
        except (ImportError, ValueError, KeyError, OSError):  # ZoneInfoNotFoundError es un KeyError
            pass
    minutes = int(shop.get("timezoneOffsetMinutes") or 0)
    return timezone(timedelta(minutes=minutes))


def week_start(now: float, tz: tzinfo, weeks_back: int = 1) -> datetime:
    """El lunes 00:00 (hora de la tienda) de la semana de hace `weeks_back` semanas (0: esta semana)."""
    today = datetime.fromtimestamp(now, tz).replace(hour=0, minute=0, second=0, microsecond=0)
    monday = today - timedelta(days=today.weekday())
    return monday - timedelta(weeks=weeks_back)


def build_report(orders: list[dict], now: float, tz: tzinfo, currency: str = "USD", weeks_back: int = 1,
                 missing: list[str] | None = None) -> WeeklyReport:
    facts = [f for f in (facts_of(order, now) for order in orders) if f is not None]
    start = week_start(now, tz, weeks_back)
    current = week_numbers(facts, start, now)
    previous = week_numbers(facts, start - timedelta(weeks=1), now)
    history = [week_numbers(facts, start - timedelta(weeks=back), now) for back in range(1, HISTORY_WEEKS + 1)]
    metrics = _metrics(current, previous, history)
    week = [f for f in facts if start.timestamp() <= f.created < (start + timedelta(days=7)).timestamp()
            and not f.cancelled]
    findings, strengths = _findings(current, previous, history, week, metrics)
    report = WeeklyReport(
        start=start.date().isoformat(),
        end=(start + timedelta(days=6)).date().isoformat(),
        complete=now >= (start + timedelta(days=7)).timestamp(),
        currency=currency,
        generated_at=now,
        metrics=metrics,
        daily_sales=current.daily_sales,
        daily_orders=current.daily_orders,
        top_products=_top_products(week),
        channels=Counter(f.channel for f in week).most_common(4),
        regions=Counter(f.region for f in week if f.region).most_common(5),
        findings=findings,
        strengths=strengths,
        comparisons=_comparisons(current, missing or []),
        plan=_plan(findings),
        missing=list(missing or []),
    )
    return report


def _average(history: list[WeekNumbers], pick: Callable[[WeekNumbers], float | None]) -> float | None:
    values = [value for value in (pick(week) for week in history) if value is not None]
    return round(sum(values) / len(values), 2) if values else None


def _metrics(current: WeekNumbers, previous: WeekNumbers, history: list[WeekNumbers]) -> list[Metric]:
    specs: list[tuple[str, str, str, str, Callable[[WeekNumbers], float | None]]] = [
        ("sales", N_("Ventas"), "money", "up", lambda w: w.sales),
        ("orders", N_("Pedidos"), "count", "up", lambda w: w.orders),
        ("aov", N_("Ticket promedio"), "money", "up", lambda w: w.aov),
        ("returning", N_("Clientes que vuelven"), "percent", "up", lambda w: w.returning_share),
        ("on_time", N_("Preparados en 2 días"), "percent", "up", lambda w: w.shipped_on_time),
        ("refunds", N_("Reembolsado"), "percent", "down", lambda w: w.refund_share),
        ("discounts", N_("Pedidos con descuento"), "percent", "down", lambda w: w.discount_share),
        ("ship_margin", N_("Envío cobrado − guías"), "money", "up", lambda w: w.shipping_margin),
        ("late", N_("Entregas atrasadas"), "count", "down", lambda w: w.late),
    ]
    return [Metric(key, title, unit, pick(current), pick(previous), _average(history, pick), better)
            for key, title, unit, better, pick in specs]


def _top_products(week: list[OrderFacts]) -> list[tuple[str, float, int, float]]:
    revenue: dict[str, float] = defaultdict(float)
    units: Counter[str] = Counter()
    for f in week:
        for title, amount, quantity in f.products:
            revenue[title] += amount
            units[title] += quantity
    total = sum(revenue.values())
    ranked = sorted(revenue.items(), key=lambda item: item[1], reverse=True)[:5]
    return [(title, round(amount, 2), units[title], round(amount * 100 / total, 1) if total else 0.0)
            for title, amount in ranked]


# --- qué arreglar y qué va bien -----------------------------------------------------------------------------------
def _money_text(value: float) -> str:
    return f"US${value:,.2f}"


def pct(value: float | None, digits: int = 1) -> str:
    """Un porcentaje como se escribe en el idioma de la app («35,2 %», «40 %»), hasta `digits` decimales."""
    if value is None:
        return "—"
    text = decimal(value, digits)
    if digits:
        text = text.rstrip("0").rstrip(",.")
    return tr("{0} %", text)


def _findings(current: WeekNumbers, previous: WeekNumbers, history: list[WeekNumbers], week: list[OrderFacts],
              metrics: list[Metric]) -> tuple[list[Finding], list[Finding]]:
    findings: list[Finding] = []
    strengths: list[Finding] = []
    by_key = {m.key: m for m in metrics}

    sales = by_key["sales"]
    base = sales.average or 0
    if current.orders == 0 and (previous.orders or base):
        findings.append(Finding("no_sales", "critical", tr("No hubo ventas esta semana"), tr(
            "La semana anterior hubo {0} pedido(s).", previous.orders), [
            tr("Revisa que la tienda esté abierta y que el pago funcione (haz un pedido de prueba)."),
            tr("Mira si se cayó alguna campaña o anuncio que traía visitas."),
        ], 100))
    elif base and current.sales < base * 0.8 and history_orders(history) >= 3:
        drop = round((1 - current.sales / base) * 100)
        findings.append(Finding("sales_down", "critical", tr("Las ventas bajaron {0}", pct(drop)), tr(
            "{0} esta semana contra {1} de promedio en las 4 anteriores.", _money_text(current.sales),
            _money_text(base)), [
            tr("Compara qué productos vendieron menos (abajo, «Productos») y si les falta stock o fotos."),
            tr("Vuelve a contactar a quienes ya compraron: un correo con novedades suele traer ventas en días."),
            tr("Si bajó por temporada, planea una promoción corta en vez de descuentos permanentes."),
        ], 80 + drop / 10))
    elif base and current.sales > base * 1.2:
        rise = round((current.sales / base - 1) * 100)
        strengths.append(Finding("sales_up", "strength", tr("Las ventas subieron {0}", pct(rise)), tr(
            "{0} esta semana contra {1} de promedio.", _money_text(current.sales), _money_text(base)), [
            tr("Anota qué cambió (publicación, anuncio, producto) para repetirlo."),
        ], rise))

    if current.shipped_on_time is not None:
        if current.shipped_on_time < 80:
            findings.append(Finding("slow_shipping", "critical", tr("Pedidos que tardan en salir"), tr(
                "Solo el {0} se preparó en 2 días (mediana: {1} h).", pct(current.shipped_on_time),
                current.median_ship_hours if current.median_ship_hours is not None else "—"), [
                tr("Deja la compra de guías automática para los pedidos pagados (Shopify en LabelBridge)."),
                tr("Revisa a diario la pestaña Pedidos → «Necesitan acción»."),
                tr("Promete en la tienda un plazo de preparación que sí puedas cumplir."),
            ], 70 + (80 - current.shipped_on_time) / 2))
        elif current.shipped_on_time >= 95 and current.orders >= 3:
            strengths.append(Finding("fast_shipping", "strength", tr("Envíos rápidos"), tr(
                "El {0} de los pedidos se preparó en 2 días.", pct(current.shipped_on_time)), [
                tr("Dilo en la tienda («enviamos en 48 h»): la rapidez también vende."),
            ], 40))

    if current.shipping_margin is not None and current.shipping_margin < 0:
        loss = -current.shipping_margin
        findings.append(Finding("shipping_loss", "critical", tr("Pagas más en guías de lo que cobras de envío"), tr(
            "Esta semana las guías costaron {0} más de lo que los clientes pagaron de envío.", _money_text(loss)), [
            tr("Revisa las tarifas de envío en Shopify (Configuración → Envío y entrega)."),
            tr("Usa el tamaño de caja y el peso reales: las guías se cobran por eso."),
            tr("Si ofreces envío gratis, súmalo al precio o ponle un mínimo de compra."),
        ], 60 + min(loss, 200) / 10))
    elif current.shipping_margin is not None and current.shipping_margin >= 0 and current.orders >= 3:
        strengths.append(Finding("shipping_covered", "strength", tr("El envío se paga solo"), tr(
            "Lo cobrado de envío cubrió las guías (quedan {0}).", _money_text(current.shipping_margin)), [], 20))

    if current.refund_share is not None and current.refund_share > 10:
        findings.append(Finding("refunds", "critical", tr("Muchos reembolsos"), tr(
            "Se reembolsó el {0} de lo vendido.", pct(current.refund_share)), [
            tr("Lee el motivo de cada reembolso en Shopify y busca lo que se repite (talla, foto, demora)."),
            tr("Mejora la descripción y las fotos del producto que más se devuelve."),
        ], 55 + current.refund_share / 2))
    elif current.refund_share is not None and current.refund_share < 5 and current.orders >= 3:
        strengths.append(Finding("few_refunds", "strength", tr("Casi sin reembolsos"), tr(
            "Solo el {0} de lo vendido se reembolsó.", pct(current.refund_share)), [], 25))

    if current.returning_share is not None and current.orders >= 3:
        if current.returning_share < 15:
            findings.append(Finding("few_returning", "watch", tr("Pocos clientes vuelven"), tr(
                "El {0} de los pedidos fue de clientes que ya habían comprado.", pct(current.returning_share)), [
                tr("Manda un correo a los clientes del último mes con una novedad o un regalo pequeño."),
                tr("Incluye en el paquete una tarjeta con un código para la próxima compra."),
            ], 40))
        elif current.returning_share >= 25:
            strengths.append(Finding("loyal", "strength", tr("Clientes fieles"), tr(
                "El {0} de los pedidos fue de clientes que ya habían comprado.", pct(current.returning_share)), [
                tr("Cuídalos: avísales primero de lo nuevo."),
            ], 35))

    if current.late:
        findings.append(Finding("late", "watch", tr("Entregas atrasadas"), tr(
            "{0} pedido(s) de la semana llegaron (o van) más de un día tarde.", current.late), [
            tr("Escríbeles antes de que pregunten, con el seguimiento (Pedidos → Retrasos)."),
            tr("Si se repite con una paquetería, cambia la regla en Pedidos → Reglas de paquetería."),
        ], 30 + current.late * 3))

    if current.discount_share is not None and current.discount_share > 40 and current.orders >= 3:
        findings.append(Finding("discounts", "watch", tr("Muchas ventas con descuento"), tr(
            "El {0} de los pedidos usó un descuento.", pct(current.discount_share)), [
            tr("Usa descuentos con fecha de fin y para productos concretos, no para toda la tienda."),
        ], 25))

    revenue = sum(sum(amount for _t, amount, _q in f.products) for f in week)
    if revenue and len(week) >= 3:
        best = max(((title, sum(a for f in week for t, a, _q in f.products if t == title))
                    for f in week for title, _a, _q in f.products), key=lambda item: item[1])
        share = round(best[1] * 100 / revenue)
        if share > 60:
            findings.append(Finding("concentration", "watch", tr("Casi todo depende de un producto"), tr(
                "«{0}» fue el {1} de lo vendido.", best[0], pct(share)), [
                tr("Asegura su stock y prepara 1 o 2 productos parecidos para no depender solo de él."),
            ], 20))

    if current.cancelled and current.cancelled * 100 / max(1, current.orders + current.cancelled) > 10:
        findings.append(Finding("cancellations", "watch", tr("Pedidos cancelados"), tr(
            "{0} pedido(s) cancelado(s) esta semana.", current.cancelled), [
            tr("Revisa en Shopify por qué se cancelaron (pago, stock, fraude)."),
        ], 20))

    findings.sort(key=lambda f: (f.severity != "critical", -f.weight))
    strengths.sort(key=lambda f: -f.weight)
    return findings, strengths


def history_orders(history: list[WeekNumbers]) -> int:
    return sum(week.orders for week in history)


def _comparisons(current: WeekNumbers, missing: list[str]) -> list[Comparison]:
    def make(key: str, store: float | None, better: str = "up", note: str = "") -> Comparison:
        b = BENCHMARKS[key]
        return Comparison(b.key, tr(b.title), store, b.value, b.unit, b.source, b.url, b.checked, tr(b.detail), better,
                          note)

    no_permission = tr("Para compararla hace falta el permiso de informes de Shopify (read_reports).")
    return [
        make("aov_jewelry", current.aov),
        make("aov_global", current.aov),
        make("repeat", current.returning_share,
             note="" if current.returning_share is not None else tr("Sin el permiso de clientes no se sabe quién vuelve.")),
        make("returns", current.refund_share, better="down"),
        make("conversion_jewelry", None, note=no_permission),
        make("cart_abandonment", None, better="down", note=no_permission),
    ]


def _plan(findings: list[Finding]) -> list[str]:
    """Las 3 acciones de más impacto (la primera de cada problema, en orden)."""
    plan = [finding.actions[0] for finding in findings if finding.actions][:3]
    if not plan:
        plan = [
            tr("Mantén lo que funciona: revisa Pedidos cada mañana y que la compra de guías siga automática."),
            tr("Prueba una mejora pequeña (una foto nueva, una descripción más clara) en tu producto más vendido."),
        ]
    return plan
