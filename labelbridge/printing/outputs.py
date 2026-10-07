"""Salidas físicas: driver del sistema (QPrinter), RAW por spooler, TCP 9100, IPP y archivo."""

from __future__ import annotations

import logging
import re
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMarginsF, QRectF, QSizeF, Qt
from PySide6.QtGui import QImage, QPageLayout, QPageSize, QPainter, QPdfWriter
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo

from ..config import LABEL_MEDIA, PrinterProfile
from ..i18n import tr
from . import QT_PRINT_LOCK, PermanentPrintError, PrintError, ipp, zpl
from .pages import MM_PER_INCH, PageSource, render_fitted

log = logging.getLogger(__name__)
QT_LOGGER = "labelbridge.qt"  # donde app.py anota las advertencias de Qt

DEFAULT_LABEL_MM = (101.6, 152.4)
LABEL_TOP_MM = 6.35  # en una hoja grande la etiqueta va arriba (a ¼ in del borde, fuera del margen no imprimible)


def label_box(page: QRectF, px_per_mm: float, label_mm: tuple[float, float]) -> QRectF:
    """Dónde va una etiqueta de tamaño fijo: a tamaño real, arriba y centrada. En un rollo de su tamaño la ocupa
    entera; en una hoja apaisada se gira con ella; si no cabe (rollo más chico) se reduce lo justo."""
    w, h = label_mm[0] * px_per_mm, label_mm[1] * px_per_mm
    if (w > page.width() * 1.02 or h > page.height() * 1.02) and h <= page.width() * 1.02 and w <= page.height() * 1.02:
        w, h = h, w  # rollo apaisado (6 × 4): la etiqueta también
    if abs(page.width() - w) <= page.width() * 0.03 and abs(page.height() - h) <= page.height() * 0.03:
        return QRectF(page)
    scale = min(1.0, page.width() / w, page.height() / h)
    w, h = w * scale, h * scale
    top = max(0.0, min(LABEL_TOP_MM * px_per_mm, page.height() - h))
    return QRectF(page.left() + (page.width() - w) / 2, page.top() + top, w, h)


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
    label_mm: tuple[float, float] | None = None,
) -> None:
    first = True
    for _ in range(copies):
        for source in sources:
            for index in range(source.count()):
                if not first and not new_page():
                    raise PrintError(tr("La impresora rechazó una página nueva"))
                first = False
                if source.vector:
                    w_mm, h_mm = source.size_mm(index) or DEFAULT_LABEL_MM
                    px_per_mm = dpi / MM_PER_INCH
                    scale = min(1.0, device_rect.width() / (w_mm * px_per_mm), device_rect.height() / (h_mm * px_per_mm))
                    w, h = w_mm * px_per_mm * scale, h_mm * px_per_mm * scale
                    rect = QRectF(device_rect.left() + (device_rect.width() - w) / 2, device_rect.top(), w, h)
                    source.paint(painter, rect, dpi * scale)
                else:
                    # Con tamaño fijo (la guía de envío a 4×6 in) va a tamaño real; si no, ocupa la hoja.
                    box = label_box(device_rect, dpi / MM_PER_INCH, label_mm) if label_mm else device_rect
                    render_dpi = min(dpi, 300.0)
                    factor = render_dpi / dpi
                    target_w = max(1, round(box.width() * factor))
                    target_h = max(1, round(box.height() * factor))
                    image = render_fitted(
                        source, index, target_w, target_h, render_dpi, rotate, crop, crisp, label_mm or target_mm
                    )
                    painter.drawImage(box, image)


