"""Middleware de permisos del asistente: cada herramienta pasa por aquí antes de ejecutarse.

- Lectura y pruebas: se permiten solas.
- Cambios de configuración: con el modo «changes» la app pide permiso al usuario en una ventana
  (una vez, o para el resto de la conversación).
- Acciones críticas (comprar guías, borrar, instalar en Windows): siempre tienen su propia ventana de
  confirmación dentro de la herramienta.
- Topes por conversación: evitan bucles (p. ej. imprimir pruebas sin fin) y gasto innecesario.
Todo queda en el registro de auditoría.
"""

from __future__ import annotations

from typing import Any

READ, CHANGE, CRITICAL = "read", "change", "critical"

TOOL_CLASSES: dict[str, str] = {
    "get_status": READ,
    "diagnose_setup": READ,
    "list_windows_printers": READ,
    "scan_network_printers": READ,
    "get_recent_jobs": READ,
    "test_shopify_connection": READ,
    "test_n8n_connection": READ,
    "ask_user_for_secret": READ,  # abre su propia ventana segura
    "open_link": READ,
    "copy_shopify_scopes": READ,
    "complete_guide_step": READ,
    "add_printer": CHANGE,
    "update_printer": CHANGE,
    "set_default_printer": CHANGE,
    "print_test_label": CHANGE,
    "set_shopify_connection": CHANGE,
    "set_shopify_printing": CHANGE,
    "save_secret": CHANGE,
    "set_sender": CHANGE,
    "set_general": CHANGE,
    "set_n8n_connection": CHANGE,
    "reprint_job": CHANGE,
    "reprocess_shopify_orders": CHANGE,
    "remove_printer": CRITICAL,
    "install_windows_printer": CRITICAL,
}

# Usos máximos por conversación.
LIMITS: dict[str, int] = {
    "print_test_label": 5,
    "scan_network_printers": 4,
    "reprint_job": 10,
    "ask_user_for_secret": 6,
    "open_link": 8,
    "install_windows_printer": 2,
    "remove_printer": 5,
    "reprocess_shopify_orders": 3,
    "test_shopify_connection": 8,
    "test_n8n_connection": 6,
    "diagnose_setup": 6,
}

FIELD_NAMES = {
    "buy_shipping_label": "comprar e imprimir guías de Shopify Shipping",
    "shipping_label_printer": "impresora de guías",
    "label_size": "tamaño de la guía",
    "notify_customer": "avisar al cliente",
    "max_labels_per_day": "máximo de guías por día",
    "print_order_label": "etiqueta interna de pedido",
    "order_label_printer": "impresora de la etiqueta interna",
    "metafield_label": "etiquetas desde metafield",
    "metafield_printer": "impresora de metafield",
    "only_paid": "solo pedidos pagados",
    "only_unfulfilled": "solo pendientes de envío",
    "required_tag": "tag requerido",
    "tag_after_print": "tag al imprimir",
    "poll_seconds": "revisar cada (s)",
    "shop": "tienda",
    "auth_mode": "autenticación",
    "client_id": "Client ID",
    "api_version": "versión de API",
    "enabled": "activo",
    "store_name": "remitente",
    "sender_address": "dirección",
    "show_items": "mostrar productos",
    "show_qr": "mostrar QR",
    "show_price": "mostrar total",
    "computer_name": "nombre del equipo",
    "start_with_windows": "iniciar con Windows",
    "start_minimized": "iniciar minimizado",
    "auto_discover": "buscar impresoras al cambiar de red",
    "notify_errors": "avisar errores",
    "notify_printed": "avisar cada impresión",
    "max_retries": "reintentos",
    "retention_days": "días de historial",
    "auto_update_check": "avisar actualizaciones",
    "paused": "pausar impresión",
    "new_alias": "nuevo alias",
    "windows_name": "impresora de Windows",
    "ip": "IP",
    "port": "puerto",
    "protocol": "protocolo",
    "language": "lenguaje",
    "media": "tamaño",
    "dpi": "resolución",
    "rotate": "rotación",
    "host": "servidor",
    "tls": "TLS",
    "username": "usuario",
    "channel": "canal",
    "hours": "horas",
}


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "sí" if value else "no"
    text = str(value).replace("\n", " ")
    return text if len(text) <= 60 else text[:57] + "…"


