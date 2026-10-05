"""Arranque de LabelBridge: servicios en segundo plano + ventana + bandeja del sistema."""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

from . import APP_NAME, __version__
from .i18n import tr

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


def _install_spanish_translator(app) -> None:
    """Botones estándar de Qt en español (Sí, No, Cancelar...)."""
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

    folders = [QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)]
    try:
        import PySide6

        base = Path(PySide6.__file__).resolve().parent
        folders += [str(base / "translations"), str(base / "Qt" / "translations")]
    except Exception:
        pass
    translator = QTranslator(app)
    for folder in folders:
        if translator.load(QLocale(QLocale.Language.Spanish), "qtbase", "_", folder):
            app.installTranslator(translator)
            app._labelbridge_translator = translator  # mantener la referencia viva
            return


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
    from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

    from . import i18n
    from .config import ConfigManager
    from .events import Events
    from .jobstore import JobStore
    from .logging_setup import memory_handler, setup_logging
    from .paths import config_path, data_dir, db_path, logs_dir, spool_dir
    from .printing.engine import PrintEngine
    from .secret_store import SecretStore
    from .security.audit import AuditLog
    from .security.crypto import CryptoError, DataProtector
    from .security.policy import SecurityPolicy
    from .services import Services
    from .system import autostart, installer_hints
    from .system.single_instance import SingleInstance
    from .ui import icons
    from .ui.actions import enqueue_test_label
    from .ui.context import AppContext, QtBridge
    from .ui.main_window import MainWindow
    from .ui.update_dialog import WhatsNewDialog
    from .updater import failed_update, pending_whats_new
    from .ui.pages.guide import is_complete as guide_complete
    from .ui.theme import STYLESHEET
    from .ui.tray import Tray

    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(APP_NAME)
    QApplication.setApplicationVersion(__version__)
    app = QApplication(argv)
    app.setQuitOnLastWindowClosed(False)
    # Idioma provisional (el del instalador o el de Windows) hasta leer la configuración.
    hints = installer_hints.read()
    i18n.set_language(i18n.choose("", hints.get("language", "")))

    setup_logging()
    _install_exception_hooks()

    instance = SingleInstance()
    if "--restart" in argv:  # reinicio pedido por la app (p. ej. al cambiar el idioma)
        instance.wait_until_free(20)
    if not instance.acquire():
        log.info("LabelBridge ya está abierto: se muestra la ventana existente")
        return 0
    log.info("Iniciando %s %s", APP_NAME, __version__)

    if sys.platform == "win32":
        app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(STYLESHEET)
    app.setWindowIcon(icons.app_icon())

    secrets = SecretStore(data_dir() / "secrets.json")
    try:
        # Clave de cifrado de los datos en disco (configuración, pedidos, etiquetas).
        protector = DataProtector.from_secrets(secrets)
    except CryptoError as exc:
        log.critical("No se pudo preparar el cifrado de datos: %s", exc)
        QMessageBox.critical(
            None,
            APP_NAME,
            tr("{0}.\n\nLabelBridge no se abre para no perder ni exponer datos. Reinicia el equipo y vuelve a intentarlo; si continúa, revisa el Administrador de credenciales de Windows.", exc),
        )
        return 1
    cfg = ConfigManager(config_path(), protector)
    language = i18n.set_language(i18n.choose(cfg.data.general.language, hints.get("language", "")))
    if language == "es":
        _install_spanish_translator(app)  # botones estándar de Qt (Sí, No, Cancelar…)
    log.info("Idioma de la interfaz: %s", language)
    connect_store = "--connect-store" in argv or installer_hints.apply(cfg, hints)
    if hints.get("shop"):
        installer_hints.forget_store()
    store = JobStore(db_path(), spool_dir(), protector)
    events = Events()
    policy = SecurityPolicy(cfg, AuditLog(logs_dir() / "seguridad.log"))
    engine = PrintEngine(store, cfg, events, policy=policy)
    services = Services(cfg, secrets, store, engine, events)
    bridge = QtBridge(events, cfg)
    memory_handler.add_listener(bridge.forward_log)
    ctx = AppContext(cfg, secrets, store, engine, services, events, bridge)

    window = MainWindow(ctx)

    def quit_app() -> None:
        window.quitting = True
        # Si algo se queda colgado al cerrar, el proceso termina igual a los 20 s (la actualización espera a
        # que la app se cierre para instalar).
        watchdog = threading.Timer(20.0, lambda: os._exit(0))
        watchdog.daemon = True  # no retrasa un cierre normal
        watchdog.start()
        app.quit()

    window.quit_app = quit_app

    def tray_message_clicked() -> None:
        if window.pending_update is not None:
            window.open_update_dialog(window.pending_update)
        else:
            window.show_and_raise()

    tray = Tray(
        ctx,
        window.show_and_raise,
        quit_app,
        lambda: enqueue_test_label(ctx),
        check_updates=window.check_updates_now,
        message_clicked=tray_message_clicked,
        open_assistant=window.open_assistant,
    )
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
    if "--show" in argv or "--updated" in argv or not guide_complete(ctx) or not wants_hidden or connect_store:
        window.show()
    if connect_store:  # recién instalada con la tienda: directo a terminar de conectarla
        QTimer.singleShot(700, window.connect_store)
    not_installed = failed_update(store.kv_get)
    if not_installed is not None:
        store.kv_set("update:installed", "")
        window.show()
        log.error("La actualización a la versión %s no se completó (sigue la %s)", not_installed.version, __version__)
        QTimer.singleShot(900, lambda: window.update_failed(not_installed))
    just_installed = pending_whats_new(store.kv_get)
    if not_installed is None and just_installed is None and store.kv_get("update:installed"):
        store.kv_set("update:installed", "")  # intento antiguo ya superado por esta versión
    if just_installed is not None:
        store.kv_set("update:installed", "")
        window.show()
        QTimer.singleShot(900, lambda: WhatsNewDialog(window, just_installed).show())
    ensure_tray()

    code = app.exec()
    log.info("Cerrando %s…", APP_NAME)
    tray.hide()
    services.stop()
    engine.stop()
    store.close()
    return code
