"""Autoprueba del ejecutable empaquetado: `LabelBridge.exe --self-test [informe.txt]`.

Comprueba que el paquete trae todo lo necesario (Qt, PDF, interfaz, librerías) sin abrir ventanas
ni tocar los datos del usuario. Devuelve 0 si todo está bien. La usa la compilación en GitHub.
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path


def run(argv: list[str]) -> int:
    report_path = Path(argv[argv.index("--self-test") + 1]) if len(argv) > argv.index("--self-test") + 1 else (
        Path(tempfile.gettempdir()) / "labelbridge-selftest.txt"
    )
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["LABELBRIDGE_HOME"] = tempfile.mkdtemp(prefix="labelbridge-selftest-")
    os.environ["LABELBRIDGE_SECRETS"] = "file"
    lines: list[str] = []
    ok = True

    def check(name: str, fn) -> None:
        nonlocal ok
        try:
            detail = fn()
            lines.append(f"OK    {name}" + (f": {detail}" if detail else ""))
        except Exception:
            ok = False
            lines.append(f"FALLO {name}\n{traceback.format_exc()}")

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([sys.argv[0]])

    def libraries() -> str:
        import importlib

        for module in ("paho.mqtt.client", "segno", "zeroconf", "PySide6.QtPdf", "PySide6.QtSvg"):
            importlib.import_module(module)
        import certifi
        import keyring

        assert Path(certifi.where()).exists(), "falta el paquete de certificados"
        return f"keyring={type(keyring.get_keyring()).__name__}"

    def windows_printing() -> str:
        if sys.platform != "win32":
            return "no es Windows"
        import win32print

        printers = win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS, None, 2)
        return f"{len(printers)} impresora(s) en el sistema"

    def label_pipeline() -> str:
        from . import paths
        from .config import ConfigManager, PrinterProfile
        from .events import Events
        from .jobstore import JobStore
        from .labels.order import sample_order
        from .printing import outputs
        from .printing.engine import Document, PrintEngine
        from .printing.pages import PdfSource

        cfg = ConfigManager(paths.config_path())
        store = JobStore(paths.db_path(), paths.spool_dir())
        engine = PrintEngine(store, cfg, Events())
        profile = PrinterProfile(alias="prueba", kind="file", output_dir=str(paths.output_dir()), media="4x6")
        pdf = engine._as_pdf(Document("order", order=sample_order()), profile)
        assert pdf.startswith(b"%PDF"), "no se generó el PDF"
        source = PdfSource(pdf)
        image = source.render(0, 812, 1218, True)
        source.close()
        assert not image.isNull(), "QtPdf no pudo dibujar el PDF"
        raster_source = PdfSource(pdf)
        zpl = outputs.build_zpl_raster([raster_source], PrinterProfile(language="zpl", media="4x6"), 1)
        raster_source.close()
        assert b"^GFA" in zpl
        store.close()
        return f"PDF {len(pdf) // 1024} KB, ZPL {len(zpl) // 1024} KB"

    def interface() -> str:
        from . import paths
        from .config import ConfigManager
        from .events import Events
        from .jobstore import JobStore
        from .printing.engine import PrintEngine
        from .secret_store import SecretStore
        from .services import Services
        from .ui.context import AppContext, QtBridge
        from .ui.main_window import NAV, MainWindow
        from .ui.theme import STYLESHEET

        app.setStyleSheet(STYLESHEET)
        cfg = ConfigManager(paths.config_path())
        cfg.data.local_api.enabled = False
        secrets = SecretStore(paths.data_dir() / "secrets.json", backend="file")
        store = JobStore(paths.db_path(), paths.spool_dir())
        events = Events()
        engine = PrintEngine(store, cfg, events)
        services = Services(cfg, secrets, store, engine, events)
        window = MainWindow(AppContext(cfg, secrets, store, engine, services, events, QtBridge(events, cfg)))
        for key, _, _ in NAV:
            window.navigate(key)
            app.processEvents()
        window.quitting = True
        window.close()
        store.close()
        return f"{len(NAV)} pantallas"

    check("librerías", libraries)
    check("impresión de Windows", windows_printing)
    check("etiqueta → PDF → ZPL", label_pipeline)
    check("interfaz", interface)
    lines.append("RESULTADO: " + ("OK" if ok else "FALLO"))
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return 0 if ok else 1
