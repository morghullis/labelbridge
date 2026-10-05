"""API HTTP local: n8n en tu red, tu web o scripts envían trabajos directamente a este PC.

POST /api/v1/jobs           (Authorization: Bearer <clave>)  → encola trabajos
GET  /api/v1/jobs[/<id>]    → estado de trabajos
GET  /api/v1/printers       → impresoras configuradas
POST /api/v1/print/test     → etiqueta de prueba
GET  /api/v1/health         → sin autenticación (detalles solo con clave)

Seguridad (Ajustes → Seguridad): límite de peticiones por minuto y equipo, bloqueo temporal tras claves
incorrectas, máximo de conexiones simultáneas, tiempo de espera por conexión, cabecera Host verificada
(contra DNS rebinding) y cada trabajo pasa por la política de seguridad antes de entrar en la cola.
"""

from __future__ import annotations

import hmac
import json
import logging
import socket
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .. import APP_NAME, __version__
from .. import events as ev
from ..config import ConfigManager
from ..i18n import tr
from ..jobstore import JobStore
from ..labels.order import sample_order
from ..models import MAX_CONTENT_BYTES, JobError, JobRequest, parse_jobs_payload
from ..discovery import local_ipv4_addresses
from ..printing.engine import PrintEngine
from ..secret_store import LOCAL_API_KEY, SecretStore
from ..security.policy import PolicyDenied
from .base import BackgroundService

log = logging.getLogger(__name__)

