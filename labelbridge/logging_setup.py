"""Registro en archivo rotativo + memoria (para la pestaña Registro de la UI)."""

from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
from collections import deque
from typing import Callable

from .paths import is_frozen, logs_dir
from .security.redact import RedactingFormatter

LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class MemoryLogHandler(logging.Handler):
    def __init__(self, capacity: int = 2000) -> None:
        super().__init__()
        self.lines: deque[tuple[int, str]] = deque(maxlen=capacity)
        self._listeners: list[Callable[[int, str], None]] = []
        self._lock_listeners = threading.Lock()

    def add_listener(self, callback: Callable[[int, str], None]) -> None:
        with self._lock_listeners:
            self._listeners.append(callback)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:  # noqa: BLE001  logging decide qué hacer con un registro que no se puede formatear
            self.handleError(record)
            return
        self.lines.append((record.levelno, line))
        with self._lock_listeners:
            listeners = list(self._listeners)
        for callback in listeners:
            try:
                callback(record.levelno, line)
            except Exception:  # noqa: BLE001  una pantalla que falla al mostrar el registro no corta el registro
                self.handleError(record)


memory_handler = MemoryLogHandler()


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if getattr(root, "_labelbridge_configured", False):
        return
    root.setLevel(level)
    # Claves, tokens, firmas de URLs y correos se borran antes de escribir el registro.
    formatter = RedactingFormatter(LOG_FORMAT, DATE_FORMAT)

    file_handler = logging.handlers.RotatingFileHandler(
        logs_dir() / "labelbridge.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    memory_handler.setFormatter(formatter)
    root.addHandler(memory_handler)

    if not is_frozen() and sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)

    # Librerías ruidosas
    for noisy in ("zeroconf", "urllib3", "httpx2", "httpcore2", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._labelbridge_configured = True  # type: ignore[attr-defined]
