"""Ventana principal: barra lateral + pantallas."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from ..i18n import N_, tr
from ..system.platform import pick
from . import icons
from .context import AppContext
from .navigation import BottomNav, Destination, NavItem, StatusIndicator, fade_in
from .notice_bar import NoticeBar
from .pages.dashboard import DashboardPage
from .pages.guide import GuidePage
from .pages.guide import is_complete as guide_complete
from .pages.history import HistoryPage
from .pages.local_api import LocalApiPage
from .pages.logs import LogsPage
from .pages.n8n import N8nPage
from .pages.orders import OrdersPage
from .pages.printers import PrintersPage
from .pages.report import ReportPage
from .pages.settings import SettingsPage
from .pages.shopify import ShopifyPage
from .theme import Color
from .widgets import error, label

log = logging.getLogger(__name__)

NAV = {
    destination.key: destination
    for destination in (
        Destination("guide", N_("Guía paso a paso"), "book"),
        Destination("assistant", N_("Asistente"), "chat"),  # no es una pantalla: abre y cierra el chat flotante
        Destination("home", N_("Inicio"), "home"),
        Destination("orders", N_("Pedidos"), "package"),
        Destination("report", N_("Reporte"), "bar-chart"),
        Destination("printers", N_("Impresoras"), "printer"),
        Destination("shopify", N_("Shopify"), "shop"),
        Destination("n8n", N_("n8n"), "flow"),
        Destination("api", N_("API local"), "server"),
        Destination("history", N_("Historial"), "clock"),
        Destination("settings", N_("Ajustes"), "sliders"),
        Destination("logs", N_("Registro"), "activity"),
    )
}
# El menú lateral, por secciones (las mismas pestañas, agrupadas).
NAV_SECTIONS = (
    (N_("Operación"), ("home", "orders", "report", "printers", "history")),
    (N_("Tienda y conexiones"), ("shopify", "n8n", "api")),
    (N_("Ayuda"), ("guide", "assistant", "logs")),
    ("", ("settings",)),
)
# En una ventana chica el menú pasa abajo: las principales y «Más» con el resto.
BOTTOM_MAIN = ("home", "orders", "printers", "history")
NARROW_BELOW = 1000  # ancho (px) desde el que el menú baja; vuelve al costado desde WIDE_FROM (sin parpadeo)
WIDE_FROM = 1040


def _assistant_dock(ctx: AppContext, host: QWidget, area: QWidget):
    """El asistente flotante es opcional: si no carga, el resto de la app sigue funcionando igual."""
    try:
        from .assistant_dock import AssistantDock

        return AssistantDock(ctx, host, area)
    except Exception:
        log.exception("No se pudo cargar el asistente")
        return None


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.quitting = False
        self._hint_shown = False
        self.quit_app = self.close  # app.py lo reemplaza por la salida real
        self.pending_update = None
        self._prompted_versions: set[str] = set()
        from .auto_update import AutoUpdater

        self.auto_updater = AutoUpdater(self, ctx)  # «Actualizar sola» (Ajustes → Actualizaciones)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self.resize(1180, 780)
        self.setMinimumSize(760, 560)
        self.narrow = False

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.sidebar = self._sidebar()
        layout.addWidget(self.sidebar)
        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        # Los errores que afectan a la persona, arriba de la pantalla (además del aviso junto al reloj).
        self.notice = NoticeBar(lambda: self.navigate("logs"))
        content.addWidget(self.notice)
        self.stack = QStackedWidget()
        self.stack.setObjectName("Content")
        content.addWidget(self.stack, 1)
        self.bottom_nav = BottomNav([NAV[key] for key in BOTTOM_MAIN],
                                    [NAV[key] for key in NAV if key not in BOTTOM_MAIN])
        self.bottom_nav.selected.connect(self.navigate)
        self.bottom_nav.status.clicked.connect(lambda: self.navigate("home"))
        self.bottom_nav.setVisible(False)
        content.addWidget(self.bottom_nav)
        layout.addLayout(content, 1)
        self.setCentralWidget(root)

        self.pages = {
            "guide": GuidePage(ctx, self.navigate),
            "home": DashboardPage(ctx, self.navigate),
            "orders": OrdersPage(ctx),
            "report": ReportPage(ctx, self.navigate),
            "printers": PrintersPage(ctx),
            "shopify": ShopifyPage(ctx, self.navigate),
            "n8n": N8nPage(ctx),
            "api": LocalApiPage(ctx),
            "history": HistoryPage(ctx),
            "settings": SettingsPage(ctx),
            "logs": LogsPage(ctx),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)
        self.pages["orders"].attention_changed.connect(self._set_orders_badge)
        # Inicio resume el día con lo que Pedidos ya trajo de Shopify (sin preguntar dos veces).
        self.pages["orders"].attention_changed.connect(self.pages["home"].set_attention)
        self.pages["orders"].rows_loaded.connect(self.pages["home"].set_orders)
        # Recordatorio de los pedidos que esperan a una persona (Ajustes → Notificaciones y sonidos).
        self.attention = 0
        self._reminder = QTimer(self)
        self._reminder.timeout.connect(self._remind)
        ctx.bridge.config_saved.connect(lambda section: section in ("notifications", "all") and self._schedule_reminder())
        self._schedule_reminder()
        # El asistente flota sobre todas las pantallas (bubble abajo a la derecha; F1 lo abre y lo cierra).
        self.assistant = _assistant_dock(ctx, root, self.stack)
        if self.assistant is not None:
            self.assistant.toggled.connect(self._mark_assistant_tab)
        shortcut = QShortcut(QKeySequence(Qt.Key.Key_F1), self)
        shortcut.activated.connect(self.toggle_assistant)
        ctx.bridge.event.connect(self._on_event)
        ctx.bridge.config_saved.connect(lambda _s: self._update_status())
        self.navigate("home" if guide_complete(ctx) else "guide")
        self._update_status()

    def _sidebar(self) -> QFrame:
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(232)
        col = QVBoxLayout(side)
        col.setContentsMargins(12, 18, 12, 14)
        col.setSpacing(2)
        brand = QHBoxLayout()
        logo = label()
        logo.setPixmap(icons.app_icon_pixmap(64).scaled(34, 34, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        brand.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(label(APP_NAME, "Brand"))
        names.addWidget(label(tr("Etiquetas automáticas"), "BrandSub"))
        brand.addLayout(names, 1)
        col.addLayout(brand)
        col.addSpacing(8)

        # Las pestañas, por secciones; con la ventana muy baja, la lista se desplaza (nunca se corta).
        nav = QWidget()
        nav.setObjectName("SidebarNav")
        nav_col = QVBoxLayout(nav)
        nav_col.setContentsMargins(0, 0, 0, 0)
        nav_col.setSpacing(2)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea, QWidget#SidebarNav { background: transparent; }")
        scroll.viewport().setStyleSheet("background: transparent;")
        scroll.setWidget(nav)

        self.nav_buttons: dict[str, NavItem] = {}
        for section, keys in NAV_SECTIONS:
            if section:
                nav_col.addWidget(label(tr(section), "NavSection"))
            else:
                nav_col.addSpacing(12)
            for key in keys:
                nav_col.addWidget(self._nav_button(NAV[key]))
        nav_col.addStretch(1)
        col.addWidget(scroll, 1)
        self._sidebar_footer(col)
        return side

    def _nav_button(self, destination: Destination) -> NavItem:
        """Una pestaña del menú lateral. El asistente se queda marcado mientras su chat está abierto."""
        item = NavItem(destination)
        if destination.key == "assistant":
            item.clicked.connect(self.toggle_assistant)
        else:
            item.clicked.connect(lambda _=False, k=destination.key: self.navigate(k))
        self.nav_buttons[destination.key] = item
        return item

    def _sidebar_footer(self, col: QVBoxLayout) -> None:
        """Abajo del menú: aviso de versión nueva, estado de la impresión y versión."""
        self.update_pill = QPushButton("")
        self.update_pill.setProperty("variant", "primary")
        self.update_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_pill.setIcon(icons.icon("download", Color.ON_PRIMARY, 16))
        self.update_pill.setStyleSheet("QPushButton { text-align: left; padding: 8px 10px; }")
        self.update_pill.setVisible(False)
        self.update_pill.clicked.connect(lambda: self.open_update_dialog(self.pending_update))
        col.addWidget(self.update_pill)
        col.addSpacing(6)
        self.status = StatusIndicator()
        self.status.clicked.connect(lambda: self.navigate("home"))
        col.addWidget(self.status)
        version = label(f"v{__version__}", "BrandSub")
        version.setContentsMargins(10, 4, 0, 0)
        col.addWidget(version)

    def open_assistant(self, connect: bool = False, provider: str | None = None) -> None:
        """Muestra la ventana y abre el chat del asistente (con «Conecta tu IA» si se pide)."""
        if self.assistant is None:  # sin asistente en esta compilación: se dice en vez de no hacer nada
            log.warning("Se pidió abrir el asistente, pero no está disponible")
            self.show_and_raise()
            self.notice.show_notice(tr("El asistente no está disponible"),
                                    tr("Esta copia de LabelBridge no pudo cargar el asistente. Revisa la pestaña Registro."),
                                    "warn")
            return
        if not self.isVisible() or self.isMinimized():
            self.show_and_raise()
        else:  # visible pero quizá tapada por otra ventana (p. ej. desde la bandeja)
            self.raise_()
            self.activateWindow()
        self.assistant.open(connect=connect, provider=provider)

    def toggle_assistant(self) -> None:
        if self.assistant is not None:
            self.assistant.toggle()
            return
        self._mark_assistant_tab(False)
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.information(self, tr("Asistente"), tr("No se pudo cargar el asistente en este equipo. "
                                   "El resto de LabelBridge funciona con normalidad."))

    def _schedule_reminder(self) -> None:
        minutes = max(0, int(self.ctx.cfg.data.notifications.remind_minutes or 0))
        if minutes:
            self._reminder.start(minutes * 60_000)
        else:
            self._reminder.stop()

    def _remind(self) -> None:
        if self.attention:
            self.ctx.events.notify(
                tr("Pedidos esperando"),
                tr("{0} pedido(s) necesitan tu atención: ábrelos en la pestaña Pedidos.", self.attention),
                "error", kind="action",
            )

    def _set_orders_badge(self, count: int) -> None:
        self.attention = count
        text = str(count) if count > 0 else ""
        self.nav_buttons["orders"].set_badge(text, attention=True)
        self.nav_buttons["orders"].setToolTip(tr("{0} pedido(s) necesitan tu atención", count) if count else "")
        self.bottom_nav.set_badge("orders", text, attention=True)

    def _mark_assistant_tab(self, is_open: bool) -> None:
        self.nav_buttons["assistant"].setChecked(is_open)

    def navigate(self, key: str) -> None:
        """Abre una pantalla; «settings/label» abre también esa pestaña de Ajustes."""
        key, _, tab = key.partition("/")
        if key == "assistant":  # el asistente no es una pantalla: se abre el chat flotante
            self.open_assistant()
            return
        page = self.pages.get(key)
        if page is None:
            return
        if tab and hasattr(page, "show_tab"):
            page.show_tab(tab)
        for name, btn in self.nav_buttons.items():
            if name != "assistant":  # su marca sigue al chat, no a la pantalla
                btn.setChecked(name == key)
        self.bottom_nav.set_current(key)
        if key == "report":  # ya lo vio: la marca de «Nuevo» se va
            self.nav_buttons["report"].set_badge("")
            self.bottom_nav.set_badge("report", "")
        changed = self.stack.currentWidget() is not page
        self.stack.setCurrentWidget(page)
        page.refresh()
        if changed:
            fade_in(page, rise=8)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        width = self.width()
        narrow = width < WIDE_FROM if self.narrow else width < NARROW_BELOW
        if narrow != self.narrow:  # ventana chica: el menú pasa abajo (y vuelve al costado al agrandarla)
            self.narrow = narrow
            self.sidebar.setVisible(not narrow)
            self.bottom_nav.setVisible(narrow)

    def _on_event(self, kind: str, data) -> None:
        if kind in ("paused", "job", "engine"):
            self._update_status()
        elif kind == "report":  # reporte semanal nuevo: la pestaña lo marca hasta que se abre
            if self.stack.currentWidget() is not self.pages["report"]:
                self.nav_buttons["report"].set_badge(tr("Nuevo"))
                self.bottom_nav.set_badge("report", tr("Nuevo"))
        elif kind == "notify" and isinstance(data, dict) and data.get("level") == "error":
            self.notice.show_notice(str(data.get("title") or ""), str(data.get("message") or ""), "error")
        elif kind == "update" and data is not None:
            self.announce_update(data)

    # --- actualizaciones -------------------------------------------------------------------
    def announce_update(self, info, prompt: bool = True, automatic: bool = True) -> None:
        self.pending_update = info
        self.update_pill.setText(tr("  Actualizar a la {0}", info.version))
        self.update_pill.setVisible(True)
        if prompt and automatic and self.auto_updater.offer(info):  # «Actualizar sola»: sin preguntar
            self.update_pill.setText(tr("  Actualizando a la {0}…", info.version))
            return
        if not prompt or info.version in self._prompted_versions:
            return
        self._prompted_versions.add(info.version)
        if self.isVisible() and not self.isMinimized():
            self.open_update_dialog(info)
        else:
            self.ctx.events.notify(
                tr("Nueva versión de LabelBridge"), tr("La versión {0} está lista. Haz clic aquí para actualizar.", info.version)
            )

    def check_updates_now(self) -> None:
        """Comprobación manual (Ajustes o bandeja): siempre informa del resultado."""
        from .. import __version__ as current
        from .widgets import error, info as show_info, run_async

        def done(found) -> None:
            if found is None:
                self.show_and_raise()
                show_info(self, tr("Actualizaciones"), tr("Ya tienes la última versión de LabelBridge ({0}).", current))
            else:
                self.announce_update(found, prompt=False)
                self.open_update_dialog(found)

        run_async(
            self.ctx.services.updates.check_now,
            done,
            lambda message: error(self, tr("Actualizaciones"), tr("No se pudo comprobar si hay versiones nuevas: {0}", message)),
        )

    def update_failed(self, info) -> None:
        """La actualización anterior no se instaló: se explica y se ofrece reintentar o descargarla a mano."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import QMessageBox

        from ..paths import logs_dir
        from ..updater import UPDATES_PAGE

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(tr("La actualización no se completó"))
        box.setText(tr("No se pudo instalar la versión {0}; sigues con la {1}.", info.version, __version__))
        box.setInformativeText(tr(pick(
            N_("Puedes reintentarlo ahora o descargar el instalador desde la página de descargas. El detalle quedó en "
               "los registros (actualizacion.log e instalador.log)."),
            N_("Puedes reintentarlo ahora o instalarla a mano: sal de LabelBridge (icono de la barra de menús → Salir) y "
               "arrastra la versión nueva a Aplicaciones → Reemplazar. Si no se abrió su ventana, descárgala desde la "
               "página de descargas. El detalle quedó en el registro actualizacion.log."),
        )))
        retry = box.addButton(tr("Reintentar"), QMessageBox.ButtonRole.AcceptRole)
        page = box.addButton(tr("Página de descargas"), QMessageBox.ButtonRole.ActionRole)
        logs = box.addButton(tr("Ver registros"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(tr("Cerrar"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is retry:
            self.check_updates_now()
        elif clicked is page:
            QDesktopServices.openUrl(QUrl(UPDATES_PAGE))
        elif clicked is logs:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(logs_dir())))

    def open_update_dialog(self, info) -> None:
        if info is None:
            return
        from .update_dialog import UpdateDialog

        self.show_and_raise()
        dialog = UpdateDialog(self, self.ctx, info, self.quit_app)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()

    def _update_status(self) -> None:
        """El estado de abajo: problema al imprimir, en pausa, sin impresora, imprimiendo (el punto late) o listo."""
        general = self.ctx.cfg.data.general
        queued = self.ctx.store.counts()["queued"]
        detail = tr("{0} · {1} en cola", general.agent_name, queued)
        if self.ctx.engine.problem:  # el motor tuvo un error inesperado (y lo está reintentando)
            state, title, detail = "error", tr("Problema al imprimir"), self.ctx.engine.problem
        elif general.paused:
            state, title = "warn", tr("En pausa")
        elif not self.ctx.cfg.data.printers:
            state, title = "error", tr("Sin impresora")
        elif queued and self.ctx.store.working_now():
            state, title = "ok", tr("Imprimiendo")
        else:  # nada saliendo ahora (lo que espera un reintento sigue contado en el detalle)
            state, title = "ok", tr("Listo para imprimir")
        live = title == tr("Imprimiendo")
        for indicator in (self.status, self.bottom_nav.status):
            indicator.set_status(state, title, detail, live=live)

    def restart_app(self) -> None:
        """Cierra y vuelve a abrir la app (la nueva espera a que esta termine de cerrarse)."""
        import sys
        from pathlib import Path

        from PySide6.QtCore import QProcess

        args = ["--show", "--restart"]
        if getattr(sys, "frozen", False):
            started = QProcess.startDetached(sys.executable, args)
        else:
            started = QProcess.startDetached(sys.executable, ["-m", "labelbridge", *args],
                                             str(Path(__file__).resolve().parents[2]))
        if isinstance(started, tuple):  # PySide devuelve (ok, pid)
            started = started[0]
        if not started:  # sin la copia nueva, cerrar esta dejaría de imprimir: se queda abierta y se avisa
            log.error("No se pudo reiniciar LabelBridge (%s)", sys.executable)
            error(self, APP_NAME, tr("No se pudo reiniciar LabelBridge. Sigue abierta como estaba; ciérrala y ábrela "
                                     "de nuevo para ver el cambio."))
            return
        self.quit_app()

    def connect_store(self, test: bool = False) -> None:
        """Pantalla Shopify con la tienda del instalador ya escrita. Con la clave también (test=True), se prueba la
        conexión y, si funciona, la tienda queda activa; si no, falta pegar la clave secreta y probar."""
        self.show_and_raise()
        self.navigate("shopify")
        page = self.pages["shopify"]
        if test:
            page.test_now()
            return
        field = page.client_secret if not page.client_secret.text() else page.client_id
        field_widget = getattr(field, "edit", field)
        field_widget.setFocus()

    def show_and_raise(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.quitting:
            event.accept()
            return
        # Cerrar la ventana no detiene el agente: sigue en la bandeja.
        event.ignore()
        self.hide()
        if not self._hint_shown:
            self._hint_shown = True
            self.ctx.events.notify(APP_NAME, tr(pick(
                N_("Sigo imprimiendo en segundo plano. Abre la app desde la bandeja del sistema."),
                N_("Sigo imprimiendo en segundo plano. Abre la app desde su icono en la barra de menús."))))
