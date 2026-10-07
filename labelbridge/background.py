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
import time
from pathlib import Path

log = logging.getLogger(__name__)

STOP_SECONDS = 120  # en el relevo, lo que se espera a que termine la etiqueta que se está imprimiendo


def run(argv: list[str], app=None) -> int:
    """Trabaja en segundo plano hasta que la app con ventana pida el relevo (o hasta `--exit-after N` segundos, para
    las pruebas). `app`: la QApplication ya creada (en las pruebas); si no, se crea."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from . import APP_NAME, __version__, errors, i18n
    from .system.single_instance import BACKGROUND, SingleInstance

    if on_hold():
        log.info("Segundo plano: alguien eligió «Salir» en la app; no se trabaja hasta que se abra o se reinicie la PC")
        return 0
    if app is None:
        QApplication.setApplicationName(APP_NAME)
        app = QApplication(argv)  # la impresión con el driver de Windows necesita la parte gráfica de Qt (sin ventanas)
    app.setQuitOnLastWindowClosed(False)
    lock_path = _option(argv, "--lock-file")
    instance = SingleInstance(BACKGROUND, key=_option(argv, "--instance-key") or None,
                              lock_path=Path(lock_path) if lock_path else None)
    if not instance.acquire():
        return 0  # ya hay una copia trabajando (la app con ventana u otra de segundo plano): nada que hacer
    state: dict = {"core": None, "handed_off": False}

    def handoff() -> None:
        log.info("Segundo plano: la app con ventana pidió el relevo; se termina lo que está en curso")
        state["handed_off"] = True
        app.quit()

    def start() -> None:
        # Con el bucle de eventos ya andando: el canal contesta (rol, relevo) mientras se prepara lo pesado.
        if state["handed_off"]:
            return
        log.info("%s %s trabaja en segundo plano (sin sesión iniciada)", APP_NAME, __version__)
        try:
            core = _Core()
        except Exception:  # noqa: BLE001  sin datos ni claves no se puede trabajar: queda anotado por qué
            log.exception("Segundo plano: no se pudo preparar LabelBridge")
            app.exit(1)
            return
        from . import legal

        if core.cfg.data.general.terms_accepted < legal.TERMS_VERSION:
            log.info("Segundo plano: faltan aceptar los términos de uso en la app; no se trabaja")
            core.close()
            app.quit()
            return
        i18n.set_language(i18n.choose(core.cfg.data.general.language, ""))
        errors.set_notifier(lambda title, text: core.events.notify(title, text, "error"))  # llega por email si hay
        core.start()
        state["core"] = core

    instance.handoff_requested.connect(handoff)
    QTimer.singleShot(0, start)
    exit_after = _option(argv, "--exit-after")
    if exit_after:
        QTimer.singleShot(int(float(exit_after) * 1000), app.quit)
    code = app.exec()
    errors.set_notifier(None)
    if state["core"] is not None:
        state["core"].stop()  # termina el trabajo en curso antes de soltar el candado
    instance.release()
    log.info("Segundo plano: LabelBridge se cerró (%s)", "relevo de la app" if state["handed_off"] else "fin")
    return code


# --- «Salir» en la app: el segundo plano no la contradice ---------------------------------------------------------
def _hold_path() -> Path:
    from .paths import data_dir

    return data_dir() / "segundo-plano-en-pausa"


def _boot_time() -> float:
    """Cuándo arrancó el equipo (aprox.): el reloj del sistema menos el tiempo encendido."""
    return time.time() - time.monotonic()


def hold() -> None:
    """La persona eligió «Salir» en la app: hasta que la abra otra vez o reinicie la PC, el segundo plano no trabaja."""
    try:
        _hold_path().write_text(str(_boot_time()), encoding="ascii")
    except OSError as exc:
        log.warning("No se pudo anotar la pausa del segundo plano: %s", exc)


def release_hold() -> None:
    try:
        _hold_path().unlink(missing_ok=True)
    except OSError as exc:
        log.warning("No se pudo quitar la pausa del segundo plano: %s", exc)


def on_hold() -> bool:
    try:
        boot = float(_hold_path().read_text(encoding="ascii"))
    except (OSError, ValueError):
        return False
    return abs(boot - _boot_time()) < 300  # misma sesión de la PC: tras reiniciar, vuelve a trabajar


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

    def close(self) -> None:
        """Sin haber arrancado: solo la base."""
        self.store.close()

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
        """Termina lo que está en curso (la impresión espera al trabajo actual, hasta 2 minutos) y cierra la base."""
        if self._timer is not None:
            self._timer.stop()
        for step, close in (("servicios", self.services.stop), ("impresión", lambda: self.engine.stop(STOP_SECONDS)),
                            ("energía", self.keep_awake.release), ("base de datos", self.store.close)):
            try:
                close()
            except Exception:  # noqa: BLE001  un paso que falla al cerrar no impide los siguientes
                log.exception("Segundo plano, al cerrar: falló %s", step)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run(sys.argv))
