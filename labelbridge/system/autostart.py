"""Abrir LabelBridge al encender el equipo: clave Run del usuario en Windows, agente de inicio de sesión en Mac.

Solo se aplica en la app instalada (.exe o .app): en desarrollo no se toca nada del sistema.
"""

from __future__ import annotations

import logging
import plistlib
import sys
from pathlib import Path

from .. import APP_NAME
from ..paths import is_frozen
from .platform import mac_app_bundle, mac_opened_from_disk_image

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MAC_AGENT_LABEL = "com.labelbridge.app"


def supported() -> bool:
    # En la Mac, solo con la app ya en Aplicaciones (no desde el .dmg).
    return sys.platform in ("win32", "darwin") and is_frozen() and not mac_opened_from_disk_image()


def _command() -> str:
    return f'"{sys.executable}" --minimized'


def mac_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{MAC_AGENT_LABEL}.plist"


def mac_agent(bundle: Path | None = None) -> dict:
    """El agente que abre la app (en segundo plano) al iniciar sesión.

    Ejecuta la app misma, no «open»: así Ajustes del Sistema → Ítems de inicio la muestra como LabelBridge.
    """
    bundle = bundle or mac_app_bundle()
    return {
        "Label": MAC_AGENT_LABEL,
        "ProgramArguments": [str(bundle / "Contents" / "MacOS" / APP_NAME), "--minimized"],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},  # si se cierra por un error, launchd la vuelve a abrir (Salir no)
        "ProcessType": "Interactive",
        "LimitLoadToSessionType": "Aqua",  # solo en la sesión con escritorio (no por SSH)
        "AssociatedBundleIdentifiers": [MAC_AGENT_LABEL],
    }


def is_enabled() -> bool:
    if sys.platform == "darwin":
        return mac_agent_path().exists()
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
        log.debug("Inicio automático no aplicado (solo en la app instalada)")
        return
    if sys.platform == "darwin":
        path = mac_agent_path()
        if enabled:
            content = plistlib.dumps(mac_agent())
            # Solo si cambió: cada archivo nuevo hace que macOS avise «se agregaron ítems en segundo plano».
            if not path.exists() or path.read_bytes() != content:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        else:
            path.unlink(missing_ok=True)
        log.info("Abrir al iniciar sesión %s", "activado" if enabled else "desactivado")
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
