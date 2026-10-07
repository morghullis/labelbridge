"""Funcionar siempre (Windows): que LabelBridge imprima aunque nadie esté usando la PC.

Tres piezas, cada una la activa la persona en Ajustes → General → «Funcionar siempre»:

- **No suspenderse enchufada:** la energía de Windows sin suspensión ni hibernación con corriente, y cerrar la tapa
  no hace nada (powercfg; si Windows lo pide, con permiso de administrador).
- **Despertarse si la suspenden:** la tarea «LabelBridge - Despertar» despierta la PC cada 10 minutos (temporizador de
  activación); LabelBridge, que seguía abierta, retoma y no la deja volver a dormirse sola. No necesita contraseña.
- **Sin sesión iniciada:** la tarea «LabelBridge - Sin sesión» abre LabelBridge en segundo plano al encender el equipo
  y cada 5 minutos (si ya está abierta, termina enseguida). Corre con la cuenta de la persona aunque no haya iniciado
  sesión, así usa la misma configuración y las mismas claves: por eso Windows guarda la contraseña de esa cuenta en la
  tarea. La pide Windows en su propia ventana de seguridad; LabelBridge no la guarda ni la ve después.

Con la PC apagada (o sin corriente) nada puede funcionar.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from ..i18n import tr

log = logging.getLogger(__name__)

BACKGROUND_TASK = "LabelBridge - Sin sesión"
WAKE_TASK = "LabelBridge - Despertar"
BACKGROUND_EVERY_MINUTES = 5
WAKE_EVERY_MINUTES = 10
# Task Scheduler: crear o actualizar y los tipos de inicio de sesión.
TASK_CREATE_OR_UPDATE = 6
LOGON_PASSWORD, LOGON_S4U, LOGON_INTERACTIVE = 1, 2, 3
ERROR_CANCELLED = 1223
WRONG_PASSWORD = 0x8007052E  # «El nombre de usuario o la contraseña no son correctos»
NOT_FOUND = 0x80070002
# powercfg: con corriente, sin suspensión ni hibernación, la tapa no hace nada y se permiten los despertadores.
POWER_COMMANDS = (
    ("powercfg", "/change", "standby-timeout-ac", "0"),
    ("powercfg", "/change", "hibernate-timeout-ac", "0"),
    ("powercfg", "/setacvalueindex", "SCHEME_CURRENT", "SUB_BUTTONS", "LIDACTION", "0"),
    ("powercfg", "/setacvalueindex", "SCHEME_CURRENT", "SUB_SLEEP", "RTCWAKE", "1"),
    ("powercfg", "/setactive", "SCHEME_CURRENT"),
)


class AlwaysOnError(Exception):
    """Algo no se pudo configurar; el texto es para la persona."""


def supported() -> bool:
    return sys.platform == "win32"


def current_user() -> str:
    """La cuenta de Windows con la que corre la app (DOMINIO\\usuario)."""
    domain = os.environ.get("USERDOMAIN", "")
    user = os.environ.get("USERNAME", "")
    return f"{domain}\\{user}" if domain else user


def app_command() -> Path:
    """El LabelBridge.exe instalado (la tarea lo abre)."""
    return Path(sys.executable)


# --- las tareas --------------------------------------------------------------------------------------------------
def task_xml(*, command: str, arguments: str, user: str, logon: str, every_minutes: int, at_boot: bool,
             wake: bool, description: str, time_limit: str = "PT0S") -> str:
    """Una tarea de Windows (esquema 1.4): repetir cada `every_minutes` (y al encender, si `at_boot`), sin dos a la vez,
    también con batería, sin límite de tiempo y, con `wake`, despertando la PC."""
    boot = "<BootTrigger><Enabled>true</Enabled><Delay>PT1M</Delay></BootTrigger>" if at_boot else ""
    start = datetime.now().replace(microsecond=0, second=0).isoformat()
    return (
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
        f"<RegistrationInfo><Author>LabelBridge</Author><Description>{escape(description)}</Description>"
        "</RegistrationInfo>"
        f"<Triggers>{boot}<TimeTrigger><Repetition><Interval>PT{int(every_minutes)}M</Interval>"
        "<StopAtDurationEnd>false</StopAtDurationEnd></Repetition>"
        f"<StartBoundary>{start}</StartBoundary><Enabled>true</Enabled></TimeTrigger></Triggers>"
        f"<Principals><Principal id=\"Author\"><UserId>{escape(user)}</UserId><LogonType>{logon}</LogonType>"
        "<RunLevel>LeastPrivilege</RunLevel></Principal></Principals>"
        "<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
        "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>"
        "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><AllowHardTerminate>true</AllowHardTerminate>"
        "<StartWhenAvailable>true</StartWhenAvailable><RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>"
        "<IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>"
        "<AllowStartOnDemand>true</AllowStartOnDemand><Enabled>true</Enabled><Hidden>false</Hidden>"
        f"<RunOnlyIfIdle>false</RunOnlyIfIdle><WakeToRun>{'true' if wake else 'false'}</WakeToRun>"
        f"<ExecutionTimeLimit>{time_limit}</ExecutionTimeLimit><Priority>7</Priority></Settings>"
        f"<Actions Context=\"Author\"><Exec><Command>{escape(command)}</Command>"
        f"<Arguments>{escape(arguments)}</Arguments></Exec></Actions>"
        "</Task>"
    )


def background_task_xml(user: str, logon: str = "Password") -> str:
    return task_xml(command=str(app_command()), arguments="--background", user=user, logon=logon,
                    every_minutes=BACKGROUND_EVERY_MINUTES, at_boot=True, wake=False,
                    description=tr("Imprime los pedidos aunque nadie haya iniciado sesión en la PC (LabelBridge en "
                                   "segundo plano). Si LabelBridge ya está abierta, termina enseguida."))


def wake_task_xml(user: str) -> str:
    return task_xml(command=str(app_command()), arguments="--wake", user=user, logon="S4U",
                    every_minutes=WAKE_EVERY_MINUTES, at_boot=False, wake=True, time_limit="PT1M",
                    description=tr("Despierta la PC si la suspenden, para que LabelBridge siga imprimiendo."))


def _folder():
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    service = win32com.client.Dispatch("Schedule.Service")
    service.Connect()
    return service.GetFolder("\\")


def _register(name: str, xml: str, user: str, password: str | None, logon: int) -> None:
    import pywintypes

    try:
        _folder().RegisterTask(name, xml, TASK_CREATE_OR_UPDATE, user, password, logon, None)
    except pywintypes.com_error as exc:
        if _hresult(exc) == WRONG_PASSWORD:
            raise AlwaysOnError(tr("Windows dice que la contraseña no es correcta. Vuelve a intentarlo con la "
                                   "contraseña de tu cuenta de Windows (no el PIN).")) from None
        raise AlwaysOnError(tr("Windows no dejó crear la tarea «{0}»: {1}", name, _com_text(exc))) from None


def _hresult(exc) -> int:
    """El código de Windows del error (el de adentro, si la llamada lo envolvió), sin signo."""
    info = getattr(exc, "excepinfo", None)
    code = info[5] if info and info[5] else getattr(exc, "hresult", 0)
    return int(code) & 0xFFFFFFFF


def _com_text(exc) -> str:
    info = getattr(exc, "excepinfo", None)
    return str((info and info[2]) or exc.strerror or exc)


def remove_task(name: str) -> None:
    import pywintypes

    try:
        _folder().DeleteTask(name, 0)
    except pywintypes.com_error as exc:
        if _hresult(exc) != NOT_FOUND:  # si ya no estaba, no hay nada que quitar
            raise AlwaysOnError(tr("Windows no dejó quitar la tarea «{0}»: {1}", name, _com_text(exc))) from None


def task_exists(name: str) -> bool:
    import pywintypes

    try:
        _folder().GetTask(name)
        return True
    except pywintypes.com_error:
        return False


def enable_wake() -> None:
    """La tarea que despierta la PC cada 10 minutos (sin contraseña) y los despertadores permitidos en la energía."""
    user = current_user()
    _register(WAKE_TASK, wake_task_xml(user), user, None, LOGON_S4U)
    prevent_sleep()  # incluye «permitir temporizadores de activación»


def enable_background(parent_window: int = 0) -> bool:
    """La tarea «sin sesión»: Windows pide la contraseña de la cuenta en su ventana de seguridad. False si se canceló."""
    user = current_user()
    password = ask_windows_password(user, parent_window)
    if password is None:
        return False
    # La contraseña va directo a la tarea de Windows (que la guarda cifrada); LabelBridge no la escribe en ningún lado.
    _register(BACKGROUND_TASK, background_task_xml(user), user, password, LOGON_PASSWORD)
    log.info("Funcionar siempre: tarea «%s» creada para %s", BACKGROUND_TASK, user)
    return True


# --- la energía ------------------------------------------------------------------------------------------------------
def prevent_sleep() -> None:
    """Con corriente: sin suspensión ni hibernación, la tapa no hace nada y se permiten los despertadores."""
    from . import windows_admin

    failed = []
    for command in POWER_COMMANDS:
        result = subprocess.run(command, capture_output=True, timeout=20,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0:
            failed.append(command)
    if failed:  # algunos equipos lo permiten solo como administrador: Windows pide el permiso
        try:
            windows_admin._run_elevated_script("\n".join(f"  & {' '.join(command)}" for command in failed))
        except RuntimeError as exc:
            raise AlwaysOnError(tr("No se pudo cambiar la energía de Windows: {0}", exc)) from None
    log.info("Funcionar siempre: la PC no se suspende enchufada")


@dataclass
class Status:
    sleep_minutes_ac: int | None  # 0 = nunca; None = no se pudo leer
    wake: bool
    background: bool

    @property
    def never_sleeps(self) -> bool:
        return self.sleep_minutes_ac == 0


def status() -> Status:
    from . import windows_admin

    try:
        sleep_ac = int(windows_admin.power_settings()["sleep_after_minutes_plugged"])
    except Exception:  # noqa: BLE001  sin dato se muestra «no se pudo leer»
        log.warning("Funcionar siempre: no se pudo leer la energía de Windows", exc_info=True)
        sleep_ac = None
    return Status(sleep_ac, task_exists(WAKE_TASK), task_exists(BACKGROUND_TASK))


# --- la contraseña, en la ventana de seguridad de Windows -----------------------------------------------------------
def ask_windows_password(user: str, parent_window: int = 0) -> str | None:
    """La ventana «Seguridad de Windows» con la cuenta ya escrita: la persona escribe su contraseña. None si cancela.
    La contraseña solo pasa a la tarea de Windows; los búferes se borran al terminar."""
    import ctypes
    from ctypes import wintypes

    class CREDUI_INFO(ctypes.Structure):  # noqa: N801  nombre de la API de Windows
        _fields_ = [("cbSize", wintypes.DWORD), ("hwndParent", wintypes.HWND), ("pszMessageText", wintypes.LPCWSTR),
                    ("pszCaptionText", wintypes.LPCWSTR), ("hbmBanner", wintypes.HANDLE)]

    credui = ctypes.WinDLL("credui")
    ole32 = ctypes.WinDLL("ole32")
    CREDUIWIN_GENERIC, CREDUIWIN_IN_CRED_ONLY = 0x1, 0x20

    packed_size = wintypes.DWORD(0)
    credui.CredPackAuthenticationBufferW(0, user, "", None, ctypes.byref(packed_size))
    packed = (ctypes.c_byte * packed_size.value)()
    if not credui.CredPackAuthenticationBufferW(0, user, "", packed, ctypes.byref(packed_size)):
        raise AlwaysOnError(tr("Windows no pudo preparar la ventana de la contraseña."))
    info = CREDUI_INFO(ctypes.sizeof(CREDUI_INFO), parent_window or None,
                       tr("LabelBridge necesita la contraseña de tu cuenta de Windows para imprimir aunque nadie haya "
                          "iniciado sesión. Windows la guarda en la tarea programada; LabelBridge no la guarda."),
                       tr("Imprimir sin sesión iniciada"), None)
    package = wintypes.ULONG(0)
    out_buffer = ctypes.c_void_p()
    out_size = wintypes.ULONG(0)
    save = wintypes.BOOL(False)
    result = credui.CredUIPromptForWindowsCredentialsW(
        ctypes.byref(info), 0, ctypes.byref(package), packed, packed_size, ctypes.byref(out_buffer),
        ctypes.byref(out_size), ctypes.byref(save), CREDUIWIN_GENERIC | CREDUIWIN_IN_CRED_ONLY)
    if result == ERROR_CANCELLED:
        return None
    if result != 0:
        raise AlwaysOnError(tr("Windows no mostró la ventana de la contraseña (código {0}).", result))
    name = ctypes.create_unicode_buffer(514)
    domain = ctypes.create_unicode_buffer(338)
    secret = ctypes.create_unicode_buffer(514)
    sizes = [wintypes.DWORD(514), wintypes.DWORD(338), wintypes.DWORD(514)]
    try:
        if not credui.CredUnPackAuthenticationBufferW(0, out_buffer, out_size, name, ctypes.byref(sizes[0]), domain,
                                                      ctypes.byref(sizes[1]), secret, ctypes.byref(sizes[2])):
            raise AlwaysOnError(tr("Windows no devolvió la contraseña escrita."))
        return secret.value
    finally:
        ctypes.memset(secret, 0, ctypes.sizeof(secret))
        if out_buffer:
            ctypes.memset(out_buffer, 0, out_size.value)
            ole32.CoTaskMemFree(out_buffer)
