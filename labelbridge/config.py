"""Configuración persistente (JSON). Los secretos NO van aquí: ver secret_store.py."""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

# id -> (nombre visible, ancho mm, alto mm). "auto" = tamaño del documento / papel de la impresora.
MEDIA_PRESETS: dict[str, tuple[str, float, float]] = {
    "4x6": ("4 × 6 in (101.6 × 152.4 mm) · envío estándar", 101.6, 152.4),
    "4x4": ("4 × 4 in (101.6 × 101.6 mm)", 101.6, 101.6),
    "4x3": ("4 × 3 in (101.6 × 76.2 mm)", 101.6, 76.2),
    "4x2": ("4 × 2 in (101.6 × 50.8 mm)", 101.6, 50.8),
    "100x150": ("100 × 150 mm", 100.0, 150.0),
    "100x100": ("100 × 100 mm", 100.0, 100.0),
    "2.25x1.25": ("2.25 × 1.25 in (57 × 32 mm)", 57.15, 31.75),
    "letter": ("Carta (8.5 × 11 in)", 215.9, 279.4),
    "a4": ("A4 (210 × 297 mm)", 210.0, 297.0),
    "auto": ("Automático (tamaño del documento / papel de la impresora)", 0.0, 0.0),
}

LABEL_MEDIA = {"4x6", "4x4", "4x3", "4x2", "100x150", "100x100", "2.25x1.25"}
# Tamaño de la guía de envío: se imprime a ese tamaño real en cualquier papel (rollo, carta o A4).
LABEL_SIZES = {
    "4x6": "4 × 6 in (tamaño real)",
    "100x150": "100 × 150 mm (tamaño real)",
    "fit": "Ajustar a la hoja",
}

PRINTER_KINDS = {"system": "Windows (driver)", "network": "Red (IP directa)", "file": "Archivo (pruebas)"}
LANGUAGES = {"driver": "Driver del sistema", "zpl": "ZPL (Zebra y compatibles)", "pdf": "PDF directo"}


def new_id() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class PrinterProfile:
    id: str = field(default_factory=new_id)
    alias: str = ""
    kind: str = "system"  # system | network | file
    system_name: str = ""  # kind=system
    host: str = ""  # kind=network
    port: int = 9100
    protocol: str = "raw"  # raw (9100) | ipp | ipps
    ipp_path: str = "/ipp/print"
    language: str = "driver"  # driver | zpl | pdf
    media: str = "4x6"
    dpi: int = 203
    rotate: str = "auto"  # auto | 0 | 90 | 180 | 270
    crop: str = "auto"  # auto | none  (recortar márgenes blancos de PDFs grandes)
    output_dir: str = ""  # kind=file
    tls_fingerprint: str = ""  # ipps: huella del certificado fijada en el primer uso
    enabled: bool = True

    def media_mm(self) -> tuple[float, float] | None:
        _, w, h = MEDIA_PRESETS.get(self.media, MEDIA_PRESETS["4x6"])
        return (w, h) if w and h else None

    def target_text(self) -> str:
        if self.kind == "system":
            return self.system_name
        if self.kind == "network":
            scheme = {"raw": "tcp", "ipp": "ipp", "ipps": "ipps"}.get(self.protocol, "tcp")
            path = self.ipp_path if self.protocol in ("ipp", "ipps") else ""
            return f"{scheme}://{self.host}:{self.port}{path}"
        return self.output_dir or "(carpeta de salida)"

    def display_name(self) -> str:
        return self.alias or self.target_text()


@dataclass
class GeneralSettings:
    agent_name: str = field(default_factory=lambda: socket.gethostname().split(".")[0][:40] or "PC")
    start_with_windows: bool = True
    start_minimized: bool = True
    notify_printed: bool = False
    notify_errors: bool = True
    paused: bool = False
    auto_discover: bool = True
    guide_acknowledged: str = ""  # pasos informativos de la guía marcados como «Entendido»
    auto_update_check: bool = True
    skipped_version: str = ""
    default_printer_id: str = ""
    max_retries: int = 5
    retention_days: int = 30


@dataclass
class LabelSettings:
    """Etiqueta de pedido generada por LabelBridge (no es una etiqueta de paquetería con franqueo)."""

    store_name: str = ""
    sender_address: str = ""
    show_items: bool = True
    show_qr: bool = True
    show_price: bool = False


CARRIERS = ("USPS", "FedEx", "UPS", "DHL")


@dataclass
class ShippingRule:
    """Por qué paquetería sale un pedido y si LabelBridge compra la guía sola. Se cumple si el método de envío
    contiene `method` (vacío: cualquiera) y las piezas suman más de `over` (0: cualquier valor)."""

    method: str = ""  # texto del método de envío (sin distinguir mayúsculas), p. ej. "Express"
    carrier: str = "USPS"  # CARRIERS u otra
    auto: bool = True  # False: la guía se compra a mano (FedEx no se puede por API) y el pedido pide acción
    over: float = 0.0  # valor de las piezas (sin envío ni impuestos) a partir del cual se aplica


