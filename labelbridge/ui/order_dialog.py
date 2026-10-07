"""«Ver pedido»: qué tiene cada pedido sin salir de LabelBridge (Pedidos → doble clic).

Productos con su foto, variante, SKU y personalización, cliente y dirección, método y seguimiento, totales, la nota del
cliente y los datos del carrito (p. ej. el mensaje de regalo), y las instrucciones del pedido, que se pueden escribir
aquí: van a la nota del pedido en Shopify (se ven allí y en la hoja de empaque).
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import net
from ..connectors.label_batch import SKIP, plan_for
from ..connectors.orders_overview import OrderRow
from ..connectors.shopify import admin_url
from ..connectors.shopify_orders import (
    FINANCIAL_TEXT,
    FULFILLMENT_TEXT,
    MAX_INSTRUCTIONS,
    OrderDetail,
    status_text,
)
from ..i18n import date_text, tr
from .context import AppContext
from .widgets import Pill, button, copy_to_clipboard, error, label, run_async

THUMB = 56
_thumbs: dict[str, bytes] = {}  # fotos ya descargadas (mientras la app está abierta)


def money(value: float, currency: str) -> str:
    return tr("US${0:,.2f}", value) if currency == "USD" else f"{value:,.2f} {currency}"


def _fetch_thumbs(urls: list[str]) -> dict[str, bytes]:
    found = {}
    for url in urls[:30]:
        if url in _thumbs:
            found[url] = _thumbs[url]
            continue
        if not url.startswith("https://"):
            continue
        try:
            found[url] = _thumbs[url] = net.request("GET", url, timeout=15, max_bytes=600_000).body
        except net.NetworkError:
            continue
    return found


class OrderDialog(QDialog):
    """El detalle de un pedido de Shopify. `run_action` hace lo mismo que los botones de la tabla de Pedidos."""

    def __init__(self, parent: QWidget, ctx: AppContext, row: OrderRow,
                 run_action: Callable[[OrderRow, str], None] | None = None, detail: OrderDetail | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.row = row
        self.run_action = run_action
        self.detail: OrderDetail | None = None
        self.images: dict[str, list] = {}
        self.setWindowTitle(tr("Pedido {0}", row.name))
        self.setMinimumSize(720, 560)
        self.resize(780, 760)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(10)

        head = QHBoxLayout()
        self.title = label(tr("Pedido {0}", row.name), "CardTitle")
        head.addWidget(self.title)
        self.pills = QHBoxLayout()
        self.pills.setSpacing(6)
        head.addLayout(self.pills)
        head.addStretch(1)
        self.total = label("", "CardTitle")
        head.addWidget(self.total)
        layout.addLayout(head)
        self.subtitle = label(tr("Cargando el pedido desde Shopify…"), "Muted", wrap=True)
        layout.addWidget(self.subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.body = QWidget()
        self.content = QVBoxLayout(self.body)
        self.content.setContentsMargins(0, 4, 8, 4)
        self.content.setSpacing(12)
        self.content.addStretch(1)
        scroll.setWidget(self.body)
        layout.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        shopify = button(tr("Abrir en Shopify"), "link")
        shopify.clicked.connect(self._open_in_shopify)
        buttons.addWidget(shopify)
        buttons.addStretch(1)
        close = button(tr("Cerrar"))
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        # «Imprimir guía»: la reimprime o abre la compra (si el pedido lleva guía).
        self.print_btn = button(tr("Reimprimir guía") if row.labels else tr("Imprimir guía"), "printer", "primary")
        if row.labels:
            self.print_btn.setToolTip(tr("Es la misma guía: no se compra ni se paga otra."))
        self.print_btn.clicked.connect(lambda: self._action("print"))
        self.print_btn.setVisible(run_action is not None and plan_for(row)[0] != SKIP)
        buttons.addWidget(self.print_btn)
        layout.addLayout(buttons)

        if detail is not None:  # ya cargado (p. ej. en las pruebas): sin ir a Shopify
            QTimer.singleShot(0, lambda: self._show(detail))
        else:
            QTimer.singleShot(0, self.reload)

    # --- datos ---------------------------------------------------------------------------------
    def reload(self) -> None:
        run_async(lambda: self.ctx.services.shopify.order_detail(self.row.legacy), self._show, self._failed)

    def _failed(self, message: str) -> None:
        self.subtitle.setText(tr("No se pudo cargar el pedido: {0}", message))
        self.subtitle.setObjectName("Warning")
        self.subtitle.style().unpolish(self.subtitle)
        self.subtitle.style().polish(self.subtitle)

    def _show(self, detail: OrderDetail) -> None:
        self.detail = detail
        self.title.setText(tr("Pedido {0}", detail.name))
        while self.pills.count():
            item = self.pills.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if detail.cancelled:
            self.pills.addWidget(Pill("error", tr("Cancelado")))
        if detail.financial:
            self.pills.addWidget(Pill("ok" if detail.financial == "PAID" else "warn",
                                      status_text(FINANCIAL_TEXT, detail.financial)))
        if detail.fulfillment:
            self.pills.addWidget(Pill("ok" if detail.fulfillment == "FULFILLED" else "info",
                                      status_text(FULFILLMENT_TEXT, detail.fulfillment)))
        if detail.manual:
            self.pills.addWidget(Pill("off", tr("Manual")))
        self.total.setText(money(detail.total, detail.currency))
        created = date_text(self.row.created) if self.row.created else detail.created[:10]
        self.subtitle.setText(tr("{0} · {1} artículo(s)", created, detail.units))
        while self.content.count() > 1:  # todo menos el estiramiento del final
            item = self.content.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for section in (self._customer_section(detail), self._items_section(detail), self._totals_section(detail),
                        self._notes_section(detail)):
            if section is not None:
                self.content.insertWidget(self.content.count() - 1, section)
        urls = [item.image for item in detail.items if item.image]
        if urls:
            run_async(lambda: _fetch_thumbs(urls), self._set_thumbs)

    # --- secciones -----------------------------------------------------------------------------
    def _section(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("Card")
        box = QVBoxLayout(frame)
        box.setContentsMargins(16, 12, 16, 14)
        box.setSpacing(6)
        box.addWidget(label(title, "SectionTitle"))
        return frame, box

    def _customer_section(self, d: OrderDetail) -> QFrame:
        frame, box = self._section(tr("Cliente y envío"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)
        history = ""
        if d.orders_count:  # con read_customers: si ya compró antes
            history = tr("Primer pedido en la tienda") if d.orders_count == 1 else tr(
                "{0} pedidos en la tienda · {1} en total", d.orders_count, money(d.spent, d.currency))
            if d.customer_since[:4].isdigit():
                history += " · " + tr("cliente desde {0}", d.customer_since[:4])
        rows = [(tr("Cliente"), d.customer), (tr("Historial"), history), (tr("Email"), d.email),
                (tr("Teléfono"), d.phone), (tr("Envío a"), "\n".join(d.address)), (tr("Método"), d.method)]
        line = 0
        for name, value in rows:
            if not value:
                continue
            grid.addWidget(label(name, "Muted"), line, 0, alignment=Qt.AlignmentFlag.AlignTop)
            grid.addWidget(label(value, wrap=True, selectable=True), line, 1)
            line += 1
        for company, number, _url in d.tracking:
            grid.addWidget(label(tr("Seguimiento"), "Muted"), line, 0)
            track = QHBoxLayout()
            track.addWidget(label(f"{company} {number}".strip(), selectable=True))
            copy = button("", "copy", "ghost", tr("Copiar el número de seguimiento"))
            copy.clicked.connect(lambda _=False, n=number: copy_to_clipboard(n))
            track.addWidget(copy)
            track_btn = button(tr("Rastrear"), "truck", "ghost")
            track_btn.clicked.connect(self._track)  # en una ventana de LabelBridge, no en la web
            track.addWidget(track_btn)
            track.addStretch(1)
            grid.addLayout(track, line, 1)
            line += 1
        grid.setColumnStretch(1, 1)
        box.addLayout(grid)
        return frame

    def _items_section(self, d: OrderDetail) -> QFrame:
        frame, box = self._section(tr("Productos ({0})", d.units))
        for item in d.items:
            row = QHBoxLayout()
            row.setSpacing(12)
            photo = label("")
            photo.setFixedSize(THUMB, THUMB)
            photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            photo.setStyleSheet("QLabel { background: #F1F5F9; border-radius: 8px; }")
            if item.image:
                self.images.setdefault(item.image, []).append(photo)
            row.addWidget(photo, alignment=Qt.AlignmentFlag.AlignTop)
            text = QVBoxLayout()
            text.setSpacing(2)
            text.addWidget(label(item.title, "ItemTitle", wrap=True, selectable=True))
            details = [part for part in (item.variant, tr("SKU {0}", item.sku) if item.sku else "") if part]
            if details:
                text.addWidget(label(" · ".join(details), "Muted", wrap=True, selectable=True))
            for key, value in item.properties:
                text.addWidget(label(f"{key}: {value}", "Small", wrap=True, selectable=True))
            if not item.shipping:
                text.addWidget(label(tr("No requiere envío"), "Small"))
            text.addStretch(1)  # las líneas juntas arriba aunque la foto sea más alta
            row.addLayout(text, 1)
            quantity = label(f"{item.quantity} × {money(item.price, d.currency)}")
            quantity.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            row.addWidget(quantity, alignment=Qt.AlignmentFlag.AlignTop)
            box.addLayout(row)
        if not d.items:
            box.addWidget(label(tr("Este pedido no tiene productos."), "Muted"))
        return frame

    def _totals_section(self, d: OrderDetail) -> QFrame:
        frame, box = self._section(tr("Totales"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        parts = [(tr("Productos"), d.subtotal), (tr("Envío"), d.shipping), (tr("Impuestos"), d.taxes)]
        if d.discounts:
            parts.append((tr("Descuentos"), -d.discounts))
        parts.append((tr("Total"), d.total))
        for line, (name, value) in enumerate(parts):
            bold = "ItemTitle" if name == tr("Total") else None
            grid.addWidget(label(name, bold or "Muted"), line, 0)
            amount = label(money(value, d.currency), bold)
            amount.setAlignment(Qt.AlignmentFlag.AlignRight)
            grid.addWidget(amount, line, 1)
        grid.setColumnStretch(0, 1)
        box.addLayout(grid)
        return frame

    def _notes_section(self, d: OrderDetail) -> QFrame:
        frame, box = self._section(tr("Notas e instrucciones"))
        if d.customer_note:
            box.addWidget(label(tr("Nota del cliente"), "Muted"))
            box.addWidget(label(d.customer_note, wrap=True, selectable=True))
        for key, value in d.attributes:
            box.addWidget(label(f"{key}: {value}", wrap=True, selectable=True))
        box.addWidget(label(tr("Instrucciones del pedido"), "Muted"))
        self.instructions = QPlainTextEdit(d.instructions)
        self.instructions.setPlaceholderText(tr("Por ejemplo: envolver para regalo, caja negra, incluir tarjeta…"))
        self.instructions.setFixedHeight(90)
        box.addWidget(self.instructions)
        actions = QHBoxLayout()
        self.save_btn = button(tr("Guardar en Shopify"), "check", "primary")
        self.save_btn.clicked.connect(self._save_instructions)
        actions.addWidget(self.save_btn)
        self.saved = label("", "Small", wrap=True)
        actions.addWidget(self.saved, 1)
        box.addLayout(actions)
        box.addWidget(label(tr("Van a la nota del pedido en Shopify (también salen en la hoja de empaque). La nota del "
                               "cliente no se toca."), "Small", wrap=True))
        return frame

    def _set_thumbs(self, found: dict[str, bytes]) -> None:
        for url, data in found.items():
            pixmap = QPixmap()
            if not pixmap.loadFromData(data):
                continue
            scaled = pixmap.scaled(THUMB, THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
            for photo in self.images.get(url, []):
                try:
                    photo.setPixmap(scaled)
                except RuntimeError:  # la ventana se cerró mientras bajaban las fotos
                    return

    # --- acciones ------------------------------------------------------------------------------
    def _save_instructions(self) -> None:
        text = self.instructions.toPlainText().strip()
        if len(text) > MAX_INSTRUCTIONS:
            error(self, tr("Instrucciones"), tr("Las instrucciones son demasiado largas (máximo {0} caracteres).",
                                                MAX_INSTRUCTIONS))
            return
        self.save_btn.setEnabled(False)
        self.saved.setText(tr("Guardando…"))

        def done(current: str) -> None:
            self.save_btn.setEnabled(True)
            self.instructions.setPlainText(current)
            self.saved.setText(tr("✓ Guardadas en Shopify.") if current else tr("✓ Instrucciones borradas."))

        def failed(message: str) -> None:
            self.save_btn.setEnabled(True)
            self.saved.setText("")
            error(self, tr("Instrucciones"), message)

        run_async(lambda: self.ctx.services.shopify.set_instructions(self.row.legacy, text), done, failed)

    def _open_in_shopify(self) -> None:
        QDesktopServices.openUrl(QUrl(admin_url(self.ctx.cfg.data.shopify.shop, f"orders/{self.row.legacy}")))

    def _track(self) -> None:
        from .tracking_dialog import open_tracking

        open_tracking(self, self.ctx, self.row)

    def _action(self, action: str) -> None:
        if self.run_action is not None:
            self.accept()
            self.run_action(self.row, action)
