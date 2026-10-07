"""Lo que el asistente puede revisar y configurar de Windows (Ajustes → Asistente → Herramientas de Windows).

Cada función hace UNA cosa concreta y devuelve datos (nunca texto de PowerShell para que el modelo lo interprete). Las
que cambian algo las llama el asistente después de que la persona lo aprobó en una ventana; las que necesitan
administrador abren el permiso de Windows (UAC), que solo una persona puede aceptar. Los nombres que vienen de Windows
(impresoras, documentos) no son confiables: nunca se interpolan en PowerShell (ver windows_printers._ps_value).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

from ..i18n import N_, tr
from . import windows_printers

log = logging.getLogger(__name__)

# Páginas de la Configuración de Windows que el asistente puede abrir (nunca otra dirección ni programa).
SETTINGS_PAGES: dict[str, tuple[str, str]] = {
    "printers": ("ms-settings:printers", N_("Impresoras y escáneres")),
    "devices_and_printers": ("control printers", N_("Dispositivos e impresoras")),
    "power": ("ms-settings:powersleep", N_("Inicio/apagado y suspensión")),
    "windows_update": ("ms-settings:windowsupdate", N_("Windows Update")),
    "network": ("ms-settings:network-status", N_("Estado de la red")),
    "startup_apps": ("ms-settings:startupapps", N_("Aplicaciones de inicio")),
    "display": ("ms-settings:display", N_("Pantalla")),
    "date_time": ("ms-settings:dateandtime", N_("Fecha y hora")),
}

# PowerShell escribe en la página de códigos de la consola: así los nombres con acentos llegan bien.
_UTF8 = "$OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8\n"


def supported() -> bool:
    return sys.platform == "win32"


def _require() -> None:
    if not supported():
        raise RuntimeError(tr("Solo disponible en Windows"))


def _powershell(script: str, timeout: float = 60) -> str:
    return windows_printers._powershell(_UTF8 + script, timeout)


def _value(text: str) -> str:
    return windows_printers._ps_value(text)


def _json(script: str) -> object:
    output = _powershell(script).strip()
    return json.loads(output) if output else None


def _list(value: object) -> list:
    """ConvertTo-Json devuelve un objeto suelto cuando hay uno solo."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


# --- leer ------------------------------------------------------------------------------------------
def overview() -> dict:
    """Windows, el equipo, el disco, el servicio de impresión, la impresora predeterminada, si espera reiniciarse y
    cuándo se duerme."""
    _require()
    data = _json(r"""
$os = Get-CimInstance Win32_OperatingSystem
$disk = Get-PSDrive -Name C -ErrorAction SilentlyContinue
$spooler = Get-Service -Name Spooler -ErrorAction SilentlyContinue
$default = Get-CimInstance Win32_Printer -Filter "Default=True" -ErrorAction SilentlyContinue
$reboot = (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') -or
          (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired')
[pscustomobject]@{
  windows = $os.Caption
  version = $os.Version
  computer = $env:COMPUTERNAME
  last_boot = $os.LastBootUpTime.ToString('s')
  disk_free_gb = if ($disk) { [math]::Round($disk.Free / 1GB, 1) } else { $null }
  print_spooler = if ($spooler) { [string]$spooler.Status } else { 'missing' }
  default_printer = if ($default) { [string]$default.Name } else { '' }
  restart_pending = [bool]$reboot
} | ConvertTo-Json -Compress
""")
    out = dict(data) if isinstance(data, dict) else {}
    try:
        out["power"] = power_settings()
    except Exception as exc:  # la energía es un dato más: sin ella, el resto sirve igual
        log.info("No se pudo leer la energía de Windows: %s", exc)
    return out


