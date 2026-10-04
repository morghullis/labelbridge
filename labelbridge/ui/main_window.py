"""Ventana principal: barra lateral + pantallas."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QCloseEvent
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

NAV = (
    ("guide", "Guía paso a paso", "book"),
    ("home", "Inicio", "home"),
    ("printers", "Impresoras", "printer"),
    ("shopify", "Shopify", "shop"),
    ("n8n", "n8n", "flow"),
    ("api", "API local", "server"),
    ("history", "Historial", "clock"),
    ("settings", "Ajustes", "sliders"),
    ("logs", "Registro", "activity"),
)


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.quitting = False
        self._hint_shown = False
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
            btn.clicked.connect(lambda _=False, k=key: self.navigate(k))
            self.nav_group.addButton(btn)
            self.nav_buttons[key] = btn
            col.addWidget(btn)
        col.addStretch(1)

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

    def navigate(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        for name, btn in self.nav_buttons.items():
            active = name == key
            btn.setChecked(active)
            icon_name = next(i for k, _, i in NAV if k == name)
            btn.setIcon(icons.icon(icon_name, "#FFFFFF" if active else "#CBD5E1", 18))
        self.stack.setCurrentWidget(page)
        page.refresh()

    def _on_event(self, kind: str, _data) -> None:
        if kind in ("paused", "job"):
            self._update_status()

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
