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


def test_the_app_is_dark_neon_whatever_the_system_uses(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication

    from labelbridge.ui.theme import Color, apply_theme

    app = QApplication.instance()
    light = QPalette()
    for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base):
        light.setColor(role, QColor("#FFFFFF"))
    light.setColor(QPalette.ColorRole.Text, QColor("#000000"))
    app.setPalette(light)  # como queda con Windows en modo claro
    apply_theme(app)
    assert app.palette().color(QPalette.ColorRole.Text).name() == Color.TEXT.lower()
    assert app.palette().color(QPalette.ColorRole.Window).name() == Color.BG.lower()
    assert app.palette().color(QPalette.ColorRole.Link).name() == Color.PRIMARY.lower()  # el verde neón
    # El esquema oscuro lo respeta Windows; la plataforma de pruebas (offscreen) lo ignora y queda «Unknown».
    assert app.styleHints().colorScheme() in (Qt.ColorScheme.Dark, Qt.ColorScheme.Unknown)


def test_counting_numbers_reuse_one_animation(qapp, monkeypatch):
    from PySide6.QtCore import QVariantAnimation
    from PySide6.QtWidgets import QLabel

    from labelbridge.ui import motion

    monkeypatch.setattr(motion, "motion_enabled", lambda: True)
    number = QLabel()
    number.show()
    for value in range(1, 60):
        motion.count_up(number, value, lambda v: str(round(v)))
    assert len(number.findChildren(QVariantAnimation)) == 1  # la app queda abierta semanas: no se acumulan
    number._count_animation.stop()
    assert number._count_value == 59