def print_with_driver(
    profile: PrinterProfile,
    sources: list[PageSource],
    copies: int,
    title: str,
    label_mm: tuple[float, float] | None = None,
) -> None:
    with QT_PRINT_LOCK:
        info = QPrinterInfo.printerInfo(profile.system_name)
        if info.isNull():
            raise PrintError(tr("La impresora '{0}' no está instalada en este equipo", profile.system_name))
        printer = QPrinter(info, QPrinter.PrinterMode.HighResolution)
        printer.setDocName(title or "LabelBridge")
        printer.setFullPage(True)
        media = profile.media_mm()
        if media:
            printer.setPageSize(_page_size(media))
            printer.setPageOrientation(QPageLayout.Orientation.Portrait)
        printer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
        with _spooler_watch() as spooler:
            painter = QPainter()
            if not painter.begin(printer):
                raise PrintError(tr("No se pudo iniciar el trabajo en '{0}' (¿apagada o sin conexión?)", profile.system_name))
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
                    crisp=profile.media in LABEL_MEDIA or label_mm is not None,
                    target_mm=media,
                    label_mm=label_mm,
                )
            finally:
                ended = painter.end()
        # El sistema avisa de un trabajo que no llegó a la cola de impresión con una advertencia de Qt (no con un
        # error): sin esto, se daría por impresa una etiqueta que nunca salió.
        state = printer.printerState()
        if not ended or state in (QPrinter.PrinterState.Error, QPrinter.PrinterState.Aborted) or spooler.failures:
            detail = "; ".join(spooler.failures[:2])
            log.error("El sistema no aceptó el trabajo en %s (fin=%s, estado=%s) %s", profile.system_name, ended,
                      state, detail)
            raise PrintError(tr("El sistema no pudo enviar el trabajo a '{0}'. Revisa que esté encendida, con papel y "
                                "sin trabajos atascados.", profile.system_name))


class _SpoolerWarnings(logging.Handler):
    """Junta las advertencias de Qt de este hilo mientras se imprime: así avisa Windows que el trabajo no entró en la
    cola (StartDoc/EndDoc/EndPage failed). Qt las manda al registro con app._install_qt_message_handler."""

    FAILED = re.compile(r"\b(StartDoc|EndDoc|StartPage|EndPage|CreateDC|ResetDC)\b.*\bfailed", re.IGNORECASE)

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.thread = threading.get_ident()
        self.failures: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if record.thread == self.thread and self.FAILED.search(message):
            self.failures.append(message)


@contextmanager
def _spooler_watch() -> Iterator[_SpoolerWarnings]:
    watcher = _SpoolerWarnings()
    qt_log = logging.getLogger(QT_LOGGER)
    qt_log.addHandler(watcher)
    try:
        yield watcher
    finally:
        qt_log.removeHandler(watcher)


PRINTER_DRIVER_XPS = 0x00000002


def raw_datatype(xps_driver: bool) -> str:
    """Los drivers basados en XPS (todos los v4) descartan el tipo RAW: hay que usar XPS_PASS."""
    return "XPS_PASS" if xps_driver else "RAW"


def _is_xps_driver(handle) -> bool:
    import win32print

    try:  # método oficial: DRIVER_INFO_8.dwPrinterDriverAttributes & PRINTER_DRIVER_XPS
        info = win32print.GetPrinterDriver(handle, None, 8)
        attributes = info.get("PrinterDriverAttributes", info.get("dwPrinterDriverAttributes"))
        if attributes is not None:
            return bool(int(attributes) & PRINTER_DRIVER_XPS)
    except Exception as exc:  # noqa: BLE001  drivers viejos no tienen el nivel 8: se prueba el respaldo
        log.info("Driver sin DRIVER_INFO_8 (%s): se mira su versión", exc)
    try:  # respaldo: los drivers v4 informan cVersion = 4
        return int(win32print.GetPrinterDriver(handle, None, 2).get("Version", 3)) >= 4
    except Exception as exc:  # noqa: BLE001  sin datos del driver se manda como siempre (RAW), pero queda anotado
        log.warning("No se pudo leer el driver de la impresora (%s): se envía como RAW; si es un driver v4/XPS podría "
                    "ignorar el trabajo", exc)
        return False


