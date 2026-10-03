"""Canal en tiempo real (MQTT): mensajes, confirmaciones y flujo completo con un broker local."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import pytest
from conftest import wait_for

from labelbridge.connectors.mqtt import MqttService, topics


class FakeClient:
    def __init__(self) -> None:
        self.published: list[tuple] = []

    def publish(self, topic, payload=b"", qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))

        class Info:
            rc = 0

        return Info()

    def is_connected(self) -> bool:
        return True


def _service(core) -> MqttService:
    core.cfg.data.mqtt.channel = "pc-bodega"
    return MqttService(core.cfg, core.secrets, core.store, core.engine, core.events)


def test_retained_job_is_stored_once_and_cleared(core):
    svc = _service(core)
    fake = FakeClient()
    topic = "labelbridge/pc-bodega/jobs/pedido-1001"
    payload = json.dumps({"id": "pedido-1001", "content": "^XA^XZ", "title": "Pedido #1001"}).encode()
    assert svc.handle_message(topic, payload, fake.publish) == 1
    assert svc.handle_message(topic, payload, fake.publish) == 0  # reentrega: no se duplica
    assert fake.published[-1] == (topic, b"", 1, True)  # retenido borrado
    job = core.store.get_by_external("mqtt", "pedido-1001")
    assert job["status"] == "queued" and job["source"] == "mqtt"
    assert svc.handle_message(topic, b"", fake.publish) == 0  # borrado de retenido: se ignora


def test_batch_and_invalid_messages(core):
    svc = _service(core)
    fake = FakeClient()
    batch = {"jobs": [{"id": "a", "content": "^XA^XZ"}, {"id": "b", "content": "^XA^XZ"}]}
    assert svc.handle_message("labelbridge/pc-bodega/jobs", json.dumps(batch).encode(), fake.publish) == 2
    assert not any(retain for *_, retain in fake.published)  # en /jobs no hay retenido que borrar

    bad_topic = "labelbridge/pc-bodega/jobs/malo"
    assert svc.handle_message(bad_topic, b"{no es json", fake.publish) == 0
    errors = [p for p in fake.published if p[0] == "labelbridge/pc-bodega/errors"]
    assert errors and json.loads(errors[0][1])["status"] == "failed"
    assert (bad_topic, b"", 1, True) in fake.published  # se limpia para no reintentar eternamente


def test_acks_after_printing(core):
    svc = _service(core)
    svc._channel = "pc-bodega"
    fake = FakeClient()
    svc._client = fake
    svc.handle_message("labelbridge/pc-bodega/jobs/ok-1", json.dumps({"id": "ok-1", "content": "^XA^XZ"}).encode(), fake.publish)
    svc.handle_message("labelbridge/pc-bodega/jobs/mal-1", json.dumps({"id": "mal-1", "printer": "no-existe", "content": "^XA^XZ"}).encode(), fake.publish)
    ok = core.store.get_by_external("mqtt", "ok-1")
    bad = core.store.get_by_external("mqtt", "mal-1")
    core.store.set_done(ok["id"], "archivo")
    core.store.set_failed(bad["id"], "sin papel")
    fake.published.clear()
    assert svc.send_acks() == 2
    sent = {topic: json.loads(payload) for topic, payload, *_ in fake.published}
    assert sent["labelbridge/pc-bodega/acks/ok-1"]["status"] == "printed"
    assert sent["labelbridge/pc-bodega/acks/mal-1"]["error"] == "sin papel"
    assert sent["labelbridge/pc-bodega/errors"]["id"] == "mal-1"
    assert core.store.pending_acks("mqtt") == []


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def broker():
    pytest.importorskip("amqtt")
    from amqtt.broker import Broker

    port = _free_port()
    ready, stop = threading.Event(), threading.Event()

    def run() -> None:
        async def main() -> None:
            instance = Broker(
                {"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}}, "auth": {"allow-anonymous": True}}
            )
            await instance.start()
            ready.set()
            while not stop.is_set():
                await asyncio.sleep(0.05)
            await instance.shutdown()

        asyncio.run(main())

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(10)
    yield port
    stop.set()
    thread.join(5)


def test_job_waits_in_broker_while_pc_is_off(core, broker):
    import paho.mqtt.client as mqtt

    # n8n publica mientras el PC está apagado (retain + QoS 1).
    n8n = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id="n8n")
    n8n.connect("127.0.0.1", broker)
    n8n.loop_start()
    acks: list = []
    n8n.on_message = lambda c, u, m: acks.append((m.topic, json.loads(m.payload)))
    n8n.subscribe("labelbridge/pc-bodega/acks/#", qos=1)
    job = {"id": "pedido-2001", "title": "Pedido #2001", "order": {"name": "#2001", "shipping_address": {"name": "Ana"}}}
    n8n.publish("labelbridge/pc-bodega/jobs/pedido-2001", json.dumps(job), qos=1, retain=True).wait_for_publish(5)

    # El PC enciende: recibe el retenido, lo imprime (impresora de archivo) y confirma.
    s = core.cfg.data.mqtt
    s.enabled, s.host, s.port, s.tls, s.channel = True, "127.0.0.1", broker, False, "pc-bodega"
    svc = MqttService(core.cfg, core.secrets, core.store, core.engine, core.events)
    core.engine.start()
    svc.start()
    try:
        assert wait_for(lambda: svc.state == "ok", 10), svc.message
        assert wait_for(lambda: any(t.endswith("/acks/pedido-2001") for t, _ in acks), 15)
        assert acks[-1][1]["status"] == "printed"

        # El retenido ya no está en el broker: un cliente nuevo no lo recibe.
        leftovers: list = []
        probe = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id="probe")
        probe.on_message = lambda c, u, m: m.payload and leftovers.append(m.topic)
        probe.connect("127.0.0.1", broker)
        probe.loop_start()
        probe.subscribe("labelbridge/pc-bodega/jobs/#", qos=1)
        time.sleep(1.0)
        probe.disconnect()
        probe.loop_stop()
        assert leftovers == []
    finally:
        svc.stop()
        n8n.disconnect()
        n8n.loop_stop()
    assert core.store.get_by_external("mqtt", "pedido-2001")["status"] == "done"
    assert topics("pc-bodega")["jobs"] == "labelbridge/pc-bodega/jobs"