MAX_BODY = MAX_CONTENT_BYTES * 4 // 3 + 64 * 1024  # base64 de 25 MB + margen


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds") if ts else None


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job["id"],
        "external_id": job["external_id"],
        "source": job["source"],
        "title": job["title"],
        "printer": job["printer"],
        "printer_used": job["printer_used"],
        "format": job["format"],
        "copies": job["copies"],
        "status": job["status"],
        "attempts": job["attempts"],
        "error": job["error"] or None,
        "created_at": _iso(job["created_at"]),
        "printed_at": _iso(job["printed_at"]),
    }


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    service: "LocalApiService"
    slots: threading.BoundedSemaphore

    def process_request(self, request, client_address) -> None:
        if not self.slots.acquire(blocking=False):
            # Demasiadas conexiones a la vez: se cierra sin atender (protege de inundaciones).
            self.service.policy.audit.record("api", "conexión", "deny", client=client_address[0],
                                             detail=tr("demasiadas conexiones simultáneas"))
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    server_version = APP_NAME
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 30  # una conexión lenta o abierta sin enviar nada se corta (evita agotar el servidor)

    def log_message(self, fmt: str, *args: Any) -> None:
        log.debug("API %s - %s", self.address_string(), fmt % args)

    # --- utilidades ------------------------------------------------------------------
    @property
    def svc(self) -> "LocalApiService":
        return self.server.service

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        allowed = [o.strip() for o in self.svc.cfg.data.local_api.cors_origins.split(",") if o.strip()]
        if origin and ("*" in allowed or origin in allowed):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Api-Key")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Max-Age", "600")

    def _send(self, status: int, payload: Any, retry_after: float = 0.0) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            declared = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            declared = 1
        unread_body = declared > 0 and not getattr(self, "_body_consumed", False)
        self.send_response(status)
        if unread_body:
            # El cuerpo sin leer contaminaría la siguiente petición de esta conexión.
            self.send_header("Connection", "close")
            self.close_connection = True
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if retry_after:
            self.send_header("Retry-After", str(max(1, int(retry_after + 0.999))))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    @property
    def client(self) -> str:
        return str(self.client_address[0])

    def _admit(self) -> bool:
        """Middleware de cada petición: bloqueo por claves incorrectas, Host permitido y límite por minuto."""
        policy = self.svc.policy
        blocked = policy.api_failures.blocked_for(self.client)
        if blocked:
            self._send(429, {"error": "Equipo bloqueado temporalmente por claves incorrectas."}, retry_after=blocked)
            return False
        if not self.svc.host_allowed(self.headers.get("Host", ""), self.client):
            policy.audit.record("api", self.command, "deny", client=self.client, detail=tr("Host {0}", self.headers.get('Host')))
            self._send(421, {"error": "Host no permitido"})
            return False
        allowed, wait = policy.api_requests.allow(self.client)
        if not allowed:
            policy.audit.record("api", self.command, "deny", client=self.client, detail=tr("límite de peticiones por minuto"))
            self._send(429, {"error": "Demasiadas peticiones. Espera unos segundos."}, retry_after=wait)
            return False
        return True

    def _authorized(self) -> bool:
        expected = self.svc.api_key()
        auth = self.headers.get("Authorization", "")
        provided = auth[7:].strip() if auth.lower().startswith("bearer ") else self.headers.get("X-Api-Key", "")
        ok = bool(expected) and hmac.compare_digest(provided.encode(), expected.encode())
        policy = self.svc.policy
        if ok:
            policy.api_failures.success(self.client)
        elif policy.api_failures.failure(self.client):
            minutes = self.svc.cfg.data.security.api_block_minutes
            policy.audit.record("api", "autenticación", "block", client=self.client, detail=f"{minutes} min")
            self.svc.events.notify(
                tr("API local: acceso bloqueado"),
                tr("Demasiadas claves incorrectas desde {0}. Bloqueado {1} minutos.", self.client, minutes),
                "error",
            )
        return ok

    def _deny_unauthorized(self) -> None:
        self.svc.policy.audit.record("api", self.command, "deny", client=self.client, detail=tr("clave inválida o ausente"))
        self._send(401, {"error": tr("Clave de API inválida o ausente (Authorization: Bearer <clave>)")})

    def _read_json(self) -> Any:
        length = self.headers.get("Content-Length")
        if length is None:
            raise JobError(tr("Falta Content-Length"))
        try:
            size = int(length)
        except ValueError:
            raise JobError(tr("Content-Length inválido")) from None
        if size < 0 or size > MAX_BODY:
            raise OverflowError
        raw = self.rfile.read(size)
        self._body_consumed = True
        try:
            return json.loads(raw.decode("utf-8") or "null")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JobError(tr("JSON inválido: {0}", exc)) from None

    def _route(self) -> tuple[str, dict[str, list[str]]]:
        self._body_consumed = False
        parsed = urlparse(self.path)
        return parsed.path.rstrip("/") or "/", parse_qs(parsed.query)

    # --- métodos -----------------------------------------------------------------------
    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self._admit():
            return
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path, query = self._route()
        if not self._admit():
            return
        if path in ("/", "/api/v1/health"):
            health: dict[str, Any] = {"ok": True, "app": APP_NAME, "version": __version__}
            has_credentials = bool(self.headers.get("Authorization") or self.headers.get("X-Api-Key"))
            if has_credentials and self._authorized():  # detalles del equipo solo con clave
                health.update(
                    agent=self.svc.cfg.data.general.agent_name,
                    paused=self.svc.cfg.data.general.paused,
                    queued=self.svc.store.counts()["queued"],
                )
            self._send(200, health)
            return
        if not self._authorized():
            self._deny_unauthorized()
            return
        if path == "/api/v1/printers":
            cfg = self.svc.cfg
            default = cfg.default_printer()
            self._send(
                200,
                {
                    "default": default.alias if default else None,
                    "printers": [
                        {
                            "alias": p.alias,
                            "kind": p.kind,
                            "target": p.target_text(),
                            "language": p.language,
                            "media": p.media,
                            "enabled": p.enabled,
                        }
                        for p in cfg.data.printers
                    ],
                },
            )
            return
        if path == "/api/v1/jobs":
            try:
                limit = max(1, min(500, int((query.get("limit") or ["50"])[0] or 50)))
            except ValueError:
                self._send(400, {"error": tr("'limit' debe ser un número")})
                return
            status = (query.get("status") or [None])[0]
            jobs = self.svc.store.list(limit=limit, status=status)
            self._send(200, {"jobs": [public_job(j) for j in jobs]})
            return
        if path.startswith("/api/v1/jobs/"):
            job = self.svc.store.find(path.rsplit("/", 1)[-1])
            if job is None:
                self._send(404, {"error": "Trabajo no encontrado"})
            else:
                self._send(200, public_job(job))
            return
        self._send(404, {"error": "Ruta no encontrada"})

    def do_POST(self) -> None:  # noqa: N802
        path, query = self._route()
        if not self._admit():
            return
        if not self._authorized():
            self._deny_unauthorized()
            return
        try:
            if path == "/api/v1/jobs":
                requests = parse_jobs_payload(self._read_json(), require_id=False)
                self._send(202, {"accepted": [self.svc.enqueue(req, client=self.client) for req in requests]})
            elif path == "/api/v1/print/test":
                printer = (query.get("printer") or [""])[0]
                req = JobRequest(
                    external_id=f"prueba-{datetime.now():%Y%m%d%H%M%S%f}",
                    title=tr("Etiqueta de prueba"),
                    printer=printer,
                    format="order",
                    order=sample_order(),
                )
                self._send(202, {"accepted": [self.svc.enqueue(req, source="test", client=self.client)]})
            elif path in ("/api/v1/pause", "/api/v1/resume"):
                self.svc.engine.set_paused(path.endswith("pause"))
                self._send(200, {"paused": self.svc.cfg.data.general.paused})
            else:
                self._send(404, {"error": "Ruta no encontrada"})
        except OverflowError:
            self._send(413, {"error": "Solicitud demasiado grande"})
        except JobError as exc:
            self._send(400, {"error": str(exc)})
        except PolicyDenied as exc:
            self._send(exc.status, {"error": str(exc)}, retry_after=exc.retry_after)
        except Exception:
            log.exception("Error en la API local")
            self._send(500, {"error": tr("Error interno (detalles en el registro de la app)")})

    def do_DELETE(self) -> None:  # noqa: N802
        path, _ = self._route()
        if not self._admit():
            return
        if not self._authorized():
            self._deny_unauthorized()
            return
        if path.startswith("/api/v1/jobs/"):
            job = self.svc.store.find(path.rsplit("/", 1)[-1])
            if job is None:
                self._send(404, {"error": "Trabajo no encontrado"})
                return
            updated = self.svc.store.cancel(job["id"])
            self.svc.events.emit(ev.JOB, updated)
            self._send(200, public_job(updated or job))
            return
        self._send(404, {"error": "Ruta no encontrada"})


