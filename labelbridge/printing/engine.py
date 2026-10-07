"""Motor de impresión: toma trabajos de la cola, elige la impresora, convierte e imprime.

Corre en su propio hilo. Reintenta con espera creciente si la impresora no responde.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .. import errors
from .. import events as ev
from .. import net
from ..config import LABEL_MEDIA, MEDIA_PRESETS, ConfigManager, PrinterProfile
from ..security.policy import is_local_network_host
from ..i18n import tr
from ..jobstore import JobStore
from ..labels.order import normalize_order
from ..labels.qt_label import draw_order_label
from ..labels.zpl_label import order_to_zpl
from . import PermanentPrintError, PrintError, outputs
from .detect import detect_format
from .pages import ImageSource, PageSource, PdfSource, VectorSource

log = logging.getLogger(__name__)

RETRY_DELAYS = (10, 30, 60, 120, 300, 600, 900)
PREFETCH_RETRY_SECONDS = 60  # una descarga anticipada que falló no se vuelve a probar antes de esto
PAUSED_WAIT_SECONDS = 5.0  # en pausa solo se descargan archivos; reanudar despierta al motor en el acto
# Destinos tcp:// / ipp:// escritos en un trabajo: solo puertos de impresión (evita usar la app
# para enviar bytes a otros servicios de la red). Las impresoras configuradas no tienen límite.
ADHOC_PORTS = set(range(9100, 9110)) | {631, 515}
DEFAULT_LABEL_MM = (101.6, 152.4)


@dataclass
class Document:
    format: str  # pdf | image | zpl | raw | order
    data: bytes = b""
    order: dict[str, Any] | None = None
    size_mm: tuple[float, float] | None = None  # tamaño real pedido (la guía a 4×6 in en cualquier papel)


def label_size_mm(meta: dict[str, Any] | None) -> tuple[float, float] | None:
    """El tamaño fijo con el que se pidió imprimir el trabajo (meta «label_size», p. ej. "4x6"), si hay."""
    key = str((meta or {}).get("label_size") or "")
    if key not in LABEL_MEDIA:
        return None
    _, w, h = MEDIA_PRESETS[key]
    return (w, h)


def resolve_printer(requested: str, cfg: ConfigManager) -> PrinterProfile:
    """Alias o nombre configurado, impresora instalada, tcp://IP:puerto, ipp://... o la predeterminada."""
    name = (requested or "").strip()
    if not name:
        profile = cfg.default_printer()
        if profile is None:
            raise PrintError(tr("No hay impresora predeterminada. Configura una en la sección Impresoras."))
        return profile
    found = cfg.find_printer(name)
    if found:
        return found
    lowered = name.lower()
    if lowered.startswith(("tcp://", "raw://", "ipp://", "ipps://")):
        parsed = urlparse(name)
        if not parsed.hostname:
            raise PermanentPrintError(tr("Dirección de impresora inválida: {0}", name))
        if not is_local_network_host(parsed.hostname):
            raise PermanentPrintError(tr("Solo se aceptan impresoras de la red local en un trabajo: {0}", parsed.hostname))
        scheme = parsed.scheme.lower()
        port = parsed.port or (9100 if scheme in ("tcp", "raw") else 631)
        if port not in ADHOC_PORTS:
            raise PermanentPrintError(tr("Puerto {0} no permitido para impresoras indicadas en el trabajo", port))
        if scheme in ("tcp", "raw"):
            return PrinterProfile(
                alias=name, kind="network", host=parsed.hostname, port=parsed.port or 9100,
                protocol="raw", language="zpl",
            )
        return PrinterProfile(
            alias=name, kind="network", host=parsed.hostname, port=parsed.port or 631,
            protocol=scheme, ipp_path=parsed.path or "/ipp/print", language="pdf",
        )
    installed = {p["name"].casefold(): p["name"] for p in outputs.list_system_printers()}
    if lowered.casefold() in installed:
        return PrinterProfile(alias=installed[lowered.casefold()], kind="system", system_name=installed[lowered.casefold()], media="auto")
    raise PrintError(tr("La impresora '{0}' no existe (ni como alias ni instalada en el equipo)", name))


