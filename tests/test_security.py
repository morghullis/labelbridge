"""Seguridad: cifrado en disco, política de peticiones (SSRF, límites, bloqueo) y registros sin secretos."""

from __future__ import annotations

import http.server
import json
import logging
import sqlite3
import threading
import urllib.error
import urllib.request

import pytest

from labelbridge import net
from labelbridge.config import ConfigManager
from labelbridge.jobstore import JobStore
from labelbridge.models import JobRequest, parse_job
from labelbridge.secret_store import DATA_KEY, SecretStore, SecretStoreError
from labelbridge.security.audit import AuditLog
from labelbridge.security.crypto import CryptoError, DataProtector
from labelbridge.security.policy import PolicyDenied, SecurityPolicy
from labelbridge.security.ratelimit import FailureTracker, RateLimiter
from labelbridge.security.redact import RedactingFormatter, redact

ORDER = {"name": "#1001", "shipping_address": {"name": "Ana Pérez", "address1": "Calle Falsa 123", "phone": "+1 305 555 0101"}}


@pytest.fixture()
def protector(home):
    return DataProtector.from_secrets(SecretStore(home / "secrets.json", backend="file"))


# --- cifrado -------------------------------------------------------------------------------------

def test_encryption_roundtrip_and_tamper_detection(home):
    secrets = SecretStore(home / "secrets.json", backend="file")
    first = DataProtector.from_secrets(secrets)
    again = DataProtector.from_secrets(SecretStore(home / "secrets.json", backend="file"))  # misma clave al reabrir
    blob = first.encrypt(b"etiqueta de Ana", "spool:1")
    assert b"Ana" not in blob and again.decrypt(blob, "spool:1") == b"etiqueta de Ana"
    with pytest.raises(CryptoError):
        again.decrypt(blob, "spool:2")  # ligado a su contexto: no se puede mover a otro trabajo
    tampered = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(CryptoError):
        first.decrypt(tampered, "spool:1")
    text = first.encrypt_text("https://x/label.pdf?token=abc", "jobs.url:1")
    assert text.startswith("lbx1:") and "token" not in text
    assert first.decrypt_text(text, "jobs.url:1") == "https://x/label.pdf?token=abc"
    assert first.decrypt(b"datos antiguos", "spool:9") == b"datos antiguos"  # sin cifrar: se migran después
    other = DataProtector.from_secrets(SecretStore(home / "otra.json", backend="file"))
    with pytest.raises(CryptoError):
        other.decrypt(blob, "spool:1")  # otra instalación (otra clave) no puede leerlo


def test_key_is_never_replaced_when_the_vault_fails(home):
    class BrokenVault:
        def get(self, name, strict=False):
            raise SecretStoreError("almacén caído")

        def set(self, name, value):
            raise AssertionError("no debe crear una clave nueva si no pudo leer la existente")

    with pytest.raises(CryptoError):
        DataProtector.from_secrets(BrokenVault(), attempts=2)


def test_config_is_encrypted_and_migrated(home, protector):
    plain = home / "config.json"
    legacy = ConfigManager(plain)  # versión anterior: texto plano
    legacy.data.labels.sender_address = "Calle Falsa 123, Miami"
    legacy.save()
    cfg = ConfigManager(plain, protector)
    assert cfg.data.labels.sender_address == "Calle Falsa 123, Miami"
    assert not plain.exists() and b"Calle Falsa" not in (home / "config.enc").read_bytes()
    cfg.data.general.agent_name = "PC-BODEGA"
    cfg.save()
    assert ConfigManager(plain, protector).data.general.agent_name == "PC-BODEGA"
    (home / "config.enc").write_bytes(b"LBX1" + b"\x00" * 40)  # dañado: se aparta y se empieza de cero
    assert ConfigManager(plain, protector).data.general.agent_name != "PC-BODEGA"
    assert list(home.glob("config.corrupt-*.enc"))


def test_jobs_are_encrypted_at_rest_and_migrated(home, protector):
    db, spool = home / "jobs.db", home / "spool"
    spool.mkdir()
    old = JobStore(db, spool)  # versión anterior: sin cifrar
    old_job, _ = old.add(JobRequest(external_id="viejo", order=ORDER, meta={"order_name": "#999"}), "shopify")
    old.close()

    store = JobStore(db, spool, protector)  # al abrir cifra lo existente
    job, _ = store.add(
        JobRequest(external_id="nuevo", url="https://cdn.example/l.pdf?sig=abc", headers={"Authorization": "Bearer x"},
                   meta={"tracking": "1Z999"}),
        "api",
    )
    order_job, _ = store.add(JobRequest(external_id="pedido", order=ORDER), "shopify")
    raw = sqlite3.connect(db)
    rows = raw.execute("SELECT url, headers, meta FROM jobs").fetchall()
    raw.close()
    flat = json.dumps(rows)
    assert "cdn.example" not in flat and "Bearer" not in flat and "1Z999" not in flat and "#999" not in flat
    for path in spool.iterdir():
        assert b"Ana" not in path.read_bytes() and b"Calle Falsa" not in path.read_bytes()
    assert store.get(job["id"])["url"] == "https://cdn.example/l.pdf?sig=abc"
    assert store.get(job["id"])["headers"] == {"Authorization": "Bearer x"}
    assert store.read_order(order_job)["shipping_address"]["name"] == "Ana Pérez"
    assert store.read_order(store.get(old_job["id"]))["name"] == "#1001"  # lo migrado se sigue leyendo
    copy = store.reprint(order_job["id"])
    assert store.read_order(copy)["name"] == "#1001"
    store.save_payload(job["id"], b"%PDF-1.4 guia", "pdf")
    assert store.read_payload(store.get(job["id"])) == b"%PDF-1.4 guia"
    store.close()


