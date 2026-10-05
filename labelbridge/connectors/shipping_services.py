"""Paqueterías y servicios para comprar una guía con Shopify Shipping («Imprimir guía» → «Servicio»).

Shopify no tiene una consulta de tarifas: la guía se compra con preferredRateSelection {carrierCode, serviceCode} y,
si ese servicio no está disponible para el envío, contesta RATES_NOT_FOUND sin cobrar nada. Sin elegir servicio
(«Automático»), Shopify usa el que pagó el cliente o la paquetería preferida de la tienda.

Shopify no publica los códigos de servicio: estos son los que se vieron funcionar en compras reales. Con el permiso
read_shipping, Shopify dice qué servicios ofrece para cada ruta (availableCarrierServices) y solo se muestran esos.
FedEx no se puede comprar por la API: se compra en el pedido en Shopify y LabelBridge la imprime sola.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..i18n import N_, tr

AUTO = "auto"  # lo que elija Shopify
FEDEX = "fedex"  # se compra en Shopify (1 clic) y LabelBridge la imprime sola

# carrierCode de Shopify → nombre corto (como en Pedidos).
CARRIER_NAMES = {"usps": "USPS", "ups_shipping": "UPS", "dhl_express": "DHL Express", "fedex": "FedEx",
                 "dhl_ecommerce": "DHL eCommerce", "canada_post": "Canada Post"}


@dataclass(frozen=True)
class Service:
    key: str  # «usps:GroundAdvantage»
    carrier: str  # carrierCode
    code: str  # serviceCode
    name: str  # nombre de la paquetería (no se traduce)
    days: str  # tiempo de entrega habitual (se traduce)
    international: bool = False
    shopify_name: str = ""  # como lo nombra Shopify en availableServicesForCountries (normalizado)

    @property
    def label(self) -> str:
        return f"{self.name} · {tr(self.days)}"


SERVICES: tuple[Service, ...] = (
    Service("usps:GroundAdvantage", "usps", "GroundAdvantage", "USPS Ground Advantage", N_("2–5 días hábiles"),
            shopify_name="ground advantage"),
    Service("usps:Priority", "usps", "Priority", "USPS Priority Mail", N_("1–3 días hábiles"),
            shopify_name="priority mail"),
    Service("usps:PriorityExpress", "usps", "PriorityExpress", "USPS Priority Mail Express", N_("1–2 días"),
            shopify_name="priority mail express"),
    Service("ups_shipping:03", "ups_shipping", "03", "UPS Ground", N_("1–5 días hábiles"), shopify_name="ground"),
    Service("ups_shipping:12", "ups_shipping", "12", "UPS 3 Day Select", N_("3 días hábiles"),
            shopify_name="3 day select"),
    Service("ups_shipping:02", "ups_shipping", "02", "UPS 2nd Day Air", N_("2 días hábiles"),
            shopify_name="2nd day air"),
    Service("ups_shipping:13", "ups_shipping", "13", "UPS Next Day Air Saver", N_("Día hábil siguiente, por la tarde"),
            shopify_name="next day air saver"),
    Service("ups_shipping:01", "ups_shipping", "01", "UPS Next Day Air", N_("Día hábil siguiente, por la mañana"),
            shopify_name="next day air"),
    Service("ups_shipping:11", "ups_shipping", "11", "UPS Standard", N_("Canadá y México, por tierra"), True,
            shopify_name="standard"),
    Service("ups_shipping:08", "ups_shipping", "08", "UPS Worldwide Expedited", N_("2–5 días hábiles"), True,
            shopify_name="worldwide expedited"),
    Service("ups_shipping:65", "ups_shipping", "65", "UPS Worldwide Saver", N_("1–3 días hábiles"), True,
            shopify_name="worldwide saver"),
    Service("ups_shipping:07", "ups_shipping", "07", "UPS Worldwide Express", N_("1–3 días hábiles, por la mañana"),
            True, shopify_name="worldwide express"),
    Service("dhl_express:P", "dhl_express", "P", "DHL Express Worldwide", N_("1–4 días hábiles"), True,
            shopify_name="express worldwide"),
)
_BY_KEY = {service.key: service for service in SERVICES}


def by_key(key: str) -> Service | None:
    return _BY_KEY.get(key or "")


def selection(key: str) -> dict[str, str] | None:
    """El servicio como lo pide shippingLabelPurchase (preferredRateSelection); None = que elija Shopify."""
    service = by_key(key)
    return {"carrierCode": service.carrier, "serviceCode": service.code} if service else None


def service_name(key: str) -> str:
    if key == FEDEX:
        return "FedEx"
    service = by_key(key)
    return service.name if service else tr("Automático")


def normalize(name: str) -> str:
    """«UPS® Ground» → «ground», «UPS 2nd Day Air A.M.® (commercial addresses only)» → «2nd day air a.m.»."""
    text = re.sub(r"\(.*?\)", " ", (name or "").lower())
    text = re.sub(r"[®™]", " ", text)
    text = re.sub(r"\b(usps|ups|dhl|fedex)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Territorios de EE. UU.: para USPS son envíos nacionales.
US_TERRITORIES = {"US", "PR", "VI", "GU", "AS", "MP", "UM"}


def is_domestic(origin: str, destination: str) -> bool:
    origin, destination = (origin or "").upper(), (destination or "").upper()
    if not origin or not destination or origin == destination:
        return True
    return origin in US_TERRITORIES and destination in US_TERRITORIES


@dataclass
class Route:
    """Lo que se sabe del envío para elegir el servicio: si es nacional y qué ofrece Shopify (None = no se sabe)."""

    domestic: bool = True
    destination: str = ""  # código del país
    offered: dict[str, set[str]] | None = None  # carrierCode → servicios (normalizados) que Shopify ofrece

    def allows(self, service: Service) -> bool:
        if not self.offered:  # sin read_shipping (o Shopify no dijo nada): los del tipo de envío
            return service.international != self.domestic
        names = self.offered.get(service.carrier)
        if not names:  # la paquetería no llega a ese destino (o la tienda no la tiene)
            return False
        if service.international:  # sus nombres en Shopify no están confirmados: basta con que la paquetería llegue
            return not self.domestic
        return service.shopify_name in names

    @property
    def fedex(self) -> bool:
        """FedEx sale en la lista (para comprarla en Shopify) si la tienda la tiene o no se sabe."""
        return not self.offered or bool(self.offered.get("fedex"))


def choices(route: Route | None) -> list[Service]:
    """Los servicios para elegir en ese envío (además de «Automático»)."""
    route = route or Route()
    return [service for service in SERVICES if route.allows(service)]


def offered_services(data: dict) -> dict[str, set[str]]:
    """availableCarrierServices → {carrierCode: servicios normalizados}."""
    out: dict[str, set[str]] = {}
    for entry in data.get("availableCarrierServices") or []:
        carrier = (entry or {}).get("carrierService") or {}
        code = str(carrier.get("name") or "").strip()
        if not code:
            continue
        names = {normalize(str(s.get("name") or "")) for s in carrier.get("availableServicesForCountries") or []}
        out.setdefault(code, set()).update(name for name in names if name)
    return out


def rejection(reason: str, key: str) -> str:
    """Por qué no se compró, contando el servicio elegido cuando Shopify no lo ofrece para ese envío."""
    from .orders_overview import friendly_reason

    if by_key(key) is not None and "RATES_NOT_FOUND" in (reason or ""):
        return tr("Shopify no ofrece {0} para este envío. No se cobró nada: elige otro servicio o «Automático»",
                  service_name(key))
    return friendly_reason(reason) or tr("sin detalle")
