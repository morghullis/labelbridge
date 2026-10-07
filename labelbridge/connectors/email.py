"""Avisos por email a los dueños de la tienda, con el correo que ellos elijan (Gmail, iCloud, Yahoo u otro por SMTP).

- Solo se escribe a las direcciones de Ajustes → Avisos por email (EmailSettings.recipients): ni el asistente ni el
  texto de un pedido pueden elegir a quién se envía.
- Tope de correos por hora (avisos y asistente juntos) y el mismo aviso no se repite en 10 minutos.
- Los avisos automáticos salen en segundo plano: un correo lento nunca traba la impresión ni la ventana.
La contraseña (de aplicación) vive en el almacén seguro.
"""

from __future__ import annotations

import logging
import queue
import smtplib
import threading
import time
from collections import deque
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .. import events as ev
from ..config import NOTIFY_EVENTS, ConfigManager
from ..i18n import tr
from ..net import ssl_context
from ..secret_store import EMAIL_PASSWORD, SecretStore

log = logging.getLogger(__name__)

REPEAT_SECONDS = 600  # el mismo aviso no se manda dos veces en este tiempo
FAILURE_NOTICE_SECONDS = 3600  # un envío que falla se avisa como mucho una vez por hora
MAX_SUBJECT = 150
MAX_BODY = 8000


class EmailError(Exception):
    pass


class EmailSender:
    def __init__(self, cfg: ConfigManager, secrets: SecretStore, events: ev.Events | None = None) -> None:
        self.cfg = cfg
        self.secrets = secrets
        self._sent: deque[float] = deque()
        self._recent: dict[str, float] = {}
        self._lock = threading.Lock()
        self._queue: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=50)
        self._worker: threading.Thread | None = None
        self.events = events
        self._warned_problem = ""  # el último «falta…» que ya se avisó (se avisa una vez por problema)
        self._failure_noticed_at = 0.0  # el último aviso de un envío fallido (como mucho uno por hora)
        if events is not None:
            events.subscribe(self._on_event)

    # --- estado ------------------------------------------------------------------------------
    def problem(self) -> str:
        """Qué falta para poder enviar («» si está listo)."""
        e = self.cfg.data.email
        if not e.enabled:
            return tr("Los avisos por email están desactivados (Ajustes → Avisos por email).")
        if not e.host.strip() or not e.username.strip():
            return tr("Falta el servidor o la cuenta de correo en Ajustes → Avisos por email.")
        if not self.secrets.get(EMAIL_PASSWORD):
            return tr("Falta la contraseña de aplicación del correo en Ajustes → Avisos por email.")
        if not e.recipient_list():
            return tr("Falta a quién avisar: escribe el email de los dueños en Ajustes → Avisos por email.")
        return ""

    def _allow(self) -> bool:
        """Tope por hora: los avisos de más se registran pero no se envían."""
        now = time.time()
        with self._lock:
            while self._sent and now - self._sent[0] > 3600:
                self._sent.popleft()
            if len(self._sent) >= max(1, self.cfg.data.email.per_hour):
                return False
            self._sent.append(now)
            return True

    # --- envío -------------------------------------------------------------------------------
    def send(self, subject: str, body: str) -> int:
        """Envía ya (y espera): para «Enviar prueba» y el asistente. Devuelve a cuántas direcciones."""
        problem = self.problem()
        if problem:
            raise EmailError(problem)
        if not self._allow():
            raise EmailError(tr("Se llegó al tope de {0} correos por hora: se envía más tarde.",
                                self.cfg.data.email.per_hour))
        return self._deliver(subject, body)

    def send_later(self, subject: str, body: str) -> bool:
        """En segundo plano (avisos automáticos). False si no se puede o ya se mandó lo mismo hace poco."""
        key = f"{subject}|{body}"
        now = time.time()
        with self._lock:
            if now - self._recent.get(key, 0) < REPEAT_SECONDS:
                return False
            self._recent = {k: t for k, t in self._recent.items() if now - t < REPEAT_SECONDS}
            self._recent[key] = now
        problem = self.problem()
        if problem:  # los avisos por email están pedidos pero no pueden salir: se dice una vez, no en cada aviso
            if problem != self._warned_problem:
                self._warned_problem = problem
                log.warning("Avisos por email sin enviar: %s", problem)
                self._notify(tr("Los avisos por email no salen"), problem)  # el texto ya dice dónde arreglarlo
            return False
        self._warned_problem = ""
        try:
            self._queue.put_nowait((subject, body))
        except queue.Full:
            log.warning("Demasiados avisos por email pendientes: se descarta «%s»", subject)
            return False
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._drain, name="email", daemon=True)
            self._worker.start()
        return True

    def _drain(self) -> None:
        while True:
            try:
                subject, body = self._queue.get(timeout=30)
            except queue.Empty:
                return
            if not self._allow():
                log.warning("Tope de correos por hora: no se envía «%s»", subject)
                continue
            try:
                self._deliver(subject, body)
            except EmailError as exc:
                log.warning("No se pudo enviar el aviso por email «%s»: %s", subject, exc)
                if time.time() - self._failure_noticed_at > FAILURE_NOTICE_SECONDS:
                    self._failure_noticed_at = time.time()
                    self._notify(tr("No se pudo enviar un aviso por email"),
                                 tr("{0}. Prueba el envío en Ajustes → Email.", exc))

    def _notify(self, title: str, text: str) -> None:
        if self.events is not None:
            self.events.notify(title, text, "error")

    def _deliver(self, subject: str, body: str) -> int:
        e = self.cfg.data.email
        recipients = e.recipient_list()
        message = EmailMessage()
        message["Subject"] = f"[LabelBridge] {' '.join(subject.split())[:MAX_SUBJECT]}"
        message["From"] = formataddr((e.sender_name.strip() or "LabelBridge", e.username.strip()))
        message["To"] = ", ".join(recipients)
        message["Message-ID"] = make_msgid(domain="labelbridge.local")
        footer = tr("— LabelBridge en {0}", self.cfg.data.general.agent_name)
        message.set_content(f"{body.strip()[:MAX_BODY]}\n\n{footer}\n")
        try:
            if e.security == "ssl":
                server = smtplib.SMTP_SSL(e.host.strip(), int(e.port), timeout=25, context=ssl_context())
            else:
                server = smtplib.SMTP(e.host.strip(), int(e.port), timeout=25)
                server.starttls(context=ssl_context())
            try:
                server.login(e.username.strip(), self.secrets.get(EMAIL_PASSWORD))
                server.send_message(message, to_addrs=recipients)
            finally:
                try:
                    server.quit()
                except (smtplib.SMTPException, OSError):
                    pass
        except smtplib.SMTPAuthenticationError as exc:
            raise EmailError(tr("El correo rechazó la cuenta o la contraseña. Gmail e iCloud piden una «contraseña de "
                                "aplicación», no la contraseña normal.")) from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise EmailError(tr("No se pudo enviar el correo: {0}", exc)) from exc
        log.info("Aviso por email enviado a %d dirección(es): %s", len(recipients), subject)
        return len(recipients)

    # --- avisos automáticos ------------------------------------------------------------------
    def _on_event(self, kind: str, data) -> None:
        if kind != ev.NOTIFY or not isinstance(data, dict):
            return
        event = str(data.get("kind") or "")
        if event in NOTIFY_EVENTS and self.cfg.data.email.enabled and self.cfg.data.email.alert_on(event):
            self.send_later(str(data.get("title") or tr("Aviso")), str(data.get("message") or ""))
