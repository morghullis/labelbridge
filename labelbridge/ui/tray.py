"""Icono en la bandeja del sistema con menú rápido y notificaciones."""

from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .. import APP_NAME
from ..i18n import tr
from ..system.platform import MAC
from . import alerts, icons
from .context import AppContext


class Tray(QSystemTrayIcon):
    def __init__(
        self,
        ctx: AppContext,
        show_window: Callable[[], None],
        quit_app: Callable[[], None],
        print_test: Callable[[], object],
        check_updates: Callable[[], object] | None = None,
        message_clicked: Callable[[], object] | None = None,
        open_assistant: Callable[[], object] | None = None,
    ) -> None:
        super().__init__(icons.app_icon())
        self.ctx = ctx
        self._last_notice: dict[str, float] = {}
        menu = QMenu()
        open_action = menu.addAction(icons.icon("home"), tr("Abrir {0}", APP_NAME))
        open_action.triggered.connect(show_window)
        if open_assistant is not None:
            assistant = menu.addAction(icons.icon("chat"), tr("Asistente"))
            assistant.triggered.connect(open_assistant)
        menu.addSeparator()
        self.pause_action = QAction(menu)
        self.pause_action.triggered.connect(lambda: ctx.engine.set_paused(not ctx.cfg.data.general.paused))
        menu.addAction(self.pause_action)
        test = menu.addAction(icons.icon("printer"), tr("Imprimir etiqueta de prueba"))
        test.triggered.connect(print_test)
        if check_updates is not None:
            updates = menu.addAction(icons.icon("download"), tr("Buscar actualizaciones"))
            updates.triggered.connect(check_updates)
        if message_clicked is not None:
            self.messageClicked.connect(message_clicked)
        menu.addSeparator()
        quit_action = menu.addAction(icons.icon("power"), tr("Salir (detiene la impresión automática)"))
        quit_action.triggered.connect(quit_app)
        self.setContextMenu(menu)
        self._menu = menu
        # En Windows, clic o doble clic en el icono abren la ventana. En la Mac el clic despliega el menú (y Qt lo
        # informa también como «Trigger»): la ventana se abre con «Abrir LabelBridge».
        opens_window = () if MAC else (QSystemTrayIcon.ActivationReason.Trigger,
                                       QSystemTrayIcon.ActivationReason.DoubleClick)
        self.activated.connect(lambda reason: show_window() if reason in opens_window else None)
        ctx.bridge.event.connect(self._on_event)
        self._update()

    def _update(self) -> None:
        paused = self.ctx.cfg.data.general.paused
        self.pause_action.setText(tr("Reanudar impresión automática") if paused else tr("Pausar impresión automática"))
        self.pause_action.setIcon(icons.icon("play" if paused else "pause"))
        queued = self.ctx.store.counts()["queued"]
        state = tr("en pausa") if paused else tr("activo")
        self.setToolTip(tr("{0} · {1} · {2} en cola", APP_NAME, state, queued))

    def _on_event(self, kind: str, data) -> None:
        if kind in ("job", "paused"):
            self._update()
        elif kind == "notify" and isinstance(data, dict):
            key = f"{data.get('title')}|{data.get('message')}"
            now = time.time()
            if now - self._last_notice.get(key, 0) < 30:
                return  # evita repetir el mismo aviso en ráfaga
            self._last_notice[key] = now
            kind = data.get("kind") or ""
            if alerts.toast_enabled(self.ctx.cfg, kind):
                icon = (
                    QSystemTrayIcon.MessageIcon.Warning
                    if data.get("level") == "error"
                    else QSystemTrayIcon.MessageIcon.Information
                )
                self.showMessage(data.get("title", APP_NAME), data.get("message", ""), icon, 6000)
            alerts.play_for(self.ctx.cfg, kind)  # el sonido va aparte: se puede tener sonido sin aviso
        elif kind == "open_url" and isinstance(data, dict):
            # Solo páginas del admin de Shopify (p. ej. el pedido para «Imprimir etiqueta» de la FedEx de Flow).
            url = str(data.get("url") or "")
            if url.startswith("https://admin.shopify.com/"):
                QDesktopServices.openUrl(QUrl(url))
        elif kind == "connector" and isinstance(data, dict) and data.get("state") == "error":
            names = {"shopify": tr("Shopify"), "mqtt": tr("n8n (tiempo real)"), "local_api": tr("API local")}
            name = names.get(data.get("name"), data.get("name"))
            key = f"conn|{data.get('name')}"
            now = time.time()
            if now - self._last_notice.get(key, 0) > 600 and self.ctx.cfg.data.general.notify_errors:
                self._last_notice[key] = now
                self.showMessage(tr("Problema con {0}", name), data.get("message", ""), QSystemTrayIcon.MessageIcon.Warning, 6000)
