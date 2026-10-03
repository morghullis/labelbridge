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
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import paho.mqtt.client as mqtt

from .. import __version__
from .. import events as ev
from .. import net
from ..config import ConfigManager, MqttSettings, slugify
from ..jobstore import JobStore
from ..models import JobError, parse_jobs_payload
from ..printing.engine import PrintEngine
from ..secret_store import MQTT_PASSWORD, SecretStore
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
        self._client = client
        self.set_status("connecting", f"Conectando con {settings.host}…")
        try:
            client.connect_async(settings.host.strip(), int(settings.port), keepalive=60)
            client.loop_start()
            while not stop.is_set():
                if client.is_connected():
                    self.send_acks()
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
                hint = " (revisa usuario y contraseña del broker)"
            self.set_status("error", f"El broker rechazó la conexión: {reason_code}{hint}")
            log.error("MQTT: conexión rechazada: %s", reason_code)
            return
        t = topics(self._channel)
        client.subscribe([(t["jobs"], 1), (f"{t['jobs']}/+", 1)])
        client.publish(t["status"], self._presence(True), qos=1, retain=True)
        self.set_status("ok", f"Conectado · escuchando el canal «{self._channel}»")
        log.info("MQTT: conectado, canal %s", self._channel)
        self.wake()

    def _on_connect_fail(self, client: mqtt.Client, userdata) -> None:
        settings = self.cfg.data.mqtt
        self.set_status("error", f"No se pudo conectar con {settings.host}:{settings.port}; reintentando…")

    def _on_disconnect(self, client: mqtt.Client, userdata, flags, reason_code, properties) -> None:
        if self.running:
            self.set_status("connecting", "Conexión perdida; reconectando…")
            log.warning("MQTT: desconectado (%s); reconectando", reason_code)

    def _on_message(self, client: mqtt.Client, userdata, message: mqtt.MQTTMessage) -> None:
        try:
            self.handle_message(message.topic, message.payload, client.publish)
        except Exception:
            log.exception("MQTT: error procesando un mensaje de %s", message.topic)

    # --- mensajes -----------------------------------------------------------------------------
    def handle_message(self, topic: str, payload: bytes, publish: Callable[..., Any]) -> int:
        """Guarda los trabajos del mensaje y limpia el retenido. Devuelve cuántos son nuevos."""
        if not payload:
            return 0  # retenido borrado (por nosotros u otro cliente)
        t = topics(self._channel or self.channel())
        per_job = topic.startswith(t["jobs"] + "/")
        try:
            data = json.loads(payload.decode("utf-8"))
            requests = parse_jobs_payload(data, require_id=True)
        except (UnicodeDecodeError, json.JSONDecodeError, JobError) as exc:
            log.warning("MQTT: trabajo inválido en %s: %s", topic, exc)
            publish(
                t["errors"],
                json.dumps(
                    {
                        "id": topic.rsplit("/", 1)[-1] if per_job else None,
                        "status": "failed",
                        "error": f"Trabajo inválido: {exc}",
                        "agent": self.cfg.data.general.agent_name,
                        "at": _now(),
                    }
                ),
                qos=1,
            )
            if per_job:
                publish(topic, b"", qos=1, retain=True)
            return 0
        new = 0
        for req in requests:
            job, created = self.store.add(req, "mqtt")
            if created:
                new += 1
                log.info("n8n (tiempo real): trabajo recibido %s", job["title"])
                self.events.emit(ev.JOB, job)
        if new:
            self.engine.wake()
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
        for job in self.store.pending_acks("mqtt"):
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
                result = client.publish(f"{t['acks']}/{topic_part(job['external_id'])}", body, qos=1)
                if status == "failed":
                    client.publish(t["errors"], body, qos=1)
                if result.rc != mqtt.MQTT_ERR_SUCCESS:
                    break
                sent += 1
            self.store.mark_acked(job["id"], status or "received", job["updated_at"])
        return sent

    # --- prueba desde la UI ----------------------------------------------------------------------
    def test_connection(self, timeout: float = 10.0) -> str:
        settings = self.cfg.data.mqtt
        host = settings.host.strip()
        if not host:
            raise RuntimeError("Escribe el servidor (broker) MQTT")
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
            raise RuntimeError(f"Error TLS con {host}:{settings.port} ({exc.reason or exc}). ¿Puerto 8883 con TLS?") from None
        except OSError as exc:
            raise RuntimeError(f"No se pudo conectar con {host}:{settings.port} ({exc.strerror or exc})") from None
        client.loop_start()
        try:
            if not done.wait(timeout):
                raise RuntimeError("El broker no respondió a tiempo")
        finally:
            client.disconnect()
            client.loop_stop()
        if result["rc"].is_failure:
            raise RuntimeError(f"El broker rechazó la conexión: {result['rc']}")
        return f"Conexión correcta. En n8n publica en «{topics(self.channel())['jobs']}/<id>» con QoS 1 y retain."
