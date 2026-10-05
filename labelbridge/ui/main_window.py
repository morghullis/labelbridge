"""Ventana principal: barra lateral + pantallas."""

from __future__ import annotations

import logging

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from . import icons
from .context import AppContext
from .pages.dashboard import DashboardPage
from .pages.guide import GuidePage
from .pages.guide import is_complete as guide_complete
from .pages.history import HistoryPage
from .pages.local_api import LocalApiPage
from .pages.logs import LogsPage
from .pages.n8n import N8nPage
from .pages.printers import PrintersPage
from .pages.settings import SettingsPage
from .pages.shopify import ShopifyPage
from .widgets import label

log = logging.getLogger(__name__)

NAV = (
    ("guide", "Guía paso a paso", "book"),
    ("assistant", "Asistente", "chat"),  # no es una pantalla: abre y cierra el chat flotante
    ("home", "Inicio", "home"),
    ("printers", "Impresoras", "printer"),
    ("shopify", "Shopify", "shop"),
    ("n8n", "n8n", "flow"),
    ("api", "API local", "server"),
    ("history", "Historial", "clock"),
    ("settings", "Ajustes", "sliders"),
    ("logs", "Registro", "activity"),
)


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
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self.resize(1180, 780)
        self.setMinimumSize(980, 640)

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._sidebar())
        self.stack = QStackedWidget()
        self.stack.setObjectName("Content")
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.pages = {
            "guide": GuidePage(ctx, self.navigate),
            "home": DashboardPage(ctx, self.navigate),
            "printers": PrintersPage(ctx),
            "shopify": ShopifyPage(ctx),
            "n8n": N8nPage(ctx),
            "api": LocalApiPage(ctx),
            "history": HistoryPage(ctx),
            "settings": SettingsPage(ctx),
            "logs": LogsPage(ctx),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)
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
        side.setFixedWidth(224)
        col = QVBoxLayout(side)
        col.setContentsMargins(14, 18, 14, 16)
        col.setSpacing(4)
        brand = QHBoxLayout()
        logo = label()
        logo.setPixmap(icons.app_icon_pixmap(64).scaled(34, 34, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        brand.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(label(APP_NAME, "Brand"))
        names.addWidget(label("Etiquetas automáticas", "BrandSub"))
        brand.addLayout(names, 1)
        col.addLayout(brand)
        col.addSpacing(18)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, text, icon_name in NAV:
            btn = QPushButton(f"  {text}")
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setIcon(icons.icon(icon_name, "#CBD5E1", 18))
            btn.setIconSize(QSize(18, 18))
            if key == "assistant":  # se queda marcada (con borde dorado) mientras el chat está abierto
                btn.setProperty("toggle", True)
                btn.clicked.connect(self.toggle_assistant)
            else:
                btn.clicked.connect(lambda _=False, k=key: self.navigate(k))
                self.nav_group.addButton(btn)
            self.nav_buttons[key] = btn
            col.addWidget(btn)
        col.addStretch(1)

        self.update_pill = QPushButton("")
        self.update_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_pill.setIcon(icons.icon("download", "#1E1B4B", 16))
        self.update_pill.setStyleSheet(
            "QPushButton { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #FDE68A, stop:1 #F59E0B);"
            " color: #1E1B4B; border: none; border-radius: 10px; padding: 9px 10px; font-weight: 700; text-align: left; }"
            "QPushButton:hover { background: #FCD34D; }"
        )
        self.update_pill.setVisible(False)
        self.update_pill.clicked.connect(lambda: self.open_update_dialog(self.pending_update))
        col.addWidget(self.update_pill)
        col.addSpacing(6)

        status = QFrame()
        status.setObjectName("SidebarStatus")
        status_layout = QVBoxLayout(status)
        status_layout.setContentsMargins(12, 10, 12, 10)
        status_layout.setSpacing(2)
        self.status_title = label("")
        self.status_title.setStyleSheet("font-weight: 600;")
        self.status_detail = label("", "BrandSub")
        status_layout.addWidget(self.status_title)
        status_layout.addWidget(self.status_detail)
        col.addWidget(status)
        col.addSpacing(6)
        col.addWidget(label(f"v{__version__}", "BrandSub"))
        return side

    def open_assistant(self, connect: bool = False, provider: str | None = None) -> None:
        """Muestra la ventana y abre el chat del asistente (con «Conecta tu IA» si se pide)."""
        if self.assistant is None:
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

        QMessageBox.information(self, "Asistente", "No se pudo cargar el asistente en este equipo. "
                                "El resto de LabelBridge funciona con normalidad.")

    def _mark_assistant_tab(self, is_open: bool) -> None:
        btn = self.nav_buttons["assistant"]
        btn.setChecked(is_open)
        btn.setIcon(icons.icon("chat", "#FFFFFF" if is_open else "#CBD5E1", 18))

    def navigate(self, key: str) -> None:
        if key == "assistant":  # el asistente no es una pantalla: se abre el chat flotante
            self.open_assistant()
            return
        page = self.pages.get(key)
        if page is None:
            return
        for name, btn in self.nav_buttons.items():
            if name == "assistant":  # su marca sigue al chat, no a la pantalla
                continue
            active = name == key
            btn.setChecked(active)
            icon_name = next(i for k, _, i in NAV if k == name)
            btn.setIcon(icons.icon(icon_name, "#FFFFFF" if active else "#CBD5E1", 18))
        self.stack.setCurrentWidget(page)
        page.refresh()

    def _on_event(self, kind: str, data) -> None:
        if kind in ("paused", "job"):
            self._update_status()
        elif kind == "update" and data is not None:
            self.announce_update(data)

    # --- actualizaciones -------------------------------------------------------------------
    def announce_update(self, info, prompt: bool = True) -> None:
        self.pending_update = info
        self.update_pill.setText(f"  Actualizar a la {info.version}")
        self.update_pill.setVisible(True)
        if not prompt or info.version in self._prompted_versions:
            return
        self._prompted_versions.add(info.version)
        if self.isVisible() and not self.isMinimized():
            self.open_update_dialog(info)
        else:
            self.ctx.events.notify(
                "Nueva versión de LabelBridge", f"La versión {info.version} está lista. Haz clic aquí para actualizar."
            )

    def check_updates_now(self) -> None:
        """Comprobación manual (Ajustes o bandeja): siempre informa del resultado."""
        from .. import __version__ as current
        from .widgets import error, info as show_info, run_async

        def done(found) -> None:
            if found is None:
                self.show_and_raise()
                show_info(self, "Actualizaciones", f"Ya tienes la última versión de LabelBridge ({current}).")
            else:
                self.announce_update(found, prompt=False)
                self.open_update_dialog(found)

        run_async(
            self.ctx.services.updates.check_now,
            done,
            lambda message: error(self, "Actualizaciones", f"No se pudo comprobar si hay versiones nuevas: {message}"),
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
        box.setWindowTitle("La actualización no se completó")
        box.setText(f"No se pudo instalar la versión {info.version}; sigues con la {__version__}.")
        box.setInformativeText(
            "Puedes reintentarlo ahora o descargar el instalador desde la página de descargas. El detalle quedó en "
            "los registros (actualizacion.log e instalador.log)."
        )
        retry = box.addButton("Reintentar", QMessageBox.ButtonRole.AcceptRole)
        page = box.addButton("Página de descargas", QMessageBox.ButtonRole.ActionRole)
        logs = box.addButton("Ver registros", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Cerrar", QMessageBox.ButtonRole.RejectRole)
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
        paused = self.ctx.cfg.data.general.paused
        queued = self.ctx.store.counts()["queued"]
        dot = "🟡" if paused else "🟢"
        self.status_title.setText(f"{dot}  {'En pausa' if paused else 'Imprimiendo'}")
        self.status_detail.setText(f"{self.ctx.cfg.data.general.agent_name} · {queued} en cola")

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
            self.ctx.events.notify(APP_NAME, "Sigo imprimiendo en segundo plano. Abre la app desde la bandeja del sistema.")
