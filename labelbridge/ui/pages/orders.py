"""Pedidos: todo lo que llega de Shopify, ordenado. Primero lo que necesita una persona (con el porqué y el botón para
resolverlo), después cada pedido por paquetería con su seguimiento y lo que costó su guía.

Los datos se calculan en connectors/orders_overview.py; aquí solo se muestran. Se recargan cuando cambia algo
(el conector anota una compra, se imprime una guía, se guardan las reglas) o al pulsar «Actualizar».
"""

from __future__ import annotations

import csv
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPoint, QSortFilterProxyModel, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ...config import CARRIERS, ShippingRule
from ...connectors.orders_overview import CSV_COLUMNS, GROUPS, OrderRow, Summary, csv_rows, in_group, summarize
from ...connectors.shopify import admin_url
from ...i18n import N_, date_text, tr
from .. import icons
from ..context import AppContext
from ..widgets import Card, Page, button, copy_to_clipboard, error, info, label, run_async, run_dialog

PERIODS = (("today", N_("Hoy")), (7, N_("Últimos 7 días")), (30, N_("Últimos 30 días")), (60, N_("Últimos 60 días")))
STALE_SECONDS = 60
COLUMNS = [N_("Pedido"), N_("Fecha"), N_("Cliente · destino"), N_("Envío · piezas"), N_("Paquetería"), N_("Guía"),
           N_("Estado")]
STATE_COLORS = {
    "action": "#B45309",
    "waiting": "#3730A3",
    "done": "#166534",
    "cancelled": "#64748B",
    "info": "#475569",
}
CARRIER_COLORS = {  # (fondo, texto)
    "USPS": ("#DBEAFE", "#1E3A8A"),
    "FedEx": ("#EDE9FE", "#4D148C"),
    "UPS": ("#FEF3C7", "#5B3A1A"),
    "DHL": ("#FEF9C3", "#A16207"),
}
ACTION_BUTTONS = {  # acción → (texto, icono)
    "open": (N_("Abrir en Shopify"), "link"),
    "buy": (N_("Comprar en Shopify"), "link"),
    "void": (N_("Anular en Shopify"), "link"),
    "reprint": (N_("Reimprimir guía"), "printer"),
    "retry": (N_("Comprar guía…"), "tag"),
}


def money(value: float) -> str:
    return tr("US${0:,.2f}", value)


def carrier_chip(carrier: str) -> QWidget:
    text = carrier or tr("Sin envío")
    bg, fg = CARRIER_COLORS.get(carrier, ("#F1F5F9", "#334155"))
    chip = label(text)
    chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
    chip.setStyleSheet(
        f"QLabel {{ background: {bg}; color: {fg}; border-radius: 9px; padding: 2px 9px; font-weight: 700;"
        " font-size: 11px; }"
    )
    return chip


def guide_text(row: OrderRow) -> str:
    if row.labels:
        state = {"yes": tr("impresa"), "failed": tr("sin imprimir"), "pending": tr("imprimiendo")}.get(row.printed, "")
        return money(row.cost) + (f" · {state}" if state else "")
    if row.voided:
        return tr("anulada ({0})", money(row.voided))
    return "—"


class OrdersModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[OrderRow] = []

    def set_rows(self, rows: list[OrderRow]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return tr(COLUMNS[section])
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        row = self.rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return row.name
            if col == 1:
                return date_text(row.created, year=False) if row.created else ""
            if col == 2:
                return " · ".join(part for part in (row.customer, row.destination) if part)
            if col == 3:
                value = f"US${row.subtotal:,.0f}" if row.subtotal else ""
                return " · ".join(part for part in (row.method, value) if part)
            if col == 4:
                number = row.tracking[0][1] if row.tracking else ""
                short = f"…{number[-8:]}" if len(number) > 10 else number
                return " · ".join(part for part in (row.carrier or "—", short) if part)
            if col == 5:
                return guide_text(row)
            if col == 6:
                return row.status + (tr(" · prueba") if row.test else "")
        if role == Qt.ItemDataRole.UserRole:  # para ordenar
            return (row.name, row.created, row.customer, row.subtotal, row.carrier, row.cost, row.state)[col]
        if role == Qt.ItemDataRole.ForegroundRole and col == 6:
            return QColor("#BE123C" if row.alert else STATE_COLORS.get(row.state, "#334155"))
        if role == Qt.ItemDataRole.ToolTipRole:
            if col == 2:
                return " · ".join(part for part in (row.customer, row.destination) if part)
            lines = [row.alert_reason or row.reason or row.status]
            for carrier, number, _url in row.tracking:
                lines.append(f"{carrier} {number}")
            if row.voided:
                lines.append(tr("Guías anuladas: {0} (Shopify las devuelve)", money(row.voided)))
            return "\n".join(line for line in lines if line)
        return None


class ActionItem(QFrame):
    """Un pedido que necesita a una persona: quién, el porqué y el botón para resolverlo."""

    def __init__(self, row: OrderRow, page: "OrdersPage") -> None:
        super().__init__()
        self.setObjectName("ActionItem")
        column = QVBoxLayout(self)
        column.setContentsMargins(14, 12, 14, 12)
        column.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(carrier_chip(row.carrier))
        name = label(row.name, "CardTitle")
        head.addWidget(name)
        who = " · ".join(part for part in (row.customer, row.destination, row.method) if part)
        head.addWidget(label(who, "Muted"), 1)
        if row.subtotal:
            head.addWidget(label(tr("{0} en piezas", money(row.subtotal)), "Muted"))
        overdue = max(1, int(page.ctx.cfg.data.notifications.unshipped_days or 0))
        if row.waiting_days >= overdue:
            head.addWidget(label(tr("{0} días sin enviar", row.waiting_days), "Overdue"))
        column.addLayout(head)
        title = label(row.status, "SectionTitle")
        title.setStyleSheet(f"color: {STATE_COLORS['action']};")
        column.addWidget(title)
        column.addWidget(label(row.reason, wrap=True))
        actions = QHBoxLayout()
        actions.setSpacing(8)
        text, icon_name = ACTION_BUTTONS.get(row.action, ACTION_BUTTONS["open"])
        main = button(tr(text), icon_name, "primary")
        main.clicked.connect(lambda: page.run_action(row, row.action or "open"))
        actions.addWidget(main)
        if row.action == "buy":  # FedEx: un clic en Shopify, o aquí con otra paquetería
            other = button(tr("Otra paquetería…"), "tag")
            other.clicked.connect(lambda: page.run_action(row, "retry"))
            actions.addWidget(other)
        elif row.action not in ("open", "void", ""):
            other = button(tr("Abrir en Shopify"), "link")
            other.clicked.connect(lambda: page.run_action(row, "open"))
            actions.addWidget(other)
        actions.addStretch(1)
        column.addLayout(actions)


class DelayItem(QFrame):
    """Un envío con retraso o problema de entrega: qué pasa, desde cuándo y cómo rastrearlo."""

    def __init__(self, row: OrderRow, page: "OrdersPage") -> None:
        super().__init__()
        self.setObjectName("DelayItem")
        column = QVBoxLayout(self)
        column.setContentsMargins(14, 12, 14, 12)
        column.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(carrier_chip(row.carrier))
        head.addWidget(label(row.name, "CardTitle"))
        who = " · ".join(part for part in (row.customer, row.destination) if part)
        head.addWidget(label(who, "Muted"), 1)
        if row.tracking:
            head.addWidget(label(row.tracking[0][1], "Muted", selectable=True))
        column.addLayout(head)
        title = label(row.alert_title, "SectionTitle")
        title.setStyleSheet("color: #BE123C;")
        column.addWidget(title)
        column.addWidget(label(row.alert_reason, wrap=True))
        actions = QHBoxLayout()
        actions.setSpacing(8)
        track = button(tr("Rastrear"), "truck", "primary")
        track.clicked.connect(lambda: page.run_action(row, "track"))
        actions.addWidget(track)
        copy = button(tr("Copiar seguimiento"), "copy")
        copy.clicked.connect(lambda: page.run_action(row, "copy"))
        actions.addWidget(copy)
        shopify = button(tr("Abrir en Shopify"), "link")
        shopify.clicked.connect(lambda: page.run_action(row, "open"))
        actions.addWidget(shopify)
        actions.addStretch(1)
        column.addLayout(actions)


class ShippingRulesDialog(QDialog):
    """Por qué paquetería sale cada pedido: se aplica la primera regla que se cumple; si no, la predeterminada."""

    def __init__(self, parent: QWidget, rules: list[ShippingRule], default_carrier: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Reglas de paquetería"))
        self.setMinimumWidth(720)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(12)
        outer.addWidget(label(tr("Por qué paquetería sale cada pedido"), "CardTitle"))
        outer.addWidget(label(
            tr("Se aplica la primera regla que se cumple. Shopify no deja comprar guías de FedEx automáticamente: esos "
               "pedidos aparecen en «Necesitan acción», la compras en Shopify con un clic y LabelBridge la imprime sola."),
            "Muted", wrap=True,
        ))
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(8)
        headers = (tr("Piezas de más de"), tr("Método de envío contiene"), tr("Paquetería"), tr("La guía se compra"), "")
        for col, text in enumerate(headers):
            self.grid.addWidget(label(text, "Small"), 0, col)
        outer.addLayout(self.grid)
        self.rows: list[tuple[QDoubleSpinBox, QLineEdit, QComboBox, QComboBox, QPushButton]] = []
        for rule in rules:
            self._add_row(rule)
        add = button(tr("Agregar regla"), "plus", "ghost")
        add.clicked.connect(lambda: self._add_row(ShippingRule("", "FedEx", False, 0.0)))
        outer.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)
        rest = QHBoxLayout()
        rest.addWidget(label(tr("Todo lo demás sale por")))
        self.default = QComboBox()
        for carrier in CARRIERS:
            if carrier != "FedEx":
                self.default.addItem(carrier, carrier)
        self.default.setCurrentIndex(max(0, self.default.findData(default_carrier)))
        rest.addWidget(self.default)
        rest.addWidget(label(tr("y LabelBridge compra la guía sola.")))
        rest.addStretch(1)
        outer.addLayout(rest)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = button(tr("Cancelar"))
        cancel.clicked.connect(self.reject)
        save = button(tr("Guardar"), "check", "primary")
        save.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        outer.addLayout(buttons)

    def _add_row(self, rule: ShippingRule) -> None:
        over = QDoubleSpinBox()
        over.setRange(0, 1_000_000)
        over.setDecimals(2)
        over.setPrefix(tr("US$"))
        over.setSpecialValueText(tr("Cualquier valor"))
        over.setValue(float(rule.over or 0))
        method = QLineEdit(rule.method)
        method.setPlaceholderText(tr("Cualquiera (p. ej. Express)"))
        carrier = QComboBox()
        for name in CARRIERS:
            carrier.addItem(name, name)
        carrier.setCurrentIndex(max(0, carrier.findData(rule.carrier)))
        mode = QComboBox()
        mode.addItem(tr("Sola (automática)"), True)
        mode.addItem(tr("En Shopify (1 clic)"), False)
        mode.setCurrentIndex(0 if rule.auto else 1)
        remove = button("", "trash", "ghost", tr("Quitar esta regla"))
        entry = (over, method, carrier, mode, remove)

        def carrier_changed() -> None:
            fedex = carrier.currentData() == "FedEx"
            if fedex:
                mode.setCurrentIndex(1)  # FedEx no se puede comprar por la API
            mode.setEnabled(not fedex)

        carrier.currentIndexChanged.connect(lambda _i: carrier_changed())
        carrier_changed()
        remove.clicked.connect(lambda: self._remove_row(entry))
        line = len(self.rows) + 1
        for col, widget in enumerate(entry):
            self.grid.addWidget(widget, line, col)
        self.rows.append(entry)

    def _remove_row(self, entry) -> None:
        if entry in self.rows:
            self.rows.remove(entry)
            for widget in entry:
                widget.hide()
                widget.deleteLater()

    def rules(self) -> list[ShippingRule]:
        out = []
        for over, method, carrier, mode, _remove in self.rows:
            rule = ShippingRule(method.text().strip(), carrier.currentData(), bool(mode.currentData()), round(over.value(), 2))
            if rule.method or rule.over > 0:
                out.append(rule)
        return out


class OrdersPage(Page):
    attention_changed = Signal(int)  # pedidos que necesitan acción (para el aviso en la barra lateral)

    def __init__(self, ctx: AppContext) -> None:
        super().__init__(
            tr("Pedidos"),
            tr("Todo lo que llega de Shopify, ordenado: qué necesita tu atención y por qué, por qué paquetería sale y "
               "cuánto costó cada guía."),
        )
        self.ctx = ctx
        self.all_rows: list[OrderRow] = []
        self.loaded_at = 0.0
        self.loading = False
        self.group = "all"

        self.period = QComboBox()
        for key, text in PERIODS:
            self.period.addItem(tr(text), key)
        self.period.setCurrentIndex(2)
        self.period.currentIndexChanged.connect(lambda _i: self.reload())
        self.header_actions.addWidget(self.period)
        self.refresh_btn = button(tr("Actualizar"), "refresh")
        self.refresh_btn.clicked.connect(self.reload)
        self.header_actions.addWidget(self.refresh_btn)
        self.manual_btn = button(tr("Nueva guía manual"), "plus", "primary",
                                 tooltip=tr("Crear un pedido manual en Shopify y comprar su guía"))
        self.manual_btn.clicked.connect(self.new_manual_label)
        self.header_actions.addWidget(self.manual_btn)
        rules_btn = button(tr("Reglas de paquetería"), "sliders")
        rules_btn.clicked.connect(self.edit_rules)
        self.header_actions.addWidget(rules_btn)
        self.export_btn = button(tr("Exportar"), "download", tooltip=tr("Guardar la lista en un archivo para Excel (CSV)"))
        self.export_btn.clicked.connect(self.export_csv)
        self.header_actions.addWidget(self.export_btn)

        self.notice = label("", "Warning", wrap=True)
        self.notice.setVisible(False)
        self.notice.setOpenExternalLinks(True)
        self.add(self.notice)

        stats = QHBoxLayout()
        stats.setSpacing(16)
        self.stat_action = self._stat(stats, tr("NECESITAN ACCIÓN"), "alert")
        self.stat_delays = self._stat(stats, tr("RETRASOS"), "clock")
        self.stat_orders = self._stat(stats, tr("PEDIDOS"), "package")
        self.stat_labels = self._stat(stats, tr("GUÍAS COMPRADAS"), "tag")
        self.stat_carriers = self._stat(stats, tr("PROMEDIO POR GUÍA"), "truck")
        self.add(layout=stats)

        self.action_card = Card(tr("Necesitan acción"))
        self.action_list = QVBoxLayout()
        self.action_list.setSpacing(10)
        self.action_card.add(layout=self.action_list)
        self.add(self.action_card)

        self.delay_card = Card(tr("Retrasos y entregas"))
        self.delay_list = QVBoxLayout()
        self.delay_list.setSpacing(10)
        self.delay_card.add(layout=self.delay_list)
        self.delay_card.setVisible(False)
        self.add(self.delay_card)

        table_card = Card(tr("Todos los pedidos"))
        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        self.tabs: dict[str, QPushButton] = {}
        for key, text in GROUPS:
            if key == "action":
                continue
            tab = button(tr(text), None, "segment")
            tab.setCheckable(True)
            tab.clicked.connect(lambda _=False, k=key: self._set_group(k))
            self.tab_group.addButton(tab)
            self.tabs[key] = tab
            tabs.addWidget(tab)
        self.tabs["all"].setChecked(True)
        tabs.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Buscar pedido, cliente o seguimiento…"))
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(240)
        self.search.textChanged.connect(lambda _t: self._fill_table())
        tabs.addWidget(self.search)
        table_card.add(layout=tabs)

        self.model = OrdersModel()
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(1, Qt.SortOrder.DescendingOrder)
        self.view.verticalHeader().setVisible(False)
        self.view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.view.setShowGrid(False)
        self.view.setAlternatingRowColors(True)
        self.view.setWordWrap(False)
        self.view.verticalHeader().setDefaultSectionSize(34)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._menu)
        self.view.doubleClicked.connect(lambda _i: self._selected_action("details"))
        header = self.view.horizontalHeader()
        header.setStretchLastSection(False)
        for col, width in enumerate((104, 92, 0, 140, 150, 140, 190)):
            if width:
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
                header.resizeSection(col, width)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)  # cliente y destino: lo que sobra
        self.view.setMinimumHeight(380)
        table_card.add(self.view, stretch=1)
        bottom = QHBoxLayout()
        for text, icon_name, action in (
            (tr("Ver pedido"), "eye", "details"),
            (tr("Abrir en Shopify"), "link", "open"),
            (tr("Rastrear"), "truck", "track"),
            (tr("Reimprimir guía"), "printer", "reprint"),
            (tr("Comprar guía…"), "tag", "retry"),
        ):
            btn = button(text, icon_name)
            btn.clicked.connect(lambda _=False, a=action: self._selected_action(a))
            bottom.addWidget(btn)
        bottom.addStretch(1)
        self.count = label("", "Muted")
        bottom.addWidget(self.count)
        table_card.add(layout=bottom)
        self.add(table_card)
        self.add(label(
            tr("Los costos salen del historial de cada pedido en Shopify (también las guías que compres a mano allí). "
               "Las guías anuladas se devuelven a tu factura de Shopify."),
            "Small", wrap=True,
        ))
        self.finish()

        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(4000)
        self._reload_timer.timeout.connect(self.reload)
        ctx.bridge.event.connect(self._on_event)
        ctx.bridge.config_saved.connect(lambda section: section in ("shopify", "all") and self._reload_timer.start())
        self._show_empty(tr("Cargando pedidos de Shopify…"))
        QTimer.singleShot(1500, self.reload)  # al abrir la app: el aviso de la barra lateral sin entrar aquí

    # --- piezas --------------------------------------------------------------------------------------
    def _stat(self, row: QHBoxLayout, title: str, icon_name: str):
        card = Card()
        head = QHBoxLayout()
        head.addWidget(label(title, "StatLabel"))
        head.addStretch(1)
        ico = label()
        ico.setPixmap(icons.pixmap(icon_name, "#94A3B8", 18))
        head.addWidget(ico)
        card.add(layout=head)
        value = label("—", "StatValue")
        card.add(value)
        detail = label("", "Small", wrap=True)
        card.add(detail)
        row.addWidget(card, 1)
        return value, detail

    def _connected(self) -> bool:
        s = self.ctx.cfg.data.shopify
        return s.enabled and bool(s.shop.strip())

    def _days(self) -> float:
        key = self.period.currentData()
        if key == "today":
            midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            return max(0.01, (time.time() - midnight) / 86400)
        return float(key)

    # --- datos ---------------------------------------------------------------------------------------
    def _on_event(self, kind: str, data: Any) -> None:
        if kind == "orders":
            self._reload_timer.start()
        elif kind == "job" and isinstance(data, dict) and data.get("source") == "shopify":
            if data.get("status") in ("done", "failed"):
                self._reload_timer.start()

    def refresh(self) -> None:
        """Al entrar en la pestaña: recarga si los datos son de hace más de un minuto."""
        if time.time() - self.loaded_at > STALE_SECONDS:
            self.reload()

    def reload(self) -> None:
        if not self._connected():
            self.all_rows = []
            self._render()
            self._show_empty(tr("Conecta tu tienda en la pestaña Shopify para ver aquí tus pedidos."))
            return
        if self.loading:
            self._reload_timer.start()  # termina la carga en curso y vuelve a mirar
            return
        self.loading = True
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText(tr("Cargando…"))
        days = self._days()
        service = self.ctx.services.shopify
        run_async(lambda: service.overview(days=days), self._loaded, self._failed)

    def _loaded(self, rows: list[OrderRow]) -> None:
        self.loading = False
        self.loaded_at = time.time()
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText(tr("Actualizar"))
        self.all_rows = rows
        self.notice.setVisible(False)
        self._render()
        if self.ctx.services.shopify.overview_truncated:  # Shopify devuelve como mucho 1000 por consulta
            self.notice.setText(tr("Se muestran los {0} pedidos más recientes de este período: elige un período más corto para ver los anteriores.", len(rows)))
            self.notice.setVisible(True)

    def _failed(self, message: str) -> None:
        self.loading = False
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText(tr("Actualizar"))
        self.notice.setText(tr("No se pudieron cargar los pedidos de Shopify: {0}", message))
        self.notice.setVisible(True)

    # --- pintar --------------------------------------------------------------------------------------
    def _show_empty(self, text: str) -> None:
        self._clear_actions()
        hint = label(text, "Muted", wrap=True)
        self.action_list.addWidget(hint)

    def _clear_actions(self) -> None:
        while self.action_list.count():
            item = self.action_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _render(self) -> None:
        rows = self.all_rows
        summary = summarize(rows)
        self._render_stats(summary)
        self._render_actions([row for row in rows if row.needs_action])
        self._render_delays([row for row in rows if row.alert])
        for key, text in GROUPS:
            if key in self.tabs:
                self.tabs[key].setText(f"{tr(text)}  {summary.groups.get(key, 0)}")
        self._fill_table()
        self._render_notice(summary)
        self.attention_changed.emit(summary.action + summary.alerts)

    def _render_stats(self, summary: Summary) -> None:
        value, detail = self.stat_action
        value.setText(str(summary.action))
        value.setStyleSheet(f"color: {STATE_COLORS['action']};" if summary.action else "")
        detail.setText(tr("Todo va solo") if not summary.action else tr("Míralos abajo, con el porqué"))
        value, detail = self.stat_delays
        value.setText(str(summary.alerts))
        value.setStyleSheet("color: #BE123C;" if summary.alerts else "")
        detail.setText(tr("Todo va a tiempo") if not summary.alerts else tr("Envíos con retraso o problemas"))
        value, detail = self.stat_orders
        value.setText(str(summary.orders))
        detail.setText(self.period.currentText())
        value, detail = self.stat_labels
        value.setText(money(summary.cost))
        anuladas = tr(" · {0} anuladas", money(summary.voided)) if summary.voided else ""
        detail.setText(tr("{0} guía(s){1}", summary.labels, anuladas))
        value, detail = self.stat_carriers
        parts = sorted(summary.by_carrier.items(), key=lambda item: -item[1][1])
        value.setText(money(summary.cost / summary.labels) if summary.labels else "—")
        detail.setText(" · ".join(f"{carrier} {money(cost)} ({count})" for carrier, (count, cost) in parts) or tr("Sin guías"))

    def _render_actions(self, rows: list[OrderRow]) -> None:
        self._clear_actions()
        self.action_card.title_label.setText(tr("Necesitan acción  {0}", len(rows)) if rows else tr("Necesitan acción"))
        if not rows:
            ok = label(tr("✓ Nada pendiente: las guías se compran e imprimen solas."), "Hint", wrap=True)
            self.action_list.addWidget(ok)
            return
        for row in rows[:20]:
            self.action_list.addWidget(ActionItem(row, self))
        if len(rows) > 20:
            self.action_list.addWidget(label(tr("…y {0} más en la tabla de abajo.", len(rows) - 20), "Muted"))

    def _render_delays(self, rows: list[OrderRow]) -> None:
        while self.delay_list.count():
            item = self.delay_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.delay_card.setVisible(bool(rows))
        self.delay_card.title_label.setText(tr("Retrasos y entregas  {0}", len(rows)))
        for row in rows[:20]:
            self.delay_list.addWidget(DelayItem(row, self))
        if len(rows) > 20:
            self.delay_list.addWidget(label(tr("…y {0} más en «Retrasos», en la tabla de abajo.", len(rows) - 20), "Muted"))

    def _render_notice(self, summary: Summary) -> None:
        if summary.auto_fulfill:
            shop = self.ctx.cfg.data.shopify.shop
            self.notice.setText(
                tr("Shopify marcó {0} pedido(s) como preparados apenas se pagaron, sin guía. Así LabelBridge no puede comprarla. Cámbialo en Shopify → Configuración → Pago → «Procesamiento de pedidos» → «No preparar automáticamente ningún artículo»: <a href=\"{1}\">abrir ese ajuste</a>.", summary.auto_fulfill, admin_url(shop, 'settings/checkout'))
            )
            self.notice.setVisible(True)

    def _set_group(self, key: str) -> None:
        self.group = key
        self._fill_table()

    def _fill_table(self) -> None:
        text = self.search.text().strip().lower()
        visible = []
        for row in self.all_rows:
            if not in_group(row, self.group):
                continue
            if text:
                haystack = " ".join([row.name, row.customer, row.destination, row.method, row.carrier, row.status]
                                    + [number for _c, number, _u in row.tracking]).lower()
                if text not in haystack:
                    continue
            visible.append(row)
        self.model.set_rows(visible)
        self.count.setText(tr("{0} pedido(s)", len(visible)))

    # --- acciones ------------------------------------------------------------------------------------
    def _selected(self) -> OrderRow | None:
        indexes = self.view.selectionModel().selectedRows()
        if not indexes:
            return None
        source = self.proxy.mapToSource(indexes[0])
        return self.model.rows[source.row()] if 0 <= source.row() < len(self.model.rows) else None

    def _selected_action(self, action: str) -> None:
        row = self._selected()
        if row is None:
            info(self, tr("Pedidos"), tr("Elige un pedido de la tabla."))
            return
        self.run_action(row, action)

    def _menu(self, pos: QPoint) -> None:
        row = self._selected()
        if row is None:
            return
        menu = QMenu(self)
        menu.addAction(icons.icon("eye"), tr("Ver pedido"), lambda: self.run_action(row, "details"))
        menu.addAction(icons.icon("link"), tr("Abrir en Shopify"), lambda: self.run_action(row, "open"))
        if row.tracking:
            menu.addAction(icons.icon("truck"), tr("Rastrear el envío"), lambda: self.run_action(row, "track"))
            menu.addAction(icons.icon("copy"), tr("Copiar el número de seguimiento"), lambda: self.run_action(row, "copy"))
        if row.labels:
            menu.addAction(icons.icon("printer"), tr("Reimprimir la guía"), lambda: self.run_action(row, "reprint"))
        if row.action in ("retry", "buy"):
            menu.addAction(icons.icon("tag"), tr("Comprar guía…"), lambda: self.run_action(row, "retry"))
        menu.exec(self.view.viewport().mapToGlobal(pos))

    def run_action(self, row: OrderRow, action: str) -> None:
        shop = self.ctx.cfg.data.shopify.shop
        if action == "details":
            self._details(row)
        elif action in ("open", "void", "buy"):
            QDesktopServices.openUrl(QUrl(admin_url(shop, f"orders/{row.legacy}")))
        elif action == "track":
            url = next((u for _c, _n, u in row.tracking if u), "")
            if url:
                QDesktopServices.openUrl(QUrl(url))
            else:
                info(self, tr("Rastrear"), tr("Este pedido todavía no tiene número de seguimiento."))
        elif action == "copy":
            if row.tracking:
                copy_to_clipboard(row.tracking[0][1])
        elif action == "reprint":
            self._reprint(row)
        elif action == "retry":
            self._retry(row)

    def _reprint(self, row: OrderRow) -> None:
        def find() -> list[dict]:
            return [
                job for job in self.ctx.store.list(limit=3000)
                if job.get("source") == "shopify"
                and str((job.get("meta") or {}).get("order_id")) == row.legacy
                and "tracking" in (job.get("meta") or {})
            ]

        run_async(find, lambda jobs: self._reprint_jobs(row, jobs), lambda message: error(self, tr("Reimprimir"), message))

    def _reprint_jobs(self, row: OrderRow, jobs: list[dict]) -> None:
        if not jobs:  # se compró en Shopify: LabelBridge trae el PDF de ahí
            run_async(
                lambda: self.ctx.services.shopify.print_order_labels(row.legacy, row.name),
                lambda _queued: info(self, tr("Reimprimir"), tr("La guía de {0} está en la cola de impresión.", row.name)),
                lambda message: error(self, tr("Reimprimir"), message),
            )
            return
        latest = max(jobs, key=lambda job: job.get("created_at") or 0)
        if self.ctx.store.reprint(latest["id"]):
            self.ctx.engine.wake()
            info(self, tr("Reimprimir"), tr("La guía de {0} está otra vez en la cola de impresión.", row.name))

    def _details(self, row: OrderRow) -> None:
        """«Ver pedido»: qué tiene el pedido, sin salir de LabelBridge."""
        from ..order_dialog import OrderDialog

        run_dialog(OrderDialog(self, self.ctx, row, self.run_action))

    def new_manual_label(self) -> None:
        """«Nueva guía manual»: pedido manual en Shopify y, después, «Comprar guía» para ese pedido."""
        from ..manual_label_dialog import run_manual_label

        if not self.ctx.cfg.data.shopify.shop.strip():
            info(self, tr("Nueva guía manual"), tr("Primero conecta tu tienda en la pantalla Shopify."))
            return
        if run_manual_label(self, self.ctx):
            self.reload()

    def _retry(self, row: OrderRow) -> None:
        """«Comprar guía…»: los datos que hagan falta, confirmar, y se compra, se imprime y Shopify queda al día."""
        from ..purchase_dialog import PurchaseDialog

        dialog = PurchaseDialog(self, self.ctx, row)
        run_dialog(dialog)
        if dialog.bought:
            self.reload()

    def edit_rules(self) -> None:
        s = self.ctx.cfg.data.shopify
        dialog = ShippingRulesDialog(self, s.shipping_rules, s.default_carrier)
        if run_dialog(dialog) != QDialog.DialogCode.Accepted:
            return
        s.shipping_rules = dialog.rules()
        s.default_carrier = dialog.default.currentData() or "USPS"
        self.ctx.cfg.save("shopify")
        self.reload()  # se vuelve a clasificar con las reglas nuevas

    def export_csv(self) -> None:
        if not self.all_rows:
            info(self, tr("Exportar"), tr("No hay pedidos para exportar en este período."))
            return
        default = str(Path.home() / tr("pedidos-{0}.csv", f"{datetime.now():%Y-%m-%d}"))
        path, _ = QFileDialog.getSaveFileName(self, tr("Exportar pedidos"), default, "CSV (*.csv)")
        if not path:
            return
        rows = sorted(self.all_rows, key=lambda row: row.created, reverse=True)
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:  # con BOM: Excel lo abre bien
            writer = csv.writer(handle)
            writer.writerow([tr(column) for column in CSV_COLUMNS])
            writer.writerows(csv_rows(rows))
        info(self, tr("Exportar"), tr("Se guardaron {0} pedidos en {1}.", len(rows), path))