class LocalApiService(BackgroundService):
    name = "local_api"
    label = "API local"

    def __init__(
        self, cfg: ConfigManager, secrets: SecretStore, store: JobStore, engine: PrintEngine, events: ev.Events
    ) -> None:
        super().__init__(events)
        self.cfg = cfg
        self.secrets = secrets
        self.store = store
        self.engine = engine
        self._server: _Server | None = None
        self.bound_port: int | None = None

    def api_key(self) -> str:
        return self.secrets.get(LOCAL_API_KEY)

    def enabled(self) -> bool:
        return self.cfg.data.local_api.enabled

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def policy(self):
        return self.engine.policy

    def enqueue(self, req: JobRequest, source: str = "api", client: str = "") -> dict[str, Any]:
        job, created = self.engine.submit(req, source, client)  # lanza PolicyDenied si no se permite
        if created:
            log.info("API local: trabajo recibido %s", job["title"])
        return {"id": job["id"], "external_id": job["external_id"], "status": job["status"], "duplicate": not created}

    def host_allowed(self, host_header: str, client: str) -> bool:
        """Cabecera Host esperada: evita que una web maliciosa use DNS rebinding para hablar con la API."""
        if not host_header:
            return client in ("127.0.0.1", "::1")
        host = host_header.strip().lower()
        if host.startswith("["):
            host = host[1 : host.find("]")] if "]" in host else host
        else:
            host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        return host in self._allowed_hosts()

    def _allowed_hosts(self) -> set[str]:
        settings = self.cfg.data.local_api
        names = {"127.0.0.1", "localhost", "::1"}
        extra = {h.strip().lower() for h in settings.allowed_hosts.split(",") if h.strip()}
        if settings.allow_lan:
            hostname = socket.gethostname().lower()
            names |= {hostname, hostname.split(".")[0], f"{hostname.split('.')[0]}.local", socket.getfqdn().lower()}
            names |= set(local_ipv4_addresses())
        return names | extra

    def start(self) -> None:
        if self._server is not None:
            return
        settings = self.cfg.data.local_api
        host = "0.0.0.0" if settings.allow_lan else "127.0.0.1"
        try:
            server = _Server((host, settings.port), _Handler)
        except OSError as exc:
            self.set_status("error", tr("No se pudo abrir el puerto {0}: {1}", settings.port, exc.strerror or exc))
            log.error("API local: puerto %s no disponible (%s)", settings.port, exc)
            return
        server.service = self
        server.slots = threading.BoundedSemaphore(max(1, self.cfg.data.security.api_max_connections))
        self._server = server
        self.bound_port = server.server_address[1]
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5}, name="local-api", daemon=True).start()
        where = tr("toda la red local") if settings.allow_lan else tr("solo este equipo")
        self.set_status("ok", tr("Escuchando en el puerto {0} ({1})", self.bound_port, where))
        log.info("API local escuchando en %s:%s", host, self.bound_port)

    def stop(self, timeout: float = 5.0) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        self.bound_port = None
        self.set_status("off", tr("Desactivada"))
