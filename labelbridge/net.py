"""Cliente HTTP mínimo (stdlib) con TLS del sistema + certifi, límites y errores claros."""

from __future__ import annotations

import http.client
import json
import socket
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

from . import APP_NAME, __version__
from .i18n import tr

USER_AGENT = f"{APP_NAME}/{__version__}"
DEFAULT_MAX_BYTES = 30 * 1024 * 1024

_ssl_context: ssl.SSLContext | None = None


class NetworkError(Exception):
    """No se pudo conectar (DNS, timeout, TLS...).

    maybe_sent=False cuando el fallo ocurrió antes de enviar la petición (DNS, conexión rechazada,
    certificado inválido): el servidor seguro que no la recibió.
    """

    def __init__(self, message: str, maybe_sent: bool = True) -> None:
        super().__init__(message)
        self.maybe_sent = maybe_sent


class HttpError(Exception):
    def __init__(self, status: int, body: bytes, url: str) -> None:
        self.status = status
        self.body = body
        self.url = url
        snippet = body[:300].decode("utf-8", "replace").strip()
        super().__init__(tr("HTTP {0}", status) + (f": {snippet}" if snippet else ""))


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
            raise NetworkError(tr("La respuesta no es JSON válido ({0})", self.url)) from exc


_SAFE_HEADERS = {"user-agent", "accept", "accept-encoding", "accept-language"}


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Al redirigir a otro dominio no reenvía credenciales (tokens, Authorization, cabeceras propias).

    Además así funcionan las URLs firmadas (S3, CDN) que rechazan cabeceras Authorization extra.
    Con `guard`, cada destino de redirección pasa antes por la política de seguridad.
    """

    def __init__(self, guard: Callable[[str], None] | None = None) -> None:
        super().__init__()
        self.guard = guard

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        if self.guard is not None:
            self.guard(newurl)  # lanza PolicyDenied si el destino no está permitido
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        if urlparse(newurl).hostname != urlparse(req.full_url).hostname:
            for name in list(new.headers):
                if name.lower() not in _SAFE_HEADERS:
                    del new.headers[name]
            for name in list(new.unredirected_hdrs):
                if name.lower() not in _SAFE_HEADERS:
                    del new.unredirected_hdrs[name]
        return new


_opener: urllib.request.OpenerDirector | None = None


def _get_opener(guard: Callable[[str], None] | None = None) -> urllib.request.OpenerDirector:
    global _opener
    if guard is not None:
        return urllib.request.build_opener(_SafeRedirectHandler(guard), urllib.request.HTTPSHandler(context=ssl_context()))
    if _opener is None:
        _opener = urllib.request.build_opener(
            _SafeRedirectHandler(), urllib.request.HTTPSHandler(context=ssl_context())
        )
    return _opener


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
    url_guard: Callable[[str], None] | None = None,
) -> Response:
    """Petición HTTP. `url_guard` aprueba la URL y cada redirección (lanza una excepción si no)."""
    if url_guard is not None:
        url_guard(url)
    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    body = data
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, method=method.upper(), headers=hdrs)
    try:
        with _get_opener(url_guard).open(req, timeout=timeout) as resp:
            content = resp.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise NetworkError(tr("La respuesta supera {0} MB", max_bytes // (1024 * 1024)))
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return Response(resp.status, content, resp.geturl(), resp_headers)
    except urllib.error.HTTPError as exc:
        content = exc.read(64 * 1024) if exc.fp else b""
        if raise_for_status:
            raise HttpError(exc.code, content, url) from None
        resp_headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
        return Response(exc.code, content, url, resp_headers)
    except http.client.HTTPException as exc:  # respuesta cortada o mal formada: pudo haberse procesado
        raise NetworkError(tr("Error de red con {0}: {1}", url, exc or type(exc).__name__)) from None
    except urllib.error.URLError as exc:
        before_send = isinstance(exc.reason, (socket.gaierror, ConnectionRefusedError, ssl.SSLCertVerificationError))
        raise NetworkError(tr("No se pudo conectar a {0}: {1}", url, exc.reason), maybe_sent=not before_send) from None
    except (TimeoutError, OSError) as exc:
        raise NetworkError(tr("Error de red con {0}: {1}", url, exc)) from None
