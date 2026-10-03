"""API HTTP local: n8n en tu red, tu web o scripts envían trabajos directamente a este PC.

POST /api/v1/jobs           (Authorization: Bearer <clave>)  → encola trabajos
GET  /api/v1/jobs[/<id>]    → estado de trabajos
GET  /api/v1/printers       → impresoras configuradas
POST /api/v1/print/test     → etiqueta de prueba
GET  /api/v1/health         → sin autenticación
"""

from __future__ import annotations

import hmac
import json
import logging
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .. import APP_NAME, __version__
from .. import events as ev
from ..config import ConfigManager
from ..jobstore import JobStore
from ..labels.order import sample_order
from ..models import MAX_CONTENT_BYTES, JobError, JobRequest, parse_jobs_payload
from ..printing.engine import PrintEngine
from ..secret_store import LOCAL_API_KEY, SecretStore
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


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    server_version = f"{APP_NAME}/{__version__}"
    protocol_version = "HTTP/1.1"

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

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        unread_body = int(self.headers.get("Content-Length") or 0) > 0 and not getattr(self, "_body_consumed", False)
        self.send_response(status)
        if unread_body:
            # El cuerpo sin leer contaminaría la siguiente petición de esta conexión.
            self.send_header("Connection", "close")
            self.close_connection = True
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        expected = self.svc.api_key()
        auth = self.headers.get("Authorization", "")
        provided = auth[7:].strip() if auth.lower().startswith("bearer ") else self.headers.get("X-Api-Key", "")
        return bool(expected) and hmac.compare_digest(provided.encode(), expected.encode())

    def _read_json(self) -> Any:
        length = self.headers.get("Content-Length")
        if length is None:
            raise JobError("Falta Content-Length")
        size = int(length)
        if size > MAX_BODY:
            raise OverflowError
        raw = self.rfile.read(size)
        self._body_consumed = True
        try:
            return json.loads(raw.decode("utf-8") or "null")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JobError(f"JSON inválido: {exc}") from None

    def _route(self) -> tuple[str, dict[str, list[str]]]:
        self._body_consumed = False
        parsed = urlparse(self.path)
        return parsed.path.rstrip("/") or "/", parse_qs(parsed.query)

    # --- métodos -----------------------------------------------------------------------
    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path, query = self._route()
        if path in ("/", "/api/v1/health"):
            counts = self.svc.store.counts()
            self._send(
                200,
                {
                    "ok": True,
                    "app": APP_NAME,
                    "version": __version__,
                    "agent": self.svc.cfg.data.general.agent_name,
                    "paused": self.svc.cfg.data.general.paused,
                    "queued": counts["queued"],
                },
            )
            return
        if not self._authorized():
            self._send(401, {"error": "Clave de API inválida o ausente (Authorization: Bearer <clave>)"})
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
            limit = min(500, int((query.get("limit") or ["50"])[0] or 50))
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
        if not self._authorized():
            self._send(401, {"error": "Clave de API inválida o ausente (Authorization: Bearer <clave>)"})
            return
        try:
            if path == "/api/v1/jobs":
                requests = parse_jobs_payload(self._read_json(), require_id=False)
                self._send(202, {"accepted": [self.svc.enqueue(req) for req in requests]})
            elif path == "/api/v1/print/test":
                printer = (query.get("printer") or [""])[0]
                req = JobRequest(
                    external_id=f"prueba-{datetime.now():%Y%m%d%H%M%S%f}",
                    title="Etiqueta de prueba",
                    printer=printer,
                    format="order",
                    order=sample_order(),
                )
                self._send(202, {"accepted": [self.svc.enqueue(req, source="test")]})
            elif path in ("/api/v1/pause", "/api/v1/resume"):
                self.svc.engine.set_paused(path.endswith("pause"))
                self._send(200, {"paused": self.svc.cfg.data.general.paused})
            else:
                self._send(404, {"error": "Ruta no encontrada"})
        except OverflowError:
            self._send(413, {"error": "Solicitud demasiado grande"})
        except JobError as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:
            log.exception("Error en la API local")
            self._send(500, {"error": f"Error interno: {exc}"})

    def do_DELETE(self) -> None:  # noqa: N802
        path, _ = self._route()
        if not self._authorized():
            self._send(401, {"error": "Clave de API inválida o ausente"})
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

    def enqueue(self, req: JobRequest, source: str = "api") -> dict[str, Any]:
        job, created = self.store.add(req, source)
        if created:
            self.events.emit(ev.JOB, job)
            self.engine.wake()
            log.info("API local: trabajo recibido %s", job["title"])
        return {"id": job["id"], "external_id": job["external_id"], "status": job["status"], "duplicate": not created}

    def start(self) -> None:
        if self._server is not None:
            return
        settings = self.cfg.data.local_api
        host = "0.0.0.0" if settings.allow_lan else "127.0.0.1"
        try:
            server = _Server((host, settings.port), _Handler)
        except OSError as exc:
            self.set_status("error", f"No se pudo abrir el puerto {settings.port}: {exc.strerror or exc}")
            log.error("API local: puerto %s no disponible (%s)", settings.port, exc)
            return
        server.service = self
        self._server = server
        self.bound_port = server.server_address[1]
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5}, name="local-api", daemon=True).start()
        where = "toda la red local" if settings.allow_lan else "solo este equipo"
        self.set_status("ok", f"Escuchando en el puerto {self.bound_port} ({where})")
        log.info("API local escuchando en %s:%s", host, self.bound_port)

    def stop(self, timeout: float = 5.0) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        self.bound_port = None
        self.set_status("off", "Desactivada")
