"""HTTP para las API de IA sin SDK (OpenAI, Gemini y compatibles): JSON, TLS del sistema, reintentos y nada de
redirecciones (la clave solo viaja al servidor configurado)."""

from __future__ import annotations

import email.utils
import json
import logging
import random
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from ...net import USER_AGENT, ssl_context
from .base import ProviderError

log = logging.getLogger(__name__)

MAX_BYTES = 16 * 1024 * 1024
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 529}
MAX_WAIT = 20.0  # segundos máximos de espera entre reintentos

# (método, url, cabeceras, cuerpo, timeout) -> (código, cabeceras en minúsculas, cuerpo)
Transport = Callable[[str, str, dict, "bytes | None", float], tuple[int, dict, bytes]]


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None  # urllib devuelve el 3xx como error y no sigue la redirección


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=ssl_context()))
    try:
        with opener.open(request, timeout=timeout) as response:
            data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ProviderError("invalid_response", "La respuesta del servidor es demasiado grande")
            return response.status, {k.lower(): v for k, v in response.headers.items()}, data
    except urllib.error.HTTPError as exc:
        data = exc.read(256 * 1024) if exc.fp else b""
        return exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, data
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise ProviderError("timeout", "El servidor tardó demasiado en responder") from None
        raise ProviderError("network", str(exc.reason)) from None
    except (TimeoutError, socket.timeout):
        raise ProviderError("timeout", "El servidor tardó demasiado en responder") from None
    except OSError as exc:
        raise ProviderError("network", str(exc)) from None


@dataclass
class HttpReply:
    status: int
    headers: dict
    data: Any  # JSON ya leído (None si el cuerpo no era JSON)
    text: str


def _retry_after(headers: dict) -> float:
    try:
        if "retry-after-ms" in headers:
            return float(headers["retry-after-ms"]) / 1000
        value = headers.get("retry-after", "")
        if not value:
            return 0.0
        try:
            return float(value)
        except ValueError:
            when = email.utils.parsedate_to_datetime(value)
            return max(0.0, when.timestamp() - time.time())
    except (TypeError, ValueError, OverflowError):
        return 0.0


class HttpJson:
    """Peticiones JSON con hasta `retries` reintentos para fallos pasajeros (red, 429 de ritmo, 5xx)."""

    def __init__(self, transport: Transport | None = None, retries: int = 2, sleep: Callable[[float], None] | None = None):
        self.transport = transport or urllib_transport
        self.retries = retries
        self.sleep = sleep

    def call(
        self,
        method: str,
        url: str,
        headers: dict,
        body: Any = None,
        timeout: float = 600.0,
        retryable: Callable[[HttpReply], "bool | float"] | None = None,
    ) -> HttpReply:
        """`retryable` decide si un error se reintenta; si devuelve segundos, se espera eso (lo pide el servidor)."""
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        all_headers = {"User-Agent": USER_AGENT, "Accept": "application/json", **headers}
        if payload is not None:
            all_headers["Content-Type"] = "application/json"
        attempt = 0
        while True:
            try:
                status, reply_headers, raw = self.transport(method, url, all_headers, payload, timeout)
            except ProviderError as exc:
                if exc.kind not in ("network", "timeout") or attempt >= self.retries:
                    raise
                wait = self._backoff(attempt, 0.0)
            else:
                text = raw.decode("utf-8", "replace")
                try:
                    data = json.loads(text) if text.strip() else None
                except ValueError:
                    data = None
                reply = HttpReply(status, reply_headers, data, text)
                if 300 <= status < 400:
                    raise ProviderError("redirect", f"El servidor redirige a otra dirección (HTTP {status})", status)
                if status < 300:
                    if data is None:
                        raise ProviderError("invalid_response", "El servidor no respondió con JSON", status)
                    return reply
                retry = status in RETRY_STATUS if retryable is None else retryable(reply)
                if not retry or attempt >= self.retries:
                    return reply
                suggested = _retry_after(reply_headers)
                if not isinstance(retry, bool) and retry > 0:
                    suggested = float(retry)
                wait = self._backoff(attempt, suggested)
            attempt += 1
            log.info("Asistente: reintento %s en %.1f s", attempt, wait)
            (self.sleep or _sleep)(wait)

    @staticmethod
    def _backoff(attempt: int, suggested: float) -> float:
        if suggested > 0:
            return min(MAX_WAIT, suggested)
        return min(MAX_WAIT, 1.5 * (2**attempt) + random.uniform(0, 0.5))
