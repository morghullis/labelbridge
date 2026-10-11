"""Pedidos: todo lo que llega de Shopify, ordenado. Primero lo que necesita una persona (con el porqué y el botón para
resolverlo), después cada pedido por paquetería con su seguimiento y lo que costó su guía.

Los datos se calculan en connectors/orders_overview.py; aquí solo se muestran. Se recargan cuando cambia algo
(el conector anota una compra, se imprime una guía, se guardan las reglas) o al pulsar «Actualizar».

«Imprimir guía» hace lo que necesita cada pedido (connectors/label_batch.py): reimprime la que ya tiene o abre la
compra (servicio, caja y aviso); con varios pedidos elegidos, todo con una sola confirmación.
"""

from __future__ import annotations

import csv
import logging
import time
from html import escape
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPoint, QSortFilterProxyModel, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
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

from ... import errors
from ...config import CARRIERS, FEDEX_SERVICES, FEDEX_SIGNATURES, ShippingRule
from ...connectors import packaging as pk
from ...connectors import shipping_services as ss
from ...connectors.label_batch import BUY, IN_SHOPIFY, REPRINT, SKIP, plan_for
from ...connectors.shipping_services import AUTO
from ...connectors.orders_overview import (
    CARRIER_FILTERS,
    CSV_COLUMNS,
    STATUS_FILTERS,
    OrderRow,
    Summary,
    csv_rows,
    in_group,
    in_status,
    summarize,
)
from ...connectors.shopify import admin_url
from ...i18n import N_, date_text, tr
from .. import icons
from ..context import AppContext
from ..purchase_dialog import fill_packages, open_packages
from ..theme import Color
from ..widgets import (
    Card,
    FlowRow,
    Page,
    SplitRow,
    button,
    confirm,
    copy_to_clipboard,
    error,
    fit_height,
    info,
    label,
    run_async,
    run_dialog,
)

log = logging.getLogger(__name__)

PERIODS = (("today", N_("Hoy")), (7, N_("Últimos 7 días")), (30, N_("Últimos 30 días")), (60, N_("Últimos 60 días")))
STALE_SECONDS = 60
COLUMNS = [N_("Pedido"), N_("Fecha"), N_("Cliente · destino"), N_("Envío · piezas"), N_("Paquetería"), N_("Guía"),
           N_("Estado")]
STATE_COLORS = {  # color del texto del estado de cada fila
    "action": Color.WARNING,
    "waiting": Color.INFO,
    "done": Color.SUCCESS,
    "cancelled": Color.TEXT_SECONDARY,
    "info": Color.ICON,
}
CARRIER_COLORS = Color.CARRIERS  # (fondo, texto)
ACTION_BUTTONS = {  # acción → (texto, icono)
    "open": (N_("Abrir en Shopify"), "link"),
    "buy": (N_("Comprar en Shopify"), "link"),
    "void": (N_("Anular en Shopify"), "link"),
    "reprint": (N_("Imprimir guía"), "printer"),
    "retry": (N_("Imprimir guía"), "printer"),
}
PRINT_ACTIONS = ("reprint", "retry")  # «Imprimir guía»: reimprime o compra, según el pedido


def money(value: float) -> str:
    return tr("US${0:,.2f}", value)


def carrier_chip(carrier: str) -> QWidget:
    text = carrier or tr("Sin envío")
    bg, fg = CARRIER_COLORS.get(carrier, (Color.SURFACE_SELECTED, Color.ICON))
    chip = label(text)
    chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
    chip.setStyleSheet(
        f"QLabel {{ background: {bg}; color: {fg}; border-radius: 6px; padding: 2px 8px; font-weight: 600;"
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
            return QColor(Color.CRITICAL if row.alert else STATE_COLORS.get(row.state, Color.TEXT))
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
        column.setContentsMargins(12, 8, 12, 8)
        column.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(carrier_chip(row.carrier))
        head.addWidget(label(row.name, "ItemTitle"))
        who = [row.customer, row.destination, row.method]
        if row.subtotal:
            who.append(tr("{0} en piezas", money(row.subtotal)))
        head.addWidget(label(" · ".join(part for part in who if part), "Muted", wrap=True), 1)  # se parte si no cabe
        overdue = max(1, int(page.ctx.cfg.data.notifications.unshipped_days or 0))
        if row.waiting_days >= overdue:
            head.addWidget(label(tr("{0} días sin enviar", row.waiting_days), "Overdue"))
        actions = head  # los botones, a la derecha de la misma línea
        text, icon_name = ACTION_BUTTONS.get(row.action, ACTION_BUTTONS["open"])
        if row.action == "buy":  # se compra en Shopify: la ventana dice qué elegir y la imprime sola
            text, icon_name = tr("Comprar guía {0}", row.carrier or row.rule_carrier or "FedEx"), "truck"
        elif row.action in PRINT_ACTIONS and row.labels:  # ya tiene guía: se reimprime la misma, sin pagar otra
            text = N_("Reimprimir guía")
        main = button(tr(text), icon_name, "primary")
        main.clicked.connect(lambda: page.run_action(row, "print" if row.action in PRINT_ACTIONS else row.action or "open"))
        actions.addWidget(main)
        if row.action == "buy":  # FedEx: guiada en Shopify, o aquí con otra paquetería
            other = button(tr("Otra paquetería…"), "printer")
            other.clicked.connect(lambda: page.run_action(row, "other"))
            actions.addWidget(other)
        elif row.action not in ("open", "void", ""):
            other = button("", "link", None, tr("Abrir en Shopify"))
            other.clicked.connect(lambda: page.run_action(row, "open"))
            actions.addWidget(other)
        column.addLayout(head)
        color = STATE_COLORS["action"]
        why = label(f"<b style='color:{color}'>{escape(row.status)}</b> · {escape(row.reason)}", wrap=True)
        why.setTextFormat(Qt.TextFormat.RichText)
        column.addWidget(why)
        # Clic derecho: lo mismo que en la tabla.
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: page.order_menu([row]).exec(self.mapToGlobal(pos)))


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
        head.addWidget(label(who, "Muted", wrap=True), 1)
        if row.tracking:
            head.addWidget(label(row.tracking[0][1], "Muted", selectable=True))
        column.addLayout(head)
        title = label(row.alert_title, "SectionTitle")
        title.setStyleSheet(f"color: {Color.CRITICAL};")
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
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: page.order_menu([row]).exec(self.mapToGlobal(pos)))


