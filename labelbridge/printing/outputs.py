"""Salidas físicas: driver del sistema (QPrinter), RAW por spooler, TCP 9100, IPP y archivo."""

from __future__ import annotations

import logging
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMarginsF, QRectF, QSizeF
from PySide6.QtGui import QPageLayout, QPageSize, QPainter, QPdfWriter
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo

from ..config import LABEL_MEDIA, PrinterProfile
from . import QT_PRINT_LOCK, PermanentPrintError, PrintError, ipp, zpl
from .pages import MM_PER_INCH, PageSource, render_fitted

log = logging.getLogger(__name__)

DEFAULT_LABEL_MM = (101.6, 152.4)


# --- impresoras del sistema ------------------------------------------------------------

def list_system_printers() -> list[dict]:
    with QT_PRINT_LOCK:
        default = QPrinterInfo.defaultPrinterName()
        printers = [
            {
                "name": info.printerName(),
                "description": info.description(),
                "model": info.makeAndModel(),
                "location": info.location(),
                "is_default": info.printerName() == default,
                "is_remote": info.isRemote(),
                "port": "",
                "driver": "",
            }
            for info in QPrinterInfo.availablePrinters()
        ]
    if sys.platform == "win32":
        try:
            import win32print

            flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            details = {p["pPrinterName"]: p for p in win32print.EnumPrinters(flags, None, 2)}
            for printer in printers:
                extra = details.get(printer["name"])
                if extra:
                    printer["port"] = extra.get("pPortName") or ""
                    printer["driver"] = extra.get("pDriverName") or ""
                    if not printer["model"]:
                        printer["model"] = printer["driver"]
        except Exception:
            log.debug("No se pudieron leer detalles de win32print", exc_info=True)
    return printers


def _page_size(media_mm: tuple[float, float]) -> QPageSize:
    w, h = media_mm
    if (w, h) == (215.9, 279.4):
        return QPageSize(QPageSize.PageSizeId.Letter)
    if (w, h) == (210.0, 297.0):
        return QPageSize(QPageSize.PageSizeId.A4)
    return QPageSize(QSizeF(w, h), QPageSize.Unit.Millimeter, f"Etiqueta {w:g}x{h:g} mm", QPageSize.SizeMatchPolicy.FuzzyMatch)


def _draw_sources(
    painter: QPainter,
    device_rect: QRectF,
    dpi: float,
    sources: list[PageSource],
    *,
    copies: int,
    new_page,
    rotate: str,
    crop: str,
    crisp: bool,
    target_mm: tuple[float, float] | None,
) -> None:
    first = True
    for _ in range(copies):
        for source in sources:
            for index in range(source.count()):
                if not first and not new_page():
                    raise PrintError("La impresora rechazó una página nueva")
                first = False
                if source.vector:
                    w_mm, h_mm = source.size_mm(index) or DEFAULT_LABEL_MM
                    px_per_mm = dpi / MM_PER_INCH
                    scale = min(1.0, device_rect.width() / (w_mm * px_per_mm), device_rect.height() / (h_mm * px_per_mm))
                    w, h = w_mm * px_per_mm * scale, h_mm * px_per_mm * scale
                    rect = QRectF(device_rect.left() + (device_rect.width() - w) / 2, device_rect.top(), w, h)
                    source.paint(painter, rect, dpi * scale)
                else:
                    render_dpi = min(dpi, 300.0)
                    factor = render_dpi / dpi
                    target_w = max(1, round(device_rect.width() * factor))
                    target_h = max(1, round(device_rect.height() * factor))
                    image = render_fitted(
                        source, index, target_w, target_h, render_dpi, rotate, crop, crisp, target_mm
                    )
                    painter.drawImage(device_rect, image)


def print_with_driver(profile: PrinterProfile, sources: list[PageSource], copies: int, title: str) -> None:
    with QT_PRINT_LOCK:
        info = QPrinterInfo.printerInfo(profile.system_name)
        if info.isNull():
            raise PrintError(f"La impresora '{profile.system_name}' no está instalada en este equipo")
        printer = QPrinter(info, QPrinter.PrinterMode.HighResolution)
        printer.setDocName(title or "LabelBridge")
        printer.setFullPage(True)
        media = profile.media_mm()
        if media:
            printer.setPageSize(_page_size(media))
            printer.setPageOrientation(QPageLayout.Orientation.Portrait)
        printer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
        painter = QPainter()
        if not painter.begin(printer):
            raise PrintError(f"No se pudo iniciar el trabajo en '{profile.system_name}' (¿apagada o sin conexión?)")
        try:
            _draw_sources(
                painter,
                QRectF(painter.viewport()),
                float(printer.resolution()),
                sources,
                copies=copies,
                new_page=printer.newPage,
                rotate=profile.rotate,
                crop=profile.crop,
                crisp=profile.media in LABEL_MEDIA,
                target_mm=media,
            )
        finally:
            painter.end()


