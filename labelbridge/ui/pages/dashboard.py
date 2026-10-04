"""Inicio: estado general, contadores y últimos trabajos."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...models import SOURCE_LABELS, status_text
from .. import icons
from ..actions import enqueue_test_label
from .guide import guide_banner, update_guide_banner
from ..context import AppContext
from ..theme import job_state
from ..widgets import Card, Page, Pill, ToggleSwitch, button, label


class DashboardPage(Page):
    def __init__(self, ctx: AppContext, navigate: Callable[[str], None]) -> None:
        super().__init__("Inicio", "Todo lo que llega de Shopify, n8n o la API se imprime solo en este equipo.")
        self.ctx = ctx
        self.navigate = navigate

        test = button("Imprimir prueba", "printer", tooltip="Etiqueta de ejemplo en la impresora predeterminada")
        test.clicked.connect(self._print_test)
        self.header_actions.addWidget(test)

        self.guide_banner = guide_banner(ctx, navigate)
        self.add(self.guide_banner)

        # Interruptor general
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

        # Contadores
        stats = QHBoxLayout()
        stats.setSpacing(16)
        self.stat_done = self._stat(stats, "IMPRESAS HOY", "check")
        self.stat_queue = self._stat(stats, "EN COLA", "clock")
        self.stat_failed = self._stat(stats, "CON ERROR HOY", "alert")
        self.add(layout=stats)

        # Conexiones
        grid = QGridLayout()
        grid.setSpacing(16)
        self.conn: dict[str, tuple[Pill, Any]] = {}
        specs = [
            ("printers", "Impresoras", "printer", "printers"),
            ("shopify", "Shopify", "shop", "shopify"),
            ("mqtt", "n8n · tiempo real", "flow", "n8n"),
            ("local_api", "API local", "server", "api"),
        ]
        for index, (key, title, icon_name, page) in enumerate(specs):
            card = Card()
            head = QHBoxLayout()
            ico = label()
            ico.setPixmap(icons.pixmap(icon_name, "#4F46E5", 20))
            head.addWidget(ico)
            head.addWidget(label(title, "CardTitle"))
            head.addStretch(1)
            pill = Pill("off", "—")
            head.addWidget(pill)
            card.add(layout=head)
            message = label("", "Muted", wrap=True)
            card.add(message)
            go = button("Configurar", None, "ghost")
            go.setStyleSheet("color: #4F46E5; font-weight: 600; text-align: left;")
            go.clicked.connect(lambda _=False, p=page: self.navigate(p))
            card.add(go)
            grid.addWidget(card, index // 2, index % 2)
            self.conn[key] = (pill, message)
        self.add(layout=grid)

        # Últimos trabajos
        recent = Card("Últimos trabajos")
        see_all = button("Ver historial", None, "ghost")
        see_all.setStyleSheet("color: #4F46E5; font-weight: 600;")
        see_all.clicked.connect(lambda: self.navigate("history"))
        recent.header.addWidget(see_all)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Hora", "Trabajo", "Origen", "Impresora", "Estado"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setShowGrid(False)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(4, 130)
        self.table.setMinimumHeight(260)
        recent.add(self.table)
        self.empty = label("Todavía no hay trabajos. Prueba con «Imprimir prueba».", "Muted")
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

    def _stat(self, row: QHBoxLayout, title: str, icon_name: str):
        card = Card()
        head = QHBoxLayout()
        head.addWidget(label(title, "StatLabel"))
        head.addStretch(1)
        ico = label()
        ico.setPixmap(icons.pixmap(icon_name, "#94A3B8", 18))
        head.addWidget(ico)
        card.add(layout=head)
        value = label("0", "StatValue")
        card.add(value)
        row.addWidget(card, 1)
        return value

    def refresh(self) -> None:
        ctx = self.ctx
        update_guide_banner(self.guide_banner, ctx)
        paused = ctx.cfg.data.general.paused
        self.auto_switch.blockSignals(True)
        self.auto_switch.setChecked(not paused)
        self.auto_switch.set_offset(0.0 if paused else 1.0)
        self.auto_switch.blockSignals(False)
        if paused:
            self.banner_icon.setPixmap(icons.pixmap("pause", "#B45309", 28))
            self.banner_title.setText("Impresión automática en pausa")
            self.banner_desc.setText("Los trabajos se siguen recibiendo y quedan en cola hasta que la reanudes.")
        else:
            self.banner_icon.setPixmap(icons.pixmap("zap", "#4F46E5", 28))
            self.banner_title.setText("Impresión automática activa")
            self.banner_desc.setText(
                f"Equipo «{ctx.cfg.data.general.agent_name}». Las etiquetas se imprimen apenas llegan."
            )

        counts = ctx.store.counts()
        self.stat_done.setText(str(counts["done_today"]))
        self.stat_queue.setText(str(counts["queued"]))
        self.stat_failed.setText(str(counts["failed_today"]))

        printers = [p for p in ctx.cfg.data.printers if p.enabled]
        default = ctx.cfg.default_printer()
        pill, message = self.conn["printers"]
        if printers:
            pill.set_state("ok", f"{len(printers)} lista(s)")
            message.setText(f"Predeterminada: {default.display_name() if default else '—'}")
        else:
            pill.set_state("warn", "Sin configurar")
            message.setText("Agrega tu impresora de etiquetas para empezar.")
        for key, service in (("shopify", ctx.services.shopify), ("mqtt", ctx.services.mqtt), ("local_api", ctx.services.local_api)):
            pill, message = self.conn[key]
            texts = {"ok": "Conectado", "connecting": "Conectando", "error": "Error", "warn": "Aviso", "off": "Desactivado"}
            if key == "local_api" and service.state == "ok":
                texts["ok"] = "Activa"
            pill.set_state(service.state, texts.get(service.state, service.state))
            message.setText(service.message)

        jobs = ctx.store.list(limit=8)
        self.table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            when = datetime.fromtimestamp(job["created_at"]).strftime("%H:%M:%S")
            values = [
                when,
                job["title"],
                SOURCE_LABELS.get(job["source"], job["source"]),
                job["printer_used"] or job["printer"] or "Predeterminada",
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))
            pill = Pill(job_state(job), status_text(job))
            if job["error"]:
                pill.setToolTip(job["error"])
            cell = QWidget()
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(6, 0, 6, 0)
            cell_layout.addWidget(pill)
            cell_layout.addStretch(1)
            self.table.setCellWidget(row, 4, cell)
            self.table.setRowHeight(row, 36)
        self.table.setVisible(bool(jobs))
        self.empty.setVisible(not jobs)

    def _print_test(self) -> None:
        enqueue_test_label(self.ctx)
