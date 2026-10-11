"""Por qué impresora sale cada documento de un pedido: la guía de envío, la etiqueta del pedido, la página del
pedido y las guías de un metafield. Sirve para mostrarlo claro (Shopify → «Por dónde sale cada cosa», el asistente) y avisar cuando dos de ellos
terminan en el mismo aparato aunque tengan alias distintos.

Solo mira la configuración de LabelBridge: nunca le pregunta al sistema (es rápido y se puede llamar desde la pantalla).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import ConfigManager, PrinterProfile
from ..i18n import N_

SHIPPING, ORDER, PAGE, METAFIELD = "shipping", "order", "page", "metafield"


@dataclass(frozen=True)
class Route:
    key: str  # SHIPPING | ORDER | PAGE | METAFIELD
    title: str  # sin traducir (N_): se traduce al mostrarlo
    alias: str  # la impresora elegida; "" = la predeterminada
    active: bool  # si hoy se imprime


def routes(cfg: ConfigManager) -> list[Route]:
    s = cfg.data.shopify
    return [
        # Las guías se imprimen aunque no se compren solas: FedEx, «Imprimir guía» en Pedidos, reimpresiones.
        Route(SHIPPING, N_("Guía de envío"), s.shipping_label_printer, True),
        Route(ORDER, N_("Etiqueta del pedido"), s.order_label_printer, s.print_order_label),
        Route(PAGE, N_("Página del pedido"), page_printer(cfg), s.print_order_page),
        Route(METAFIELD, N_("Guías de un metafield"), s.metafield_printer, s.metafield_label),
    ]


def printer_for(alias: str, cfg: ConfigManager) -> PrinterProfile | None:
    """La impresora de LabelBridge a la que va un alias ("" = la predeterminada). None: no está en Impresoras (o está
    desactivada); al imprimir, el motor todavía la busca entre las instaladas en el equipo."""
    return cfg.find_printer(alias) if alias.strip() else cfg.default_printer()


def device(profile: PrinterProfile) -> tuple[str, str]:
    """El aparato de verdad detrás de una impresora de LabelBridge (dos alias pueden ser la misma)."""
    if profile.kind == "network":
        return "network", f"{profile.host.strip().casefold()}:{profile.port}"
    if profile.kind == "system":
        return "system", profile.system_name.strip().casefold()
    return "file", profile.output_dir.strip() or "default"


def shared_printer(first: str, second: str, cfg: ConfigManager) -> PrinterProfile | None:
    """La impresora en la que coinciden dos alias, o None si salen por aparatos distintos (o alguno no se conoce)."""
    a, b = printer_for(first, cfg), printer_for(second, cfg)
    if a is None or b is None:
        return None
    return a if device(a) == device(b) else None


def page_printer(cfg: ConfigManager) -> str:
    """La impresora de la página del pedido: solo la elegida para ella ("" = sin elegir). No toma la de la etiqueta del
    pedido: suele ser otra de etiquetas, y la página es una hoja."""
    return cfg.data.shopify.order_page_printer.strip()


def label_like(profile: PrinterProfile) -> bool:
    """¿Es una impresora de etiquetas (ZPL o papel chico)? La página del pedido, en hoja carta, no va ahí."""
    if profile.language == "zpl":
        return True
    media = profile.media_mm()
    return media is not None and max(media) < 200


def page_problem(cfg: ConfigManager, alias: str | None = None, on: bool | None = None) -> str:
    """Por qué no saldría la página del pedido: "unset" (falta elegir su impresora: nunca va a la predeterminada a
    ciegas, que puede ser la de etiquetas o un plotter), "shipping" (es la de las guías: gastaría etiquetas), "label"
    (es de etiquetas: la página es una hoja) o ""."""
    s = cfg.data.shopify
    alias = page_printer(cfg) if alias is None else alias
    if not (s.print_order_page if on is None else on):
        return ""
    if not alias.strip():
        return "unset"
    if shared_printer(alias, s.shipping_label_printer, cfg) is not None:
        return "shipping"
    profile = printer_for(alias, cfg)
    if profile is not None and label_like(profile):
        return "label"
    return ""


def order_label_clash(cfg: ConfigManager) -> PrinterProfile | None:
    """Si la etiqueta del pedido está activa y sale por la misma impresora que las guías, esa impresora."""
    s = cfg.data.shopify
    if not s.print_order_label:
        return None
    return shared_printer(s.order_label_printer, s.shipping_label_printer, cfg)
