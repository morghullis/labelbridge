"""Revisión de la traducción: abre todas las pantallas y diálogos con la app en inglés, recordando lo que devuelve
tr(), y lista los textos visibles que siguen en español, o sea, los que no pasan por tr().

Uso: QT_QPA_PLATFORM=offscreen python devtools/i18n_audit.py
Las pruebas (tests/test_i18n.py) usan visible_leftovers() para que no se escape ninguno.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SPANISH = re.compile(
    r"[áéíóúñÁÉÍÓÚÑ¿¡]|\b(el|la|los|las|del|que|para|con|una|sin|está|hay|pedidos?|guías?|impresoras?|etiquetas?|"
    r"tienda|clave|cola|prueba|guardar|cancelar|abrir|buscar|actualizar|ajustes|historial|registro|asistente|"
    r"paquetería|envío|todos|ver|copiar|probar|conectar|agregar|quitar|editar|predeterminada|archivo|carpeta|red|"
    r"equipo|correo|seguridad|permisos|límite|costo|precio|modelo|proveedor|dirección|nombre|remitente|productos|"
    r"cliente|destino|fecha|estado|origen|formato|intentos|detalle|trabajos?|activa|activo|desactivado|nueva?|"
    r"inicio|guía|sí|no hay)\b",
    re.IGNORECASE,
)


def _words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñü]{3,}", re.sub(r"<[^>]+>|\{[^{}]*\}", " ", text))}


@cache
def spanish_only_words() -> frozenset[str]:
    """Las palabras que solo aparecen en el español de la traducción (en el inglés no): si se ve una, faltó tr()."""
    catalog = json.loads((ROOT / "assets" / "locale" / "en.json").read_text(encoding="utf-8"))
    spanish = set().union(*map(_words, catalog))
    english = set().union(*map(_words, catalog.values()))
    return frozenset(spanish - english)


def looks_spanish(text: str) -> bool:
    return bool(SPANISH.search(text) or _words(text) & spanish_only_words())


def _texts(widget) -> list[str]:
    from PySide6.QtWidgets import (QAbstractButton, QComboBox, QGroupBox, QLabel, QLineEdit, QPlainTextEdit, QTabBar,
                                   QTableView, QTextEdit)

    out = []
    if isinstance(widget, QLabel):
        out.append(widget.text())
    if isinstance(widget, (QPlainTextEdit, QTextEdit)):
        out += [line for line in widget.toPlainText().splitlines() if line.strip()]
        out.append(widget.placeholderText())
    if isinstance(widget, QAbstractButton):
        out.append(widget.text())
    if isinstance(widget, QLineEdit):
        out.append(widget.placeholderText())
    if isinstance(widget, QComboBox):
        out += [widget.itemText(i) for i in range(widget.count())]
    if isinstance(widget, QGroupBox):
        out.append(widget.title())
    if isinstance(widget, QTabBar):
        out += [widget.tabText(i) for i in range(widget.count())]
    if isinstance(widget, QTableView) and widget.model() is not None:
        model = widget.model()
        from PySide6.QtCore import Qt

        out += [str(model.headerData(c, Qt.Orientation.Horizontal) or "") for c in range(model.columnCount())]
        for row in range(min(model.rowCount(), 5)):
            out += [str(model.index(row, c).data() or "") for c in range(model.columnCount())]
    out.append(widget.toolTip())
    out.append(widget.windowTitle() if widget.isWindow() else "")
    return [text for text in out if text]


def leftovers_in(widget, translated: set[str]) -> list[str]:
    """Textos en español del widget y sus hijos que no salieron de tr()."""
    from PySide6.QtWidgets import QWidget

    from labelbridge.i18n import LANGUAGES
    from labelbridge.ui.pages.guide import _highlight_code

    shown = {t for t in translated if len(t) > 1} | {_highlight_code(t) for t in translated if "<" in t}
    shown |= set(LANGUAGES.values())  # «Español» / «English» se muestran siempre en su idioma
    pieces = sorted(shown, key=len, reverse=True)
    found = []
    for child in [widget, *widget.findChildren(QWidget)]:
        ancestor = child
        while ancestor is not None and ancestor.objectName() != "ChatArea":
            ancestor = ancestor.parentWidget()
        if ancestor is not None:
            continue  # los mensajes del chat son conversación (el saludo ya sale de tr())
        for text in _texts(child):
            plain = text.strip()
            for piece in pieces:
                if piece in plain:
                    plain = plain.replace(piece, " ")
            plain = re.sub(r"<[^>]+>|\S+\.json\b", " ", plain)  # sin etiquetas HTML ni nombres de archivo
            if looks_spanish(plain):
                found.append(f"{type(child).__name__}: {text[:140]}")
    return found


def sample_rows(cfg) -> list:
    """Pedidos de ejemplo: FedEx por comprar, una compra rechazada, un envío atrasado y uno entregado."""
    import time
    from datetime import datetime, timezone

    from labelbridge.connectors.orders_overview import classify

    now = time.time()

    def iso(ts: float) -> str:
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def order(number: int, subtotal: float = 300.0, fulfillment: dict | None = None) -> dict:
        return {
            "id": f"gid://shopify/Order/{number}", "legacyResourceId": str(number), "name": f"#{number}",
            "createdAt": iso(now - 3 * 86400), "displayFinancialStatus": "PAID",
            "displayFulfillmentStatus": "FULFILLED" if fulfillment else "UNFULFILLED", "tags": [],
            "totalPriceSet": {"shopMoney": {"amount": str(subtotal + 20), "currencyCode": "USD"}},
            "currentSubtotalPriceSet": {"shopMoney": {"amount": str(subtotal)}},
            "shippingAddress": {"name": "Ana", "city": "Miami", "provinceCode": "FL", "countryCodeV2": "US"},
            "shippingLine": {"title": "Express"}, "fulfillments": [fulfillment] if fulfillment else [],
            "fulfillmentOrders": {"nodes": [{"status": "OPEN", "deliveryMethod": {"methodType": "SHIPPING"}}]},
            "events": {"nodes": [{"createdAt": iso(now - 2 * 86400), "message": "5,68 $",
                                  "action": "shipping_label_created_success", "arguments": [number]}] if fulfillment else []},
        }

    def shipment(**delivery) -> dict:
        return {"status": "SUCCESS", "createdAt": iso(now - 2 * 86400),
                "trackingInfo": [{"company": "USPS", "number": "9200", "url": "https://tools.usps.com"}], **delivery}

    s = cfg.data.shopify
    s.buy_shipping_label = True
    return [
        classify(order(1, 1500), settings=s, since=now - 30 * 86400, now=now),
        classify(order(2), settings=s, since=now - 30 * 86400, now=now,
                 ledger={"state": "failed", "reason": "TOTAL_WEIGHT_ZERO weight"}),
        classify(order(3, fulfillment=shipment(displayStatus="IN_TRANSIT", inTransitAt=iso(now - 86400),
                                               estimatedDeliveryAt=iso(now - 86400 * 2))), settings=s, now=now,
                 jobs=[{"status": "done"}]),
        classify(order(4, fulfillment=shipment(displayStatus="ATTEMPTED_DELIVERY")), settings=s, now=now),
        classify(order(5, fulfillment=shipment(displayStatus="LABEL_PRINTED")), settings=s, now=now),
        classify(order(6, fulfillment=shipment(displayStatus="DELIVERED", deliveredAt=iso(now - 3600))), settings=s,
                 now=now),
    ]


def visible_leftovers() -> list[str]:
    """Abre la ventana con todas sus pantallas y diálogos y devuelve lo que no se traduciría."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["LABELBRIDGE_HOME"] = tempfile.mkdtemp(prefix="labelbridge-i18n-")
    os.environ["LABELBRIDGE_SECRETS"] = "file"
    os.environ.setdefault("LABELBRIDGE_REDUCED_MOTION", "1")
    from labelbridge.app import _ensure_qt_plugin_path

    _ensure_qt_plugin_path()
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([sys.argv[0]])
    from labelbridge import i18n, paths
    from labelbridge.config import ConfigManager, PrinterProfile
    from labelbridge.events import Events
    from labelbridge.jobstore import JobStore
    from labelbridge.printing.engine import PrintEngine
    from labelbridge.secret_store import SecretStore
    from labelbridge.services import Services
    from labelbridge.ui.context import AppContext, QtBridge
    from labelbridge.ui.main_window import MainWindow
    from labelbridge.ui.theme import apply_theme

    i18n.set_language("en")
    translated = i18n.set_audit(True)
    try:
        apply_theme(app)
        cfg = ConfigManager(paths.config_path())
        cfg.data.local_api.enabled = False
        cfg.data.printers.append(PrinterProfile(alias="zebra", kind="file"))
        secrets = SecretStore(paths.data_dir() / "secrets.json", backend="file")
        store = JobStore(paths.db_path(), paths.spool_dir())
        events = Events()
        engine = PrintEngine(store, cfg, events)
        services = Services(cfg, secrets, store, engine, events)
        window = MainWindow(AppContext(cfg, secrets, store, engine, services, events, QtBridge(events, cfg)))
        window.resize(1280, 900)
        window.show()
        found: list[str] = []
        for key, page in window.pages.items():
            window.navigate(key)
            app.processEvents()
            found += [f"[{key}] {item}" for item in leftovers_in(page, translated)]
        sidebar = window.centralWidget().layout().itemAt(0).widget()
        found += [f"[barra lateral] {item}" for item in leftovers_in(sidebar, translated)]
        # Diálogos que se abren desde las pantallas.
        from labelbridge.ui.pages.orders import ShippingRulesDialog
        from labelbridge.ui.printer_dialog import PrinterDialog

        dialog = PrinterDialog(window, window.ctx, cfg.data.printers[0], False)
        found += [f"[impresora] {item}" for item in leftovers_in(dialog, translated)]
        dialog.close()
        rules = ShippingRulesDialog(window, cfg.data.shopify.shipping_rules, cfg.data.shopify.default_carrier)
        found += [f"[reglas] {item}" for item in leftovers_in(rules, translated)]
        rules.close()
        # Pedidos con datos: los que necesitan acción, los retrasos y la ventana «Comprar guía».
        from labelbridge.ui.purchase_dialog import PurchaseDialog

        rows = sample_rows(cfg)
        orders = window.pages["orders"]
        orders.all_rows = rows
        orders._render()
        app.processEvents()
        found += [f"[pedidos con datos] {item}" for item in leftovers_in(orders, translated)]
        for row in rows[:2]:
            dialog = PurchaseDialog(window, window.ctx, row)
            dialog.other_carrier.setChecked(True)
            found += [f"[comprar guía] {item}" for item in leftovers_in(dialog, translated)]
            dialog._done({"state": "purchased", "carrier": "USPS", "tracking": "9200"})
            found += [f"[comprar guía] {item}" for item in leftovers_in(dialog, translated)]
            dialog.close()
        import time

        from labelbridge.updater import UpdateInfo
        from labelbridge.ui.update_dialog import UpdateDialog, WhatsNewDialog

        info = UpdateInfo(version="99.0.0", url="https://x/y.exe", size=35_000_000, notes="- x", published_at="2026-10-04T18:00:00Z")
        update = UpdateDialog(window, window.ctx, info, lambda: None)
        update._started = time.time() - 2
        update._on_progress(12_000_000, 35_000_000)  # el texto de la descarga en curso
        found += [f"[actualización] {item}" for item in leftovers_in(update, translated)]
        update.close()
        news = WhatsNewDialog(window, info)
        found += [f"[novedades] {item}" for item in leftovers_in(news, translated)]
        news.close()
        if window.assistant is not None:
            window.assistant.open()
            app.processEvents()
            found += [f"[asistente] {item}" for item in leftovers_in(window.assistant.panel, translated)]
        window.quitting = True
        window.close()
        store.close()
    finally:
        i18n.set_audit(False)
        i18n.set_language("es")
    return sorted(set(found))


if __name__ == "__main__":
    items = visible_leftovers()
    for item in items:
        print(item)
    print(f"{len(items)} texto(s) sin traducir")
