"""Límites de velocidad (cubeta de fichas) y bloqueo temporal tras intentos fallidos.

Los límites se leen de los ajustes en cada consulta: si el usuario los cambia, se aplican al momento.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

MAX_KEYS = 4096  # claves distintas recordadas (IPs, canales); las inactivas se olvidan


class RateLimiter:
    """Permite `per_minute` eventos por minuto y clave, con ráfagas de hasta ese mismo número."""

    def __init__(self, per_minute: Callable[[], int], clock: Callable[[], float] = time.monotonic) -> None:
        self._per_minute = per_minute
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, float]] = {}  # clave -> (fichas, instante)

    def allow(self, key: str, cost: float = 1.0) -> tuple[bool, float]:
        """(permitido, segundos a esperar si no)."""
        limit = max(1, int(self._per_minute()))
        rate = limit / 60.0
        now = self._clock()
        with self._lock:
            tokens, updated = self._buckets.get(key, (float(limit), now))
            tokens = min(float(limit), tokens + (now - updated) * rate)
            if tokens >= cost:
                self._buckets[key] = (tokens - cost, now)
                allowed, wait = True, 0.0
            else:
                self._buckets[key] = (tokens, now)
                allowed, wait = False, (cost - tokens) / rate
            if len(self._buckets) > MAX_KEYS:
                self._forget_idle(now, limit / rate)
        return allowed, wait

    def _forget_idle(self, now: float, full_after: float) -> None:
        for key, (_tokens, updated) in list(self._buckets.items()):
            if now - updated > full_after:
                del self._buckets[key]


class FailureTracker:
    """Tras `max_failures` fallos en `window` segundos, bloquea la clave durante `block_seconds`."""

    def __init__(
        self,
        max_failures: Callable[[], int],
        block_seconds: Callable[[], float],
        window: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_failures = max_failures
        self._block_seconds = block_seconds
        self._window = window
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}
        self._blocked_until: dict[str, float] = {}

    def blocked_for(self, key: str) -> float:
        with self._lock:
            remaining = self._blocked_until.get(key, 0.0) - self._clock()
            if remaining <= 0:
                self._blocked_until.pop(key, None)
                return 0.0
            return remaining

    def failure(self, key: str) -> bool:
        """Registra un fallo. Devuelve True si con este fallo la clave queda bloqueada."""
        now = self._clock()
        with self._lock:
            recent = [t for t in self._failures.get(key, []) if now - t < self._window]
            recent.append(now)
            if len(recent) >= max(1, int(self._max_failures())):
                self._failures.pop(key, None)
                self._blocked_until[key] = now + max(1.0, float(self._block_seconds()))
                return True
            self._failures[key] = recent
            if len(self._failures) > MAX_KEYS:
                for old in list(self._failures)[: len(self._failures) // 2]:
                    del self._failures[old]
            return False

    def success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
