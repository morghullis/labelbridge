"""LabelBridge sin ventana: imprime y compra guías aunque nadie haya iniciado sesión en la PC (o con la sesión cerrada).

La arranca la tarea programada de Windows «LabelBridge - Sin sesión» al encender el equipo y cada pocos minutos
(system/always_on.py). Si la app con ventana ya está abierta, termina enseguida. Cuando alguien inicia sesión y se abre
la app, esta le pide el relevo: termina lo que está imprimiendo y se cierra (system/single_instance.py), y la app sigue
desde ahí, con la misma configuración, la misma cola y las mismas claves.

No muestra nada (no hay nadie para verlo): lo que pasa queda en el registro y, si están configurados, salen los avisos
por email. Tampoco instala actualizaciones: lo hace la app con ventana.
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)


def run(argv: list[str], app=None) -> int:
    """Trabaja en segundo plano hasta que la app con ventana pida el relevo (o hasta `--exit-after N` segundos, para
    las pruebas). `app`: la QApplication ya creada (en las pruebas); si no, se crea."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from . import APP_NAME, __version__, errors, i18n
    from .system.single_instance import BACKGROUND, SingleInstance

    owns_app = app is None
    if owns_app:
        QApplication.setApplicationName(APP_NAME)
        app = QApplication(argv)  # la impresión con el driver de Windows necesita la parte gráfica de Qt (sin ventanas)
    app.setQuitOnLastWindowClosed(False)
    instance = SingleInstance(BACKGROUND, key=_option(argv, "--instance-key"))
    if not instance.acquire():
        return 0  # ya hay una copia trabajando (la app con ventana u otra de segundo plano): nada que hacer
    log.info("%s %s trabaja en segundo plano (sin sesión iniciada)", APP_NAME, __version__)
    try:
        core = _Core()
    except Exception:  # noqa: BLE001  sin datos ni claves no se puede trabajar: queda anotado por qué
        log.exception("Segundo plano: no se pudo preparar LabelBridge")
        instance.release()
        return 1
    i18n.set_language(i18n.choose(core.cfg.data.general.language, ""))
    errors.set_notifier(lambda title, text: core.events.notify(title, text, "error"))  # llega por email si hay

    def handoff() -> None:
        log.info("Segundo plano: la app con ventana pidió el relevo; se termina lo que está en curso")
        core.handed_off = True
        app.quit()

    instance.handoff_requested.connect(handoff)
    core.start()
    exit_after = _option(argv, "--exit-after")
    if exit_after:
        QTimer.singleShot(int(float(exit_after) * 1000), app.quit)
    code = app.exec()
    errors.set_notifier(None)
    core.stop()
    instance.release()
    log.info("Segundo plano: LabelBridge se cerró (%s)", "relevo de la app" if core.handed_off else "fin")
    return code


def _option(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else ""


class _Core:
    """Lo mismo que trabaja en la app con ventana (datos, cola, impresión, Shopify, n8n, API local, energía), sin
    nada de pantalla."""

    def __init__(self) -> None:
        from .config import ConfigManager
        from .events import Events
        from .jobstore import JobStore
        from .paths import config_path, data_dir, db_path, logs_dir, spool_dir
        from .printing.engine import PrintEngine
        from .secret_store import SecretStore
        from .security.audit import AuditLog
        from .security.crypto import DataProtector
        from .security.policy import SecurityPolicy
        from .services import Services
        from .system.power import KeepAwake, ResumeWatcher

        self.handed_off = False
        self.secrets = SecretStore(data_dir() / "secrets.json")
        protector = DataProtector.from_secrets(self.secrets)
        self.cfg = ConfigManager(config_path(), protector)
        self.store = JobStore(db_path(), spool_dir(), protector)
        self.events = Events()
        policy = SecurityPolicy(self.cfg, AuditLog(logs_dir() / "seguridad.log"))
        self.engine = PrintEngine(self.store, self.cfg, self.events, policy=policy)
        self.services = Services(self.cfg, self.secrets, self.store, self.engine, self.events)
        self.keep_awake = KeepAwake()
        self.resume = ResumeWatcher(self._caught_up)
        self._timer = None

    def _caught_up(self, _gap: float) -> None:
        for service in self.services.all():
            service.wake()
        self.engine.wake()

    def start(self) -> None:
        from PySide6.QtCore import QTimer

        self.engine.start()
        self.services.start()
        self.keep_awake.apply(self.cfg.data.general.keep_awake)  # la PC no se duerme sola mientras trabaja
        self._timer = QTimer()
        self._timer.timeout.connect(lambda: self.resume.check())
        self._timer.start(int(self.resume.every * 1000))

    def stop(self) -> None:
        """Termina lo que está en curso (la impresión espera al trabajo actual) y cierra la base."""
        if self._timer is not None:
            self._timer.stop()
        for step, close in (("servicios", self.services.stop), ("impresión", self.engine.stop),
                            ("energía", self.keep_awake.release), ("base de datos", self.store.close)):
            try:
                close()
            except Exception:  # noqa: BLE001  un paso que falla al cerrar no impide los siguientes
                log.exception("Segundo plano, al cerrar: falló %s", step)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run(sys.argv))
