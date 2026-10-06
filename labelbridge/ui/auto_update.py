"""«Actualizar sola» (Ajustes → Actualizaciones, encendido por defecto): la versión nueva se descarga, se verifica
(SHA-256) y se instala sin preguntar, en un momento tranquilo: nada imprimiéndose ni por imprimirse ya, ninguna compra
de guía en curso y ninguna ventana abierta esperando a alguien. Al abrirse de nuevo, la app muestra las novedades.

Si una versión no se pudo instalar sola, esa versión ya no se intenta sola: se avisa como antes y la persona decide.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from .. import updater
from ..updater import UpdateInfo
from .context import AppContext
from .widgets import run_async

log = logging.getLogger(__name__)

RETRY_MS = 60_000  # mientras está ocupada, se vuelve a mirar cada minuto


class AutoUpdater(QObject):
    def __init__(self, window, ctx: AppContext) -> None:
        super().__init__(window)
        self.window = window
        self.ctx = ctx
        self.info: UpdateInfo | None = None
        self.installer: Path | None = None
        self.timer = QTimer(self)
        self.timer.setInterval(RETRY_MS)
        self.timer.timeout.connect(self.try_install)

    def wanted(self, info: UpdateInfo) -> bool:
        g = self.ctx.cfg.data.general
        return (g.auto_install_updates and updater.can_install()
                and self.ctx.store.kv_get("update:auto_failed") != info.version)

    def offer(self, info: UpdateInfo) -> bool:
        """True si se encarga ella (se actualiza sola); False: que se avise como antes."""
        if not self.wanted(info):
            return False
        if self.info is not None and self.info.version == info.version:
            return True  # ya la está descargando o esperando el momento
        self.info, self.installer = info, None
        log.info("Actualizar sola: descargando la versión %s", info.version)
        run_async(lambda: updater.download(info), self._downloaded, self._failed)
        return True

    def _downloaded(self, path: str) -> None:
        self.installer = Path(path)
        log.info("Actualizar sola: versión %s descargada y verificada", self.info.version if self.info else "?")
        self.timer.start()
        self.try_install()

    def _failed(self, message: str) -> None:
        log.warning("Actualizar sola: no se pudo descargar la versión nueva: %s", message)
        info, self.info = self.info, None
        if info is not None:  # se avisa como antes (la descarga se puede reintentar a mano)
            self.window.announce_update(info, prompt=True, automatic=False)

    def busy(self) -> str:
        """Por qué no es buen momento («» si sí lo es)."""
        if self.ctx.store.working_now():
            return "imprimiendo"
        if self.ctx.cfg.data.shopify.shop.strip():  # sin tienda conectada no hay compras de guías
            try:
                if self.ctx.services.shopify._pending():
                    return "comprando una guía"
            except Exception:  # la tienda escrita a medias: no impide actualizar
                log.debug("Actualizar sola: no se pudieron revisar las compras en curso", exc_info=True)
        if QApplication.activeModalWidget() is not None:
            return "hay una ventana abierta"
        from .fedex_dialog import _open_windows

        if _open_windows:
            return "esperando una guía de FedEx"
        return ""

    def try_install(self) -> bool:
        if self.info is None or self.installer is None:
            return False
        reason = self.busy()
        if reason:
            log.info("Actualizar sola: se espera un momento tranquilo (%s)", reason)
            return False
        self.timer.stop()
        info, installer = self.info, self.installer
        self.ctx.store.kv_set("update:installed", info.to_json())  # al abrirse: novedades (o el aviso si falló)
        try:
            updater.install_and_restart(installer)
        except Exception:
            log.exception("Actualizar sola: no se pudo instalar la versión %s", info.version)
            self.ctx.store.kv_set("update:installed", "")
            self.ctx.store.kv_set("update:auto_failed", info.version)
            self.info = None
            self.window.announce_update(info, prompt=True, automatic=False)
            return False
        log.info("Actualizar sola: instalando la versión %s (la app se cierra y se vuelve a abrir)", info.version)
        self.window.quit_app()
        return True
