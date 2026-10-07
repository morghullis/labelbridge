"""«Imprimir N guías» (Pedidos con varios pedidos elegidos): qué se hace con cada uno, las opciones de las compras y
un clic para todo. Va de uno en uno con el avance a la vista y se puede detener entre un pedido y otro.

Lo que decide qué hacer con cada pedido y la tanda viven en connectors/label_batch.py (sin pantalla).
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..connectors import label_batch as lb
from ..connectors import shipping_services as ss
from ..connectors.orders_overview import OrderRow
from ..connectors.shopify import package_input
from ..i18n import tr
from .context import AppContext
from .purchase_dialog import ServicePicker, box_text, has_saved_box
from .widgets import button, label, run_async

RESULT_COLORS = {"done": "#0C5132", "failed": "#C70A24", "skipped": "#616161", "working": "#00418A"}


class _Signals(QObject):
    step = Signal(int, object)  # desde la tanda (otro hilo) → la ventana


def action_text(items: list[lb.BatchItem]) -> str:
    """El botón dice lo que va a pasar: cuántas se compran y cuántas se reimprimen."""
    buy = lb.purchases(items)
    reprint = sum(1 for item in items if item.plan == lb.REPRINT)
    if buy and reprint:
        return tr("Comprar {0} y reimprimir {1}", buy, reprint)
    if buy:
        return tr("Comprar e imprimir {0} guía(s)", buy)
    if reprint:
        return tr("Reimprimir {0} guía(s)", reprint)
    return tr("Nada que imprimir")


class BulkPrintDialog(QDialog):
    """Varias guías con una sola confirmación: las que ya existen se reimprimen y las demás se compran."""

    def __init__(self, parent: QWidget, ctx: AppContext, rows: list[OrderRow]) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.items = lb.make_batch(rows)
        self.running = False
        self.finished_any = False  # se compró o reimprimió algo: Pedidos se recarga al cerrar
        self.stop_event = threading.Event()
        self._signals = _Signals()
        # La tanda corre en otro hilo: cada avance se pinta en el de la ventana.
        self._signals.step.connect(self._step, Qt.ConnectionType.QueuedConnection)
        s = ctx.cfg.data.shopify
        self.setWindowTitle(tr("Imprimir {0} guías", len(rows)))
        self.setMinimumSize(760, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(label(tr("Imprimir {0} guías", len(rows)), "CardTitle"))
        buy = lb.purchases(self.items)
        reprint = sum(1 for item in self.items if item.plan == lb.REPRINT)
        skip = len(self.items) - buy - reprint
        layout.addWidget(label(tr("Se compran {0} · se reimprimen {1} · se omiten {2}", buy, reprint, skip), "Muted"))

        self.table = QTableWidget(len(self.items), 4)
        self.table.setHorizontalHeaderLabels([tr("Pedido"), tr("Cliente · destino"), tr("Qué se hace"), tr("Resultado")])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # sin el recuadro de foco en la primera celda
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        for col, mode in enumerate((QHeaderView.ResizeMode.ResizeToContents, QHeaderView.ResizeMode.Stretch,
                                    QHeaderView.ResizeMode.ResizeToContents, QHeaderView.ResizeMode.Stretch)):
            header.setSectionResizeMode(col, mode)
        for index, item in enumerate(self.items):
            row = item.row
            who = " · ".join(part for part in (row.customer, row.destination) if part)
            plan = tr(lb.PLAN_TEXT[item.plan])
            for col, text in enumerate((row.name, who, plan, item.why)):
                cell = QTableWidgetItem(text)
                cell.setToolTip(text)
                if col == 3 and item.why:
                    cell.setForeground(QColor(RESULT_COLORS["skipped"]))
                self.table.setItem(index, col, cell)
        layout.addWidget(self.table, 1)

        # Opciones de las compras (las reimpresiones salen igual que la primera vez).
        self.options_box = QWidget()
        form = QFormLayout(self.options_box)
        form.setContentsMargins(0, 4, 0, 0)
        form.setSpacing(8)
        self.services = ServicePicker(self, fedex=False)
        kinds = {ss.is_domestic("US", item.row.country) for item in self.items if item.plan == lb.BUY}
        if len(kinds) == 1:
            self.services.fill(ss.Route(domestic=kinds.pop()))
        else:  # nacionales e internacionales juntos: cada uno con lo que elija Shopify
            self.services.fill(ss.Route(domestic=True), prefer=ss.AUTO)
            self.services.combo.setEnabled(False)
            self.services.hint.setText(tr("Hay envíos nacionales e internacionales: cada uno sale con «Automático»."))
        service_box = QVBoxLayout()
        service_box.setSpacing(4)
        service_box.addWidget(self.services.combo)
        service_box.addWidget(self.services.hint)
        form.addRow(tr("Servicio"), service_box)
        self.package = QComboBox()
        self.package.addItem(tr("Paquete predeterminado de Shopify"), "shopify")
        if has_saved_box(s.package):
            self.package.addItem(tr("Mi caja · {0}", box_text(s.package)), "saved")
            self.package.setCurrentIndex(1 if s.package.use_custom else 0)
        form.addRow(tr("Paquete"), self.package)
        self.notify = QCheckBox(tr("Avisar a cada cliente por correo con su número de seguimiento"))
        self.notify.setChecked(s.notify_customer)
        form.addRow(self.notify)
        self.options_box.setVisible(buy > 0)
        layout.addWidget(self.options_box)

        printer = s.shipping_label_printer or tr("la impresora predeterminada")
        self.note = label(
            tr("Shopify cobra cada guía en tu factura y Shopify calcula el peso de cada pedido. Se imprimen en «{0}». "
               "Hasta {1} compras por tanda.", printer, lb.MAX_BATCH),
            "Hint", wrap=True,
        )
        layout.addWidget(self.note)
        self.progress = QProgressBar()
        self.progress.setRange(0, len(self.items))
        self.progress.setValue(0)
        self.progress.setFixedHeight(8)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.summary = label("", "Hint", wrap=True)
        self.summary.setVisible(False)
        layout.addWidget(self.summary)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_btn = button(tr("Cancelar"))
        self.cancel_btn.clicked.connect(self._cancel)
        buttons.addWidget(self.cancel_btn)
        self.go_btn = button(action_text(self.items), "printer", "primary")
        self.go_btn.clicked.connect(self._start)
        too_many = buy > lb.MAX_BATCH
        self.go_btn.setEnabled((buy or reprint) > 0 and not too_many)
        if too_many:
            self.summary.setObjectName("Warning")
            self.summary.setText(tr("Elige hasta {0} pedidos para comprar a la vez.", lb.MAX_BATCH))
            self.summary.setVisible(True)
        buttons.addWidget(self.go_btn)
        layout.addLayout(buttons)

    # --- estado --------------------------------------------------------------------------------
    def reject(self) -> None:
        if not self.running:  # Esc o la X mientras compra: se espera (o se usa «Detener»)
            super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.running:
            event.ignore()
            return
        super().closeEvent(event)

    def _cancel(self) -> None:
        if self.running:  # «Detener»: termina el pedido en curso y no sigue
            self.stop_event.set()
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setText(tr("Deteniendo…"))
        else:
            self.reject()

    def options(self) -> lb.BatchOptions:
        package = package_input(self.ctx.cfg.data.shopify.package) if self.package.currentData() == "saved" else None
        return lb.BatchOptions(service=self.services.key(), package=package, notify=self.notify.isChecked())

    # --- la tanda ------------------------------------------------------------------------------
    def _start(self) -> None:
        options = self.options()
        self.running = True
        self.options_box.setEnabled(False)
        self.go_btn.setEnabled(False)
        self.cancel_btn.setText(tr("Detener"))
        self.progress.setVisible(True)
        shopify = self.ctx.services.shopify
        run_async(
            lambda: lb.run_batch(shopify, self.items, options, progress=self._signals.step.emit, stop=self.stop_event),
            self._finished,
            self._failed,
        )

    def _step(self, index: int, item: lb.BatchItem) -> None:
        texts = {"working": tr("Comprando…") if item.plan == lb.BUY else tr("Reimprimiendo…"),
                 "done": "✓ " + item.detail + (" · " + tr("US${0:,.2f}", item.cost) if item.cost else ""),
                 "failed": "✗ " + item.detail, "skipped": item.detail}
        cell = self.table.item(index, 3)
        if cell is not None:
            cell.setText(texts.get(item.state, ""))
            cell.setToolTip(cell.text())
            cell.setForeground(QColor(RESULT_COLORS.get(item.state, "#303030")))
        if item.state in ("done", "failed", "skipped"):
            self.progress.setValue(min(self.progress.maximum(), index + 1))
        self.table.scrollToItem(self.table.item(index, 0), QTableWidget.ScrollHint.EnsureVisible)

    def _finished(self, summary: lb.BatchSummary) -> None:
        self.running = False
        self.finished_any = bool(summary.bought or summary.reprinted)
        parts = []
        if summary.bought:
            parts.append(tr("✓ {0} comprada(s)", summary.bought))
        if summary.reprinted:
            parts.append(tr("{0} reimpresa(s)", summary.reprinted))
        if summary.failed:
            parts.append(tr("✗ {0} con problema", summary.failed))
        if summary.skipped:
            parts.append(tr("{0} omitida(s)", summary.skipped))
        if summary.cost:
            parts.append(tr("US${0:,.2f} en guías", summary.cost))
        text = " · ".join(parts) or tr("No se hizo nada.")
        if summary.stopped:
            text = tr("Detenido. ") + text
        self._finish(text, "Warning" if summary.failed else "Hint")

    def _failed(self, message: str) -> None:
        self.running = False
        self._finish(message, "Warning")

    def _finish(self, text: str, style: str) -> None:
        self.progress.setVisible(False)
        self.summary.setObjectName(style)
        self.summary.setText(text)
        self.summary.setVisible(True)
        self.summary.style().unpolish(self.summary)
        self.summary.style().polish(self.summary)
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setText(tr("Cerrar"))
        self.go_btn.setVisible(False)
