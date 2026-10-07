"""Registro de auditoría: qué se aprobó, qué se rechazó y qué se bloqueó (logs/seguridad.log, JSON por línea)."""

from __future__ import annotations

import json
import logging
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

from .redact import redact

log = logging.getLogger(__name__)

MAX_BYTES = 2_000_000
BACKUPS = 3
DENIED = {"deny", "block", "declined"}


class AuditLog:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.recent: deque[dict] = deque(maxlen=500)

    def record(self, channel: str, action: str, decision: str, *, client: str = "", detail: str = "") -> dict:
        """channel: api | mqtt | shopify | asistente | descarga… · decision: allow | deny | block | approved | declined."""
        entry = {
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "channel": channel,
            "action": action,
            "decision": decision,
            "client": client,
            "detail": redact(detail)[:500],
        }
        self.recent.append(entry)
        if decision in DENIED:
            log.warning("Seguridad: %s %s → %s %s %s", channel, action, decision, client, entry["detail"])
        if self.path is not None:
            line = json.dumps(entry, ensure_ascii=False) + "\n"
            with self._lock:
                try:
                    self._rotate()
                    with self.path.open("a", encoding="utf-8") as handle:
                        handle.write(line)
                except OSError:
                    log.warning("No se pudo escribir el registro de seguridad", exc_info=True)
        return entry

    def _rotate(self) -> None:
        if not self.path.exists() or self.path.stat().st_size < MAX_BYTES:
            return
        for index in range(BACKUPS, 0, -1):
            older = self.path.with_name(f"{self.path.name}.{index}")
            newer = self.path if index == 1 else self.path.with_name(f"{self.path.name}.{index - 1}")
            if newer.exists():
                newer.replace(older)