# Paquetería de una regla → su carrierCode en Shopify (para la lista de servicios).
CARRIER_CODES = {"USPS": "usps", "UPS": "ups_shipping", "DHL": "dhl_express"}


def fill_services(combo: QComboBox, carrier: str, allowed: tuple[str, ...] | None, current: str) -> None:
    """Los servicios de esa paquetería para una regla («Automático» y los suyos; con un empaque oficial de USPS, solo
    los que vale). FedEx: el servicio se elige abajo (se compra en Shopify)."""
    combo.blockSignals(True)
    combo.clear()
    code = CARRIER_CODES.get(carrier, "")
    if carrier == "FedEx":
        combo.addItem(tr("El de FedEx de abajo"), "")
    else:
        if not allowed:
            combo.addItem(tr("Automático"), "")
        for service in ss.SERVICES:
            if service.carrier == code and (not allowed or service.key in allowed):
                combo.addItem(service.name, service.key)
    combo.setCurrentIndex(max(0, combo.findData(current or "")))
    combo.setEnabled(carrier != "FedEx")
    combo.blockSignals(False)


class ShippingRulesDialog(QDialog):
    """Por qué paquetería sale cada pedido, con qué empaque y qué servicio: se aplica la primera regla que se cumple;
    si no, «Todo lo demás». Con un empaque oficial de USPS solo se puede elegir Priority Mail (o Express)."""

    def __init__(self, parent: QWidget, rules: list[ShippingRule], default_carrier: str,
                 fedex: tuple[str, str, bool] = ("FedEx 2Day®", "signature", True), *, by_flow: bool = False,
                 shop: str = "", ctx: AppContext | None = None) -> None:
        super().__init__(parent)
        self.shop = shop
        self.ctx = ctx
        s = ctx.cfg.data.shopify if ctx is not None else None
        self.settings = s
        self.setWindowTitle(tr("Reglas de paquetería"))
        self.setMinimumWidth(1080)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(12)
        outer.addWidget(label(tr("Por qué paquetería sale cada pedido y en qué empaque"), "CardTitle"))
        outer.addWidget(label(
            tr("Se aplica la primera regla que se cumple, según el envío que pagó el cliente y el valor de las piezas. "
               "La guía se compra para el empaque elegido: con un empaque oficial de USPS, solo Priority Mail. Shopify "
               "no deja que LabelBridge compre guías de FedEx: las compra Shopify Flow automáticamente o tú en Shopify "
               "con un clic, y LabelBridge las imprime."),
            "Muted", wrap=True,
        ))
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(8)
        headers = (tr("Piezas de más de"), tr("Método de envío contiene"), tr("Paquetería"), tr("Empaque"),
                   tr("Servicio"), tr("La guía se compra"), "")
        for col, text in enumerate(headers):
            self.grid.addWidget(label(text, "Small"), 0, col)
        outer.addLayout(self.grid)
        self.rows: list[tuple[QDoubleSpinBox, QLineEdit, QComboBox, QComboBox, QComboBox, QComboBox, QPushButton]] = []
        for rule in rules:
            self._add_row(rule)
        extra = QHBoxLayout()
        add = button(tr("Agregar regla"), "plus", "ghost")
        add.clicked.connect(lambda: self._add_row(ShippingRule("", "USPS", True, 0.0, "", pk.PRIORITY)))
        extra.addWidget(add)
        self.packages_btn = button(tr("Empaques…"), "package", "ghost", tr("Tus empaques: sobres, bolsas y cajas"))
        self.packages_btn.clicked.connect(self._manage_packages)
        self.packages_btn.setVisible(ctx is not None)
        extra.addWidget(self.packages_btn)
        extra.addStretch(1)
        outer.addLayout(extra)
        rest = QHBoxLayout()
        rest.addWidget(label(tr("Todo lo demás sale por")))
        self.default = QComboBox()
        for carrier in CARRIERS:
            if carrier != "FedEx":
                self.default.addItem(carrier, carrier)
        self.default.setCurrentIndex(max(0, self.default.findData(default_carrier)))
        rest.addWidget(self.default)
        self.default_package = QComboBox()
        self.default_service = QComboBox()
        if s is not None:
            rest.addWidget(label(tr("en")))
            fill_packages(self.default_package, s, s.default_package, sizes=False)
            rest.addWidget(self.default_package)
            rest.addWidget(label(tr("con")))
            rest.addWidget(self.default_service)
            self._wire(self.default, self.default_package, self.default_service, s.default_service)
        rest.addWidget(label(tr("y LabelBridge compra la guía sola.")))
        rest.addStretch(1)
        outer.addLayout(rest)
        # FedEx: quién la compra (Shopify Flow solo, o una persona con la ventana «Guía FedEx») y qué se elige.
        outer.addWidget(label(tr("¿Quién compra la FedEx?"), "Small", wrap=True))
        who = QHBoxLayout()
        who.setSpacing(8)
        self.fedex_flow = QComboBox()
        self.fedex_flow.addItem(tr("Shopify Flow, automáticamente (sin firma ni seguro)"), True)
        self.fedex_flow.addItem(tr("Yo, en Shopify con la ventana «Guía FedEx»"), False)
        self.fedex_flow.setCurrentIndex(0 if by_flow else 1)
        who.addWidget(self.fedex_flow)
        self.flow_help = button(tr("Cómo armar el flujo…"), "book", "ghost")
        self.flow_help.clicked.connect(self.show_flow_setup)
        who.addWidget(self.flow_help)
        who.addStretch(1)
        outer.addLayout(who)
        self.prefs_title = label(tr("La ventana «Guía FedEx» te dice que elijas"), "Small", wrap=True)
        outer.addWidget(self.prefs_title)
        prefs = QHBoxLayout()
        prefs.setSpacing(8)
        self.fedex_service = QComboBox()
        for name in FEDEX_SERVICES:
            self.fedex_service.addItem(name, name)
        self.fedex_service.setCurrentIndex(max(0, self.fedex_service.findData(fedex[0])))
        self.fedex_signature = QComboBox()
        for code, text in FEDEX_SIGNATURES.items():
            self.fedex_signature.addItem(tr(text), code)
        self.fedex_signature.setCurrentIndex(max(0, self.fedex_signature.findData(fedex[1])))
        self.fedex_insure = QCheckBox(tr("Seguro por el valor de las piezas"))
        self.fedex_insure.setChecked(bool(fedex[2]))
        for widget in (self.fedex_service, self.fedex_signature, self.fedex_insure):
            prefs.addWidget(widget)
        prefs.addStretch(1)
        outer.addLayout(prefs)
        self.fedex_flow.currentIndexChanged.connect(lambda _i: self._fedex_mode())
        self._fedex_mode()
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = button(tr("Cancelar"))
        cancel.clicked.connect(self.reject)
        save = button(tr("Guardar"), "check", "primary")
        save.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        outer.addLayout(buttons)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, lambda: fit_height(self))

    def _wire(self, carrier: QComboBox, package: QComboBox, service: QComboBox, current: str) -> None:
        """La lista de servicios sigue a la paquetería y al empaque elegidos."""

        def refresh(prefer: str = "") -> None:
            allowed = (pk.allowed_services(self.settings, str(package.currentData() or ""))
                       if self.settings is not None else None)
            fill_services(service, carrier.currentData() or "", allowed, prefer or str(service.currentData() or ""))

        # Al pasar a USPS: Priority Mail (vale en cualquier empaque); a otra paquetería, «Automático».
        carrier.currentIndexChanged.connect(lambda _i: refresh(pk.PRIORITY if carrier.currentData() == "USPS" else ""))
        package.currentIndexChanged.connect(lambda _i: refresh())
        refresh(current)

    def _manage_packages(self) -> None:
        entries = [(entry[2], entry[3], entry[4]) for entry in self.rows] + [
            (self.default, self.default_package, self.default_service)]
        uses = [(str(c.currentData() or ""), str(p.currentData() or ""), str(s.currentData() or ""))
                for c, p, s in entries]
        if self.ctx is None:
            return
        from ..packages_dialog import saved_uses

        # Las reglas en pantalla y las guardadas: si se cancela esta ventana, las guardadas siguen valiendo.
        if not open_packages(self, self.ctx, uses + saved_uses(self.ctx.cfg.data.shopify)):
            return
        combos = [entry[3] for entry in self.rows] + [self.default_package]
        for combo in combos:  # la lista cambió: se rehace sin perder lo elegido
            fill_packages(combo, self.settings, str(combo.currentData() or ""), sizes=False)
            combo.currentIndexChanged.emit(combo.currentIndex())

    def problem(self) -> str:
        """Una regla con un empaque oficial de USPS y otra paquetería: no se puede comprar así."""
        if self.settings is None:
            return ""
        entries = [(entry[2], entry[3]) for entry in self.rows] + [(self.default, self.default_package)]
        for carrier, package in entries:
            key = str(package.currentData() or "")
            if pk.official(self.settings, key) and carrier.currentData() != "USPS":
                return tr("{0} es un empaque oficial de USPS: esa regla tiene que salir por USPS.",
                          pk.name(self.settings, key))
        return ""

    def accept(self) -> None:
        from ..flow_setup_dialog import fedex_rule

        problem = self.problem()
        if problem:
            error(self, tr("Reglas de paquetería"), problem)
            return
        if self.fedex_flow.currentData() and fedex_rule(self.rules()) is None:
            error(self, tr("Reglas de paquetería"), tr(
                "Para que Shopify Flow compre la FedEx hace falta una regla de FedEx con un valor (p. ej. piezas de más "
                "de US$1000) o un método de envío. Sin ella, el flujo compraría FedEx para todos los pedidos."))
            return
        super().accept()

    def _fedex_mode(self) -> None:
        """Con Shopify Flow no hay firma ni seguro (Flow no los ofrece): solo cuenta el servicio."""
        flow = bool(self.fedex_flow.currentData())
        self.fedex_signature.setEnabled(not flow)
        self.fedex_insure.setEnabled(not flow)
        self.flow_help.setVisible(flow)
        self.prefs_title.setText(tr("El servicio que compra Shopify Flow") if flow
                                 else tr("La ventana «Guía FedEx» te dice que elijas"))

    def show_flow_setup(self) -> None:
        from ..flow_setup_dialog import FlowSetupDialog, fedex_rule

        run_dialog(FlowSetupDialog(self, self.shop, fedex_rule(self.rules()),
                                   self.fedex_service.currentData() or FEDEX_SERVICES[0]))

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
        package = QComboBox()
        service = QComboBox()
        if self.settings is not None:
            package.setMinimumWidth(250)
            fill_packages(package, self.settings, rule.package, sizes=False)
            self._wire(carrier, package, service, rule.service)
        else:  # sin ajustes (no pasa en la app): se conservan tal cual
            package.addItem(rule.package, rule.package)
            service.addItem(rule.service, rule.service)
            package.setVisible(False)
            service.setVisible(False)
        remove = button("", "trash", "ghost", tr("Quitar esta regla"))
        entry = (over, method, carrier, package, service, mode, remove)

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
        for over, method, carrier, package, service, mode, _remove in self.rows:
            rule = ShippingRule(method.text().strip(), carrier.currentData(), bool(mode.currentData()),
                                round(over.value(), 2), str(package.currentData() or ""),
                                str(service.currentData() or ""))
            if rule.method or rule.over > 0:
                out.append(rule)
        return out


