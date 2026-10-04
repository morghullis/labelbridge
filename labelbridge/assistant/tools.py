"""Herramientas del asistente: hacen lo mismo que los botones de la app.

Cada herramienta valida sus datos, cambia la configuración con la misma lógica que las pantallas y
devuelve un resumen en JSON para el modelo. Lo que cuesta dinero (comprar guías de Shopify Shipping)
o borra algo siempre pide confirmación en una ventana de la app, fuera del chat. Los secretos nunca
llegan al modelo: se piden en una ventana segura o se apartan del texto (ver secrets_guard.py).
"""

from __future__ import annotations

import ipaddress
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Protocol

from .. import __version__
from ..config import MEDIA_PRESETS, PrinterProfile, slugify
from ..connectors.shopify import DEV_DASHBOARD_URL, SCOPES_BASIC, SCOPES_LABELS, ShopifyError, admin_url, normalize_shop
from ..discovery import discover
from ..models import SOURCE_LABELS, status_text
from ..printing.hints import looks_like_label_printer, looks_like_zpl, suggest_alias
from ..printing.outputs import list_system_printers
from ..secret_store import MQTT_PASSWORD, SHOPIFY_ACCESS_TOKEN, SHOPIFY_CLIENT_SECRET
from ..system import autostart
from ..updater import UPDATES_PAGE
from .secrets_guard import SecretsGuard

log = logging.getLogger(__name__)

# tipo -> (nombre en el almacén seguro, título de la ventana, de dónde se saca)
SECRET_KINDS = {
    "shopify_client_secret": (
        SHOPIFY_CLIENT_SECRET,
        "Client secret de Shopify",
        "Dev Dashboard de Shopify → tu app → Settings → Client secret (empieza por shpss_).",
    ),
    "shopify_access_token": (
        SHOPIFY_ACCESS_TOKEN,
        "Token de acceso de Shopify",
        "Token de una app antigua creada en el admin de Shopify (empieza por shpat_).",
    ),
    "mqtt_password": (
        MQTT_PASSWORD,
        "Contraseña del broker MQTT (n8n)",
        "La contraseña del usuario de este PC en tu broker (EMQX).",
    ),
}


class ToolError(Exception):
    """Error que se devuelve al modelo (is_error) para que lo explique o lo corrija."""


class AssistantUI(Protocol):
    """Lo que las herramientas necesitan de la interfaz (en las pruebas se usa una versión falsa)."""

    def confirm(self, title: str, text: str, accept: str) -> bool: ...

    def ask_secret(self, title: str, hint: str) -> str | None: ...

    def open_url(self, url: str) -> None: ...

    def copy_text(self, text: str) -> None: ...

    def config_changed(self, section: str) -> None: ...


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    handler: Callable[[dict], Any]
    activity: str  # lo que muestra el chat mientras se ejecuta


# --- esquemas ----------------------------------------------------------------------------------

def _obj(properties: dict | None = None, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False}


def _s(description: str, enum: list[str] | None = None) -> dict:
    return {"type": "string", "description": description, **({"enum": enum} if enum else {})}


def _i(description: str) -> dict:
    return {"type": "integer", "description": description}


def _b(description: str) -> dict:
    return {"type": "boolean", "description": description}


_TYPES = {"string": str, "integer": int, "boolean": bool, "object": dict, "array": list}


def validate(schema: dict, args: Any) -> dict:
    """Comprueba la entrada del modelo contra el esquema antes de ejecutar nada."""
    if not isinstance(args, dict):
        raise ToolError("La entrada debe ser un objeto JSON")
    properties = schema.get("properties", {})
    unknown = sorted(set(args) - set(properties))
    if unknown:
        raise ToolError(f"Parámetros desconocidos: {', '.join(unknown)}")
    missing = [key for key in schema.get("required", []) if key not in args]
    if missing:
        raise ToolError(f"Faltan parámetros: {', '.join(missing)}")
    for key, value in args.items():
        spec = properties[key]
        expected = _TYPES[spec["type"]]
        if expected is int and isinstance(value, float) and value.is_integer():
            value = args[key] = int(value)
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            raise ToolError(f"«{key}» debe ser de tipo {spec['type']}")
        if "enum" in spec and value not in spec["enum"]:
            raise ToolError(f"«{key}» debe ser uno de: {', '.join(map(str, spec['enum']))}")
    return args


# --- herramientas --------------------------------------------------------------------------------

