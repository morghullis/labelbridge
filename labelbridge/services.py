"""Arranca, detiene y reinicia los servicios cuando cambia la configuración."""

from __future__ import annotations

import logging
import secrets as pysecrets
import threading
import time

from . import events as ev
from .config import ConfigManager
from .discovery import DiscoveredPrinter, discover, local_ipv4_addresses
from .connectors.base import BackgroundService
from .connectors.local_api import LocalApiService
from .connectors.mqtt import MqttService
from .connectors.shopify import ShopifyService
from .jobstore import JobStore
from .printing.engine import PrintEngine
from .secret_store import LOCAL_API_KEY, SecretStore
from .updater import UpdateService

log = logging.getLogger(__name__)


def generate_key(prefix: str = "lb") -> str:
    return f"{prefix}_{pysecrets.token_urlsafe(32)}"


DISCOVERY = "discovery"  # evento con la lista de impresoras encontradas


class NetworkWatcher(BackgroundService):
    """Busca impresoras en segundo plano cada vez que el PC se conecta a una red (Wi‑Fi/LAN)."""

    name = "network"
    label = "Red"

    def __init__(self, cfg: ConfigManager, events: ev.Events) -> None:
        super().__init__(events)
        self.cfg = cfg
        self.results: list[DiscoveredPrinter] = []
        self.scanned_at = 0.0
        self._announced: set[str] = set()

    def enabled(self) -> bool:
        return self.cfg.data.general.auto_discover

    def run(self, stop: threading.Event, wake: threading.Event) -> None:
        if not self.sleep(stop, wake, 8):  # deja arrancar la app primero
            return
        last_networks: tuple[str, ...] | None = None
        while not stop.is_set():
            networks = tuple(sorted(".".join(ip.split(".")[:3]) for ip in local_ipv4_addresses()))
            if networks and networks != last_networks:
                last_networks = networks
                self.set_status("connecting", "Buscando impresoras en la red…")
                found = discover(cancel=stop)
                if stop.is_set():
                    break
                self.publish(found)
                self.set_status("ok", f"{len(found)} impresora(s) en la red · {time.strftime('%H:%M')}")
            elif not networks:
                last_networks = None
                self.set_status("warn", "Sin conexión de red")
            if not self.sleep(stop, wake, 60):
                break

    def publish(self, found: list[DiscoveredPrinter]) -> None:
        self.results = found
        self.scanned_at = time.time()
        self.events.emit(DISCOVERY, found)
        configured = {p.host for p in self.cfg.data.printers if p.kind == "network"}
        new = [p for p in found if p.ip not in configured and p.ip not in self._announced]
        self._announced.update(p.ip for p in new)
        if new:
            names = ", ".join(f"{p.title} ({p.ip})" for p in new[:3])
            more = f" y {len(new) - 3} más" if len(new) > 3 else ""
            self.events.notify("Impresoras encontradas en la red", f"{names}{more}. Agrégalas en Impresoras → En la red.")


class Services:
    def __init__(
        self, cfg: ConfigManager, secrets: SecretStore, store: JobStore, engine: PrintEngine, events: ev.Events
    ) -> None:
        self.cfg = cfg
        self.secrets = secrets
        self.local_api = LocalApiService(cfg, secrets, store, engine, events)
        self.mqtt = MqttService(cfg, secrets, store, engine, events)
        self.shopify = ShopifyService(cfg, secrets, store, engine, events)
        self.network = NetworkWatcher(cfg, events)
        self.updates = UpdateService(cfg, events)
        self.by_section: dict[str, BackgroundService] = {
            "local_api": self.local_api,
            "mqtt": self.mqtt,
            "shopify": self.shopify,
            "general": self.network,
        }
        if not secrets.get(LOCAL_API_KEY):
            secrets.set(LOCAL_API_KEY, generate_key())
        cfg.subscribe(self._on_config_saved)

    def all(self) -> list[BackgroundService]:
        return [*self.by_section.values(), self.updates]

    def start(self) -> None:
        for service in self.all():
            if service.enabled():
                service.start()
            else:
                service.set_status("off", "Desactivado")

    def stop(self) -> None:
        for service in self.all():
            try:
                service.stop(timeout=3)
            except Exception:
                log.exception("Error deteniendo %s", service.name)

    def restart(self, section: str) -> None:
        service = self.by_section.get(section)
        if service is not None:
            # En segundo plano: detener puede esperar a que termine una petición HTTP.
            threading.Thread(target=service.restart, name=f"restart-{section}", daemon=True).start()

    def _on_config_saved(self, section: str) -> None:
        if section == "all":
            for name in self.by_section:
                self.restart(name)
        elif section == "general":
            # "general" también se guarda al pausar: solo se reinicia lo que cambió de estado.
            if self.network.enabled() != self.network.running:
                self.restart(section)
            if self.updates.enabled() != self.updates.running:
                threading.Thread(target=self.updates.restart, name="restart-updates", daemon=True).start()
        elif section in self.by_section:
            self.restart(section)
