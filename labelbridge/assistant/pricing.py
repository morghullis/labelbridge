"""Precio de cada llamada al modelo, para el límite de gasto diario (igual con cualquier proveedor).

Orden: el precio que el usuario ponga en Ajustes → Asistente; si no, el de la lista de precios públicos
(providers/catalog.py); los modelos en este PC son gratis; y si el modelo no está en la lista, una estimación
prudente (alta) para que el límite diario nunca se quede corto.
"""

from __future__ import annotations

from dataclasses import dataclass

from .providers import Endpoint, Usage
from .providers.catalog import ESTIMATE, price_of
from ..i18n import tr

CACHE_WRITE_FACTOR = 1.25  # escribir en la caché cuesta algo más que la entrada normal
CACHE_READ_FACTOR = 0.10  # leer de la caché, mucho menos


@dataclass(frozen=True)
class Price:
    """USD por millón de tokens."""

    input: float
    output: float
    cache_write: float
    cache_read: float
    source: str  # catalog | custom | local | estimate | reported

    @property
    def free(self) -> bool:
        return self.input == 0 and self.output == 0

    def text(self) -> str:
        if self.source == "local":
            return tr("Gratis: el modelo funciona en tu equipo o tu red.")
        values = tr("{0:g} USD entrada · {1:g} USD salida por millón de tokens", self.input, self.output)
        return {
            "catalog": tr("Precio público: {0}.", values),
            "custom": tr("Precio que pusiste: {0}.", values),
            "estimate": tr("Modelo sin precio conocido: se estima con {0} (pon el de tu proveedor para un límite exacto).", values),
            "reported": tr("El proveedor informa el costo exacto de cada llamada y se usa ese para el límite diario."),
        }[self.source]


def price_for(endpoint: Endpoint, settings) -> Price:
    custom_in = float(getattr(settings, "price_input", -1.0))
    custom_out = float(getattr(settings, "price_output", -1.0))
    if endpoint.provider.local and custom_in < 0 and custom_out < 0:
        return Price(0.0, 0.0, 0.0, 0.0, "local")
    known = price_of(endpoint.provider.id, endpoint.model)
    base = known or ESTIMATE
    source = "catalog" if known else "estimate"
    if endpoint.provider.reports_cost:
        source = "reported"  # el precio de la lista solo se usa si una respuesta no trae el costo
    if custom_in >= 0 or custom_out >= 0:
        source = "custom"
    unit_in = custom_in if custom_in >= 0 else base[0]
    unit_out = custom_out if custom_out >= 0 else base[1]
    if custom_in >= 0 or not known:
        write, read = unit_in * CACHE_WRITE_FACTOR, unit_in * CACHE_READ_FACTOR
    else:
        write, read = base[2], base[3]
    return Price(unit_in, unit_out, write, read, source)


def cost_of(price: Price, usage: Usage) -> float:
    if usage.cost is not None and price.source != "custom":
        return max(0.0, float(usage.cost))  # el proveedor informó el costo exacto
    total = (
        usage.input * price.input
        + usage.output * price.output
        + usage.cache_write * price.cache_write
        + usage.cache_read * price.cache_read
    )
    return total / 1_000_000
