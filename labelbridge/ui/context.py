"""Objetos compartidos por todas las pantallas y puente de eventos hacia el hilo de la UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, Signal

from ..config import ConfigManager
from ..events import Events
from ..jobstore import JobStore
from ..printing.engine import PrintEngine
from ..secret_store import SecretStore
from ..services import Services


class QtBridge(QObject):
    """Las señales Qt cruzan hilos de forma segura: los servicios emiten y la UI recibe."""

    event = Signal(str, object)
    config_saved = Signal(str)
    log_line = Signal(int, str)

    def __init__(self, events: Events, cfg: ConfigManager) -> None:
        super().__init__()
        events.subscribe(self._forward_event)
        cfg.subscribe(self._forward_config)

    def _forward_event(self, kind: str, data: Any) -> None:
        self.event.emit(kind, data)

    def _forward_config(self, section: str) -> None:
        self.config_saved.emit(section)

    def forward_log(self, level: int, line: str) -> None:
        self.log_line.emit(level, line)


@dataclass
class AppContext:
    cfg: ConfigManager
    secrets: SecretStore
    store: JobStore
    engine: PrintEngine
    services: Services
    events: Events
    bridge: QtBridge
