"""Registro en vivo (para diagnosticar sin abrir archivos)."""

from __future__ import annotations

import logging

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QPlainTextEdit

from ...i18n import N_, tr
from ...logging_setup import memory_handler
from ...paths import logs_dir
from ..context import AppContext
from ..widgets import Card, Page, button, copy_to_clipboard

LEVELS = (
    (N_("Todo"), logging.DEBUG),
    (N_("Información"), logging.INFO),
    (N_("Avisos y errores"), logging.WARNING),
    (N_("Errores"), logging.ERROR),
)


class LogsPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(tr("Registro"), tr("Actividad del agente en tiempo real."))
        card = Card()
        row = QHBoxLayout()
        self.level = QComboBox()
        for text, value in LEVELS:
            self.level.addItem(tr(text), value)
        self.level.setCurrentIndex(1)
        self.level.currentIndexChanged.connect(self._reload)
        row.addWidget(self.level)
        row.addStretch(1)
        copy = button(tr("Copiar"), "copy")
        copy.clicked.connect(lambda: copy_to_clipboard(self.text.toPlainText()))
        folder = button(tr("Abrir carpeta de registros"), "folder")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(logs_dir()))))
        row.addWidget(copy)
        row.addWidget(folder)
        card.add(layout=row)
        self.text = QPlainTextEdit()
        self.text.setObjectName("Code")
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(3000)
        self.text.setMinimumHeight(520)
        card.add(self.text, stretch=1)
        self.add(card, stretch=1)
        ctx.bridge.log_line.connect(self._append)
        self._reload()

    def _reload(self) -> None:
        minimum = self.level.currentData()
        self.text.setPlainText("\n".join(line for level, line in list(memory_handler.lines) if level >= minimum))
        self.text.moveCursor(QTextCursor.MoveOperation.End)

    def _append(self, level: int, line: str) -> None:
        if level >= self.level.currentData():
            self.text.appendPlainText(line)
