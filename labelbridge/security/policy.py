"""La puerta de seguridad: toda petición externa y toda descarga pasa por aquí antes de ejecutarse.

- Trabajos que llegan por la API local o por n8n: límite por minuto, máximo de copias y URL permitida.
- Descargas: solo http(s); nada de direcciones reservadas (p. ej. 169.254.169.254) y, según el ajuste,
  nada de la red interna (protege contra SSRF: que un trabajo haga que este PC pida datos a otros equipos
  de tu red). Se comprueba también cada redirección.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Callable
from urllib.parse import urlparse

from ..i18n import tr
from ..models import JobRequest
from .audit import AuditLog
from .ratelimit import FailureTracker, RateLimiter


def is_local_network_host(host: str) -> bool:
    """IP privada o de este equipo, o un nombre de la red local (sin dominio, .local, .lan, .home): las impresoras
    que un trabajo o el asistente indican por dirección tienen que estar aquí, nunca en internet."""
    host = (host or "").strip().strip("[]")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        name = host.lower().rstrip(".")
        return bool(name) and ("." not in name or name.endswith((".local", ".lan", ".home", ".internal")))
    return address.is_private or address.is_loopback or address.is_link_local

log = logging.getLogger(__name__)

PUSH_SOURCES = {"api", "mqtt"}  # canales externos: con límite de velocidad
LOCAL_SOURCES = {"api", "manual", "test"}  # pueden descargar desde la red local si el ajuste lo permite
PRIVATE_URL_MODES = ("never", "local_api", "always")
INTERNAL_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home.arpa")
CARRIER_NAT = ipaddress.ip_network("100.64.0.0/10")


class PolicyDenied(Exception):
    """Petición rechazada por la política de seguridad. status: código HTTP equivalente."""

    def __init__(self, message: str, status: int = 403, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class SecurityPolicy:
    def __init__(
        self,
        cfg,
        audit: AuditLog | None = None,
        resolver: Callable[..., list] = socket.getaddrinfo,
    ) -> None:
        self.cfg = cfg
        self.audit = audit or AuditLog()
        self._resolver = resolver
        settings = lambda: cfg.data.security  # noqa: E731 - se leen en cada consulta
        self._jobs = RateLimiter(lambda: settings().jobs_per_minute)
        self.api_requests = RateLimiter(lambda: settings().api_requests_per_minute)
        self.api_failures = FailureTracker(
            lambda: settings().api_max_auth_failures, lambda: settings().api_block_minutes * 60
        )

    @property
    def settings(self):
        return self.cfg.data.security

    # --- trabajos ------------------------------------------------------------------------------
    def admit_job(self, req: JobRequest, source: str, client: str = "") -> None:
        """Aprueba un trabajo nuevo o lanza PolicyDenied."""
        s = self.settings
        if source in PUSH_SOURCES:
            allowed, wait = self._jobs.allow(f"{source}:{client}")
            if not allowed:
                self.audit.record(source, "trabajo", "deny", client=client, detail=tr("límite de trabajos por minuto"))
                raise PolicyDenied(
                    tr("Demasiados trabajos: máximo {0} por minuto. Espera unos segundos.", s.jobs_per_minute), 429, wait
                )
        if req.copies > max(1, s.max_copies):
            self.audit.record(source, "trabajo", "deny", client=client, detail=f"{req.copies} copias")
            raise PolicyDenied(tr("Máximo {0} copias por trabajo (ajustable en Ajustes → Seguridad).", s.max_copies), 400)
        if req.url:
            self.check_url(req.url, source, resolve=False)

    # --- URLs ----------------------------------------------------------------------------------
    def check_url(self, url: str, source: str, resolve: bool = True) -> None:
        """Aprueba una URL de descarga o lanza PolicyDenied (también para cada redirección)."""
        problem = self._url_problem(url, source, resolve)
        if problem:
            self.audit.record("descarga", source, "deny", detail=f"{problem}: {url}")
            raise PolicyDenied(problem, 403)

    def url_guard(self, source: str) -> Callable[[str], None]:
        return lambda url: self.check_url(url, source)

    def _url_problem(self, url: str, source: str, resolve: bool) -> str:
        s = self.settings
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return tr("Solo se permiten descargas http(s)")
        if parsed.username or parsed.password:
            return tr("La URL no puede llevar usuario y contraseña")
        host = (parsed.hostname or "").rstrip(".").lower()
        if not host:
            return tr("URL sin servidor")
        internal = host == "localhost" or host.endswith(INTERNAL_SUFFIXES)
        for address in self._addresses(host, resolve):
            if address.is_link_local or address.is_multicast or address.is_unspecified:
                return tr("Dirección reservada no permitida")
            if address.is_loopback or address.is_private or address.is_reserved or address in CARRIER_NAT:
                internal = True
        if internal:
            mode = s.private_urls if s.private_urls in PRIVATE_URL_MODES else "local_api"
            if mode == "always" or (mode == "local_api" and source in LOCAL_SOURCES):
                return ""
            return tr("Descargas desde la red interna no permitidas para este origen (Ajustes → Seguridad)")
        if parsed.scheme != "https" and s.require_https:
            return tr("Las descargas desde internet deben usar https")
        return ""

    def _addresses(self, host: str, resolve: bool) -> list:
        try:
            address = ipaddress.ip_address(host.strip("[]"))
            return [address.ipv4_mapped or address] if isinstance(address, ipaddress.IPv6Address) else [address]
        except ValueError:
            pass
        if not resolve:
            return []
        try:
            infos = self._resolver(host, None)
        except OSError:
            return []  # sin DNS no hay conexión: la descarga fallará sola
        found = []
        for info in infos:
            try:
                address = ipaddress.ip_address(info[4][0].split("%")[0])
            except (ValueError, IndexError):
                continue
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
            found.append(address)
        return found