def print_raw_system(printer_name: str, data: bytes, title: str) -> None:
    """Envía bytes sin procesar (ZPL, EPL...) a una impresora instalada, sin pasar por el driver."""
    if sys.platform == "win32":
        import pywintypes
        import win32print

        try:
            handle = win32print.OpenPrinter(printer_name)
        except pywintypes.error as exc:
            raise PrintError(tr("No se pudo abrir '{0}': {1}", printer_name, exc.strerror)) from None
        try:
            datatype = raw_datatype(_is_xps_driver(handle))
            try:
                win32print.StartDocPrinter(handle, 1, (title or "LabelBridge", None, datatype))
            except pywintypes.error as exc:
                raise PrintError(tr("El spooler rechazó el trabajo {0}: {1}", datatype, exc.strerror)) from None
            try:
                win32print.StartPagePrinter(handle)
                written = win32print.WritePrinter(handle, data)
                if written != len(data):
                    raise PrintError(tr("El spooler solo aceptó {0} de {1} bytes", written, len(data)))
                win32print.EndPagePrinter(handle)
            except BaseException:
                win32print.AbortPrinter(handle)  # no enviar un trabajo a medias
                raise
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
        raise PermanentPrintError(tr("No se encontró el comando 'lp' (CUPS)")) from None
    if result.returncode != 0:
        raise PrintError(tr("lp falló: {0}", result.stderr.decode('utf-8', 'replace').strip()))


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
        raise PrintError(tr("Tiempo de espera agotado conectando con {0}:{1}", host, port)) from None
    except OSError as exc:
        raise PrintError(tr("No se pudo conectar con {0}:{1} ({2})", host, port, exc.strerror or exc)) from None


def send_ipp(profile: PrinterProfile, pdf: bytes, title: str, copies: int, actual_size: bool = False) -> str:
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
                scaling="none" if actual_size else "",
            )
    except ipp.CertificateMismatch as exc:
        raise PermanentPrintError(str(exc)) from None
    except ipp.IppError as exc:
        raise PrintError(str(exc)) from None
    except OSError as exc:
        raise PrintError(tr("No se pudo conectar por IPP con {0}:{1} ({2})", profile.host, profile.port, exc)) from None
    return fingerprint


# --- conversión ------------------------------------------------------------------------------

def build_pdf(
    sources: list[PageSource],
    media_mm: tuple[float, float] | None,
    rotate: str,
    crop: str,
    dpi: int = 300,
    label_mm: tuple[float, float] | None = None,
) -> bytes:
    """Genera un PDF (una página por página de origen) del tamaño del papel; con `label_mm`, la etiqueta va a ese
    tamaño real dentro de la hoja."""
    if media_mm is None:
        media_mm = label_mm or sources[0].size_mm(0) or DEFAULT_LABEL_MM
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
                crisp=max(media_mm) < 160 or label_mm is not None,
                target_mm=media_mm,
                label_mm=label_mm,
            )
        finally:
            painter.end()
    buffer.close()
    return bytes(buffer_data)


def build_zpl_raster(
    sources: list[PageSource], profile: PrinterProfile, copies: int, label_mm: tuple[float, float] | None = None
) -> bytes:
    """PDF/imagen → mapa de bits ZPL (^GFA). Sirve para cualquier Zebra sin driver."""
    media = profile.media_mm() or label_mm or (sources[0].size_mm(0) if sources else None) or DEFAULT_LABEL_MM
    dpi = max(150, int(profile.dpi or 203))
    width = round(media[0] / MM_PER_INCH * dpi)
    height = round(media[1] / MM_PER_INCH * dpi)
    images = []
    for source in sources:
        for index in range(source.count()):
            if source.vector:
                images.append(source.render(index, width, height, True))
            elif label_mm:
                box = label_box(QRectF(0, 0, width, height), dpi / MM_PER_INCH, label_mm)
                label = render_fitted(
                    source, index, round(box.width()), round(box.height()), dpi, profile.rotate, profile.crop, True, label_mm
                )
                canvas = QImage(width, height, QImage.Format.Format_RGB32)
                canvas.fill(Qt.GlobalColor.white)
                painter = QPainter(canvas)
                painter.drawImage(round(box.left()), round(box.top()), label)
                painter.end()
                images.append(canvas)
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