@dataclass
class ShopifySettings:
    enabled: bool = False
    shop: str = ""  # mi-tienda.myshopify.com
    auth_mode: str = "client_credentials"  # client_credentials | token
    client_id: str = ""
    api_version: str = "2026-10"
    poll_seconds: int = 30
    only_paid: bool = True
    only_unfulfilled: bool = True
    required_tag: str = ""
    # La etiqueta principal es la guía de Shopify Shipping (cuesta dinero: se activa a mano, con confirmación).
    # La etiqueta de pedido de LabelBridge es opcional y no lleva franqueo.
    print_order_label: bool = False
    order_label_printer: str = ""  # alias; vacío = predeterminada
    buy_shipping_label: bool = False
    shipping_label_printer: str = ""
    label_size: str = "4x6"  # LABEL_SIZES: la guía sale a 4×6 in aunque la impresora tenga hojas carta o A4
    # Paquetería de cada pedido según su método de envío y valor; sin regla, default_carrier con compra automática.
    # Las piezas de más de US$1000 salen por FedEx, que se compra a mano.
    shipping_rules: list[ShippingRule] = field(default_factory=lambda: [ShippingRule("", "FedEx", False, 1000.0)])
    default_carrier: str = "USPS"
    notify_customer: bool = False
    max_labels_per_day: int = 50
    metafield_label: bool = False
    metafield_namespace: str = "custom"
    metafield_key: str = "label_url"
    metafield_printer: str = ""
    tag_after_print: str = ""


def slugify(text: str, fallback: str = "pc") -> str:
    """Identificador seguro para temas MQTT: minúsculas, números y guiones."""
    import unicodedata

    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")[:40]
    return slug or fallback


@dataclass
class MqttSettings:
    """Canal en tiempo real: n8n publica cuando ocurre un evento y este PC lo recibe al instante."""

    enabled: bool = False
    host: str = ""  # p. ej. xxxxxxxx.ala.us-east-1.emqxsl.com (EMQX Serverless)
    port: int = 8883
    tls: bool = True
    username: str = ""
    channel: str = field(default_factory=lambda: slugify(socket.gethostname().split(".")[0]))
    client_suffix: str = field(default_factory=lambda: uuid.uuid4().hex[:6])


@dataclass
class LocalApiSettings:
    enabled: bool = True
    port: int = 7300
    allow_lan: bool = False
    cors_origins: str = ""
    allowed_hosts: str = ""  # nombres extra aceptados en la cabecera Host (p. ej. pc-bodega.miempresa.lan)


@dataclass
class SecuritySettings:
    """Límites y reglas de seguridad. Se aplican al momento al guardarlos (Ajustes → Seguridad)."""

    api_requests_per_minute: int = 120  # peticiones a la API local por minuto y por equipo
    api_max_auth_failures: int = 10  # claves incorrectas seguidas antes de bloquear a ese equipo
    api_block_minutes: int = 15
    api_max_connections: int = 16  # conexiones simultáneas a la API local
    jobs_per_minute: int = 60  # trabajos nuevos por minuto desde la API local o n8n
    max_copies: int = 20  # copias máximas por trabajo
    private_urls: str = "local_api"  # descargas desde la red interna: never | local_api | always
    require_https: bool = True  # descargas desde internet solo por https
    max_message_mb: int = 30  # tamaño máximo de un mensaje de n8n (MQTT)


ASSISTANT_EFFORTS = {"low": "Rápido (menos tokens)", "medium": "Equilibrado", "high": "Profundo (más tokens)"}


@dataclass
class AssistantSettings:
    """Asistente de configuración (Ajustes → Asistente). Funciona con cualquier proveedor de IA: los límites y
    permisos de abajo se aplican igual a todos."""

    enabled: bool = True
    provider: str = "anthropic"  # ver assistant/providers/catalog.py
    model: str = "claude-opus-5-5"
    base_url: str = ""  # "" = la dirección oficial del proveedor
    # low | medium | high: cuánto razona (y cuántos tokens gasta). Con las pruebas reales (devtools/assistant_eval.py)
    # «low» acertó igual que «medium» con ~25 % menos tokens.
    effort: str = "low"
    # USD por millón de tokens para el límite diario; negativo = automático (precio público del modelo).
    price_input: float = -1.0
    price_output: float = -1.0
    approval: str = "changes"  # changes: pide permiso para cualquier cambio · critical: solo compras y borrados
    daily_budget_usd: float = 2.0  # gasto estimado máximo por día (0 = sin límite)
    messages_per_hour: int = 40
    max_message_chars: int = 4000
    max_turns: int = 40  # mensajes por conversación antes de pedir empezar una nueva


