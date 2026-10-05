"""Instalar en Windows una impresora de red encontrada (puerto TCP/IP + driver).

LabelBridge no necesita esto para imprimir (habla directo con la IP), pero sirve para que la
impresora también aparezca en Windows para otros programas.
"""

from __future__ import annotations

import base64
import ctypes
import ipaddress
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from ..i18n import tr

log = logging.getLogger(__name__)

GENERIC_DRIVERS = ("Microsoft IPP Class Driver", "Generic / Text Only", "Microsoft PCL6 Class Driver")


def supported() -> bool:
    return sys.platform == "win32"


def _powershell(script: str, timeout: float = 60) -> str:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
        capture_output=True,
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip() or tr("PowerShell falló"))
    return result.stdout.decode("utf-8", "replace")


def list_drivers() -> list[str]:
    if not supported():
        return []
    try:
        output = _powershell("Get-PrinterDriver | Select-Object -ExpandProperty Name")
    except Exception:
        log.exception("No se pudieron listar los drivers de impresora")
        return list(GENERIC_DRIVERS)
    drivers = sorted({line.strip() for line in output.splitlines() if line.strip()})
    return drivers or list(GENERIC_DRIVERS)


def _ps_value(value: str) -> str:
    """Expresión PowerShell que reconstruye el texto desde base64.

    Los nombres vienen de la red (mDNS, IPP, HTTP) y no son confiables: PowerShell también cierra
    comillas simples con comillas tipográficas (U+2018..U+201B), así que nunca se interpolan.
    """
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return f"([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}')))"


def install_network_printer(name: str, ip: str, driver: str, port: int = 9100) -> None:
    """Crea el puerto TCP/IP y la impresora con permisos de administrador (aparece el aviso UAC)."""
    if not supported():
        raise RuntimeError(tr("Solo disponible en Windows"))
    ip = str(ipaddress.ip_address(ip.strip()))  # solo IPs válidas
    name = " ".join(name.split())[:100] or tr("Impresora {0}", ip)
    log_file = Path(tempfile.gettempdir()) / "labelbridge-install-printer.log"
    port_name = f"IP_{ip}" if port == 9100 else f"IP_{ip}_{int(port)}"
    script = f"""
$ErrorActionPreference = 'Stop'
$portName = {_ps_value(port_name)}
$printerName = {_ps_value(name)}
$driverName = {_ps_value(driver)}
$hostAddress = {_ps_value(ip)}
$logFile = {_ps_value(str(log_file))}
try {{
  if (-not (Get-PrinterPort -Name $portName -ErrorAction SilentlyContinue)) {{
    Add-PrinterPort -Name $portName -PrinterHostAddress $hostAddress -PortNumber {int(port)}
  }}
  if (-not (Get-Printer -Name $printerName -ErrorAction SilentlyContinue)) {{
    Add-Printer -Name $printerName -DriverName $driverName -PortName $portName
  }}
  'OK' | Out-File -Encoding utf8 $logFile
}} catch {{
  $_.Exception.Message | Out-File -Encoding utf8 $logFile
  exit 1
}}
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    log_file.unlink(missing_ok=True)
    exit_code = _run_elevated(
        "powershell.exe", f"-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand {encoded}"
    )
    message = log_file.read_text(encoding="utf-8-sig").strip() if log_file.exists() else ""
    if exit_code != 0 or message != "OK":
        raise RuntimeError(message or tr("La instalación terminó con código {0}", exit_code))
    log.info("Impresora '%s' instalada en Windows (%s, driver %s)", name, ip, driver)


def _run_elevated(program: str, params: str, timeout_ms: int = 120_000) -> int:
    """ShellExecuteEx con 'runas' y espera al proceso: así sabemos si funcionó."""
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    see_mask_nocloseprocess = 0x00000040
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = see_mask_nocloseprocess
    info.lpVerb = "runas"
    info.lpFile = program
    info.lpParameters = params
    info.nShow = 0  # SW_HIDE
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        raise RuntimeError(tr("Se canceló el permiso de administrador"))
    kernel32 = ctypes.windll.kernel32
    kernel32.WaitForSingleObject(info.hProcess, timeout_ms)
    code = wintypes.DWORD()
    kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    kernel32.CloseHandle(info.hProcess)
    return int(code.value)
