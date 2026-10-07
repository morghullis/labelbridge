"""Historial: todos los trabajos, con reimpresión, reintento, cancelación y vista previa."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QScrollArea,
    QTableView,
    QVBoxLayout,
)

from ...connectors.shopify import admin_url
from ...i18n import N_, date_text, tr
from ...jobstore import PayloadUnavailable
from ...labels.order import normalize_order
from ...labels.qt_label import draw_order_label
from ...models import SOURCE_LABELS, STATUS_LABELS, status_text
from ...printing.detect import detect_format
from ...printing.pages import ImageSource, PdfSource, VectorSource
from .. import icons
from ..context import AppContext
from ..theme import STATE_COLORS, job_state
from ..widgets import (
    Card,
    Page,
    button,
    confirm,
    context_menu,
    copy_to_clipboard,
    error,
    info,
    label,
    run_async,
    run_dialog,
)

COLUMNS = [N_("Fecha"), N_("Trabajo"), N_("Origen"), N_("Impresora"), N_("Formato"), N_("Estado"), N_("Intentos"),
           N_("Detalle")]
FORMAT_LABELS = {"pdf": "PDF", "image": N_("Imagen"), "zpl": "ZPL", "raw": "RAW", "order": N_("Etiqueta pedido"), "auto": "—"}


class JobsModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self.jobs: list[dict[str, Any]] = []

    def set_jobs(self, jobs: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self.jobs = jobs
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return len(self.jobs)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return tr(COLUMNS[section])
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        job = self.jobs[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return date_text(job["created_at"], year=False, clock="%H:%M:%S")
            if col == 1:
                return job["title"]
            if col == 2:
                return tr(SOURCE_LABELS.get(job["source"], job["source"]))
            if col == 3:
                return job["printer_used"] or job["printer"] or tr("Predeterminada")
            if col == 4:
                return tr(FORMAT_LABELS.get(job["format"], job["format"].upper()))
            if col == 5:
                return status_text(job)
            if col == 6:
                return str(job["attempts"])
            if col == 7:
                if job["status"] == "done" and job["printed_at"]:
                    return tr("Impreso {0}", datetime.fromtimestamp(job['printed_at']).strftime('%H:%M:%S'))
                return job["error"]
        if role == Qt.ItemDataRole.ForegroundRole and col == 5:
            return QColor(STATE_COLORS[job_state(job)][1])
        if role == Qt.ItemDataRole.ToolTipRole and col in (1, 7):
            return job["error"] or job["external_id"]
        return None


class HistoryPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(tr("Historial"), tr("Cada etiqueta recibida, impresa o con error. Doble clic para ver la vista previa."))
        self.ctx = ctx
        card = Card()
        filters = QHBoxLayout()
        self.status = QComboBox()
        self.status.addItem(tr("Todos los estados"), None)
        for key, text in STATUS_LABELS.items():
            self.status.addItem(tr(text), key)
        self.status.currentIndexChanged.connect(self.refresh)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Buscar por pedido, impresora o error…"))
        self.search.textChanged.connect(lambda: self._timer.start())
        filters.addWidget(self.status)
        filters.addWidget(self.search, 1)
        card.add(layout=filters)

        self.model = JobsModel()
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.verticalHeader().setVisible(False)
        self.view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.view.setShowGrid(False)
        self.view.setAlternatingRowColors(True)
        self.view.setWordWrap(False)
        self.view.verticalHeader().setDefaultSectionSize(34)
        header = self.view.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(7, 260)
        self.view.setMinimumHeight(460)
        self.view.doubleClicked.connect(lambda _i: self._preview())
        context_menu(self.view, self._menu)
        card.add(self.view, stretch=1)

        actions = QHBoxLayout()
        for text, icon_name, slot, variant in (
            (tr("Vista previa"), "eye", self._preview, None),
            (tr("Reimprimir"), "printer", self._reprint, "primary"),
            (tr("Reintentar"), "retry", self._retry, None),
            (tr("Cancelar"), "x", self._cancel, None),
            (tr("Eliminar"), "trash", self._delete, "danger"),
        ):
            btn = button(text, icon_name, variant)
            btn.clicked.connect(slot)
            actions.addWidget(btn)
        actions.addStretch(1)
        self.count = label("", "Muted")
        actions.addWidget(self.count)
        card.add(layout=actions)
        self.add(card, stretch=1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self.refresh)
        ctx.bridge.event.connect(lambda kind, _d: kind == "job" and self._timer.start())
        self.refresh()

    def refresh(self) -> None:
        selected = {job["id"] for job in self._selected()}
        jobs = self.ctx.store.list(limit=500, status=self.status.currentData(), search=self.search.text().strip())
        self.model.set_jobs(jobs)
        self.count.setText(tr("{0} trabajo(s)", len(jobs)))
        for row, job in enumerate(jobs):
            if job["id"] in selected:
                self.view.selectRow(row)

    def _selected(self) -> list[dict[str, Any]]:
        rows = sorted({i.row() for i in self.view.selectionModel().selectedRows()}) if self.view.selectionModel() else []
        return [self.model.jobs[r] for r in rows if r < len(self.model.jobs)]

    def _require(self) -> list[dict[str, Any]]:
        jobs = self._selected()
        if not jobs:
            info(self, tr("Historial"), tr("Selecciona uno o más trabajos."))
        return jobs

    def _after_change(self) -> None:
        self.ctx.engine.wake()
        self.refresh()

    def _menu(self) -> QMenu | None:
        """Clic derecho: lo que se puede hacer con los trabajos elegidos."""
        jobs = self._selected()
        if not jobs:
            return None
        menu = QMenu(self)
        if len(jobs) == 1:
            menu.addAction(icons.icon("eye"), tr("Vista previa"), self._preview)
        menu.addAction(icons.icon("printer"), tr("Reimprimir (sin pagar otra guía)"), self._reprint)
        if any(job["status"] in ("failed", "canceled") for job in jobs):
            menu.addAction(icons.icon("retry"), tr("Reintentar"), self._retry)
        if any(job["status"] == "queued" for job in jobs):
            menu.addAction(icons.icon("x"), tr("Cancelar"), self._cancel)
        job = jobs[0]
        meta = job.get("meta") or {}
        if len(jobs) == 1 and meta.get("tracking"):
            menu.addAction(icons.icon("copy"), tr("Copiar el número de seguimiento"),
                           lambda: copy_to_clipboard(str(meta["tracking"])))
        if len(jobs) == 1 and job.get("source") == "shopify" and str(meta.get("order_id") or "").isdigit():
            menu.addAction(icons.icon("link"), tr("Abrir el pedido en Shopify"), lambda: QDesktopServices.openUrl(
                QUrl(admin_url(self.ctx.cfg.data.shopify.shop, f"orders/{meta['order_id']}"))))
        menu.addSeparator()
        menu.addAction(icons.icon("trash"), tr("Eliminar del historial"), self._delete)
        return menu

    def _reprint(self) -> None:
        for job in self._require():
            new_job = self.ctx.store.reprint(job["id"])
            meta = job.get("meta") or {}
            if new_job is not None:
                self.ctx.events.emit("job", new_job)
            elif job.get("source") == "shopify" and str(meta.get("order_id") or "").isdigit():
                # La copia guardada ya no está: la misma guía se vuelve a traer de Shopify (nunca se compra otra).
                order_id, name = str(meta["order_id"]), str(meta.get("order_name") or meta["order_id"])
                run_async(lambda order_id=order_id, name=name: self.ctx.services.shopify.reprint_label(order_id, name),
                          lambda _queued: self._after_change(),
                          lambda message: error(self, tr("Reimprimir"), message))
            else:
                error(self, tr("Reimprimir"), tr("El contenido de «{0}» ya no está disponible.", job['title']))
        self._after_change()

    def _retry(self) -> None:
        for job in self._require():
            if job["status"] in ("failed", "canceled", "queued"):
                self.ctx.events.emit("job", self.ctx.store.retry(job["id"]))
        self._after_change()

    def _cancel(self) -> None:
        for job in self._require():
            if job["status"] == "queued":
                self.ctx.events.emit("job", self.ctx.store.cancel(job["id"]))
        self._after_change()

    def _delete(self) -> None:
        jobs = [j for j in self._require() if j["status"] != "printing"]
        if jobs and confirm(self, tr("Eliminar"), tr("¿Eliminar {0} trabajo(s) del historial?", len(jobs))):
            for job in jobs:
                self.ctx.store.delete(job["id"])
            self.refresh()

    def _preview(self) -> None:
        jobs = self._require()
        if not jobs:
            return
        run_dialog(PreviewDialog(self, self.ctx, jobs[0]))


class PreviewDialog(QDialog):
    def __init__(self, parent, ctx: AppContext, job: dict[str, Any]) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Vista previa · {0}", job['title']))
        self.resize(560, 760)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        details = [
            f"<b>{job['title']}</b>",
            tr("ID: {0} · Origen: {1}", job['external_id'], tr(SOURCE_LABELS.get(job['source'], job['source']))),
            tr("Estado: {0}", status_text(job))
            + (f" · {job['error']}" if job["error"] else ""),
        ]
        if job["url"]:
            details.append(f"URL: {job['url'][:90]}")
        layout.addWidget(label("<br>".join(details), wrap=True, selectable=True))

        try:
            raw = ctx.store.read_payload(job)  # en disco va cifrado
        except PayloadUnavailable as exc:
            raw = None
            layout.addWidget(label(tr("{0}. Si es una URL, todavía no se descargó.", exc), "Muted", wrap=True))
        if raw is not None:
            self._render(layout, ctx, job, raw)
        row = QHBoxLayout()
        if raw is not None and job["format"] != "order":
            save_btn = button(tr("Guardar copia…"), "download")
            save_btn.clicked.connect(lambda: self._save_copy(job, raw))
            row.addWidget(save_btn)
        row.addStretch(1)
        close = button(tr("Cerrar"))
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)

    def _save_copy(self, job: dict[str, Any], raw: bytes) -> None:
        """Exporta el contenido descifrado a donde el usuario elija (los datos de la cola siguen cifrados)."""
        fmt = job["format"] if job["format"] != "auto" else detect_format(raw)
        extension = {"pdf": "pdf", "zpl": "zpl", "image": "png"}.get(fmt, "bin")
        name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in job["title"]).strip() or tr("etiqueta")
        target, _ = QFileDialog.getSaveFileName(self, tr("Guardar copia"), f"{name}.{extension}")
        if target:
            Path(target).write_bytes(raw)

    def _render(self, layout: QVBoxLayout, ctx: AppContext, job: dict[str, Any], raw: bytes) -> None:
        source = None
        try:
            if job["format"] == "order":
                order = json.loads(raw.decode("utf-8"))
                data = normalize_order(order, ctx.cfg.data.labels, ctx.cfg.data.shopify.shop)
                source = VectorSource((101.6, 152.4), lambda p, r, dpi: draw_order_label(p, r, dpi, data, ctx.cfg.data.labels.show_items))
            else:
                fmt = job["format"] if job["format"] != "auto" else detect_format(raw)
                if fmt == "pdf":
                    source = PdfSource(raw)
                elif fmt == "image":
                    source = ImageSource(raw)
                else:
                    text = QPlainTextEdit(raw[:200_000].decode("utf-8", "replace"))
                    text.setObjectName("Code")
                    text.setReadOnly(True)
                    layout.addWidget(label(tr("Contenido {0} (se envía tal cual a la impresora):", fmt.upper()), "Muted"))
                    layout.addWidget(text, 1)
                    return
            size = source.size_mm(0)
            width = 480
            height = round(width * size[1] / size[0]) if size else round(width * 1.5)
            image = source.render(0, width * 2, height * 2, False)
            pixmap = QPixmap.fromImage(image)
            pixmap.setDevicePixelRatio(2.0)
            holder = label()
            holder.setPixmap(pixmap)
            holder.setStyleSheet("background: white; border: 1px solid #E2E8F0;")
            scroll = QScrollArea()
            scroll.setWidget(holder)
            scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            layout.addWidget(scroll, 1)
            if source.count() > 1:
                layout.addWidget(label(tr("Página 1 de {0}", source.count()), "Muted"))
        except Exception as exc:
            layout.addWidget(label(tr("No se pudo generar la vista previa: {0}", exc), "Warning", wrap=True))
        finally:
            if source is not None:
                source.close()
