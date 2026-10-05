"""Canal en tiempo real (MQTT): n8n se ejecuta solo cuando ocurre un evento y publica el trabajo;
este PC lo recibe al instante por una conexión saliente cifrada (no hay que abrir puertos).

Temas (canal = identificador de este PC, p. ej. "pc-bodega"):
  labelbridge/<canal>/jobs/<id>   n8n publica un trabajo (QoS 1, retain): espera aunque el PC esté apagado
  labelbridge/<canal>/jobs        también acepta trabajos sin retain (uno o {"jobs": [...]})
  labelbridge/<canal>/acks/<id>   la app confirma printed / failed
  labelbridge/<canal>/errors      la app publica los fallos (para avisarte desde n8n)
  labelbridge/<canal>/status      presencia del PC (retain + last will)
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import paho.mqtt.client as mqtt

from .. import __version__
from .. import events as ev
from .. import net
from ..config import ConfigManager, MqttSettings, slugify
from ..i18n import tr
from ..jobstore import JobStore
from ..models import JobError, parse_jobs_payload
from ..printing.engine import PrintEngine
from ..secret_store import MQTT_PASSWORD, SecretStore
from ..security.policy import PolicyDenied
from .base import BackgroundService

log = logging.getLogger(__name__)

TOPIC_ROOT = "labelbridge"
FINAL_ACK = {"done": "printed", "failed": "failed", "canceled": "failed"}


def topics(channel: str) -> dict[str, str]:
    base = f"{TOPIC_ROOT}/{channel}"
    return {
        "jobs": f"{base}/jobs",
        "acks": f"{base}/acks",
        "errors": f"{base}/errors",
        "status": f"{base}/status",
    }


def topic_part(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", text)[:120] or "trabajo"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MqttService(BackgroundService):
    name = "mqtt"
    label = "n8n · tiempo real"

    def __init__(
        self, cfg: ConfigManager, secrets: SecretStore, store: JobStore, engine: PrintEngine, events: ev.Events
    ) -> None:
        super().__init__(events)
        self.cfg = cfg
        self.secrets = secrets
        self.store = store
        self.engine = engine
        self._client: mqtt.Client | None = None
        self._channel = ""
        # mid de publicación -> (job_id, estado, updated_at): se confirma al recibir el PUBACK.
        self._inflight: dict[int, tuple[str, str, float]] = {}
        self._inflight_lock = threading.Lock()
        self._resubscribe_at = 0.0  # >0: volver a suscribirse (trabajos retenidos que esperaban por el límite)
        events.subscribe(self._on_event)

    def enabled(self) -> bool:
        settings = self.cfg.data.mqtt
        return settings.enabled and bool(settings.host.strip())

    def _on_event(self, kind: str, data: Any) -> None:
        if kind == ev.JOB and isinstance(data, dict) and data.get("source") == "mqtt" and data.get("ack_pending"):
            self.wake()

    # --- cliente -----------------------------------------------------------------------------
    def _new_client(self, settings: MqttSettings, client_id: str, persistent: bool) -> mqtt.Client:
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=not persistent,
            protocol=mqtt.MQTTv311,
        )
        if settings.username.strip():
            client.username_pw_set(settings.username.strip(), self.secrets.get(MQTT_PASSWORD) or None)
        if settings.tls:
            client.tls_set_context(net.ssl_context())
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        return client

    def channel(self) -> str:
        return slugify(self.cfg.data.mqtt.channel)

    def client_id(self) -> str:
        settings = self.cfg.data.mqtt
        return f"labelbridge-{self.channel()}-{settings.client_suffix}"

    def _presence(self, online: bool) -> str:
        return json.dumps(
            {"online": online, "agent": self.cfg.data.general.agent_name, "version": __version__, "at": _now()}
        )

    # --- bucle ----------------------------------------------------------------------------------
    def run(self, stop: threading.Event, wake: threading.Event) -> None:
        settings = self.cfg.data.mqtt
        self._channel = self.channel()
        t = topics(self._channel)
        # Sesión persistente: si el PC se desconecta, el broker guarda los mensajes QoS 1.
        client = self._new_client(settings, self.client_id(), persistent=True)
        client.will_set(t["status"], self._presence(False), qos=1, retain=True)
        client.on_connect = self._on_connect
        client.on_connect_fail = self._on_connect_fail
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.on_publish = self._on_publish
        with self._inflight_lock:
            self._inflight.clear()  # cliente nuevo: lo no confirmado se reenvía
        self._client = client
        self.set_status("connecting", tr("Conectando con {0}…", settings.host))
        try:
            client.connect_async(settings.host.strip(), int(settings.port), keepalive=60)
            client.loop_start()
            while not stop.is_set():
                if client.is_connected():
                    self.send_acks()
                    if self._resubscribe_at and time.monotonic() >= self._resubscribe_at:
                        self._resubscribe_at = 0.0
                        client.subscribe([(t["jobs"], 1), (f"{t['jobs']}/+", 1)])  # el broker reenvía lo retenido
                if not self.sleep(stop, wake, 30):
                    break
        finally:
            try:
                if client.is_connected():
                    client.publish(t["status"], self._presence(False), qos=1, retain=True).wait_for_publish(2)
                client.disconnect()
            except Exception:
                log.debug("MQTT: error al desconectar", exc_info=True)
            client.loop_stop()
            self._client = None

    def _on_connect(self, client: mqtt.Client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            hint = ""
            text = str(reason_code).lower()
            if "password" in text or "not authorized" in text:
                hint = tr(" (revisa usuario y contraseña del broker)")
            self.set_status("error", tr("El broker rechazó la conexión: {0}{1}", reason_code, hint))
            log.error("MQTT: conexión rechazada: %s", reason_code)
            return
        t = topics(self._channel)
        client.subscribe([(t["jobs"], 1), (f"{t['jobs']}/+", 1)])
        client.publish(t["status"], self._presence(True), qos=1, retain=True)
        self.set_status("ok", tr("Conectado · escuchando el canal «{0}»", self._channel))
        log.info("MQTT: conectado, canal %s", self._channel)
        self.wake()

    def _on_connect_fail(self, client: mqtt.Client, userdata) -> None:
        settings = self.cfg.data.mqtt
        self.set_status("error", tr("No se pudo conectar con {0}:{1}; reintentando…", settings.host, settings.port))

    def _on_disconnect(self, client: mqtt.Client, userdata, flags, reason_code, properties) -> None:
        if self.running:
            self.set_status("connecting", tr("Conexión perdida; reconectando…"))
            log.warning("MQTT: desconectado (%s); reconectando", reason_code)

    def _on_publish(self, client: mqtt.Client, userdata, mid: int, reason_code, properties) -> None:
        with self._inflight_lock:
            entry = self._inflight.pop(mid, None)
        if entry is not None:
            job_id, status, updated_at = entry
            self.store.mark_acked(job_id, status, updated_at)

    def _on_message(self, client: mqtt.Client, userdata, message: mqtt.MQTTMessage) -> None:
        try:
            self.handle_message(message.topic, message.payload, client.publish)
        except Exception:
            log.exception("MQTT: error procesando un mensaje de %s", message.topic)

    # --- mensajes -----------------------------------------------------------------------------
    def _report_error(self, publish: Callable[..., Any], job_id: str | None, message: str) -> None:
        t = topics(self._channel or self.channel())
        body = {"id": job_id, "status": "failed", "error": message, "agent": self.cfg.data.general.agent_name, "at": _now()}
        publish(t["errors"], json.dumps(body, ensure_ascii=False), qos=1)

    def handle_message(self, topic: str, payload: bytes, publish: Callable[..., Any]) -> int:
        """Guarda los trabajos del mensaje y limpia el retenido. Devuelve cuántos son nuevos."""
        if not payload:
            return 0  # retenido borrado (por nosotros u otro cliente)
        t = topics(self._channel or self.channel())
        per_job = topic.startswith(t["jobs"] + "/")
        topic_id = topic.rsplit("/", 1)[-1] if per_job else None
        limit_mb = max(1, self.cfg.data.security.max_message_mb)
        problem = ""
        requests = []
        if len(payload) > limit_mb * 1024 * 1024:
            problem = tr("Mensaje demasiado grande (máximo {0} MB)", limit_mb)
            self.engine.policy.audit.record("mqtt", "mensaje", "deny", detail=tr("{0} bytes en {1}", len(payload), topic))
        else:
            try:
                requests = parse_jobs_payload(json.loads(payload.decode("utf-8")), require_id=True)
            except (UnicodeDecodeError, json.JSONDecodeError, JobError) as exc:
                problem = tr("Trabajo inválido: {0}", exc)
        if problem:
            log.warning("MQTT: %s en %s", problem, topic)
            self._report_error(publish, topic_id, problem)
            if per_job:
                publish(topic, b"", qos=1, retain=True)
            return 0
        new = 0
        for req in requests:
            try:
                job, created = self.engine.submit(req, "mqtt")
            except PolicyDenied as exc:
                if exc.status == 429 and per_job:
                    # Demasiados trabajos seguidos: el mensaje se queda retenido en el broker y se vuelve a
                    # pedir en un minuto (no se pierde nada).
                    self._resubscribe_at = time.monotonic() + 60
                    log.warning("n8n: %s Se procesará en un minuto.", exc)
                    return new
                self._report_error(publish, req.external_id, str(exc))
                continue
            if created:
                new += 1
                log.info("n8n (tiempo real): trabajo recibido %s", job["title"])
        if per_job:
            # Ya está guardado en la cola local: se borra del broker para no recibirlo otra vez.
            publish(topic, b"", qos=1, retain=True)
        return new

    def send_acks(self) -> int:
        client = self._client
        if client is None or not client.is_connected():
            return 0
        t = topics(self._channel or self.channel())
        sent = 0
        with self._inflight_lock:
            waiting = {entry[0] for entry in self._inflight.values()}
        for job in self.store.pending_acks("mqtt"):
            if job["id"] in waiting:
                continue  # ya enviado; esperando confirmación del broker
            status = FINAL_ACK.get(job["status"])
            if status:
                body = json.dumps(
                    {
                        "id": job["external_id"],
                        "status": status,
                        "error": job["error"] or None,
                        "agent": self.cfg.data.general.agent_name,
                        "printer": job["printer_used"] or job["printer"],
                        "at": _now(),
                    },
                    ensure_ascii=False,
                )
                with self._inflight_lock:
                    result = client.publish(f"{t['acks']}/{topic_part(job['external_id'])}", body, qos=1)
                    if result.rc == mqtt.MQTT_ERR_SUCCESS:
                        self._inflight[result.mid] = (job["id"], status, job["updated_at"])
                if result.rc != mqtt.MQTT_ERR_SUCCESS:
                    break
                if status == "failed":
                    client.publish(t["errors"], body, qos=1)
                sent += 1
            else:
                self.store.mark_acked(job["id"], "received", job["updated_at"])  # aún no hay nada que confirmar
        return sent

    # --- prueba desde la UI ----------------------------------------------------------------------
    def test_connection(self, timeout: float = 10.0) -> str:
        settings = self.cfg.data.mqtt
        host = settings.host.strip()
        if not host:
            raise RuntimeError(tr("Escribe el servidor (broker) MQTT"))
        client = self._new_client(settings, f"labelbridge-prueba-{uuid.uuid4().hex[:8]}", persistent=False)
        result: dict[str, Any] = {}
        done = threading.Event()

        def on_connect(c, userdata, flags, reason_code, properties) -> None:
            result["rc"] = reason_code
            done.set()

        client.on_connect = on_connect
        try:
            client.connect(host, int(settings.port), keepalive=30)
        except ssl.SSLError as exc:
            raise RuntimeError(tr("Error TLS con {0}:{1} ({2}). ¿Puerto 8883 con TLS?", host, settings.port, exc.reason or exc)) from None
        except OSError as exc:
            raise RuntimeError(tr("No se pudo conectar con {0}:{1} ({2})", host, settings.port, exc.strerror or exc)) from None
        client.loop_start()
        try:
            if not done.wait(timeout):
                raise RuntimeError(tr("El broker no respondió a tiempo"))
        finally:
            client.disconnect()
            client.loop_stop()
        if result["rc"].is_failure:
            raise RuntimeError(tr("El broker rechazó la conexión: {0}", result['rc']))
        return tr("Conexión correcta. En n8n publica en «{0}/<id>» con QoS 1 y retain.", topics(self.channel())['jobs'])
