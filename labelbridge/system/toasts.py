"""Avisos nativos de Windows 10/11: con el nombre y el logo de LabelBridge, título, texto, de qué equipo vienen y un
botón que lleva directo a lo que avisan (pedidos, historial, reporte…). Quedan en el Centro de notificaciones, agrupados
bajo LabelBridge, y el sonido lo pone la app (Ajustes → Avisos), no Windows.

Usa las API de Windows (pywinrt). Para que Windows muestre el nombre y el logo, la app se identifica con su AUMID
(SetCurrentProcessExplicitAppUserModelID) y lo registra en HKCU\\Software\\Classes\\AppUserModelId (sin administrador);
el acceso directo del instalador lleva el mismo. Si algo de esto falta (otra plataforma, Windows viejo), `create()`
devuelve None y avisa la bandeja como antes.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape, quoteattr

log = logging.getLogger(__name__)

AUMID = "LabelBridge.App"  # el mismo del acceso directo (packaging/installer.iss)
REGISTRY_KEY = r"Software\Classes\AppUserModelId" + "\\" + AUMID
OPEN = "open:"  # argumentos de un botón: «open:<pantalla>»
_NOT_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")  # caracteres de control que un aviso no puede llevar


def _xml_text(text: str) -> str:
    return escape(_NOT_XML.sub(" ", str(text)))


def toast_xml(title: str, message: str, *, target: str, action: str, attribution: str = "", logo: str = "") -> str:
    """El aviso en el formato de Windows (ToastGeneric): título, texto, de dónde viene, el logo redondo y dos botones
    (ir a lo que avisa y «Descartar», el de Windows). Silencioso: el sonido lo pone la app."""
    lines = [
        f"<toast launch={quoteattr(OPEN + target)} activationType=\"foreground\">",
        "<visual><binding template=\"ToastGeneric\">",
        f"<text hint-maxLines=\"2\">{_xml_text(title)}</text>",
        f"<text>{_xml_text(message)}</text>",
    ]
    if attribution:
        lines.append(f"<text placement=\"attribution\">{_xml_text(attribution)}</text>")
    if logo:
        lines.append(f"<image placement=\"appLogoOverride\" hint-crop=\"circle\" src={quoteattr(logo)}/>")
    lines += [
        "</binding></visual>",
        "<actions>",
        f"<action content={quoteattr(_NOT_XML.sub(' ', action))} arguments={quoteattr(OPEN + target)} "
        "activationType=\"foreground\"/>",
        "<action content=\"\" arguments=\"dismiss\" activationType=\"system\"/>",
        "</actions>",
        "<audio silent=\"true\"/>",
        "</toast>",
    ]
    return "".join(lines)


def target_of(arguments: str) -> str | None:
    """La pantalla a abrir según el botón (o el clic en el aviso); None si no es nuestro."""
    return arguments[len(OPEN):] if arguments.startswith(OPEN) else None


def set_process_identity() -> None:
    """La app se presenta a Windows como LabelBridge (avisos, barra de tareas): antes de abrir ventanas."""
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(AUMID))
    except (AttributeError, OSError) as exc:  # Windows muy viejo: los avisos usan la bandeja
        log.warning("Windows no aceptó la identidad de la app para los avisos: %s", exc)


def register(icon: Path) -> None:
    """El nombre y el logo con que Windows muestra los avisos (para el usuario actual, sin pedir administrador)."""
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "LabelBridge")
        winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, str(icon))
        winreg.SetValueEx(key, "IconBackgroundColor", 0, winreg.REG_SZ, "FF0A0F0D")


class WindowsToasts:
    """Muestra avisos nativos; `on_open(pantalla)` se llama (desde otro hilo) al pulsar el aviso o su botón."""

    def __init__(self, on_open: Callable[[str], None], logo: Path) -> None:
        from winrt.windows.data.xml.dom import XmlDocument
        from winrt.windows.ui.notifications import ToastActivatedEventArgs, ToastNotification, ToastNotificationManager

        self._XmlDocument, self._ToastNotification = XmlDocument, ToastNotification
        self._ActivatedArgs = ToastActivatedEventArgs
        self.notifier = ToastNotificationManager.create_toast_notifier_with_id(AUMID)
        self.on_open = on_open
        self.logo = logo.as_uri() if logo.is_file() else ""
        self._shown: list = []  # los avisos vivos (si Python los suelta, sus botones dejan de responder)

    def show(self, title: str, message: str, *, target: str, action: str, attribution: str = "",
             group: str = "") -> None:
        document = self._XmlDocument()
        document.load_xml(toast_xml(title, message, target=target, action=action, attribution=attribution,
                                    logo=self.logo))
        toast = self._ToastNotification(document)
        if group:
            toast.group = group
        toast.add_activated(self._activated)
        self.notifier.show(toast)
        self._shown = [*self._shown[-19:], toast]

    def _activated(self, _sender, args) -> None:
        try:
            target = target_of(args.as_(self._ActivatedArgs).arguments)
        except Exception:  # noqa: BLE001  un aviso que no se entiende no debe tumbar nada
            log.warning("No se pudo leer el clic en el aviso de Windows", exc_info=True)
            return
        if target is not None:
            self.on_open(target)


def create(on_open: Callable[[str], None], logo: Path) -> WindowsToasts | None:
    """Los avisos nativos si este Windows los permite; None si no (avisa la bandeja)."""
    if sys.platform != "win32":
        return None
    try:
        register(logo)
        return WindowsToasts(on_open, logo)
    except Exception as exc:  # noqa: BLE001  sin avisos nativos, la bandeja avisa igual: que quede anotado por qué
        log.warning("Avisos nativos de Windows no disponibles (se usa la bandeja): %s", exc)
        return None
