"""Consumo del asistente: tokens y gasto estimado por día, con límite diario y de mensajes por hora."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import date
from typing import Any, Callable

# Precio público por millón de tokens (USD): entrada, salida, escritura de caché (5 min), lectura de caché.
# Es una estimación para el límite diario; la factura real está en platform.claude.com → Usage.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.0, 20.0, 5.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 2.50, 0.20),
}
FIELDS = ("input", "output", "cache_write", "cache_read", "calls", "cost")


def cost_of(model: str, usage: Any) -> float:
    prices = PRICES.get(model, PRICES["claude-opus-5-5"])
    tokens = (
        getattr(usage, "input_tokens", 0) or 0,
        getattr(usage, "output_tokens", 0) or 0,
        getattr(usage, "cache_creation_input_tokens", 0) or 0,
        getattr(usage, "cache_read_input_tokens", 0) or 0,
    )
    return sum(count * price for count, price in zip(tokens, prices)) / 1_000_000


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

    def record(self, model: str, usage: Any) -> float:
        cost = cost_of(model, usage)
        with self._lock:
            data = self.today()
            data["input"] += getattr(usage, "input_tokens", 0) or 0
            data["output"] += getattr(usage, "output_tokens", 0) or 0
            data["cache_write"] += getattr(usage, "cache_creation_input_tokens", 0) or 0
            data["cache_read"] += getattr(usage, "cache_read_input_tokens", 0) or 0
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