def _powercfg_minutes(subgroup: str, setting: str) -> tuple[int, int]:
    """(enchufado, con batería) en minutos; 0 = nunca. powercfg sale en el idioma de Windows, pero los valores son
    siempre los dos últimos números hexadecimales."""
    result = subprocess.run(["powercfg", "/query", "SCHEME_CURRENT", subgroup, setting], capture_output=True,
                            timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    values = re.findall(r"0x([0-9a-fA-F]{8})", result.stdout.decode("ascii", "replace"))
    if result.returncode != 0 or len(values) < 2:
        raise RuntimeError(tr("powercfg no respondió"))
    ac, dc = (int(value, 16) for value in values[-2:])
    return ac // 60, dc // 60


def power_settings() -> dict:
    _require()
    sleep_ac, sleep_dc = _powercfg_minutes("SUB_SLEEP", "STANDBYIDLE")
    screen_ac, screen_dc = _powercfg_minutes("SUB_VIDEO", "VIDEOIDLE")
    return {"sleep_after_minutes_plugged": sleep_ac, "sleep_after_minutes_battery": sleep_dc,
            "screen_off_minutes_plugged": screen_ac, "screen_off_minutes_battery": screen_dc,
            "note": "0 = nunca"}


def print_queue(printer: str = "") -> list[dict]:
    """Los trabajos que esperan en la cola de Windows (de una impresora o de todas)."""
    _require()
    names = f"@({_value(printer)})" if printer else "(Get-Printer | Select-Object -ExpandProperty Name)"
    data = _json(f"""
$jobs = foreach ($name in {names}) {{
  Get-PrintJob -PrinterName $name -ErrorAction SilentlyContinue | ForEach-Object {{
    [pscustomobject]@{{ printer = $name; id = $_.Id; document = [string]$_.DocumentName;
      status = [string]$_.JobStatus; pages = $_.TotalPages;
      submitted = if ($_.SubmittedTime) {{ $_.SubmittedTime.ToString('s') }} else {{ '' }} }}
  }}
}}
ConvertTo-Json -InputObject @($jobs) -Compress
""")
    return [dict(job) for job in _list(data) if isinstance(job, dict)]


def check_connection(host: str, port: int = 9100, timeout: float = 3.0) -> dict:
    """¿Responde la impresora en la red? Abre y cierra una conexión TCP (no imprime nada) y prueba ping."""
    started = time.monotonic()
    reachable, error = False, ""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            reachable = True
    except OSError as exc:
        error = exc.strerror or exc.__class__.__name__
    latency = round((time.monotonic() - started) * 1000) if reachable else None
    count = ["-n", "2", "-w", "1000"] if sys.platform == "win32" else ["-c", "2", "-W", "1"]
    try:
        ping = subprocess.run(["ping", *count, host], capture_output=True, timeout=10,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ping = False
    return {"host": host, "port": int(port), "port_open": reachable, "latency_ms": latency, "ping": ping,
            "error": error}


# --- cambiar (siempre después del permiso de la persona) --------------------------------------------
def _run_elevated_script(script: str, timeout_ms: int = 120_000) -> None:
    """Corre el script como administrador: Windows muestra su permiso (UAC) y solo una persona puede aceptarlo."""
    log_file = Path(tempfile.gettempdir()) / f"labelbridge-admin-{os.getpid()}-{int(time.time() * 1000)}.log"
    body = f"""
$ErrorActionPreference = 'Stop'
$logFile = {_value(str(log_file))}
try {{
{script}
  'OK' | Out-File -Encoding utf8 $logFile
}} catch {{
  $_.Exception.Message | Out-File -Encoding utf8 $logFile
  exit 1
}}
"""
    encoded = base64.b64encode(body.encode("utf-16-le")).decode("ascii")
    try:
        code = windows_printers._run_elevated(
            "powershell.exe", f"-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand {encoded}", timeout_ms)
        message = log_file.read_text(encoding="utf-8-sig").strip() if log_file.exists() else ""
    finally:
        log_file.unlink(missing_ok=True)
    if code != 0 or message != "OK":
        raise RuntimeError(message or tr("Terminó con código {0}", code))


def clear_queue(printer: str, elevate: Callable[[str], None] | None = None) -> int:
    """Borra los trabajos atascados de la cola de Windows de esa impresora. Sin permiso de administrador primero; si
    Windows lo pide (trabajos de otros usuarios), con él. Devuelve cuántos había."""
    _require()
    pending = len(print_queue(printer))
    script = f"Get-PrintJob -PrinterName {_value(printer)} | Remove-PrintJob"
    try:
        _powershell("$ErrorActionPreference = 'Stop'\n" + script)
    except RuntimeError:
        (elevate or _run_elevated_script)("  " + script)
    return pending


def restart_spooler() -> None:
    """Reinicia el servicio de impresión de Windows (destraba colas). Pide permiso de administrador."""
    _require()
    _run_elevated_script("  Restart-Service -Name Spooler -Force")


def set_default_printer(printer: str) -> None:
    """La predeterminada de Windows (y que Windows no la cambie sola por la última usada)."""
    _require()
    _powershell(f"""
$ErrorActionPreference = 'Stop'
$name = {_value(printer)}
$found = Get-CimInstance Win32_Printer | Where-Object {{ $_.Name -eq $name }} | Select-Object -First 1
if (-not $found) {{ throw "No existe la impresora $name" }}
Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows NT\\CurrentVersion\\Windows' -Name LegacyDefaultPrinterMode -Value 1
Invoke-CimMethod -InputObject $found -MethodName SetDefaultPrinter | Out-Null
""")


def set_power(sleep_minutes: int, screen_minutes: int | None = None) -> None:
    """Cuándo se duerme el equipo enchufado (0 = nunca) y, si se indica, cuándo se apaga la pantalla."""
    _require()
    commands = [["powercfg", "/change", "standby-timeout-ac", str(int(sleep_minutes))]]
    if screen_minutes is not None:
        commands.append(["powercfg", "/change", "monitor-timeout-ac", str(int(screen_minutes))])
    failed = []
    for command in commands:
        result = subprocess.run(command, capture_output=True, timeout=20,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0:
            failed.append(command)
    if failed:  # algunos equipos lo permiten solo como administrador
        _run_elevated_script("\n".join(f"  & {' '.join(command)}" for command in failed))


def open_settings(page: str) -> str:
    """Abre una página de la Configuración de Windows de la lista SETTINGS_PAGES. Devuelve su nombre."""
    _require()
    target, title = SETTINGS_PAGES[page]
    if target.startswith("ms-settings:"):
        os.startfile(target)  # type: ignore[attr-defined]  (solo existe en Windows)
    else:
        subprocess.Popen(target.split(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return tr(title)


def print_test_page(printer: str) -> None:
    """La página de prueba de Windows en esa impresora (la del driver, no la de LabelBridge)."""
    _require()
    result = subprocess.run(["rundll32", "printui.dll,PrintUIEntry", "/k", "/n", printer], capture_output=True,
                            timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0:
        raise RuntimeError(tr("Windows no pudo imprimir la página de prueba"))
