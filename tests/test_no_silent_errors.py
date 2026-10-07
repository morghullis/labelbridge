"""Ninguna falla en silencio: cada error queda en el registro y la persona se entera cuando le afecta (1.16)."""

from __future__ import annotations

import json
import logging
import threading
import time

import pytest
from conftest import wait_for
from test_orders import _ui_context

from labelbridge import errors
from labelbridge.models import parse_job


@pytest.fixture()
def notices():
    """Los avisos que llegarían a la persona (junto al reloj y en la ventana)."""
    received: list[tuple[str, str]] = []
    errors.reset()
    errors.set_notifier(lambda title, text: received.append((title, text)))
    yield received
    errors.set_notifier(None)
    errors.reset()


def _wait_qt(predicate, timeout: float = 10.0) -> bool:
    from PySide6.QtWidgets import QApplication

    deadline = time.time() + timeout
    while time.time() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


# --- el mensaje para la persona --------------------------------------------------------------------------
def test_bugs_say_what_kind_of_error_instead_of_a_cryptic_word():
    assert errors.user_message(KeyError("id")) == "Error inesperado (KeyError). El detalle quedó en Registro."
    assert errors.user_message(RuntimeError("")) == "Error inesperado (RuntimeError). El detalle quedó en Registro."
    assert errors.user_message(OSError("Disco lleno")) == "Disco lleno"  # los esperados ya dicen qué pasó
    assert errors.is_bug(TypeError()) and not errors.is_bug(ConnectionError())


def test_report_logs_the_trace_and_notifies_once_per_minute(notices, caplog):
    caplog.set_level(logging.ERROR)
    try:
        raise ValueError("La impresora no responde")
    except ValueError as exc:
        errors.report("Imprimir", exc)
        errors.report("Imprimir", exc)  # el mismo aviso en ráfaga no se repite
    assert notices == [("Imprimir", "La impresora no responde")]
    assert any(record.exc_info for record in caplog.records)  # con la traza


# --- tareas en segundo plano -----------------------------------------------------------------------------
def test_background_task_errors_reach_the_screen_or_a_notice(qapp, notices, caplog):
    from labelbridge.ui.widgets import run_async

    caplog.set_level(logging.WARNING)
    shown: list[str] = []
    run_async(lambda: {}["id"], None, shown.append)
    assert _wait_qt(lambda: shown)
    assert shown == ["Error inesperado (KeyError). El detalle quedó en Registro."]
    assert any("tarea en segundo plano" in record.getMessage() for record in caplog.records)
    run_async(lambda: 1 / 0, what="Cargar fotos")  # sin quien la atienda: se avisa igual
    assert _wait_qt(lambda: notices)
    assert notices[0][0] == "Cargar fotos"


# --- el motor de impresión -------------------------------------------------------------------------------
def test_print_engine_survives_an_unexpected_error_and_says_so(core, notices, monkeypatch):
    from labelbridge import events as ev

    real = core.store.next_ready
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is locked")
        return real()

    monkeypatch.setattr(core.store, "next_ready", flaky)
    core.engine.start()
    assert wait_for(lambda: core.engine.problem == "database is locked", 5)
    assert ("engine", {"problem": "database is locked"}) in core.received
    assert notices and notices[0][0] == "Problema al imprimir"
    core.store.add(parse_job({"id": "despues-del-error", "content": "^XA^XZ"}), "api")
    core.engine.wake()
    assert wait_for(lambda: core.store.list()[0]["status"] == "done", 20)  # siguió imprimiendo
    assert core.engine.problem == ""
    assert (ev.ENGINE, {"problem": ""}) in core.received


def test_a_job_never_stays_printing_if_its_failure_cannot_be_saved(core, monkeypatch):
    job, _ = core.store.add(parse_job({"id": "atascado", "content": "^XA^XZ", "printer": "no-existe"}), "api")

    def broken(*_args, **_kwargs):
        raise RuntimeError("disk I/O error")

    monkeypatch.setattr(core.engine, "_handle_failure", broken)
    with pytest.raises(RuntimeError):
        core.engine._run(core.store.get(job["id"]))
    assert core.store.get(job["id"])["status"] == "queued"  # volvió a la cola


