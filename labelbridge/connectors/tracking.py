"""«Rastrear» dentro de LabelBridge: lo que Shopify sabe de cada envío del pedido (estado, fecha estimada y los
movimientos que informa la paquetería), sin ir a la web de la paquetería.

Shopify guarda los movimientos de las guías que compró (FulfillmentEvent: «Shipping Label Created, USPS Awaiting
Item», «In transit»…) con su fecha y lugar. Aquí se leen y se ordenan; la ventana está en ui/tracking_dialog.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..i18n import N_, tr
from .orders_overview import carrier_name, iso_ts

TRACKING_QUERY = """
query LabelBridgeTracking($id: ID!) {
  order(id: $id) {
    name
    shippingAddress { city provinceCode countryCodeV2 }
    fulfillments(first: 10) {
      status createdAt displayStatus inTransitAt deliveredAt estimatedDeliveryAt
      trackingInfo { company number url }
      events(first: 50, sortKey: HAPPENED_AT, reverse: true) {
        nodes { happenedAt status message city province country estimatedDeliveryAt }
      }
    }
  }
}
"""

# Lo que informa la paquetería (estados del envío y de cada movimiento), en palabras de cualquiera.
STATUS_TEXT = {
    "LABEL_PURCHASED": N_("Guía comprada"),
    "LABEL_PRINTED": N_("Guía impresa"),
    "CONFIRMED": N_("Guía creada: la paquetería espera el paquete"),
    "SUBMITTED": N_("Avisado a la paquetería"),
    "FULFILLED": N_("Preparado"),
    "MARKED_AS_FULFILLED": N_("Marcado como preparado"),
    "CARRIER_PICKED_UP": N_("Recogido por la paquetería"),
    "IN_TRANSIT": N_("En camino"),
    "OUT_FOR_DELIVERY": N_("Sale para entrega hoy"),
    "ATTEMPTED_DELIVERY": N_("Intento de entrega fallido"),
    "DELAYED": N_("Envío retrasado"),
    "READY_FOR_PICKUP": N_("Listo para recoger"),
    "PICKED_UP": N_("Recogido por el cliente"),
    "DELIVERED": N_("Entregado"),
    "FAILURE": N_("Problema con el envío"),
    "NOT_DELIVERED": N_("No se pudo entregar"),
    "LABEL_VOIDED": N_("Guía anulada"),
    "CANCELED": N_("Cancelado"),
}
# Los pasos de un envío y en cuál está cada estado.
STEPS = (N_("Guía creada"), N_("Recogido"), N_("En camino"), N_("En reparto"), N_("Entregado"))
STEP_OF = {
    "LABEL_PURCHASED": 0, "LABEL_PRINTED": 0, "CONFIRMED": 0, "SUBMITTED": 0, "FULFILLED": 0, "MARKED_AS_FULFILLED": 0,
    "CARRIER_PICKED_UP": 1, "IN_TRANSIT": 2, "DELAYED": 2, "OUT_FOR_DELIVERY": 3, "ATTEMPTED_DELIVERY": 3,
    "READY_FOR_PICKUP": 3, "NOT_DELIVERED": 3, "FAILURE": 2, "DELIVERED": 4, "PICKED_UP": 4,
}
PROBLEMS = {"ATTEMPTED_DELIVERY", "DELAYED", "FAILURE", "NOT_DELIVERED"}
CLOSED = {"LABEL_VOIDED", "CANCELED"}


def status_text(code: str) -> str:
    return tr(STATUS_TEXT[code]) if code in STATUS_TEXT else (code or "").replace("_", " ").capitalize()


def _date(value: str | None) -> float:
    """Fecha de Shopify → marca de tiempo; solo la fecha (medianoche UTC): ese día, no la noche anterior en América."""
    stamp = iso_ts(value)
    if stamp and str(value).endswith(("T00:00:00Z", "T00:00:00+00:00")):
        stamp += 12 * 3600
    return stamp


def _place(node: dict) -> str:
    city = str(node.get("city") or "").strip()
    if city.isupper():
        city = city.title()  # «MIAMI» → «Miami»
    country = str(node.get("country") or "").strip()
    parts = [city, str(node.get("province") or "").strip()]
    if country and country.upper() not in ("US", "UNITED STATES"):
        parts.append(country)
    return ", ".join(part for part in parts if part)


@dataclass
class TrackingEvent:
    at: float
    status: str
    message: str = ""  # lo que escribió la paquetería (en su idioma)
    place: str = ""

    @property
    def title(self) -> str:
        return status_text(self.status)


@dataclass
class Shipment:
    carrier: str
    number: str
    url: str = ""
    status: str = ""  # displayStatus del envío
    created: float = 0.0
    in_transit: float = 0.0
    delivered: float = 0.0
    eta: float = 0.0
    events: list[TrackingEvent] = field(default_factory=list)  # el más reciente primero
    more: list[str] = field(default_factory=list)  # otros números del mismo envío (varios paquetes)

    @property
    def current(self) -> str:
        """El estado más al día: el del envío o, si dice poco, el del último movimiento."""
        if (self.status in STEP_OF and STEP_OF[self.status] > 0) or self.status in CLOSED | PROBLEMS:
            return self.status
        return self.events[0].status if self.events else self.status

    @property
    def step(self) -> int:
        return STEP_OF.get(self.current, 0)

    @property
    def problem(self) -> bool:
        return self.current in PROBLEMS

    @property
    def closed(self) -> bool:
        return self.status in CLOSED

    @property
    def headline(self) -> str:
        """«En camino · llega el 10/10», «Entregado el 08/10», «Guía anulada»…"""
        from ..i18n import date_text

        text = status_text(self.current)
        if self.closed:
            return text
        if self.delivered:
            return tr("{0} el {1}", status_text("DELIVERED"), date_text(self.delivered, year=False, clock=""))
        if self.eta:
            return tr("{0} · llega el {1}", text, date_text(self.eta, year=False, clock=""))
        return text


@dataclass
class Tracking:
    order: str
    destination: str = ""
    shipments: list[Shipment] = field(default_factory=list)


def parse_tracking(order: dict) -> Tracking:
    address = order.get("shippingAddress") or {}
    destination = ", ".join(str(part) for part in (address.get("city"), address.get("provinceCode")) if part)
    if address.get("countryCodeV2") and address["countryCodeV2"] != "US":
        destination = ", ".join(part for part in (destination, address["countryCodeV2"]) if part)
    shipments = []
    for fulfillment in order.get("fulfillments") or []:
        numbers = [info for info in fulfillment.get("trackingInfo") or [] if info.get("number")]
        if not numbers:
            continue
        first = numbers[0]
        events = [
            TrackingEvent(iso_ts(node.get("happenedAt")), str(node.get("status") or ""),
                          str(node.get("message") or "").strip(), _place(node))
            for node in ((fulfillment.get("events") or {}).get("nodes") or [])
        ]
        events.sort(key=lambda event: event.at, reverse=True)
        eta = _date(fulfillment.get("estimatedDeliveryAt")) or next(
            (_date(node.get("estimatedDeliveryAt")) for node in ((fulfillment.get("events") or {}).get("nodes") or [])
             if node.get("estimatedDeliveryAt")), 0.0)
        shipments.append(Shipment(
            carrier=carrier_name(str(first.get("company") or "")) or tr("Paquetería"),
            number=str(first["number"]),
            url=str(first.get("url") or ""),
            status=str(fulfillment.get("displayStatus") or ""),
            created=iso_ts(fulfillment.get("createdAt")),
            in_transit=iso_ts(fulfillment.get("inTransitAt")),
            delivered=iso_ts(fulfillment.get("deliveredAt")),
            eta=eta,
            events=events,
            more=[str(info["number"]) for info in numbers[1:]],
        ))
    # Primero los envíos activos (el más nuevo arriba); las guías anuladas al final.
    shipments.sort(key=lambda s: (s.closed, -s.created))
    return Tracking(order=str(order.get("name") or ""), destination=destination, shipments=shipments)