def print_raw_system(printer_name: str, data: bytes, title: str) -> None:
    """Envía bytes sin procesar (ZPL, EPL...) a una impresora instalada, sin pasar por el driver."""
    if sys.platform == "win32":
        import pywintypes
        import win32print

        try:
            handle = win32print.OpenPrinter(printer_name)
        except pywintypes.error as exc:
            raise PrintError(f"No se pudo abrir '{printer_name}': {exc.strerror}") from None
        try:
            last_error = None
            for datatype in ("RAW", "XPS_PASS"):
                try:
                    win32print.StartDocPrinter(handle, 1, (title or "LabelBridge", None, datatype))
                    break
                except pywintypes.error as exc:
                    last_error = exc
            else:
                raise PrintError(f"El spooler rechazó el trabajo RAW: {last_error.strerror if last_error else ''}")
            try:
                win32print.StartPagePrinter(handle)
                win32print.WritePrinter(handle, data)
                win32print.EndPagePrinter(handle)
            finally:
                win32print.EndDocPrinter(handle)
        finally:
            win32print.ClosePrinter(handle)
        return
    try:
        result = subprocess.run(
            ["lp", "-d", printer_name, "-o", "raw", "-t", title or "LabelBridge"],
            input=data,
            capture_output=True,
            timeout=60,
        )
    except FileNotFoundError:
        raise PermanentPrintError("No se encontró el comando 'lp' (CUPS)") from None
    if result.returncode != 0:
        raise PrintError(f"lp falló: {result.stderr.decode('utf-8', 'replace').strip()}")


# --- red -----------------------------------------------------------------------------------

def send_tcp(host: str, port: int, data: bytes, timeout: float = 20.0) -> None:
    """RAW / JetDirect / AppSocket (puerto 9100)."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall(data)
            try:
                sock.shutdown(socket.SHUT_WR)
                sock.settimeout(2.0)
                while sock.recv(4096):
                    pass
            except OSError:
                pass
    except socket.timeout:
        raise PrintError(f"Tiempo de espera agotado conectando con {host}:{port}") from None
    except OSError as exc:
        raise PrintError(f"No se pudo conectar con {host}:{port} ({exc.strerror or exc})") from None


def send_ipp(profile: PrinterProfile, pdf: bytes, title: str, copies: int) -> str:
    """Imprime un PDF por IPP. Devuelve la huella TLS (para fijarla en el primer uso)."""
    tls = profile.protocol == "ipps"
    fingerprint = ""
    try:
        for _ in range(copies):
            _, fingerprint = ipp.print_job(
                profile.host,
                profile.port,
                profile.ipp_path or "/ipp/print",
                pdf,
                job_name=title,
                tls=tls,
                pin=profile.tls_fingerprint,
            )
    except ipp.CertificateMismatch as exc:
        raise PermanentPrintError(str(exc)) from None
    except ipp.IppError as exc:
        raise PrintError(str(exc)) from None
    except OSError as exc:
        raise PrintError(f"No se pudo conectar por IPP con {profile.host}:{profile.port} ({exc})") from None
    return fingerprint


# --- conversión ------------------------------------------------------------------------------

def build_pdf(
    sources: list[PageSource], media_mm: tuple[float, float] | None, rotate: str, crop: str, dpi: int = 300
) -> bytes:
    """Genera un PDF (una página por página de origen) del tamaño de la etiqueta."""
    if media_mm is None:
        media_mm = sources[0].size_mm(0) or DEFAULT_LABEL_MM
    buffer_data = QByteArray()
    buffer = QBuffer(buffer_data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    with QT_PRINT_LOCK:
        writer = QPdfWriter(buffer)
        writer.setCreator("LabelBridge")
        writer.setResolution(dpi)
        writer.setPageSize(_page_size(media_mm))
        writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
        painter = QPainter(writer)
        try:
            _draw_sources(
                painter,
                QRectF(painter.viewport()),
                float(dpi),
                sources,
                copies=1,
                new_page=writer.newPage,
                rotate=rotate,
                crop=crop,
                crisp=max(media_mm) < 160,
                target_mm=media_mm,
            )
        finally:
            painter.end()
    buffer.close()
    return bytes(buffer_data)


def build_zpl_raster(sources: list[PageSource], profile: PrinterProfile, copies: int) -> bytes:
    """PDF/imagen → mapa de bits ZPL (^GFA). Sirve para cualquier Zebra sin driver."""
    media = profile.media_mm() or (sources[0].size_mm(0) if sources else None) or DEFAULT_LABEL_MM
    dpi = max(150, int(profile.dpi or 203))
    width = round(media[0] / MM_PER_INCH * dpi)
    height = round(media[1] / MM_PER_INCH * dpi)
    images = []
    for source in sources:
        for index in range(source.count()):
            if source.vector:
                images.append(source.render(index, width, height, True))
            else:
                images.append(render_fitted(source, index, width, height, dpi, profile.rotate, profile.crop, True, media))
    return zpl.raster_label(images, copies)


def write_file(profile: PrinterProfile, title: str, extension: str, data: bytes, default_dir: Path) -> Path:
    folder = Path(profile.output_dir) if profile.output_dir else default_dir
    folder.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\-. #]+", "_", title or "trabajo").strip()[:60] or "trabajo"
    path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}_{safe}.{extension}"
    counter = 1
    while path.exists():
        path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}_{safe}_{counter}.{extension}"
        counter += 1
    path.write_bytes(data)
    return path
