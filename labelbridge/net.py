"""Cliente HTTP mínimo (stdlib) con TLS del sistema + certifi, límites y errores claros."""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from . import APP_NAME, __version__

USER_AGENT = f"{APP_NAME}/{__version__}"
DEFAULT_MAX_BYTES = 30 * 1024 * 1024

_ssl_context: ssl.SSLContext | None = None


class NetworkError(Exception):
    """No se pudo conectar (DNS, timeout, TLS...)."""


class HttpError(Exception):
    def __init__(self, status: int, body: bytes, url: str) -> None:
        self.status = status
        self.body = body
        self.url = url
        snippet = body[:300].decode("utf-8", "replace").strip()
        super().__init__(f"HTTP {status}" + (f": {snippet}" if snippet else ""))


@dataclass
class Response:
    status: int
    body: bytes
    url: str
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()

    def json(self) -> Any:
        if not self.body.strip():
            return None
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NetworkError(f"La respuesta no es JSON válido ({self.url})") from exc


def ssl_context() -> ssl.SSLContext:
    global _ssl_context
    if _ssl_context is None:
        ctx = ssl.create_default_context()
        try:
            import certifi

            ctx.load_verify_locations(certifi.where())
        except Exception:
            pass
        _ssl_context = ctx
    return _ssl_context


def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    data: bytes | None = None,
    timeout: float = 20.0,
    max_bytes: int = DEFAULT_MAX_BYTES,
    raise_for_status: bool = True,
) -> Response:
    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    body = data
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, method=method.upper(), headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
            content = resp.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise NetworkError(f"La respuesta supera {max_bytes // (1024 * 1024)} MB")
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return Response(resp.status, content, resp.geturl(), resp_headers)
    except urllib.error.HTTPError as exc:
        content = exc.read(64 * 1024) if exc.fp else b""
        if raise_for_status:
            raise HttpError(exc.code, content, url) from None
        resp_headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
        return Response(exc.code, content, url, resp_headers)
    except urllib.error.URLError as exc:
        raise NetworkError(f"No se pudo conectar a {url}: {exc.reason}") from None
    except (TimeoutError, OSError) as exc:
        raise NetworkError(f"Error de red con {url}: {exc}") from None
