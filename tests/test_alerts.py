"""Avisos y sonidos: pedido nuevo, retrasos de entrega, horario de silencio y los tonos de LabelBridge."""

from __future__ import annotations

import json
import wave
from datetime import datetime, timezone

from test_orders import FakeShopify, _service, iso, make_order, usps

from labelbridge.ui import alerts


def test_a_new_order_is_announced_once(core, monkeypatch):
    fake = FakeShopify(iso(datetime.now(timezone.utc).timestamp()))
    service, s = _service(core, monkeypatch, fake)
    s.buy_shipping_label = False
    service.cycle(service.client(), s)
    service.cycle(service.client(), s)
    news = [data for kind, data in core.received if kind == "notify" and data.get("kind") == "new_order"]
    assert [data["title"] for data in news] == ["Pedido nuevo #3001"]  # el pagado y nuevo; una sola vez
    assert "Cliente" in news[0]["message"] and "sale por USPS" in news[0]["message"]

    # Al actualizar la app, un pedido de antes que cambia en Shopify no suena como nuevo.
    core.store.kv_set(service._key("announce_since"), str(datetime.now(timezone.utc).timestamp() + 3600))
    core.store.unmark_seen(service._key("order:3001:announced"))
    service.cycle(service.client(), s)
    assert len([1 for kind, data in core.received if kind == "notify" and data.get("kind") == "new_order"]) == 1


def test_delivery_delays_are_announced_once(core, monkeypatch):
    from labelbridge import net

    now = datetime.now(timezone.utc).timestamp()
    late = make_order(1701, created=now - 5 * 86400, labels=(("1", "5,68"),), fulfillment="FULFILLED", fulfillments=(
        usps(created=now - 4 * 86400, displayStatus="IN_TRANSIT", inTransitAt=iso(now - 3 * 86400),
             estimatedDeliveryAt=iso(now - 86400)),))
    fine = make_order(1702, created=now - 86400, labels=(("2", "5,68"),), fulfillment="FULFILLED", fulfillments=(
        usps(created=now - 80000, displayStatus="IN_TRANSIT", inTransitAt=iso(now - 3600),
             estimatedDeliveryAt=iso(now + 86400)),))

    def fake(method, url, **kwargs):
        if url.endswith("/admin/oauth/access_token"):
            return net.Response(200, json.dumps({"access_token": "tok", "expires_in": 86399}).encode(), url)
        body = {"data": {"orders": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [late, fine]}}}
        return net.Response(200, json.dumps(body).encode(), url)

    service, _s = _service(core, monkeypatch, fake)
    assert service.check_deliveries() == 1
    assert service.check_deliveries() == 0  # el mismo retraso no vuelve a avisar
    delays = [data for kind, data in core.received if kind == "notify" and data.get("kind") == "delivery"]
    assert len(delays) == 1 and delays[0]["title"] == "#1701#: Entrega atrasada"
    assert any(kind == "orders" for kind, _data in core.received)  # la pestaña Pedidos se actualiza


def test_quiet_hours_and_which_notices_show(core):
    n = core.cfg.data.notifications
    n.quiet, n.quiet_from, n.quiet_to = True, "22:00", "07:00"
    assert alerts.quiet_now(n, datetime(2026, 10, 5, 23, 30)) and alerts.quiet_now(n, datetime(2026, 10, 6, 6, 59))
    assert not alerts.quiet_now(n, datetime(2026, 10, 5, 12, 0))
    n.quiet_from, n.quiet_to = "13:00", "15:00"
    assert alerts.quiet_now(n, datetime(2026, 10, 5, 14, 0)) and not alerts.quiet_now(n, datetime(2026, 10, 5, 16, 0))
    n.quiet = False
    assert not alerts.quiet_now(n, datetime(2026, 10, 5, 14, 0))

    core.cfg.data.general.notify_printed = False
    n.new_order = False
    assert not alerts.toast_enabled(core.cfg, "printed") and not alerts.toast_enabled(core.cfg, "new_order")
    assert alerts.toast_enabled(core.cfg, "print_error") and alerts.toast_enabled(core.cfg, "")
    assert n.sound("new_order") == "register" and n.sound("printed") == ""
    n.sounds = "basura"  # una configuración dañada no rompe los avisos
    assert n.sound("action") == "alert"


def test_tones_are_generated_once_per_volume(tmp_path, monkeypatch):
    from labelbridge.system import sounds

    played = []
    monkeypatch.setattr(sounds, "_play_file", lambda path: played.append(path) or True)
    for key in ("register", "chime", "ding", "bell", "harp", "pop", "alert"):
        path = sounds.tone_file(key, 60, tmp_path)
        with wave.open(str(path)) as wav:
            assert wav.getnchannels() == 1 and wav.getsampwidth() == 2 and 0.1 <= wav.getnframes() / wav.getframerate() <= 2
    first = sounds.tone_file("ding", 60, tmp_path)
    stamp = first.stat().st_mtime_ns
    assert sounds.tone_file("ding", 60, tmp_path).stat().st_mtime_ns == stamp  # ya generado: no se rehace
    quiet = sounds.tone_file("ding", 20, tmp_path)
    loud = sounds.tone_file("ding", 100, tmp_path)
    assert _peak(quiet) < _peak(loud)
    scaled = sounds.scaled_copy(loud, 50, tmp_path)
    assert abs(_peak(scaled) - _peak(loud) / 2) < 400
    assert sounds.play("", 80) is False and sounds.play("ding", 0) is False  # sin sonido o volumen 0
    assert sounds.play("custom", 80, str(tmp_path / "no-existe.wav")) is False


def _peak(path) -> int:
    import array

    with wave.open(str(path)) as wav:
        return max(abs(v) for v in array.array("h", wav.readframes(wav.getnframes())))
