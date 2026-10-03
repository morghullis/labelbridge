"""Iniciar LabelBridge al encender Windows (clave Run del usuario).

Solo se aplica en la versión instalada (.exe): en desarrollo no se toca el registro.
"""

from __future__ import annotations

import logging
import sys

from .. import APP_NAME
from ..paths import is_frozen

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def supported() -> bool:
    return sys.platform == "win32" and is_frozen()


def _command() -> str:
    return f'"{sys.executable}" --minimized'


def is_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return bool(value)
    except OSError:
        return False


def apply(enabled: bool) -> None:
    if not supported():
        log.debug("Inicio automático no aplicado (solo en la app instalada de Windows)")
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
    log.info("Inicio con Windows %s", "activado" if enabled else "desactivado")
