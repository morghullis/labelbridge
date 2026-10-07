"""Arranque de LabelBridge: servicios en segundo plano + ventana + bandeja del sistema."""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

from . import APP_NAME, __version__
from .i18n import N_, tr
from .system.platform import MAC, mac_opened_from_disk_image, pick

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
    except Exception as exc:  # noqa: BLE001  quedan las carpetas de Qt; a lo sumo, algún botón estándar en inglés
        log.info("No se encontraron las traducciones de PySide6: %s", exc)
    translator = QTranslator(app)
    for folder in folders:
        if translator.load(QLocale(QLocale.Language.Spanish), "qtbase", "_", folder):
            app.installTranslator(translator)
            app._labelbridge_translator = translator  # mantener la referencia viva
            return
    log.info("Sin traducción de Qt al español: los botones estándar pueden salir en inglés")


def _install_exception_hooks() -> None:
    """Un error que nadie atrapó (un clic, un temporizador, un hilo) queda en el registro con su traza y se avisa: así
    un botón que «no hace nada» siempre dice por qué."""
    from . import errors

    def excepthook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.critical("Error no controlado", exc_info=(exc_type, exc, tb))
        errors.notify(tr("Algo falló en LabelBridge"), errors.user_message(exc))

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        log.critical("Error no controlado en hilo %s", args.thread.name if args.thread else "?",
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        if args.exc_value is not None:
            errors.notify(tr("Algo falló en segundo plano"), errors.user_message(args.exc_value))

    sys.excepthook = excepthook
    threading.excepthook = thread_hook


def _install_qt_message_handler() -> None:
    """Las advertencias de Qt (la impresora, el spooler, imágenes…) van al registro en vez de perderse en la consola,
    que la app instalada no tiene."""
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    from .printing.outputs import QT_LOGGER

    qt_log = logging.getLogger(QT_LOGGER)  # las impresiones miran aquí si el sistema rechazó un trabajo
    levels = {QtMsgType.QtDebugMsg: logging.DEBUG, QtMsgType.QtInfoMsg: logging.INFO,
              QtMsgType.QtWarningMsg: logging.WARNING, QtMsgType.QtCriticalMsg: logging.ERROR,
              QtMsgType.QtFatalMsg: logging.CRITICAL}
    noise = ("qt.qpa.fonts",)  # avisos conocidos que no dicen nada útil

    def handler(mode, context, message: str) -> None:
        category = getattr(context, "category", "") or ""
        if category.startswith(noise):
            return
        prefix = f"[{category}] " if category and category != "default" else ""
        try:
            qt_log.log(levels.get(mode, logging.WARNING), "%s%s", prefix, message)
        except Exception:  # noqa: BLE001  si el registro mismo falla no hay dónde anotarlo, y Qt no puede caerse aquí
            return

    qInstallMessageHandler(handler)


def _watch_dock(app, window) -> None:
    """Mac: el icono del Dock (o abrir la app otra vez desde Aplicaciones) vuelve a mostrar la ventana cerrada."""
    from PySide6.QtCore import Qt, QTimer

    def reopen(state) -> None:
        if state == Qt.ApplicationState.ApplicationActive and not window.isVisible():
            window.show_and_raise()

    # macOS también activa la app al abrirla: se escucha el Dock cuando ya arrancó (oculta, si así se pidió).
    QTimer.singleShot(3000, lambda: app.applicationStateChanged.connect(reopen))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if "--wake" in argv:  # la tarea «LabelBridge - Despertar» solo despierta la PC: no hay nada más que hacer
        return 0
    _ensure_qt_plugin_path()
    if "--self-test" in argv:
        from .selftest import run as self_test

        return self_test(argv)
    if "--background" in argv:  # sin sesión iniciada (tarea «LabelBridge - Sin sesión»): sin ventana
        from . import background
        from .logging_setup import setup_logging

        setup_logging()
        _install_exception_hooks()
        _install_qt_message_handler()  # sin esto, una etiqueta que Windows rechaza se daría por impresa
        return background.run(argv)

    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import i18n
    from .logging_setup import setup_logging
    from .paths import logs_dir
    from .system import installer_hints
    from .system.single_instance import SingleInstance
    from .ui import icons
    from .ui.theme import apply_theme

    class Application(QApplication):
        """Avisa cuando se pide cerrar la app entera (Salir, Cmd+Q, «Salir» del Dock o al cerrar sesión)."""

        quit_requested = None

        def event(self, event) -> bool:  # noqa: N802
            if event.type() == QEvent.Type.Quit and self.quit_requested is not None:
                self.quit_requested()
            return super().event(event)

    # El registro, antes que nada: lo que pase al arrancar (el idioma, el instalador) también queda escrito.
    setup_logging()
    _install_exception_hooks()
    _install_qt_message_handler()

    from .system import toasts

    toasts.set_process_identity()  # Windows: los avisos y la barra de tareas como LabelBridge (antes de las ventanas)
    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(APP_NAME)
    QApplication.setApplicationVersion(__version__)
    app = Application(argv)
    app.setQuitOnLastWindowClosed(False)
    # Idioma provisional (el del instalador o el de Windows) hasta leer la configuración.
    hints = installer_hints.read()
    i18n.set_language(i18n.choose("", hints.get("language", "")))

    instance = SingleInstance()
    from . import background

    background.release_hold()  # abrirla deja trabajar otra vez al segundo plano (al cerrar sesión o reiniciar)
    if "--restart" in argv:  # reinicio pedido por la app (p. ej. al cambiar el idioma)
        instance.wait_until_free(20)
    if not instance.acquire():
        log.info("LabelBridge ya está abierto: se muestra la ventana existente")
        return 0
    if mac_opened_from_disk_image():
        log.warning("Abierta desde la imagen de disco: se pide moverla a Aplicaciones")
        QMessageBox.information(None, APP_NAME, tr(
            "Primero mueve LabelBridge a la carpeta Aplicaciones: en la ventana de la imagen de disco, arrastra su "
            "icono a Aplicaciones y ábrela desde ahí.\n\nAbierta desde aquí no podría abrirse sola al iniciar sesión "
            "ni actualizarse."))
        return 0
    log.info("Iniciando %s %s", APP_NAME, __version__)

    if sys.platform == "win32":
        app.setFont(QFont("Segoe UI", 10))
    apply_theme(app)  # siempre oscuro con verde neón, esté Windows en claro o en oscuro
    app.setWindowIcon(icons.app_icon())
    try:
        return _run(app, argv, hints, instance)
    except Exception as exc:
        from . import errors

        log.critical("LabelBridge no pudo abrir", exc_info=True)
        QMessageBox.critical(None, APP_NAME, tr(
            "LabelBridge no pudo abrir: {0}\n\nEl detalle quedó en el registro, en la carpeta {1}. Vuelve a abrirla; "
            "si se repite, envía ese archivo a soporte.", errors.user_message(exc), logs_dir()))
        return 0  # ya se avisó: en la Mac, launchd no la vuelve a abrir en un bucle de ventanas


def _terms_pending(cfg, hints: dict) -> bool:
    """¿Falta aceptar los términos de uso y la privacidad de esta versión? Los aceptados en el instalador de Windows
    cuentan (y quedan anotados en la configuración)."""
    from . import legal

    general = cfg.data.general
    if general.terms_accepted >= legal.TERMS_VERSION:
        return False
    from_installer = hints.get("terms_accepted", "")
    if from_installer.isdigit() and int(from_installer) >= legal.TERMS_VERSION:
        general.terms_accepted = int(from_installer)
        cfg.save("general")
        log.info("Términos de uso y privacidad aceptados en el instalador (versión %s)", general.terms_accepted)
        return False
    return True


def _accept_terms(cfg, instance=None) -> bool:
    """La ventana de términos y privacidad, esperando la respuesta (instalación nueva: antes de empezar). Si se abre
    LabelBridge otra vez mientras tanto, la ventana vuelve al frente."""
    from PySide6.QtWidgets import QDialog

    from .ui.legal_dialog import terms_dialog

    dialog = terms_dialog()

    def bring_front() -> None:
        dialog.raise_()
        dialog.activateWindow()

    if instance is not None:
        instance.activated.connect(bring_front)
    bring_front()
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    if instance is not None:
        instance.activated.disconnect(bring_front)
    if not accepted:
        return False
    _terms_saved(cfg)
    return True


def _terms_saved(cfg) -> None:
    from . import legal

    cfg.data.general.terms_accepted = legal.TERMS_VERSION
    cfg.save("general")
    log.info("Términos de uso y privacidad aceptados (versión %s)", legal.TERMS_VERSION)


def _ask_terms_while_working(cfg, window, quit_app) -> None:
    """Una instalación que ya trabajaba (p. ej. tras actualizarse sola): la app sigue imprimiendo mientras se pide
    aceptar los términos nuevos; si no se aceptan, se cierra. Nunca deja una PC desatendida sin imprimir."""
    from .ui.legal_dialog import terms_dialog

    dialog = terms_dialog(window)
    dialog.accepted.connect(lambda: _terms_saved(cfg))
    dialog.rejected.connect(quit_app)
    dialog.open()  # sin esperar: la impresión y los servicios siguen
    dialog.raise_()
    dialog.activateWindow()


def _run(app, argv: list[str], hints: dict, instance) -> int:
    """Lo que hace falta para trabajar: datos, servicios, ventana y bandeja. Corre el programa hasta que se cierra."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox, QSystemTrayIcon

    from . import errors, i18n
    from .config import ConfigManager
    from .events import Events
    from .jobstore import JobStore
    from .logging_setup import memory_handler
    from .paths import config_path, data_dir, db_path, logs_dir, spool_dir
    from .printing.engine import PrintEngine
    from .secret_store import SecretStore
    from .security.audit import AuditLog
    from .security.crypto import CryptoError, DataProtector
    from .security.policy import SecurityPolicy
    from .services import Services
    from .system import autostart, installer_hints
    from .ui.actions import enqueue_test_label
    from .ui.context import AppContext, QtBridge
    from .ui.main_window import MainWindow
    from .ui.pages.guide import is_complete as guide_complete
    from .ui.tray import Tray
    from .ui.update_dialog import WhatsNewDialog
    from .updater import failed_update, pending_whats_new

    secrets = SecretStore(data_dir() / "secrets.json")
    try:
        # Clave de cifrado de los datos en disco (configuración, pedidos, etiquetas).
        protector = DataProtector.from_secrets(secrets)
    except CryptoError as exc:
        log.critical("No se pudo preparar el cifrado de datos: %s", exc)
        QMessageBox.critical(
            None,
            APP_NAME,
            tr(pick(N_("{0}.\n\nLabelBridge no se abre para no perder ni exponer datos. Reinicia el equipo y vuelve a intentarlo; si continúa, revisa el Administrador de credenciales de Windows."),
                    N_("{0}.\n\nLabelBridge no se abre para no perder ni exponer datos. Reinicia la Mac y vuelve a intentarlo; si continúa, revisa el Llavero (Acceso a Llaveros).")), exc),
        )
        return 0  # ya se avisó: en la Mac, launchd no la vuelve a abrir en un bucle de ventanas
    fresh_install = not config_path().exists()  # sin configuración: primera vez en este equipo
    cfg = ConfigManager(config_path(), protector)
    language = i18n.set_language(i18n.choose(cfg.data.general.language, hints.get("language", "")))
    if language == "es":
        _install_spanish_translator(app)  # botones estándar de Qt (Sí, No, Cancelar…)
    log.info("Idioma de la interfaz: %s", language)
    if cfg.load_problem:  # la configuración no se pudo leer: se empezó de cero (y se dice dónde quedó la copia)
        QMessageBox.warning(None, APP_NAME, cfg.load_problem)
    terms_pending = _terms_pending(cfg, hints)
    if terms_pending and fresh_install:  # instalación nueva: se aceptan antes de empezar
        if not _accept_terms(cfg, instance):
            log.info("No se aceptaron los términos de uso: LabelBridge se cierra sin empezar")
            return 0
        terms_pending = False
    connect_store = "--connect-store" in argv or installer_hints.apply(cfg, hints)
    # Con la clave del instalador la tienda se prueba y se activa sola al abrirse (ver ShopifyPage.connected).
    auto_connect = installer_hints.apply_secret(secrets, cfg, hints)
    if hints.get("shop") or hints.get("client_secret"):
        installer_hints.forget_store()  # también la clave: ya está en el almacén seguro (o no se usó)
    store = JobStore(db_path(), spool_dir(), protector)
    events = Events()
    policy = SecurityPolicy(cfg, AuditLog(logs_dir() / "seguridad.log"))
    engine = PrintEngine(store, cfg, events, policy=policy)
    services = Services(cfg, secrets, store, engine, events)
    bridge = QtBridge(events, cfg)
    memory_handler.add_listener(bridge.forward_log)
    ctx = AppContext(cfg, secrets, store, engine, services, events, bridge)
    errors.set_notifier(lambda title, text: events.notify(title, text, "error"))

    window = MainWindow(ctx)

    def quit_app() -> None:
        window.quitting = True
        app.quit()

    def quit_from_menu() -> None:
        """«Salir» elegido por la persona: tampoco trabaja el segundo plano hasta abrirla otra vez o reiniciar."""
        from . import background

        background.hold()
        quit_app()

    def start_watchdog() -> None:
        # Si algo se queda colgado al cerrar, el proceso termina igual a los 20 s (la actualización espera a que la
        # app se cierre para instalar). Arranca solo cuando la salida es segura: si se cancela (una compra en curso),
        # la app sigue.
        def force_exit() -> None:
            # Anotarlo en otro hilo: si un hilo trabado tiene tomado el registro, esto no puede trabarse también.
            note = threading.Thread(target=lambda: log.warning("El cierre tardó más de 20 s: se termina el proceso"),
                                    daemon=True)
            note.start()
            note.join(1.0)
            os._exit(0)

        watchdog = threading.Timer(20.0, force_exit)
        watchdog.daemon = True  # no retrasa un cierre normal
        watchdog.start()

    app.aboutToQuit.connect(start_watchdog)
    window.quit_app = quit_app

    def quit_requested() -> None:
        # Cerrar la app entera no es «cerrar la ventana»: sin esto, Cmd+Q la escondería con el aviso de que sigue
        # imprimiendo. Si algo cancela la salida (una compra en curso), la app sigue como siempre.
        window.quitting = True
        QTimer.singleShot(0, lambda: setattr(window, "quitting", False))

    app.quit_requested = quit_requested

    def tray_message_clicked() -> None:
        if window.pending_update is not None:
            window.open_update_dialog(window.pending_update)
        else:
            window.show_and_raise()

    def open_target(target: str) -> None:
        """El clic en un aviso de Windows: la pantalla de lo que avisa (o lo de siempre, si no dice cuál)."""
        if not target:
            tray_message_clicked()
            return
        window.show_and_raise()
        window.navigate(target)

    tray = Tray(
        ctx,
        window.show_and_raise,
        quit_from_menu,
        lambda: enqueue_test_label(ctx),
        check_updates=window.check_updates_now,
        message_clicked=tray_message_clicked,
        open_assistant=window.open_assistant,
        open_target=open_target,
    )
    instance.activated.connect(window.show_and_raise)
    if MAC:
        _watch_dock(app, window)

    def ensure_tray(attempts_left: int = 10) -> None:
        # Al iniciar sesión en Windows la bandeja puede tardar unos segundos en existir. Si no aparece, la ventana
        # queda a la vista (los errores se ven arriba de la pantalla) y se sigue probando cada minuto.
        if QSystemTrayIcon.isSystemTrayAvailable():
            if attempts_left < 0:
                log.info("La bandeja del sistema ya está disponible")
            tray.show()
            return
        if attempts_left == 0:  # una sola vez: la ventana queda a la vista (los errores se ven arriba)
            log.warning("No hay bandeja del sistema: se muestra la ventana y se sigue probando cada minuto")
            window.show()
        next_try = attempts_left - 1 if attempts_left > 0 else -1  # -1: se sigue probando en silencio
        QTimer.singleShot(3000 if attempts_left > 0 else 60_000, lambda: ensure_tray(next_try))

    engine.start()
    services.start()
    try:
        autostart.apply(cfg.data.general.start_with_windows)
    except OSError as exc:
        errors.report(tr("No se pudo configurar el inicio automático"), exc,
                      tr("{0}. Revísalo en Ajustes → General.", errors.user_message(exc)))

    # Funcionar siempre: el equipo no se duerme solo mientras la app está abierta (la pantalla sí se apaga) y, si
    # vuelve de una suspensión, todo se pone al día enseguida.
    from .system.power import KeepAwake, ResumeWatcher

    keep_awake = KeepAwake()

    def stay_awake(_section: str = "") -> None:
        wanted = cfg.data.general.keep_awake
        if not keep_awake.apply(wanted) and wanted and sys.platform in ("win32", "darwin"):
            errors.report(tr("El equipo podría dormirse"), message=tr(
                "No se pudo evitar que el equipo se duerma mientras LabelBridge está abierto: si se duerme, no imprime. "
                "Revisa la energía del equipo (o pídeselo al asistente)."))

    stay_awake()
    bridge.config_saved.connect(stay_awake)
    app.aboutToQuit.connect(keep_awake.release)

    def caught_up(_gap: float) -> None:
        for service in services.all():
            service.wake()
        engine.wake()

    resume = ResumeWatcher(caught_up)
    resume_timer = QTimer()
    resume_timer.timeout.connect(lambda: resume.check())
    resume_timer.start(int(resume.every * 1000))

    wants_hidden = "--minimized" in argv or cfg.data.general.start_minimized
    if "--show" in argv or "--updated" in argv or not guide_complete(ctx) or not wants_hidden or connect_store:
        window.show()
    if connect_store:  # recién instalada con la tienda: directo a conectarla (y probarla, si vino la clave)
        QTimer.singleShot(700, lambda: window.connect_store(test=auto_connect))
    not_installed = failed_update(store.kv_get)
    if not_installed is not None:
        store.kv_set("update:installed", "")
        store.kv_set("update:auto_failed", not_installed.version)  # esa versión ya no se instala sola: se pregunta
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
    if terms_pending:  # ya trabajaba en este equipo: sigue imprimiendo mientras se aceptan los términos nuevos
        QTimer.singleShot(1200, lambda: _ask_terms_while_working(cfg, window, quit_app))
    for title, text in services.startup_notices:  # lo que falló al preparar los servicios, ya con la ventana escuchando
        QTimer.singleShot(1500, lambda title=title, text=text: events.notify(title, text, "error"))

    code = app.exec()
    log.info("Cerrando %s…", APP_NAME)
    errors.set_notifier(None)  # cerrando: ya no hay dónde avisar
    for step, close in (("bandeja", tray.hide), ("servicios", services.stop), ("impresión", engine.stop),
                        ("base de datos", store.close)):
        try:
            close()
        except Exception:  # noqa: BLE001  un paso que falla al cerrar no impide los siguientes (y queda anotado)
            log.exception("Al cerrar: falló %s", step)
    return code