def test_spooler_warnings_turn_into_a_print_error():
    from labelbridge.printing.outputs import QT_LOGGER, _spooler_watch

    qt_log = logging.getLogger(QT_LOGGER)
    with _spooler_watch() as spooler:
        qt_log.warning("QWin32PrintEngine::end: EndDoc failed (El controlador no acepta el trabajo)")
        qt_log.warning("[qt.qpa.fonts] algo sin importancia")
        other = threading.Thread(target=lambda: qt_log.warning("StartDoc failed en otro hilo"))
        other.start()
        other.join()
    assert spooler.failures == ["QWin32PrintEngine::end: EndDoc failed (El controlador no acepta el trabajo)"]


# --- servicios en segundo plano --------------------------------------------------------------------------
def test_a_service_that_crashes_restarts_by_itself_and_warns(core, notices, monkeypatch):
    from labelbridge.connectors import base

    monkeypatch.setattr(base, "RESTART_FIRST_SECONDS", 0.05)
    runs = {"n": 0}

    class Flaky(base.BackgroundService):
        name, label = "prueba", "Servicio de prueba"

        def enabled(self) -> bool:
            return True

        def run(self, stop, wake) -> None:
            runs["n"] += 1
            if runs["n"] == 1:
                raise KeyError("cursor")
            stop.wait(5)

    service = Flaky(core.events)
    service.start()
    try:
        assert wait_for(lambda: runs["n"] >= 2, 5)  # volvió a arrancar solo
        assert notices[0][0] == "Servicio de prueba se detuvo por un error"
        assert "Error inesperado (KeyError)" in notices[0][1]
    finally:
        service.stop(1)


# --- configuración y secretos ----------------------------------------------------------------------------
def test_unreadable_settings_are_kept_aside_and_reported(home):
    from labelbridge.config import ConfigManager

    path = home / "config.json"
    path.write_text("{ esto no es json", encoding="utf-8")
    cfg = ConfigManager(path)
    assert cfg.load_problem and "valores de fábrica" in cfg.load_problem
    copies = list(home.glob("config.corrupt-*.json"))
    assert len(copies) == 1 and copies[0].read_text(encoding="utf-8") == "{ esto no es json"


class _FakeKeyring:
    def __init__(self) -> None:
        self.fail_reads = False
        self.fail_deletes = False
        self.mac_denied_deletes = False  # el Llavero de la Mac: todo error al borrar llega como PasswordDeleteError
        self.values: dict[str, str] = {}
        self.reads = 0
        self.writes = 0

    def get_password(self, _service, name):
        self.reads += 1
        if self.fail_reads:
            raise OSError("El usuario negó el acceso al Llavero")
        return self.values.get(name)

    def set_password(self, _service, name, value):
        self.writes += 1
        self.values[name] = value

    def delete_password(self, _service, name):
        from keyring.errors import PasswordDeleteError

        if self.fail_deletes:
            raise OSError("Acceso denegado")
        if self.mac_denied_deletes:
            raise PasswordDeleteError("Access Denied")
        if name not in self.values:
            raise PasswordDeleteError("no existe")
        del self.values[name]


def test_secret_reads_retry_after_a_keychain_error_and_deletes_never_fail_silently(home):
    from labelbridge.secret_store import SecretStore, SecretStoreError

    store = SecretStore(home / "secrets.json", backend="file")
    fake = _FakeKeyring()
    store._keyring = fake
    fake.values["clave"] = "valor"
    fake.fail_reads = True
    assert store.get("clave") == ""  # esta vez no se pudo leer…
    reads = fake.reads
    assert store.get("clave") == "" and fake.reads == reads  # …y no se vuelve a preguntar en cada petición
    fake.fail_reads = False
    store._failed_at["clave"] -= 61  # pasó un minuto
    assert store.get("clave") == "valor"  # vuelve a preguntar (no quedó «vacío» guardado)
    store.set("no-estaba", "")  # borrar lo que no existe no es un error
    fake.fail_deletes = True
    with pytest.raises(SecretStoreError):
        store.set("clave", "")
    assert store.get("clave") == "valor"  # dice la verdad: sigue guardado
    fake.fail_deletes, fake.mac_denied_deletes = False, True
    with pytest.raises(SecretStoreError):  # en la Mac el error llega disfrazado de «no existe»: se comprueba
        store.set("clave", "")
    writes = fake.writes
    assert store.update("clave", "valor") is False and fake.writes == writes  # sin cambios, no se toca el almacén


