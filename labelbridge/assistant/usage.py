"""Consumo del asistente: tokens y gasto estimado por día, con límite diario y de mensajes por hora.

Es igual para todos los proveedores de IA: el costo de cada llamada lo calcula pricing.py.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import date
from typing import Callable

from .providers import Usage

FIELDS = ("input", "output", "cache_write", "cache_read", "calls", "cost")


class UsageMeter:
    """Guarda el consumo del día en la base de datos local (sobrevive a reinicios)."""

    def __init__(self, store, clock: Callable[[], float] = time.time) -> None:
        self.store = store
        self._clock = clock
        self._lock = threading.Lock()
        self._messages: deque[float] = deque()

    def _key(self) -> str:
        return f"assistant:usage:{date.fromtimestamp(self._clock()).isoformat()}"

    def today(self) -> dict[str, float]:
        try:
            data = json.loads(self.store.kv_get(self._key()) or "{}")
        except json.JSONDecodeError:
            data = {}
        return {field: float(data.get(field, 0)) for field in FIELDS}

    def record(self, usage: Usage, cost: float) -> float:
        with self._lock:
            data = self.today()
            data["input"] += usage.input
            data["output"] += usage.output
            data["cache_write"] += usage.cache_write
            data["cache_read"] += usage.cache_read
            data["calls"] += 1
            data["cost"] = round(data["cost"] + cost, 6)
            self.store.kv_set(self._key(), json.dumps(data))
        return cost

    def admit_message(self, settings) -> str:
        """"" si se puede enviar otro mensaje; si no, el motivo para el usuario."""
        budget = float(settings.daily_budget_usd or 0)
        if budget > 0 and self.today()["cost"] >= budget:
            return (
                f"Se alcanzó el límite diario del asistente (≈ {budget:.2f} USD). Vuelve mañana o súbelo en "
                "Ajustes → Asistente."
            )
        now = self._clock()
        with self._lock:
            while self._messages and now - self._messages[0] > 3600:
                self._messages.popleft()
            if len(self._messages) >= max(1, int(settings.messages_per_hour)):
                wait = int((3600 - (now - self._messages[0])) // 60) + 1
                return f"Llegaste al máximo de {settings.messages_per_hour} mensajes por hora. Prueba en {wait} min."
            self._messages.append(now)
        return ""