# --- política ------------------------------------------------------------------------------------

def _policy(core, resolver=None):
    return SecurityPolicy(core.cfg, AuditLog(), resolver=resolver or (lambda host, port: []))


def test_downloads_cannot_reach_internal_or_reserved_addresses(core):
    policy = _policy(core, resolver=lambda host, port: [(2, 1, 6, "", ("10.0.0.7", 0))])
    with pytest.raises(PolicyDenied, match="reservada"):
        policy.check_url("http://169.254.169.254/latest/meta-data", "api")  # metadatos de nube: nunca
    with pytest.raises(PolicyDenied, match="red interna"):
        policy.check_url("http://127.0.0.1:8080/admin", "mqtt")
    policy.check_url("http://192.168.1.20/etiqueta.pdf", "api")  # API local de este equipo: permitido por defecto
    with pytest.raises(PolicyDenied, match="red interna"):
        policy.check_url("https://router.example.com/x", "shopify")  # el nombre resuelve a una IP interna
    with pytest.raises(PolicyDenied, match="https"):
        _policy(core).check_url("http://cdn.example.com/l.pdf", "mqtt")
    with pytest.raises(PolicyDenied, match="usuario"):
        _policy(core).check_url("https://user:pass@cdn.example.com/l.pdf", "api")
    with pytest.raises(PolicyDenied):
        _policy(core).check_url("file:///etc/passwd", "api")
    core.cfg.data.security.private_urls = "never"
    with pytest.raises(PolicyDenied):
        policy.check_url("http://192.168.1.20/etiqueta.pdf", "api")
    _policy(core).check_url("https://cdn.shopify.com/label.pdf", "shopify")
    assert any(entry["decision"] == "deny" for entry in policy.audit.recent)


def test_redirects_are_checked_too(core):
    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/etiqueta.pdf"
        with pytest.raises(PolicyDenied, match="reservada"):
            net.request("GET", url, url_guard=_policy(core).url_guard("api"))
    finally:
        server.shutdown()


def test_job_admission_limits(core):
    core.cfg.data.security.jobs_per_minute = 3
    policy = _policy(core)
    for index in range(3):
        policy.admit_job(parse_job({"id": f"j{index}", "content": "^XA^XZ"}), "mqtt")
    with pytest.raises(PolicyDenied) as denied:
        policy.admit_job(parse_job({"id": "j4", "content": "^XA^XZ"}), "mqtt")
    assert denied.value.status == 429 and denied.value.retry_after > 0
    policy.admit_job(parse_job({"id": "s1", "content": "^XA^XZ"}), "shopify")  # fuentes internas: sin límite
    with pytest.raises(PolicyDenied, match="copias"):
        policy.admit_job(parse_job({"id": "c", "content": "^XA^XZ", "copies": 40}), "api")


def test_rate_limiter_and_lockout_with_clock():
    now = [0.0]
    limiter = RateLimiter(lambda: 60, clock=lambda: now[0])
    assert all(limiter.allow("ip")[0] for _ in range(60))
    allowed, wait = limiter.allow("ip")
    assert not allowed and 0 < wait <= 1.01
    now[0] += 1.0
    assert limiter.allow("ip")[0] and limiter.allow("otra-ip")[0]

    tracker = FailureTracker(lambda: 3, lambda: 600, clock=lambda: now[0])
    assert not tracker.failure("ip") and not tracker.failure("ip")
    assert tracker.failure("ip") and tracker.blocked_for("ip") == 600
    now[0] += 601
    assert tracker.blocked_for("ip") == 0


# --- API local -----------------------------------------------------------------------------------

def _api(core):
    from labelbridge.connectors.local_api import LocalApiService
    from labelbridge.secret_store import LOCAL_API_KEY

    core.cfg.data.local_api.port = 0
    core.secrets.set(LOCAL_API_KEY, "lb_clave_de_prueba_123456789012345")
    service = LocalApiService(core.cfg, core.secrets, core.store, core.engine, core.events)
    service.start()
    return service, f"http://127.0.0.1:{service.bound_port}"


