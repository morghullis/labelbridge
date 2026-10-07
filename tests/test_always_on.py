"""Funcionar siempre: el relevo entre la copia de segundo plano (sin sesión) y la app con ventana, el modo de segundo
plano trabajando y cerrándose, las tareas de Windows bien formadas y la tarjeta de Ajustes."""

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
from labelbridge.app import _ensure_qt_plugin_path
_ensure_qt_plugin_path()
from PySide6.QtCore import QCoreApplication
from labelbridge.system.single_instance import BACKGROUND, SingleInstance
app = QCoreApplication([])
instance = SingleInstance(BACKGROUND, key={key!r})
assert instance.acquire()
instance.handoff_requested.connect(app.quit)
print("ready", flush=True)
app.exec()
instance.release()
print("bye", flush=True)
"""


def test_the_window_takes_over_from_the_background_copy(qapp):
    from labelbridge.system.single_instance import APP, BACKGROUND, SingleInstance

    key = f"LabelBridge-prueba-{os.getpid()}-{time.time_ns()}"
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    holder = subprocess.Popen([sys.executable, "-c", HOLDER.format(root=str(ROOT), key=key)], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, env=env)
    try:
        assert holder.stdout.readline().strip() == "ready"
        assert not SingleInstance(BACKGROUND, key=key).acquire()  # otra de segundo plano no la reemplaza
        window = SingleInstance(APP, key=key)
        assert window.acquire()  # la app pidió el relevo, la de segundo plano se cerró y la app quedó
        assert holder.wait(timeout=30) == 0 and "bye" in holder.stdout.read()
        assert not SingleInstance(BACKGROUND, key=key).acquire()  # con la app abierta, el segundo plano no arranca
        window.release()
    finally:
        if holder.poll() is None:
            holder.kill()


def test_the_background_mode_runs_and_closes_cleanly(qapp, home, caplog):
    import logging

    from labelbridge import background

    caplog.set_level(logging.INFO)
    key = f"LabelBridge-prueba-{os.getpid()}-{time.time_ns()}"
    from labelbridge import i18n

    try:
        assert background.run(["LabelBridge", "--background", "--exit-after", "0.5", "--instance-key", key],
                              app=qapp) == 0
    finally:
        i18n.set_language("es")  # usa el idioma de su configuración: no debe cambiar el de las demás pruebas
    assert "trabaja en segundo plano" in caplog.text and "se cerró" in caplog.text


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
