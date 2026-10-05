"""Bus de eventos sencillo, seguro entre hilos. La UI lo reemite como señal Qt."""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

log = logging.getLogger(__name__)

Listener = Callable[[str, Any], None]

# Tipos de evento
JOB = "job"  # dict del trabajo (creado/actualizado)
CONNECTOR = "connector"  # {"name", "state", "message"}
NOTIFY = "notify"  # {"title", "message", "level"}
PAUSED = "paused"  # bool
CONFIG_EXTERNAL = "config_external"  # sección que cambió el asistente: las pantallas recargan sus campos
ORDERS = "orders"  # {"order", "state"}: cambió lo que LabelBridge sabe de un pedido (pestaña Pedidos)


class Events:
    def __init__(self) -> None:
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()

    def subscribe(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def emit(self, kind: str, data: Any = None) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(kind, data)
            except Exception:
                log.exception("Error en listener de eventos (%s)", kind)

    def notify(self, title: str, message: str, level: str = "info", kind: str = "") -> None:
        """Aviso para la persona. `kind` (NOTIFY_EVENTS) decide en Ajustes si sale el aviso y con qué sonido."""
        self.emit(NOTIFY, {"title": title, "message": message, "level": level, "kind": kind})
