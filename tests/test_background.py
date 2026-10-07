"""Funcionar siempre, sin sesión iniciada: el relevo entre la copia de segundo plano y la app con ventana (nunca dos
trabajando), el modo de segundo plano trabajando y cerrándose, las tareas de Windows bien formadas y la tarjeta de
Ajustes."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}

HOLDER = """
import sys
sys.path.insert(0, {root!r})
from pathlib import Path
from labelbridge.app import _ensure_qt_plugin_path
_ensure_qt_plugin_path()
from PySide6.QtCore import QCoreApplication
from labelbridge.system.single_instance import BACKGROUND, SingleInstance
app = QCoreApplication([])
instance = SingleInstance(BACKGROUND, key={key!r}, lock_path=Path({lock!r}))
assert instance.acquire()
instance.handoff_requested.connect(app.quit)
print("ready", flush=True)
import time
if {stuck!r}:
    time.sleep(600)  # trabada: tiene el candado pero no atiende a nadie
time.sleep({delay!r})  # arrancando: todavía sin contestar
app.exec()
instance.release()
print("bye", flush=True)
"""


def _holder(tmp_path, stuck: bool = False, delay: float = 0.0):
    """Una copia de segundo plano en otro proceso (con su candado); devuelve el proceso, la clave y el candado."""
    import queue
    import threading

    key = f"LabelBridge-prueba-{os.getpid()}-{time.time_ns()}"
    lock = tmp_path / "trabajando.lock"
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    process = subprocess.Popen([sys.executable, "-c", HOLDER.format(root=str(ROOT), key=key, lock=str(lock), stuck=stuck, delay=delay)],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    lines: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True).start()
    assert lines.get(timeout=30).strip() == "ready"  # nunca cuelga la prueba
    return process, key, lock, lines


def test_the_window_takes_over_from_the_background_copy(qapp, tmp_path):
    from labelbridge.system.single_instance import APP, BACKGROUND, SingleInstance

    holder, key, lock, lines = _holder(tmp_path)
    try:
        assert not SingleInstance(BACKGROUND, key=key, lock_path=lock).acquire()  # otra de segundo plano no la reemplaza
        window = SingleInstance(APP, key=key, lock_path=lock)
        assert window.acquire()  # pidió el relevo y quedó con el candado cuando la otra lo soltó
        assert holder.wait(timeout=30) == 0 and lines.get(timeout=5).strip() == "bye"
        assert not SingleInstance(BACKGROUND, key=key, lock_path=lock).acquire()  # con la app, el segundo plano no arranca
        window.release()
    finally:
        if holder.poll() is None:
            holder.kill()


def test_a_stuck_background_copy_is_closed_so_the_window_can_start(qapp, tmp_path, monkeypatch):
    from labelbridge.system import single_instance

    monkeypatch.setattr(single_instance, "ANSWER_SECONDS", 1)
    monkeypatch.setattr(single_instance, "HANDOFF_SECONDS", 2)
    holder, key, lock, _lines = _holder(tmp_path, stuck=True)
    try:
        window = single_instance.SingleInstance(single_instance.APP, key=key, lock_path=lock)
        assert window.acquire()  # no contestó ni soltó el candado: se cerró y la app arrancó
        assert holder.wait(timeout=20) != 0
        window.release()
    finally:
        if holder.poll() is None:
            holder.kill()


def test_a_background_copy_still_starting_gets_the_handoff_once_it_answers(qapp, tmp_path, monkeypatch):
    """Si la de segundo plano tarda en contestar (arrancando), la app sigue preguntando: no la cierra a la fuerza."""
    from labelbridge.system import single_instance

    monkeypatch.setattr(single_instance, "ANSWER_SECONDS", 1)
    monkeypatch.setattr(single_instance, "HANDOFF_SECONDS", 20)
    holder, key, lock, lines = _holder(tmp_path, delay=4)
    try:
        window = single_instance.SingleInstance(single_instance.APP, key=key, lock_path=lock)
        assert window.acquire()
        assert holder.wait(timeout=30) == 0 and lines.get(timeout=5).strip() == "bye"  # se cerró sola, no a la fuerza
        window.release()
    finally:
        if holder.poll() is None:
            holder.kill()


def test_a_lock_file_that_cannot_be_opened_does_not_crash(tmp_path):
    from labelbridge.system.single_instance import WorkLock

    (tmp_path / "x.lock").mkdir()  # abrirlo falla (como con un antivirus que lo tiene tomado)
    assert not WorkLock(tmp_path / "x.lock").try_acquire()


def test_the_lock_is_the_real_guard(tmp_path):
    from labelbridge.system.single_instance import WorkLock

    first, second = WorkLock(tmp_path / "x.lock"), WorkLock(tmp_path / "x.lock")
    assert first.try_acquire() and not second.try_acquire()
    assert first.holder() == (os.getpid(), "app")
    first.release()
    assert second.try_acquire()
    second.release()


def _accepted_config(local_api: bool = False):
    from labelbridge import legal
    from labelbridge.config import ConfigManager
    from labelbridge.paths import config_path, data_dir
    from labelbridge.secret_store import SecretStore
    from labelbridge.security.crypto import DataProtector

    cfg = ConfigManager(config_path(), DataProtector.from_secrets(SecretStore(data_dir() / "secrets.json")))
    cfg.data.general.terms_accepted = legal.TERMS_VERSION
    cfg.data.local_api.enabled = local_api
    cfg.save()


def _run_background(qapp, tmp_path, *extra):
    from labelbridge import background, i18n

    key = f"LabelBridge-prueba-{os.getpid()}-{time.time_ns()}"
    try:
        return background.run(["LabelBridge", "--background", "--instance-key", key, "--lock-file",
                               str(tmp_path / "trabajando.lock"), *extra], app=qapp)
    finally:
        i18n.set_language("es")  # usa el idioma de su configuración: no debe cambiar el de las demás pruebas


def test_the_background_mode_runs_and_closes_cleanly(qapp, home, tmp_path, caplog):
    import logging

    caplog.set_level(logging.INFO)
    _accepted_config()
    assert _run_background(qapp, tmp_path, "--exit-after", "0.8") == 0
    assert "trabaja en segundo plano" in caplog.text and "se cerró" in caplog.text


def test_the_background_waits_for_the_terms_and_for_salir(qapp, home, tmp_path, caplog):
    import logging

    from labelbridge import background

    caplog.set_level(logging.INFO)
    assert _run_background(qapp, tmp_path, "--exit-after", "5") == 0  # configuración nueva: términos sin aceptar
    assert "faltan aceptar los términos" in caplog.text
    _accepted_config()
    background.hold()  # «Salir» en la app
    assert background.on_hold()
    caplog.clear()
    assert _run_background(qapp, tmp_path, "--exit-after", "5") == 0
    assert "eligió «Salir»" in caplog.text and "trabaja en segundo plano" not in caplog.text
    background.release_hold()  # abrir la app la deja trabajar otra vez
    assert not background.on_hold()


def test_the_wake_task_only_wakes_the_pc():
    from labelbridge import app

    assert app.main(["LabelBridge", "--wake"]) == 0  # nada más: no abre ventanas ni toca datos


def _parse(xml: str):
    return ET.fromstring(xml.replace('encoding="UTF-16"', ""))


def test_the_background_task_starts_at_boot_and_every_five_minutes():
    from labelbridge.system import always_on

    task = _parse(always_on.background_task_xml("PC-BODEGA\\Juan & Ana"))
    triggers = task.find("t:Triggers", NS)
    assert triggers.find("t:BootTrigger", NS) is not None
    assert triggers.find("t:TimeTrigger/t:Repetition/t:Interval", NS).text == "PT5M"
    principal = task.find("t:Principals/t:Principal", NS)
    assert principal.find("t:UserId", NS).text == "PC-BODEGA\\Juan & Ana"  # escapado y de vuelta igual
    assert principal.find("t:LogonType", NS).text == "Password"  # corre sin sesión, con las claves de la cuenta
    settings = task.find("t:Settings", NS)
    assert settings.find("t:MultipleInstancesPolicy", NS).text == "IgnoreNew"
    assert settings.find("t:DisallowStartIfOnBatteries", NS).text == "false"
    assert settings.find("t:ExecutionTimeLimit", NS).text == "PT0S"  # sin límite: trabaja hasta el relevo
    assert task.find("t:Actions/t:Exec/t:Arguments", NS).text == "--background"


def test_the_wake_task_wakes_the_pc_without_a_password():
    from labelbridge.system import always_on

    task = _parse(always_on.wake_task_xml("PC\\juan"))
    assert task.find("t:Settings/t:WakeToRun", NS).text == "true"
    assert task.find("t:Principals/t:Principal/t:LogonType", NS).text == "S4U"
    assert task.find("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval", NS).text == "PT10M"
    assert task.find("t:Triggers/t:BootTrigger", NS) is None
    assert task.find("t:Actions/t:Exec/t:Arguments", NS).text == "--wake"


def test_the_settings_card_shows_each_piece_and_asks_before_changing(qapp, monkeypatch):
    from labelbridge.system import always_on
    from labelbridge.ui import always_on_card

    monkeypatch.setattr(always_on, "status", lambda: always_on.Status(30, False, True))
    card = always_on_card.AlwaysOnCard()
    deadline = time.time() + 5
    while card.status is None and time.time() < deadline:
        qapp.processEvents()
    assert card.status is not None
    sleep_pill, sleep_btn = card.rows["sleep"]
    assert "30 min" in sleep_pill.text() and sleep_btn.text() == "Aplicar" and sleep_btn.isEnabled()
    assert card.rows["wake"][1].text() == "Activar" and card.rows["background"][1].text() == "Quitar"
    applied: list[int] = []
    monkeypatch.setattr(always_on, "prevent_sleep", lambda: applied.append(1))
    monkeypatch.setattr(always_on_card, "confirm", lambda *a: False)
    card._act("sleep")  # sin confirmar, no se cambia nada en Windows
    qapp.processEvents()
    assert applied == []


@pytest.mark.skipif(sys.platform == "win32", reason="fuera de Windows")
def test_outside_windows_there_is_nothing_to_configure():
    from labelbridge.system import always_on

    assert not always_on.supported()


def test_the_status_reads_windows_and_warns_when_the_task_fails(monkeypatch):
    from labelbridge.system import always_on, windows_admin

    monkeypatch.setattr(windows_admin, "power_settings", lambda: {"sleep_after_minutes_plugged": 0,
                                                                  "sleep_after_minutes_battery": 15})
    results = {always_on.WAKE_TASK: 0, always_on.BACKGROUND_TASK: always_on.WRONG_PASSWORD}
    monkeypatch.setattr(always_on, "task_result", lambda name: results.get(name))
    status = always_on.status()
    assert status.never_sleeps and status.wake and status.background
    assert "contraseña" in status.background_problem  # nunca un «Activo» engañoso
    results[always_on.BACKGROUND_TASK] = 0x41303  # todavía no corrió: no es un problema
    assert always_on.status().background_problem == ""
    results[always_on.BACKGROUND_TASK] = always_on.DISABLED  # una instalación interrumpida la dejó en pausa
    assert "en pausa" in always_on.status().background_problem


def test_tasks_are_named_per_windows_account(monkeypatch):
    from labelbridge.system import always_on

    monkeypatch.setenv("USERNAME", "juan")
    assert always_on._task_name("Sin sesión") == "LabelBridge - Sin sesión (juan)"
