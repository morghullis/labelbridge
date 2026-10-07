"""Inicio: el resumen del día. Lo que llegó hoy, lo que se imprimió, lo que espera a alguien y si la impresora está
lista; el interruptor de la impresión automática, las conexiones de un vistazo y lo último que pasó."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...connectors.orders_overview import OrderRow
from ...i18n import long_date, tr
from ...models import SOURCE_LABELS, status_text
from .. import icons
from ..actions import enqueue_test_label
from .guide import guide_banner, update_guide_banner
from ..context import AppContext
from ..theme import job_state
from ..widgets import Card, Page, Pill, ToggleSwitch, button, context_menu, error, label


class DayCard(QFrame):
    """Un número del día (pedidos, impresas, por hacer, impresora), con su detalle; clic para ir a verlo."""

    def __init__(self, title: str, icon_name: str, on_click: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.setObjectName("Card")
        self.on_click = on_click
        if on_click is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        column = QVBoxLayout(self)
        column.setContentsMargins(18, 14, 18, 14)
        column.setSpacing(4)
        head = QHBoxLayout()
        head.addWidget(label(title, "StatLabel"))
        head.addStretch(1)
        ico = label()
        ico.setPixmap(icons.pixmap(icon_name, "#94A3B8", 18))
        head.addWidget(ico)
        column.addLayout(head)
        self.value = label("—", "StatValue")
        column.addWidget(self.value)
        self.detail = label("", "Small", wrap=True)
        column.addWidget(self.detail)

    def set(self, value: str, detail: str, color: str = "") -> None:
        self.value.setText(value)
        self.value.setStyleSheet(f"color: {color};" if color else "")
        self.detail.setText(detail)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self.on_click is not None and event.button() == Qt.MouseButton.LeftButton:
            self.on_click()
        super().mouseReleaseEvent(event)


class DashboardPage(Page):
    def __init__(self, ctx: AppContext, navigate: Callable[[str], None]) -> None:
        super().__init__(tr("Inicio"), tr("Hoy, {0}", long_date()))
        self.ctx = ctx
        self.navigate = navigate
        self.orders: list[OrderRow] = []
        self.attention = 0
        self.subtitle = self.findChild(QLabel, "PageSubtitle")

        test = button(tr("Imprimir prueba"), "printer", tooltip=tr("Etiqueta de ejemplo en la impresora predeterminada"))
        test.clicked.connect(self._print_test)
        self.test_btn = test
        self.header_actions.addWidget(test)

        self.guide_banner = guide_banner(ctx, navigate)
        self.add(self.guide_banner)

        # Interruptor general (y si el equipo se queda despierto).
        banner = Card()
        row = QHBoxLayout()
        row.setSpacing(14)
        self.banner_icon = label()
        row.addWidget(self.banner_icon)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.banner_title = label("", "CardTitle")
        self.banner_desc = label("", "Muted", wrap=True)
        texts.addWidget(self.banner_title)
        texts.addWidget(self.banner_desc)
        row.addLayout(texts, 1)
        self.auto_switch = ToggleSwitch(not ctx.cfg.data.general.paused)
        self.auto_switch.toggled.connect(lambda on: ctx.engine.set_paused(not on))
        row.addWidget(self.auto_switch)
        banner.add(layout=row)
        self.add(banner)

        # El día en cuatro números.
        day = QHBoxLayout()
        day.setSpacing(16)
        self.card_orders = DayCard(tr("PEDIDOS HOY"), "package", lambda: navigate("orders"))
        self.card_printed = DayCard(tr("IMPRESAS HOY"), "check", lambda: navigate("history"))
        self.card_pending = DayCard(tr("POR HACER"), "alert", lambda: navigate("orders"))
        self.card_printer = DayCard(tr("IMPRESORA"), "printer", lambda: navigate("printers"))
        for card in (self.card_orders, self.card_printed, self.card_pending, self.card_printer):
            day.addWidget(card, 1)
        self.add(layout=day)

        # Conexiones de un vistazo (clic: su pantalla).
        conns = Card(tr("Conexiones"))
        strip = QHBoxLayout()
        strip.setSpacing(10)
        self.conn: dict[str, tuple[Pill, Any]] = {}
        for key, title, icon_name, page in (
            ("shopify", "Shopify", "shop", "shopify"),
            ("mqtt", "n8n", "flow", "n8n"),
            ("local_api", "API local", "server", "api"),
        ):
            item = QPushButton()
            item.setObjectName("ConnItem")
            item.setCursor(Qt.CursorShape.PointingHandCursor)
            item.clicked.connect(lambda _=False, p=page: self.navigate(p))
            inside = QHBoxLayout(item)
            inside.setContentsMargins(12, 8, 12, 8)
            inside.setSpacing(8)
            ico = label()
            ico.setPixmap(icons.pixmap(icon_name, "#4F46E5", 18))
            inside.addWidget(ico)
            inside.addWidget(label(tr(title), "ItemTitle"))
            inside.addStretch(1)
            pill = Pill("off", "—")
            inside.addWidget(pill)
            item.setMinimumHeight(44)
            strip.addWidget(item, 1)
            self.conn[key] = (pill, item)
        conns.add(layout=strip)
        self.add(conns)

        # Lo último que pasó.
        recent = Card(tr("Lo último"))
        see_all = button(tr("Ver historial"), None, "ghost")
        see_all.setStyleSheet("color: #4F46E5; font-weight: 600;")
        see_all.clicked.connect(lambda: self.navigate("history"))
        recent.header.addWidget(see_all)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([tr("Hora"), tr("Trabajo"), tr("Impresora"), tr("Estado")])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(3, 130)
        self.table.setMinimumHeight(230)
        context_menu(self.table, self._recent_menu)
        recent.add(self.table)
        self.empty = label(tr("Todavía no hay trabajos. Prueba con «Imprimir prueba»."), "Muted")
        recent.add(self.empty)
        self.add(recent)
        self.finish()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(250)
        self._refresh_timer.timeout.connect(self.refresh)
        ctx.bridge.event.connect(lambda *_: self._refresh_timer.start())
        ctx.bridge.config_saved.connect(lambda *_: self._refresh_timer.start())
        self.refresh()

    # --- datos de Pedidos (la pestaña los trae de Shopify; aquí no se vuelve a preguntar) -------------
    def set_orders(self, rows: list[OrderRow]) -> None:
        self.orders = list(rows)
        self._refresh_timer.start()

    def set_attention(self, count: int) -> None:
        self.attention = count
        self._refresh_timer.start()

    def refresh(self) -> None:
        ctx = self.ctx
        if self.subtitle is not None:  # la app puede quedar abierta varios días
            self.subtitle.setText(tr("Hoy, {0}", long_date()))
        update_guide_banner(self.guide_banner, ctx)
        g = ctx.cfg.data.general
        paused = g.paused
        self.auto_switch.blockSignals(True)
        self.auto_switch.setChecked(not paused)
        self.auto_switch.set_offset(0.0 if paused else 1.0)
        self.auto_switch.blockSignals(False)
        awake = tr(" Este equipo no se dormirá mientras LabelBridge esté abierto.") if g.keep_awake else ""
        if paused:
            self.banner_icon.setPixmap(icons.pixmap("pause", "#B45309", 28))
            self.banner_title.setText(tr("Impresión automática en pausa"))
            self.banner_desc.setText(tr("Los trabajos se siguen recibiendo y quedan en cola hasta que la reanudes."))
        else:
            self.banner_icon.setPixmap(icons.pixmap("zap", "#4F46E5", 28))
            self.banner_title.setText(tr("Impresión automática activa"))
            self.banner_desc.setText(
                tr("Equipo «{0}». Las etiquetas se imprimen apenas llegan.", g.agent_name) + awake
            )
        self._refresh_day()
        self._refresh_connections()
        self._refresh_recent()

    def _refresh_day(self) -> None:
        ctx = self.ctx
        midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        today = [row for row in self.orders if row.created >= midnight]
        if not ctx.cfg.data.shopify.enabled:
            self.card_orders.set("—", tr("Conecta tu tienda en Shopify"))
        else:
            value = sum(row.subtotal for row in today)
            self.card_orders.set(str(len(today)), tr("US${0:,.2f} en piezas", value) if today else tr("Ninguno todavía"))
        counts = ctx.store.counts()
        failed = counts["failed_today"]
        self.card_printed.set(str(counts["done_today"]), tr("{0} con error", failed) if failed else tr("Sin errores"),
                              "#BE123C" if failed and not counts["done_today"] else "")
        if self.attention:
            self.card_pending.set(str(self.attention), tr("Míralos en Pedidos, con el porqué"), "#B45309")
        else:
            self.card_pending.set("0", tr("Todo va solo"))
        printers = [p for p in ctx.cfg.data.printers if p.enabled]
        default = ctx.cfg.default_printer()
        if not printers:
            self.card_printer.set("—", tr("Agrega tu impresora de etiquetas"), "#B45309")
        else:
            name = default.display_name() if default else printers[0].display_name()
            state = tr("En pausa") if ctx.cfg.data.general.paused else tr("Lista")
            queued = counts["queued"]
            detail = state + (" · " + tr("{0} en cola", queued) if queued else "")
            self.card_printer.set(name, detail)
            self.card_printer.value.setStyleSheet("font-size: 17px; font-weight: 700;")  # un nombre, no un número

    def _refresh_connections(self) -> None:
        ctx = self.ctx
        texts = {"ok": tr("Conectado"), "connecting": tr("Conectando"), "error": tr("Error"), "warn": tr("Aviso"),
                 "off": tr("Desactivado")}
        for key, service in (("shopify", ctx.services.shopify), ("mqtt", ctx.services.mqtt),
                             ("local_api", ctx.services.local_api)):
            pill, item = self.conn[key]
            words = dict(texts)
            if key == "local_api" and service.state == "ok":
                words["ok"] = tr("Activa")
            pill.set_state(service.state, words.get(service.state, service.state))
            item.setToolTip(service.message)

    def _refresh_recent(self) -> None:
        jobs = self.ctx.store.list(limit=6)
        self.table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            when = datetime.fromtimestamp(job["created_at"])
            clock = when.strftime("%H:%M") if when.date() == datetime.now().date() else when.strftime("%d/%m %H:%M")
            source = tr(SOURCE_LABELS.get(job["source"], job["source"]))
            values = [clock, f"{job['title']} · {source}", job["printer_used"] or job["printer"] or tr("Predeterminada")]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, job["id"])
                self.table.setItem(row, col, item)
            pill = Pill(job_state(job), status_text(job))
            if job["error"]:
                pill.setToolTip(job["error"])
            cell = QWidget()
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(6, 0, 6, 0)
            cell_layout.addWidget(pill)
            cell_layout.addStretch(1)
            self.table.setCellWidget(row, 3, cell)
            self.table.setRowHeight(row, 36)
        self.table.setVisible(bool(jobs))
        self.empty.setVisible(not jobs)

    def _recent_menu(self) -> QMenu | None:
        """Clic derecho en «Lo último»: reimprimir esa etiqueta o verla en Historial."""
        item = self.table.item(self.table.currentRow(), 0)
        job_id = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not job_id:
            return None
        menu = QMenu(self)
        menu.addAction(icons.icon("printer"), tr("Reimprimir (sin pagar otra guía)"), lambda: self._reprint(job_id))
        menu.addAction(icons.icon("clock"), tr("Ver en Historial"), lambda: self.navigate("history"))
        return menu

    def _reprint(self, job_id: str) -> None:
        new_job = self.ctx.store.reprint(job_id)
        if new_job is None:
            error(self, tr("Reimprimir"), tr("Esa etiqueta ya no está guardada: reimprímela desde Pedidos o Historial."))
            return
        self.ctx.events.emit("job", new_job)
        self.ctx.engine.wake()

    def _print_test(self) -> None:
        enqueue_test_label(self.ctx)

