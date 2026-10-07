"""La interfaz se construye y todas las pantallas cargan (sin pantalla real)."""

from __future__ import annotations

from labelbridge.events import Events
from labelbridge.models import parse_job


def test_main_window_builds_and_navigates(core):
    from labelbridge.services import Services
    from labelbridge.ui.context import AppContext, QtBridge
    from labelbridge.ui.main_window import NAV, MainWindow
    from labelbridge.ui.theme import STYLESHEET
    from labelbridge.ui.tray import Tray

    core.cfg.data.local_api.enabled = False
    services = Services(core.cfg, core.secrets, core.store, core.engine, core.events)
    bridge = QtBridge(core.events, core.cfg)
    ctx = AppContext(core.cfg, core.secrets, core.store, core.engine, services, core.events, bridge)
    core.store.add(parse_job({"id": "ui-1", "content": "^XA^XZ", "title": "Pedido #1"}), "api")

    from PySide6.QtWidgets import QApplication

    QApplication.instance().setStyleSheet(STYLESHEET)
    window = MainWindow(ctx)
    tray = Tray(ctx, window.show_and_raise, lambda: None, lambda: None)
    window.show()
    for key in NAV:
        if key == "assistant":  # la pestaña abre el chat flotante (tests/test_mascot.py)
            continue
        window.navigate(key)
        QApplication.processEvents()
        assert window.stack.currentWidget() is window.pages[key]
    core.events.emit("job", core.store.list()[0])
    core.events.emit("connector", {"name": "shopify", "state": "error", "message": "prueba"})
    QApplication.processEvents()
    window.quitting = True
    window.close()
    tray.hide()
    assert isinstance(core.events, Events)


def test_the_app_stays_light_even_with_windows_in_dark_mode(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication

    from labelbridge.ui.theme import TEXT, apply_theme

    app = QApplication.instance()
    dark = QPalette()
    for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base):
        dark.setColor(role, QColor("#1E1E1E"))
    dark.setColor(QPalette.ColorRole.Text, QColor("#FFFFFF"))
    app.setPalette(dark)  # como queda con el modo oscuro de Windows 11
    apply_theme(app)
    assert app.palette().color(QPalette.ColorRole.Text).name() == TEXT.lower()
    assert app.palette().color(QPalette.ColorRole.Base).name() == "#ffffff"
    # El esquema claro lo respeta Windows; la plataforma de pruebas (offscreen) lo ignora y queda «Unknown».
    assert app.styleHints().colorScheme() in (Qt.ColorScheme.Light, Qt.ColorScheme.Unknown)
