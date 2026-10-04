"""Genera las imágenes de la Guía paso a paso (assets/guide/*.png).

Abre la app con datos de ejemplo (sin servicios ni impresoras reales), fotografía cada pantalla y
resalta en dorado lo que hay que pulsar, con números que coinciden con las instrucciones de la guía.
Lo que ocurre fuera de la app (Shopify, bandeja de Windows) se dibuja como ilustración.

Uso: python packaging/make_guide_images.py   (la compilación lo ejecuta antes de empaquetar)
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["QT_SCALE_FACTOR"] = "2"  # imágenes nítidas en pantallas de alta resolución
os.environ["LABELBRIDGE_HOME"] = tempfile.mkdtemp(prefix="labelbridge-guia-")
os.environ["LABELBRIDGE_SECRETS"] = "file"

from labelbridge.app import _ensure_qt_plugin_path  # noqa: E402

_ensure_qt_plugin_path()

from PySide6.QtCore import QEventLoop, QPoint, QPointF, QRectF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

OUT = ROOT / "assets" / "guide"
S = 2  # escala de las imágenes
GOLD = QColor("#F59E0B")
GOLD_GLOW = QColor(245, 158, 11, 80)
INK = QColor("#0F172A")
MUTED = QColor("#64748B")


# --- utilidades de dibujo -----------------------------------------------------------------

def pump(ms: int = 250) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def font(size: float, bold: bool = False, italic: bool = False) -> QFont:
    f = QFont("Segoe UI" if sys.platform == "win32" else "Helvetica Neue")
    f.setPixelSize(max(1, round(size)))
    f.setBold(bold)
    f.setItalic(italic)
    return f


def draw_badge(p: QPainter, number: int, center: QPointF, radius: float) -> None:
    p.save()
    p.setPen(QPen(QColor("white"), radius * 0.18))
    p.setBrush(GOLD)
    p.drawEllipse(center, radius, radius)
    p.setPen(QColor("#1E1B4B"))
    p.setFont(font(radius * 1.15, bold=True))
    p.drawText(QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2), Qt.AlignmentFlag.AlignCenter, str(number))
    p.restore()


def draw_highlight(p: QPainter, rect: QRectF, number: int | None, unit: float) -> None:
    p.save()
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(GOLD_GLOW, 9 * unit))
    p.drawRoundedRect(rect, 10 * unit, 10 * unit)
    p.setPen(QPen(GOLD, 3 * unit))
    p.drawRoundedRect(rect, 10 * unit, 10 * unit)
    p.restore()
    if number is not None:
        draw_badge(p, number, QPointF(rect.left() + 2 * unit, rect.top() + 2 * unit), 13 * unit)


def widget_rect(widget: QWidget, root: QWidget) -> QRectF:
    top_left = widget.mapTo(root, QPoint(0, 0))
    return QRectF(top_left.x(), top_left.y(), widget.width(), widget.height())


def united(rects: list[QRectF]) -> QRectF:
    result = QRectF(rects[0])
    for rect in rects[1:]:
        result = result.united(rect)
    return result


def capture(root: QWidget, targets: list, name: str, context: list[QWidget] | None = None, margin: int = 36) -> None:
    """Captura `root`, oscurece todo menos los objetivos, los numera y recorta alrededor."""
    pixmap = root.grab()
    ratio = pixmap.devicePixelRatio()
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(1)  # el escalado lo controlamos nosotros (si no, QPainter escala dos veces)
    numbered: list[tuple[int, QRectF]] = []
    for index, target in enumerate(targets, start=1):
        widgets = target if isinstance(target, (list, tuple)) else [target]
        numbered.append((index, united([widget_rect(w, root) for w in widgets])))

    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(ratio, ratio)
    padded = [(n, r.adjusted(-7, -7, 7, 7)) for n, r in numbered]
    shade = QPainterPath()
    shade.addRect(QRectF(0, 0, root.width(), root.height()))
    for _, rect in padded:
        hole = QPainterPath()
        hole.addRoundedRect(rect, 10, 10)
        shade = shade.subtracted(hole)
    p.fillPath(shade, QColor(15, 23, 42, 92))
    for _, rect in padded:
        draw_highlight(p, rect, None, 1)
    for number, rect in padded:
        draw_badge(p, number, QPointF(rect.left() + 2, rect.top() + 2), 13)
    p.end()

    area = united([r for _, r in padded] + [widget_rect(w, root) for w in (context or [])])
    area = area.adjusted(-margin, -margin, margin, margin).intersected(QRectF(0, 0, root.width(), root.height()))
    crop = image.copy(round(area.x() * ratio), round(area.y() * ratio), round(area.width() * ratio), round(area.height() * ratio))
    OUT.mkdir(parents=True, exist_ok=True)
    crop.save(str(OUT / name))
    print("  ✓", name, f"{crop.width()}x{crop.height()}")


class Sketch:
    """Ilustraciones simples (pantallas fuera de la app) con el mismo estilo de resaltado."""

    def __init__(self, width: int, height: int, background: str = "#F4F6FB") -> None:
        self.image = QImage(width * S, height * S, QImage.Format.Format_ARGB32_Premultiplied)
        self.image.fill(QColor(background))
        self.p = QPainter(self.image)
        self.p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.p.scale(S, S)
        self.width, self.height = width, height

    def window(self, rect: QRectF, title: str) -> QRectF:
        p = self.p
        p.setPen(QPen(QColor("#CBD5E1"), 1))
        p.setBrush(QColor("white"))
        p.drawRoundedRect(rect, 12, 12)
        bar = QRectF(rect.left(), rect.top(), rect.width(), 36)
        path = QPainterPath()
        path.addRoundedRect(bar, 12, 12)
        path.addRect(QRectF(bar.left(), bar.top() + 18, bar.width(), 18))
        p.fillPath(path.simplified(), QColor("#F1F5F9"))
        for i, color in enumerate(("#F87171", "#FBBF24", "#34D399")):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawEllipse(QPointF(rect.left() + 18 + i * 16, rect.top() + 18), 5, 5)
        p.setPen(QPen(QColor("#E2E8F0"), 1))
        p.setBrush(QColor("white"))
        address = QRectF(rect.left() + 76, rect.top() + 8, rect.width() - 92, 20)
        p.drawRoundedRect(address, 10, 10)
        self.text(address.left() + 10, address.top() + 1, title, 11, color="#475569", height=18)
        return QRectF(rect.left(), rect.top() + 36, rect.width(), rect.height() - 36)

    def text(self, x: float, y: float, value: str, size: float = 13, bold: bool = False, color: str = "#0F172A",
             width: float = 600, height: float | None = None, italic: bool = False) -> None:
        self.p.setPen(QColor(color))
        self.p.setFont(font(size, bold, italic))
        self.p.drawText(QRectF(x, y, width, height or size * 1.6), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, value)

    def button(self, rect: QRectF, value: str, primary: bool = False) -> QRectF:
        p = self.p
        p.setPen(QPen(QColor("#111827" if primary else "#CBD5E1"), 1))
        p.setBrush(QColor("#111827" if primary else "white"))
        p.drawRoundedRect(rect, 8, 8)
        p.setPen(QColor("white" if primary else "#111827"))
        p.setFont(font(12, bold=True))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, value)
        return rect

    def field(self, rect: QRectF, value: str, label_text: str = "") -> QRectF:
        p = self.p
        if label_text:
            self.text(rect.left(), rect.top() - 20, label_text, 11, bold=True, color="#334155")
        p.setPen(QPen(QColor("#CBD5E1"), 1))
        p.setBrush(QColor("white"))
        p.drawRoundedRect(rect, 7, 7)
        self.text(rect.left() + 10, rect.top(), value, 12, color="#0F172A", height=rect.height())
        return rect

    def checkbox(self, x: float, y: float, value: str) -> QRectF:
        p = self.p
        box = QRectF(x, y, 16, 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#4F46E5"))
        p.drawRoundedRect(box, 4, 4)
        p.setPen(QPen(QColor("white"), 2))
        p.drawPolyline([QPointF(x + 4, y + 8), QPointF(x + 7, y + 11), QPointF(x + 12, y + 5)])
        self.text(x + 24, y - 3, value, 12, height=22)
        return QRectF(x, y, 24 + 9 * len(value), 16)

    def highlight(self, rect: QRectF, number: int | None) -> None:
        draw_highlight(self.p, rect.adjusted(-6, -6, 6, 6), number, 1)

    def note(self, value: str) -> None:
        self.text(20, self.height - 30, value, 11, italic=True, color="#64748B", width=self.width - 40)

    def save(self, name: str) -> None:
        self.p.end()
        OUT.mkdir(parents=True, exist_ok=True)
        self.image.save(str(OUT / name))
        print("  ✓", name, f"{self.image.width()}x{self.image.height()}")


# --- ilustraciones ------------------------------------------------------------------------

def sketch_dev_dashboard() -> None:
    k = Sketch(980, 600)
    left = k.window(QRectF(20, 20, 450, 540), "dev.shopify.com/dashboard")
    p = k.p
    p.fillRect(QRectF(left.left() + 1, left.top(), 120, left.height() - 1), QColor("#F8FAFC"))
    for i, item in enumerate(("Inicio", "Apps", "Tiendas", "Ajustes")):
        k.text(left.left() + 18, left.top() + 20 + i * 34, item, 13, bold=item == "Apps", color="#0F172A")
    k.text(left.left() + 140, left.top() + 18, "Apps", 20, bold=True)
    create = k.button(QRectF(left.right() - 130, left.top() + 18, 110, 32), "Create app", primary=True)
    popup = QRectF(left.left() + 150, left.top() + 90, 270, 220)
    p.setPen(QPen(QColor("#CBD5E1"), 1))
    p.setBrush(QColor("white"))
    p.drawRoundedRect(popup, 10, 10)
    k.text(popup.left() + 16, popup.top() + 14, "Crear una app", 14, bold=True)
    option = k.button(QRectF(popup.left() + 16, popup.top() + 50, popup.width() - 32, 34), "Start from Dev Dashboard")
    k.field(QRectF(popup.left() + 16, popup.top() + 120, popup.width() - 32, 32), "LabelBridge", "Nombre de la app")
    k.button(QRectF(popup.right() - 96, popup.bottom() - 48, 80, 32), "Create", primary=True)
    k.highlight(create, 2)
    k.highlight(QRectF(option.left(), option.top(), option.width(), 108), 2)

    right = k.window(QRectF(490, 20, 470, 540), "dev.shopify.com · LabelBridge · Versiones")
    x = right.left() + 22
    x += 18
    k.text(x, right.top() + 8, "Access scopes", 13, bold=True)
    scopes = k.field(QRectF(x, right.top() + 46, right.width() - 62, 32), "read_orders")
    k.text(x, right.top() + 100, "Protected customer data", 13, bold=True)
    rows = [k.checkbox(x + 4, right.top() + 140 + i * 26, value) for i, value in enumerate(("Name", "Address", "Phone"))]
    release = k.button(QRectF(x, right.top() + 236, 100, 34), "Release", primary=True)
    install = k.button(QRectF(x + 112, right.top() + 236, 120, 34), "Install app")
    k.text(x, right.top() + 290, "App settings · Credentials", 13, bold=True)
    client_id = k.field(QRectF(x, right.top() + 352, right.width() - 148, 32), "a1b2c3d4e5f6a7b8c9d0", "Client ID")
    k.button(QRectF(client_id.right() + 10, client_id.top(), 70, 32), "Copiar")
    secret = k.field(QRectF(x, right.top() + 420, right.width() - 148, 32), "•••••••••••••••••••••", "Client secret")
    k.button(QRectF(secret.right() + 10, secret.top(), 70, 32), "Copiar")
    k.highlight(scopes, 3)
    k.highlight(QRectF(rows[0].left(), rows[0].top(), 140, 68), 4)
    k.highlight(QRectF(release.left(), release.top(), install.right() - release.left(), 34), 5)
    k.highlight(QRectF(client_id.left(), client_id.top() - 22, client_id.width() + 80, secret.bottom() - client_id.top() + 22), 6)
    k.note("Ilustración: las pantallas de Shopify pueden verse un poco distintas, pero los nombres son los mismos.")
    k.save("devdashboard.png")


def sketch_draft_order() -> None:
    k = Sketch(980, 520)
    area = k.window(QRectF(20, 20, 940, 460), "admin.shopify.com · Pedidos preliminares")
    p = k.p
    p.fillRect(QRectF(area.left() + 1, area.top(), 190, area.height() - 1), QColor("#F8FAFC"))
    menu = ["Inicio", "Pedidos", "   Pedidos preliminares", "Productos", "Clientes"]
    for i, item in enumerate(menu):
        k.text(area.left() + 18, area.top() + 20 + i * 32, item, 13, bold=item in ("Pedidos", "   Pedidos preliminares"))
    drafts = QRectF(area.left() + 12, area.top() + 82, 170, 30)
    main_x = area.left() + 216
    k.text(main_x, area.top() + 16, "Crear pedido", 20, bold=True)
    products = QRectF(main_x, area.top() + 60, 430, 120)
    customer = QRectF(main_x, area.top() + 196, 430, 120)
    for box, title, lines in (
        (products, "Productos", ["Anillo de prueba · Oro 14k", "1 × $480.00"]),
        (customer, "Cliente", ["María López", "Calle 8 #120, Miami FL 33130"]),
    ):
        p.setPen(QPen(QColor("#E2E8F0"), 1))
        p.setBrush(QColor("white"))
        p.drawRoundedRect(box, 10, 10)
        k.text(box.left() + 16, box.top() + 12, title, 13, bold=True)
        for j, line in enumerate(lines):
            k.text(box.left() + 16, box.top() + 46 + j * 26, line, 12, color="#334155")
    pay = k.button(QRectF(area.right() - 190, area.bottom() - 70, 160, 36), "Cobrar  ▾", primary=True)
    dropdown = QRectF(pay.left() - 40, pay.top() - 96, 200, 86)
    p.setPen(QPen(QColor("#CBD5E1"), 1))
    p.setBrush(QColor("white"))
    p.drawRoundedRect(dropdown, 8, 8)
    mark = QRectF(dropdown.left() + 6, dropdown.top() + 8, dropdown.width() - 12, 32)
    p.fillRect(mark, QColor("#F1F5F9"))
    k.text(mark.left() + 10, mark.top(), "Marcar como pagado", 12, bold=True, height=32)
    k.text(mark.left() + 10, mark.top() + 40, "Enviar factura", 12, height=32)
    k.highlight(drafts, 1)
    k.highlight(QRectF(products.left(), products.top(), products.width(), customer.bottom() - products.top()), 2)
    k.highlight(QRectF(dropdown.left(), dropdown.top(), dropdown.width(), pay.bottom() - dropdown.top()), 3)
    k.note("Ilustración del administrador de Shopify (Pedidos → Pedidos preliminares).")
    k.save("draft-order.png")


def sketch_tray() -> None:
    k = Sketch(760, 380, background="#1E293B")
    p = k.p
    gradient = QLinearGradient(0, 0, 760, 380)
    gradient.setColorAt(0, QColor("#1E3A8A"))
    gradient.setColorAt(1, QColor("#0F172A"))
    p.fillRect(QRectF(0, 0, 760, 380), gradient)
    bar = QRectF(0, 328, 760, 52)
    p.fillRect(bar, QColor("#111827"))
    k.text(640, 334, "10:24", 12, bold=True, color="#E5E7EB", width=90, height=20)
    k.text(640, 352, "04/10/2026", 11, color="#9CA3AF", width=90, height=18)
    icon_rect = QRectF(588, 340, 28, 28)
    icon = QImage(str(ROOT / "assets" / "icons" / "app-64.png"))
    if not icon.isNull():
        p.drawImage(icon_rect, icon)
    for i in range(2):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#4B5563"))
        p.drawEllipse(QPointF(540 + i * 22, 354), 6, 6)
    menu = QRectF(380, 70, 330, 236)
    p.setPen(QPen(QColor("#CBD5E1"), 1))
    p.setBrush(QColor("white"))
    p.drawRoundedRect(menu, 10, 10)
    items = ["Abrir LabelBridge", "Pausar impresión automática", "Imprimir etiqueta de prueba", "Buscar actualizaciones",
             "Salir (detiene la impresión automática)"]
    rows = []
    for i, item in enumerate(items):
        y = menu.top() + 12 + i * 42 + (10 if i == 4 else 0)
        rows.append(QRectF(menu.left() + 8, y, menu.width() - 16, 34))
        k.text(menu.left() + 22, y, item, 13, height=34)
    p.setPen(QPen(QColor("#E2E8F0"), 1))
    p.drawLine(QPointF(menu.left() + 12, menu.top() + 186), QPointF(menu.right() - 12, menu.top() + 186))
    k.highlight(icon_rect, 1)
    k.highlight(rows[1], 2)
    k.text(24, 24, "Clic derecho en el icono junto al reloj", 15, bold=True, color="#F8FAFC")
    k.text(24, 48, "La app sigue trabajando aunque cierres la ventana.", 13, color="#CBD5E1")
    k.save("tray.png")


# --- capturas de la app ----------------------------------------------------------------------

def demo_context():
    from labelbridge import paths
    from labelbridge.config import ConfigManager, PrinterProfile
    from labelbridge.events import Events
    from labelbridge.jobstore import JobStore
    from labelbridge.labels.order import sample_order
    from labelbridge.models import parse_job
    from labelbridge.printing.engine import PrintEngine
    from labelbridge.secret_store import SHOPIFY_CLIENT_SECRET, SecretStore
    from labelbridge.services import Services
    from labelbridge.ui.context import AppContext, QtBridge

    cfg = ConfigManager(paths.config_path())
    zebra = PrinterProfile(alias="etiquetas", kind="network", host="192.168.1.50", port=9100, language="zpl", media="4x6")
    office = PrinterProfile(alias="oficina", kind="system", system_name="HP LaserJet Pro M404", language="driver", media="letter")
    cfg.data.printers = [zebra, office]
    general = cfg.data.general
    general.default_printer_id = zebra.id
    general.agent_name = "PC-BODEGA"
    general.auto_discover = False
    general.guide_acknowledged = "daily,what"
    cfg.data.local_api.enabled = False
    cfg.data.labels.store_name = "Pedro Yoruba Jewelry"
    cfg.data.labels.sender_address = "2401 NW 7th St\nMiami, FL 33125\n+1 305 555 0123"
    shopify = cfg.data.shopify
    shopify.enabled, shopify.shop, shopify.client_id = True, "pedroyorubajewelry.myshopify.com", "a1b2c3d4e5f6a7b8c9d0e1f2"
    cfg.data.mqtt.channel = "pc-bodega"
    secrets = SecretStore(paths.data_dir() / "secrets.json", backend="file")
    secrets.set(SHOPIFY_CLIENT_SECRET, "shpss_ejemplo_no_real")
    store = JobStore(paths.db_path(), paths.spool_dir())
    for number in (1004, 1005, 1006):
        job, _ = store.add(parse_job({"id": f"pedido-{number}", "order": {**sample_order(), "name": f"#{number}"}}), "shopify")
        store.set_done(job["id"], "etiquetas")
    test, _ = store.add(parse_job({"id": "prueba-1", "title": "Etiqueta de prueba", "order": sample_order()}), "test")
    store.set_done(test["id"], "etiquetas")
    events = Events()
    engine = PrintEngine(store, cfg, events)
    services = Services(cfg, secrets, store, engine, events)
    services.shopify.state = "ok"
    services.shopify.message = "Conectado a pedroyorubajewelry.myshopify.com · revisado a las 10:24"
    return AppContext(cfg, secrets, store, engine, services, events, QtBridge(events, cfg))


def fake_system_printers() -> list[dict]:
    base = {"description": "", "location": "", "is_remote": False}
    return [
        {**base, "name": "ZDesigner ZD421-203dpi ZPL", "model": "ZDesigner ZD421-203dpi ZPL", "port": "USB001",
         "driver": "ZDesigner ZD421-203dpi ZPL", "is_default": True},
        {**base, "name": "HP LaserJet Pro M404", "model": "HP LaserJet Pro M404-M405 PCL 6", "port": "IP_192.168.1.40",
         "driver": "HP LaserJet Pro M404-M405 PCL 6", "is_default": False},
        {**base, "name": "Microsoft Print to PDF", "model": "Microsoft Print To PDF", "port": "PORTPROMPT:",
         "driver": "Microsoft Print To PDF", "is_default": False},
    ]


def main() -> None:
    app = QApplication.instance() or QApplication([sys.argv[0]])
    from labelbridge.ui import theme
    from labelbridge.ui.pages import printers as printers_page

    printers_page.list_system_printers = fake_system_printers
    app.setStyleSheet(theme.STYLESHEET)
    if sys.platform == "win32":
        app.setFont(QFont("Segoe UI", 10))

    from labelbridge.ui.main_window import MainWindow
    from labelbridge.ui.printer_dialog import PrinterDialog

    ctx = demo_context()
    window = MainWindow(ctx)
    window.resize(1180, 1700)
    window.show()
    pump(600)
    print("Generando imágenes de la guía en", OUT)

    window.navigate("printers")
    page = window.pages["printers"]
    pump(500)
    page.sys_table.selectRow(0)
    pump(100)
    capture(window, [page.add_btn, page.tabs.tabBar(), page.add_sys_btn], "printers.png", context=[page.table, page.sys_table])

    dialog = PrinterDialog(window, ctx, ctx.cfg.data.printers[0], False)
    dialog.resize(560, dialog.sizeHint().height())
    dialog.show()
    pump(300)
    capture(dialog, [dialog.alias, dialog.language, dialog.media, dialog.save_btn], "printer-dialog.png", context=[dialog], margin=0)
    dialog.close()

    window.navigate("home")
    page = window.pages["home"]
    pump(300)
    title = page.findChild(QLabel, "PageTitle")
    capture(window, [page.test_btn], "test-print.png", context=[title, page.test_btn], margin=24)
    capture(window, [page.table], "test-result.png", margin=24)

    window.navigate("settings")
    page = window.pages["settings"]
    pump(300)
    capture(window, [page.store_name, page.sender, page.save_btn], "sender.png", context=[page.show_price])
    capture(window, [page.autostart], "daily.png", context=[page.agent, page.notify_printed])

    window.navigate("shopify")
    page = window.pages["shopify"]
    pump(300)
    capture(window, [page.enabled, page.shop, [page.client_id, page.client_secret], page.test_btn], "shopify-connect.png")
    capture(window, [page.print_order, page.buy_label, page.metafield], "shopify-what.png", context=[page.buy_warning])

    sketch_dev_dashboard()
    sketch_draft_order()
    sketch_tray()
    window.quitting = True
    window.close()


if __name__ == "__main__":
    main()
