"""Arranque de LabelBridge: servicios en segundo plano + ventana + bandeja del sistema."""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

from . import APP_NAME, __version__

log = logging.getLogger("labelbridge")


def _ensure_qt_plugin_path() -> None:
    """Qt corta su ruta de plugins en ';'. Si la carpeta del proyecto lo contiene, se la indicamos a mano."""
    if getattr(sys, "frozen", False):
        return
    try:
        import PySide6
    except ImportError:
        return
    plugins = Path(PySide6.__file__).resolve().parent / "Qt" / "plugins"
    if plugins.is_dir():
        current = os.environ.get("QT_PLUGIN_PATH", "")
        if str(plugins) not in current.split(os.pathsep):
            os.environ["QT_PLUGIN_PATH"] = os.pathsep.join(p for p in (str(plugins), current) if p)


def _install_exception_hooks() -> None:
    def excepthook(exc_type, exc, tb) -> None:
        log.critical("Error no controlado", exc_info=(exc_type, exc, tb))

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        log.critical("Error no controlado en hilo %s", args.thread.name if args.thread else "?",
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = excepthook
    threading.excepthook = thread_hook


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    _ensure_qt_plugin_path()
    if "--self-test" in argv:
        from .selftest import run as self_test

        return self_test(argv)

    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from .config import ConfigManager
    from .events import Events
    from .jobstore import JobStore
    from .logging_setup import memory_handler, setup_logging
    from .paths import config_path, data_dir, db_path, spool_dir
    from .printing.engine import PrintEngine
    from .secret_store import SecretStore
    from .services import Services
    from .system import autostart
    from .system.single_instance import SingleInstance
    from .ui import icons
    from .ui.actions import enqueue_test_label
    from .ui.context import AppContext, QtBridge
    from .ui.main_window import MainWindow
    from .ui.theme import STYLESHEET
    from .ui.tray import Tray

    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(APP_NAME)
    QApplication.setApplicationVersion(__version__)
    app = QApplication(argv)
    app.setQuitOnLastWindowClosed(False)

    setup_logging()
    _install_exception_hooks()

    instance = SingleInstance()
    if not instance.acquire():
        log.info("LabelBridge ya está abierto: se muestra la ventana existente")
        return 0
    log.info("Iniciando %s %s", APP_NAME, __version__)

    if sys.platform == "win32":
        app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(STYLESHEET)
    app.setWindowIcon(icons.app_icon())

    cfg = ConfigManager(config_path())
    secrets = SecretStore(data_dir() / "secrets.json")
    store = JobStore(db_path(), spool_dir())
    events = Events()
    engine = PrintEngine(store, cfg, events)
    services = Services(cfg, secrets, store, engine, events)
    bridge = QtBridge(events, cfg)
    memory_handler.add_listener(bridge.forward_log)
    ctx = AppContext(cfg, secrets, store, engine, services, events, bridge)

    window = MainWindow(ctx)

    def quit_app() -> None:
        window.quitting = True
        app.quit()

    tray = Tray(ctx, window.show_and_raise, quit_app, lambda: enqueue_test_label(ctx))
    instance.activated.connect(window.show_and_raise)

    def ensure_tray(attempts_left: int = 10) -> None:
        # Al iniciar sesión en Windows la bandeja puede tardar unos segundos en existir.
        if QSystemTrayIcon.isSystemTrayAvailable():
            tray.show()
        elif attempts_left > 0:
            QTimer.singleShot(3000, lambda: ensure_tray(attempts_left - 1))
        else:
            log.warning("No hay bandeja del sistema: se muestra la ventana")
            window.show()

    engine.start()
    services.start()
    try:
        autostart.apply(cfg.data.general.start_with_windows)
    except OSError:
        log.warning("No se pudo configurar el inicio automático", exc_info=True)

    wants_hidden = "--minimized" in argv or cfg.data.general.start_minimized
    if "--show" in argv or not cfg.data.printers or not wants_hidden:
        window.show()
    ensure_tray()

    code = app.exec()
    log.info("Cerrando %s…", APP_NAME)
    tray.hide()
    services.stop()
    engine.stop()
    store.close()
    return code
