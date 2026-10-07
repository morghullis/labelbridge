"""«Rastrear»: el seguimiento de un pedido en una ventana de LabelBridge, sin ir a la web de la paquetería.

Por cada envío: paquetería y número (con botón para copiarlo), cómo va y cuándo llega, los pasos (guía creada →
recogido → en camino → en reparto → entregado) y los movimientos que informó la paquetería, del más reciente al más
viejo. Los datos vienen de Shopify (connectors/tracking.py).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPen
from PySide6.QtWidgets import QDialog, QFrame, QGridLayout, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from ..connectors.orders_overview import OrderRow
from ..connectors.tracking import STEPS, Shipment, Tracking
from ..i18n import date_text, tr
from .context import AppContext
from .theme import Color
from .widgets import Pill, button, copy_to_clipboard, label, run_async, run_dialog, window_closed

DONE, CURRENT, TODO, PROBLEM = Color.SUCCESS, Color.TEXT_STRONG, Color.INPUT_BORDER, Color.CRITICAL


class Stepper(QWidget):
    """Los pasos de un envío: hechos en verde, el actual en índigo (rojo si hay un problema), los que faltan en gris."""

    def __init__(self, step: int, problem: bool = False, closed: bool = False) -> None:
        super().__init__()
        self.step, self.problem, self.closed = step, problem, closed
        self.setMinimumHeight(58)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(520, 58)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        count = len(STEPS)
        margin = 46.0
        width = max(1.0, self.width() - 2 * margin)
        y, radius = 14.0, 8.0
        xs = [margin + width * index / (count - 1) for index in range(count)]
        for index in range(count - 1):
            color = DONE if index < self.step and not self.closed else TODO
            painter.setPen(QPen(QColor(color), 3))
            painter.drawLine(QPointF(xs[index] + radius, y), QPointF(xs[index + 1] - radius, y))
        font = QFont(self.font())
        font.setPointSizeF(max(8.0, font.pointSizeF() - 1))
        painter.setFont(font)
        for index, x in enumerate(xs):
            if self.closed:
                color = TODO
            elif index < self.step or (index == self.step and index == count - 1):
                color = DONE
            elif index == self.step:
                color = PROBLEM if self.problem else CURRENT
            else:
                color = TODO
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawEllipse(QPointF(x, y), radius, radius)
            painter.setPen(QColor(Color.TEXT_STRONG if index == self.step and not self.closed else Color.TEXT_SECONDARY))
            box = QRectF(x - margin, y + radius + 4, 2 * margin, 22)
            painter.drawText(box, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop), tr(STEPS[index]))
        painter.end()


class TrackingDialog(QDialog):
    """El seguimiento de los envíos de un pedido (Pedidos → Rastrear, Ver pedido, Imprimir guía)."""

    def __init__(self, parent: QWidget | None, ctx: AppContext, row: OrderRow, tracking: Tracking | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.row = row
        self.tracking: Tracking | None = None
        self.setWindowTitle(tr("Seguimiento · {0}", row.name))
        self.setMinimumSize(640, 480)
        self.resize(700, 620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(label(tr("Seguimiento de {0}", row.name), "CardTitle"))
        self.pill = Pill("off", "")
        self.pill.setVisible(False)
        head.addWidget(self.pill)
        head.addStretch(1)
        layout.addLayout(head)
        who = " · ".join(part for part in (row.customer, row.destination) if part)
        self.subtitle = label(who or tr("Cargando el seguimiento…"), "Muted", wrap=True)
        layout.addWidget(self.subtitle)
        self.status = label(tr("Cargando el seguimiento desde Shopify…"), "Small", wrap=True)
        layout.addWidget(self.status)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        self.content = QVBoxLayout(body)
        self.content.setContentsMargins(0, 4, 8, 4)
        self.content.setSpacing(12)
        self.content.addStretch(1)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        self.refresh_btn = button(tr("Actualizar"), "refresh")
        self.refresh_btn.clicked.connect(self.reload)
        buttons.addWidget(self.refresh_btn)
        self.web_btn = button(tr("Abrir en la web de la paquetería"), "link", "ghost")
        self.web_btn.clicked.connect(self._open_web)
        self.web_btn.setVisible(False)
        buttons.addWidget(self.web_btn)
        buttons.addStretch(1)
        close = button(tr("Cerrar"), None, "primary")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        if tracking is not None:  # ya cargado (p. ej. en las pruebas): sin ir a Shopify
            QTimer.singleShot(0, lambda: self._show(tracking))
        else:
            QTimer.singleShot(0, self.reload)

    # --- datos ---------------------------------------------------------------------------------
    def reload(self) -> None:
        self.refresh_btn.setEnabled(False)
        self.status.setText(tr("Cargando el seguimiento desde Shopify…"))
        self.status.setVisible(True)

        def failed(message: str) -> None:
            try:
                self.refresh_btn.setEnabled(True)
                self.status.setText(tr("No se pudo cargar el seguimiento: {0}", message))
            except RuntimeError as exc:  # la ventana se cerró antes
                if not window_closed(exc):
                    raise
                return

        def loaded(tracking: Tracking) -> None:
            try:
                self._show(tracking)
            except RuntimeError as exc:  # la ventana se cerró antes
                if not window_closed(exc):
                    raise
                return

        run_async(lambda: self.ctx.services.shopify.tracking(self.row.legacy), loaded, failed)

    def _show(self, tracking: Tracking) -> None:
        self.tracking = tracking
        self.refresh_btn.setEnabled(True)
        while self.content.count() > 1:  # todo menos el estiramiento del final
            item = self.content.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if tracking.destination and not self.row.destination:
            self.subtitle.setText(tracking.destination)
        shipments = tracking.shipments
        if not shipments:
            self.status.setText(tr("Este pedido todavía no tiene número de seguimiento."))
            self.pill.setVisible(False)
            self.web_btn.setVisible(False)
            return
        main = shipments[0]
        state = "ok" if main.step == len(STEPS) - 1 else ("error" if main.problem else "off" if main.closed else "info")
        self.pill.set_state(state, main.headline)
        self.pill.setVisible(True)
        self.status.setText(tr("Datos de Shopify, actualizados cuando la paquetería informa un movimiento."))
        self.web_btn.setVisible(bool(main.url))
        for shipment in shipments:  # con un solo envío, su estado ya está arriba
            self.content.insertWidget(self.content.count() - 1, self._shipment_card(shipment, len(shipments) > 1))

    def _shipment_card(self, s: Shipment, with_headline: bool = True) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Card")
        box = QVBoxLayout(frame)
        box.setContentsMargins(16, 12, 16, 14)
        box.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(label(s.carrier, "SectionTitle"))
        head.addWidget(label(s.number, selectable=True))
        copy = button("", "copy", "ghost", tr("Copiar el número de seguimiento"))
        copy.clicked.connect(lambda _=False, n=s.number: copy_to_clipboard(n))
        head.addWidget(copy)
        head.addStretch(1)
        if with_headline:
            headline = label(s.headline, "ItemTitle")
            if s.problem:
                headline.setStyleSheet(f"color: {PROBLEM};")
            head.addWidget(headline)
        box.addLayout(head)
        if s.more:
            box.addWidget(label(tr("También: {0}", ", ".join(s.more)), "Small", wrap=True, selectable=True))
        if s.closed:
            box.addWidget(label(tr("Esta guía se anuló: no se usa para el envío."), "Muted", wrap=True))
        else:
            box.addWidget(Stepper(s.step, s.problem, s.closed))
        box.addWidget(label(tr("Movimientos"), "Muted"))
        if not s.events:
            box.addWidget(label(tr("La paquetería todavía no informa movimientos. Aparecen aquí en cuanto Shopify los "
                                   "recibe."), "Small", wrap=True))
            return frame
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        for line, event in enumerate(s.events):
            when = label(date_text(event.at, year=False) if event.at else "", "Muted")
            when.setAlignment(Qt.AlignmentFlag.AlignTop)
            grid.addWidget(when, line, 0, alignment=Qt.AlignmentFlag.AlignTop)
            text = QVBoxLayout()
            text.setSpacing(1)
            title = label(event.title, "ItemTitle", wrap=True)
            if event.status in ("ATTEMPTED_DELIVERY", "DELAYED", "FAILURE", "NOT_DELIVERED"):
                title.setStyleSheet(f"color: {PROBLEM};")
            text.addWidget(title)
            details = " · ".join(part for part in (event.message, event.place) if part)
            if details:
                text.addWidget(label(details, "Small", wrap=True, selectable=True))
            grid.addLayout(text, line, 1)
        grid.setColumnStretch(1, 1)
        box.addLayout(grid)
        return frame

    def _open_web(self) -> None:
        """Opcional: la página de la paquetería, por si tiene más detalle que Shopify."""
        if self.tracking and self.tracking.shipments and self.tracking.shipments[0].url:
            QDesktopServices.openUrl(QUrl(self.tracking.shipments[0].url))


def open_tracking(parent: QWidget, ctx: AppContext, row: OrderRow) -> None:
    """«Rastrear» desde cualquier parte (Pedidos, Ver pedido, Imprimir guía): su propia ventana, encima de la que la
    abrió."""
    run_dialog(TrackingDialog(parent, ctx, row))
