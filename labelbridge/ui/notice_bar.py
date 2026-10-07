"""Avisos dentro de la ventana: un error que afecta a la persona se ve arriba de la pantalla, también cuando no hay
bandeja del sistema o el aviso de Windows ya se fue. Una línea de color a la izquierda (sin caja), el último aviso y
cuántos más hubo, «Ver registro» y cerrar."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout

from ..i18n import tr
from .navigation import fade_in
from .widgets import button, label, link_button, repolish

INFO_SECONDS = 12  # los avisos que no son errores se van solos; los errores quedan hasta cerrarlos


class NoticeBar(QFrame):
    def __init__(self, open_logs: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.setObjectName("NoticeBar")
        self.setProperty("level", "error")
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 10, 10, 10)
        row.setSpacing(12)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.title = label("", "NoticeTitle", wrap=True)
        self.message = label("", "NoticeText", wrap=True, selectable=True)
        texts.addWidget(self.title)
        texts.addWidget(self.message)
        row.addLayout(texts, 1)
        self.logs = link_button(tr("Ver registro"))
        self.logs.setVisible(open_logs is not None)
        if open_logs is not None:
            self.logs.clicked.connect(open_logs)
        row.addWidget(self.logs, 0, Qt.AlignmentFlag.AlignTop)
        close = button("", "x", "ghost", tr("Cerrar"))
        close.clicked.connect(self.dismiss)
        row.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        self.count = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self.setVisible(False)

    def show_notice(self, title: str, message: str, level: str = "error") -> None:
        """El aviso más nuevo; si ya había otro a la vista, se cuenta («y 2 avisos más»)."""
        self.count = self.count + 1 if not self.isHidden() else 1  # isVisible() es falso con la ventana escondida
        extra = tr(" (y {0} aviso(s) más)", self.count - 1) if self.count > 1 else ""
        self.title.setText(title + extra)
        self.message.setText(message)
        self.message.setVisible(bool(message))
        if self.property("level") != level:
            self.setProperty("level", level)
            repolish(self)
        self._timer.stop()
        if level != "error":
            self._timer.start(INFO_SECONDS * 1000)
        if self.isHidden():
            self.setVisible(True)
            fade_in(self)

    def dismiss(self) -> None:
        self._timer.stop()
        self.count = 0
        self.setVisible(False)
