"""Actualizaciones automáticas.

Cada versión publicada deja en la página pública de descargas un `latest.json`:
    {"version": "1.1.0", "published_at": "...", "notes": "- ...", "page_url": "...",
     "installer": {"url": "...exe", "size": 35000000, "sha256": "..."}}
La app lo consulta cada pocas horas; si hay una versión nueva pregunta al usuario, descarga el
instalador (verificando SHA-256), lo ejecuta en modo silencioso y se vuelve a abrir sola.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from . import __version__
from . import events as ev
from . import net
from .config import ConfigManager
from .connectors.base import BackgroundService
from .i18n import language, tr
from .paths import is_frozen

log = logging.getLogger(__name__)

UPDATES_PAGE = "https://morghullis.github.io/labelbridge-updates/"
MANIFEST_URL = os.environ.get("LABELBRIDGE_UPDATE_URL", UPDATES_PAGE + "latest.json")
CHECK_EVERY = 6 * 3600
UPDATE = "update"  # evento con UpdateInfo
MAX_INSTALLER_BYTES = 300 * 1024 * 1024


class UpdateError(Exception):
    pass


@dataclass
class UpdateInfo:
    version: str
    url: str
    size: int = 0
    sha256: str = ""
    notes: str = ""
    published_at: str = ""
    page_url: str = UPDATES_PAGE
    notes_en: str = ""

    def localized_notes(self) -> str:
        """Las novedades en el idioma de la app (en español si la versión no trae las de ese idioma)."""
        return self.notes_en if language() == "en" and self.notes_en else self.notes

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "UpdateInfo":
        data = json.loads(text)
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


def parse_version(text: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", text.split("-")[0].split("+")[0])
    return tuple(int(n) for n in numbers[:4]) or (0,)


def is_newer(candidate: str, current: str = __version__) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def fetch_manifest(url: str = MANIFEST_URL) -> UpdateInfo:
    separator = "&" if "?" in url else "?"
    data = net.request("GET", f"{url}{separator}t={int(time.time())}", timeout=20, max_bytes=256 * 1024).json()
    if not isinstance(data, dict) or not data.get("version"):
        raise UpdateError(tr("La información de versión no es válida"))
    installer = data.get("installer") or {}
    allowed = ("https://", "http://127.0.0.1") if url.startswith("http://127.0.0.1") else ("https://",)  # pruebas locales
    if not str(installer.get("url", "")).startswith(allowed):
        raise UpdateError(tr("El instalador publicado no tiene una URL segura"))
    return UpdateInfo(
        version=str(data["version"]),
        url=str(installer["url"]),
        size=int(installer.get("size") or 0),
        sha256=str(installer.get("sha256") or "").lower(),
        notes=str(data.get("notes") or ""),
        notes_en=str(data.get("notes_en") or ""),
        published_at=str(data.get("published_at") or ""),
        page_url=str(data.get("page_url") or UPDATES_PAGE),
    )


def check_for_update(url: str = MANIFEST_URL) -> UpdateInfo | None:
    """Devuelve la versión publicada si es más nueva que la instalada."""
    info = fetch_manifest(url)
    return info if is_newer(info.version) else None


def download(
    info: UpdateInfo,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
    folder: Path | None = None,
) -> Path:
    """Descarga el instalador en bloques (con progreso) y comprueba su huella SHA-256."""
    folder = folder or Path(tempfile.gettempdir()) / "LabelBridge-actualizacion"
    folder.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^\w.\-]", "_", info.url.rsplit("/", 1)[-1]) or f"LabelBridge-Setup-{info.version}.exe"
    target = folder / name
    partial = target.with_suffix(target.suffix + ".part")
    digest = hashlib.sha256()
    received = 0
    request = urllib.request.Request(info.url, headers={"User-Agent": net.USER_AGENT})
    try:
        with net._get_opener().open(request, timeout=60) as response, open(partial, "wb") as out:
            total = int(response.headers.get("Content-Length") or info.size or 0)
            while True:
                if cancel is not None and cancel.is_set():
                    raise UpdateError(tr("Descarga cancelada"))
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_INSTALLER_BYTES:
                    raise UpdateError(tr("El instalador es demasiado grande"))
                digest.update(chunk)
                out.write(chunk)
                if progress:
                    progress(received, total)
    except (OSError, ValueError) as exc:
        partial.unlink(missing_ok=True)
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError(tr("No se pudo descargar la actualización: {0}", exc)) from None
    except UpdateError:
        partial.unlink(missing_ok=True)
        raise
    if info.size and received != info.size:
        partial.unlink(missing_ok=True)
        raise UpdateError(tr("La descarga quedó incompleta; vuelve a intentarlo"))
    if info.sha256 and digest.hexdigest() != info.sha256:
        partial.unlink(missing_ok=True)
        raise UpdateError(tr("El archivo descargado no coincide con el publicado (SHA-256); no se instalará"))
    os.replace(partial, target)
    return target


def can_install() -> bool:
    return sys.platform == "win32" and is_frozen()


CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000

# Script que aplica la actualización cuando la app se cierra. Va en un archivo .ps1 legible (no un comando
# codificado) y deja registro en logs/actualizacion.log; el instalador deja el suyo en logs/instalador.log.
APPLY_SCRIPT = r"""# LabelBridge: aplica la actualización descargada. Lo genera la app; se puede borrar.
$ErrorActionPreference = 'Continue'
try { Start-Transcript -Path @@LOG@@ -Append | Out-Null } catch {}
$appPid = @@PID@@
$app = @@APP@@
$installer = @@INSTALLER@@
"Esperando a que LabelBridge (PID $appPid) termine de cerrarse..."
$running = Get-Process -Id $appPid -ErrorAction SilentlyContinue
if ($running) { [void]$running.WaitForExit(120000) }
if (Get-Process -Id $appPid -ErrorAction SilentlyContinue) {
    "La app no se cerró a tiempo: se cierra a la fuerza para actualizar."
    Stop-Process -Id $appPid -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}
