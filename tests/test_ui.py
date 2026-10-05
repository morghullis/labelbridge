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
    for key, _, _ in NAV:
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