def test_local_api_key_is_not_replaced_when_the_keychain_cannot_be_read(core, monkeypatch):
    from labelbridge.secret_store import LOCAL_API_KEY, SecretStoreError
    from labelbridge.services import Services

    def denied(name, strict=False):
        raise SecretStoreError("No se pudo leer la clave del almacén seguro")

    monkeypatch.setattr(core.secrets, "get", denied)
    created = []
    monkeypatch.setattr(core.secrets, "set", lambda name, value: created.append(name))
    notice = Services._ensure_local_api_key(core.secrets)
    assert LOCAL_API_KEY not in created  # no se inventa otra clave que dejaría fuera a las integraciones
    assert notice and notice[0] == "No se pudo leer la clave de la API local"  # la app lo muestra con la ventana lista


# --- conectores ------------------------------------------------------------------------------------------
def test_damaged_purchase_record_is_kept_and_reported(core):
    from labelbridge.connectors.shopify import ShopifyService

    shopify = ShopifyService(core.cfg, core.secrets, core.store, core.engine, core.events)
    core.cfg.data.shopify.shop = "tienda-prueba.myshopify.com"
    key = shopify._key("pending")
    core.store.kv_set(key, "{roto")
    assert shopify._pending() == {}
    assert core.store.kv_get(key) == "{}"
    assert "{roto" in core.store.kv_items(f"{key}:danado").values()  # el texto quedó aparte
    titles = [data["title"] for kind, data in core.received if kind == "notify"]
    assert "Revisa los pedidos recientes" in titles


def test_metafield_without_a_url_is_reported_once(core):
    from labelbridge.connectors.shopify import ShopifyService

    core.cfg.data.shopify.shop = "tienda-prueba.myshopify.com"
    shopify = ShopifyService(core.cfg, core.secrets, core.store, core.engine, core.events)
    shopify._invalid_metafield("123", "#1001", "pendiente")
    shopify._invalid_metafield("123", "#1001", "pendiente")
    notes = [data for kind, data in core.received if kind == "notify"]
    assert len(notes) == 1 and notes[0]["title"] == "Pedido #1001: el metafield no trae una etiqueta"


def test_invalid_n8n_job_is_reported_to_the_person(core):
    from labelbridge.connectors.mqtt import MqttService

    mqtt = MqttService(core.cfg, core.secrets, core.store, core.engine, core.events)
    published = []
    assert mqtt.handle_message("labelbridge/x/jobs", b"{no es json", lambda *a, **k: published.append(a)) == 0
    notes = [data for kind, data in core.received if kind == "notify"]
    assert notes and notes[0]["kind"] == "print_error" and notes[0]["level"] == "error"
    assert published  # n8n también se entera (tema de errores)


def test_email_alerts_that_cannot_go_out_are_reported_once(core):
    from labelbridge.connectors.email import EmailSender

    core.cfg.data.email.enabled = True  # pedido, pero sin servidor ni cuenta
    sender = EmailSender(core.cfg, core.secrets, core.events)
    assert sender.send_later("Pedido nuevo", "#1001") is False
    assert sender.send_later("Pedido nuevo", "#1002") is False
    notes = [data for kind, data in core.received if kind == "notify" and data["title"] == "Los avisos por email no salen"]
    assert len(notes) == 1


# --- la ventana ------------------------------------------------------------------------------------------
def test_errors_show_at_the_top_of_the_window(core, qapp):
    from labelbridge.ui.main_window import MainWindow
    from labelbridge.ui.notice_bar import NoticeBar

    window = MainWindow(_ui_context(core))
    window.show()
    try:
        core.events.notify("No se pudo imprimir", "Pedido #1001: la impresora no responde", "error",
                           kind="print_error")
        assert _wait_qt(lambda: window.notice.isVisible())
        assert window.notice.title.text() == "No se pudo imprimir"
        core.events.notify("Problema con n8n", "Sin conexión", "error")
        assert _wait_qt(lambda: "(y 1 aviso(s) más)" in window.notice.title.text())
        window.notice.logs.click()  # «Ver registro»
        assert window.stack.currentWidget() is window.pages["logs"]
        window.notice.dismiss()
        assert not window.notice.isVisible()
        core.events.notify("Etiqueta impresa", "#1001", kind="printed")  # lo que no es error no ocupa la barra
        qapp.processEvents()
        assert not window.notice.isVisible()
    finally:
        window.quitting = True
        window.close()
    bar = NoticeBar()
    bar.show_notice("Aviso", "Se va solo", "warn")
    assert bar._timer.isActive()  # los que no son errores se van solos