"Instalando $installer"
$setup = Start-Process -FilePath $installer -ArgumentList @('/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOCANCEL', @@SETUP_LOG@@) -PassThru
$null = $setup.Handle  # sin esto PowerShell 5.1 no conserva el código de salida
$setup.WaitForExit()
"El instalador terminó con código $($setup.ExitCode)"
Start-Sleep -Seconds 3
if (-not (Get-Process -Name 'LabelBridge' -ErrorAction SilentlyContinue)) {
    "Abriendo LabelBridge"
    Start-Process -FilePath $app -ArgumentList '--updated'
}
try { Stop-Transcript | Out-Null } catch {}
"""


def _ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def apply_script(installer: Path, app: Path, pid: int, logs: Path) -> str:
    """Contenido del script de actualización (separado para poder probarlo)."""
    setup_log = f'/LOG="{logs / "instalador.log"}"'
    return (
        APPLY_SCRIPT.replace("@@LOG@@", _ps_literal(str(logs / "actualizacion.log")))
        .replace("@@PID@@", str(int(pid)))
        .replace("@@APP@@", _ps_literal(str(app)))
        .replace("@@INSTALLER@@", _ps_literal(str(installer)))
        .replace("@@SETUP_LOG@@", _ps_literal(setup_log))
    )


def install_and_restart(installer: Path, app: Path | None = None) -> None:
    """Prepara la actualización: cuando esta app se cierre, un script instala la versión nueva y la vuelve a abrir.

    Si la app tarda en cerrarse, el script espera hasta 2 minutos y luego la cierra; si el instalador falla, la
    app se vuelve a abrir igualmente (con la versión anterior) y avisa (ver failed_update).
    """
    if not can_install():
        raise UpdateError(tr("La instalación automática solo funciona en la app instalada de Windows"))
    from .paths import logs_dir

    script = installer.with_name("aplicar-actualizacion.ps1")
    # Con BOM: Windows PowerShell 5.1 lee así bien las rutas con tildes o eñes.
    app = app or Path(sys.executable)
    script.write_text(apply_script(installer, app, os.getpid(), logs_dir()), encoding="utf-8-sig")
    command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-WindowStyle", "Hidden", "-File", str(script)]
    # Consola oculta propia (CREATE_NO_WINDOW). No usar DETACHED_PROCESS: sin consola, Windows PowerShell se
    # cierra al arrancar sin ejecutar nada (era el fallo de las actualizaciones hasta la 1.3.0).
    flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        # Fuera de cualquier «job» del proceso actual: así el script sobrevive al cierre de la app.
        subprocess.Popen(command, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, close_fds=True)
    except OSError:
        subprocess.Popen(command, creationflags=flags, close_fds=True)
    log.info("Actualización: el instalador se ejecutará al cerrar la app (%s)", installer.name)


class UpdateService(BackgroundService):
    """Revisa la página de descargas al arrancar y cada 6 horas."""

    name = "updates"
    label = "Actualizaciones"

    def __init__(self, cfg: ConfigManager, events: ev.Events) -> None:
        super().__init__(events)
        self.cfg = cfg
        self.available: UpdateInfo | None = None
        self.last_checked = 0.0

    def enabled(self) -> bool:
        return self.cfg.data.general.auto_update_check

    def check_now(self) -> UpdateInfo | None:
        info = check_for_update()
        self.last_checked = time.time()
        if info is not None:
            self.available = info
        return info

    def run(self, stop: threading.Event, wake: threading.Event) -> None:
        if not self.sleep(stop, wake, 25):  # deja arrancar la app primero
            return
        while not stop.is_set():
            try:
                info = self.check_now()
                if info is not None and info.version != self.cfg.data.general.skipped_version:
                    log.info("Actualización disponible: %s (instalada %s)", info.version, __version__)
                    self.events.emit(UPDATE, info)
            except Exception as exc:  # sin Internet, página caída...: se reintenta luego
                log.info("No se pudo buscar actualizaciones: %s", exc)
            if not self.sleep(stop, wake, CHECK_EVERY):
                break


def notes_to_html(notes: str) -> str:
    """Notas en formato «- punto» (con **negritas**; los «  - » sangrados van dentro del punto anterior) → HTML."""
    from html import escape

    def inline(text: str) -> str:
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escape(text))

    items: list[tuple[str, list[str]]] = []
    paragraphs = []
    for line in notes.splitlines():
        text = line.strip()
        if not text:
            continue
        if text[:2] in ("- ", "* ", "• "):
            if line[:1] in (" ", "\t") and items:
                items[-1][1].append(inline(text[2:]))
            else:
                items.append((inline(text[2:]), []))
        else:
            paragraphs.append(f"<p>{inline(text)}</p>")
    html = "".join(paragraphs)
    if items:
        html += "<ul style='margin-left: -18px'>"
        for text, children in items:
            sub = "<ul>" + "".join(f"<li>{child}</li>" for child in children) + "</ul>" if children else ""
            html += f"<li>{text}{sub}</li>"
        html += "</ul>"
    return html or f"<p>{escape(tr('Mejoras y correcciones.'))}</p>"


def _stored_update(store_get: Callable[[str], Any]) -> UpdateInfo | None:
    raw = store_get("update:installed")
    if not raw:
        return None
    try:
        return UpdateInfo.from_json(raw)
    except Exception:
        return None


def pending_whats_new(store_get: Callable[[str], Any]) -> UpdateInfo | None:
    """La actualización que se acaba de instalar (para mostrar las novedades una vez)."""
    info = _stored_update(store_get)
    return info if info is not None and parse_version(info.version) == parse_version(__version__) else None


def failed_update(store_get: Callable[[str], Any]) -> UpdateInfo | None:
    """Se intentó actualizar a una versión más nueva y la app sigue en la anterior: la instalación falló."""
    info = _stored_update(store_get)
    return info if info is not None and is_newer(info.version) else None
