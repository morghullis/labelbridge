"""«Guía FedEx»: comprar en Shopify, con la menor cantidad de pasos, una guía que ninguna app puede comprar.

Shopify no deja que las apps compren FedEx ni les acepta firma o seguro (shippingLabelPurchase no los tiene), así que
la guía se compra en el pedido en Shopify. Esta ventana hace todo lo demás: abre el pedido, queda flotando encima del
navegador con lo que hay que elegir en el mismo orden que la pantalla de Shopify (servicio, firma, seguro por el valor
de las piezas con botón para copiarlo, la caja), y en cuanto Shopify tiene la guía la imprime y lo confirma.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QDialog, QFrame, QGridLayout, QHBoxLayout, QProgressBar, QVBoxLayout, QWidget

from ..config import FEDEX_SIGNATURES
from ..connectors.orders_overview import OrderRow
from ..connectors.shopify import admin_url
from ..i18n import tr
from .context import AppContext
from .purchase_dialog import box_text, has_saved_box
from .widgets import button, copy_to_clipboard, label, run_async, step_title, window_closed

CHECK_EVERY_MS = 10_000  # cada cuánto se mira si Shopify ya tiene la guía (solo con la ventana abierta)
CHECK_FAILURES_SHOWN = 3  # tantas vueltas seguidas sin respuesta de Shopify: se muestra el motivo


def checklist(ctx: AppContext, row: OrderRow, carrier: str) -> list[tuple[str, str, str, str]]:
    """Lo que hay que elegir en Shopify, en el orden de su pantalla: (qué, cuál, aclaración, texto para copiar)."""
    s = ctx.cfg.data.shopify
    fedex = carrier == "FedEx"
    if has_saved_box(s.package) and s.package.use_custom:
        items = [(tr("Paquete y peso"), tr("Mi caja"), box_text(s.package), "")]
    else:
        items = [(tr("Paquete y peso"), tr("La que sale elegida"), tr("el paquete de Shopify"), "")]
    items.append((tr("Servicio de envío"), s.fedex_service if fedex else tr("El de {0} que prefieras", carrier), "", ""))
    if fedex and s.fedex_signature in FEDEX_SIGNATURES and s.fedex_signature != "none":
        items.append((tr("Firma"), tr(FEDEX_SIGNATURES[s.fedex_signature]), "", ""))
    if fedex and s.fedex_insure and row.subtotal > 0:
        items.append((tr("Seguro de envío"), tr("US${0:,.2f}", row.subtotal), tr("el valor de las piezas"),
                      f"{row.subtotal:.2f}"))
    items.append((tr("Aviso al cliente"), tr("Actívalo") if s.notify_customer else tr("Como prefieras"), "", ""))
    return items


class FedexDialog(QDialog):
    """La guía de un pedido que se compra en Shopify (FedEx): abre el pedido, dice qué elegir y la imprime sola."""

    def __init__(self, parent: QWidget | None, ctx: AppContext, row: OrderRow, *, open_shopify: bool = True) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.row = row
        self.carrier = row.carrier or row.rule_carrier or "FedEx"
        self.done = False
        self.checking = False
        self.failures = 0  # vueltas seguidas en que Shopify no contestó
        self.open_shopify = open_shopify
        self.use_other = False  # «Usar otra paquetería…»: Pedidos abre «Imprimir guía» con la lista de servicios
        # Ventana propia que queda encima del navegador mientras se compra en Shopify (sin bloquear LabelBridge).
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle(tr("Guía {0} · {1}", self.carrier, row.name))
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)
        layout.addWidget(label(tr("Guía {0} de {1}", self.carrier, row.name), "CardTitle"))
        who = " · ".join(part for part in (row.customer, row.destination) if part)
        if row.subtotal:
            who += " · " + tr("{0} en piezas", tr("US${0:,.2f}", row.subtotal))
        layout.addWidget(label(who, "Muted", wrap=True))

        # ① Abrir el pedido en Shopify.
        layout.addWidget(self._step(1, tr("En el pedido en Shopify, pulsa «Crear etiqueta de envío»")))
        open_row = QHBoxLayout()
        open_row.setContentsMargins(34, 0, 0, 0)
        self.open_btn = button(tr("Abrir el pedido en Shopify"), "link", "primary")
        self.open_btn.clicked.connect(self._open)
        open_row.addWidget(self.open_btn)
        open_row.addStretch(1)
        layout.addLayout(open_row)

        # ② Qué elegir, en el orden de la pantalla de Shopify.
        layout.addWidget(self._step(2, tr("Elige así y pulsa «Comprar etiqueta de envío»")))
        card = QFrame()
        card.setObjectName("Card")
        grid = QGridLayout(card)
        grid.setContentsMargins(14, 10, 14, 10)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        for line, (what, value, note, copy) in enumerate(checklist(ctx, row, self.carrier)):
            grid.addWidget(label(what, "Muted"), line, 0, alignment=Qt.AlignmentFlag.AlignTop)
            text = QVBoxLayout()
            text.setSpacing(0)
            text.addWidget(label(value, "ItemTitle", selectable=True))
            if note:
                text.addWidget(label(note, "Small"))
            grid.addLayout(text, line, 1)
            if copy:
                copy_btn = button(tr("Copiar"), "copy", None, tr("Copia el valor para pegarlo en Shopify"))
                copy_btn.setMinimumWidth(copy_btn.sizeHint().width())
                copy_btn.clicked.connect(lambda _=False, value=copy: copy_to_clipboard(value))
                grid.addWidget(copy_btn, line, 2, alignment=Qt.AlignmentFlag.AlignTop)
        grid.setColumnStretch(1, 1)
        box = QHBoxLayout()
        box.setContentsMargins(34, 0, 0, 0)
        box.addWidget(card, 1)
        layout.addLayout(box)
        hint = label(tr("No hace falta imprimirla desde Shopify: LabelBridge la imprime en «{0}».",
                        ctx.cfg.data.shopify.shipping_label_printer or tr("la impresora predeterminada")),
                     "Small", wrap=True)
        hint.setContentsMargins(34, 0, 0, 0)
        layout.addWidget(hint)

        # ③ LabelBridge la imprime sola.
        layout.addWidget(self._step(3, tr("LabelBridge la imprime sola")))
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(6)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.status = label(tr("Preparando…"), "Hint", wrap=True)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.other_btn = button(tr("Usar otra paquetería…"), None, "ghost")
        self.other_btn.clicked.connect(self._other)
        buttons.addWidget(self.other_btn)
        buttons.addStretch(1)
        self.track_btn = button(tr("Rastrear"), "truck")
        self.track_btn.setVisible(False)
        self.track_btn.clicked.connect(self._track)
        buttons.addWidget(self.track_btn)
        self.close_btn = button(tr("Cerrar"))
        self.close_btn.clicked.connect(self.accept)
        buttons.addWidget(self.close_btn)
        layout.addLayout(buttons)

        self.timer = QTimer(self)
        self.timer.setInterval(CHECK_EVERY_MS)
        self.timer.timeout.connect(self._check)
        QTimer.singleShot(0, self._start)

    # --- piezas --------------------------------------------------------------------------------
    @staticmethod
    def _step(number: int, text: str) -> QWidget:
        return step_title(number, text)

    def fit(self) -> None:
        """El alto justo para el contenido (con textos que ocupan dos líneas, p. ej. en inglés)."""
        layout = self.layout()
        layout.activate()
        width = max(self.minimumWidth(), self.width())
        needed = layout.heightForWidth(width) if layout.hasHeightForWidth() else layout.sizeHint().height()
        self.resize(width, max(needed, layout.minimumSize().height()))

    def place(self) -> None:
        """A la derecha de la pantalla, para ver el pedido de Shopify al lado."""
        screen = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        self.fit()
        self.move(max(screen.left(), screen.right() - self.width() - 24), screen.top() + 60)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self.fit)

    # --- flujo ---------------------------------------------------------------------------------
    def _start(self) -> None:
        """Si Shopify ya tiene la guía, se imprime; si no, el pedido queda esperándola y se abre en Shopify."""
        shopify = self.ctx.services.shopify

        def first_look() -> dict:
            found = shopify.collect_shopify_label(self.row.legacy, self.row.name)
            if found.get("state") == "waiting":
                shopify.buy_in_shopify(self.row.legacy, self.row.name, self.carrier)
            return found

        def looked(found: dict) -> None:
            if self._show_result(found):
                return
            self._waiting()
            if self.open_shopify:
                self._open()

        def failed(message: str) -> None:  # p. ej. ya tiene una compra en curso o sin confirmar
            self._safe(lambda: (self.progress.setVisible(False), self._say(message, "Warning")))

        run_async(first_look, lambda found: self._safe(lambda: looked(found)), failed)

    def _waiting(self) -> None:
        self._say(tr("Esperando que la compres en Shopify… Puedes cerrar esta ventana: LabelBridge la imprime igual en "
                     "cuanto Shopify la tenga."), "Hint")
        self.timer.start()

    def _check(self) -> None:
        if self.checking or self.done:
            return
        self.checking = True
        shopify = self.ctx.services.shopify

        def finished(found: dict) -> None:
            self.checking = False
            self.failures = 0
            self._show_result(found)

        def failed(message: str) -> None:  # sin conexión un momento: se vuelve a mirar en la próxima vuelta
            self.checking = False
            self.failures += 1
            if self.failures >= CHECK_FAILURES_SHOWN:  # ya no es un momento: que se vea el motivo
                self._say(tr("No se pudo revisar el pedido en Shopify: {0}. Se sigue intentando.", message), "Warning")

        run_async(lambda: shopify.collect_shopify_label(self.row.legacy, self.row.name),
                  lambda found: self._safe(lambda: finished(found)), lambda message: self._safe(lambda: failed(message)))

    def _show_result(self, found: dict) -> bool:
        state = found.get("state")
        if state == "printing":
            self.done = True
            self.timer.stop()
            self.progress.setVisible(False)
            what = " ".join(part for part in (found.get("carrier"), found.get("tracking")) if part)
            self._say(tr("✓ Guía {0} en la cola de impresión. El pedido quedó preparado en Shopify.", what), "Hint")
            self.track_btn.setVisible(bool(found.get("tracking")))
            self.other_btn.setVisible(False)
            self.open_btn.setEnabled(False)
            self.close_btn.setText(tr("Listo"))
            return True
        if state == "no_document":
            self.done = True
            self.timer.stop()
            self.progress.setVisible(False)
            self._say(tr("Shopify compró la guía pero no entregó el PDF: imprímela desde el pedido en Shopify."),
                      "Warning")
            return True
        return False

    def _say(self, text: str, style: str) -> None:
        self.status.setText(text)
        self.status.setObjectName(style)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        if self.isVisible():
            QTimer.singleShot(0, self.fit)

    def _safe(self, action) -> None:
        try:
            action()
        except RuntimeError as exc:  # la ventana se cerró antes de que contestara Shopify
            if not window_closed(exc):
                raise
            return

    # --- acciones ------------------------------------------------------------------------------
    def _open(self) -> None:
        QDesktopServices.openUrl(QUrl(admin_url(self.ctx.cfg.data.shopify.shop, f"orders/{self.row.legacy}")))

    def _other(self) -> None:
        self.use_other = True
        self.accept()

    def _track(self) -> None:
        from .tracking_dialog import open_tracking

        open_tracking(self, self.ctx, self.row)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.timer.stop()
        super().closeEvent(event)

    def accept(self) -> None:
        self.timer.stop()
        super().accept()


_open_windows: list[FedexDialog] = []  # las ventanas abiertas (sin modal): que no las borre el recolector


def open_fedex(parent: QWidget, ctx: AppContext, row: OrderRow, on_other=None) -> FedexDialog:
    """Abre la ventana «Guía FedEx» (sin bloquear LabelBridge) y el pedido en Shopify. `on_other` se llama si la
    persona prefiere otra paquetería."""
    dialog = FedexDialog(parent, ctx, row)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    _open_windows.append(dialog)

    def finished(_result: int) -> None:
        if dialog in _open_windows:
            _open_windows.remove(dialog)
        if dialog.use_other and on_other is not None:
            on_other(row)

    dialog.finished.connect(finished)
    dialog.show()
    dialog.place()
    return dialog
