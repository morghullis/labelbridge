"""Reporte: cómo va la tienda cada semana. Los números contra la semana anterior y el promedio de 4 semanas, las ventas
por día, lo que hay que arreglar (con pasos), lo que va bien, el plan de la semana, la comparación con referencias del
sector (con su fuente; son estimaciones) y los productos y canales.

El cálculo está en connectors/store_report.py y la consulta en ShopifyService.weekly_report: aquí solo se muestra. El
reporte de la semana que terminó se prepara solo el lunes (y queda guardado); «Actualizar» lo vuelve a calcular.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from functools import partial
from html import escape
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...connectors.store_report import Comparison, Finding, Metric, WeeklyReport, money_text, pct
from ...i18n import N_, date_text, decimal, tr
from ..charts import DailyBars
from ..context import AppContext
from ..theme import Color
from ..widgets import Card, FlowRow, Page, Pill, button, label, link_button, run_async

WEEKS = ((1, N_("Semana pasada")), (0, N_("Esta semana (hasta hoy)")), (2, N_("Hace 2 semanas")),
         (3, N_("Hace 3 semanas")), (4, N_("Hace 4 semanas")))
DAYS = (N_("Lun"), N_("Mar"), N_("Mié"), N_("Jue"), N_("Vie"), N_("Sáb"), N_("Dom"))
KPIS = ("sales", "orders", "aov", "returning", "on_time")
SEVERITY = {"critical": N_("Importante"), "watch": N_("Para revisar"), "strength": N_("Va bien")}
STALE_AFTER = 3600  # al volver a la pestaña, lo mostrado hace más de 1 h se vuelve a leer


def day_text(day: date, year: bool = True) -> str:
    return date_text(datetime(day.year, day.month, day.day), year=year, clock="")


def metric_text(metric: Metric, value: float | None = None, currency: str = "USD") -> str:
    value = metric.value if value is None else value
    if value is None:
        return "—"
    if metric.unit == "money":
        return money_text(value, currency)
    if metric.unit == "percent":
        return pct(value)
    if metric.unit == "hours":
        return tr("{0} h", decimal(value, 0))
    return f"{value:,.0f}"


def delta_text(metric: Metric) -> tuple[str, str]:
    """El cambio contra la semana anterior y su color: verde si mejoró, rojo si empeoró (con flecha: nunca solo color)."""
    change = metric.change
    if metric.previous == 0 and metric.unit != "percent" and metric.value is not None:
        if not metric.value:
            return tr("Igual que la semana anterior"), Color.TEXT_SECONDARY
        # Desde 0 no hay porcentaje, pero sí un cambio que mostrar (no «sin datos»).
        up = metric.value > 0
        good = up == (metric.better == "up")
        return tr("{0} desde 0 la semana anterior", "▲" if up else "▼"), Color.SUCCESS if good else Color.CRITICAL
    if change is None:
        return tr("Sin datos de la semana anterior"), Color.TEXT_SECONDARY
    if abs(change) < 0.05:
        return tr("Igual que la semana anterior"), Color.TEXT_SECONDARY
    arrow = "▲" if change > 0 else "▼"
    amount = tr("{0} pts", decimal(abs(change), 1)) if metric.unit == "percent" else pct(abs(change))
    good = (change > 0) == (metric.better == "up")
    return tr("{0} {1} vs. semana anterior", arrow, amount), Color.SUCCESS if good else Color.CRITICAL


def _plain(widget: QLabel) -> QLabel:
    """Texto que puede traer nombres de la tienda: siempre como texto, nunca como HTML."""
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


class FindingRow(QFrame):
    """Un punto del reporte: la línea de color dice qué tan importante es (y la etiqueta, por si el color no se ve)."""

    def __init__(self, finding: Finding) -> None:
        super().__init__()
        self.setObjectName("FindingRow")
        self.setProperty("severity", finding.severity)
        column = QVBoxLayout(self)
        column.setContentsMargins(14, 6, 0, 6)
        column.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(_plain(label(finding.title, "ItemTitle", wrap=True)), 1)
        head.addWidget(label(tr(SEVERITY.get(finding.severity, "")), "Small"))
        column.addLayout(head)
        column.addWidget(_plain(label(finding.evidence, "Muted", wrap=True)))
        for number, action in enumerate(finding.actions, start=1):
            column.addWidget(_plain(label(f"{number}. {action}", wrap=True)))


class ComparisonRow(QWidget):
    """Tu tienda contra una referencia del sector, con la fuente (enlace) y qué mide exactamente."""

    def __init__(self, item: Comparison, currency_text: Callable[[float | None], str]) -> None:
        super().__init__()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 4, 0, 4)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(2)
        value = currency_text if item.unit == "money" else (lambda v: pct(v, 2))  # la cifra exacta de la fuente
        grid.addWidget(label(item.title, "ItemTitle", wrap=True), 0, 0)
        grid.addWidget(label(tr("Tu tienda: {0}", value(item.store))), 0, 1)
        grid.addWidget(label(tr("Referencia: {0}", value(item.reference)), "Muted"), 0, 2)
        state, text = _verdict(item)
        grid.addWidget(Pill(state, text), 0, 3)
        source = QLabel(tr("{0} · <a href=\"{1}\">{2}</a> (consultado el {3}) · estimación", escape(item.detail),
                           escape(item.url, quote=True), escape(item.source), item.checked))
        source.setObjectName("Small")
        source.setWordWrap(True)
        source.setTextFormat(Qt.TextFormat.RichText)
        source.setOpenExternalLinks(True)
        grid.addWidget(source, 1, 0, 1, 4)
        if item.note:
            grid.addWidget(label(item.note, "Small", wrap=True), 2, 0, 1, 4)
        grid.setColumnStretch(0, 1)


def _verdict(item: Comparison) -> tuple[str, str]:
    verdict = item.verdict
    if verdict == "unknown":
        return "off", tr("Sin dato")
    if verdict == "few":
        return "off", tr("Pocos pedidos")
    if verdict == "near":
        return "info", tr("Parecido")
    good = (verdict == "above") == (item.better == "up")
    if item.better == "up":
        return ("ok", tr("Por encima")) if good else ("warn", tr("Por debajo"))
    return ("ok", tr("Mejor")) if good else ("warn", tr("Más alto"))


class ReportPage(Page):
    def __init__(self, ctx: AppContext, navigate: Callable[[str], None] | None = None) -> None:
        super().__init__(
            tr("Reporte"),
            tr("Cómo va la tienda cada semana: qué arreglar, qué va bien y cómo te comparas con el sector."),
        )
        self.ctx = ctx
        self.navigate = navigate
        self.loading = False
        self.report: WeeklyReport | None = None
        self._loaded_at = 0.0  # cuándo se mostró (reloj monotónico): para leerlo de nuevo si quedó viejo
        self._stale = False  # llegó el reporte del lunes mientras no se veía (o mientras se leía otro)

        self.week = QComboBox()
        for weeks_back, text in WEEKS:
            self.week.addItem(tr(text), weeks_back)
        self.week.currentIndexChanged.connect(lambda _i: self.load())
        self.header_actions.addWidget(self.week)
        self.refresh_btn = button(tr("Actualizar"), "refresh", tooltip=tr("Volver a calcularlo con los pedidos de ahora"))
        self.refresh_btn.clicked.connect(lambda: self.load(refresh=True))
        self.header_actions.addWidget(self.refresh_btn)

        self.status = label("", "Small", wrap=True)
        self.add(self.status)
        self.notice = label("", "Warning", wrap=True)
        self.notice.setVisible(False)
        self.add(self.notice)
        self.connect_btn = button(tr("Conectar Shopify"), "shop", "primary")
        self.connect_btn.setVisible(False)
        if navigate is not None:
            self.connect_btn.clicked.connect(lambda: navigate("shopify"))
        connect_row = QHBoxLayout()
        connect_row.addWidget(self.connect_btn)
        connect_row.addStretch(1)
        self.add(layout=connect_row)

        # Los números de la semana.
        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(18)
        strip = Card()
        self.kpis = FlowRow(min_column=170)
        self.kpi_labels: dict[str, tuple[QLabel, QLabel, QLabel, QLabel]] = {}
        for key in KPIS:
            self.kpi_labels[key] = self._kpi_block()
        strip.add(self.kpis)
        body.addWidget(strip)

        chart_card = Card(tr("Ventas por día"))
        self.chart = DailyBars(lambda value: money_text(value, self._currency(), 0))
        chart_card.add(self.chart)
        self.table = label("", "Small")
        self.table.setTextFormat(Qt.TextFormat.RichText)
        self.table.setVisible(False)
        chart_card.add(self.table)
        self.table_btn = link_button(tr("Ver como tabla"))
        self.table_btn.clicked.connect(self._toggle_table)
        table_row = QHBoxLayout()
        table_row.addWidget(self.table_btn)
        table_row.addStretch(1)
        chart_card.add(layout=table_row)
        body.addWidget(chart_card)

        self.plan_card = Card(tr("Plan para esta semana"))
        self.plan_list = QVBoxLayout()
        self.plan_list.setSpacing(6)
        self.plan_card.add(layout=self.plan_list)
        body.addWidget(self.plan_card)

        self.fix_card = Card(tr("Lo que hay que arreglar"))
        self.fix_list = QVBoxLayout()
        self.fix_list.setSpacing(14)
        self.fix_card.add(layout=self.fix_list)
        body.addWidget(self.fix_card)

        self.good_card = Card(tr("Lo que va bien"))
        self.good_list = QVBoxLayout()
        self.good_list.setSpacing(14)
        self.good_card.add(layout=self.good_list)
        body.addWidget(self.good_card)

        compare = Card(tr("Comparación con el sector"),
                      tr("Promedios públicos de otras tiendas (estimaciones, no datos de tu tienda): sirven de "
                         "orientación, no de meta exacta."))
        self.compare_list = QVBoxLayout()
        self.compare_list.setSpacing(10)
        compare.add(layout=self.compare_list)
        body.addWidget(compare)

        mix = Card(tr("Productos y canales"))
        self.products = label("", wrap=True)
        self.products.setTextFormat(Qt.TextFormat.RichText)
        mix.add(self.products)
        self.channels = _plain(label("", "Muted", wrap=True))
        mix.add(self.channels)
        body.addWidget(mix)

        self.missing = label("", "Hint", wrap=True)
        self.missing.setVisible(False)
        body.addWidget(self.missing)
        self.body.setVisible(False)
        self.add(self.body)
        self.finish()
        ctx.bridge.event.connect(self._on_event)
        self._loaded_once = False

    # --- piezas --------------------------------------------------------------------------------------------
    def _kpi_block(self) -> tuple[QLabel, QLabel, QLabel, QLabel]:
        block = QWidget()
        column = QVBoxLayout(block)
        column.setContentsMargins(14, 2, 14, 2)
        column.setSpacing(2)
        title, value = label("", "StatLabel"), label("—", "StripValue")
        delta, average = label("", "Small", wrap=True), label("", "Small", wrap=True)
        for widget in (title, value, delta, average):
            column.addWidget(widget)
        column.addStretch(1)
        self.kpis.add_block(block)
        return title, value, delta, average

    @staticmethod
    def _clear(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

    def _toggle_table(self) -> None:
        showing = not self.table.isVisible()
        self.table.setVisible(showing)
        self.chart.setVisible(not showing)
        self.table_btn.setText(tr("Ver gráfico") if showing else tr("Ver como tabla"))

    def _currency(self) -> str:
        return self.report.currency if self.report else "USD"

    # --- datos -------------------------------------------------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._loaded_once:
            self._loaded_once = True
            self.load()

    def refresh(self) -> None:
        """Al volver a la pestaña: se lee de nuevo si llegó el reporte del lunes mientras no se veía, si lo mostrado
        tiene más de 1 h o si la última vez falló (p. ej. sin internet)."""
        if not self._loaded_once or self.loading:
            return  # la primera vez lo lee showEvent
        if self._stale or self.report is None or time.monotonic() - self._loaded_at > STALE_AFTER:
            self.load()

    def _on_event(self, kind: str, data) -> None:
        if kind != "report":
            return
        if self.isVisible() and not self.loading and self.week.currentData() == 1:
            self.load()  # el del lunes ya está guardado: se muestra sin volver a calcularlo
        else:
            self._stale = True  # se lee al volver a la pestaña

    def load(self, refresh: bool = False) -> None:
        if self.loading:
            return
        if not self.ctx.cfg.data.shopify.shop.strip():
            self.status.setText(tr("Conecta la tienda para ver su reporte semanal."))
            self.connect_btn.setVisible(True)
            self.body.setVisible(False)
            return
        self.connect_btn.setVisible(False)
        weeks_back = int(self.week.currentData() or 0)
        self.loading = True
        self._stale = False
        self._set_busy(True)
        self.status.setText(tr("Preparando el reporte con los pedidos de Shopify…"))
        self.notice.setVisible(False)
        shopify = self.ctx.services.shopify

        def work() -> WeeklyReport:
            saved = None if refresh or weeks_back == 0 else shopify.saved_report(weeks_back)
            return saved or shopify.weekly_report(weeks_back)

        run_async(work, self._show, self._failed, what=tr("Preparar el reporte"))

    def _set_busy(self, busy: bool) -> None:
        """Mientras se lee, ni otra semana ni «Actualizar»: lo que se ve siempre es de la semana elegida."""
        self.refresh_btn.setEnabled(not busy)
        self.week.setEnabled(not busy)

    def _failed(self, message: str) -> None:
        self.loading = False
        self._set_busy(False)
        self.status.setText("")
        self.notice.setText(tr("No se pudo preparar el reporte: {0}", message))
        self.notice.setVisible(True)

    def _show(self, report: WeeklyReport) -> None:
        self.loading = False
        self._set_busy(False)
        self.report = report
        self._loaded_at = time.monotonic()
        money = partial(money_text, currency=report.currency)
        start, end = date.fromisoformat(report.start), date.fromisoformat(report.end)
        self.status.setText(tr("Del {0} al {1}{2} · preparado el {3}", day_text(start), day_text(end),
                               "" if report.complete else tr(" (hasta hoy)"), date_text(report.generated_at)))
        for key, (title, value, delta, average) in self.kpi_labels.items():
            metric = report.metric(key)
            if metric is None:
                continue
            title.setText(tr(metric.title))
            value.setText(metric_text(metric, currency=report.currency))
            text, color = delta_text(metric)
            delta.setText(text)
            delta.setStyleSheet(f"color: {color};")
            average.setText(tr("Promedio 4 sem.: {0}", metric_text(metric, metric.average, report.currency))
                            if metric.average is not None else "")
            average.setToolTip(tr("Promedio de las 4 semanas anteriores"))
        details = [tr("{0} · {1} pedido(s)", day_text(start + timedelta(days=i), year=False), count)
                   for i, count in enumerate(report.daily_orders)]
        self.chart.set_data([tr(day) for day in DAYS], report.daily_sales, details,
                            tr("Sin ventas en estos días"))
        self.table.setText(self.chart.table_html(tr("Ventas"), tr("Día")))
        self._fill_plan(report)
        self._fill_findings(self.fix_list, report.findings, tr("Nada urgente esta semana."))
        self._fill_findings(self.good_list, report.strengths, tr("Todavía no hay suficientes pedidos para decirlo."))
        self._clear(self.compare_list)
        for item in report.comparisons:
            self.compare_list.addWidget(ComparisonRow(item, money))
        self._fill_mix(report, money)
        self.missing.setText(" ".join(report.missing))
        self.missing.setVisible(bool(report.missing))
        self.body.setVisible(True)

    def _fill_plan(self, report: WeeklyReport) -> None:
        self._clear(self.plan_list)
        for number, step in enumerate(report.plan, start=1):
            self.plan_list.addWidget(_plain(label(f"{number}. {step}", wrap=True)))

    def _fill_findings(self, layout: QVBoxLayout, findings: list[Finding], empty: str) -> None:
        self._clear(layout)
        if not findings:
            layout.addWidget(label(empty, "Muted", wrap=True))
        for finding in findings:
            layout.addWidget(FindingRow(finding))

    def _fill_mix(self, report: WeeklyReport, money: Callable[[float | None], str]) -> None:
        if report.top_products:
            rows = "".join(
                f"<tr><td>{escape(title)}</td><td align='right'>&nbsp;&nbsp;{money(sales)}</td>"
                f"<td align='right'>&nbsp;&nbsp;{units} u.</td><td align='right'>&nbsp;&nbsp;{pct(share)}</td></tr>"
                for title, sales, units, share in report.top_products)
            self.products.setText(f"<b>{escape(tr('Lo más vendido'))}</b><table cellspacing='0' cellpadding='3'>"
                                  f"{rows}</table>")
        else:
            self.products.setText(escape(tr("Sin ventas esta semana.")))
        channels = " · ".join(f"{name}: {count}" for name, count in report.channels)
        regions = " · ".join(f"{name}: {count}" for name, count in report.regions)
        parts = [tr("Canales: {0}", channels) if channels else "", tr("Destinos: {0}", regions) if regions else ""]
        self.channels.setText("\n".join(part for part in parts if part))
