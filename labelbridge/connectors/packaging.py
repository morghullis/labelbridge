"""Empaques de las guías y el servicio que va con cada uno («Empaques» y «Reglas de paquetería»).

Una guía vale para el empaque y el servicio con que se compró. Shopify Shipping la compra con un empaque propio
(customPackage: tipo, medidas de fuera y peso vacío: un sobre blanco, una bolsa de nailon, una caja) o con un empaque
oficial de la paquetería (carrierPackage). Sin empaque, Shopify usa su paquete predeterminado.

USPS: sus empaques oficiales de Priority Mail (los Flat Rate y los de Priority sin tarifa plana) solo valen con una guía
de Priority Mail o Priority Mail Express; con Ground Advantage, USPS cobra la diferencia o devuelve el paquete. Los Flat
Rate además se pagan a tarifa plana, así que se compran como empaque oficial (carrierPackage). Shopify no publica sus
códigos ni tiene una consulta que los liste: se prueban los conocidos en orden (si Shopify rechaza uno no compra ni cobra
nada) y el que funciona queda anotado para las siguientes (ShopifyService._purchase).

FedEx: Shopify no deja que las apps la compren. Sus empaques oficiales (sobre, Pak, cajas) solo valen con FedEx Express
(2Day, Overnight, Express Saver), nunca con FedEx Ground ni Home Delivery: la ventana «Guía FedEx» lo recuerda.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from ..config import LEGACY_BOX_ID, ShippingPackage
from ..i18n import N_, tr
from .orders_overview import rule_for
from .shipping_services import AUTO, by_key, service_name

SHOPIFY = ""  # el paquete predeterminado de Shopify
RULES = "rules"  # (compras desde la app) el empaque y el servicio que dicen las reglas para ese pedido
PRIORITY, EXPRESS, GROUND = "usps:Priority", "usps:PriorityExpress", "usps:GroundAdvantage"

DIM_UNITS = {"INCHES": "in", "CENTIMETERS": "cm"}
WEIGHT_UNITS = {"OUNCES": "oz", "POUNDS": "lb", "GRAMS": "g", "KILOGRAMS": "kg"}

# Códigos con que Shopify puede rechazar un código de empaque que no conoce (sin tarifas para ese empaque, empaque o
# medidas que no reconoce): solo con estos se prueba el siguiente código. Con cualquier otro (o uno nuevo), no.
PACKAGE_CODE_ERRORS = {"RATES_NOT_FOUND", "NOT_FOUND", "PACKAGE_CARRIER_MISMATCH", "INVALID_PACKAGE_DIMENSIONS",
                       "PURCHASE_FAILED", ""}


class PackagingError(Exception):
    """El empaque y el servicio no van juntos (o el empaque ya no existe): no se compra nada."""


@dataclass(frozen=True)
class CarrierPackage:
    """Un empaque oficial de la paquetería, de los que Shopify cobra a tarifa plana."""

    key: str  # «usps:flat_rate_envelope»
    name: str  # como lo llama USPS (no se traduce)
    label: str  # en pocas palabras (se traduce)
    size: str  # medidas (no se traducen)
    codes: tuple[str, ...]  # carrierPackageCode posibles, el más probable primero
    services: tuple[str, ...] = (PRIORITY,)
    carrier: str = "usps"


# USPS Priority Mail Flat Rate. Los códigos siguen los ejemplos de la documentación de Shopify (Shipping Partner
# Platform: «flat_rate_envelope», «flat_rate_padded_envelope», «flat_rate_small_box», «flat_rate_box_medium»…).
USPS_FLAT_RATE: tuple[CarrierPackage, ...] = (
    CarrierPackage("usps:flat_rate_envelope", "Priority Mail Flat Rate Envelope", N_("Sobre Flat Rate"),
                   "12½ × 9½ in", ("flat_rate_envelope", "usps_flat_rate_envelope", "FlatRateEnvelope")),
    CarrierPackage("usps:flat_rate_padded_envelope", "Priority Mail Padded Flat Rate Envelope",
                   N_("Sobre acolchado Flat Rate"), "12½ × 9½ in",
                   ("flat_rate_padded_envelope", "padded_flat_rate_envelope", "FlatRatePaddedEnvelope")),
    CarrierPackage("usps:flat_rate_legal_envelope", "Priority Mail Legal Flat Rate Envelope",
                   N_("Sobre legal Flat Rate"), "15 × 9½ in",
                   ("flat_rate_legal_envelope", "legal_flat_rate_envelope", "FlatRateLegalEnvelope")),
    CarrierPackage("usps:flat_rate_small_box", "Priority Mail Small Flat Rate Box", N_("Caja chica Flat Rate"),
                   "8⅝ × 5⅜ × 1⅝ in",
                   ("flat_rate_small_box", "flat_rate_box_small", "small_flat_rate_box", "SmallFlatRateBox")),
    CarrierPackage("usps:flat_rate_medium_box", "Priority Mail Medium Flat Rate Box", N_("Caja mediana Flat Rate"),
                   "11 × 8½ × 5½ in",
                   ("flat_rate_medium_box", "flat_rate_box_medium", "medium_flat_rate_box", "MediumFlatRateBox")),
    CarrierPackage("usps:flat_rate_large_box", "Priority Mail Large Flat Rate Box", N_("Caja grande Flat Rate"),
                   "12 × 12 × 5½ in",
                   ("flat_rate_large_box", "flat_rate_box_large", "large_flat_rate_box", "LargeFlatRateBox")),
)
_CARRIER = {package.key: package for package in USPS_FLAT_RATE}


@dataclass(frozen=True)
class Packing:
    """Con qué se compra una guía: el empaque (SHOPIFY, un empaque propio o uno oficial) y el servicio (AUTO: el que
    elija Shopify). `note`: por qué el servicio no es el que se pidió (p. ej. empaque oficial de USPS → Priority)."""

    package: str = SHOPIFY
    service: str = AUTO
    note: str = ""


def carrier_package(key: str) -> CarrierPackage | None:
    return _CARRIER.get(key or "")


def own_package(settings, key: str) -> ShippingPackage | None:
    return next((p for p in getattr(settings, "packages", None) or [] if p.id == key), None) if key else None


def exists(settings, key: str) -> bool:
    return key == SHOPIFY or carrier_package(key) is not None or own_package(settings, key) is not None


def new_id() -> str:
    return "empaque-" + uuid.uuid4().hex[:8]


def own_name(package: ShippingPackage) -> str:
    """El nombre de un empaque propio («Mi caja» para la caja de antes de la 1.20, que no tenía nombre)."""
    if package.name.strip():
        return package.name.strip()
    return tr("Mi caja") if package.id == LEGACY_BOX_ID else tr("Empaque sin nombre")


def size_text(p) -> str:
    """«10 × 13 × 0.5 in · 1 oz»: medidas de fuera y peso vacío de un empaque propio."""
    dims = " × ".join(f"{float(v):g}" for v in (p.length, p.width, p.height))
    weight = f"{float(p.weight):g} {WEIGHT_UNITS.get(p.weight_unit, '')}".strip()
    return f"{dims} {DIM_UNITS.get(p.dim_unit, '')} · {weight}".strip()


def name(settings, key: str) -> str:
    """El nombre del empaque, para listas y mensajes."""
    if key == SHOPIFY:
        return tr("Paquete predeterminado de Shopify")
    official = carrier_package(key)
    if official is not None:
        return tr("USPS · {0}", tr(official.label))
    own = own_package(settings, key)
    return own_name(own) if own is not None else tr("Empaque que ya no existe")


def describe(settings, key: str) -> str:
    """El nombre con sus medidas («Bolsa de nailon · 10 × 13 × 0.5 in · 1 oz»)."""
    official = carrier_package(key)
    if official is not None:
        return f"{name(settings, key)} · {official.size}"
    own = own_package(settings, key)
    return f"{own_name(own)} · {size_text(own)}" if own is not None else name(settings, key)


def choices(settings) -> list[str]:
    """Los empaques para elegir, en orden: el de Shopify, los propios y los oficiales de USPS."""
    return ([SHOPIFY] + [p.id for p in getattr(settings, "packages", None) or []]
            + [package.key for package in USPS_FLAT_RATE])


def allowed_services(settings, key: str) -> tuple[str, ...] | None:
    """Con qué servicios vale ese empaque (None: con cualquiera)."""
    official = carrier_package(key)
    if official is not None:
        return official.services
    own = own_package(settings, key)
    if own is not None and own.usps_priority:
        return (PRIORITY, EXPRESS)
    return None


def official(settings, key: str) -> bool:
    """¿Es un empaque oficial de USPS (va solo con Priority Mail)?"""
    return allowed_services(settings, key) is not None


def fit(settings, package: str, service: str = AUTO) -> Packing:
    """El empaque con el servicio que le corresponde. Con un empaque oficial de USPS y «Automático», Priority Mail
    (Shopify elegiría Ground Advantage, que USPS no acepta en su empaque). PackagingError si no van juntos."""
    service = service or AUTO
    if not exists(settings, package):
        raise PackagingError(tr("El empaque elegido ya no existe: revisa tus reglas de paquetería o elige otro."))
    allowed = allowed_services(settings, package)
    if allowed is None or service in allowed:
        return Packing(package, service)
    what = name(settings, package)
    if service == AUTO:
        return Packing(package, allowed[0], tr("{0} es un empaque oficial de USPS: va con {1}.", what,
                                               service_name(allowed[0])))
    names = tr(" o ").join(service_name(key) for key in allowed)
    if service == GROUND:
        raise PackagingError(tr("USPS no acepta Ground Advantage en su empaque oficial ({0}): elige {1} o un empaque "
                                "propio. No se compró nada.", what, names))
    raise PackagingError(tr("{0} solo vale con {1}: elige ese servicio o un empaque propio. No se compró nada.",
                            what, names))


def for_order(settings, method: str, subtotal: float, domestic: bool = True) -> Packing:
    """El empaque y el servicio de un pedido según las reglas (por el envío que pagó y el valor de las piezas) o, si
    ninguna se cumple, los de «Todo lo demás». A otro país, un servicio nacional de las reglas (p. ej. Priority Mail)
    no existe: el servicio lo elige Shopify (con un empaque oficial de USPS no se puede: PackagingError)."""
    rule = rule_for(getattr(settings, "shipping_rules", None) or [], method, subtotal)
    if rule is not None:
        packing = fit(settings, getattr(rule, "package", "") or SHOPIFY, getattr(rule, "service", "") or AUTO)
    else:
        packing = fit(settings, getattr(settings, "default_package", "") or SHOPIFY,
                      getattr(settings, "default_service", "") or AUTO)
    service = by_key(packing.service)
    if domestic or service is None or service.international:
        return packing
    if official(settings, packing.package):
        raise PackagingError(tr("{0} es un empaque de USPS para envíos dentro de EE. UU.: este pedido va a otro país. "
                                "Elige otro empaque en «Imprimir guía». No se compró nada.",
                                name(settings, packing.package)))
    return Packing(packing.package, AUTO, tr("Envío a otro país: el servicio lo elige Shopify."))


def custom_input(p) -> dict[str, Any]:
    """Un empaque propio (o la caja de antes de la 1.20) como paquete de Shopify Shipping (customPackage)."""
    return {
        "type": p.type or "BOX",
        "dimensions": {"length": float(p.length), "width": float(p.width), "height": float(p.height),
                       "unit": p.dim_unit or "INCHES"},
        "weight": {"value": float(p.weight), "unit": p.weight_unit or "OUNCES"},
    }


def custom_for(settings, key: str) -> dict[str, Any] | None:
    """El customPackage de un empaque propio (None para el de Shopify y los oficiales)."""
    own = own_package(settings, key)
    return custom_input(own) if own is not None else None


def carrier_input(package: CarrierPackage, code: str) -> dict[str, str]:
    """Un empaque oficial como lo pide Shopify (carrierPackage), con uno de sus códigos."""
    return {"carrierCode": package.carrier, "carrierPackageCode": code, "name": package.name}


def codes_to_try(package: CarrierPackage, known: str = "") -> list[str]:
    """Los códigos a probar: el que ya funcionó (si hay) y, si no, los conocidos en orden."""
    return [known] if known else list(package.codes)


def package_problem(codes: list[str]) -> bool:
    """¿El rechazo de Shopify puede ser por el código del empaque? (Si es por otra cosa, no se prueba el siguiente.)"""
    return all(code in PACKAGE_CODE_ERRORS for code in codes or [""])


def describe_service(packing: Packing) -> str:
    return service_name(packing.service) if by_key(packing.service) else tr("el servicio que elija Shopify")