def _fields(args: dict, skip: tuple[str, ...] = ()) -> str:
    parts = [f"{FIELD_NAMES.get(k, k)}: {_value(v)}" for k, v in args.items() if k not in skip]
    return "\n".join(f"• {part}" for part in parts) or "• (sin cambios)"


def describe(name: str, args: dict) -> str:
    """Resumen legible de lo que el asistente quiere hacer (para la ventana de permiso y la auditoría)."""
    if name == "add_printer":
        target = args.get("windows_name") if args.get("connection") == "windows" else f"IP {args.get('ip', '?')}"
        return f"Agregar una impresora ({target}):\n" + _fields(args, ("connection", "windows_name", "ip"))
    if name == "update_printer":
        return f"Cambiar la impresora «{args.get('alias')}»:\n" + _fields(args, ("alias",))
    if name == "set_default_printer":
        return f"Usar «{args.get('alias')}» como impresora predeterminada."
    if name == "print_test_label":
        return f"Imprimir una etiqueta de prueba en «{args.get('alias') or 'la impresora predeterminada'}» (gasta una etiqueta)."
    if name == "set_shopify_connection":
        return "Configurar la conexión con Shopify:\n" + _fields(args)
    if name == "set_shopify_printing":
        return "Cambiar qué se imprime en cada pedido:\n" + _fields(args)
    if name == "save_secret":
        return f"Guardar el dato secreto {args.get('secret_ref')} como {args.get('kind')}."
    if name == "set_sender":
        return "Cambiar el remitente de la etiqueta interna:\n" + _fields(args)
    if name == "set_general":
        return "Cambiar ajustes generales:\n" + _fields(args)
    if name == "set_n8n_connection":
        return "Configurar la conexión con n8n:\n" + _fields(args)
    if name == "reprint_job":
        mode = "Reintentar" if args.get("mode") == "retry" else "Volver a imprimir"
        return f"{mode} un trabajo del historial ({args.get('job_id', '')[:12]}…)."
    if name == "reprocess_shopify_orders":
        return f"Revisar otra vez los pedidos de Shopify de las últimas {args.get('hours')} horas."
    return f"{name}:\n" + _fields(args)


class ToolPermissions:
    """Estado de permisos de UNA conversación (al empezar otra, todo vuelve a pedirse)."""

    def __init__(self, ctx, ui) -> None:
        self.ctx = ctx
        self.ui = ui
        self.counts: dict[str, int] = {}
        self.allow_all_changes = False

    @property
    def audit(self):
        return self.ctx.engine.policy.audit

    def _needs_approval(self, name: str, args: dict) -> bool:
        if TOOL_CLASSES.get(name, CHANGE) != CHANGE or self.allow_all_changes:
            return False
        if self.ctx.cfg.data.assistant.approval != "changes":
            return False
        s = self.ctx.cfg.data.shopify
        if name == "set_shopify_printing" and args.get("buy_shipping_label") and not s.buy_shipping_label:
            return False  # la compra tiene su propia ventana de confirmación (más explícita)
        if name == "reprocess_shopify_orders" and s.buy_shipping_label and args.get("hours"):
            return False  # ídem: reprocesar con compra activa se confirma dentro de la herramienta
        return True

    def authorize(self, name: str, args: dict) -> tuple[bool, str]:
        """(permitido, motivo para el modelo si no)."""
        if name not in TOOL_CLASSES:
            self.audit.record("asistente", name, "deny", detail="herramienta desconocida")
            return False, f"La herramienta {name} no existe."
        used = self.counts.get(name, 0)
        limit = LIMITS.get(name)
        if limit is not None and used >= limit:
            self.audit.record("asistente", name, "deny", detail=f"tope de {limit} usos por conversación")
            return False, (
                f"Límite de {limit} usos de {name} en esta conversación. Explícale al usuario qué falta en vez de "
                "repetirla; si hace falta, que empiece una conversación nueva."
            )
        summary = describe(name, args)
        if self._needs_approval(name, args):
            decision = self.ui.approve("El asistente pide permiso", summary)
            if decision == "deny":
                self.audit.record("asistente", name, "declined", detail=summary)
                return False, "El usuario NO dio permiso para este cambio. No lo repitas: pregúntale qué prefiere."
            if decision == "session":
                self.allow_all_changes = True
        self.counts[name] = used + 1
        self.audit.record("asistente", name, "allow", detail=summary)
        return True, ""
