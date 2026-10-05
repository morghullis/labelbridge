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

from ..i18n import N_, tr
from ..system.platform import pick

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
    "buy_shipping_label": N_("comprar e imprimir guías de Shopify Shipping"),
    "shipping_label_printer": N_("impresora de guías"),
    "label_size": N_("tamaño de la guía"),
    "notify_customer": N_("avisar al cliente"),
    "max_labels_per_day": N_("máximo de guías por día"),
    "print_order_label": N_("etiqueta interna de pedido"),
    "order_label_printer": N_("impresora de la etiqueta interna"),
    "metafield_label": N_("etiquetas desde metafield"),
    "metafield_printer": N_("impresora de metafield"),
    "only_paid": N_("solo pedidos pagados"),
    "only_unfulfilled": N_("solo pendientes de envío"),
    "required_tag": N_("tag requerido"),
    "tag_after_print": N_("tag al imprimir"),
    "poll_seconds": N_("revisar cada (s)"),
    "shop": N_("tienda"),
    "auth_mode": N_("autenticación"),
    "client_id": N_("Client ID"),
    "api_version": N_("versión de API"),
    "enabled": N_("activo"),
    "store_name": N_("remitente"),
    "sender_address": N_("dirección"),
    "show_items": N_("mostrar productos"),
    "show_qr": N_("mostrar QR"),
    "show_price": N_("mostrar total"),
    "computer_name": N_("nombre del equipo"),
    "start_with_windows": pick(N_("iniciar con Windows"), N_("abrir al iniciar sesión")),
    "start_minimized": N_("iniciar minimizado"),
    "auto_discover": N_("buscar impresoras al cambiar de red"),
    "notify_errors": N_("avisar errores"),
    "notify_printed": N_("avisar cada impresión"),
    "max_retries": N_("reintentos"),
    "retention_days": N_("días de historial"),
    "auto_update_check": N_("avisar actualizaciones"),
    "paused": N_("pausar impresión"),
    "new_alias": N_("nuevo alias"),
    "windows_name": pick(N_("impresora de Windows"), N_("impresora de la Mac")),
    "ip": N_("IP"),
    "port": N_("puerto"),
    "protocol": N_("protocolo"),
    "language": N_("lenguaje"),
    "media": N_("tamaño"),
    "dpi": N_("resolución"),
    "rotate": N_("rotación"),
    "host": N_("servidor"),
    "tls": N_("TLS"),
    "username": N_("usuario"),
    "channel": N_("canal"),
    "hours": N_("horas"),
}


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return tr("sí") if value else tr("no")
    text = str(value).replace("\n", " ")
    return text if len(text) <= 60 else text[:57] + "…"


def _fields(args: dict, skip: tuple[str, ...] = ()) -> str:
    parts = [f"{tr(FIELD_NAMES.get(k, k))}: {_value(v)}" for k, v in args.items() if k not in skip]
    return "\n".join(f"• {part}" for part in parts) or tr("• (sin cambios)")


def describe(name: str, args: dict) -> str:
    """Resumen legible de lo que el asistente quiere hacer (para la ventana de permiso y la auditoría)."""
    if name == "add_printer":
        target = args.get("windows_name") if args.get("connection") == "windows" else f"IP {args.get('ip', '?')}"
        return tr("Agregar una impresora ({0}):", target) + "\n" + _fields(args, ("connection", "windows_name", "ip"))
    if name == "update_printer":
        return tr("Cambiar la impresora «{0}»:", args.get("alias")) + "\n" + _fields(args, ("alias",))
    if name == "set_default_printer":
        return tr("Usar «{0}» como impresora predeterminada.", args.get("alias"))
    if name == "print_test_label":
        target = args.get("alias") or tr("la impresora predeterminada")
        return tr("Imprimir una etiqueta de prueba en «{0}» (gasta una etiqueta).", target)
    if name == "set_shopify_connection":
        return tr("Configurar la conexión con Shopify:") + "\n" + _fields(args)
    if name == "set_shopify_printing":
        return tr("Cambiar qué se imprime en cada pedido:") + "\n" + _fields(args)
    if name == "save_secret":
        return tr("Guardar el dato secreto {0} como {1}.", args.get("secret_ref"), args.get("kind"))
    if name == "set_sender":
        return tr("Cambiar el remitente de la etiqueta interna:") + "\n" + _fields(args)
    if name == "set_general":
        return tr("Cambiar ajustes generales:") + "\n" + _fields(args)
    if name == "set_n8n_connection":
        return tr("Configurar la conexión con n8n:") + "\n" + _fields(args)
    if name == "reprint_job":
        job = args.get("job_id", "")[:12]
        if args.get("mode") == "retry":
            return tr("Reintentar un trabajo del historial ({0}…).", job)
        return tr("Volver a imprimir un trabajo del historial ({0}…).", job)
    if name == "reprocess_shopify_orders":
        return tr("Revisar otra vez los pedidos de Shopify de las últimas {0} horas.", args.get("hours"))
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
            decision = self.ui.approve(tr("El asistente pide permiso"), summary)
            if decision == "deny":
                self.audit.record("asistente", name, "declined", detail=summary)
                return False, "El usuario NO dio permiso para este cambio. No lo repitas: pregúntale qué prefiere."
            if decision == "session":
                self.allow_all_changes = True
        self.counts[name] = used + 1
        self.audit.record("asistente", name, "allow", detail=summary)
        return True, ""