class PrintEngine:
    def __init__(
        self, store: JobStore, cfg: ConfigManager, events: ev.Events, shop_domain_fn=None, policy=None
    ) -> None:
        from ..security.policy import SecurityPolicy

        self.store = store
        self.cfg = cfg
        self.events = events
        # Política de seguridad: aprueba cada trabajo externo, cada descarga y sus redirecciones.
        self.policy = policy if policy is not None else SecurityPolicy(cfg)
        self._shop_domain_fn = shop_domain_fn or (lambda: cfg.data.shopify.shop)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_cleanup = 0.0
        self._prefetch_failed: dict[str, float] = {}  # trabajo → cuándo falló su descarga (no se reintenta en seguida)
        self.current_job_id: str | None = None
        # Por qué el motor no está funcionando bien («» si todo va bien): el estado de abajo lo muestra.
        self.problem = ""
        # Funciones que añaden cabeceras de autenticación a descargas concretas (p. ej. Shopify).
        self.url_auth_providers: list = []

    # --- entrada de trabajos --------------------------------------------------------
    def submit(self, req, source: str, client: str = "") -> tuple[dict[str, Any], bool]:
        """Puerta única para los trabajos que llegan de fuera (API local, n8n, Shopify).

        Pasa por la política de seguridad (lanza PolicyDenied), se guarda en la cola y despierta al motor.
        """
        self.policy.admit_job(req, source, client)
        job, created = self.store.add(req, source)
        if created:
            self.events.emit(ev.JOB, job)
            self.wake()
        return job, created

    # --- ciclo de vida -----------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="print-engine", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def wake(self) -> None:
        self._wake.set()

    @property
    def paused(self) -> bool:
        return self.cfg.data.general.paused

    def set_paused(self, paused: bool) -> None:
        self.cfg.data.general.paused = paused
        self.cfg.save("general")
        self.events.emit(ev.PAUSED, paused)
        log.info("Impresión automática %s", "pausada" if paused else "reanudada")
        self.wake()

    # --- bucle ---------------------------------------------------------------------
    def _loop(self) -> None:
        """Nada lo detiene salvo stop(): un error inesperado (la base de datos, un trabajo raro) queda anotado, se avisa,
        el estado de abajo lo muestra y el motor sigue con la vuelta siguiente."""
        log.info("Motor de impresión iniciado")
        while not self._stop.is_set():
            wait = 5.0
            try:
                self._prefetch()
                if not self.paused:
                    while not self._stop.is_set() and not self.paused:
                        job = self.store.next_ready()
                        if job is None:
                            break
                        self._run(job)
                if time.time() - self._last_cleanup > 3600:
                    self._last_cleanup = time.time()
                    removed = self.store.cleanup(self.cfg.data.general.retention_days)
                    if removed:
                        log.info("Historial: %d trabajos antiguos eliminados", removed)
                wait = PAUSED_WAIT_SECONDS if self.paused else self._wait_seconds()
                self._set_problem("")
            except Exception as exc:  # noqa: BLE001  el motor no puede morir: se anota, se avisa y sigue
                log.exception("Error inesperado en el motor de impresión")
                self._set_problem(errors.user_message(exc))
                wait = 10.0
            self._wake.wait(timeout=wait)
            self._wake.clear()
        log.info("Motor de impresión detenido")

    def _set_problem(self, problem: str) -> None:
        if problem == self.problem:
            return
        self.problem = problem
        self.events.emit(ev.ENGINE, {"problem": problem})
        if problem:
            errors.notify(tr("Problema al imprimir"),
                          tr("{0}. LabelBridge lo vuelve a intentar solo; si se repite, revisa la pestaña Registro.",
                             problem))

    def _wait_seconds(self) -> float:
        next_at = self.store.next_retry_at()
        if next_at is None:
            return 5.0
        return max(0.2, min(5.0, next_at - time.time()))

    def _prefetch(self) -> None:
        """Descarga los archivos de trabajos con URL apenas llegan (las URLs firmadas caducan). Si uno falla, siguen los
        demás (y ese se vuelve a probar en un minuto); sin conexión no se insiste con el resto. Al imprimirlo, el trabajo
        lo descarga de nuevo y, si sigue fallando, se reintenta y se avisa como cualquier otro error."""
        now = time.time()
        for job in self.store.needing_download():
            if now - self._prefetch_failed.get(job["id"], 0.0) < PREFETCH_RETRY_SECONDS:
                continue
            try:
                self._download(job)
            except net.NetworkError as exc:
                log.warning("Sin conexión para descargar %s todavía: %s", job["title"], exc)
                self._prefetch_failed[job["id"]] = now
                break
            except Exception as exc:  # noqa: BLE001  se anota y se sigue con los demás
                expected = isinstance(exc, (PrintError, net.HttpError))
                log.warning("No se pudo descargar %s todavía: %s", job["title"], exc, exc_info=not expected)
                self._prefetch_failed[job["id"]] = now
            else:
                self._prefetch_failed.pop(job["id"], None)

    def _download(self, job: dict[str, Any]) -> dict[str, Any]:
        from ..security.policy import PolicyDenied

        headers = dict(job["headers"] or {})
        for provider in self.url_auth_providers:  # cada uno decide según la URL y de dónde vino el trabajo
            headers.update(provider(job["url"], job["source"]) or {})
        guard = self.policy.url_guard(job["source"])
        try:
            resp = net.request("GET", job["url"], headers=headers or None, timeout=45, url_guard=guard)
        except PolicyDenied as exc:
            raise PermanentPrintError(tr("Descarga bloqueada por seguridad: {0}", exc)) from None
        data = resp.body
        if not data:
            raise PrintError(tr("El archivo descargado está vacío"))
        fmt = job["format"]
        if fmt == "auto":
            fmt = detect_format(data, resp.content_type)
        updated = self.store.save_payload(job["id"], data, fmt)
        log.info("Descargado %s (%d KB, %s)", job["title"], len(data) // 1024, fmt)
        return updated or job

    # --- ejecución de un trabajo -------------------------------------------------------
    def _run(self, job: dict[str, Any]) -> None:
        job = self.store.set_printing(job["id"]) or job
        self.current_job_id = job["id"]
        self.events.emit(ev.JOB, job)
        profile: PrinterProfile | None = None
        try:
            profile = resolve_printer(job["printer"], self.cfg)
            if not profile.enabled:
                raise PrintError(tr("La impresora '{0}' está desactivada", profile.display_name()))
            document = self._document(job)
            detail = self.deliver(document, profile, job["copies"], job["title"])
            done = self.store.set_done(job["id"], profile.display_name())
            log.info("Impreso: %s → %s%s", job["title"], profile.display_name(), f" ({detail})" if detail else "")
            self.events.emit(ev.JOB, done)
            # Si sale el aviso o suena lo decide Ajustes → Notificaciones y sonidos.
            self.events.notify(tr("Etiqueta impresa"), f"{job['title']} → {profile.display_name()}", kind="printed")
        except Exception as exc:  # noqa: BLE001  toda falla de un trabajo pasa por _handle_failure (reintento o aviso)
            try:
                self._handle_failure(job, profile, exc)
            except Exception:  # noqa: BLE001  ni siquiera se pudo anotar la falla (la base de datos)
                log.exception("No se pudo registrar la falla de %s", job["title"])
                self._requeue(job)
                raise  # el bucle lo muestra como problema del motor
        finally:
            self.current_job_id = None

    def _requeue(self, job: dict[str, Any]) -> None:
        """Que el trabajo no quede «imprimiendo» para siempre: vuelve a la cola (o, si ni eso se puede, al abrir la app
        otra vez, que devuelve a la cola lo que quedó a medias)."""
        try:
            self.store.set_retry(job["id"], tr("Error inesperado al imprimir: se reintenta"), RETRY_DELAYS[1])
        except Exception:  # noqa: BLE001  la base de datos no responde: se resuelve al reabrir la app
            log.exception("No se pudo devolver %s a la cola", job["title"])

    def _handle_failure(self, job: dict[str, Any], profile: PrinterProfile | None, exc: Exception) -> None:
        retryable = getattr(exc, "retryable", True) if isinstance(exc, PrintError) else True
        if isinstance(exc, (PrintError, net.NetworkError, net.HttpError)):
            message = str(exc)
            if isinstance(exc, net.HttpError) and exc.status in (401, 403, 404, 410):
                retryable = False
        else:
            log.exception("Error imprimiendo %s", job["title"])
            message = errors.user_message(exc)
        printer_name = profile.display_name() if profile else ""
        attempts = job["attempts"]
        max_retries = max(0, self.cfg.data.general.max_retries)
        if retryable and attempts <= max_retries:
            delay = RETRY_DELAYS[min(attempts - 1, len(RETRY_DELAYS) - 1)]
            updated = self.store.set_retry(job["id"], message, delay, printer_name)
            log.warning("Fallo imprimiendo %s (intento %d): %s. Reintento en %ds", job["title"], attempts, message, delay)
        else:
            updated = self.store.set_failed(job["id"], message, printer_name)
            log.error("Trabajo fallido: %s: %s", job["title"], message)
            self.events.notify(tr("No se pudo imprimir"), f"{job['title']}: {message}", "error", kind="print_error")
        self.events.emit(ev.JOB, updated)

    def _document(self, job: dict[str, Any]) -> Document:
        from ..jobstore import PayloadUnavailable

        if job["url"] and not job["payload_path"]:
            job = self._download(job)
        try:
            data = self.store.read_payload(job)
        except PayloadUnavailable as exc:
            raise PermanentPrintError(str(exc)) from None
        if job["format"] == "order":
            return Document("order", order=json.loads(data.decode("utf-8")))
        fmt = job["format"]
        if fmt == "auto":
            fmt = detect_format(data)
            self.store.set_format(job["id"], fmt)
        return Document(fmt, data=data, size_mm=label_size_mm(job.get("meta")))

    # --- conversión + salida -------------------------------------------------------------
    def _sources(self, document: Document, profile: PrinterProfile) -> list[PageSource]:
        if document.format == "pdf":
            return [PdfSource(document.data)]
        if document.format == "image":
            return [ImageSource(document.data)]
        if document.format == "order":
            label = normalize_order(document.order or {}, self.cfg.data.labels, self._shop_domain_fn())
            media = profile.media_mm() or DEFAULT_LABEL_MM
            show_items = self.cfg.data.labels.show_items
            return [VectorSource(media, lambda p, r, dpi: draw_order_label(p, r, dpi, label, show_items))]
        raise PermanentPrintError(tr("Formato no soportado para esta impresora: {0}", document.format))

    def order_zpl(self, order: dict[str, Any], profile: PrinterProfile, copies: int) -> bytes:
        label = normalize_order(order, self.cfg.data.labels, self._shop_domain_fn())
        media = profile.media_mm() or DEFAULT_LABEL_MM
        return order_to_zpl(label, media, profile.dpi or 203, self.cfg.data.labels.show_items, copies)

    def deliver(self, document: Document, profile: PrinterProfile, copies: int, title: str) -> str:
        """Imprime un documento en un perfil. Devuelve un detalle para el registro."""
        fmt = document.format
        raw_formats = ("zpl", "raw")

        if profile.kind == "file":
            if fmt in raw_formats:
                ext = "zpl" if fmt == "zpl" else "bin"
                path = outputs.write_file(profile, title, ext, document.data * copies, self._out_dir())
            elif profile.language == "zpl":
                data = self._as_zpl(document, profile, copies)
                path = outputs.write_file(profile, title, "zpl", data, self._out_dir())
            else:
                pdf = self._as_pdf(document, profile)
                path = outputs.write_file(profile, title, "pdf", pdf, self._out_dir())
            return str(path)

        if profile.kind == "system":
            if not profile.system_name:
                raise PermanentPrintError(tr("La impresora no tiene configurado su nombre en el sistema"))
            if fmt in raw_formats:
                outputs.print_raw_system(profile.system_name, document.data * copies, title)
                return "RAW"
            if profile.language == "zpl":
                outputs.print_raw_system(profile.system_name, self._as_zpl(document, profile, copies), title)
                return "ZPL"
            sources = self._sources(document, profile)
            try:
                outputs.print_with_driver(profile, sources, copies, title, document.size_mm)
            finally:
                for source in sources:
                    source.close()
            return "driver"

        if profile.kind == "network":
            if not profile.host:
                raise PermanentPrintError(tr("La impresora de red no tiene IP configurada"))
            if profile.protocol in ("ipp", "ipps"):
                if fmt in raw_formats:
                    raise PermanentPrintError(tr("Las impresoras IPP no aceptan ZPL/RAW; usa una conexión RAW 9100"))
                pdf = document.data if fmt == "pdf" and not document.size_mm else self._as_pdf(document, profile)
                fingerprint = outputs.send_ipp(profile, pdf, title, copies, actual_size=bool(document.size_mm))
                self._pin_certificate(profile, fingerprint)
                return "IPP"
            if fmt in raw_formats:
                outputs.send_tcp(profile.host, profile.port, document.data * copies)
                return "RAW 9100"
            if profile.language == "pdf":
                pdf = document.data if fmt == "pdf" and not document.size_mm else self._as_pdf(document, profile)
                outputs.send_tcp(profile.host, profile.port, pdf * copies)
                return "PDF 9100"
            outputs.send_tcp(profile.host, profile.port, self._as_zpl(document, profile, copies))
            return "ZPL 9100"

        raise PermanentPrintError(tr("Tipo de impresora desconocido: {0}", profile.kind))

    def _as_zpl(self, document: Document, profile: PrinterProfile, copies: int) -> bytes:
        if document.format == "order":
            return self.order_zpl(document.order or {}, profile, copies)
        sources = self._sources(document, profile)
        try:
            return outputs.build_zpl_raster(sources, profile, copies, document.size_mm)
        finally:
            for source in sources:
                source.close()

    def _as_pdf(self, document: Document, profile: PrinterProfile) -> bytes:
        if document.format == "pdf" and profile.media == "auto" and not document.size_mm:
            return document.data
        sources = self._sources(document, profile)
        try:
            return outputs.build_pdf(sources, profile.media_mm(), profile.rotate, profile.crop, label_mm=document.size_mm)
        finally:
            for source in sources:
                source.close()

    def _pin_certificate(self, profile: PrinterProfile, fingerprint: str) -> None:
        if not fingerprint or profile.tls_fingerprint:
            return
        stored = self.cfg.printer_by_id(profile.id)
        if stored is not None:
            stored.tls_fingerprint = fingerprint
            self.cfg.save("printers")
            log.info("Certificado de %s fijado (SHA-256 %s…)", profile.display_name(), fingerprint[:16])

    def _out_dir(self) -> Path:
        from ..paths import output_dir

        return output_dir()

    # --- pruebas manuales --------------------------------------------------------------------
    def print_test(self, profile: PrinterProfile) -> str:
        """Imprime la etiqueta de prueba directamente (sin pasar por la cola)."""
        from ..labels.order import sample_order

        document = Document("order", order=sample_order())
        detail = self.deliver(document, profile, 1, tr("Prueba LabelBridge"))
        self.store.kv_set("guide:test_printed", "1")
        return detail
