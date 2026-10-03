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
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QTableView,
    QVBoxLayout,
)

from ...labels.order import normalize_order
from ...labels.qt_label import draw_order_label
from ...models import SOURCE_LABELS, STATUS_LABELS, status_text
from ...printing.detect import detect_format
from ...printing.pages import ImageSource, PdfSource, VectorSource
from ..context import AppContext
from ..theme import STATE_COLORS, job_state
from ..widgets import Card, Page, button, confirm, error, info, label

COLUMNS = ["Fecha", "Trabajo", "Origen", "Impresora", "Formato", "Estado", "Intentos", "Detalle"]
FORMAT_LABELS = {"pdf": "PDF", "image": "Imagen", "zpl": "ZPL", "raw": "RAW", "order": "Etiqueta pedido", "auto": "—"}


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
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        job = self.jobs[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return datetime.fromtimestamp(job["created_at"]).strftime("%d/%m %H:%M:%S")
            if col == 1:
                return job["title"]
            if col == 2:
                return SOURCE_LABELS.get(job["source"], job["source"])
            if col == 3:
                return job["printer_used"] or job["printer"] or "Predeterminada"
            if col == 4:
                return FORMAT_LABELS.get(job["format"], job["format"].upper())
            if col == 5:
                return status_text(job)
            if col == 6:
                return str(job["attempts"])
            if col == 7:
                if job["status"] == "done" and job["printed_at"]:
                    return f"Impreso {datetime.fromtimestamp(job['printed_at']).strftime('%H:%M:%S')}"
                return job["error"]
        if role == Qt.ItemDataRole.ForegroundRole and col == 5:
            return QColor(STATE_COLORS[job_state(job)][1])
        if role == Qt.ItemDataRole.ToolTipRole and col in (1, 7):
            return job["error"] or job["external_id"]
        return None


class HistoryPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__("Historial", "Cada etiqueta recibida, impresa o con error. Doble clic para ver la vista previa.")
        self.ctx = ctx
        card = Card()
        filters = QHBoxLayout()
        self.status = QComboBox()
        self.status.addItem("Todos los estados", None)
        for key, text in STATUS_LABELS.items():
            self.status.addItem(text, key)
        self.status.currentIndexChanged.connect(self.refresh)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar por pedido, impresora o error…")
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
        card.add(self.view, stretch=1)

        actions = QHBoxLayout()
        for text, icon_name, slot, variant in (
            ("Vista previa", "eye", self._preview, None),
            ("Reimprimir", "printer", self._reprint, "primary"),
            ("Reintentar", "retry", self._retry, None),
            ("Cancelar", "x", self._cancel, None),
            ("Eliminar", "trash", self._delete, "danger"),
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
        self.count.setText(f"{len(jobs)} trabajo(s)")
        for row, job in enumerate(jobs):
            if job["id"] in selected:
                self.view.selectRow(row)

    def _selected(self) -> list[dict[str, Any]]:
        rows = sorted({i.row() for i in self.view.selectionModel().selectedRows()}) if self.view.selectionModel() else []
        return [self.model.jobs[r] for r in rows if r < len(self.model.jobs)]

    def _require(self) -> list[dict[str, Any]]:
        jobs = self._selected()
        if not jobs:
            info(self, "Historial", "Selecciona uno o más trabajos.")
        return jobs

    def _after_change(self) -> None:
        self.ctx.engine.wake()
        self.refresh()

    def _reprint(self) -> None:
        for job in self._require():
            new_job = self.ctx.store.reprint(job["id"])
            if new_job is None:
                error(self, "Reimprimir", f"El contenido de «{job['title']}» ya no está disponible.")
            else:
                self.ctx.events.emit("job", new_job)
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
        if jobs and confirm(self, "Eliminar", f"¿Eliminar {len(jobs)} trabajo(s) del historial?"):
            for job in jobs:
                self.ctx.store.delete(job["id"])
            self.refresh()

    def _preview(self) -> None:
        jobs = self._require()
        if not jobs:
            return
        PreviewDialog(self, self.ctx, jobs[0]).exec()


class PreviewDialog(QDialog):
    def __init__(self, parent, ctx: AppContext, job: dict[str, Any]) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Vista previa · {job['title']}")
        self.resize(560, 760)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        details = [
            f"<b>{job['title']}</b>",
            f"ID: {job['external_id']} · Origen: {SOURCE_LABELS.get(job['source'], job['source'])}",
            f"Estado: {STATUS_LABELS.get(job['status'], job['status'])}"
            + (f" · {job['error']}" if job["error"] else ""),
        ]
        if job["url"]:
            details.append(f"URL: {job['url'][:90]}")
        layout.addWidget(label("<br>".join(details), wrap=True, selectable=True))

        path = Path(job["payload_path"]) if job["payload_path"] else None
        if path is None or not path.exists():
            layout.addWidget(label("El archivo todavía no se descargó o ya no existe.", "Muted"))
        else:
            self._render(layout, ctx, job, path)
        row = QHBoxLayout()
        if path is not None and path.exists():
            open_btn = button("Abrir archivo", "folder")
            open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
            row.addWidget(open_btn)
        row.addStretch(1)
        close = button("Cerrar")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)

    def _render(self, layout: QVBoxLayout, ctx: AppContext, job: dict[str, Any], path: Path) -> None:
        source = None
        try:
            if job["format"] == "order":
                order = json.loads(path.read_text(encoding="utf-8"))
                data = normalize_order(order, ctx.cfg.data.labels, ctx.cfg.data.shopify.shop)
                source = VectorSource((101.6, 152.4), lambda p, r, dpi: draw_order_label(p, r, dpi, data, ctx.cfg.data.labels.show_items))
            else:
                raw = path.read_bytes()
                fmt = job["format"] if job["format"] != "auto" else detect_format(raw)
                if fmt == "pdf":
                    source = PdfSource(raw)
                elif fmt == "image":
                    source = ImageSource(raw)
                else:
                    text = QPlainTextEdit(raw[:200_000].decode("utf-8", "replace"))
                    text.setObjectName("Code")
                    text.setReadOnly(True)
                    layout.addWidget(label(f"Contenido {fmt.upper()} (se envía tal cual a la impresora):", "Muted"))
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
                layout.addWidget(label(f"Página 1 de {source.count()}", "Muted"))
        except Exception as exc:
            layout.addWidget(label(f"No se pudo generar la vista previa: {exc}", "Warning", wrap=True))
        finally:
            if source is not None:
                source.close()