def _call(url, method="GET", key=None, body=None, host=None):
    request = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None)
    if key:
        request.add_header("Authorization", f"Bearer {key}")
    if host:
        request.add_header("Host", host)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read()), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}"), dict(exc.headers)


def test_local_api_blocks_brute_force_floods_and_rebinding(core):
    core.cfg.data.security.api_max_auth_failures = 3
    service, base = _api(core)
    key = core.secrets.get("local_api_key")
    try:
        status, health, headers = _call(base + "/api/v1/health")
        assert status == 200 and "agent" not in health and headers.get("X-Content-Type-Options") == "nosniff"
        assert _call(base + "/api/v1/health", key=key)[1]["agent"]
        status, _, _ = _call(base + "/api/v1/jobs", host="atacante.example.com:7300")
        assert status == 421  # DNS rebinding: Host no permitido
        for _ in range(3):
            assert _call(base + "/api/v1/jobs", key="clave-incorrecta")[0] == 401
        status, body, headers = _call(base + "/api/v1/jobs", key=key)
        assert status == 429 and "Retry-After" in headers  # bloqueado aunque ahora use la clave buena
        assert any(e["decision"] == "block" for e in core.engine.policy.audit.recent)
        assert any(kind == "notify" for kind, _ in core.received)
    finally:
        service.stop()


def test_local_api_rate_limit_and_safe_errors(core):
    core.cfg.data.security.api_requests_per_minute = 10
    service, base = _api(core)
    key = core.secrets.get("local_api_key")
    try:
        status, body, _ = _call(base + "/api/v1/jobs", "POST", key, {"id": "x", "content": "^XA^XZ", "copies": 45})
        assert status == 400 and "copias" in body["error"]
        statuses = [_call(base + "/api/v1/printers", key=key)[0] for _ in range(12)]
        assert statuses.count(429) >= 1
    finally:
        service.stop()


# --- MQTT ----------------------------------------------------------------------------------------

def test_mqtt_rejects_oversized_and_defers_floods(core):
    from labelbridge.connectors.mqtt import MqttService, topics

    core.cfg.data.mqtt.channel = "pc"
    core.cfg.data.security.max_message_mb = 1
    core.cfg.data.security.jobs_per_minute = 1
    service = MqttService(core.cfg, core.secrets, core.store, core.engine, core.events)
    sent = []
    publish = lambda topic, payload=b"", qos=0, retain=False: sent.append((topic, payload, retain))  # noqa: E731
    jobs = topics("pc")["jobs"]
    assert service.handle_message(f"{jobs}/grande", b"x" * (1024 * 1024 + 10), publish) == 0
    assert any(topic.endswith("/errors") for topic, _, _ in sent) and (f"{jobs}/grande", b"", True) in sent
    sent.clear()
    assert service.handle_message(f"{jobs}/a", json.dumps({"id": "a", "content": "^XA^XZ"}).encode(), publish) == 1
    assert service.handle_message(f"{jobs}/b", json.dumps({"id": "b", "content": "^XA^XZ"}).encode(), publish) == 0
    assert (f"{jobs}/b", b"", True) not in sent  # se queda retenido en el broker para más tarde
    assert service._resubscribe_at > 0


# --- registros -----------------------------------------------------------------------------------

def test_logs_never_contain_secrets():
    text = (
        "token shpat_abcdef1234567890 y shpss_0123456789abcdef, clave sk-ant-api03-xyz123456789 "
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc https://x.com/l.pdf?X-Amz-Signature=deadbeef&a=1 "
        "cliente ana@example.com lb_aaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    clean = redact(text)
    for secret in ("shpat_abcdef", "shpss_0123", "sk-ant-api03", "eyJhbGci", "deadbeef", "ana@example.com", "lb_aaaa"):
        assert secret not in clean
    formatter = RedactingFormatter("%(message)s")
    try:
        raise ValueError("fallo con shpss_0123456789abcdef")
    except ValueError:
        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "error", None, exc_info=__import__("sys").exc_info())
    assert "shpss_0123456789abcdef" not in formatter.format(record)


def test_secret_store_file_format(home):
    legacy = home / "legacy.json"
    import base64

    legacy.write_text(json.dumps({"mqtt_password": base64.b64encode(b"vieja").decode()}), encoding="utf-8")
    store = SecretStore(legacy, backend="file")
    assert store.get("mqtt_password") == "vieja"  # formato antiguo
    store.set(DATA_KEY, "clave")
    raw = json.loads(legacy.read_text(encoding="utf-8"))
    assert raw["format"] == 2 and "vieja" not in legacy.read_text(encoding="utf-8")
    assert SecretStore(legacy, backend="file").get("mqtt_password") == "vieja"