class Toolbox:
    def __init__(self, ctx, ui: AssistantUI, guard: SecretsGuard, cancel: threading.Event | None = None) -> None:
        self.ctx = ctx
        self.ui = ui
        self.guard = guard
        self.cancel = cancel or threading.Event()
        self.tools = self._build()
        self.by_name = {tool.name: tool for tool in self.tools}

    def definitions(self) -> list[dict]:
        """Definiciones para la API (orden fijo: el prefijo del prompt debe ser siempre idéntico)."""
        return [{"name": t.name, "description": t.description, "input_schema": t.schema} for t in self.tools]

    def activity(self, name: str) -> str:
        tool = self.by_name.get(name)
        return tool.activity if tool else name

    def run(self, name: str, args: Any) -> tuple[str, bool]:
        """Ejecuta una herramienta. Devuelve (contenido para el modelo, es_error)."""
        tool = self.by_name.get(name)
        if tool is None:
            return f"No existe la herramienta {name}", True
        try:
            result = tool.handler(validate(tool.schema, args))
        except ToolError as exc:
            return str(exc), True
        except Exception as exc:  # el modelo recibe el error y se lo explica al usuario
            log.exception("Asistente: falló la herramienta %s", name)
            return f"Error inesperado: {exc}", True
        log.info("Asistente: %s %s", name, json.dumps(_loggable(args), ensure_ascii=False))
        return (result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)), False

    # --- utilidades ------------------------------------------------------------------------------
    def _save(self, section: str) -> None:
        self.ctx.cfg.save(section)
        self.ui.config_changed(section)

    def _printer(self, alias: str) -> PrinterProfile:
        key = alias.strip().casefold()
        for profile in self.ctx.cfg.data.printers:
            if key in (profile.alias.casefold(), profile.system_name.casefold()):
                return profile
        names = ", ".join(p.alias for p in self.ctx.cfg.data.printers) or "ninguna"
        raise ToolError(f"No hay ninguna impresora con el alias «{alias}». Configuradas: {names}.")

    def _printer_alias(self, value: str) -> str:
        """Impresora para un tipo de etiqueta: alias existente o vacío (= predeterminada)."""
        value = value.strip()
        if value.casefold() in ("", "predeterminada", "default"):
            return ""
        return self._printer(value).alias

    def _printer_summary(self, p: PrinterProfile) -> dict:
        default = self.ctx.cfg.default_printer()
        return {
            "alias": p.alias,
            "connection": {"system": "windows", "network": "network", "file": "file (pruebas)"}.get(p.kind, p.kind),
            "target": p.target_text(),
            "language": p.language,
            "media": p.media,
            "dpi": p.dpi,
            "rotate": p.rotate,
            "enabled": p.enabled,
            "default": bool(default and default.id == p.id),
        }

    def _shop_key(self, suffix: str) -> str | None:
        try:
            return self.ctx.services.shopify._key(suffix)
        except ShopifyError:
            return None

    # --- lectura ---------------------------------------------------------------------------------
    def get_status(self, _args: dict) -> dict:
        from ..ui.pages.guide import STEPS

        data = self.ctx.cfg.data
        s = data.shopify
        shop_service = self.ctx.services.shopify
        prefix = self._shop_key("fo:")
        bought_today = self.ctx.store.count_seen(prefix, self.ctx.store.start_of_today()) if prefix else 0
        steps = []
        for number, step in enumerate(STEPS, start=1):
            try:
                done = bool(step.check(self.ctx))
            except Exception:
                done = False
            steps.append({"step": number, "key": step.key, "title": step.title, "done": done, "optional": step.optional})
        mqtt = data.mqtt
        return {
            "app_version": __version__,
            "computer": data.general.agent_name,
            "automatic_printing": "en pausa" if data.general.paused else "activa",
            "printers": [self._printer_summary(p) for p in data.printers],
            "shopify": {
                "connector_enabled": s.enabled,
                "shop": s.shop,
                "auth_mode": s.auth_mode,
                "client_id": s.client_id,
                "client_secret_saved": bool(self.ctx.secrets.get(SHOPIFY_CLIENT_SECRET)),
                "access_token_saved": bool(self.ctx.secrets.get(SHOPIFY_ACCESS_TOKEN)),
                "api_version": s.api_version,
                "connection_state": shop_service.state,
                "connection_message": shop_service.message,
                "buy_shipping_label": s.buy_shipping_label,
                "shipping_label_printer": s.shipping_label_printer or "(predeterminada)",
                "notify_customer": s.notify_customer,
                "max_labels_per_day": s.max_labels_per_day,
                "labels_bought_today": bought_today,
                "print_order_label": s.print_order_label,
                "order_label_printer": s.order_label_printer or "(predeterminada)",
                "metafield_label": s.metafield_label,
                "only_paid": s.only_paid,
                "only_unfulfilled": s.only_unfulfilled,
                "required_tag": s.required_tag,
                "tag_after_print": s.tag_after_print,
                "poll_seconds": s.poll_seconds,
            },
            "sender_for_order_label": {
                "store_name": data.labels.store_name,
                "address": data.labels.sender_address,
            },
            "n8n_mqtt": {
                "enabled": mqtt.enabled,
                "host": mqtt.host,
                "username": mqtt.username,
                "channel": mqtt.channel,
                "password_saved": bool(self.ctx.secrets.get(MQTT_PASSWORD)),
                "state": self.ctx.services.mqtt.state,
            },
            "start_with_windows": data.general.start_with_windows,
            "guide_steps": steps,
            "jobs": self.ctx.store.counts(),
        }

    def list_windows_printers(self, _args: dict) -> dict:
        printers = list_system_printers()
        configured = {p.system_name.casefold() for p in self.ctx.cfg.data.printers if p.kind == "system"}
        return {
            "printers": [
                {
                    "windows_name": p["name"],
                    "model_or_driver": p["model"] or p["driver"] or p["description"],
                    "port": p["port"] or p["location"],
                    "windows_default": p["is_default"],
                    "looks_like_label_printer": looks_like_label_printer(f"{p['name']} {p['model']} {p['driver']}"),
                    "speaks_zpl": looks_like_zpl(f"{p['name']} {p['model']} {p['driver']}"),
                    "already_in_labelbridge": p["name"].casefold() in configured,
                }
                for p in printers
            ]
        }

    def scan_network_printers(self, _args: dict) -> dict:
        found = discover(self.cancel)
        self.ctx.services.network.results = found
        self.ctx.events.emit("discovery", found)  # la pantalla Impresoras también los muestra
        configured = {p.host for p in self.ctx.cfg.data.printers if p.kind == "network"}
        return {
            "found": [
                {
                    "ip": p.ip,
                    "name": p.title,
                    "model": p.model,
                    "protocols": p.protocols,
                    "speaks_zpl": p.is_zpl,
                    "suggested": p.suggested(),
                    "already_in_labelbridge": p.ip in configured,
                }
                for p in found
            ]
        }

    def get_recent_jobs(self, args: dict) -> dict:
        limit = max(1, min(30, args.get("limit", 10)))
        jobs = self.ctx.store.list(limit=limit, status="failed" if args.get("only_errors") else None)
        return {
            "jobs": [
                {
                    "job_id": job["id"],
                    "title": job["title"],
                    "status": status_text(job),
                    "source": SOURCE_LABELS.get(job["source"], job["source"]),
                    "printer": job["printer_used"] or job["printer"] or "(predeterminada)",
                    "created": datetime.fromtimestamp(job["created_at"]).strftime("%d/%m %H:%M"),
                    "error": (job["error"] or "")[:300],
                    "tracking": (job.get("meta") or {}).get("tracking"),
                }
                for job in jobs
            ]
        }

    # --- impresoras ------------------------------------------------------------------------------
    def add_printer(self, a: dict) -> dict:
        printers = self.ctx.cfg.data.printers
        aliases = [p.alias for p in printers]
        if a["connection"] == "windows":
            name = a.get("windows_name", "").strip()
            if not name:
                raise ToolError("Falta windows_name. Usa list_windows_printers para ver los nombres exactos.")
            installed = list_system_printers()
            match = next((p for p in installed if p["name"].casefold() == name.casefold()), None)
            if match is None:
                names = ", ".join(p["name"] for p in installed) or "ninguna"
                raise ToolError(f"En Windows no hay ninguna impresora llamada «{name}». Instaladas: {names}.")
            text = f"{match['name']} {match['model']} {match['driver']}"
            profile = PrinterProfile(
                kind="system",
                system_name=match["name"],
                language="zpl" if looks_like_zpl(text) else "driver",
                media="4x6" if looks_like_label_printer(text) else "auto",
                dpi=300 if "300" in text else 203,
            )
            base_alias = match["name"]
        else:
            ip = a.get("ip", "").strip()
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                raise ToolError("Falta la IP de la impresora o no es válida (ejemplo: 192.168.1.50).") from None
            found = next((p for p in self.ctx.services.network.results if p.ip == ip), None)
            suggested = found.suggested() if found else {"protocol": "raw", "port": 9100, "language": "zpl"}
            protocol = a.get("protocol") or suggested["protocol"]
            port = a.get("port") or (suggested["port"] if protocol == suggested["protocol"] else (9100 if protocol == "raw" else 631))
            if not 1 <= port <= 65535:
                raise ToolError("El puerto debe estar entre 1 y 65535.")
            is_label = found is None or found.is_zpl or looks_like_label_printer(f"{found.title} {found.model}")
            profile = PrinterProfile(
                kind="network",
                host=ip,
                port=port,
                protocol=protocol,
                ipp_path=suggested.get("ipp_path", "/ipp/print"),
                language=suggested["language"] if protocol == suggested["protocol"] else ("zpl" if protocol == "raw" else "pdf"),
                media="4x6" if is_label else "auto",
            )
            base_alias = (found.name or found.model) if found and (found.name or found.model) else ip
        for key in ("language", "media", "dpi"):
            if key in a:
                setattr(profile, key, a[key])
        profile.alias = suggest_alias(a.get("alias") or ("etiquetas" if not aliases else base_alias), aliases)
        current_default = self.ctx.cfg.default_printer()
        make_default = a.get("make_default")
        if make_default is None:
            make_default = current_default is None or current_default.kind == "file"
        printers.append(profile)
        if make_default:
            self.ctx.cfg.data.general.default_printer_id = profile.id
        self._save("printers")
        self.ctx.engine.wake()
        return {"added": self._printer_summary(profile)}

    def update_printer(self, a: dict) -> dict:
        profile = self._printer(a["alias"])
        if "new_alias" in a:
            others = [p.alias for p in self.ctx.cfg.data.printers if p.id != profile.id]
            profile.alias = suggest_alias(a["new_alias"], others)
        if "windows_name" in a:
            if profile.kind != "system":
                raise ToolError("windows_name solo aplica a impresoras de Windows.")
            installed = {p["name"].casefold(): p["name"] for p in list_system_printers()}
            if a["windows_name"].casefold() not in installed:
                raise ToolError(f"En Windows no hay ninguna impresora llamada «{a['windows_name']}».")
            profile.system_name = installed[a["windows_name"].casefold()]
        if any(key in a for key in ("ip", "port", "protocol")) and profile.kind != "network":
            raise ToolError("ip, port y protocol solo aplican a impresoras de red.")
        if "ip" in a:
            try:
                ipaddress.ip_address(a["ip"].strip())
            except ValueError:
                raise ToolError("La IP no es válida.") from None
            profile.host = a["ip"].strip()
        if "port" in a:
            if not 1 <= a["port"] <= 65535:
                raise ToolError("El puerto debe estar entre 1 y 65535.")
            profile.port = a["port"]
        for key in ("protocol", "language", "media", "dpi", "rotate", "enabled"):
            if key in a:
                setattr(profile, key, a[key])
        self._save("printers")
        self.ctx.engine.wake()
        return {"updated": self._printer_summary(profile)}

    def remove_printer(self, a: dict) -> dict:
        profile = self._printer(a["alias"])
        if not self.ui.confirm(
            "Eliminar impresora", f"El asistente quiere quitar «{profile.alias}» de LabelBridge.\n\n¿Eliminarla?", "Eliminar"
        ):
            return {"removed": False, "reason": "El usuario no aprobó eliminarla en la ventana de confirmación."}
        cfg = self.ctx.cfg
        cfg.data.printers = [p for p in cfg.data.printers if p.id != profile.id]
        if cfg.data.general.default_printer_id == profile.id:
            cfg.data.general.default_printer_id = cfg.data.printers[0].id if cfg.data.printers else ""
        self._save("printers")
        return {"removed": True, "alias": profile.alias}

    def set_default_printer(self, a: dict) -> dict:
        profile = self._printer(a["alias"])
        if not profile.enabled:
            profile.enabled = True
        self.ctx.cfg.data.general.default_printer_id = profile.id
        self._save("printers")
        return {"default": profile.alias}

    def print_test_label(self, a: dict) -> dict:
        profile = self._printer(a["alias"]) if a.get("alias") else self.ctx.cfg.default_printer()
        if profile is None:
            raise ToolError("No hay impresoras configuradas: agrega una primero.")
        try:
            detail = self.ctx.engine.print_test(profile)
        except Exception as exc:
            raise ToolError(f"No se pudo imprimir en «{profile.alias}»: {exc}") from None
        return {
            "sent_to": profile.alias,
            "detail": detail,
            "note": "Pregunta si salió completa, derecha y del tamaño correcto; si no, ajusta media, rotate o dpi.",
        }

    # --- Shopify ---------------------------------------------------------------------------------
    def _shopify_missing(self) -> list[str]:
        s = self.ctx.cfg.data.shopify
        missing = []
        if not s.shop:
            missing.append("dominio de la tienda (shop)")
        if s.auth_mode == "token":
            if not self.ctx.secrets.get(SHOPIFY_ACCESS_TOKEN):
                missing.append("token de acceso shpat_ (pídelo con ask_user_for_secret)")
        else:
            if not s.client_id:
                missing.append("Client ID")
            if not self.ctx.secrets.get(SHOPIFY_CLIENT_SECRET):
                missing.append("Client secret (pídelo con ask_user_for_secret)")
        return missing

    def set_shopify_connection(self, a: dict) -> dict:
        s = self.ctx.cfg.data.shopify
        if "shop" in a:
            try:
                s.shop = normalize_shop(a["shop"])
            except ShopifyError:
                raise ToolError(
                    "Ese no es el dominio interno de Shopify. Pide el que termina en .myshopify.com "
                    "(Shopify admin → Configuración → Dominios), aunque la tienda use otro dominio propio."
                ) from None
        if "auth_mode" in a:
            s.auth_mode = a["auth_mode"]
        if "client_id" in a:
            s.client_id = a["client_id"].strip()
        if "api_version" in a:
            s.api_version = a["api_version"]
        if "enabled" in a:
            if a["enabled"] and not s.shop:
                raise ToolError("Primero indica la tienda (shop).")
            s.enabled = a["enabled"]
        self._save("shopify")
        return {
            "shop": s.shop,
            "connector_enabled": s.enabled,
            "auth_mode": s.auth_mode,
            "client_id": s.client_id,
            "missing": self._shopify_missing(),
        }

    def ask_user_for_secret(self, a: dict) -> dict:
        secret_name, title, hint = SECRET_KINDS[a["kind"]]
        value = self.ui.ask_secret(title, hint)
        if not value:
            return {"saved": False, "reason": "El usuario cerró la ventana sin pegar el dato."}
        self.ctx.secrets.set(secret_name, value)
        self._after_secret(a["kind"])
        return {"saved": True, "kind": a["kind"], "characters": len(value.strip())}

    def save_secret(self, a: dict) -> dict:
        value = self.guard.take(a["secret_ref"])
        if value is None:
            raise ToolError(f"No hay ningún dato apartado como {a['secret_ref']}.")
        secret_name, _title, _hint = SECRET_KINDS[a["kind"]]
        self.ctx.secrets.set(secret_name, value)
        self._after_secret(a["kind"])
        return {"saved": True, "kind": a["kind"], "characters": len(value)}

    def _after_secret(self, kind: str) -> None:
        # El servicio vuelve a leer el secreto al reiniciarse.
        self._save("mqtt" if kind == "mqtt_password" else "shopify")

    def test_shopify_connection(self, _args: dict) -> dict:
        missing = self._shopify_missing()
        if missing:
            raise ToolError("Falta configurar: " + "; ".join(missing))
        try:
            data = self.ctx.services.shopify.test_connection()
        except Exception as exc:
            raise ToolError(f"Shopify rechazó la conexión: {exc}") from None
        labels = self.ctx.cfg.data.labels
        if data.get("name") and not labels.store_name.strip():
            labels.store_name = str(data["name"])
            self._save("general")
        return {
            "connected": True,
            "store_name": data.get("name"),
            "domain": data.get("myshopifyDomain"),
            "currency": data.get("currencyCode"),
            "scopes": sorted(data.get("scopes") or []),
            "missing_scopes": data.get("missing") or [],
            "connector_enabled": self.ctx.cfg.data.shopify.enabled,
        }

    def set_shopify_printing(self, a: dict) -> dict:
        s = self.ctx.cfg.data.shopify
        printers = {}
        for key in ("shipping_label_printer", "order_label_printer", "metafield_printer"):
            if key in a:
                printers[key] = self._printer_alias(a[key])
        if "max_labels_per_day" in a and not 1 <= a["max_labels_per_day"] <= 5000:
            raise ToolError("max_labels_per_day debe estar entre 1 y 5000.")
        if "poll_seconds" in a and not 15 <= a["poll_seconds"] <= 3600:
            raise ToolError("poll_seconds debe estar entre 15 y 3600.")
        if a.get("buy_shipping_label") and not s.buy_shipping_label:
            limit = a.get("max_labels_per_day", s.max_labels_per_day)
            approved = self.ui.confirm(
                "Comprar guías de Shopify Shipping automáticamente",
                "El asistente quiere activar la compra automática de guías de envío.\n\n"
                "Por cada pedido nuevo pagado y pendiente de envío, LabelBridge comprará la guía con Shopify Shipping "
                "(usando el paquete y la tarifa predeterminados de tu tienda) y la imprimirá. Shopify cobra cada guía "
                f"en tu factura. Máximo {limit} guías por día.\n\n¿Activar la compra automática en este PC?",
                "Activar compra de guías",
            )
            if not approved:
                # No se aplica nada de esta petición: apagar otras etiquetas sin la guía dejaría pedidos sin imprimir.
                return {
                    "applied": False,
                    "reason": "El usuario no aprobó la compra de guías en la ventana de confirmación; no se cambió nada.",
                }
        result: dict[str, Any] = {"applied": True}
        for key in (
            "buy_shipping_label", "notify_customer", "max_labels_per_day", "print_order_label", "metafield_label",
            "only_paid", "only_unfulfilled", "poll_seconds",
        ):
            if key in a:
                setattr(s, key, a[key])
        for key in ("required_tag", "tag_after_print"):
            if key in a:
                setattr(s, key, a[key].strip())
        for key, alias in printers.items():
            setattr(s, key, alias)
        self._save("shopify")
        result.update(
            {
                "buy_shipping_label": s.buy_shipping_label,
                "shipping_label_printer": s.shipping_label_printer or "(predeterminada)",
                "notify_customer": s.notify_customer,
                "max_labels_per_day": s.max_labels_per_day,
                "print_order_label": s.print_order_label,
                "order_label_printer": s.order_label_printer or "(predeterminada)",
                "metafield_label": s.metafield_label,
                "only_paid": s.only_paid,
                "only_unfulfilled": s.only_unfulfilled,
                "required_tag": s.required_tag,
                "tag_after_print": s.tag_after_print,
            }
        )
        if s.buy_shipping_label:
            result["reminder"] = (
                "Para comprar guías la app de Shopify necesita los permisos write_orders y "
                "write_merchant_managed_fulfillment_orders: compruébalo con test_shopify_connection."
            )
        return result

    def reprocess_shopify_orders(self, a: dict) -> dict:
        hours = a["hours"]
        if not 0 <= hours <= 720:
            raise ToolError("hours debe estar entre 0 y 720.")
        s = self.ctx.cfg.data.shopify
        if not s.shop:
            raise ToolError("Primero configura la tienda.")
        if hours and s.buy_shipping_label and not self.ui.confirm(
            "Reprocesar pedidos anteriores",
            f"El asistente quiere revisar los pedidos de las últimas {hours} h.\n\nLa compra automática de guías está "
            "activa: se comprará (con cargo en Shopify) la guía de cada pedido pagado y pendiente de envío de ese "
            "periodo que LabelBridge todavía no haya comprado.\n\n¿Continuar?",
            "Reprocesar y comprar",
        ):
            return {"reprocessed": False, "reason": "El usuario no lo aprobó en la ventana de confirmación."}
        since = self.ctx.services.shopify.reset_since(hours)
        return {
            "reprocessed": True,
            "orders_created_since": datetime.fromtimestamp(since).strftime("%d/%m/%Y %H:%M"),
            "connector_enabled": s.enabled,
        }

    # --- ajustes ---------------------------------------------------------------------------------
    def set_sender(self, a: dict) -> dict:
        lb = self.ctx.cfg.data.labels
        for key in ("store_name", "sender_address"):
            if key in a:
                setattr(lb, key, a[key].strip())
        for key in ("show_items", "show_qr", "show_price"):
            if key in a:
                setattr(lb, key, a[key])
        self._save("general")
        return {
            "store_name": lb.store_name,
            "sender_address": lb.sender_address,
            "show_items": lb.show_items,
            "show_qr": lb.show_qr,
            "show_price": lb.show_price,
        }

    def set_general(self, a: dict) -> dict:
        g = self.ctx.cfg.data.general
        if "max_retries" in a and not 0 <= a["max_retries"] <= 20:
            raise ToolError("max_retries debe estar entre 0 y 20.")
        if "retention_days" in a and not 1 <= a["retention_days"] <= 365:
            raise ToolError("retention_days debe estar entre 1 y 365.")
        if "computer_name" in a and a["computer_name"].strip():
            g.agent_name = a["computer_name"].strip()[:40]
        for key in (
            "start_with_windows", "start_minimized", "auto_discover", "notify_errors", "notify_printed",
            "max_retries", "retention_days", "auto_update_check",
        ):
            if key in a:
                setattr(g, key, a[key])
        if "paused" in a and a["paused"] != g.paused:
            self.ctx.engine.set_paused(a["paused"])
        self._save("general")
        if "start_with_windows" in a:
            try:
                autostart.apply(g.start_with_windows)
            except OSError:
                pass
        return {
            "computer_name": g.agent_name,
            "automatic_printing": "en pausa" if g.paused else "activa",
            "start_with_windows": g.start_with_windows,
            "start_minimized": g.start_minimized,
            "auto_discover": g.auto_discover,
            "notify_errors": g.notify_errors,
            "notify_printed": g.notify_printed,
            "max_retries": g.max_retries,
            "retention_days": g.retention_days,
            "auto_update_check": g.auto_update_check,
        }

    def complete_guide_step(self, a: dict) -> dict:
        g = self.ctx.cfg.data.general
        keys = {k for k in g.guide_acknowledged.split(",") if k}
        keys.add(a["step"])
        g.guide_acknowledged = ",".join(sorted(keys))
        self._save("general")
        return {"acknowledged": sorted(keys)}

    def reprint_job(self, a: dict) -> dict:
        job = self.ctx.store.find(a["job_id"])
        if job is None:
            raise ToolError("No existe ese trabajo; usa get_recent_jobs para ver los job_id.")
        printer = self._printer_alias(a["printer"]) if a.get("printer") else None
        if a.get("mode", "reprint") == "retry":
            if job["status"] not in ("failed", "canceled", "queued"):
                raise ToolError("Solo se reintentan trabajos con error, cancelados o en cola; usa mode=reprint.")
            updated = self.ctx.store.retry(job["id"])
        else:
            updated = self.ctx.store.reprint(job["id"], printer)
            if updated is None:
                raise ToolError("El contenido de ese trabajo ya no está disponible para reimprimir.")
        self.ctx.events.emit("job", updated)
        self.ctx.engine.wake()
        return {"queued": updated["title"], "job_id": updated["id"]}

    # --- n8n en tiempo real (MQTT) ------------------------------------------------------------------
    def set_n8n_connection(self, a: dict) -> dict:
        m = self.ctx.cfg.data.mqtt
        if "host" in a:
            m.host = a["host"].strip().removeprefix("mqtts://").removeprefix("mqtt://").split("/")[0]
        if "port" in a:
            if not 1 <= a["port"] <= 65535:
                raise ToolError("El puerto debe estar entre 1 y 65535.")
            m.port = a["port"]
        if "tls" in a:
            m.tls = a["tls"]
        if "username" in a:
            m.username = a["username"].strip()
        if "channel" in a:
            m.channel = slugify(a["channel"])
        if "enabled" in a:
            if a["enabled"] and not m.host:
                raise ToolError("Primero indica el servidor (host) del broker MQTT.")
            m.enabled = a["enabled"]
        self._save("mqtt")
        return {
            "enabled": m.enabled,
            "host": m.host,
            "port": m.port,
            "tls": m.tls,
            "username": m.username,
            "channel": m.channel,
            "password_saved": bool(self.ctx.secrets.get(MQTT_PASSWORD)),
        }

    def test_n8n_connection(self, _args: dict) -> dict:
        try:
            message = self.ctx.services.mqtt.test_connection()
        except Exception as exc:
            raise ToolError(str(exc)) from None
        return {"connected": True, "message": message}

    # --- navegador y portapapeles ------------------------------------------------------------------
    def open_link(self, a: dict) -> dict:
        target = a["target"]
        shop = self.ctx.cfg.data.shopify.shop
        urls = {
            "shopify_dev_dashboard": DEV_DASHBOARD_URL,
            "shopify_new_draft_order": admin_url(shop, "draft_orders/new"),
            "shopify_shipping_settings": admin_url(shop, "settings/shipping"),
            "shopify_orders": admin_url(shop, "orders"),
            "labelbridge_downloads": UPDATES_PAGE,
        }
        self.ui.open_url(urls[target])
        return {"opened": urls[target]}

    def copy_shopify_scopes(self, a: dict) -> dict:
        scopes = SCOPES_LABELS if a.get("for_shipping_labels", True) else SCOPES_BASIC
        self.ui.copy_text(scopes)
        return {"copied_to_clipboard": scopes}

    # --- catálogo --------------------------------------------------------------------------------
    def _build(self) -> list[Tool]:
        media = list(MEDIA_PRESETS)
        printer_alias = _s("Alias de una impresora de LabelBridge (de get_status).")
        label_printer = _s("Alias de la impresora para este tipo de etiqueta, o «predeterminada».")
        return [
            Tool(
                "get_status",
                "Estado actual de LabelBridge en este PC: impresoras, conexión con Shopify y qué se imprime en cada "
                "pedido, remitente, n8n, pasos de la guía y conteo de trabajos. Los secretos solo aparecen como "
                "guardados o no. Llámala al empezar y siempre antes de afirmar cómo está configurado algo.",
                _obj(),
                self.get_status,
                "Revisando la configuración",
            ),
            Tool(
                "list_windows_printers",
                "Impresoras instaladas en Windows en este PC (USB o de red ya instaladas), con modelo/driver y si "
                "parecen de etiquetas o ZPL. Llámala antes de agregar una impresora de tipo windows para usar su "
                "nombre exacto.",
                _obj(),
                self.list_windows_printers,
                "Leyendo las impresoras de Windows",
            ),
            Tool(
                "scan_network_printers",
                "Busca impresoras en la red local o Wi-Fi (puertos 9100, 631, 515 y Bonjour). Tarda 10-20 s y no "
                "imprime nada. Úsala cuando la impresora es de red o Wi-Fi y no está instalada en Windows o no se "
                "sabe su IP.",
                _obj(),
                self.scan_network_printers,
                "Buscando impresoras en la red",
            ),
            Tool(
                "add_printer",
                "Agrega una impresora a LabelBridge. connection=windows para una impresora instalada en Windows "
                "(windows_name exacto de list_windows_printers); connection=network para una impresora por IP sin "
                "driver (ip). El lenguaje, el tamaño y la resolución se sugieren solos si no se indican. La primera "
                "impresora real queda como predeterminada.",
                _obj(
                    {
                        "connection": _s("Cómo se conecta.", ["windows", "network"]),
                        "windows_name": _s("Nombre exacto en Windows (solo connection=windows)."),
                        "ip": _s("IP de la impresora (solo connection=network)."),
                        "port": _i("Puerto (por defecto 9100 para raw y 631 para IPP)."),
                        "protocol": _s("Protocolo de red: raw (puerto 9100), ipp o ipps.", ["raw", "ipp", "ipps"]),
                        "alias": _s("Nombre corto para usarla, p. ej. «etiquetas»."),
                        "language": _s(
                            "driver = usar el driver de Windows; zpl = Zebra y compatibles; pdf = PDF directo.",
                            ["driver", "zpl", "pdf"],
                        ),
                        "media": _s("Tamaño del papel. 4x6 es la etiqueta de envío estándar.", media),
                        "dpi": {"type": "integer", "description": "Resolución de la impresora térmica.", "enum": [203, 300, 600]},
                        "make_default": _b("Dejarla como predeterminada."),
                    },
                    ("connection",),
                ),
                self.add_printer,
                "Agregando la impresora",
            ),
            Tool(
                "update_printer",
                "Cambia una impresora ya agregada: alias, lenguaje, tamaño, resolución, rotación, activar/desactivar "
                "o su conexión. Úsala si la etiqueta sale cortada, girada o del tamaño incorrecto.",
                _obj(
                    {
                        "alias": printer_alias,
                        "new_alias": _s("Nuevo alias."),
                        "windows_name": _s("Otra impresora de Windows (solo tipo windows)."),
                        "ip": _s("Nueva IP (solo tipo network)."),
                        "port": _i("Nuevo puerto (solo tipo network)."),
                        "protocol": _s("raw, ipp o ipps (solo tipo network).", ["raw", "ipp", "ipps"]),
                        "language": _s("driver, zpl o pdf.", ["driver", "zpl", "pdf"]),
                        "media": _s("Tamaño del papel.", media),
                        "dpi": {"type": "integer", "description": "Resolución.", "enum": [203, 300, 600]},
                        "rotate": _s("Rotación de la etiqueta.", ["auto", "0", "90", "180", "270"]),
                        "enabled": _b("Activa o desactivada."),
                    },
                    ("alias",),
                ),
                self.update_printer,
                "Actualizando la impresora",
            ),
            Tool(
                "remove_printer",
                "Quita una impresora de LabelBridge (no la desinstala de Windows). Solo si el usuario lo pide; la "
                "app pide confirmación en una ventana.",
                _obj({"alias": printer_alias}, ("alias",)),
                self.remove_printer,
                "Quitando la impresora",
            ),
            Tool(
                "set_default_printer",
                "Elige la impresora predeterminada (la que se usa cuando un tipo de etiqueta no indica otra).",
                _obj({"alias": printer_alias}, ("alias",)),
                self.set_default_printer,
                "Cambiando la impresora predeterminada",
            ),
            Tool(
                "print_test_label",
                "Imprime ahora una etiqueta de ejemplo (pedido #1001) en la impresora indicada o en la "
                "predeterminada. Gasta una etiqueta: úsala cuando el usuario quiera probar o acepte hacer la prueba.",
                _obj({"alias": printer_alias}),
                self.print_test_label,
                "Imprimiendo la etiqueta de prueba",
            ),
            Tool(
                "set_shopify_connection",
                "Configura la conexión con la tienda de Shopify (sin secretos): dominio .myshopify.com, tipo de "
                "autenticación, Client ID, versión de API y activar o desactivar el conector. Los secretos se "
                "piden aparte con ask_user_for_secret.",
                _obj(
                    {
                        "shop": _s("Tienda: nombre o dominio mi-tienda.myshopify.com."),
                        "auth_mode": _s(
                            "client_credentials = app del Dev Dashboard con Client ID y Client secret (recomendado); "
                            "token = token shpat_ de una app antigua del admin.",
                            ["client_credentials", "token"],
                        ),
                        "client_id": _s("Client ID de la app del Dev Dashboard (no es secreto)."),
                        "api_version": _s("Versión de la API de Shopify.", ["2026-10", "2026-07"]),
                        "enabled": _b("Activar el conector: empieza a revisar los pedidos nuevos."),
                    }
                ),
                self.set_shopify_connection,
                "Configurando la conexión con Shopify",
            ),
            Tool(
                "ask_user_for_secret",
                "Abre en la app una ventana segura para que el usuario pegue un dato secreto (Client secret de "
                "Shopify, token shpat_ o contraseña de MQTT). Se guarda en el almacén seguro de Windows y tú nunca lo "
                "ves. Úsala siempre que haga falta un secreto, en vez de pedir que lo escriban en el chat.",
                _obj({"kind": _s("Qué dato secreto pedir.", list(SECRET_KINDS))}, ("kind",)),
                self.ask_user_for_secret,
                "Esperando el dato secreto",
            ),
            Tool(
                "save_secret",
                "Guarda un dato secreto que el usuario escribió en el chat y que la app apartó como [SECRETO_n]. "
                "Úsala cuando veas una referencia [SECRETO_n] y sepas a qué corresponde.",
                _obj(
                    {
                        "secret_ref": _s("La referencia tal cual, p. ej. SECRETO_1."),
                        "kind": _s("A qué corresponde.", list(SECRET_KINDS)),
                    },
                    ("secret_ref", "kind"),
                ),
                self.save_secret,
                "Guardando el dato secreto",
            ),
            Tool(
                "test_shopify_connection",
                "Prueba la conexión con la tienda con lo guardado. Devuelve el nombre de la tienda, los permisos "
                "(scopes) concedidos y los que faltan. Úsala después de configurar la tienda o los secretos, al "
                "activar la compra de guías y cuando algo de Shopify falle.",
                _obj(),
                self.test_shopify_connection,
                "Probando la conexión con Shopify",
            ),
            Tool(
                "set_shopify_printing",
                "Decide qué se imprime en cada pedido nuevo y con qué impresora. buy_shipping_label=true compra "
                "automáticamente la guía de envío de Shopify Shipping (con franqueo, Shopify cobra cada guía) e "
                "imprime su PDF: es la etiqueta que va pegada en el paquete. Activarla abre una ventana de "
                "confirmación; si el usuario no acepta, no se activa. print_order_label es una etiqueta interna de "
                "LabelBridge sin franqueo (opcional, útil para empacar).",
                _obj(
                    {
                        "buy_shipping_label": _b("Comprar e imprimir la guía de Shopify Shipping de cada pedido."),
                        "shipping_label_printer": label_printer,
                        "notify_customer": _b("Shopify avisa al cliente por correo al comprar la guía."),
                        "max_labels_per_day": _i("Límite de guías compradas por día (seguridad)."),
                        "print_order_label": _b("Imprimir también la etiqueta interna de pedido (sin franqueo)."),
                        "order_label_printer": label_printer,
                        "metafield_label": _b("Imprimir etiquetas de otra paquetería guardadas en un metafield."),
                        "metafield_printer": label_printer,
                        "only_paid": _b("Solo pedidos pagados."),
                        "only_unfulfilled": _b("Solo pedidos pendientes de envío."),
                        "required_tag": _s("Solo pedidos con este tag (vacío = todos)."),
                        "tag_after_print": _s("Tag que se agrega al pedido al imprimir (vacío = ninguno)."),
                        "poll_seconds": _i("Cada cuántos segundos se revisan pedidos nuevos (15-3600)."),
                    }
                ),
                self.set_shopify_printing,
                "Configurando qué se imprime en cada pedido",
            ),
            Tool(
                "reprocess_shopify_orders",
                "Vuelve a revisar los pedidos creados en las últimas N horas (0 = solo desde ahora). Con la compra "
                "de guías activa puede comprar guías de esos pedidos: la app pide confirmación.",
                _obj({"hours": _i("Horas hacia atrás (0-720).")}, ("hours",)),
                self.reprocess_shopify_orders,
                "Reprocesando pedidos de Shopify",
            ),
            Tool(
                "set_sender",
                "Remitente y contenido de la etiqueta interna de pedido de LabelBridge. No cambia la guía de Shopify "
                "Shipping (esa usa la dirección de la ubicación de la tienda en Shopify).",
                _obj(
                    {
                        "store_name": _s("Nombre de la tienda."),
                        "sender_address": _s("Dirección desde la que se envía (varias líneas)."),
                        "show_items": _b("Incluir la lista de productos."),
                        "show_qr": _b("Incluir un QR al pedido."),
                        "show_price": _b("Incluir el total."),
                    }
                ),
                self.set_sender,
                "Guardando el remitente",
            ),
            Tool(
                "set_general",
                "Ajustes generales de este PC: nombre, iniciar con Windows, iniciar minimizado, buscar impresoras al "
                "cambiar de red, avisos, reintentos, historial, avisos de actualización y pausar o reanudar la "
                "impresión automática.",
                _obj(
                    {
                        "computer_name": _s("Nombre de este PC en LabelBridge."),
                        "start_with_windows": _b("Iniciar al encender Windows (recomendado)."),
                        "start_minimized": _b("Iniciar en la bandeja del sistema."),
                        "auto_discover": _b("Buscar impresoras al conectarse a una red."),
                        "notify_errors": _b("Avisar si una etiqueta no se puede imprimir."),
                        "notify_printed": _b("Avisar cada vez que se imprime."),
                        "max_retries": _i("Reintentos si la impresora falla (0-20)."),
                        "retention_days": _i("Días de historial (1-365)."),
                        "auto_update_check": _b("Avisar cuando haya una versión nueva."),
                        "paused": _b("true pausa la impresión automática; false la reanuda."),
                    }
                ),
                self.set_general,
                "Guardando los ajustes",
            ),
            Tool(
                "complete_guide_step",
                "Marca como entendido un paso informativo de la guía: «what» (qué se imprime) o «daily» (uso diario: "
                "iniciar con Windows, la app sigue junto al reloj). Úsala cuando el usuario ya lo tenga claro.",
                _obj({"step": _s("Paso de la guía.", ["what", "daily"])}, ("step",)),
                self.complete_guide_step,
                "Actualizando la guía",
            ),
            Tool(
                "get_recent_jobs",
                "Últimos trabajos de impresión con estado, impresora, error y número de rastreo. Úsala cuando algo "
                "no se imprimió o para confirmar que un pedido salió.",
                _obj({"limit": _i("Cuántos (1-30)."), "only_errors": _b("Solo los que fallaron.")}),
                self.get_recent_jobs,
                "Revisando el historial",
            ),
            Tool(
                "reprint_job",
                "Vuelve a imprimir un trabajo del historial (mode=reprint, no compra otra guía) o reintenta uno que "
                "falló (mode=retry).",
                _obj(
                    {
                        "job_id": _s("job_id de get_recent_jobs."),
                        "mode": _s("reprint o retry.", ["reprint", "retry"]),
                        "printer": _s("Alias de otra impresora (opcional, solo reprint)."),
                    },
                    ("job_id",),
                ),
                self.reprint_job,
                "Enviando a imprimir",
            ),
            Tool(
                "set_n8n_connection",
                "Configura el canal en tiempo real con n8n (broker MQTT, p. ej. EMQX Serverless): servidor, puerto, "
                "TLS, usuario, canal y activar. La contraseña se pide con ask_user_for_secret (kind=mqtt_password).",
                _obj(
                    {
                        "enabled": _b("Activar el canal."),
                        "host": _s("Servidor del broker, p. ej. xxxx.ala.us-east-1.emqxsl.com."),
                        "port": _i("Puerto (8883 con TLS)."),
                        "tls": _b("Usar TLS."),
                        "username": _s("Usuario de este PC en el broker."),
                        "channel": _s("Canal (minúsculas y guiones)."),
                    }
                ),
                self.set_n8n_connection,
                "Configurando n8n",
            ),
            Tool(
                "test_n8n_connection",
                "Prueba la conexión con el broker MQTT de n8n con lo guardado.",
                _obj(),
                self.test_n8n_connection,
                "Probando la conexión con n8n",
            ),
            Tool(
                "open_link",
                "Abre en el navegador una página útil: el Dev Dashboard de Shopify (crear la app), un pedido "
                "preliminar nuevo (pedido de prueba), los ajustes de envío de Shopify (paquete predeterminado, "
                "formato de etiqueta), los pedidos o la página de descargas de LabelBridge.",
                _obj(
                    {
                        "target": _s(
                            "Página.",
                            [
                                "shopify_dev_dashboard",
                                "shopify_new_draft_order",
                                "shopify_shipping_settings",
                                "shopify_orders",
                                "labelbridge_downloads",
                            ],
                        )
                    },
                    ("target",),
                ),
                self.open_link,
                "Abriendo el navegador",
            ),
            Tool(
                "copy_shopify_scopes",
                "Copia al portapapeles los permisos (scopes) que hay que pegar en la app del Dev Dashboard. Con "
                "for_shipping_labels=true incluye los necesarios para comprar guías.",
                _obj({"for_shipping_labels": _b("Incluir los permisos para comprar guías (recomendado).")}),
                self.copy_shopify_scopes,
                "Copiando los permisos",
            ),
        ]


def _loggable(args: Any) -> Any:
    """Para el registro: nunca se escriben valores de secretos (las herramientas no los reciben, pero por si acaso)."""
    if not isinstance(args, dict):
        return args
    return {k: ("•••" if "secret" in k or "password" in k or "token" in k else v) for k, v in args.items()}