class OrdersPage(Page):
    attention_changed = Signal(int)  # pedidos que necesitan acción (para el aviso en la barra lateral)
    rows_loaded = Signal(list)  # los pedidos recién traídos de Shopify (Inicio arma con ellos el resumen del día)

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
        # Lo que se usa poco, en un menú: reglas de paquetería y exportar.
        self.tools_btn = button("", "more", None, tr("Reglas de paquetería y exportar"))
        tools = QMenu(self)
        tools.addAction(icons.icon("sliders"), tr("Reglas de paquetería…"), self.edit_rules)
        tools.addAction(icons.icon("package"), tr("Empaques…"), lambda: open_packages(self, self.ctx))
        tools.addAction(icons.icon("download"), tr("Exportar a Excel (CSV)…"), self.export_csv)
        self.tools_btn.setMenu(tools)
        self.header_actions.addWidget(self.tools_btn)

        self.notice = label("", "Warning", wrap=True)
        self.notice.setVisible(False)
        self.notice.setOpenExternalLinks(True)
        self.add(self.notice)

        # Los números, en una sola franja.
        strip = Card()
        stats = FlowRow(min_column=150)
        self.stat_action = self._stat(stats, tr("Necesitan acción"))
        self.stat_delays = self._stat(stats, tr("Retrasos"))
        self.stat_orders = self._stat(stats, tr("Pedidos"))
        self.stat_labels = self._stat(stats, tr("Guías compradas"))
        self.stat_carriers = self._stat(stats, tr("Promedio por guía"))
        strip.add(stats)
        self.add(strip)

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
        # Filtros a la izquierda y búsqueda a la derecha; en una ventana angosta, la búsqueda pasa debajo.
        filters = SplitRow(spacing=6)
        # Filtro principal: procesado = «Preparado» en Shopify (por defecto, todos, como siempre).
        self.status = "all"
        self.status_group = QButtonGroup(self)
        self.status_group.setExclusive(True)
        self.status_tabs: dict[str, QPushButton] = {}
        for key, text in STATUS_FILTERS:
            tab = button(tr(text), None, "segment")
            tab.setCheckable(True)
            tab.clicked.connect(lambda _=False, k=key: self._set_status(k))
            self.status_group.addButton(tab)
            self.status_tabs[key] = tab
            filters.add_start(tab)
        self.status_tabs["all"].setChecked(True)
        filters.add_end(label(tr("Paquetería"), "Muted"))
        self.carrier_filter = QComboBox()
        for key, text in CARRIER_FILTERS:
            self.carrier_filter.addItem(tr(text), key)
        self.carrier_filter.currentIndexChanged.connect(
            lambda _i: self._set_group(self.carrier_filter.currentData() or "all"))
        filters.add_end(self.carrier_filter)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Buscar pedido, cliente o seguimiento…"))
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(240)
        self.search.textChanged.connect(lambda _t: self._fill_table())
        filters.add_end(self.search, 1)
        table_card.add(filters)

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
        self.view.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)  # varios: «Imprimir N guías»
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
        self.view.selectionModel().selectionChanged.connect(lambda *_a: self._selection_changed())
        table_card.add(self.view, stretch=1)
        bottom = QHBoxLayout()
        self.print_btn = button(tr("Imprimir guía"), "printer", "primary",
                                tr("Reimprime la guía o la compra e imprime (elige varios pedidos para hacerlo de una vez)"))
        self.print_btn.clicked.connect(self.print_selected)
        bottom.addWidget(self.print_btn)
        self.details_btn = button(tr("Ver pedido"), "eye")
        self.details_btn.clicked.connect(lambda: self._selected_action("details"))
        bottom.addWidget(self.details_btn)
        self.more_btn = button(tr("Más"), "more")
        self.more_menu = QMenu(self)
        self.more_menu.aboutToShow.connect(self._fill_more)
        self.more_btn.setMenu(self.more_menu)
        bottom.addWidget(self.more_btn)
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
    def _stat(self, row: FlowRow, title: str):
        """Un número de la franja de arriba: título, valor y detalle (separados por líneas finas)."""
        block = QWidget()
        column = QVBoxLayout(block)
        column.setSpacing(2)
        column.setContentsMargins(14, 2, 14, 2)
        column.addWidget(label(title, "StatLabel"))
        value = label("—", "StripValue")
        column.addWidget(value)
        detail = label("", "Small", wrap=True)
        column.addWidget(detail)
        column.addStretch(1)
        row.add_block(block)
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
        self.rows_loaded.emit(rows)
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
        for key, text in STATUS_FILTERS:
            count = sum(1 for row in rows if in_status(row, key))
            self.status_tabs[key].setText(f"{tr(text)}  {count}")
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
        value.setStyleSheet(f"color: {Color.CRITICAL};" if summary.alerts else "")
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
        if not rows:  # todo en orden: una línea tranquila, sin caja
            ok = QWidget()
            line = QHBoxLayout(ok)
            line.setContentsMargins(0, 2, 0, 2)
            line.setSpacing(8)
            mark = label()
            mark.setPixmap(icons.pixmap("check", Color.SUCCESS, 16))
            line.addWidget(mark)
            line.addWidget(label(tr("Nada pendiente: las guías se compran e imprimen solas."), "Muted", wrap=True), 1)
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

    def _set_status(self, key: str) -> None:
        self.status = key
        self.status_tabs[key].setChecked(True)
        self._fill_table()

    def _fill_table(self) -> None:
        text = self.search.text().strip().lower()
        visible = []
        for row in self.all_rows:
            if not in_status(row, self.status) or not in_group(row, self.group):
                continue
            if text:
                haystack = " ".join([row.name, row.customer, row.destination, row.method, row.carrier, row.status]
                                    + [number for _c, number, _u in row.tracking]).lower()
                if text not in haystack:
                    continue
            visible.append(row)
        self.model.set_rows(visible)
        self.count.setText(tr("{0} pedido(s)", len(visible)))
        self._selection_changed()

    # --- acciones ------------------------------------------------------------------------------------
    def _selected_rows(self) -> list[OrderRow]:
        rows = []
        for index in sorted(self.view.selectionModel().selectedRows(), key=lambda i: i.row()):
            source = self.proxy.mapToSource(index)
            if 0 <= source.row() < len(self.model.rows):
                rows.append(self.model.rows[source.row()])
        return rows

    def _selected(self) -> OrderRow | None:
        rows = self._selected_rows()
        return rows[0] if len(rows) == 1 else None

    def _selection_changed(self) -> None:
        rows = self._selected_rows()
        count = len(rows)
        reprint = count == 1 and bool(rows[0].labels)
        self.print_btn.setText(tr("Imprimir {0} guías", count) if count > 1
                               else tr("Reimprimir guía") if reprint else tr("Imprimir guía"))
        self.print_btn.setToolTip(tr("Es la misma guía: no se compra ni se paga otra.") if reprint else "")
        self.print_btn.setEnabled(count > 0)
        self.details_btn.setEnabled(count == 1)
        self.more_btn.setEnabled(count == 1)

    def _selected_action(self, action: str) -> None:
        row = self._selected()
        if row is None:
            info(self, tr("Pedidos"), tr("Elige un pedido de la tabla."))
            return
        self.run_action(row, action)

    def _fill_more(self) -> None:
        """«Más ▾»: lo que se puede hacer con el pedido elegido, además de imprimir y ver."""
        self.more_menu.clear()
        row = self._selected()
        if row is None:
            return
        self.more_menu.addAction(icons.icon("link"), tr("Abrir en Shopify"), lambda: self.run_action(row, "open"))
        track = self.more_menu.addAction(icons.icon("truck"), tr("Rastrear el envío"), lambda: self.run_action(row, "track"))
        copy = self.more_menu.addAction(icons.icon("copy"), tr("Copiar el número de seguimiento"),
                                        lambda: self.run_action(row, "copy"))
        track.setEnabled(bool(row.tracking))
        copy.setEnabled(bool(row.tracking))
        self.more_menu.addSeparator()
        self._add_state_action(self.more_menu, row)

    def _add_state_action(self, menu: QMenu, row: OrderRow) -> None:
        """Cambiar el estado de preparación en Shopify (procesado / pendiente)."""
        if row.fulfilled:
            menu.addAction(icons.icon("retry"), tr("Cancelar preparación en Shopify"), lambda: self.run_action(row, "unfulfill"))
        else:
            menu.addAction(icons.icon("check"), tr("Marcar preparado en Shopify"), lambda: self.run_action(row, "fulfill"))

    def _menu(self, pos: QPoint) -> None:
        rows = self._selected_rows()
        if rows:
            self.order_menu(rows).exec(self.view.viewport().mapToGlobal(pos))

    def order_menu(self, rows: list[OrderRow]) -> QMenu:
        """El menú del clic derecho de uno o varios pedidos (tabla, «Necesitan acción» y «Retrasos»)."""
        menu = QMenu(self)
        if len(rows) > 1:
            menu.addAction(icons.icon("printer"), tr("Imprimir {0} guías", len(rows)), self.print_selected)
            pending = [row for row in rows if not row.fulfilled]
            if pending:
                menu.addAction(icons.icon("check"), tr("Marcar {0} preparados en Shopify", len(pending)),
                               lambda: self._fulfill(pending))
            return menu
        row = rows[0]
        plan, why = plan_for(row)
        if plan == REPRINT:  # la misma guía: nunca se compra otra
            menu.addAction(icons.icon("printer"), tr("Reimprimir guía (sin pagar otra)"), lambda: self.run_action(row, "print"))
        elif plan == IN_SHOPIFY:
            menu.addAction(icons.icon("truck"), tr("Comprar guía {0}", row.carrier or row.rule_carrier or "FedEx"),
                           lambda: self.run_action(row, "buy"))
            menu.addAction(icons.icon("printer"), tr("Otra paquetería…"), lambda: self.run_action(row, "other"))
        else:
            printing = menu.addAction(icons.icon("printer"), tr("Imprimir guía"), lambda: self.run_action(row, "print"))
            if plan == SKIP:  # se ve por qué no lleva guía, sin poder comprarla
                printing.setEnabled(False)
                printing.setToolTip(why)
                menu.setToolTipsVisible(True)
        menu.addAction(icons.icon("eye"), tr("Ver pedido"), lambda: self.run_action(row, "details"))
        menu.addAction(icons.icon("file"), tr("Imprimir página del pedido"), lambda: self.run_action(row, "page"))
        menu.addAction(icons.icon("link"), tr("Abrir en Shopify"), lambda: self.run_action(row, "open"))
        if row.tracking:
            menu.addAction(icons.icon("truck"), tr("Rastrear el envío"), lambda: self.run_action(row, "track"))
            menu.addAction(icons.icon("copy"), tr("Copiar el número de seguimiento"), lambda: self.run_action(row, "copy"))
        menu.addSeparator()
        self._add_state_action(menu, row)
        return menu

    def print_selected(self) -> None:
        """«Imprimir guía» con lo elegido en la tabla: uno, o varios con una sola confirmación."""
        rows = self._selected_rows()
        if not rows:
            info(self, tr("Imprimir guía"), tr("Elige uno o varios pedidos de la tabla."))
            return
        if len(rows) == 1:
            self.run_action(rows[0], "print")
            return
        from ..bulk_print_dialog import BulkPrintDialog

        dialog = BulkPrintDialog(self, self.ctx, rows)
        run_dialog(dialog)
        if dialog.finished_any:
            self.reload()

    def run_action(self, row: OrderRow, action: str) -> None:
        shop = self.ctx.cfg.data.shopify.shop
        if action == "print":
            self._print(row)
        elif action == "details":
            self._details(row)
        elif action == "buy":  # se compra en Shopify (FedEx): la ventana «Guía FedEx» guía y la imprime sola
            self._fedex(row)
        elif action == "other":  # otra paquetería en vez de la de la regla
            self._retry(row, prefer=AUTO)
        elif action == "fulfill":
            self._fulfill([row])
        elif action == "unfulfill":
            self._unfulfill(row)
        elif action in ("open", "void"):
            QDesktopServices.openUrl(QUrl(admin_url(shop, f"orders/{row.legacy}")))
        elif action == "track":  # en una ventana de LabelBridge, sin ir a la web de la paquetería
            if row.tracking:
                from ..tracking_dialog import open_tracking

                open_tracking(self, self.ctx, row)
            else:
                info(self, tr("Rastrear"), tr("Este pedido todavía no tiene número de seguimiento."))
        elif action == "copy":
            if row.tracking:
                copy_to_clipboard(row.tracking[0][1])
        elif action == "reprint":
            self._reprint(row)
        elif action == "retry":
            self._retry(row)
        elif action == "page":
            self._print_page(row)

    def _print_page(self, row: OrderRow) -> None:
        """La página del pedido (como «Imprimir página de pedido» de Shopify), por su impresora."""
        shopify = self.ctx.services.shopify

        def failed(message: str) -> None:
            error(self, tr("Imprimir página del pedido"), message)

        run_async(lambda: shopify.print_order_page(row.legacy, row.name),
                  lambda _queued: info(self, tr("Imprimir página del pedido"),
                                       tr("La página de {0} se está imprimiendo.", row.name)), failed,
                  what=tr("Imprimir página del pedido"))

    def _print(self, row: OrderRow) -> None:
        """«Imprimir guía» de un pedido: reimprime la que tiene, abre la compra o explica por qué no lleva guía."""
        plan, why = plan_for(row)
        if plan == REPRINT:
            self._reprint(row)
        elif plan == IN_SHOPIFY:
            self._fedex(row)
        elif plan == BUY:
            self._retry(row)
        else:
            info(self, tr("Imprimir guía"), tr("{0}: {1}", row.name, why))

    def _fulfill(self, rows: list[OrderRow]) -> None:
        """«Marcar preparado en Shopify» (se envió de otra forma): pasa a Procesados y ya no espera guía."""
        names = ", ".join(row.name for row in rows[:5]) + ("…" if len(rows) > 5 else "")
        if not confirm(self, tr("Marcar preparado en Shopify"), tr(
                "¿Marcar {0} como preparado en Shopify, sin comprar guía?\n\nPasa a «Procesados» y LabelBridge ya no le "
                "compra guía. El cliente no recibe aviso.", names)):
            return
        shopify = self.ctx.services.shopify

        def work() -> list[str]:
            problems = []
            for row in rows:
                try:
                    shopify.mark_fulfilled(row.legacy, row.name)
                except Exception as exc:  # noqa: BLE001  cada pedido por su lado: se informa al final
                    log.warning("No se pudo marcar %s como preparado: %s", row.name, exc, exc_info=errors.is_bug(exc))
                    problems.append(f"{row.name}: {errors.user_message(exc)}")
            return problems

        def done(problems: list[str]) -> None:
            if problems:
                error(self, tr("Marcar preparado en Shopify"), "\n".join(problems))
            self.reload()

        run_async(work, done, lambda message: error(self, tr("Marcar preparado en Shopify"), message))

    def _unfulfill(self, row: OrderRow) -> None:
        """«Cancelar preparación en Shopify»: vuelve a Pendientes (con guía activa, se anula en Shopify)."""
        if row.labels:
            info(self, tr("Cancelar preparación en Shopify"), tr(
                "{0} tiene una guía activa: anúlala en Shopify («Anular etiqueta»). Así se cancela la preparación y "
                "Shopify te devuelve el dinero de la guía.", row.name))
            QDesktopServices.openUrl(QUrl(admin_url(self.ctx.cfg.data.shopify.shop, f"orders/{row.legacy}")))
            return
        auto = self.ctx.cfg.data.shopify.buy_shipping_label
        extra = tr(" Como la compra automática está activa, LabelBridge le comprará la guía.") if auto else ""
        if not confirm(self, tr("Cancelar preparación en Shopify"), tr(
                "¿Cancelar la preparación de {0} en Shopify?\n\nVuelve a «Pendientes».{1}", row.name, extra)):
            return
        run_async(lambda: self.ctx.services.shopify.cancel_fulfillment(row.legacy, row.name),
                  lambda _count: self.reload(),
                  lambda message: error(self, tr("Cancelar preparación en Shopify"), message))

    def _reprint(self, row: OrderRow) -> None:
        run_async(
            lambda: self.ctx.services.shopify.reprint_label(row.legacy, row.name),
            lambda _queued: info(self, tr("Reimprimir guía"), tr("La guía de {0} está en la cola de impresión. Es la misma "
                                                                "guía: no se compró ni se cobró otra.", row.name)),
            lambda message: error(self, tr("Reimprimir guía"), message),
        )

    def _details(self, row: OrderRow) -> None:
        """«Ver pedido»: qué tiene el pedido, sin salir de LabelBridge."""
        from ..order_dialog import OrderDialog

        run_dialog(OrderDialog(self, self.ctx, row, self.run_action))

    def new_manual_label(self) -> None:
        """«Nueva guía manual»: pedido manual en Shopify y, después, «Imprimir guía» para ese pedido."""
        from ..manual_label_dialog import run_manual_label

        if not self.ctx.cfg.data.shopify.shop.strip():
            info(self, tr("Nueva guía manual"), tr("Primero conecta tu tienda en la pantalla Shopify."))
            return
        if run_manual_label(self, self.ctx):
            self.reload()

    def _retry(self, row: OrderRow, prefer: str | None = None) -> None:
        """La compra de la guía: servicio, caja y aviso, un clic, y se compra, se imprime y Shopify queda al día."""
        from ..purchase_dialog import PurchaseDialog

        dialog = PurchaseDialog(self, self.ctx, row, prefer=prefer)
        run_dialog(dialog)
        if dialog.fedex_requested:
            self._fedex(row)
        elif dialog.bought:
            self.reload()

    def _fedex(self, row: OrderRow) -> None:
        """«Guía FedEx»: abre el pedido en Shopify, dice qué elegir (servicio, firma, seguro) y la imprime sola."""
        from ..fedex_dialog import open_fedex

        dialog = open_fedex(self, self.ctx, row, on_other=lambda r: self._retry(r, prefer=AUTO))
        dialog.finished.connect(lambda _r: self._reload_timer.start())

    def edit_rules(self) -> None:
        s = self.ctx.cfg.data.shopify
        dialog = ShippingRulesDialog(self, s.shipping_rules, s.default_carrier,
                                     (s.fedex_service, s.fedex_signature, s.fedex_insure),
                                     by_flow=s.fedex_by_flow, shop=s.shop, ctx=self.ctx)
        if run_dialog(dialog) != QDialog.DialogCode.Accepted:
            return
        was_flow = s.fedex_by_flow
        s.shipping_rules = dialog.rules()
        s.default_carrier = dialog.default.currentData() or "USPS"
        s.default_package = str(dialog.default_package.currentData() or "")
        s.default_service = str(dialog.default_service.currentData() or "")
        s.fedex_service = dialog.fedex_service.currentData() or s.fedex_service
        s.fedex_signature = dialog.fedex_signature.currentData() or s.fedex_signature
        s.fedex_insure = dialog.fedex_insure.isChecked()
        s.fedex_by_flow = bool(dialog.fedex_flow.currentData())
        self.ctx.cfg.save("shopify")
        self.reload()  # se vuelve a clasificar con las reglas nuevas
        if s.fedex_by_flow and not was_flow:  # recién elegido: cómo armar el flujo en Shopify
            from ..flow_setup_dialog import FlowSetupDialog, fedex_rule

            run_dialog(FlowSetupDialog(self, s.shop, fedex_rule(s.shipping_rules), s.fedex_service))

    def export_csv(self) -> None:
        if not self.all_rows:
            info(self, tr("Exportar"), tr("No hay pedidos para exportar en este período."))
            return
        default = str(Path.home() / tr("pedidos-{0}.csv", f"{datetime.now():%Y-%m-%d}"))
        path, _ = QFileDialog.getSaveFileName(self, tr("Exportar pedidos"), default, "CSV (*.csv)")
        if not path:
            return
        rows = sorted(self.all_rows, key=lambda row: row.created, reverse=True)
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as handle:  # con BOM: Excel lo abre bien
                writer = csv.writer(handle)
                writer.writerow([tr(column) for column in CSV_COLUMNS])
                writer.writerows(csv_rows(rows))
        except OSError as exc:  # sin permiso, disco lleno, el archivo abierto en Excel…
            log.warning("No se pudo exportar a %s: %s", path, exc)
            error(self, tr("Exportar"), tr("No se pudo guardar el archivo: {0}. Si está abierto en Excel, ciérralo y "
                                           "vuelve a intentarlo.", exc.strerror or exc))
            return
        info(self, tr("Exportar"), tr("Se guardaron {0} pedidos en {1}.", len(rows), path))