@dataclass
class Settings:
    version: int = 1
    general: GeneralSettings = field(default_factory=GeneralSettings)
    labels: LabelSettings = field(default_factory=LabelSettings)
    printers: list[PrinterProfile] = field(default_factory=list)
    shopify: ShopifySettings = field(default_factory=ShopifySettings)
    mqtt: MqttSettings = field(default_factory=MqttSettings)
    local_api: LocalApiSettings = field(default_factory=LocalApiSettings)
    security: SecuritySettings = field(default_factory=SecuritySettings)
    assistant: AssistantSettings = field(default_factory=AssistantSettings)


def _coerce(value: Any, default: Any) -> Any:
    """Convierte valores del JSON al tipo del valor por defecto, tolerando basura."""
    try:
        if isinstance(default, bool):
            return bool(value)
        if isinstance(default, int) and not isinstance(default, bool):
            return int(value)
        if isinstance(default, float):
            return float(value)
        if isinstance(default, str):
            return "" if value is None else str(value)
    except (TypeError, ValueError):
        return default
    return value


def _from_dict(cls: type, raw: Any) -> Any:
    obj = cls()
    if not isinstance(raw, dict):
        return obj
    for f in fields(cls):
        if f.name not in raw:
            continue
        current = getattr(obj, f.name)
        value = raw[f.name]
        if is_dataclass(current):
            setattr(obj, f.name, _from_dict(type(current), value))
        elif f.name == "printers":
            items = value if isinstance(value, list) else []
            setattr(obj, f.name, [_from_dict(PrinterProfile, item) for item in items])
        elif f.name == "shipping_rules":
            items = value if isinstance(value, list) else []
            setattr(obj, f.name, [_from_dict(ShippingRule, item) for item in items])
        else:
            setattr(obj, f.name, _coerce(value, current))
    return obj


class ConfigManager:
    """Carga/guarda la configuración y avisa a los suscriptores cuando cambia.

    Con `protector` la configuración se guarda cifrada en config.enc (una config.json antigua se migra y
    se borra). Sin él (pruebas, herramientas) se usa config.json en texto.
    """

    CONTEXT = "config"

    def __init__(self, path: Path, protector=None) -> None:
        self.path = path
        self.protector = protector
        self.store_path = path.with_suffix(".enc") if protector is not None else path
        self._lock = threading.RLock()
        self._listeners: list[Callable[[str], None]] = []
        self.data: Settings = self._load()

    def _read(self, path: Path) -> dict:
        raw = path.read_bytes()
        if self.protector is not None:
            raw = self.protector.decrypt(raw, self.CONTEXT)
        return json.loads(raw.decode("utf-8"))

    def _load(self) -> Settings:
        source = self.store_path if self.store_path.exists() else self.path
        if not source.exists():
            return Settings()
        try:
            data = _from_dict(Settings, self._read(source))
        except Exception:
            backup = source.with_name(f"{source.stem}.corrupt-{int(time.time())}{source.suffix}")
            log.exception("%s dañado o ilegible; se guarda copia en %s y se usan valores por defecto", source.name, backup)
            try:
                source.replace(backup)
            except OSError:
                pass
            return Settings()
        if source != self.store_path:  # config.json en texto de una versión anterior: se cifra y se borra
            self.data = data
            self._write()
            try:
                source.unlink()
            except OSError:
                log.warning("No se pudo borrar %s después de cifrarla", source.name)
            log.info("Configuración cifrada en %s", self.store_path.name)
        return data

    def _write(self) -> None:
        with self._lock:
            raw = json.dumps(asdict(self.data), indent=2, ensure_ascii=False).encode("utf-8")
            if self.protector is not None:
                raw = self.protector.encrypt(raw, self.CONTEXT)
            tmp = self.store_path.with_suffix(".tmp")
            tmp.write_bytes(raw)
            os.replace(tmp, self.store_path)

    def subscribe(self, callback: Callable[[str], None]) -> None:
        self._listeners.append(callback)

    def save(self, section: str = "all") -> None:
        self._write()
        for callback in list(self._listeners):
            try:
                callback(section)
            except Exception:
                log.exception("Error notificando cambio de configuración (%s)", section)

    # --- impresoras -------------------------------------------------------
    def printer_by_id(self, printer_id: str) -> PrinterProfile | None:
        return next((p for p in self.data.printers if p.id == printer_id), None)

    def default_printer(self) -> PrinterProfile | None:
        enabled = [p for p in self.data.printers if p.enabled]
        chosen = self.printer_by_id(self.data.general.default_printer_id)
        if chosen and chosen.enabled:
            return chosen
        return enabled[0] if enabled else None

    def find_printer(self, name: str) -> PrinterProfile | None:
        key = name.strip().casefold()
        if not key:
            return None
        for attr in ("alias", "system_name", "id"):
            for profile in self.data.printers:
                if profile.enabled and str(getattr(profile, attr)).casefold() == key:
                    return profile
        return None