def test_restart_that_cannot_start_keeps_the_app_open(core, qapp, monkeypatch):
    from PySide6.QtCore import QProcess

    from labelbridge.ui import main_window as main_window_module
    from labelbridge.ui.main_window import MainWindow

    window = MainWindow(_ui_context(core))
    quit_calls, shown = [], []
    window.quit_app = lambda: quit_calls.append(True)
    monkeypatch.setattr(QProcess, "startDetached", staticmethod(lambda *a, **k: (False, -1)))
    monkeypatch.setattr(main_window_module, "error", lambda *a: shown.append(a))
    window.restart_app()
    assert not quit_calls and shown  # no se cierra sin la copia nueva, y lo dice
    window.quitting = True
    window.close()


def test_a_late_reply_to_a_closed_window_is_ignored_but_real_errors_are_not():
    from labelbridge.ui.widgets import window_closed

    assert window_closed(RuntimeError("Internal C++ object (QLabel) already deleted."))
    assert not window_closed(RuntimeError("No se pudo leer la impresora"))


def test_status_bar_shows_an_engine_problem(core, qapp):
    from labelbridge.ui.main_window import MainWindow

    window = MainWindow(_ui_context(core))
    core.engine.problem = "database is locked"
    window._update_status()
    assert (window.status.state, window.status.title, window.status.detail) == (
        "error", "Problema al imprimir", "database is locked")
    core.engine.problem = ""
    window._update_status()
    assert window.status.title == "Listo para imprimir"
    window.quitting = True
    window.close()


def test_update_record_that_cannot_be_read_is_logged(caplog):
    from labelbridge.updater import pending_whats_new

    caplog.set_level(logging.WARNING)
    assert pending_whats_new(lambda _key: json.dumps({"no": "sirve"})[:5]) is None
    assert any("ilegible" in record.getMessage() for record in caplog.records)


def test_a_failing_keychain_never_leaves_the_shopify_page_half_saved(core, monkeypatch):
    from labelbridge.secret_store import SecretStoreError
    from labelbridge.ui.pages.shopify import ShopifyPage

    page = ShopifyPage(_ui_context(core))
    page.shop.setText("otra-tienda.myshopify.com")

    def broken(name, value):
        raise SecretStoreError("No se pudo borrar shopify_access_token del almacén seguro: sigue guardado")

    monkeypatch.setattr(core.secrets, "update", broken)
    assert "sigue guardado" in page._collect()
    assert core.cfg.data.shopify.shop == ""  # nada cambió a medias


def test_routes_card_follows_a_new_default_printer(core, qapp):
    from labelbridge.config import PrinterProfile
    from labelbridge.printing import routing
    from labelbridge.ui.pages.shopify import ShopifyPage

    office = PrinterProfile(alias="oficina", kind="system", system_name="HP_LaserJet")
    core.cfg.data.printers.append(office)
    page = ShopifyPage(_ui_context(core))
    core.cfg.data.general.default_printer_id = office.id
    core.cfg.save("general")  # «Predeterminada» en Impresoras guarda «general»
    _wait_qt(lambda: "oficina" in page.routes.combos[routing.SHIPPING].itemText(0), 3)
    assert page.routes.combos[routing.SHIPPING].itemText(0) == "Predeterminada (oficina)"


def test_notice_count_keeps_going_while_the_window_is_hidden(qapp):
    from PySide6.QtWidgets import QWidget

    from labelbridge.ui.notice_bar import NoticeBar

    hidden_window = QWidget()  # la ventana escondida en la bandeja
    bar = NoticeBar()
    bar.setParent(hidden_window)
    for index in range(3):
        bar.show_notice(f"Error {index}", "")
    assert bar.title.text() == "Error 2 (y 2 aviso(s) más)"


def test_an_error_while_stopping_is_not_reported_as_a_crash(core, notices):
    from labelbridge.connectors import base

    class Stopping(base.BackgroundService):
        name, label = "prueba", "Servicio de prueba"

        def enabled(self) -> bool:
            return True

        def run(self, stop, wake) -> None:
            stop.wait(5)
            raise ConnectionError("socket cerrado al detener")

    service = Stopping(core.events)
    service.start()
    service.stop(2)
    assert notices == [] and service.state == "off"
